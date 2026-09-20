"""Real Earth Engine zonal statistics and terrestrial geometry joins.

This module never returns fixtures. Missing data stops live analysis.
"""
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
from pyproj import CRS, Transformer
from shapely.geometry import box, shape, mapping
from shapely.ops import transform, unary_union
from .cache import Cache
from .models import REGIONS
from .scoring import components, haversine

def load_ground(path, expected):
    file=Path(path)
    if not file.is_file(): raise ValueError(f'{expected} regional GeoJSON is not configured.')
    raw=json.loads(file.read_text(encoding='utf-8'))
    if raw.get('crs') and '4326' not in json.dumps(raw['crs']) and 'CRS84' not in json.dumps(raw['crs']):
        raise ValueError(f'{expected} must use EPSG:4326.')
    geometries=[shape(f['geometry']) for f in raw.get('features',[]) if f.get('geometry')]
    if not geometries or any(not g.is_valid for g in geometries):
        raise ValueError(f'{expected} must contain valid, nonempty geometries.')
    allowed=('LineString','MultiLineString') if expected=='HIFLD' else ('Polygon','MultiPolygon')
    if any(g.geom_type not in allowed for g in geometries): raise ValueError(f'{expected} contains incompatible geometry types.')
    return unary_union(geometries)

def solar_resource(lon,lat):
    # Nearby cells reuse a climatology grid request.
    lon,lat=round(lon,1),round(lat,1)
    cache=Cache(); key=f'power-climatology-v2:{lon}:{lat}'
    previous=cache.get(key)
    if previous: return previous['data']
    with httpx.Client(timeout=60) as client:
        res=client.get('https://power.larc.nasa.gov/api/temporal/climatology/point',params={
            'parameters':'ALLSKY_SFC_SW_DWN','community':'RE','longitude':lon,'latitude':lat,'format':'JSON'})
        res.raise_for_status(); data=res.json()
    values=data['properties']['parameter']['ALLSKY_SFC_SW_DWN']
    val=values.get('ANN')
    if val is None or not 0 < val < 15: raise ValueError('NASA POWER returned no valid solar climatology.')
    result={'value':val,'vintage':data.get('header',{}).get('range','Period not supplied by NASA POWER'),
        'api_version':data.get('header',{}).get('api',{}).get('version'),
        'sources':data.get('header',{}).get('sources',[]),'source_url':str(res.url)}
    cache.set(key,result)
    return result

def analyze(plan, boundary=None, technologies=None):
    import ee
    project=os.getenv('EARTH_ENGINE_PROJECT')
    if not project: raise ValueError('Set EARTH_ENGINE_PROJECT and authenticate Earth Engine.')
    credentials=os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if credentials:
        account=json.loads(Path(credentials).read_text())['client_email']
        ee.Initialize(ee.ServiceAccountCredentials(account,credentials),project=project)
    else: ee.Initialize(project=project)
    ee.data.setDeadline(180000)
    ground_path=os.getenv('HIFLD_GEOJSON','data/private/transmission.geojson')
    protected_path=os.getenv('PADUS_GEOJSON','data/private/protected.geojson')
    w,s,e,n=REGIONS[plan.region]['bounds']
    region=(boundary if boundary is not None else shape(plan.polygon) if plan.polygon else box(w,s,e,n)).intersection(box(w,s,e,n))
    if region.is_empty: raise ValueError('Boundary does not intersect the selected supported region.')
    w,s,e,n=region.bounds
    if boundary is not None:
        from .urban import ground_cached, local_geometry
        ground=local_geometry(ground_cached(ground_path,'HIFLD',Path(ground_path).stat().st_mtime_ns),box(w-3,s-3,e+3,n+3))
        protected=local_geometry(ground_cached(protected_path,'PAD-US',Path(protected_path).stat().st_mtime_ns),box(w,s,e,n))
    else:
        ground=load_ground(ground_path,'HIFLD');protected=load_ground(protected_path,'PAD-US')
    cells=[]
    for y in range(5):
        for x in range(5):
            lon=w+(x+.5)*(e-w)/5; lat=s+(y+.5)*(n-s)/5
            # Fixed 2 km x 2 km screening footprint (4 km²), approximate geographic cell.
            dx=1/(111.32*math.cos(math.radians(lat))); dy=1/111.32
            footprint=box(lon-dx,lat-dy,lon+dx,lat+dy)
            if not region.covers(footprint): continue
            if plan.radius_km and any(haversine(plan.center,p)>plan.radius_km for p in footprint.exterior.coords): continue
            cells.append({'site_id':f'cell-{y}-{x}','lon':lon,'lat':lat,'geometry':mapping(footprint)})
    if not cells: raise ValueError('No complete 2 km screening cells fit this boundary. Try a larger area.')
    features=ee.FeatureCollection([ee.Feature(ee.Geometry(c['geometry']),{'site_id':c['site_id']}) for c in cells])
    cover=ee.ImageCollection('ESA/WorldCover/v200').first().select('Map')
    # 100 m analysis scale is recorded explicitly; this is screening, not full-resolution engineering.
    developed=cover.eq(50).multiply(100).rename('developed_pct')
    natural=cover.remap([10,20,30,90,95,100],[1,1,1,1,1,1],0).multiply(100).rename('natural_pct')
    incompatible=cover.remap([70,80,90,95],[1,1,1,1],0).multiply(100).rename('incompatible_pct')
    elevation=ee.Image('USGS/SRTMGL1_003').select('elevation')
    slope=ee.Terrain.slope(elevation).rename('slope_deg')
    viirs=(ee.ImageCollection('NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG').filterDate(plan.start_date,plan.end_date)
        .map(lambda image: image.select('avg_rad').updateMask(image.select('cf_cvg').gt(0))).mean().rename('night_radiance'))
    wind=(ee.ImageCollection('ECMWF/ERA5/HOURLY').filterDate(plan.start_date,plan.end_date)
        .select(['u_component_of_wind_100m','v_component_of_wind_100m'])
        .map(lambda image: image.select(0).pow(2).add(image.select(1).pow(2)).sqrt().rename('wind_speed')).mean())
    stack=developed.addBands([natural,incompatible,slope,viirs,wind])
    stats=stack.reduceRegions(collection=features,reducer=ee.Reducer.mean(),scale=100,tileScale=4).getInfo()
    properties={f['properties']['site_id']:f['properties'] for f in stats['features']}
    # Sequential or low concurrency avoids hammering NASA POWER.
    technologies=technologies or ['solar','wind']
    if 'solar' in technologies:
        with ThreadPoolExecutor(max_workers=2) as executor:
            solar=list(executor.map(lambda c: solar_resource(c['lon'],c['lat']),cells))
    else:
        solar=[None]*len(cells)
    results=[]
    for cell,power in zip(cells,solar):
        props=properties[cell['site_id']]
        required=['slope_deg','developed_pct','natural_pct','incompatible_pct','wind_speed','night_radiance']
        if any(props.get(key) is None or not math.isfinite(props[key]) for key in required): continue
        lon,lat=cell['lon'],cell['lat']; footprint=shape(cell['geometry'])
        proj=Transformer.from_crs('EPSG:4326',CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m'),always_xy=True).transform
        projected=transform(proj,footprint)
        # Reduce the geometry before projection; include enough range for the maximum allowed distance.
        nearby=ground.intersection(box(lon-3,lat-3,lon+3,lat+3))
        if nearby.is_empty: grid=999.0
        else: grid=transform(proj,footprint.centroid).distance(transform(proj,nearby))/1000
        intersection=protected.intersection(footprint)
        overlap=0 if intersection.is_empty else transform(proj,intersection).area/projected.area*100
        for technology in technologies:
            resource=power['value'] if technology=='solar' else props['wind_speed']
            # Explicit screening assumptions: 35 MW/km² usable solar land; 5 MW/km² wind spacing.
            area_km2=projected.area/1e6; usable=max(0,1-props['incompatible_pct']/100)
            capacity=round(area_km2*usable*(35 if technology=='solar' else 5),2)
            factor=min(.32,resource*.8/24) if technology=='solar' else max(0,min(.5,(resource-3)*.075))
            results.append({'id':cell['site_id']+'-'+technology,'site_id':cell['site_id'],'name':f"{'Solar' if technology=='solar' else 'Wind'} · {lat:.2f}°N {abs(lon):.2f}°W",
                'technology':technology,'longitude':lon,'latitude':lat,'geometry':cell['geometry'],'capacity_mw':capacity,
                'resource_value':round(resource,3),'resource_unit':'kWh/m²/day' if technology=='solar' else 'm/s at 100 m',
                'grid_distance_km':round(grid,3),'slope_deg':round(props['slope_deg'],2),
                'protected_overlap_pct':overlap,'developed_pct':round(props['developed_pct'],2),'natural_pct':round(props['natural_pct'],2),
                'night_radiance':round(props['night_radiance'],3),'developed_surface_verified':False,
                'excluded_land_cover':props['incompatible_pct']>1 or (technology=='wind' and props['developed_pct']>20),
                'annual_gwh':round(capacity*8760*factor/1000,1),
                'components':components((resource-3)*30 if technology=='solar' else (resource-4)*20,grid,props['slope_deg'],props['natural_pct'],props['developed_pct']),
                'confidence':'Preliminary screening','provenance':'computed','evidence_ids':['worldcover','srtm','viirs','power' if technology=='solar' else 'era5','hifld','padus'],
                'vintage':f'{plan.start_date} – {plan.end_date}', 'land_cover':'WorldCover zonal fractions',
                'metric_sources':{'resource':{'dataset':'power' if technology=='solar' else 'era5','vintage':power['vintage'] if technology=='solar' else f'{plan.start_date}/{plan.end_date}'},
                    'slope':{'dataset':'srtm','vintage':'2000'},'land_cover':{'dataset':'worldcover','vintage':'2021'},
                    'grid_distance':{'dataset':'hifld','vintage':os.getenv('HIFLD_VINTAGE','2022-10-24')},
                    'protected_overlap':{'dataset':'padus','vintage':os.getenv('PADUS_VINTAGE','4.1')}},
                'limitations':['2 km screening footprint; zonal statistics sampled at 100 m.',
                    'Capacity assumes 35 MW/km² solar or 5 MW/km² wind; usable fraction excludes incompatible cover.',
                    'Solar yield assumes 0.8 performance ratio; wind uses an illustrative speed-to-capacity-factor proxy.',
                    'Grid proximity does not establish interconnection capacity. No parcel availability or permitting verification.']})
    if not results: raise ValueError('The selected Earth Engine products returned no complete measurements for this boundary.')
    return results
