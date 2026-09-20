"""Index the national HIFLD download; keep the indexed PAD-US geodatabase intact."""
import argparse
from pathlib import Path
import pyogrio
from pyogrio.raw import read, write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/private/source/transmission-lines-1-geojson.geojson'))
    parser.add_argument('--output', type=Path, default=Path('data/private/national/transmission.gpkg'))
    args=parser.parse_args()
    if args.output.exists():
        print('National transmission index already exists; no files changed.')
        return
    args.output.parent.mkdir(parents=True,exist_ok=True)
    meta,_,geometries,values=read(args.source,columns=['ID','TYPE','STATUS','OWNER','VOLTAGE','VOLT_CLASS','SOURCEDATE'],force_2d=True)
    if not len(geometries) or not meta['crs']:
        raise ValueError('Transmission source is empty or missing its CRS.')
    temp=args.output.with_name('transmission-preparing.gpkg')
    if temp.exists():
        raise ValueError('An incomplete or running index build exists; inspect it before retrying.')
    write(temp, geometries, values, meta['fields'], driver='GPKG', layer='transmission',
          crs=meta['crs'], geometry_type='MultiLineString', promote_to_multi=True,
          layer_options={'SPATIAL_INDEX':'YES'})
    if pyogrio.read_info(temp)['features'] != len(geometries):
        raise ValueError('National transmission index count failed verification.')
    temp.replace(args.output)
    print(f'Indexed {len(geometries):,} national transmission features at {args.output}.')


if __name__ == '__main__':
    main()
