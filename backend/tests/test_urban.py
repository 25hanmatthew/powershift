import pytest
from pydantic import ValidationError
from shapely.geometry import box, LineString
from shapely.strtree import STRtree
from backend import urban
from backend.models import Plan, Constraints
from backend.scoring import rank_candidates
from fastapi.testclient import TestClient
from backend.main import app

BOUNDS=(-121.505,38.575,-121.485,38.59)
def way(id,tags):
    coords=list(box(-121.50,38.58,-121.499,38.581).exterior.coords)
    return {'type':'way','id':id,'tags':tags,'geometry':[{'lon':x,'lat':y} for x,y in coords]}

@pytest.mark.parametrize('tags,expected',[
    ({'building':'office'},'rooftop'),({'amenity':'parking'},'parking_canopy'),
    ({'amenity':'parking','parking':'multi-storey'},'parking_deck'),
    ({'amenity':'parking','parking':'underground'},None),({'building':'construction'},None)])
def test_surface_classification(tags,expected):
    assert urban.classify(tags)==expected

def test_bounds_and_height():
    for bounds in [(-122,38,-121,39),(-1,0,1,1),(0,0,0,0)]:
        with pytest.raises(ValidationError): urban.UrbanRequest(bounds=bounds)
    assert urban.height({'height':'100 ft'},'rooftop')[0]==pytest.approx(30.48)
    assert urban.height({'building:levels':'5'},'parking_deck')[0]==16

def test_relation_retains_courtyard():
    outer=way(1,{})['geometry'];inner=[{'lon':x,'lat':y} for x,y in box(-121.4998,38.5802,-121.4992,38.5808).exterior.coords]
    geom=urban.osm_polygons({'type':'relation','members':[{'role':'outer','geometry':outer},{'role':'inner','geometry':inner}]})[0]
    assert len(geom.interiors)==1
    assert geom.area==pytest.approx(.000001-.00000036)

@pytest.fixture
def screened(monkeypatch,tmp_path):
    for env in ['HIFLD_GEOJSON','PADUS_GEOJSON']:
        path=tmp_path/env;path.write_text('{}');monkeypatch.setenv(env,str(path))
    raw={'elements':[way(1,{'building':'parking','building:levels':'5'}),way(2,{'amenity':'parking'})],'retrieved_at':'2026-09-19T00:00:00+00:00'}
    monkeypatch.setattr(urban,'fetch_osm',lambda bounds:(raw,False))
    monkeypatch.setattr(urban,'ground_cached',lambda path,kind,mtime:STRtree([LineString([(-121.5,38.57),(-121.5,38.60)]) if kind=='HIFLD' else box(-122,38,-121.9,38.1)]))
    monkeypatch.setattr(urban,'solar_resource',lambda x,y:{'value':5,'vintage':'2001–2020'})
    return urban.discover(urban.UrbanRequest(bounds=BOUNDS))

def test_deduplicate_and_top_area_only(screened):
    candidates,summary=screened
    assert len(candidates)==1 and summary['skipped']==1
    c=candidates[0]
    assert c['surface_type']=='parking_deck' and c['building_height_m']==16
    assert c['capacity_mw']==pytest.approx(c['surface_area_m2']*.45*.0002/1.3,abs=.00001)
    assert c['annual_gwh']==pytest.approx(c['capacity_mw']*1.3*5*.8*365/1000,abs=.00002)
    result=rank_candidates(candidates,Plan(region='sacramento',target_mw=.1,constraints=Constraints(zero_new_land=True)))
    assert len(result['candidates'])==1
    assert any(v['label']=='Structural suitability' and not v['passed'] for v in result['verification'])

@pytest.mark.parametrize('boundary',[
    box(-121.50,38.58,-121.4995,38.581),
    box(*BOUNDS).difference(box(-121.4998,38.5802,-121.4992,38.5808)),
])
def test_city_boundary_rejects_crossing_footprints_and_enclaves(boundary):
    raw={'elements':[way(1,{'building':'office'})],'retrieved_at':'2026-09-19T00:00:00+00:00'}
    with pytest.raises(ValueError,match='No complete mapped surfaces'):
        urban.discover(urban.UrbanRequest(bounds=BOUNDS),supplied=(raw,False),city_boundary=boundary)

def test_city_boundary_preserves_fully_contained_footprint(screened):
    raw={'elements':[way(1,{'building':'office'})],'retrieved_at':'2026-09-19T00:00:00+00:00'}
    found,_=urban.discover(urban.UrbanRequest(bounds=BOUNDS),supplied=(raw,False),city_boundary=box(*BOUNDS))
    assert len(found)==1

def test_live_route_and_export_preserve_evidence(screened,monkeypatch,tmp_path):
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    monkeypatch.setattr('backend.main.discover_urban',lambda req:screened)
    with TestClient(app) as client:
        response=client.post('/api/urban/search',json={'bounds':BOUNDS,'region':'sacramento','target_mw':.1})
        assert response.status_code==200,response.text
        result=response.json();assert result['mode']=='live'
        exported=client.get('/api/runs/'+result['run_id']+'/export').json()
        c=exported['features'][0]['properties']
        assert c['surface_type']=='parking_deck' and c['source_url'].startswith('https://www.openstreetmap.org/')
