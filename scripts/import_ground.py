"""Prepare local WGS84 screening layers from extracted national source archives.

Install scripts/requirements-data.txt, then run python -m scripts.import_ground.
Original archives and extracted source files are never modified.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pyogrio
from pyogrio.raw import read
from pyproj import Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union

from backend.models import REGIONS


def geometry_parts(geometry, allowed):
    if geometry.geom_type in allowed:
        return [geometry] if not geometry.is_empty else []
    if hasattr(geometry, 'geoms'):
        return [part for child in geometry.geoms for part in geometry_parts(child, allowed)]
    return []


def convert(source, layers, target, clip, columns, allowed):
    features = []
    counts = {}
    repaired = 0
    attributes = Counter()
    for layer in layers:
        info = pyogrio.read_info(source, layer=layer)
        if not info['crs']:
            raise ValueError(f'Missing coordinate reference system: {source} {layer}')
        to_source = Transformer.from_crs('EPSG:4326', info['crs'], always_xy=True)
        to_wgs84 = Transformer.from_crs(info['crs'], 'EPSG:4326', always_xy=True)
        # Densify edges before projection so the projected filter covers curved edges.
        mask = transform(to_source.transform, clip.segmentize(.05))
        fields = [column for column in columns if column in info['fields']]
        meta, fids, geometries, values = read(source, layer=layer, columns=fields,
                                             mask=mask, force_2d=True, return_fids=True, datetime_as_string=True)
        before = len(features)
        for index, wkb in enumerate(geometries):
            if wkb is None:
                raise ValueError(f'Null geometry in {layer}, feature {fids[index]}')
            geometry = from_wkb(wkb)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
                repaired += 1
            geometry = transform(to_wgs84.transform, geometry)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
                repaired += 1
            geometry = geometry.intersection(clip)
            parts = geometry_parts(geometry, allowed)
            if not parts:
                continue  # Touching the clipping boundary has no line length/area.
            geometry = unary_union(parts)
            if not geometry.is_valid or geometry.is_empty:
                raise ValueError(f'Invalid output geometry in {layer}, feature {fids[index]}')
            properties = {str(field): values[j][index].item() if hasattr(values[j][index], 'item')
                          else values[j][index] for j, field in enumerate(meta['fields'])}
            properties = {key:None if isinstance(value,float) and not math.isfinite(value) else value
                          for key,value in properties.items()}
            properties.update(source_layer=layer or 'HIFLD transmission lines', source_fid=int(fids[index]))
            attributes[str(properties.get('STATUS', properties.get('GAP_Sts', 'unknown')))] += 1
            features.append({'type':'Feature','geometry':mapping(geometry),'properties':properties})
        counts[layer or 'transmission'] = len(features)-before
        print(json.dumps({'layer':layer,'selected':len(geometries),'written':len(features)-before}), flush=True)
    if not features:
        raise ValueError(f'No features produced for {target}')
    data = {'type':'FeatureCollection','name':target.stem,'features':features}
    temp = target.with_suffix('.geojson.tmp')
    temp.write_text(json.dumps(data, separators=(',',':'), allow_nan=False), encoding='utf-8')
    temp.replace(target)
    return {'file':str(target),'feature_count':len(features),'layers':counts,
            'geometry_repairs':repaired,'attribute_counts':dict(attributes),'bytes':target.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=Path('data/private/source'))
    parser.add_argument('--output-dir',type=Path,default=Path('data/private'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    area = unary_union([box(*region['bounds']) for region in REGIONS.values()])
    grid_area = unary_union([box(w-3,s-3,e+3,n+3) for w,s,e,n in
                            [region['bounds'] for region in REGIONS.values()]])
    grid = convert(args.source_dir/'transmission-lines-1-geojson.geojson', [None],
                   args.output_dir/'transmission.geojson',grid_area,
                   ['ID','TYPE','STATUS','OWNER','VOLTAGE','VOLT_CLASS','SOURCEDATE'],
                   ('LineString','MultiLineString'))
    protected = convert(args.source_dir/'PADUS4_1Geodatabase.gdb',
                        ['PADUS4_1Fee','PADUS4_1Designation','PADUS4_1Easement','PADUS4_1Marine'],
                        args.output_dir/'protected.geojson',area,
                        ['Unit_Nm','State_Nm','GAP_Sts','Category','Own_Name','Mang_Name','Src_Date'],
                        ('Polygon','MultiPolygon'))
    manifest = {'created_at':datetime.now(timezone.utc).isoformat(),'crs':'EPSG:4326',
                'regions':REGIONS,'transmission_buffer_degrees':3,'simplification':False,
                'transmission':grid,'protected':protected,
                'padus_version':'4.1',
                'hifld_source_url':'https://www.datalumos.org/datalumos/project/240591/version/V1/view',
                'padus_source_url':'https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download',
                'protection_policy':'Conservatively exclude all supplied Fee, Designation, Easement and Marine polygons, regardless of GAP status. Proclamation/planning outlines are omitted because they do not depict internal ownership. This is screening, not a legal determination.'}
    (args.output_dir/'import-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest),flush=True)


if __name__ == '__main__':
    main()
