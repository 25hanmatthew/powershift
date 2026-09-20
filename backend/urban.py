"""Bounded urban solar screening from mapped surfaces, never synthetic fallback."""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import httpx
from pydantic import BaseModel, Field, model_validator
from pyproj import CRS, Transformer
from shapely.geometry import box, shape, mapping, Polygon, LineString
from shapely.ops import transform, unary_union, polygonize
from .cache import Cache
from .earth import solar_resource
from shapely.strtree import STRtree
from .models import REGIONS
from .scoring import components

class UrbanRequest(BaseModel):
    bounds: tuple[float, float, float, float]
    region: str = 'sacramento'
    surface: str = 'all'
    target_mw: float = Field(1, gt=0, le=10000)

    @model_validator(mode='after')
    def validate_area(self):
        w,s,e,n=self.bounds
        if not all(math.isfinite(v) for v in self.bounds) or not -180<=w<e<=180 or not -85<=s<n<=85:
            raise ValueError('Use a valid longitude/latitude search rectangle.')
        if self.region not in REGIONS or not box(*REGIONS[self.region]['bounds']).covers(box(*self.bounds)):
            raise ValueError('Keep the urban search within the selected supported region.')
        area=(e-w)*(n-s)*111.32**2*math.cos(math.radians((s+n)/2))
        if area>36: raise ValueError('Zoom in: urban searches cover at most 36 km² at a time.')
        if self.surface not in ('all','rooftop','parking_deck','parking_canopy'):
            raise ValueError('Choose rooftops, parking decks, canopies, or all surfaces.')
        return self

def osm_polygons(element):
    if element.get('type')=='way':
        points=[(p['lon'],p['lat']) for p in element.get('geometry',[]) if 'lon' in p and 'lat' in p]
        if len(points)<4 or points[0]!=points[-1]: return []
        geom=Polygon(points)
    elif element.get('type')=='relation':
        lines={'outer':[],'inner':[]}
        for member in element.get('members',[]):
            role=member.get('role') or 'outer'
            points=[(p['lon'],p['lat']) for p in member.get('geometry',[]) if 'lon' in p and 'lat' in p]
            if role in lines and len(points)>1: lines[role].append(LineString(points))
        if not lines['outer']: return []
        outer=list(polygonize(unary_union(lines['outer'])))
        inner=list(polygonize(unary_union(lines['inner']))) if lines['inner'] else []
        geom=unary_union(outer).difference(unary_union(inner))
    else: return []
    if geom.is_empty or not geom.is_valid: return []
    return [geom] if geom.geom_type=='Polygon' else list(geom.geoms) if geom.geom_type=='MultiPolygon' else []

def classify(tags):
    if tags.get('location')=='underground' or tags.get('parking')=='underground': return None
    if tags.get('building') in ('no','construction','ruins','demolished') or tags.get('construction'): return None
    if tags.get('parking')=='multi-storey' or tags.get('building')=='parking': return 'parking_deck'
    if tags.get('amenity')=='parking' and tags.get('parking','surface') in ('surface','rooftop'):
        return 'parking_deck' if tags.get('parking')=='rooftop' else 'parking_canopy'
    if tags.get('building') and tags.get('building') not in ('roof','carport','garage','garages'): return 'rooftop'
    return None

def height(tags,surface):
    raw=tags.get('height','')
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(m|ft)?\s*',raw)
    if match:
        value=float(match[1])*(.3048 if match[2]=='ft' else 1)
        if 2<=value<=350: return value,'Mapped height'
    try:
        levels=float(tags.get('building:levels',''))
        if 1<=levels<=100: return levels*3.2,'Mapped levels × assumed 3.2 m'
    except ValueError: pass
    return (0,'Ground parking') if surface=='parking_canopy' else (8,'Assumed 8 m; height unmapped')

def fetch_query(key,query):
    cache=Cache(); previous=cache.get(key)
    if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])).days<7:
        return previous['data'],True
    for endpoint in ('https://overpass.kumi.systems/api/interpreter','https://overpass-api.de/api/interpreter'):
        try:
            response=httpx.get(endpoint,params={'data':query},timeout=45,headers={'User-Agent':'PowerShift/1.0 urban-solar-screening'})
            response.raise_for_status()
            if len(response.content)>20_000_000: raise ValueError('Too many urban features. Zoom in and search a smaller area.')
            data=response.json()
            if data.get('remark') or not isinstance(data.get('elements'),list): continue
            data['retrieved_at']=datetime.now(timezone.utc).isoformat();cache.set(key,data)
            return data,False
        except (httpx.HTTPError,json.JSONDecodeError): continue
    raise ValueError('OpenStreetMap footprint service is unavailable or timed out. Zoom in or retry. No synthetic footprints were substituted.')

def fetch_osm(bounds):
    w,s,e,n=bounds
    query=f'[out:json][timeout:35];(wr["building"]({s},{w},{n},{e});wr["amenity"="parking"]({s},{w},{n},{e}););out meta geom;'
    return fetch_query('urban-osm-v1:'+hashlib.sha256(json.dumps(bounds).encode()).hexdigest(),query)

@lru_cache(maxsize=4)
def ground_cached(path,kind,mtime):
    # Index once; dissolve only features near the search instead of the entire national file.
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    if raw.get('crs') and '4326' not in json.dumps(raw['crs']) and 'CRS84' not in json.dumps(raw['crs']):
        raise ValueError(f'{kind} must use EPSG:4326.')
    geometries=[shape(f['geometry']) for f in raw.get('features',[]) if f.get('geometry')]
    allowed=('LineString','MultiLineString') if kind=='HIFLD' else ('Polygon','MultiPolygon')
    if not geometries or any(not g.is_valid or g.is_empty or g.geom_type not in allowed for g in geometries):
        raise ValueError(f'{kind} must contain valid, nonempty {allowed} geometries.')
    return STRtree(geometries)

def local_geometry(index,window):
    ids=index.query(window,predicate='intersects')
    return unary_union([index.geometries[i].intersection(window) for i in ids])

def discover(request:UrbanRequest, supplied=None, city_boundary=None, screening_limit=250):
    raw,cache_hit=supplied if supplied is not None else fetch_osm(request.bounds)
    bounds=box(*request.bounds); center=bounds.centroid
    project=Transformer.from_crs(4326,CRS.from_proj4(f'+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m'),always_xy=True).transform
    choices=[]; skipped=0
    # Relations first, then standalone ways: duplicate building outlines must not add capacity twice.
    for element in sorted(raw['elements'],key=lambda e:e.get('type')!='relation'):
        tags=element.get('tags',{}); surface=classify(tags)
        if not surface: continue
        for part,geom in enumerate(osm_polygons(element)):
            if not bounds.covers(geom) or (city_boundary is not None and not city_boundary.covers(geom)): skipped+=1;continue
            area=transform(project,geom).area
            if area<200: skipped+=1;continue
            choices.append({'element':element,'tags':tags,'surface':surface,'geom':geom,'area':area,'part':part})
    # Spatial lookup preserves relation-first deduplication without comparing every city building to every other.
    index=STRtree([c['geom'] for c in choices]);accepted=set();unique=[]
    for i,item in enumerate(choices):
        geom=item['geom']
        if any(int(j) in accepted and geom.intersection(choices[int(j)]['geom']).area>min(geom.area,choices[int(j)]['geom'].area)*.03 for j in index.query(geom)):
            skipped+=1;continue
        accepted.add(i);unique.append(item)
    choices=[c for c in unique if request.surface=='all' or c['surface']==request.surface]
    choices.sort(key=lambda c:-c['area']); omitted=max(0,len(choices)-screening_limit);choices=choices[:screening_limit]
    if not choices: raise ValueError('No complete mapped surfaces of at least 200 m² were found in this area. Try another neighborhood or surface type.')
    if request.region == 'us':
        from .national_ground import local_ground
        grid,protected=local_ground(request.bounds)
        projected_grid=transform(project,grid)
    else:
        paths={kind:Path(os.getenv(env,'')) for kind,env in [('HIFLD','HIFLD_GEOJSON'),('PAD-US','PADUS_GEOJSON')]}
        if any(not p.is_file() for p in paths.values()): raise ValueError('Urban screening needs the configured HIFLD and PAD-US files for infrastructure and protected-area checks.')
        grid=ground_cached(str(paths['HIFLD']),'HIFLD',paths['HIFLD'].stat().st_mtime_ns)
        protected=ground_cached(str(paths['PAD-US']),'PAD-US',paths['PAD-US'].stat().st_mtime_ns)
        nearby=local_geometry(grid,bounds.buffer(.5));projected_grid=transform(project,nearby)
        protected=local_geometry(protected,bounds)
    power=solar_resource(center.x,center.y); candidates=[]
    timestamp=raw.get('osm3s',{}).get('timestamp_osm_base',raw['retrieved_at'])
    for item in choices:
        element,tags,surface,geom,area=[item[k] for k in ('element','tags','surface','geom','area')]
        usable=.55 if surface=='parking_canopy' else .45
        capacity=area*usable*.0002/1.3
        point=geom.representative_point(); projected=transform(project,geom)
        distance=projected.distance(projected_grid)/1000 if not projected_grid.is_empty else 999
        overlap=transform(project,geom.intersection(protected)).area/area*100
        building_height,height_basis=height(tags,surface)
        source_url=f"https://www.openstreetmap.org/{element['type']}/{element['id']}"
        identifier=f"osm-{element['type']}-{element['id']}-{item['part']}"
        label={'rooftop':'Rooftop','parking_deck':'Parking deck','parking_canopy':'Parking canopy'}[surface]
        candidates.append({'id':identifier,'site_id':identifier,'name':tags.get('name') or f"{label} · {tags.get('addr:street') or element['id']}",
            'technology':'solar','surface_type':surface,'longitude':point.x,'latitude':point.y,'geometry':mapping(geom),
            'capacity_mw':round(capacity,5),'annual_gwh':round(capacity*1.3*power['value']*.8/24*8760/1000,5),
            'resource_value':power['value'],'resource_unit':'kWh/m²/day','grid_distance_km':round(distance,3),'protected_overlap_pct':overlap,
            'slope_deg':0,'roof_pitch_deg':None,'developed_pct':100,'natural_pct':0,'developed_surface_verified':True,
            'surface_area_m2':round(area,1),'usable_fraction':usable,'building_height_m':building_height,'height_basis':height_basis,
            'source_url':source_url,'roof_shape':tags.get('roof:shape','Unmapped'),'source_timestamp':element.get('timestamp',timestamp),
            'components':{**components((power['value']-3)*30,distance,0,0,100),'buildability':50},
            'confidence':'Mapped surface · suitability unverified','provenance':'computed','evidence_ids':['osm-urban','power','hifld','padus'],
            'vintage':timestamp,'land_cover':'Mapped existing developed surface','metric_sources':{'resource':{'dataset':'power','vintage':str(power['vintage'])},'slope':{'dataset':'not_measured','vintage':'Roof pitch unverified'}},
            'limitations':['OSM maps existing surfaces; ownership, access, roof condition and structural load capacity are not verified.',
                f'Capacity assumes {usable:.0%} usable mapped area, 200 W DC/m² of usable area and a 1.30 DC/AC ratio. Parking decks use top area only, not every floor.',
                'No LiDAR shading, obstructions, roof pitch, fire-access layout, available utility capacity or existing solar coverage is measured.',
                'Energy uses NASA POWER climatology with an assumed 0.8 performance ratio. Site yield needs an engineering model.',
                'HIFLD proximity is to transmission, not local distribution capacity. Building height may be assumed; roof geometry is illustrative.']})
    return candidates,{'surface':request.surface,'cache_hit':cache_hit,'retrieved_at':raw['retrieved_at'],'source_timestamp':timestamp,'skipped':skipped,'omitted':omitted,
        'note':f'{len(candidates)} mapped surfaces screened; {skipped} small, overlapping, or boundary-crossing footprints omitted. '+(f'Only the {screening_limit} largest of {len(candidates)+omitted} matching surfaces are shown.' if omitted else 'OpenStreetMap coverage may be incomplete.')}

OSM_DATASET={'id':'osm-urban','name':'OpenStreetMap urban surfaces','provider':'OpenStreetMap contributors','sensor':'Mapped building and parking geometry','space_derived':False,'access_method':'Overpass API','asset_id':None,'resolution_m':None,'vintage':'Timestamp retained per search','variables':['building footprints','parking footprints','height tags'],'metrics':['mapped area','surface classification'],'energy_types':['solar'],'source_url':'https://www.openstreetmap.org/copyright','description':'Existing building and parking polygons, queried in the visible neighborhood. © OpenStreetMap contributors, ODbL.','limitations':'Coverage and tags vary. Mapped surface does not establish structural suitability, available area, permission or interconnection capacity.'}
