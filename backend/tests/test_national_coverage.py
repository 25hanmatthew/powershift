import numpy as np
import pytest
from pyogrio.raw import write
from shapely import to_wkb
from shapely.geometry import box, mapping, LineString
from backend import city_search, national_ground
from backend.models import Plan
from backend.scoring import in_geography
from backend.us_states import STATE_NAMES


def response(features):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'features':features}
    return Response()


def feature(name, state, geoid, bounds):
    return {'properties':{'BASENAME':name,'STATE':state,'GEOID':geoid},'geometry':mapping(box(*bounds))}


@pytest.mark.parametrize('name,state,code,bounds',[
    ('Boston','Massachusetts','25',(-71.2,42.2,-71,42.4)),
    ('Austin','TX','48',(-98,30,-97.5,30.5)),
    ('Anchorage','Alaska','02',(-150,61,-149,61.5)),
    ('Urban Honolulu','HI','15',(-158,21,-157.7,21.5)),
    ('Washington','DC','11',(-77.1,38.8,-77,39)),
])
def test_nationwide_boundary_and_ranking(tmp_path,monkeypatch,name,state,code,bounds):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    calls=[]
    def get(url,**kwargs):
        calls.append(kwargs['params']['where'])
        # A CDP must be searchable even if it isn't an incorporated city.
        return response([feature(name,code,code+'00001',bounds)] if '/5/' in url else [])
    monkeypatch.setattr(city_search.httpx,'get',get)
    city=city_search.resolve_city(f'{name}, {state}')
    assert city['region']=='us'
    assert all(f"STATE = '{code}'" in clause for clause in calls)
    plan=Plan(region='us',mode='live',polygon=mapping(box(*bounds)))
    assert in_geography({'longitude':(bounds[0]+bounds[2])/2,'latitude':(bounds[1]+bounds[3])/2,
                         'geometry':mapping(box(*bounds).buffer(-.001))},plan)
    assert len(STATE_NAMES)==51


def test_same_name_in_multiple_states_requires_state(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    features=[feature('Portland','41','4100001',(-123,45,-122,46)),
              feature('Portland','23','2300001',(-71,43,-70,44))]
    monkeypatch.setattr(city_search.httpx,'get',lambda *a,**k:response(features))
    with pytest.raises(ValueError,match='Maine, Oregon'):
        city_search.resolve_city('Portland')


def test_national_screening_requires_local_boundary():
    with pytest.raises(ValueError,match='local boundary'):
        Plan(region='us')


def test_missing_national_data_never_uses_regional_extract(tmp_path,monkeypatch):
    monkeypatch.setenv('HIFLD_NATIONAL',str(tmp_path/'missing.gpkg'))
    with pytest.raises(ValueError,match='regional extracts cannot'):
        national_ground.local_ground((-71.2,42.2,-71,42.4))


def test_spatial_read_clips_local_features_and_preserves_protection(tmp_path):
    path=tmp_path/'protected.gpkg'
    write(path,np.array([to_wkb(box(-71.2,42.2,-71,42.4)),to_wkb(box(-120,38,-119,39))]),
          [],[],driver='GPKG',layer='protected',geometry_type='Polygon',crs='EPSG:4326')
    window=box(-71.1,42.3,-70.9,42.5)
    result=national_ground.read_window(path,['protected'],window,('Polygon','MultiPolygon'))
    assert result.equals(box(-71.1,42.3,-71,42.4))


def test_national_discovery_uses_local_ground_not_regional_files(monkeypatch):
    from backend import urban
    bounds=(-71.1,42.3,-71,42.4)
    footprint=box(-71.09,42.31,-71.089,42.311)
    raw={'elements':[{'type':'way','id':1,'tags':{'building':'office'},
         'geometry':[{'lon':x,'lat':y} for x,y in footprint.exterior.coords]}],
         'retrieved_at':'2026-09-20T00:00:00+00:00'}
    monkeypatch.setattr(national_ground,'local_ground',lambda bounds:(LineString([(-71.095,42.3),(-71.095,42.4)]),footprint))
    monkeypatch.setattr(urban,'solar_resource',lambda *a:{'value':4,'vintage':'test'})
    monkeypatch.setattr(urban,'ground_cached',lambda *a:pytest.fail('Regional layer used outside its coverage'))
    req=urban.UrbanRequest.model_construct(bounds=bounds,region='us',surface='rooftop',target_mw=1)
    candidates,_=urban.discover(req,supplied=(raw,False),city_boundary=box(*bounds))
    assert candidates[0]['protected_overlap_pct']==pytest.approx(100)
    assert 0<candidates[0]['grid_distance_km']<1


@pytest.mark.parametrize('query,official,code', [('Honolulu, HI','Urban Honolulu','15'),
                                              ('New York City, NY','New York','36'),
                                              ('Washington, D.C.','Washington','11')])
def test_familiar_city_names_use_correct_place(tmp_path,monkeypatch,query,official,code):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    def get(url,**kwargs):
        assert f"'{official.upper()}'" in kwargs['params']['where']
        return response([feature(official,code,code+'00001',(-158,21,-157,22))])
    monkeypatch.setattr(city_search.httpx,'get',get)
    assert city_search.resolve_city(query)['name']==official


def test_large_city_query_splits_deduplicates_and_caches(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    city={'geoid':'test','bounds':[-98,30,-97,31],'geometry':mapping(box(-98,30,-97,31))}
    calls=[]
    def fetch(key,query):
        calls.append(key)
        if key=='city-surfaces-v1:test': raise ValueError('Too many urban features.')
        return {'elements':[{'type':'way','id':10}], 'retrieved_at':'2026-09-20T00:00:00+00:00'},False
    monkeypatch.setattr(city_search,'fetch_query',fetch)
    data,hit=city_search.fetch_city_surfaces(city,'office')
    assert len(calls)==5 and len(data['elements'])==1 and not hit
    _,hit=city_search.fetch_city_surfaces(city,'office')
    assert hit and len(calls)==5


def test_tile_failure_never_returns_partial_city(tmp_path,monkeypatch):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    city={'geoid':'test','bounds':[-98,30,-97,31],'geometry':mapping(box(-98,30,-97,31))}
    def fetch(key,query):
        raise ValueError('Too many urban features.' if key=='city-surfaces-v1:test' else 'Upstream failure')
    monkeypatch.setattr(city_search,'fetch_query',fetch)
    with pytest.raises(ValueError,match='Upstream failure'):
        city_search.fetch_city_surfaces(city,'office')
    assert city_search.Cache().get('city-surfaces-tiled-v1:test') is None
