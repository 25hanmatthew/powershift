"""Versioned PUDL joins and selective NOAA downloads for offline wind training."""
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import duckdb
from .schema import QualityGateError, SCHEMA_VERSION, FEATURES

PUDL_VERSION = 'v2026.9.0'
PUDL = f'https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/{PUDL_VERSION}/'
STATES = ['CA','NV','OR','WA']
YEARS = [2021,2022,2023]


def connection():
    con=duckdb.connect()
    con.execute("SET memory_limit='1GB'; SET threads=4; SET enable_progress_bar=false; INSTALL httpfs; LOAD httpfs;")
    return con


def quality_summary(frame):
    return {'rows':len(frame), 'plants':int(frame.plant_id_eia.nunique()),
            'generators':int(frame[['plant_id_eia','generator_id']].drop_duplicates().shape[0]),
            'date_start':str(frame.report_month.min().date()) if len(frame) else None,
            'date_end':str(frame.report_month.max().date()) if len(frame) else None,
            'technology':'onshore wind', 'label_quality':frame.label_quality.value_counts().to_dict(),
            'missingness':{str(k):round(float(v),4) for k,v in frame.isna().mean().items()}}


def labels(directory, max_plants=60):
    target=directory/'labels.parquet'
    if target.exists(): return pd.read_parquet(target)
    con=connection()
    frame=con.execute("""SELECT plant_id_eia,generator_id,report_date as report_month,
        capacity_mw,capacity_factor,latitude,longitude,state,county,generator_operating_date,
        generator_retirement_date FROM read_parquet(?)
        WHERE technology_description='Onshore Wind Turbine' AND state IN ('CA','NV','OR','WA')
        AND operational_status='existing' AND report_date>='2021-01-01' AND report_date<'2024-01-01'
        AND capacity_mw>0 AND latitude IS NOT NULL AND longitude IS NOT NULL""",
        [PUDL+'out_eia__monthly_generators.parquet']).df()
    direct=con.execute("""SELECT plant_id_eia,generator_id,report_date as report_month,
        net_generation_mwh FROM read_parquet(?) WHERE report_date>='2021-01-01'
        AND report_date<'2024-01-01'""",[PUDL+'core_eia923__monthly_generation.parquet']).df()
    con.close()
    keys=['plant_id_eia','generator_id','report_month']
    if frame.duplicated(keys).any() or direct.duplicated(keys).any():
        raise QualityGateError('Duplicate generator-month keys in PUDL input.')
    frame=frame.merge(direct,on=keys,how='left',validate='one_to_one')
    # Exclude partial operating/retirement months, for which nameplate*hours is biased.
    frame=frame[(frame.generator_operating_date<=frame.report_month) &
                (frame.generator_retirement_date.isna() | (frame.generator_retirement_date>frame.report_month+pd.offsets.MonthEnd(0)))]
    reported=frame.net_generation_mwh/(frame.capacity_mw*frame.report_month.dt.days_in_month*24)
    frame['label_quality']=np.where(reported.notna(),'gold','silver')
    frame['actual_capacity_factor']=reported.fillna(frame.capacity_factor)
    initial=len(frame)
    frame=frame[frame.actual_capacity_factor.between(0,1) & frame.county.notna()].copy()
    removed_invalid=initial-len(frame)
    valid_rows=len(frame)
    # Stable plant selection does not inspect outcome values or benchmark performance.
    plants=sorted(frame.plant_id_eia.unique(),key=lambda x:hashlib.sha256(str(x).encode()).hexdigest())[:max_plants]
    frame=frame[frame.plant_id_eia.isin(plants)].copy()
    frame['county_key']=frame.county.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county')
    frame['schema_version']=SCHEMA_VERSION
    if len(frame)<600 or frame.plant_id_eia.nunique()<30:
        raise QualityGateError(f'Insufficient clean labels: {len(frame)} rows, {frame.plant_id_eia.nunique()} plants; need 600 rows and 30 plants.')
    frame.to_parquet(target,index=False)
    report=quality_summary(frame)
    report.update(invalid_rows_removed_before_sampling=removed_invalid,rows_outside_selected_plants=valid_rows-len(frame))
    (directory/'labels-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return frame


def rare(directory, frame):
    target=directory/'rare-monthly.parquet'
    if target.exists(): monthly=pd.read_parquet(target)
    else:
        con=connection()
        # Read only the region, years and wind column through Parquet HTTP range requests.
        monthly=con.execute("""SELECT state,place_name,county_id_fips,
            date_trunc('month',datetime_utc) AS report_month,
            avg(capacity_factor_onshore_wind) AS resource_expected_capacity_factor,
            count(capacity_factor_onshore_wind) AS baseline_hours
            FROM read_parquet(?) WHERE state IN ('CA','NV','OR','WA')
            AND report_year BETWEEN 2021 AND 2023 AND county_id_fips IS NOT NULL
            GROUP BY ALL""",[PUDL+'out_vcerare__hourly_available_capacity_factor.parquet']).df()
        con.close()
        monthly['county_key']=monthly.place_name.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county')
        monthly.to_parquet(target,index=False)
    keys=['state','county_key','report_month']
    if monthly.duplicated(keys).any(): raise QualityGateError('RARE county-name mapping is ambiguous; require a verified FIPS crosswalk.')
    result=frame.merge(monthly,on=keys,how='left',validate='many_to_one')
    valid=result.resource_expected_capacity_factor.between(0,1)&(result.baseline_hours>=result.report_month.dt.days_in_month*24*.95)
    coverage=float(valid.mean())
    if coverage<.95: raise QualityGateError(f'RARE county-month join coverage is {coverage:.1%}; require 95%.')
    result=result[valid].copy()
    result['residual_target']=result.actual_capacity_factor-result.resource_expected_capacity_factor
    result.to_parquet(directory/'labels-baseline.parquet',index=False)
    return result,coverage


def station_catalog(directory, years=None):
    years = years or YEARS
    target=directory/'isd-history.csv'
    if not target.exists():
        response=httpx.get('https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv',timeout=60)
        response.raise_for_status();target.write_bytes(response.content)
    frame=pd.read_csv(target,dtype={'USAF':str,'WBAN':str,'BEGIN':str,'END':str})
    frame=frame[(frame.CTRY=='US') & frame.LAT.notna() & frame.LON.notna() &
                (frame.BEGIN<=f'{min(years)}0101') & (frame.END>=f'{max(years)}1231')].copy()
    frame['station_id']=frame.USAF.str.zfill(6)+frame.WBAN.str.zfill(5)
    return frame.drop_duplicates('station_id')


def nearby_stations(plants, stations):
    from backend.scoring import haversine
    matches=[]
    for p in plants.itertuples():
        nearby=[(haversine((p.longitude,p.latitude),(s.LON,s.LAT)),s.station_id) for s in stations.itertuples()]
        for distance,station in sorted(nearby)[:3]:
            if distance<=100: matches.append({'plant_id_eia':p.plant_id_eia,'station_id':station,'distance_km':distance})
    return pd.DataFrame(matches,columns=['plant_id_eia','station_id','distance_km'])


def decode(series, position, quality_position, missing, scale):
    fields=series.fillna('').str.split(',',expand=True)
    if fields.shape[1]<=max(position,quality_position): return pd.Series(np.nan,index=series.index)
    values=pd.to_numeric(fields[position],errors='coerce')
    return (values/scale).where(fields[quality_position].isin(['1','5']) & (values.abs()!=missing))


def aggregate_station(frame):
    dates=pd.to_datetime(frame.DATE,errors='coerce',utc=True).dt.tz_localize(None).dt.floor('h')
    weather=pd.DataFrame({'hour':dates,'wind':decode(frame.WND,3,4,9999,10),
                          'temp':decode(frame.TMP,0,1,9999,10)})
    weather=weather.dropna(subset=['hour']).groupby('hour').mean()  # one vote per observed hour
    weather['report_month']=weather.index.to_period('M').to_timestamp()
    rows=[]
    for month,group in weather.groupby('report_month'):
        wind=group.wind.dropna(); temp=group.temp.dropna()
        if len(wind)<2: continue
        rows.append({'report_month':month,'isd_mean_wind_mps':wind.mean(),'isd_wind_std_mps':wind.std(),
                     'isd_wind_p10_mps':wind.quantile(.1),'isd_wind_p90_mps':wind.quantile(.9),
                     'isd_mean_temp_c':temp.mean(),'isd_coverage_fraction':min(len(wind),len(temp))/(month.days_in_month*24)})
    return pd.DataFrame(rows)


def decode_isd_archive(content):
    """Mandatory ISD fields, zero-based offsets from NOAA's format specification.

    https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf
    Preserve the same wind/temperature quality codes as the Global Hourly CSV.
    """
    import gzip
    records=[line for line in gzip.decompress(content).decode('ascii').splitlines() if len(line)>=105]
    return pd.DataFrame({
        'DATE':pd.to_datetime([line[15:27] for line in records],format='%Y%m%d%H%M',errors='coerce'),
        'WND':[','.join([line[60:63],line[63:64],line[64:65],line[65:69],line[69:70]]) for line in records],
        'TMP':[line[87:92]+','+line[92:93] for line in records]})


def weather(directory, frame, years=None, max_workers=4, prefer_archive=False):
    years = years or YEARS
    target=directory/'weather.parquet'
    if target.exists(): weather_frame=pd.read_parquet(target)
    else:
        stations=station_catalog(directory, years)
        plants=frame.sort_values('report_month').drop_duplicates('plant_id_eia')[['plant_id_eia','latitude','longitude']]
        matches=nearby_stations(plants,stations)
        if matches.plant_id_eia.nunique()/len(plants)<.9:
            raise QualityGateError('Fewer than 90% of training plants have ISD stations within 100 km.')
        matches.to_parquet(directory/'station-matches.parquet',index=False)
        weather_dir=directory/'isd';weather_dir.mkdir(exist_ok=True)
        errors=[]
        def fetch(pair):
            station,year=pair;path=weather_dir/f'{station}-{year}.parquet'
            if path.exists(): return pd.read_parquet(path)
            url=f'https://noaa-global-hourly-pds.s3.amazonaws.com/{year}/{station}.csv'
            try:
                raw=None;source_format='global_hourly_csv'
                if prefer_archive:
                    archive=f'https://www.ncei.noaa.gov/pub/data/noaa/{year}/{station[:6]}-{station[6:]}-{year}.gz'
                    try:
                        response=httpx.get(archive,timeout=30);response.raise_for_status()
                        raw=decode_isd_archive(response.content);source_format='isd_full_gzip'
                    except (httpx.HTTPError,ValueError,OSError):
                        pass  # the public CSV mirror remains an equivalent fallback
                if raw is None:
                    response=httpx.get(url,timeout=60);response.raise_for_status()
                    from io import BytesIO
                    raw=pd.read_csv(BytesIO(response.content),usecols=lambda c:c in ['DATE','WND','TMP'],dtype=str)
                data=aggregate_station(raw)
                if data.empty: return data
                data['station_id']=station
                data['source_format']=source_format
                temporary=path.with_suffix('.tmp')
                data.to_parquet(temporary,index=False);temporary.replace(path)
                return data
            except Exception as error:
                errors.append({'station':station,'year':year,'error':type(error).__name__})
                return pd.DataFrame()
        pairs=[(station,year) for station in sorted(matches.station_id.unique()) for year in years]
        results=[]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for i,data in enumerate(executor.map(fetch,pairs)):
                if len(data): results.append(data)
                if (i+1)%15==0: print(f'NOAA station-years processed: {i+1}/{len(pairs)}',flush=True)
        formats={}
        for result in results:
            key=result.source_format.iloc[0] if 'source_format' in result else 'cached_global_hourly_csv'
            formats[key]=formats.get(key,0)+1
        (directory/'weather-download-report.json').write_text(json.dumps({'requested_station_years':len(pairs),'source_formats':formats,'failed_downloads':errors},indent=2),encoding='utf-8')
        if not results: raise QualityGateError('No usable NOAA station-years.')
        data=matches.merge(pd.concat(results,ignore_index=True),on='station_id',validate='many_to_many')
        data=data[data.isd_coverage_fraction>=.7].copy()
        rows=[]
        weather_columns=['isd_mean_wind_mps','isd_wind_std_mps','isd_wind_p10_mps','isd_wind_p90_mps','isd_mean_temp_c','isd_coverage_fraction']
        for (plant,month),group in data.groupby(['plant_id_eia','report_month']):
            weights=1/np.maximum(group.distance_km.to_numpy(),1)**2
            row={name:float(np.average(group[name],weights=weights)) for name in weather_columns}
            row.update(plant_id_eia=plant,report_month=month,isd_station_count=len(group),isd_nearest_station_km=float(group.distance_km.min()))
            rows.append(row)
        weather_frame=pd.DataFrame(rows)
        weather_frame.to_parquet(target,index=False)
    result=frame.merge(weather_frame,on=['plant_id_eia','report_month'],how='left',validate='many_to_one')
    valid=result.isd_coverage_fraction.ge(.7)
    if valid.mean()<.8: raise QualityGateError(f'Only {valid.mean():.1%} of generator-months meet 70% hourly weather coverage; require 80%.')
    return result[valid].copy()


def terrain(directory, frame, required_features=None):
    required_features=required_features or FEATURES
    target=directory/'terrain.parquet'
    if target.exists(): context=pd.read_parquet(target)
    else:
        import ee
        import os
        credentials=os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if credentials:
            account=json.loads(Path(credentials).read_text())['client_email']
            ee.Initialize(ee.ServiceAccountCredentials(account,credentials),project=os.environ['EARTH_ENGINE_PROJECT'])
        else: ee.Initialize(project=os.environ['EARTH_ENGINE_PROJECT'])
        ee.data.setDeadline(180000)
        plants=frame.sort_values('report_month').drop_duplicates('plant_id_eia')
        features=[]
        for p in plants.itertuples():
            dx=1/(111.32*math.cos(math.radians(p.latitude)));dy=1/111.32
            features.append(ee.Feature(ee.Geometry.Rectangle([p.longitude-dx,p.latitude-dy,p.longitude+dx,p.latitude+dy]),{'plant_id_eia':int(p.plant_id_eia)}))
        elevation=ee.Image('USGS/SRTMGL1_003').select('elevation').rename('elevation_m')
        cover=ee.ImageCollection('ESA/WorldCover/v200').first().select('Map')
        image=elevation.addBands([ee.Terrain.slope(elevation).rename('slope_deg'),
              cover.eq(50).multiply(100).rename('developed_pct'),
              cover.remap([10,20,30,90,95,100],[1,1,1,1,1,1],0).multiply(100).rename('natural_pct')])
        sampled=image.reduceRegions(collection=ee.FeatureCollection(features),reducer=ee.Reducer.mean(),scale=100,tileScale=4).getInfo()
        context=pd.DataFrame([feature['properties'] for feature in sampled['features']])
        context.to_parquet(target,index=False)
    result=frame.merge(context,on='plant_id_eia',how='left',validate='many_to_one')
    result['month_sin']=np.sin(2*np.pi*result.report_month.dt.month/12)
    result['month_cos']=np.cos(2*np.pi*result.report_month.dt.month/12)
    valid=np.isfinite(result[required_features]).all(axis=1)
    if valid.mean()<.95: raise QualityGateError(f'Only {valid.mean():.1%} of rows have complete finite features; require 95%.')
    result=result[valid].copy()
    if len(result)<600 or result.plant_id_eia.nunique()<30:
        raise QualityGateError('Final joined training table needs at least 600 rows and 30 plants.')
    result.to_parquet(directory/'training.parquet',index=False)
    report=quality_summary(result)
    report.update(schema_version=SCHEMA_VERSION,features=FEATURES,pudl_version=PUDL_VERSION,
                  states=STATES,retained_states=sorted(result.state.unique().tolist()),isd_minimum_hourly_coverage=.7,isd_max_station_km=100,
                  excluded_predictors=['plant_id_eia','generator_id','plant_name','latitude','longitude','label_quality','actual_capacity_factor','year','plant_age_years','capacity_mw'],
                  notes=['Generator age and capacity are not predictors: new-site operational characteristics are not yet known.',
                         'Quality labels are retained for weighting and reporting, never used as predictors.',
                         'RARE county averages are not engineering-grade site forecasts.',
                         'Ground wind observations are station height; the RARE baseline represents modeled generation.'])
    (directory/'training-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return result,report
