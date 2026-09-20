import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend import services
from backend.main import app
from backend.models import Plan

def mock_response(monkeypatch, body):
    requests=[]
    client_type=httpx.AsyncClient
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=body)
    monkeypatch.setattr(services.httpx,'AsyncClient',lambda **kwargs:client_type(transport=httpx.MockTransport(handler),**kwargs))
    monkeypatch.setenv('OPENAI_API_KEY','test-openai-key')
    monkeypatch.setenv('OPENAI_MODEL','gpt-4.1-mini')
    return requests

def completed(value):
    return {'status':'completed','output':[{'type':'reasoning','summary':[]},{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}],
            'usage':{'input_tokens':123,'output_tokens':24}}

def test_responses_endpoint_contract_and_usage(monkeypatch):
    requests=mock_response(monkeypatch,completed({'region':'sacramento'}))
    result,metrics=asyncio.run(services.openai_json('Return JSON.',{'goal':'solar'}))
    assert result=={'region':'sacramento'}
    assert str(requests[0].url)=='https://api.openai.com/v1/responses'
    assert requests[0].headers['Authorization']=='Bearer test-openai-key'
    payload=json.loads(requests[0].content)
    assert payload['model']=='gpt-4.1-mini'
    assert payload['text']['format']=={'type':'json_object'}
    assert payload['store'] is False
    assert 'json' in payload['input'][0]['content'].lower()
    assert metrics['usage']=={'input_tokens':123,'output_tokens':24}

@pytest.mark.parametrize('body',[
    {'status':'incomplete','output':[]},
    {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'Declined'}]}]},
    completed(['invalid contract']),
    {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'not JSON'}]}]},
])
def test_invalid_or_refused_outputs_are_not_applied(monkeypatch,body):
    mock_response(monkeypatch,body)
    with pytest.raises(ValueError): asyncio.run(services.openai_json('Return JSON.',{}))

def test_planner_uses_openai_and_keeps_deterministic_constraints(monkeypatch):
    mock_response(monkeypatch,completed({'region':'sacramento','technology':'solar','target_mw':50,'capacity_mw':99999}))
    telemetry=[]
    plan=asyncio.run(services.planner(Plan(query='Find 50 MW solar in Sacramento.',mode='live'),telemetry))
    assert (plan.region,plan.technology,plan.target_mw)==('sacramento','solar',50)
    assert plan.constraints.exclude_protected
    assert telemetry[0]['provider']=='OpenAI'

def test_demo_does_not_call_openai(monkeypatch):
    requests=mock_response(monkeypatch,completed({}))
    asyncio.run(services.planner(Plan(),[]))
    assert not requests

def test_status_checks_openai_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    assert services.service_status()['services']['openai']
    monkeypatch.delenv('OPENAI_API_KEY')
    assert not services.service_status()['services']['openai']

def test_voice_missing_key_reports_actionable_error(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    with TestClient(app) as client:
        with client.websocket_connect('/api/voice',headers={'origin':'http://127.0.0.1:5180'}) as socket:
            event=socket.receive_json()
            assert event['type']=='error' and 'OPENAI_API_KEY' in event['message']

def test_voice_session_contract(monkeypatch):
    monkeypatch.setenv('OPENAI_TRANSCRIPTION_MODEL','gpt-4o-mini-transcribe')
    event=services.transcription_session()
    assert event['type']=='session.update'
    assert event['session']['type']=='transcription'
    audio=event['session']['audio']['input']
    assert audio['format']=={'type':'audio/pcm','rate':24000}
    assert audio['transcription']['model']=='gpt-4o-mini-transcribe'
    assert audio['turn_detection']['type']=='server_vad'
