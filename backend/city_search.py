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

CENSUS='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/4'
STATES={'ca':('06','California'),'california':('06','California'),'nv':('32','Nevada'),'nevada':('32','Nevada'),'wa':('53','Washington'),'washington':('53','Washington')}

class CitySearchRequest(BaseModel):
    query: str = Field(min_length=2,max_length=500)

def parse_query(query):
    text=query.strip().rstrip('.?!')
    if re.search(r'\b(geothermal|hydro(?:power|electric)?|biomass|tidal|wave)\b',text,re.I):
        raise ValueError('Solar and onshore wind screening are available. Hydro needs flow/head and habitat data; geothermal needs subsurface resource and well data. Those options are not yet screened.')
    wind=bool(re.search(r'\b(wind|turbines?)\b',text,re.I))
    if wind and re.search(r'\b(rooftops?|roofs?|parking|solar)\b',text,re.I):
        raise ValueError('Wind screening is for land-based sites. Search wind separately from rooftop or parking solar, for example “3 wind sites in Reno, NV”.')
    match=re.search(r'\b(?:in|within|around|near)\s+(?:the city of\s+)?(.+)',text,re.I)
    if match:
        city=re.split(r'\s+(?:with|for|using|under|that|to add|avoiding)\b',match[1],flags=re.I)[0].strip()
    elif re.fullmatch(r"[A-Za-zÀ-ÿ .,'-]{2,80}",text) and not re.search(r'\b(find|solar|roof|rooftops|parking|best|show)\b',text,re.I):
        city=text
    else:
        raise ValueError('Include a city name, for example “Find the 5 best rooftops for solar in Sacramento, CA”.')
    city=re.sub(r'^(?:downtown|city of)\s+','',city,flags=re.I)
    if not re.fullmatch(r"[A-Za-zÀ-ÿ .,'-]{2,80}",city):
        raise ValueError('Use a city name and optional state, such as “Sacramento, CA”.')
    limit_match=re.search(r'(?:^|\b(?:top|best|show(?: me)?|find(?: the)?)\s+)(\d+)\s*(?:best\s+)?(?:rooftops?|roofs?|sites?|locations?|parking|solar|results?|options?)?',text,re.I)
    limit=int(limit_match[1]) if limit_match and not re.match(r'\s*(?:MW|GW|kW|megawatts?)\b',text[limit_match.end(1):],re.I) else 5
    if not 1<=limit<=20: raise ValueError('Ask for between 1 and 20 recommendations; the default is 5.')
    power=re.search(r'\b(\d+(?:\.\d+)?)\s*(MW|GW|kW|megawatts?)\b',text,re.I)
    target=float(power[1])*({'gw':1000,'kw':.001}.get(power[2].lower(),1)) if power else None
    if target is not None and not 0<target<=10000: raise ValueError('Capacity must be above zero and at most 10,000 MW.')
    roof=bool(re.search(r'\broof(?:top)?s?\b',text,re.I));parking=bool(re.search(r'\b(parking|canop(?:y|ies)|carports?)\b',text,re.I))
    surface='all' if roof and parking else 'rooftop' if roof else 'parking_deck' if re.search(r'\b(decks?|garages?|structures?)\b',text,re.I) else 'parking_canopy' if parking else 'all'
    return {'city':city,'limit':limit,'target_mw':target,'surface':surface,'technology':'wind' if wind else 'solar'}

def resolve_city(name):
    state=None;clean=name.strip()
    for suffix,(code,label) in STATES.items():
        match=re.search(r'(?:,\s*|\s+)'+suffix+r'$',clean,re.I)
        if match: state=code;clean=clean[:match.start()].strip();break
    key='city-boundary-census-v1:'+hashlib.sha256((clean.casefold()+':'+str(state)).encode()).hexdigest()
    previous=Cache().get(key)
    if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])).days<30:
        return previous['data']
    escaped=clean.upper().replace("'","''")
    where=f"UPPER(BASENAME) = '{escaped}'"+(f" AND STATE = '{state}'" if state else " AND STATE IN ('06','32','53')")
    response=httpx.get(CENSUS+'/query',params={'f':'geojson','where':where,'outFields':'NAME,BASENAME,STATE,GEOID','outSR':4326,'returnGeometry':'true'},timeout=30)
    response.raise_for_status();data=response.json()
    if data.get('error'): raise ValueError('The city boundary service could not complete this search. Try again.')
    matches=[]
    for feature in data.get('features',[]):
        geom=shape(feature['geometry'])
        if geom.geom_type not in ('Polygon','MultiPolygon'): continue
        # Census rings can touch at annexation boundaries. Repair topology while retaining enclaves.
        if not geom.is_valid:
            geom=make_valid(geom)
            if geom.geom_type=='GeometryCollection':
                geom=unary_union([part for part in geom.geoms if part.geom_type in ('Polygon','MultiPolygon')])
        if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'): continue
        # Coverage is explicit: never quietly replace a requested city with a regional preset.
        region=next((key for key in ('sacramento','california-nevada','washington') if box(*REGIONS[key]['bounds']).covers(geom)),None)
        if region:
            props=feature['properties'];matches.append({'name':props['BASENAME'],'state':next(label for code,label in STATES.values() if code==props['STATE']),
                'geoid':props['GEOID'],'region':region,'geometry':mapping(geom),'bounds':list(geom.bounds),'source_url':CENSUS,'vintage':'2026-01-01'})
    if not matches: raise ValueError('No supported city boundary matched that name. Try Sacramento, Davis, Reno or Spokane. Current coverage is northern California/Nevada and eastern Washington; no alternate city was substituted.')
    if len(matches)>1: raise ValueError('More than one city matches. Add the state to your request.')
    city=matches[0];Cache().set(key,city);return city

def search_city(query):
    parsed=parse_query(query);city=resolve_city(parsed['city']);w,s,e,n=city['bounds']
    if parsed['technology']=='wind': return search_city_wind(query,parsed,city)
    # Focus public/commercial roof tags and parking across the city, keeping public Overpass queries bounded.
    roof_types='commercial|industrial|retail|warehouse|office|school|hospital|university|public|civic|government|college|hotel|sports_centre|supermarket|parking'
    bbox=f'{s},{w},{n},{e}'
    overpass=f'[out:json][timeout:35];(wr["building"~"^({roof_types})$"]({bbox});wr["building"]["name"]({bbox});wr["amenity"="parking"]({bbox}););out meta geom;'
    raw=fetch_query('city-surfaces-v1:'+city['geoid'],overpass)
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

def search_city_wind(query,parsed,city):
    w,s,e,n=city['bounds']
    plan=Plan(region=city['region'],technology='wind',mode='live',query=query,target_mw=parsed['target_mw'] or 1,
        polygon={'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]},constraints={'zero_new_land':False})
    files=[]
    for env in ('HIFLD_GEOJSON','PADUS_GEOJSON'):
        path=Path(os.getenv(env,''));files.append([str(path),path.stat().st_mtime_ns if path.is_file() else None])
    key='city-wind-v1:'+hashlib.sha256(json.dumps([city['geometry'],plan.start_date,plan.end_date,files],sort_keys=True).encode()).hexdigest()
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
