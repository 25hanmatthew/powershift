import pytest
from backend.suppliers import safe_website, search_suppliers, project_profile, parse_builders
from backend.cache import Cache
from backend.main import app
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def company_page(monkeypatch):
 monkeypatch.setattr('backend.suppliers.read_company_page',lambda url:'We install commercial rooftop solar and solar parking canopies in Sacramento.')

SITE = {'id':'site','name':'Roof','longitude':-121.49,'latitude':38.58,'technology':'solar','surface_type':'rooftop','capacity_mw':.276}
CITY = {'name':'Sacramento','state':'CA'}

@pytest.mark.parametrize('value',['javascript:alert(1)','data:text/html,hello','https://user:secret@example.com','https://bad host/','file:///etc/passwd'])
def test_unsafe_contact_urls(value): assert safe_website(value) is None

def test_websites():
 assert safe_website('www.example.com') == 'https://www.example.com'
 assert safe_website('https://example.com/contact?utm_source=tracking') == 'https://example.com/contact'

@pytest.mark.parametrize('technology,surface,expected',[
 ('solar','rooftop','commercial rooftop solar'),('solar','parking_canopy','solar parking canopy'),
 ('solar','parking_deck','solar canopy on a parking structure'),('solar',None,'ground-mounted solar farm'),
 ('wind',None,'onshore wind farm')])
def test_project_match(technology,surface,expected):
 p=project_profile({**SITE,'technology':technology,'surface_type':surface},CITY)
 assert p['kind']==expected and p['technology']==technology
 assert p['location']=='Sacramento, CA' and p['size']=='276 kW'

def source_response(rows,sources=None):
 import json
 return {'status':'completed','output':[
  {'type':'web_search_call','action':{'sources':[{'url':url} for url in (sources or ['https://builder.example/commercial?utm_source=search'])]}},
  {'type':'message','content':[{'type':'output_text','text':json.dumps({'builders':rows})}]}]}

def builder(**kw):
 return {'name':'Builder','role':'Solar EPC','project_fit':'Commercial rooftop solar construction; confirm capacity.',
  'service_area':'Sacramento','evidence':'Company describes commercial installations in Sacramento.',
  'website':'https://builder.example','source_url':'https://builder.example/commercial',
  'phone':None,'address':'Not specified','technology':'solar','construction_services':True,**kw}

def test_provenance_and_construction_filter():
 rows=[builder(),builder(),builder(technology='wind'),builder(construction_services=False),
       builder(website='https://unrelated.example'),builder(source_url='https://builder.example/invented')]
 result=parse_builders(source_response(rows),project_profile(SITE,CITY))
 assert len(result)==1 and result[0]['address'] is None
 assert result[0]['source_url']=='https://builder.example/commercial'
 assert 'distance_km' not in result[0] and 'directions_url' not in result[0]

def test_unsupported_results_fail_without_fallback():
 with pytest.raises(ValueError,match='source evidence'):
  parse_builders(source_response([builder(source_url='https://invented.example')]),project_profile(SITE,CITY))
 with pytest.raises(ValueError,match='did not finish'):
  parse_builders({'status':'incomplete'},project_profile(SITE,CITY))

def test_cache_is_scoped_to_project(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path));calls=[]
 def find(profile): calls.append(profile);return [builder()]
 monkeypatch.setattr('backend.suppliers.fetch_builders',find)
 assert not search_suppliers(SITE,CITY)['cache_hit']
 assert search_suppliers(SITE,CITY)['cache_hit']
 assert not search_suppliers({**SITE,'surface_type':'parking_canopy'},CITY)['cache_hit']
 assert not search_suppliers({**SITE,'capacity_mw':20},CITY)['cache_hit']
 assert len(calls)==3

def test_endpoint_uses_only_selected_live_site(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path));cache=Cache()
 cache.set('run:live',{'result':{'mode':'live','candidates':[SITE],'city':CITY}})
 cache.set('run:demo',{'result':{'mode':'demo','candidates':[SITE]}})
 calls=[]
 def find(site,city):calls.append((site,city));return {'builders':[]}
 monkeypatch.setattr('backend.main.search_suppliers',find)
 with TestClient(app) as client:
  for run,id,status in [('missing','site',404),('demo','site',422),('live','missing',404)]:
   assert client.post('/api/suppliers/search',json={'run_id':run,'site_id':id}).status_code==status
  assert not calls
  assert client.post('/api/suppliers/search',json={'run_id':'live','site_id':'site','technology':'hydro'}).status_code==422
  assert client.post('/api/suppliers/search',json={'run_id':'live','site_id':'site'}).status_code==200
  assert calls==[(SITE,CITY)]

def test_empty_search_can_be_retried(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path));calls=[]
 def find(profile): calls.append(profile);return []
 monkeypatch.setattr('backend.suppliers.fetch_builders',find)
 assert not search_suppliers(SITE,CITY)['builders']
 assert not search_suppliers(SITE,CITY)['cache_hit']
 assert len(calls)==2

def test_provider_failure_is_not_cached(tmp_path,monkeypatch):
 monkeypatch.setenv('CACHE_DIR',str(tmp_path));calls=[]
 def find(profile): calls.append(profile);raise ValueError('Search unavailable')
 monkeypatch.setattr('backend.suppliers.fetch_builders',find)
 for _ in range(2):
  with pytest.raises(ValueError,match='Search unavailable'): search_suppliers(SITE,CITY)
 assert len(calls)==2


def test_generic_canopy_contractor_is_excluded():
 profile=project_profile({**SITE,'surface_type':'parking_canopy'},CITY)
 rows=[builder(project_fit='Installs shade canopies',evidence='Design and fabrication of aluminum awnings.'),
       builder(project_fit='Designs and installs solar parking canopies.',evidence='Solar carport construction in Sacramento.')]
 result=parse_builders(source_response(rows),profile)
 assert len(result)==1 and 'solar parking' in result[0]['project_fit']


def test_model_claim_without_page_capability_is_rejected(monkeypatch):
 monkeypatch.setattr('backend.suppliers.read_company_page',lambda url:'We build aluminum awnings and shade structures.')
 with pytest.raises(ValueError,match='source evidence'):
  parse_builders(source_response([builder(project_fit='Installs solar parking canopies.')]),
                project_profile({**SITE,'surface_type':'parking_canopy'},CITY))

def test_builder_details_are_extracted_from_source_and_questions_follow_project():
 from backend.suppliers import builder_details
 roof=builder_details('We offer design, installation, permitting and monitoring. Email team@example.com.',project_profile(SITE,CITY))
 assert roof['email']=='team@example.com'
 assert 'Design & engineering' in roof['services'] and 'Financing support' not in roof['services']
 assert any('roof loading' in q for q in roof['questions'])
 canopy=builder_details('Solar canopy construction.',project_profile({**SITE,'surface_type':'parking_canopy'},CITY))
 assert any('vehicle clearance' in q for q in canopy['questions'])
 assert not any('roof loading' in q for q in canopy['questions'])
