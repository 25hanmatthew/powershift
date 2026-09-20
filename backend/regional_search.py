"""Compare measured urban and land opportunities inside an explicit regional boundary."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pyproj import CRS, Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree
from .cache import Cache
from .earth import analyze
from .models import Plan
from .national_ground import fingerprint
from .scoring import rank_candidates
from .urban import UrbanRequest, discover

APPROACHES={'rooftop':'Rooftop solar','parking':'Parking solar','ground':'Ground-mounted solar','wind':'Onshore wind'}

def approach(candidate):
    if candidate['technology']=='wind': return 'wind'
    if candidate.get('surface_type')=='rooftop': return 'rooftop'
    return 'parking' if candidate.get('surface_type') else 'ground'

def regional_boundary(city, radius_km=40):
    center=shape(city['geometry']).centroid
    crs=CRS.from_proj4(f'+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m')
    return transform(Transformer.from_crs(crs,4326,always_xy=True).transform,
                     Point(0,0).buffer(radius_km*1000,quad_segs=16))

def comparison(physical, ranked):
    rows=[]
    for key,label in APPROACHES.items():
        observations=[c for c in physical if approach(c)==key]
        eligible=[c for c in ranked['candidates'] if approach(c)==key]
        excluded=[c for c in ranked['excluded'] if approach(c)==key]
        reasons=Counter(r for c in excluded for r in c['exclusion_reasons'])
        rows.append({'id':key,'label':label,'screened':len(observations),'eligible':len(eligible),
                     'excluded':len(excluded),'best_score':eligible[0]['score'] if eligible else None,
                     'exclusions':[{'reason':r,'count':n} for r,n in reasons.most_common()],
                     'best_site_id':eligible[0]['id'] if eligible else None})
    return rows

def diverse_shortlist(candidates, limit):
    # Round-robin approaches, taking the strongest unused site in each. Not a global top-N.
    chosen=[];used=set();groups=list(dict.fromkeys(approach(c) for c in candidates))
    while len(chosen)<limit:
        previous=len(chosen)
        for key in groups:
            candidate=next((c for c in candidates if approach(c)==key and c['site_id'] not in used),None)
            if candidate and len(chosen)<limit:
                chosen.append(candidate);used.add(candidate['site_id'])
        if len(chosen)==previous: break
    return chosen

def search_region(query, parsed, city):
    from .city_search import fetch_city_surfaces
    boundary=regional_boundary(city)
    city={**city,'geometry':mapping(boundary),'bounds':list(boundary.bounds),'region':'us',
          'geoid':city['geoid']+'-radius40km-v1','scope':'regional','radius_km':40}
    plan=Plan(region='us',technology=parsed['technology'],mode='live',query=query,polygon=city['geometry'],
              target_mw=parsed['target_mw'] or 1,constraints={'zero_new_land':False})
    cache=Cache()
    stamp=hashlib.sha256(json.dumps([city['geometry'],plan.start_date,plan.end_date,fingerprint()],sort_keys=True).encode()).hexdigest()
    urban_key='regional-urban-v1:'+stamp;land_key='regional-land-v1:'+stamp
    urban_saved=cache.get(urban_key);land_saved=cache.get(land_key);land_hit=land_saved is not None
    urban_hit=bool(urban_saved and (datetime.now(timezone.utc)-datetime.fromisoformat(urban_saved['created_at'])).days<7)
    if urban_hit:
        urban=urban_saved['data']['physical'];urban_summary=urban_saved['data']['summary']
    else:
        raw=fetch_city_surfaces(city,'commercial|industrial|retail|warehouse|office|school|hospital|university|public|civic|government|college|hotel|sports_centre|supermarket|parking')
        request=UrbanRequest.model_construct(bounds=tuple(city['bounds']),region='us',surface='all',target_mw=1)
        try:
            urban,urban_summary=discover(request,supplied=raw,city_boundary=boundary,screening_limit=500)
        except ValueError as exc:
            if 'No complete mapped surfaces' not in str(exc): raise
            urban=[];urban_summary={'retrieved_at':raw[0]['retrieved_at'],'source_timestamp':raw[0]['retrieved_at'],'omitted':0,'skipped':0}
        cache.set(urban_key,{'physical':urban,'summary':urban_summary})
    if land_saved: land=land_saved['data']
    else:
        land=analyze(plan,boundary=boundary,technologies=['solar','wind'],grid_size=7)
        cache.set(land_key,land);land_saved=cache.get(land_key)
    index=STRtree([shape(c['geometry']) for c in urban])
    land=[dict(c) for c in land]
    for c in land:
        reasons=[]
        if len(index.query(shape(c['geometry']),predicate='intersects')):
            reasons.append('Overlaps a screened urban surface')
        if c['developed_pct']>5: reasons.append('Built-up cover above 5%')
        if c['technology']=='wind' and c['resource_value']<5.8: reasons.append('Mean wind below 5.8 m/s')
        c['screening_reasons']=reasons
        c['limitations']=[*c.get('limitations',[]),'Regional screening excludes land cells with >5% built-up cover or overlap with screened urban surfaces, and wind below 5.8 m/s. Parcel availability, setbacks and existing generation are not verified.']
    if parsed['surface']!='all' and plan.technology=='solar':
        urban=[{**c,'screening_reasons':([] if c.get('surface_type')==parsed['surface'] else ['Requested surface type'])} for c in urban]
        for c in land: c['screening_reasons'].append('Requested surface type')
    physical=[*urban,*land]
    all_ranked=rank_candidates(physical,plan)
    shortlist=diverse_shortlist(all_ranked['candidates'],parsed['limit'])
    if parsed['target_mw'] is None: plan.target_mw=max(.001,sum(c['capacity_mw'] for c in shortlist))
    ranked=rank_candidates(shortlist,plan)
    rows=comparison(physical,all_ranked)
    best_score=max((row['best_score'] for row in rows if row['best_score'] is not None),default=None)
    leaders=[row for row in rows if row['best_score']==best_score and best_score is not None]
    recommended=' and '.join(row['label'] for row in leaders) or None
    land_cells=len({c['site_id'] for c in land})
    note=(f"Compared {len(urban)} mapped urban surfaces and {land_cells} sampled 2 km land cells within 40 km of {city['name']}'s Census-boundary center. "
          f"Showing up to {parsed['limit']} distinct locations, balancing the shortlist across eligible approaches and taking the highest scores within each without double-counting locations. "
          "Scores weight energy resource 30%, environmental impact 30%, grid proximity 20%, buildability 15%, and land reuse 5%. "
          "This is sampled opportunity screening, not an exhaustive land inventory or a cost-optimal build plan. "
          "Land availability, interconnection capacity, roof suitability and permitting require verification. Hydro and geothermal are not screened.")
    city.update(limit=parsed['limit'],target_specified=parsed['target_mw'] is not None,screened=len(physical),eligible=len(all_ranked['candidates']),excluded=len(all_ranked['excluded']))
    summary={**urban_summary,'surface':'regional','cache_hit':urban_hit and land_hit,
             'note':note,'earth_engine_executions':0 if land_hit else 1,'comparison':rows,'recommended_approach':recommended,
             'urban_surfaces':len(urban),'land_cells':land_cells,'screened_options':len(physical),
             'land_timestamp':land_saved['created_at'],
             'dataset_ids':['osm-urban','power','worldcover','era5','copdem' if city['bounds'][3]>=60 else 'srtm','viirs','hifld','padus']}
    # Return the full exclusion audit, but keep map recommendations deliberately short.
    ranked['excluded']=all_ranked['excluded']
    return ranked,[*shortlist,*all_ranked['excluded']],plan,summary,city
