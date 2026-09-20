import copy
import pytest
from pyproj import Geod
from shapely.geometry import box, mapping, shape
from backend.city_search import parse_query, search_city
from backend.regional_search import regional_boundary, diverse_shortlist
from backend.demo import candidates
from backend.main import app
from fastapi.testclient import TestClient

QUERY='What is the best way to increase renewable energy in Sacramento, CA and surrounding areas?'

def test_plain_regional_request():
    parsed=parse_query(QUERY)
    assert parsed['city']=='Sacramento, CA'
    assert parsed['scope']=='regional' and parsed['technology']=='auto'
    assert parsed['limit']==8
    assert parse_query('Compare solar and wind in Sacramento and surrounding areas')['scope']=='regional'
    assert parse_query('Find wind in Sacramento and surrounding areas')['technology']=='wind'
    assert parse_query('Find solar rooftops in Sacramento and surrounding areas')['surface']=='rooftop'

def test_radius_is_measured_and_not_city_limits():
    city={'geometry':mapping(box(-121.5,38.5,-121.4,38.6))}
    boundary=regional_boundary(city)
    center=shape(city['geometry']).centroid
    assert boundary.covers(shape(city['geometry'])) and len(boundary.exterior.coords)==65
    for lon,lat in boundary.exterior.coords:
        _,_,distance=Geod(ellps='WGS84').inv(center.x,center.y,lon,lat)
        assert distance==pytest.approx(40000,abs=.01)

@pytest.fixture
def regional_data(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    city={'name':'Sacramento','state':'California','geoid':'test','region':'sacramento',
          'geometry':mapping(box(-121.5,38.5,-121.4,38.6)),'bounds':[-121.5,38.5,-121.4,38.6],
          'source_url':'https://example.test','vintage':'test'}
    monkeypatch.setattr('backend.city_search.resolve_city',lambda name:copy.deepcopy(city))
    monkeypatch.setattr('backend.regional_search.fingerprint',lambda:'test-files')
    monkeypatch.setattr('backend.city_search.fetch_city_surfaces',lambda *a:({'elements':[]},False))
    seed=next(c for c in candidates('sacramento') if c['technology']=='solar')
    def site(identifier,lon,lat,**extra):
        return {**copy.deepcopy(seed),'id':identifier,'site_id':identifier,'longitude':lon,'latitude':lat,
                'geometry':mapping(box(lon-.001,lat-.001,lon+.001,lat+.001)),
                'capacity_mw':1,'protected_overlap_pct':0,'slope_deg':0,'grid_distance_km':1,
                'developed_pct':0,'excluded_land_cover':False,'resource_value':6,
                'provenance':'computed',**extra}
    urban=[site('roof',-121.45,38.55,surface_type='rooftop',developed_surface_verified=True),
           site('parking',-121.44,38.55,surface_type='parking_canopy',developed_surface_verified=True)]
    land=[site('ground',-121.55,38.55),site('wind',-121.55,38.55,technology='wind',site_id='ground'),
          site('overlap',-121.45,38.55),site('low-wind',-121.65,38.55,technology='wind',resource_value=4),
          site('protected',-121.6,38.55,protected_overlap_pct=1)]
    calls=[]
    def discover(*args,**kwargs):
        assert kwargs['screening_limit']==500
        calls.append('urban')
        return urban,{'retrieved_at':'2026-09-20T00:00:00+00:00','source_timestamp':'test','omitted':0,'skipped':0}
    def analyze(plan,**kwargs):
        calls.append('land')
        assert plan.region=='us' and kwargs['grid_size']==7
        assert kwargs['technologies']==['solar','wind']
        return land
    monkeypatch.setattr('backend.regional_search.discover',discover)
    monkeypatch.setattr('backend.regional_search.analyze',analyze)
    return calls

def test_real_source_union_exclusions_and_unique_capacity(regional_data):
    with TestClient(app) as client:
        response=client.post('/api/city/search',json={'query':QUERY})
        assert response.status_code==200,response.text
        data=response.json()
        assert data['city']['scope']=='regional' and data['city']['radius_km']==40
        assert data['city']['screened']==7
        assert {c['id'] for c in data['candidates']}=={'roof','parking','ground'}
        assert data['portfolio']['capacity_mw']==3
        excluded={c['id']:c for c in data['excluded']}
        assert 'Mean wind below 5.8 m/s' in excluded['low-wind']['exclusion_reasons']
        assert 'Overlaps a screened urban surface' in excluded['overlap']['exclusion_reasons']
        assert 'Protected land' in excluded['protected']['exclusion_reasons']
        assert len(data['urban_summary']['comparison'])==4
        assert {d['id'] for d in data['datasets']}=={'osm-urban','power','era5','srtm','viirs','worldcover','hifld','padus','isd','pudl'}
        assert '40 km regional radius' in data['data_notice']
        assert not data['cache_hit'] and data['telemetry'][0]['earth_engine_executions']==1
        again=client.post('/api/city/search',json={'query':QUERY}).json()
        assert again['cache_hit'] and again['telemetry'][0]['earth_engine_executions']==0
        assert regional_data==['urban','land']

def test_empty_wind_is_reported_not_invented(regional_data,monkeypatch):
    monkeypatch.setattr('backend.regional_search.analyze',lambda *a,**k:[])
    result=search_city(QUERY)
    row=next(r for r in result[3]['comparison'] if r['id']=='wind')
    assert row['eligible']==0 and row['best_score'] is None

def test_shortlist_never_uses_two_technologies_on_same_cell():
    options=[{'id':'s','site_id':'cell','technology':'solar'},
             {'id':'w','site_id':'cell','technology':'wind'},
             {'id':'w2','site_id':'cell2','technology':'wind'}]
    assert [c['id'] for c in diverse_shortlist(options,8)]==['s','w2']

def test_shortlist_balances_approaches_instead_of_filling_with_one_type():
    options=[{'id':f'{kind}{i}','site_id':f'{kind}{i}','technology':'solar','surface_type':kind}
             for kind in ['rooftop','parking_canopy'] for i in range(8)]
    selected=diverse_shortlist(options,8)
    assert sum(c['surface_type']=='rooftop' for c in selected)==4
    assert sum(c['surface_type']=='parking_canopy' for c in selected)==4
