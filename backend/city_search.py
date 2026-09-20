"""Plain-text city screening. City boundaries and calculations never come from an LLM."""
import hashlib
import re
import os
import json
from pathlib import Path
from datetime import datetime, timezone
import httpx
from pydantic import BaseModel, Field
from shapely.geometry import shape, box, mapping
from shapely import make_valid
from shapely.ops import unary_union
from .cache import Cache
from .models import REGIONS, Plan
from .urban import UrbanRequest, discover, fetch_query
from .scoring import rank_candidates
from .earth import analyze as analyze_land
from .us_states import STATES, STATE_NAMES
from concurrent.futures import ThreadPoolExecutor

CENSUS='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/4'
CENSUS_BASE = CENSUS.rsplit('/', 1)[0]

class CitySearchRequest(BaseModel):
    query: str = Field(min_length=2,max_length=500)

def parse_query(query):
    text=query.strip().rstrip('.?!')
    if re.search(r'\b(geothermal|hydro(?:power|electric)?|biomass|tidal|wave)\b',text,re.I):
        raise ValueError('Solar and onshore wind screening are available. Hydro needs flow/head and habitat data; geothermal needs subsurface resource and well data. Those options are not yet screened.')
    regional=bool(re.search(r'\b(surrounding|nearby areas)\b',text,re.I))
    wind=bool(re.search(r'\b(wind|turbines?)\b',text,re.I))
    if not regional and wind and re.search(r'\b(rooftops?|roofs?|parking|solar)\b',text,re.I):
        raise ValueError('Wind screening is for land-based sites. Search wind separately from rooftop or parking solar, for example “3 wind sites in Reno, NV”.')
    match=re.search(r'\b(?:in|within|around|near)\s+(?:the city of\s+)?(.+)',text,re.I)
    if match:
        city=re.split(r'\s+(?:with|for|using|under|that|to add|avoiding)\b',match[1],flags=re.I)[0].strip()
    elif re.fullmatch(r"[A-Za-zÀ-ÿ .,'-]{2,80}",text) and not re.search(r'\b(find|solar|roof|rooftops|parking|best|show)\b',text,re.I):
        city=text
    else:
        raise ValueError('Include a city name, for example “Find the 5 best rooftops for solar in Sacramento, CA”.')
    if regional:
        city=re.split(r'\s+(?:and|&)\s+(?:the\s+)?(?:surrounding|nearby)',city,flags=re.I)[0].strip()
    city=re.sub(r'^(?:downtown|city of)\s+','',city,flags=re.I)
    if not re.fullmatch(r"[A-Za-zÀ-ÿ .,'-]{2,80}",city):
        raise ValueError('Use a city name and optional state, such as “Sacramento, CA”.')
    limit_match=re.search(r'(?:^|\b(?:top|best|show(?: me)?|find(?: the)?)\s+)(\d+)\s*(?:best\s+)?(?:rooftops?|roofs?|sites?|locations?|parking|solar|results?|options?)?',text,re.I)
    limit=int(limit_match[1]) if limit_match and not re.match(r'\s*(?:MW|GW|kW|megawatts?)\b',text[limit_match.end(1):],re.I) else (8 if regional else 5)
    if not 1<=limit<=20: raise ValueError('Ask for between 1 and 20 recommendations; the default is 5.')
    power=re.search(r'\b(\d+(?:\.\d+)?)\s*(MW|GW|kW|megawatts?)\b',text,re.I)
    target=float(power[1])*({'gw':1000,'kw':.001}.get(power[2].lower(),1)) if power else None
    if target is not None and not 0<target<=10000: raise ValueError('Capacity must be above zero and at most 10,000 MW.')
    roof=bool(re.search(r'\broof(?:top)?s?\b',text,re.I));parking=bool(re.search(r'\b(parking|canop(?:y|ies)|carports?)\b',text,re.I))
    surface='all' if roof and parking else 'rooftop' if roof else 'parking_deck' if re.search(r'\b(decks?|garages?|structures?)\b',text,re.I) else 'parking_canopy' if parking else 'all'
    solar=bool(re.search(r'\bsolar\b',text,re.I)) or roof or parking
    technology=('auto' if (wind and solar) or not (wind or solar) else 'wind' if wind else 'solar') if regional else 'wind' if wind else 'solar'
    return {'city':city,'limit':limit,'target_mw':target,'surface':surface,'technology':technology,'scope':'regional' if regional else 'city'}

def resolve_city(name):
    state=None;clean=re.sub(r'\bD\.C\.?$', 'DC', name.strip(), flags=re.I)
    if clean.casefold() in ('washington dc', 'washington, dc', 'district of columbia'):
        clean='Washington, DC'
    for suffix,(code,label) in sorted(STATES.items(), key=lambda item: -len(item[0])):
        match=re.search(r'(?:,\s*|\s+)'+suffix+r'$',clean,re.I)
        if match: state=code;clean=clean[:match.start()].strip();break
    if clean.casefold() in ('new york city','nyc') and state in (None,'36'): clean='New York';state='36'
    if clean.casefold()=='honolulu' and state in (None,'15'): clean='Urban Honolulu';state='15'
    key='city-boundary-census-us-v3:'+hashlib.sha256((clean.casefold()+':'+str(state)).encode()).hexdigest()
    previous=Cache().get(key)
    if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])).days<30:
        return previous['data']
    escaped=clean.upper().replace("'","''")
    where=f"UPPER(BASENAME) = '{escaped}'"+(f" AND STATE = '{state}'" if state else " AND STATE IN ("+','.join("'"+code+"'" for code in STATE_NAMES)+")")
    def query_layer(layer):
        url=f'{CENSUS_BASE}/{layer}'
        response=httpx.get(url+'/query',params={'f':'geojson','where':where,'outFields':'NAME,BASENAME,STATE,GEOID','outSR':4326,'returnGeometry':'true'},timeout=40)
        response.raise_for_status();data=response.json()
        if data.get('error') or data.get('exceededTransferLimit'):
            raise ValueError('The city boundary service could not complete this search. Add a state or retry.')
        return [dict(feature, source_url=url) for feature in data.get('features',[])]
    # Honolulu and other unincorporated communities are Census-designated places.
    with ThreadPoolExecutor(max_workers=2) as pool:
        features=[feature for group in pool.map(query_layer,(4,5)) for feature in group]
    if not features:
        features=query_layer(1)  # New England towns and other county subdivisions.
    matches=[]
    seen=set()
    for feature in features:
        props=feature['properties']
        if props.get('STATE') not in STATE_NAMES or (state and props.get('STATE')!=state): continue
        if props.get('BASENAME','').casefold()!=clean.casefold(): continue
        if props['GEOID'] in seen: continue
        seen.add(props['GEOID'])
        geom=shape(feature['geometry'])
        if geom.geom_type not in ('Polygon','MultiPolygon'): continue
        # Census rings can touch at annexation boundaries. Repair topology while retaining enclaves.
        if not geom.is_valid:
            geom=make_valid(geom)
            if geom.geom_type=='GeometryCollection':
                geom=unary_union([part for part in geom.geoms if part.geom_type in ('Polygon','MultiPolygon')])
        if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'): continue
        # Coverage is explicit: never quietly replace a requested city with a regional preset.
        region=next((key for key in ('sacramento','california-nevada','washington') if box(*REGIONS[key]['bounds']).covers(geom)), 'us')
        if region:
            props=feature['properties'];matches.append({'name':props['BASENAME'],'state':STATE_NAMES[props['STATE']],
                'geoid':props['GEOID'],'region':region,'geometry':mapping(geom),'bounds':list(geom.bounds),'source_url':feature['source_url'],'vintage':'Current TIGERweb boundary; retrieved '+datetime.now(timezone.utc).date().isoformat()})
    if not matches: raise ValueError('No US city or Census place matched that name. Include the city and state, for example Sacramento, CA or Austin, TX. No alternate city was substituted.')
    if len(matches)>1: raise ValueError('More than one city matches. Add the state: '+', '.join(sorted({c['state'] for c in matches}))+'.')
    city=matches[0];Cache().set(key,city);return city

def search_city(query):
    parsed=parse_query(query);city=resolve_city(parsed['city']);w,s,e,n=city['bounds']
    if parsed['scope']=='regional':
        from .regional_search import search_region
        return search_region(query,parsed,city)
    if parsed['technology']=='wind': return search_city_wind(query,parsed,city)
    # Focus public/commercial roof tags and parking across the city, keeping public Overpass queries bounded.
    roof_types='commercial|industrial|retail|warehouse|office|school|hospital|university|public|civic|government|college|hotel|sports_centre|supermarket|parking'
    raw=fetch_city_surfaces(city,roof_types)
    # This trusted city boundary is resolved above; the public neighborhood endpoint keeps its 36 km² limit.
    req=UrbanRequest.model_construct(bounds=tuple(city['bounds']),region=city['region'],surface=parsed['surface'],target_mw=parsed['target_mw'] or 1)
    physical,summary=discover(req,supplied=raw,city_boundary=shape(city['geometry']))
    polygon={'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}
    plan=Plan(region=city['region'],technology='solar',mode='live',query=query,target_mw=parsed['target_mw'] or 1,polygon=polygon,constraints={'zero_new_land':True})
    all_ranked=rank_candidates(physical,plan)
    shortlist=all_ranked['candidates'][:parsed['limit']]
    if parsed['target_mw'] is None: plan.target_mw=max(.001,sum(c['capacity_mw'] for c in shortlist))
    ranked=rank_candidates(shortlist,plan)
    city.update(limit=parsed['limit'],target_specified=parsed['target_mw'] is not None,screened=len(physical),eligible=len(all_ranked['candidates']),excluded=len(all_ranked['excluded']))
    summary['note']=f"Showing {len(shortlist)} recommendations within {city['name']}, {city['state']}, from {len(physical)} screened surfaces. Citywide discovery covers mapped commercial/public or named building roofs and parking areas; untagged and residential roofs may be absent. "+summary['note']
    return ranked,shortlist,plan,summary,city

def fetch_city_surfaces(city,roof_types):
    """Split oversized city queries, preserving all returned geometry and cache reuse."""
    key='city-surfaces-tiled-v1:'+city['geoid']
    cache=Cache();previous=cache.get(key)
    if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])).days<7:
        return previous['data'],True
    boundary=shape(city['geometry'])
    def fetch(bounds,depth=0):
        w,s,e,n=bounds
        if not boundary.intersects(box(*bounds)):
            return []
        bbox=f'{s},{w},{n},{e}'
        query=f'[out:json][timeout:35];(wr["building"~"^({roof_types})$"]({bbox});wr["building"]["name"]({bbox});wr["amenity"="parking"]({bbox}););out meta geom;'
        part_key=('city-surfaces-v1:'+city['geoid'] if depth==0 else key+':'+hashlib.sha256(bbox.encode()).hexdigest())
        try:
            return [fetch_query(part_key,query)[0]]
        except ValueError as exc:
            if depth>=3 or not any(message in str(exc) for message in ('Too many urban features','timed out')):
                raise
            mx=(w+e)/2;my=(s+n)/2
            tiles=((w,s,mx,my),(mx,s,e,my),(w,my,mx,n),(mx,my,e,n))
            # Parallelize the first split only, keeping service load bounded.
            if depth==0:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    return [part for parts in pool.map(lambda tile:fetch(tile,depth+1),tiles) for part in parts]
            return [part for tile in tiles for part in fetch(tile,depth+1)]
    parts=fetch(city['bounds'])
    elements={(el['type'],el['id']):el for part in parts for el in part['elements']}
    data={'elements':list(elements.values()),'retrieved_at':max(part['retrieved_at'] for part in parts),
          'osm3s':{'timestamp_osm_base':min(part.get('osm3s',{}).get('timestamp_osm_base',part['retrieved_at']) for part in parts)}}
    cache.set(key,data)
    return data,False


def search_city_wind(query,parsed,city):
    w,s,e,n=city['bounds']
    plan=Plan(region=city['region'],technology='wind',mode='live',query=query,target_mw=parsed['target_mw'] or 1,
        polygon={'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]},constraints={'zero_new_land':False})
    files=[]
    for env in ('HIFLD_GEOJSON','PADUS_GEOJSON'):
        path=Path(os.getenv(env,''));files.append([str(path),path.stat().st_mtime_ns if path.is_file() else None])
    if city['region']=='us':
        from .national_ground import fingerprint
        files.append(fingerprint())
    key='city-wind-v3:'+hashlib.sha256(json.dumps([city['geometry'],plan.start_date,plan.end_date,files],sort_keys=True).encode()).hexdigest()
    cache=Cache();stored=cache.get(key);hit=stored is not None
    boundary=shape(city['geometry'])
    if stored: physical=stored['data']
    else:
        try: physical=analyze_land(plan,boundary=boundary,technologies=['wind'])
        except ValueError as exc:
            if 'No complete 2 km screening cells' not in str(exc): raise
            physical=[]
        cache.set(key,physical);stored=cache.get(key)
    # Keep exact boundary checks on cached data too. Existing hard exclusions reject protected/steep sites.
    physical=[dict(c) for c in physical if c['technology']=='wind' and boundary.covers(shape(c['geometry']))]
    for c in physical:
        # Conservative screening assumptions, not a turbine performance or permitting assessment.
        c['excluded_land_cover']=bool(c.get('excluded_land_cover') or c['developed_pct']>5 or c['resource_value']<5.8)
        c['limitations']=[*c.get('limitations',[]),'City wind filter assumes >=5.8 m/s mean 100 m wind and <=5% built-up cover. Building setbacks, aviation, noise, turbulence and parcels are not assessed.']
    all_ranked=rank_candidates(physical,plan);shortlist=all_ranked['candidates'][:parsed['limit']]
    if parsed['target_mw'] is None: plan.target_mw=max(.001,sum(c['capacity_mw'] for c in shortlist))
    ranked=rank_candidates(shortlist,plan)
    city.update(limit=parsed['limit'],technology='wind',target_specified=parsed['target_mw'] is not None,screened=len(physical),eligible=len(all_ranked['candidates']),excluded=len(all_ranked['excluded']))
    note=(f"Showing {len(shortlist)} onshore wind recommendations inside {city['name']} from {len(physical)} complete 2 km screening cells. " if shortlist else f"No suitable onshore wind screening cells found inside {city['name']}. ")
    note+='ERA5 mean wind at 100 m, WorldCover, terrain and protected-area screening. Assumed filters: at least 5.8 m/s and no more than 5% built-up cover. These are preliminary land areas, not approved turbine locations; rooftop wind is not modeled.'
    summary={'surface':'wind','cache_hit':hit,'retrieved_at':stored['created_at'],'source_timestamp':stored['created_at'],'omitted':0,'skipped':len(all_ranked['excluded']),'note':note,'earth_engine_executions':0 if hit or not physical else 1}
    return ranked,shortlist,plan,summary,city
