"""Fresh 2024 ISD/PUDL outcomes for a prespecified weather-performance study."""
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from backend.ml import data, plant_data
from backend.ml.schema import QualityGateError
from scripts.station_normalized_wind import NORMALIZED, transform_station
from scripts.train_isd_pudl_wind import write, digest

ROOT = Path('data/private/ml-v9-insights')
RAW = ['isd_mean_wind_mps','isd_wind_std_mps','isd_wind_p10_mps','isd_wind_p90_mps','isd_mean_temp_c']


def freeze():
    ROOT.mkdir(exist_ok=True)
    plan = {'version':'isd-pudl-weather-insights-v9','train_years':[2019,2020,2021,2022],'fresh_test_year':2024,
        'task':'Retrospective weather adjustment for operating plants with historical generation. Not forecasting future weather or screening new sites.',
        'history_sha256':digest(Path('data/private/ml-v8-eastern/complete.parquet')),
        'old_evaluation_sha256':digest(Path('data/private/ml-v8-eastern/evaluation.json')),
        'hypotheses':{
            'H1':'ISD weather adjustment reduces 2024 absolute CF error relative to PUDL plant/calendar-month historical means.',
            'H2':'Months with normalized wind at least 0.15 below that plant/calendar-month historical mean have lower CF anomalies than near-normal months (+/-0.05), compared within plants.',
            'H3':'Months at least 3 C colder than the plant/calendar-month historical weather have lower wind-adjusted output than near-normal months (+/-1 C), compared within plants.'},
        'inference':'4000 geographic-block bootstrap draws; 98.333% intervals for each of three prespecified primary hypotheses. Minimum 30 paired plants and 5 blocks for contrasts. Associations, not causal effects.',
        'data_quality':'Original reported wind-only monthly PUDL labels; same station identities as historical joins; ISD codes 1/5, one vote per hour, >=70% wind/temp hourly coverage; stations <=100 km; >=90% weather join retention; >=100 plants and >=1200 fresh rows.',
        'baseline':'At least three historical observations for the same plant and calendar month. Train-row baselines exclude that row year. Cross-validation baselines exclude the complete validation year.',
        'comparison':'Train and choose models on historical years only. Fit a seasonal-history reference, wind-only weather model, all-weather model, and raw-station ablation. Also report previous-observed-year persistence.',
        'new_geography_claim':False,'live_scoring_enabled':False}
    path = ROOT/'study-plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise QualityGateError('Insight study changed after freezing.')
    if not path.exists():
        write(path,plan)
    return plan


def download_labels(ids):
    sources = {
      'generators': ("""SELECT plant_id_eia,generator_id,report_date AS report_month,capacity_mw,latitude,longitude,state,county,technology_description,generator_operating_date,generator_retirement_date FROM read_parquet(?) WHERE report_date>='2024-01-01' AND report_date<'2025-01-01' AND plant_id_eia IN (SELECT unnest(?))""",'out_eia__monthly_generators'),
      'generation': ("""SELECT plant_id_eia,report_date AS report_month,net_generation_mwh FROM read_parquet(?) WHERE report_date>='2024-01-01' AND report_date<'2025-01-01' AND energy_source_code='WND' AND prime_mover_code='WT' AND plant_id_eia IN (SELECT unnest(?))""",'core_eia923__monthly_generation_fuel'),
      'reporting': ("""SELECT plant_id_eia,report_date,reporting_frequency_code FROM read_parquet(?) WHERE report_date>='2024-01-01' AND report_date<'2025-01-01' AND plant_id_eia IN (SELECT unnest(?))""",'out_eia__yearly_plants')}
    con = data.connection()
    for name,(sql,table) in sources.items():
        path = ROOT/f'source-2024-{name}.parquet'
        if not path.exists():
            con.execute(sql,[data.PUDL+table+'.parquet',ids]).df().to_parquet(path,index=False)
        print(f'PUDL 2024 {name} ready',flush=True)
    con.close()
    return plant_data.clean_labels(**{name:pd.read_parquet(ROOT/f'source-2024-{name}.parquet') for name in sources})


def prepare():
    freeze()
    if (ROOT/'evaluation-started.json').exists():
        raise QualityGateError('Fresh insights test was already opened.')
    if (ROOT/'test-2024.parquet').exists():
        return
    history = pd.read_parquet('data/private/ml-v8-eastern/complete.parquet')
    history = history[history.report_month.dt.year.between(2019,2022)].copy()
    history['calendar_month'] = history.report_month.dt.month
    adequate = history.groupby(['plant_id_eia','calendar_month']).size().ge(3).groupby('plant_id_eia').sum()
    ids = sorted(int(p) for p in adequate[adequate.ge(9)].index)
    history = history[history.plant_id_eia.isin(ids)]
    history.to_parquet(ROOT/'history.parquet',index=False)
    labels = download_labels(ids)
    if labels.empty:
        raise QualityGateError('No eligible 2024 PUDL labels.')
    # Keep the same physical plant location used by the historical station match.
    old = history.drop_duplicates('plant_id_eia')[['plant_id_eia','latitude','longitude']].rename(columns={'latitude':'prior_lat','longitude':'prior_lon'})
    labels = labels.merge(old,on='plant_id_eia',validate='many_to_one')
    labels = labels[(labels.latitude-labels.prior_lat).abs().le(.02)&(labels.longitude-labels.prior_lon).abs().le(.02)]
    labels.to_parquet(ROOT/'labels-2024.parquet',index=False)
    matches = pd.concat([pd.read_parquet('data/private/ml-v7-station-normalized/station-matches.parquet'),pd.read_parquet('data/private/ml-v8-eastern/fresh/station-matches.parquet')]).drop_duplicates(['plant_id_eia','station_id'])
    matches = matches[matches.plant_id_eia.isin(labels.plant_id_eia)&matches.distance_km.le(100)]
    climate = pd.concat([pd.read_parquet('data/private/ml-v7-station-normalized/climatology.parquet'),pd.read_parquet('data/private/ml-v8-eastern/normalized/climatology.parquet')]).drop_duplicates(['station_id','month'])
    matches = matches[matches.station_id.isin(climate.station_id)]
    matches.to_parquet(ROOT/'station-matches.parquet',index=False)
    cache = ROOT/'isd-2024'; cache.mkdir(exist_ok=True)
    errors = []
    def fetch(station):
        path = cache/f'{station}-2024.parquet'
        if path.exists():
            return pd.read_parquet(path)
        try:
            url = f'https://www.ncei.noaa.gov/pub/data/noaa/2024/{station[:6]}-{station[6:]}-2024.gz'
            response = httpx.get(url,timeout=35); response.raise_for_status()
            raw = data.decode_isd_archive(response.content)
        except (httpx.HTTPError,ValueError,OSError):
            try:
                response = httpx.get(f'https://noaa-global-hourly-pds.s3.amazonaws.com/2024/{station}.csv',timeout=45); response.raise_for_status()
                raw = pd.read_csv(BytesIO(response.content),usecols=lambda c:c in ['DATE','WND','TMP'],dtype=str)
            except (httpx.HTTPError,ValueError,OSError) as error:
                errors.append({'station':station,'error':type(error).__name__})
                return pd.DataFrame()
        result = data.aggregate_station(raw)
        if result.empty:
            return result
        result['station_id'] = station
        result.to_parquet(path,index=False)
        return result
    observed = []
    stations = sorted(matches.station_id.unique())
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i,part in enumerate(pool.map(fetch,stations)):
            if len(part):
                observed.append(part)
            if (i+1)%30==0:
                print(f'2024 station records: {i+1}/{len(stations)}',flush=True)
    if not observed:
        raise QualityGateError('No 2024 ISD weather.')
    normalized = transform_station(pd.concat(observed,ignore_index=True),climate)
    joined = matches.merge(normalized,on='station_id',validate='many_to_many')
    rows = []
    for (plant,month),group in joined.groupby(['plant_id_eia','report_month']):
        w = 1/np.maximum(group.distance_km,1)**2
        row = {'plant_id_eia':plant,'report_month':month,'isd_station_count':len(group),'isd_nearest_station_km':float(group.distance_km.min())}
        row.update({c:float(np.average(group[c],weights=w)) for c in RAW+NORMALIZED+['isd_coverage_fraction']})
        rows.append(row)
    result = labels.merge(pd.DataFrame(rows),on=['plant_id_eia','report_month'],validate='one_to_one')
    report = {'eligible_historical_plants':len(ids),'eligible_2024_label_rows':len(labels),'joined_rows':len(result),'plants':int(result.plant_id_eia.nunique()),
        'retained_fraction':len(result)/len(labels),'stations':len(stations),'download_errors':errors,'source_version':data.PUDL_VERSION}
    write(ROOT/'data-report.json',report)
    if len(result)/len(labels)<.9 or result.plant_id_eia.nunique()<100 or len(result)<1200:
        raise QualityGateError('Fresh 2024 coverage below frozen quality requirements.')
    result.to_parquet(ROOT/'test-2024.parquet',index=False)
    print('Fresh 2024 data ready: '+json.dumps({k:v for k,v in report.items() if k!='download_errors'}),flush=True)


if __name__=='__main__':
    prepare()
