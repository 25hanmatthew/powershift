"""Reported wind-only plant labels and site-matched ERA5 physics for revision 3."""
import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data
from .schema import PLANT_FEATURES as FEATURES, PLANT_SCHEMA_VERSION as SCHEMA_VERSION, WIND_FEATURES, QualityGateError

STATES = ['CA','NV','OR','WA','ID','MT','WY','UT','CO','AZ','NM','TX','OK','KS','NE','SD','ND']
YEARS = [2019,2020,2021,2022,2023]


def clean_labels(generators, generation, reporting):
    """One observation per plant-month, with an exact reported-generation denominator."""
    keys=['plant_id_eia','report_month']
    if generators.duplicated(keys+['generator_id']).any() or generation.duplicated(keys).any():
        raise QualityGateError('Duplicate source keys in reported plant data.')
    end=generators.report_month+pd.offsets.MonthEnd(0)
    intersects=(generators.generator_operating_date<=end) & (generators.generator_retirement_date.isna() | (generators.generator_retirement_date>=generators.report_month))
    live=generators[intersects].copy()
    end=live.report_month+pd.offsets.MonthEnd(0)
    live['valid_unit']=(live.technology_description.eq('Onshore Wind Turbine') & live.capacity_mw.gt(0) &
                        live.generator_operating_date.le(live.report_month) &
                        (live.generator_retirement_date.isna() | live.generator_retirement_date.gt(end)))
    plants=live.groupby(keys,as_index=False).agg(capacity_mw=('capacity_mw','sum'),
        all_valid=('valid_unit','all'), latitude=('latitude','first'),longitude=('longitude','first'),
        state=('state','first'),county=('county','first'),generators_count=('generator_id','size'),
        latitude_range=('latitude',lambda x:x.max()-x.min()),longitude_range=('longitude',lambda x:x.max()-x.min()))
    plants=plants[plants.all_valid & plants.latitude_range.le(.01) & plants.longitude_range.le(.01)].copy()
    plants['report_year']=plants.report_month.dt.year
    reporting=reporting.copy()
    reporting['report_year']=reporting.report_date.dt.year
    if reporting.duplicated(['plant_id_eia','report_year']).any():
        raise QualityGateError('Duplicate yearly plant reporting metadata.')
    plants=plants.merge(reporting[['plant_id_eia','report_year','reporting_frequency_code']],on=['plant_id_eia','report_year'],validate='many_to_one')
    # EIA code AM reports actual monthly values once a year. A supplies only an
    # annual total, with estimated monthly values, and must not be a weather label.
    plants=plants[plants.reporting_frequency_code.isin(['M','AM'])]
    frame=plants.merge(generation,on=keys,validate='one_to_one')
    frame['actual_capacity_factor']=frame.net_generation_mwh/(frame.capacity_mw*frame.report_month.dt.days_in_month*24)
    frame=frame[frame.actual_capacity_factor.between(0,1) & frame.county.notna() & frame.latitude.notna() & frame.longitude.notna()].copy()
    frame['label_quality']='gold'
    frame['label_source']='EIA-923 reported plant wind generation / EIA-860 operating wind capacity'
    frame['generator_id']='plant-total'  # compatibility identifier, never a predictor
    frame['county_key']=frame.county.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county')
    frame['schema_version']=SCHEMA_VERSION
    return frame


def labels(directory):
    path=directory/'labels.parquet'
    if path.exists(): return pd.read_parquet(path)
    con=data.connection()
    sql_states=','.join(f"'{state}'" for state in STATES)
    sources={
        'generators':f"""SELECT plant_id_eia,generator_id,report_date AS report_month,capacity_mw,
          latitude,longitude,state,county,technology_description,generator_operating_date,generator_retirement_date
          FROM read_parquet(?) WHERE state IN ({sql_states}) AND report_date>='2019-01-01' AND report_date<'2024-01-01'""",
        'generation':"""SELECT plant_id_eia,report_date AS report_month,net_generation_mwh
          FROM read_parquet(?) WHERE energy_source_code='WND' AND prime_mover_code='WT'
          AND report_date>='2019-01-01' AND report_date<'2024-01-01'""",
        'reporting':"""SELECT plant_id_eia,report_date,reporting_frequency_code FROM read_parquet(?)
          WHERE report_date>='2019-01-01' AND report_date<'2024-01-01'"""}
    names={'generators':'out_eia__monthly_generators','generation':'core_eia923__monthly_generation_fuel','reporting':'out_eia__yearly_plants'}
    frames={}
    for name,sql in sources.items():
        cache=directory/f'source-{name}.parquet'
        if not cache.exists(): con.execute(sql,[data.PUDL+names[name]+'.parquet']).df().to_parquet(cache,index=False)
        frames[name]=pd.read_parquet(cache)
    con.close()
    result=clean_labels(**frames)
    # Balance regional coverage without looking at outcomes; bound source downloads.
    chosen=[]
    for _,group in result.groupby('state',sort=True):
        chosen.extend(sorted(group.plant_id_eia.unique(),key=lambda p:hashlib.sha256(f'plant-v3:{int(p)}'.encode()).hexdigest())[:40])
    result=result[result.plant_id_eia.isin(chosen)].copy()
    if result.plant_id_eia.nunique()<200:
        raise QualityGateError('Need at least 200 clean monthly-reporting wind plants for the expanded experiment.')
    result.to_parquet(path,index=False)
    return result


def rare(directory,frame):
    path=directory/'rare-monthly.parquet'
    if not path.exists():
        con=data.connection()
        sql_states=','.join(f"'{state}'" for state in STATES)
        table=con.execute(f"""SELECT state,place_name,county_id_fips,date_trunc('month',datetime_utc) AS report_month,
            avg(capacity_factor_onshore_wind) AS resource_expected_capacity_factor,
            count(capacity_factor_onshore_wind) AS baseline_hours
            FROM read_parquet(?) WHERE state IN ({sql_states}) AND report_year BETWEEN 2019 AND 2023
            AND county_id_fips IS NOT NULL GROUP BY ALL""",[data.PUDL+'out_vcerare__hourly_available_capacity_factor.parquet']).df()
        con.close()
        table['county_key']=table.place_name.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county')
        table.to_parquet(path,index=False)
    return data.rare(directory,frame)


def initialize_earth():
    import ee
    from pathlib import Path
    credentials=os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if credentials:
        account=json.loads(Path(credentials).read_text(encoding='utf-8'))['client_email']
        ee.Initialize(ee.ServiceAccountCredentials(account,credentials),project=os.environ['EARTH_ENGINE_PROJECT'])
    else: ee.Initialize(project=os.environ['EARTH_ENGINE_PROJECT'])
    ee.data.setDeadline(180000)
    return ee


def hourly_wind_image(image):
    u=image.select('u_component_of_wind_100m');v=image.select('v_component_of_wind_100m')
    speed=u.pow(2).add(v.pow(2)).sqrt()
    low=image.select('u_component_of_wind_10m').pow(2).add(image.select('v_component_of_wind_10m').pow(2)).sqrt()
    # Generic physical covariate only, not an engineering turbine yield claim.
    curve=speed.pow(3).subtract(3**3).divide(12**3-3**3).clamp(0,1).where(speed.gte(25),0)
    return speed.rename('era5_wind100_mean_mps').addBands([
        speed.pow(2).rename('wind_squared'),curve.rename('era5_power_proxy_cf'),
        speed.lt(3).rename('era5_low_wind_fraction'),speed.gte(12).rename('era5_high_wind_fraction'),
        low.rename('era5_wind10_mean_mps'),image.select('temperature_2m').subtract(273.15).rename('era5_temperature_c'),
        image.select('surface_pressure').rename('era5_surface_pressure_pa')])


def site_wind(directory,frame,progress=print):
    path=directory/'site-wind.parquet'
    if path.exists(): return frame.merge(pd.read_parquet(path),on=['plant_id_eia','report_month'],validate='many_to_one')
    ee=initialize_earth()
    plants=frame.sort_values('report_month').drop_duplicates('plant_id_eia')
    locations=ee.FeatureCollection([ee.Feature(ee.Geometry.Point([float(p.longitude),float(p.latitude)]),{'plant_id_eia':int(p.plant_id_eia)}) for p in plants.itertuples()])
    cache=directory/'era5';cache.mkdir(exist_ok=True)
    dates=sorted(frame.report_month.unique())
    def fetch(date):
        date=pd.Timestamp(date);file=cache/f'{date:%Y-%m}.parquet'
        saved=None;needed=plants
        if file.exists():
            saved=pd.read_parquet(file)
            if 'era5_hours' in saved and saved.era5_hours.min()>=date.days_in_month*24*.99:
                needed=plants[~plants.plant_id_eia.isin(saved.plant_id_eia)]
                if needed.empty: return saved[saved.plant_id_eia.isin(plants.plant_id_eia)]
            else: saved=None
        query_locations=locations if saved is None else ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([float(p.longitude),float(p.latitude)]),{'plant_id_eia':int(p.plant_id_eia)}) for p in needed.itertuples()])
        end=date+pd.offsets.MonthBegin(1)
        collection=ee.ImageCollection('ECMWF/ERA5/HOURLY').filterDate(f'{date:%Y-%m-%d}',f'{end:%Y-%m-%d}')
        stack=collection.map(hourly_wind_image).mean()
        standard=stack.select('wind_squared').subtract(stack.select('era5_wind100_mean_mps').pow(2)).max(0).sqrt().rename('era5_wind100_std_mps')
        stack=stack.addBands([standard,stack.select('era5_wind100_mean_mps').divide(stack.select('era5_wind10_mean_mps').max(.2)).rename('era5_shear_ratio')])
        stack=stack.addBands(collection.select('u_component_of_wind_100m').count().rename('era5_hours'))
        sampled=stack.select(WIND_FEATURES+['era5_hours']).reduceRegions(collection=query_locations,reducer=ee.Reducer.first(),scale=27830,tileScale=4).getInfo()
        rows=pd.DataFrame([feature['properties'] for feature in sampled['features']])
        rows['report_month']=date
        if saved is not None: rows=pd.concat([saved[saved.plant_id_eia.isin(plants.plant_id_eia)],rows],ignore_index=True)
        if len(rows)!=len(plants) or not np.isfinite(rows[WIND_FEATURES]).all(axis=None):
            raise QualityGateError(f'ERA5 did not cover every plant in {date:%Y-%m}.')
        if rows.era5_hours.min()<date.days_in_month*24*.99:
            raise QualityGateError(f'ERA5 hourly coverage is below 99% in {date:%Y-%m}.')
        temporary=file.with_suffix('.tmp')
        rows.to_parquet(temporary,index=False);temporary.replace(file)
        return rows
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i,rows in enumerate(pool.map(fetch,dates)):
            results.append(rows);progress(f'ERA5 monthly grids processed: {i+1}/{len(dates)}',flush=True)
    wind=pd.concat(results,ignore_index=True)
    wind.to_parquet(path,index=False)
    return frame.merge(wind,on=['plant_id_eia','report_month'],validate='many_to_one')
