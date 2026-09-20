import json
import os
import re
import time
from pathlib import Path
import httpx
from .openai_config import openai_model, response_settings
from .models import Plan, REGIONS

REGISTRY=json.loads((Path(__file__).resolve().parent.parent/'data/datasets.json').read_text(encoding='utf-8'))

def service_status():
    configured = {
        'earth_engine': bool(os.getenv('EARTH_ENGINE_PROJECT')),
        'elasticsearch': bool(os.getenv('ELASTICSEARCH_URL') and os.getenv('ELASTICSEARCH_API_KEY')),
        'semantic_search': bool(os.getenv('ELASTIC_INFERENCE_ID')),
        'openai': bool(os.getenv('OPENAI_API_KEY')),
        'transmission': Path(os.getenv('HIFLD_GEOJSON','data/private/transmission.geojson')).is_file(),
        'protected_areas': Path(os.getenv('PADUS_GEOJSON','data/private/protected.geojson')).is_file(),
    }
    return {'services':configured,'live_ready':all(configured[k] for k in ['earth_engine','elasticsearch','transmission','protected_areas']),
            'note':'Configured means settings are present; connectivity is verified during a live run.'}

def parse_local(plan: Plan):
    query=plan.query.lower()
    if any(word in query for word in ['geothermal','hydro']):
        raise ValueError('Solar and wind are implemented. Hydro retrofit and geothermal are not yet supported.')
    updates={}
    number=re.search(r'(\d+(?:\.\d+)?)\s*(mw|megawatts?|gw|gigawatts?)\b',query)
    if number: updates['target_mw']=float(number[1])*(1000 if number[2].startswith('g') else 1)
    if 'wind' in query and 'solar' not in query: updates['technology']='wind'
    if 'solar' in query and 'wind' not in query: updates['technology']='solar'
    if 'wind' in query and 'solar' in query: updates['technology']='auto'
    if 'washington' in query: updates['region']='washington'
    elif 'sacramento' in query: updates['region']='sacramento'
    elif 'california' in query or 'nevada' in query: updates['region']='california-nevada'
    elif not plan.polygon:
        # Unknown locations must not be silently mapped to the flagship region.
        place=re.search(r'\b(?:in|around|near)\s+([a-z][a-z\s]+?)(?:[.,]|$|\s+(?:using|with|while|within))',query)
        if place and not any(x in place[1] for x in ['region','transmission','grid','here']):
            raise ValueError('Supported regions are northern California + Nevada, Sacramento, and eastern Washington. Select a region or draw a boundary.')
    constraints=plan.constraints.model_dump()
    if re.search(r'zero[ -](?:new|undeveloped)|no (?:new|undeveloped) land',query): constraints['zero_new_land']=True
    dist=re.search(r'(?:within|under|max(?:imum)?)\s+(\d+(?:\.\d+)?)\s*km\s+(?:of|from|to)\s+(?:the\s+)?(?:grid|transmission)',query)
    if dist: constraints['max_grid_km']=float(dist[1])
    radius=re.search(r'(\d+(?:\.\d+)?)\s*km\s+radius',query)
    if radius:
        region=updates.get('region',plan.region); west,south,east,north=REGIONS[region]['bounds']
        updates.update(radius_km=float(radius[1]),center=[(west+east)/2,(south+north)/2])
    updates['constraints']=constraints
    return Plan.model_validate({**plan.model_dump(),**updates})

async def openai_json(system, data):
    started=time.perf_counter()
    model=openai_model()
    async with httpx.AsyncClient(timeout=60) as client:
        response=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':f"Bearer {os.environ['OPENAI_API_KEY']}"},json={
            **response_settings(model), 'instructions':system,
            'input':[{'role':'user','content':'Return a JSON object for this input:\n'+json.dumps(data)}],
            'text':{'format':{'type':'json_object'}},'store':False})
        response.raise_for_status()
        body=response.json()
    if body.get('status')!='completed':
        raise ValueError('OpenAI did not complete the structured response. Try the analysis again.')
    content=[part for item in body.get('output',[]) if item.get('type')=='message' for part in item.get('content',[])]
    if any(part.get('type')=='refusal' for part in content):
        raise ValueError('OpenAI declined this request. Rephrase the energy-planning goal.')
    text=''.join(part.get('text','') for part in content if part.get('type')=='output_text')
    try:
        result=json.loads(text)
        if not isinstance(result,dict): raise ValueError('Expected a JSON object.')
    except (ValueError,TypeError) as exc:
        raise ValueError('OpenAI returned an invalid planning response. No model decisions were applied.') from exc
    return result, {'model':model,
         'llm_latency_ms':round((time.perf_counter()-started)*1000),'usage':body.get('usage',{})}

def transcription_session():
    return {'type':'session.update','session':{'type':'transcription','audio':{'input':{
        'format':{'type':'audio/pcm','rate':24000},
        'transcription':{'model':os.getenv('OPENAI_TRANSCRIPTION_MODEL') or 'gpt-4o-mini-transcribe',
                         'prompt':'Solar, wind, megawatts, grid transmission, California, Nevada, Sacramento, Washington.'},
        'noise_reduction':{'type':'near_field'},
        'turn_detection':{'type':'server_vad','threshold':0.5,'prefix_padding_ms':300,'silence_duration_ms':900},
    }}}}

async def planner(plan, telemetry):
    local=parse_local(plan)
    if not os.getenv('OPENAI_API_KEY') or plan.mode=='demo':
        telemetry.append({'stage':'planner','provider':'deterministic parser','status':'local','llm_latency_ms':0})
        return local
    result, metrics=await openai_json('Translate this energy request. Return JSON with region (california-nevada, sacramento, washington), technology (auto,solar,wind), target_mw. Do not invent metrics. Keep the supplied defaults for unspecified values. If another region or unsupported technology is requested, return {"unsupported": true}.',local.model_dump())
    if result.get('unsupported'): raise ValueError('This request is outside the implemented solar/wind regions.')
    allowed={k:v for k,v in result.items() if k in ['region','technology','target_mw']}
    telemetry.append({'stage':'planner','provider':'OpenAI','status':'measured',**metrics})
    return Plan.model_validate({**local.model_dump(),**allowed})

class Elastic:
    def __init__(self):
        self.base=os.getenv('ELASTICSEARCH_URL','').rstrip('/')
        self.headers={'Authorization':f"ApiKey {os.getenv('ELASTICSEARCH_API_KEY','')}"}
    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=60) as client:
            res=await client.request(method,self.base+path,headers=self.headers,**kwargs)
            res.raise_for_status()
            return res.json() if res.content else {}
    async def ensure(self):
        dataset_properties={'id':{'type':'keyword'},'name':{'type':'text'},'description':{'type':'text'},
            'energy_types':{'type':'keyword'},'metrics':{'type':'keyword'},'space_derived':{'type':'boolean'}}
        if os.getenv('ELASTIC_INFERENCE_ID'):
            dataset_properties['semantic']={'type':'semantic_text','inference_id':os.environ['ELASTIC_INFERENCE_ID']}
        schemas={'energy_datasets':dataset_properties,'energy_candidates':{
            'id':{'type':'keyword'},'run_id':{'type':'keyword'},'centroid':{'type':'geo_point'},'geometry':{'type':'geo_shape'},
            'technology':{'type':'keyword'},'grid_distance_km':{'type':'float'},'protected_overlap_pct':{'type':'float'},'slope_deg':{'type':'float'},
            'components':{'type':'object'},'capacity_mw':{'type':'float'}}}
        for index, properties in schemas.items():
            try:
                await self.request('GET',f'/{index}')
                await self.request('PUT',f'/{index}/_mapping',json={'properties':properties})
            except httpx.HTTPStatusError as error:
                if error.response.status_code!=404: raise
                await self.request('PUT',f'/{index}',json={'mappings':{'properties':properties}})
        for item in REGISTRY:
            doc=dict(item)
            if os.getenv('ELASTIC_INFERENCE_ID'): doc['semantic']=item['description']+' '+item['limitations']
            await self.request('PUT',f"/energy_datasets/_doc/{item['id']}",json=doc)
        await self.request('POST','/energy_datasets/_refresh')
    async def discover(self, plan, telemetry):
        started=time.perf_counter(); types=['solar','wind'] if plan.technology=='auto' else [plan.technology]
        filters=[{'terms':{'energy_types':types}}]
        lexical={'bool':{'must':{'multi_match':{'query':plan.query+' irradiance slope terrain wind grid protected land','fields':['name^2','description','metrics']}},'filter':filters}}
        body={'size':20,'query':lexical}
        hybrid=bool(os.getenv('ELASTIC_INFERENCE_ID'))
        if hybrid:
            body={'size':20,'retriever':{'rrf':{'retrievers':[{'standard':{'query':lexical}},{'standard':{'query':{'bool':{'must':{'semantic':{'field':'semantic','query':plan.query}},'filter':filters}}}}],'rank_window_size':50}}}
        result=await self.request('POST','/energy_datasets/_search',json=body)
        telemetry.append({'stage':'discovery','provider':'Elasticsearch','operation':'energy_datasets/_search','status':'measured','latency_ms':round((time.perf_counter()-started)*1000),'hybrid':hybrid})
        hits=[h['_source'] for h in result['hits']['hits']]
        required={'worldcover','srtm','viirs','hifld','padus'}|({'power'} if plan.technology=='solar' else {'era5'} if plan.technology=='wind' else {'power','era5'})
        found={h['id'] for h in hits}
        if required-found: raise ValueError('Dataset search did not return every required measurement source. Check the Elastic registry index.')
        return [h for h in hits if h['id'] in required]
    async def store_candidates(self, run_id, candidates):
        lines=[]
        for c in candidates:
            lines.extend([json.dumps({'index':{'_index':'energy_candidates','_id':run_id+':'+c['id']}}),json.dumps({**c,'run_id':run_id,'centroid':{'lat':c['latitude'],'lon':c['longitude']}})])
        if lines:
            async with httpx.AsyncClient(timeout=60) as client:
                res=await client.post(self.base+'/_bulk?refresh=true',headers={**self.headers,'Content-Type':'application/x-ndjson'},content='\n'.join(lines)+'\n')
                res.raise_for_status(); result=res.json()
            if result.get('errors'): raise ValueError('Elasticsearch could not index all candidate measurements.')
    async def retrieve(self, run_id, plan, telemetry):
        started=time.perf_counter(); w,s,e,n=REGIONS[plan.region]['bounds']
        filters=[{'term':{'run_id':run_id}},{'geo_bounding_box':{'centroid':{'top_left':{'lat':n,'lon':w},'bottom_right':{'lat':s,'lon':e}}}}]
        if plan.polygon: filters.append({'geo_shape':{'geometry':{'shape':plan.polygon,'relation':'within'}}})
        if plan.radius_km: filters.append({'geo_distance':{'distance':f'{plan.radius_km}km','centroid':{'lon':plan.center[0],'lat':plan.center[1]}}})
        response=await self.request('POST','/energy_candidates/_search',json={'size':500,'query':{'bool':{'filter':filters}}})
        telemetry.append({'stage':'analysis','provider':'Elasticsearch','operation':'energy_candidates/_search','status':'measured','latency_ms':round((time.perf_counter()-started)*1000),'hits':response['hits']['total']['value']})
        return [h['_source'] for h in response['hits']['hits']]
