"""Explicitly synthetic fixtures for an offline interaction demo, never real evidence."""
from .scoring import components

SITES = [
    ('Lahontan Basin', -119.08,39.42,'solar',72,6.0,2.4,2.1,11,24),
    ('Carson Plains',-119.63,39.12,'solar',64,5.8,1.6,3.4,8,38),
    ('Solano Corridor',-121.82,38.14,'wind',90,9.2,1.1,2.3,8,20),
    ('Honey Lake',-120.19,40.27,'solar',58,5.6,4.1,2.8,19,15),
    ('Fernley Flats',-119.18,39.63,'solar',80,6.2,6.4,1.3,29,8),
    ('Altamont Pass',-121.63,37.74,'wind',78,8.6,2.8,9.6,44,9),
    ('Sacramento East',-121.25,38.64,'solar',46,5.5,1.2,1.4,6,77),
    ('Dixie Valley',-117.92,39.87,'wind',108,8.9,14.2,7.2,57,4),
    ('Yolo Terrace',-121.75,38.74,'solar',42,5.4,2.6,1.7,15,53),
    ('Eagle Ridge',-120.53,40.60,'wind',96,8.4,9.2,12.3,48,2),
    ('Fallon Bench',-118.74,39.47,'solar',68,6.1,3.7,3.1,17,21),
    ('Washoe Uplands',-119.85,39.78,'wind',84,7.8,5.4,8.2,34,5),
    ('Truckee Habitat',-120.12,39.34,'solar',60,5.5,4.5,6.3,72,0),
    ('Stillwater Habitat',-118.55,39.55,'solar',70,6.0,8.7,2.6,80,0),
    ('Remote Basin',-117.58,40.32,'wind',120,9.2,38.2,7.8,65,0),
    ('Sierra Escarpment',-120.05,38.85,'wind',100,8.5,6.4,26.5,69,0),
]

def candidates(region):
    output=[]
    for i, row in enumerate(SITES):
        name,lon,lat,tech,cap,resource,grid,slope,natural,reuse=row
        if region=='washington':
            lon=-120.1+(i%4)*.72; lat=46.05+(i//4)*.59
            name=['Columbia Bench','Palouse Ridge','Yakima Terrace','Walla Walla'][i%4]+f' {i//4+1}'
        if region=='sacramento':
            lon=-121.86+(i%4)*.25; lat=38.13+(i//4)*.23
            name=['Delta Terrace','Sacramento East','Yolo Surface','Elk Grove'][i%4]+f' {i//4+1}'
        d=.018
        output.append({'id':f'{region}-{tech}-{i+1:02}', 'site_id': f'{region}-{i+1:02}', 'name':name,
            'technology':tech,'longitude':lon,'latitude':lat,'capacity_mw':cap,
            'geometry':{'type':'Polygon','coordinates':[[[lon-d,lat-d],[lon+d,lat-d],[lon+d,lat+d],[lon-d,lat+d],[lon-d,lat-d]]]},
            'resource_value':resource,'resource_unit':'kWh/m²/day' if tech=='solar' else 'm/s at 100 m',
            'grid_distance_km':grid,'slope_deg':slope,'protected_overlap_pct':12 if i in [12,13] else 0,
            'developed_pct':reuse,'natural_pct':natural,'developed_surface_verified':False,
            'annual_gwh':round(cap*8760*(resource*.8/24 if tech=='solar' else min(.48,resource*.045))/1000,1),
            'components':components((resource-3)*30 if tech=='solar' else (resource-4)*20,grid,slope,natural,reuse),
            'confidence':'Demonstration','provenance':'synthetic',
            'evidence_ids':['worldcover','srtm','viirs','power' if tech=='solar' else 'era5','hifld','padus'],
            'limitations':['All site measurements are synthetic fixtures for interaction testing. They were not computed from the listed datasets.','Capacity and generation are screening estimates, not engineering designs.'],
            'vintage':'Synthetic fixture · 2026-09', 'land_cover':'Mixed developed / open land'})
    return output
