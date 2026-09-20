import pytest
from shapely.geometry import box,mapping,Polygon,shape
from backend.city_search import parse_query,resolve_city,search_city
from backend.demo import candidates
from backend.models import Plan
from backend.main import app
from fastapi.testclient import TestClient

@pytest.mark.parametrize('query,city,count,surface,target',[
 ('Find the 5 best rooftops for solar in Sacramento, CA','Sacramento, CA',5,'rooftop',None),
 ('3 parking lots in Reno, NV','Reno, NV',3,'parking_canopy',None),
 ('Show me 2 parking structures within Spokane, WA','Spokane, WA',2,'parking_deck',None),
 ('Find 500 kW of solar on rooftops and parking in Davis','Davis',5,'all',.5),
 ('Sacramento','Sacramento',5,'all',None),
 ('Find 1 MW of solar in Sacramento','Sacramento',5,'all',1),
])
def test_plain_language(query,city,count,surface,target):
 p=parse_query(query);assert (p['city'],p['limit'],p['surface'],p['target_mw'])==(city,count,surface,target)

@pytest.mark.parametrize('query',['Find solar','Find 50 rooftops in Reno','Find geothermal in Sacramento','Find 0 MW solar in Reno'])
def test_rejects_unsupported_or_missing_parameters(query):
 with pytest.raises(ValueError):parse_query(query)

def test_boundary_resolver_never_substitutes_wrong_state(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path))
 class Response:
  def raise_for_status(self):pass
  def json(self):return {'features':[{'properties':{'BASENAME':'Sacramento','STATE':'21','GEOID':'2167638'},'geometry':mapping(box(-87.27,37.41,-87.26,37.42))}]}
 monkeypatch.setattr('backend.city_search.httpx.get',lambda *a,**k:Response())
 with pytest.raises(ValueError,match='No supported city'):resolve_city('Sacramento')

def test_boundary_repairs_provider_ring_self_intersection(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path))
 invalid=Polygon([(-119.8,39.5),(-119.7,39.6),(-119.8,39.6),(-119.7,39.5),(-119.8,39.5)])
 assert not invalid.is_valid
 class Response:
  def raise_for_status(self):pass
  def json(self):return {'features':[{'properties':{'BASENAME':'Reno','STATE':'32','GEOID':'3260600'},'geometry':mapping(invalid)}]}
 monkeypatch.setattr('backend.city_search.httpx.get',lambda *a,**k:Response())
 resolved=resolve_city('Reno, NV');boundary=shape(resolved['geometry'])
 assert boundary.is_valid and not boundary.is_empty
 assert resolved['name']=='Reno' and resolved['state']=='Nevada'
 assert boundary.bounds==invalid.bounds

def test_shortlist_capacity_and_live_route(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path))
 city={'name':'Sacramento','state':'California','geoid':'0664000','region':'sacramento','geometry':mapping(box(-122,38,-120.8,39.2)),'bounds':[-122,38,-120.8,39.2],'source_url':'https://example.test','vintage':'2026-01-01'}
 monkeypatch.setattr('backend.city_search.resolve_city',lambda name:dict(city))
 monkeypatch.setattr('backend.city_search.fetch_query',lambda *a:({'elements':[]},True))
 physical=[dict(c,provenance='computed',surface_type='rooftop',developed_surface_verified=True) for c in candidates('sacramento') if c['technology']=='solar']
 summary={'cache_hit':True,'retrieved_at':'2026-09-20T00:00:00+00:00','note':'Test observations','omitted':0,'skipped':0,'source_timestamp':'2026-09-20T00:00:00Z'}
 monkeypatch.setattr('backend.city_search.discover',lambda req,**kwargs:(physical,dict(summary)))
 with TestClient(app) as client:
  response=client.post('/api/city/search',json={'query':'Find 2 rooftops in Sacramento'})
  assert response.status_code==200,response.text
  result=response.json();assert result['mode']=='live';assert len(result['candidates'])<=2
  assert not result['city']['target_specified']
  assert set(result['selected_ids'])=={c['id'] for c in result['candidates']}
  assert result['portfolio']['capacity_mw']==round(sum(c['capacity_mw'] for c in result['candidates']),2)
  exported=client.get('/api/runs/'+result['run_id']+'/export').json();assert len(exported['features'])<=2


def test_city_wind_uses_land_data_and_rejects_urban_low_wind(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path))
 city={'name':'Reno','state':'Nevada','geoid':'3260600','region':'california-nevada','geometry':mapping(box(-122,38,-118,41)),'bounds':[-122,38,-118,41],'source_url':'https://example.test','vintage':'2026-01-01'}
 monkeypatch.setattr('backend.city_search.resolve_city',lambda name:dict(city))
 seed=next(c for c in candidates('sacramento') if c['technology']=='wind')
 physical=[dict(seed,id='good',site_id='good',provenance='computed',resource_value=7,developed_pct=0,protected_overlap_pct=0,slope_deg=0,grid_distance_km=1,excluded_land_cover=False),
  dict(seed,id='urban',resource_value=7,developed_pct=50,protected_overlap_pct=0,slope_deg=0,grid_distance_km=1),
  dict(seed,id='calm',resource_value=3,developed_pct=0,protected_overlap_pct=0,slope_deg=0,grid_distance_km=1)]
 calls=[]
 def land(plan,**kwargs):
  calls.append(kwargs);return physical
 monkeypatch.setattr('backend.city_search.analyze_land',land)
 with TestClient(app) as client:
  response=client.post('/api/city/search',json={'query':'Find 3 wind sites in Reno, NV'})
  assert response.status_code==200,response.text
  result=response.json();assert result['mode']=='live' and result['plan']['technology']=='wind'
  assert [c['id'] for c in result['candidates']]==['good']
  assert result['city']['excluded']==2
  assert 'era5' in {d['id'] for d in result['datasets']} and 'osm-urban' not in {d['id'] for d in result['datasets']}
  assert calls[0]['technologies']==['wind'] and calls[0]['boundary'].equals(shape(city['geometry']))
  again=client.post('/api/city/search',json={'query':'1 wind site in Reno, NV'}).json()
  assert again['cache_hit'] and len(calls)==1

def test_wind_rejects_roof_and_mixed_requests():
 assert parse_query('3 wind sites in Reno, NV')['technology']=='wind'
 for query in ['Wind on rooftops in Reno','Solar and wind in Reno']:
  with pytest.raises(ValueError,match='land-based'):parse_query(query)
