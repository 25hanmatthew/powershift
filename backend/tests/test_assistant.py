import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import assistant
from backend.assistant import ChatRequest, Investigation, RerankArgs, SearchArgs, SiteArgs
from backend.cache import Cache
from backend.demo import candidates
from backend.main import app
from backend.models import Plan
from backend.scoring import rank_candidates
from backend.services import REGISTRY


@pytest.fixture
def saved(monkeypatch, tmp_path):
    monkeypatch.setenv('CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    plan = Plan()
    physical = candidates(plan.region)
    result = {**rank_candidates(physical, plan), 'run_id': 'test-run', 'plan': plan.model_dump(),
              'mode': 'demo', 'datasets': REGISTRY, 'explanation': 'Synthetic test data.',
              'urban_summary': {'comparison': [{'best_score': 12}], 'recommended_approach': 'Old recommendation'}}
    Cache().set('run:test-run', {'result': result, 'physical': physical})
    return result


def tool(name, args, call_id='call-1'):
    return {'status': 'completed', 'output': [{'type': 'function_call', 'call_id': call_id, 'name': name, 'arguments': json.dumps(args)}]}


def answer(text='Here is the evidence.', sites=None, sources=None):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text',
        'text': json.dumps({'answer': text, 'site_ids': sites or [], 'source_ids': sources or [], 'followups': []})}]}]}


def run(request, search=None):
    events = []
    async def emit(event): events.append(event)
    result = asyncio.run(assistant.investigate(request, search or AsyncMock(), emit))
    return result, events


def test_agent_inspects_then_compares_actual_sites(monkeypatch, saved):
    ids = [c['id'] for c in saved['candidates'][:2]]
    provider = AsyncMock(side_effect=[tool('inspect_sites', {'site_ids': ids}),
                                   tool('compare_sites', {'site_ids': ids}, 'call-2'), answer(sites=ids)])
    monkeypatch.setattr(assistant, 'model_response', provider)
    result, events = run(ChatRequest(message='Compare the top two sites.', run_id='test-run'))
    assert result['result'] is None
    assert [s['id'] for s in result['sites']] == ids
    assert len([e for e in events if e['type'] == 'tool_end' and e['ok']]) == 2
    outputs = [i for i in provider.call_args.args[0] if i.get('type') == 'function_call_output']
    diff = json.loads(outputs[-1]['output'])['differences_from_first'][0]
    assert diff['capacity_mw'] == pytest.approx(saved['candidates'][1]['capacity_mw']-saved['candidates'][0]['capacity_mw'])


def test_rerank_creates_new_run_and_preserves_original(monkeypatch, saved):
    monkeypatch.setattr(assistant, 'model_response', AsyncMock(side_effect=[
        tool('rerank_sites', {'weights': {'grid': 90}, 'constraints': {'max_grid_km': 5}, 'technology': None, 'target_mw': None}), answer()]))
    reply, _ = run(ChatRequest(message='Prioritize grid proximity and limit distance to 5 km.', run_id='test-run'))
    result = reply['result']
    assert result['run_id'] != 'test-run'
    assert result['plan']['weights']['grid'] == 90
    assert result['plan']['weights']['resource'] == saved['plan']['weights']['resource']
    assert result['plan']['constraints']['exclude_protected'] is True
    assert all(c['grid_distance_km'] <= 5 for c in result['candidates'])
    assert 'comparison' not in result['urban_summary']
    assert Cache().get('run:test-run')['data']['result'] == saved
    assert Cache().get('run:'+result['run_id'])['data']['result'] == result


def test_invalid_arguments_and_unknown_tools_never_mutate_map(monkeypatch, saved):
    monkeypatch.setattr(assistant, 'model_response', AsyncMock(side_effect=[
        tool('rerank_sites', {'weights': {'grid': 900}, 'constraints': None, 'technology': None, 'target_mw': None}),
        tool('execute_shell', {'command': 'anything'}, 'call-2'), answer('Those actions could not be applied.')]))
    reply, events = run(ChatRequest(message='Change the grid priority.', run_id='test-run'))
    assert reply['result'] is None
    assert all(not e['ok'] for e in events if e['type'] == 'tool_end')
    assert Cache().get('run:test-run')['data']['result'] == saved


def test_fabricated_citations_are_rejected_and_repaired(monkeypatch, saved):
    provider = AsyncMock(side_effect=[answer(sites=['not-a-site'], sources=['imaginary-source']), answer('No evidence for that claim.')])
    monkeypatch.setattr(assistant, 'model_response', provider)
    reply, _ = run(ChatRequest(message='What is the evidence?', run_id='test-run'))
    assert provider.await_count == 2
    assert not reply['sites'] and not reply['sources']


def test_stale_run_and_site_ids_fail_closed(saved):
    with pytest.raises(ValueError, match='expired'):
        Investigation(ChatRequest(message='Explain this.', run_id='missing'), AsyncMock())
    state = Investigation(ChatRequest(message='Explain.', run_id='test-run'), AsyncMock())
    with pytest.raises(ValueError, match='current analysis'):
        state.sites(['other-run-site'])


def test_failed_final_answer_does_not_commit_scenario(monkeypatch, saved):
    provider = AsyncMock(side_effect=[tool('rerank_sites', {'weights': {'grid': 80}, 'constraints': None, 'technology': None, 'target_mw': None}), RuntimeError('provider down')])
    monkeypatch.setattr(assistant, 'model_response', provider)
    with pytest.raises(RuntimeError):
        run(ChatRequest(message='Prioritize grid.', run_id='test-run'))
    with Cache().connect() as db:
        assert db.execute("select count(*) from cache where key like 'run:%'").fetchone()[0] == 1


def test_new_search_uses_validated_canonical_request(saved):
    search = AsyncMock(return_value=saved)
    state = Investigation(ChatRequest(message='Find rooftops.'), search)
    args = SearchArgs(city='Sacramento', state='CA', technology='solar', surface='rooftop', scope='city', limit=5, target_mw=2)
    asyncio.run(state.call('search_sites', args))
    search.assert_awaited_once_with('Find 5 solar rooftops in Sacramento, CA with a 2 MW target')
    with pytest.raises(ValueError, match='one new search'):
        asyncio.run(state.call('search_sites', args))
    assert state.result['run_id'] == 'test-run'


def test_new_location_keeps_current_priorities_and_constraints(saved):
    view = {k: copy.deepcopy(saved['plan'][k]) for k in ('weights', 'constraints', 'target_mw', 'technology')}
    view['weights']['grid'] = 80
    view['constraints']['max_grid_km'] = 5
    view['target_mw'] = 100
    search = AsyncMock(return_value=saved)
    state = Investigation(ChatRequest(message='Search another city.', run_id='test-run', view=view), search)
    args = SearchArgs(city='Davis', state='CA', technology='solar', surface='all', scope='city', limit=5, target_mw=None)
    asyncio.run(state.call('search_sites', args))
    search.assert_awaited_once_with('Find 5 solar sites in Davis, CA with a 100 MW target')
    assert state.result['plan']['weights']['grid'] == 80
    assert state.result['plan']['constraints']['max_grid_km'] == 5
    assert state.result['plan']['target_mw'] == 100
    assert state.result['run_id'] != saved['run_id']
    assert Cache().get('run:test-run')['data']['result'] == saved


@pytest.mark.parametrize('change', [{'state': 'XX'}, {'technology': 'auto'}, {'technology': 'wind', 'surface': 'rooftop'}])
def test_unsupported_search_does_not_call_data_service(saved, change):
    search = AsyncMock()
    state = Investigation(ChatRequest(message='Search.'), search)
    args = SearchArgs(**{**dict(city='Sacramento', state='CA', technology='solar', surface='all', scope='city', limit=5, target_mw=None), **change})
    with pytest.raises(ValueError): asyncio.run(state.call('search_sites', args))
    search.assert_not_called()


def test_current_ui_preferences_are_used_in_evidence(saved):
    view = {k: saved['plan'][k] for k in ('weights', 'constraints', 'target_mw', 'technology')}
    view = copy.deepcopy(view);view['weights']['grid'] = 85
    state = Investigation(ChatRequest(message='Explain.', run_id='test-run', view=view), AsyncMock())
    assert state.analysis()['plan']['weights']['grid'] == 85
    assert Cache().get('run:test-run')['data']['result'] == saved


def test_sse_route_streams_actions_and_checked_reply(monkeypatch, saved):
    monkeypatch.setattr(assistant, 'model_response', AsyncMock(side_effect=[tool('get_analysis', {}), answer()]))
    response = TestClient(app).post('/api/assistant/chat', json={'message': 'Explain the analysis.', 'run_id': 'test-run'})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert [e['type'] for e in events] == ['status', 'tool_start', 'tool_end', 'status', 'done']


def test_missing_key_is_explicit_not_fake_ai(monkeypatch, saved):
    monkeypatch.delenv('OPENAI_API_KEY')
    response = TestClient(app).post('/api/assistant/chat', json={'message': 'Explain.'})
    assert response.status_code == 503 and 'OPENAI_API_KEY' in response.json()['detail']


def test_provider_contract_has_strict_tools_and_output(monkeypatch, saved):
    requests = []
    original = httpx.AsyncClient
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=answer())
    monkeypatch.setattr(assistant.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    asyncio.run(assistant.model_response([{'role': 'user', 'content': 'Explain.'}]))
    payload = requests[0]
    assert payload['store'] is False and payload['parallel_tool_calls'] is False
    assert payload['text']['format']['type'] == 'json_schema'
    for tool_spec in payload['tools']:
        assert tool_spec['strict'] is True
        assert tool_spec['parameters']['additionalProperties'] is False
        assert set(tool_spec['parameters']['required']) == set(tool_spec['parameters']['properties'])


def test_tool_rounds_are_bounded(monkeypatch, saved):
    provider = AsyncMock(return_value=tool('get_analysis', {}))
    monkeypatch.setattr(assistant, 'model_response', provider)
    with pytest.raises(ValueError, match='action limit'):
        run(ChatRequest(message='Keep investigating.', run_id='test-run'))
    assert provider.await_count == 7
    assert provider.call_args.args[1] == 'none'


def test_nonfinite_or_extra_mutations_rejected():
    with pytest.raises(ValidationError):
        RerankArgs(weights={'grid': float('nan')}, constraints=None, technology=None, target_mw=None)
    with pytest.raises(ValidationError):
        RerankArgs(weights=None, constraints={'exclude_protected': False}, technology=None, target_mw=None)
