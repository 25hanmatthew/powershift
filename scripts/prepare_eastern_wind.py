"""Untouched eastern wind plants for the station-normalized model's final test."""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from backend.ml import data, plant_data, protocol
from backend.ml.atlas_data import site_atlas
from backend.ml.schema import PHYSICAL_FEATURES, QualityGateError
from scripts.station_normalized_wind import prepare as normalize
from scripts.train_isd_pudl_wind import add_features, write, digest

ROOT = Path('data/private/ml-v8-eastern')
STATES = ['NY', 'PA', 'MI', 'OH', 'IN', 'WV', 'ME', 'WI']


def reserve(frame, old):
    """Sparse eastern states need region-wide block selection, not within-state pairs."""
    path = ROOT/'split-protocol.json'
    if path.exists():
        return json.loads(path.read_text())
    plants = frame[['plant_id_eia','state','latitude','longitude']].drop_duplicates('plant_id_eia').copy()
    if set(plants.plant_id_eia)&old:
        raise QualityGateError('Previously examined plants entered the new reserve.')
    plants['block'] = protocol.geographic_blocks(plants)
    counts = plants.groupby('block').size()
    ordered = sorted(counts[counts.ge(3)].index,key=lambda b:hashlib.sha256(f'wind-eastern-v8:global:{b}'.encode()).hexdigest())
    chosen = []
    for block in ordered:
        chosen.append(block)
        spatial = plants[plants.block.isin(chosen)]
        if len(spatial)>=15 and len(chosen)>=3 and spatial.state.nunique()>=3:
            break
    spatial = plants[plants.block.isin(chosen)]
    temporal = plants[~plants.block.isin(chosen)]
    if len(spatial)<15 or len(chosen)<3 or len(temporal)<15:
        raise QualityGateError('Need 15 fresh plants per test and three entire spatial blocks.')
    design = {'version':'wind-eastern-v8','created_at':datetime.now(timezone.utc).isoformat(),
        'previously_examined_plants':sorted(int(p) for p in old),'spatial_blocks':sorted(chosen),
        'spatial_test_plants':sorted(int(p) for p in spatial.plant_id_eia),'temporal_test_plants':sorted(int(p) for p in temporal.plant_id_eia),
        'training_years':[2019,2020,2021,2022],'temporal_test_year':2023,'one_shot':True,
        'selection_rule':'Outcome-blind SHA256 ordering of region-wide blocks with at least three fresh plants; retain blocks until at least 15 plants, three blocks, and three states. Remove entire blocks from fitting.',
        'pre_evaluation_design_note':'The default within-state selector could reserve only four plants because most eastern states have a single populated block. No protocol was written and no predictions scored. This regional selector retains stricter minimum spatial coverage.'}
    write(path,design)
    return design


def prepare():
    load_dotenv('.env')
    ROOT.mkdir(exist_ok=True)
    if (ROOT/'evaluation-started.json').exists():
        raise QualityGateError('Eastern tests already opened.')
    fresh = ROOT/'fresh'; fresh.mkdir(exist_ok=True)
    development = pd.read_parquet('data/private/ml-v7-station-normalized/training.parquet')
    old = set(pd.read_parquet('data/private/ml-v4/labels.parquet').plant_id_eia)
    old |= set(pd.read_parquet('data/private/ml-v6-fusion/complete.parquet').plant_id_eia)
    sql_states = ','.join(f"'{state}'" for state in STATES)
    generators = fresh/'source-generators.parquet'
    if not generators.exists():
        con = data.connection()
        con.execute(f"""SELECT plant_id_eia,generator_id,report_date AS report_month,capacity_mw,
          latitude,longitude,state,county,technology_description,generator_operating_date,generator_retirement_date
          FROM read_parquet(?) WHERE state IN ({sql_states}) AND report_date>='2019-01-01' AND report_date<'2024-01-01'""",
          [data.PUDL+'out_eia__monthly_generators.parquet']).df().to_parquet(generators,index=False)
        con.close()
    path = fresh/'labels.parquet'
    if path.exists():
        frame = pd.read_parquet(path)
    else:
        cache = Path('data/private/ml-v3-plant')
        frame = plant_data.clean_labels(pd.read_parquet(generators), pd.read_parquet(cache/'source-generation.parquet'), pd.read_parquet(cache/'source-reporting.parquet'))
        frame = frame[~frame.plant_id_eia.isin(old)]
        counts = frame[frame.report_month.dt.year.lt(2023)].groupby('plant_id_eia').size()
        future = set(frame[frame.report_month.dt.year.eq(2023)].plant_id_eia)
        plants = frame.drop_duplicates('plant_id_eia')
        plants = plants[plants.plant_id_eia.map(counts).ge(24)&plants.plant_id_eia.isin(future)]
        chosen = []
        for _, group in plants.groupby('state'):
            chosen += sorted(group.plant_id_eia, key=lambda p: hashlib.sha256(f'eastern-v8:{p}'.encode()).hexdigest())[:20]
        frame = frame[frame.plant_id_eia.isin(chosen)].copy()
        frame.to_parquet(path,index=False)
    design = reserve(frame,old)
    write(ROOT/'source-config.json', {'states': STATES, 'source_labels_sha256': digest(path), 'pudl_version': data.PUDL_VERSION,
        'old_v6_evaluation_sha256': digest(Path('data/private/ml-v6-fusion/evaluation.json')),
        'development_sha256': digest(Path('data/private/ml-v7-station-normalized/training.parquet'))})
    print('Reserved eastern plants: '+str(frame.groupby('state').plant_id_eia.nunique().to_dict()),flush=True)
    physical = fresh/'physical.parquet'
    if not physical.exists():
        rare = fresh/'rare-monthly.parquet'
        if not rare.exists():
            con = data.connection()
            table = con.execute(f"""SELECT state,place_name,county_id_fips,date_trunc('month',datetime_utc) AS report_month,
              avg(capacity_factor_onshore_wind) AS resource_expected_capacity_factor,count(capacity_factor_onshore_wind) AS baseline_hours
              FROM read_parquet(?) WHERE state IN ({sql_states}) AND report_year BETWEEN 2019 AND 2023
              AND county_id_fips IS NOT NULL GROUP BY ALL""",[data.PUDL+'out_vcerare__hourly_available_capacity_factor.parquet']).df()
            con.close()
            table['county_key'] = table.place_name.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county')
            table.to_parquet(rare,index=False)
        frame,_ = data.rare(fresh,frame)
        if not (fresh/'isd-history.csv').exists():
            shutil.copy2('data/private/ml-v3-plant/isd-history.csv',fresh/'isd-history.csv')
        (fresh/'isd').mkdir(exist_ok=True)
        for location in ['data/private/ml-v5-isd/isd','data/private/ml-v6-fusion/fresh/isd']:
            for file in Path(location).glob('*.parquet'):
                if not (fresh/'isd'/file.name).exists():
                    shutil.copy2(file,fresh/'isd'/file.name)
        print('Preparing eastern station and hub-height weather',flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            station = pool.submit(data.weather,fresh,frame,years=[2019,2020,2021,2022,2023],max_workers=6,prefer_archive=True)
            wind = plant_data.site_wind(fresh,frame)
            observed = station.result()
        frame = wind.merge(observed[['plant_id_eia','report_month']+[c for c in observed if c.startswith('isd_')]],on=['plant_id_eia','report_month'],validate='one_to_one')
        frame,_ = data.terrain(fresh,frame,required_features=PHYSICAL_FEATURES)
        frame = add_features(site_atlas(fresh,frame))
        frame.to_parquet(physical,index=False)
    normalized = ROOT/'normalized'; normalized.mkdir(exist_ok=True)
    (normalized/'isd-reference').mkdir(exist_ok=True)
    for file in Path('data/private/ml-v7-station-normalized/isd-reference').glob('*.parquet'):
        if not (normalized/'isd-reference'/file.name).exists():
            shutil.copy2(file,normalized/'isd-reference'/file.name)
    frame = normalize(physical,[fresh],normalized,(2019,2020,2021,2022,2023))
    combined = pd.concat([development,frame],ignore_index=True)
    partitions = protocol.split(combined,design)
    combined.to_parquet(ROOT/'complete.parquet',index=False)
    print('Eastern splits: '+str([(len(x),x.plant_id_eia.nunique()) for x in partitions]),flush=True)


if __name__ == '__main__':
    prepare()
