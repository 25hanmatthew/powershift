"""Read only the local window from national HIFLD and PAD-US vector sources."""
import hashlib
import json
import os
from pathlib import Path
from pyproj import Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union
from .cache import Cache

PADUS_LAYERS = ('PADUS4_1Fee', 'PADUS4_1Designation', 'PADUS4_1Easement', 'PADUS4_1Marine')


def sources():
    return (Path(os.getenv('HIFLD_NATIONAL', 'data/private/national/transmission.gpkg')),
            Path(os.getenv('PADUS_NATIONAL', 'data/private/source/PADUS4_1Geodatabase.gdb')))


def fingerprint():
    files=[]
    for path in sources():
        if not path.exists():
            raise ValueError('National ground data is not configured. Prepare the national HIFLD index and PAD-US geodatabase; regional extracts cannot screen this location.')
        children=sorted(path.iterdir()) if path.is_dir() else [path]
        files.append([(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in children if p.is_file()])
    return files


def polygon_parts(geometry, allowed):
    if geometry.is_empty:
        return []
    if geometry.geom_type in allowed:
        return [geometry]
    return [part for child in getattr(geometry, 'geoms', []) for part in polygon_parts(child, allowed)]


def read_window(path, layers, window, allowed):
    import pyogrio
    from pyogrio.raw import read
    output=[]
    for layer in layers:
        info=pyogrio.read_info(path, layer=layer)
        if not info['crs']:
            raise ValueError('National source has no coordinate reference system.')
        forward=Transformer.from_crs(4326, info['crs'], always_xy=True).transform
        reverse=Transformer.from_crs(info['crs'], 4326, always_xy=True).transform
        mask=transform(forward, window.segmentize(.05))
        _, _, geometries, _=read(path, layer=layer, columns=[], mask=mask, force_2d=True)
        for raw in geometries:
            if raw is None:
                raise ValueError('National source returned a missing geometry.')
            geom=make_valid(transform(reverse, make_valid(from_wkb(raw))))
            output.extend(polygon_parts(geom.intersection(window), allowed))
    return unary_union(output)


def local_ground(bounds):
    signature=fingerprint()
    key='national-ground-v1:'+hashlib.sha256(json.dumps([list(bounds),signature],sort_keys=True).encode()).hexdigest()
    cache=Cache();saved=cache.get(key)
    if saved:
        return shape(saved['data']['grid']), shape(saved['data']['protected'])
    transmission,padus=sources();w,s,e,n=bounds
    # Three degrees covers the app's 100 km screening distance, including Alaska.
    grid_window=box(max(-180,w-3),max(-85,s-3),min(180,e+3),min(85,n+3))
    grid=read_window(transmission, [None], grid_window, ('LineString','MultiLineString'))
    protected=read_window(padus, PADUS_LAYERS, box(*bounds), ('Polygon','MultiPolygon'))
    cache.set(key, {'grid':mapping(grid), 'protected':mapping(protected)})
    return grid,protected
