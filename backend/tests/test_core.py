import copy
import json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.models import Plan, Constraints, Weights
from backend.demo import candidates
from backend.scoring import rank_candidates
from backend.cache import analysis_key, Cache
from backend.services import parse_local
from backend.main import app

def test_protected_and_terrain_exclusions_cannot_be_outweighed():
    plan=Plan(weights=Weights(resource=100,environment=0,grid=0,buildability=0,reuse=0))
    result=rank_candidates(candidates(plan.region),plan)
    assert len(result['candidates'])>=5
    assert all(c['protected_overlap_pct']==0 and c['slope_deg']<=15 for c in result['candidates'])
    assert any('Protected land' in c['exclusion_reasons'] for c in result['excluded'])
    assert any('Slope limit' in c['exclusion_reasons'] for c in result['excluded'])

def test_grid_threshold_changes_eligibility():
    sites=candidates('california-nevada')
    loose=rank_candidates(sites,Plan())
    strict=rank_candidates(sites,Plan(constraints=Constraints(max_grid_km=2)))
    assert len(strict['candidates'])<len(loose['candidates'])
    assert all(c['grid_distance_km']<=2 for c in strict['candidates'])

def test_no_double_counting_alternative_technologies():
    site=candidates('california-nevada')[0]
    duplicate={**site,'id':'alternative','technology':'wind'}
    result=rank_candidates([site,duplicate],Plan(target_mw=200))
    assert result['portfolio']['capacity_mw']==site['capacity_mw']
    assert result['portfolio']['site_count']==1
    assert not result['portfolio']['target_met']
    assert result['portfolio']['shortfall_mw']==200-site['capacity_mw']

def test_zero_new_land_requires_verified_footprints():
    result=rank_candidates(candidates('sacramento'),Plan(region='sacramento',constraints=Constraints(zero_new_land=True)))
    assert result['portfolio']['capacity_mw']==0
    assert not result['candidates']
    assert not result['portfolio']['target_met']

def test_zero_weights_and_stable_ties():
    plan=Plan(weights=Weights(resource=0,environment=0,grid=0,buildability=0,reuse=0))
    sites=candidates(plan.region)
    assert rank_candidates(sites,plan)==rank_candidates(list(reversed(sites)),plan) or rank_candidates(sites,plan)['selected_ids']==rank_candidates(list(reversed(sites)),plan)['selected_ids']
    assert all(0<=c['score']<=100 for c in rank_candidates(sites,plan)['candidates'])

def test_geography_contains_entire_footprint():
    sites=candidates('california-nevada')
    plan=Plan(polygon={'type':'Polygon','coordinates':[[[-120,38],[-118,38],[-118,40],[-120,40],[-120,38]]]})
    result=rank_candidates(sites,plan)
    assert result['candidates']
    assert all(-120<=c['longitude']<=-118 and 38<=c['latitude']<=40 for c in result['candidates'])
    assert any('Outside analysis boundary' in c['exclusion_reasons'] for c in result['excluded'])
    crossing=copy.deepcopy(sites[0]);crossing['geometry']['coordinates'][0][0]=[-120.1,39]
    assert not rank_candidates([crossing],plan)['candidates']

def test_radius_and_region_restrict_candidates():
    plan=Plan(radius_km=40,center=(-121.5,38.6))
    result=rank_candidates(candidates(plan.region),plan)
    assert 0<len(result['candidates'])<12
    result=rank_candidates(candidates('california-nevada'),Plan(region='washington'))
    assert not result['candidates']

def test_cache_key_excludes_weights_and_filters_but_includes_geometry():
    a=Plan(); b=Plan(weights=Weights(resource=100),constraints=Constraints(max_grid_km=2),technology='wind',target_mw=500)
    assert analysis_key(a)==analysis_key(b)
    assert analysis_key(a)!=analysis_key(Plan(region='washington'))
    assert analysis_key(a)!=analysis_key(Plan(mode='live'))
    assert analysis_key(a)!=analysis_key(Plan(start_date='2023-01-01',end_date='2024-01-01'))

@pytest.mark.parametrize('query,region,tech,mw',[
    ('Find 100 MW of wind in eastern Washington while avoiding protected areas.','washington','wind',100),
    ('Find 50 MW of solar around Sacramento using zero undeveloped land.','sacramento','solar',50),
    ('Add 1.5 GW of solar and wind in California.','california-nevada','auto',1500),
])
def test_parser(query,region,tech,mw):
    parsed=parse_local(Plan(query=query))
    assert (parsed.region,parsed.technology,parsed.target_mw)==(region,tech,mw)

def test_unknown_geography_and_stretch_technologies_are_rejected():
    with pytest.raises(ValueError): parse_local(Plan(query='Find 200 MW solar in Texas.'))
    with pytest.raises(ValueError): parse_local(Plan(query='Find geothermal in Nevada.'))

def test_invalid_input_is_rejected():
    with pytest.raises(ValidationError): Plan(target_mw=-20)
    with pytest.raises(ValidationError): Plan(weights=Weights(resource=-2))
    with pytest.raises(ValidationError): Plan(radius_km=2)
    with pytest.raises(ValidationError): Plan(polygon={'type':'Point','coordinates':[0,0]})
    with pytest.raises(ValidationError): Plan(end_date='2023-01-01')

def test_api_demo_pipeline_rerank_and_export(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    with TestClient(app) as client:
        response=client.post('/api/runs',json=Plan().model_dump())
        assert response.status_code==200
        run=response.json()['run_id']
        stream=client.get(f'/api/runs/{run}/events').text
        events=[json.loads(line[6:]) for line in stream.splitlines() if line.startswith('data: ')]
        assert [e['stage'] for e in events if e['type']=='progress']==[0,1,2,3,4]
        result=events[-1]['data']
        assert result['mode']=='demo' and result['portfolio']['target_met']
        assert all(c['provenance']=='synthetic' for c in result['candidates'])
        assert all(t.get('tokens_saved') is None for t in result['telemetry'])
        before={c['id']:c['resource_value'] for c in result['candidates']}
        reranked=client.post(f'/api/runs/{run}/rerank',json={'weights':Weights(resource=100,environment=0).model_dump(),'constraints':Constraints(max_grid_km=2).model_dump(),'target_mw':250,'technology':'auto'}).json()
        assert all(c['resource_value']==before[c['id']] for c in reranked['candidates'])
        assert reranked['telemetry']==result['telemetry']
        exported=client.get(f'/api/runs/{run}/export').json()
        assert exported['type']=='FeatureCollection'
        assert exported['properties']['mode']=='demo'
        assert 'synthetic' in exported['properties']['data_notice']
        assert exported['features']
        for feature in exported['features']:
            assert feature['geometry']['type']=='Polygon'
            assert feature['properties']['grid_distance_km']<=2

def test_live_missing_configuration_does_not_fabricate(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    for key in ['EARTH_ENGINE_PROJECT','ELASTICSEARCH_URL','OPENAI_API_KEY']: monkeypatch.delenv(key,raising=False)
    with TestClient(app) as client:
        run=client.post('/api/runs',json=Plan(mode='live').model_dump()).json()['run_id']
        events=client.get(f'/api/runs/{run}/events').text
        assert '"type": "error"' in events
        assert '"type": "result"' not in events

def test_stale_fallback_only_uses_matching_computed_cache(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path));monkeypatch.delenv('EARTH_ENGINE_PROJECT',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    plan=Plan(mode='live')
    # Test-only simulated provider measurements, isolated in tmp_path.
    physical=[{**c,'provenance':'computed'} for c in candidates(plan.region)]
    Cache().set(analysis_key(plan),physical)
    with TestClient(app) as client:
        run=client.post('/api/runs',json=plan.model_dump()).json()['run_id']
        events=client.get(f'/api/runs/{run}/events').text
        result=[json.loads(line[6:]) for line in events.splitlines() if line.startswith('data: ')][-1]['data']
        assert result['stale'] and result['cache_hit']
        assert result['mode']=='live'
        assert result['data_notice'].startswith('STALE')

def test_bad_request_returns_422():
    with TestClient(app) as client:
        assert client.post('/api/runs',json={'target_mw':-1}).status_code==422
        assert client.get('/api/runs/not-a-run').status_code==404
