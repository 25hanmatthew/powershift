"""Publish a compact, audited replay of the frozen ISD/PUDL study. Never fit a model."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from backend.ml.linear_model import LinearModel
from backend.ml.data import PUDL, connection

ROOT=Path('data/private/ml-v9-insights')
OUTPUT=Path('data/public/wind-fleet-2024.json')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8'))
def require(condition,message):
    if not condition: raise ValueError(message)

def audit(frame,history,artifact,evaluation):
    require(not frame.duplicated(['plant_id_eia','report_month']).any(),'Duplicate plant-month observations.')
    require(frame.report_month.dt.year.eq(2024).all(),'Replay must contain only held-out 2024 observations.')
    require(history.report_month.dt.year.between(2019,2022).all(),'Historical baseline contains a test year.')
    require(frame.isd_coverage_fraction.ge(.7).all() and frame.isd_nearest_station_km.le(100).all(),'Weather quality gate failed.')
    require(frame.label_quality.eq('gold').all() and frame.capacity_mw.gt(0).all(),'Reported-generation quality gate failed.')
    require(frame.actual_capacity_factor.between(0,1).all() and frame.isd_station_count.ge(1).all(),'Invalid capacity factor or station count.')
    reference=history.assign(month=history.report_month.dt.month).groupby(['plant_id_eia','month']).actual_capacity_factor.agg(['mean','count'])
    keys=pd.MultiIndex.from_arrays([frame.plant_id_eia,frame.report_month.dt.month])
    expected=reference.reindex(keys)
    require(expected['count'].ge(3).all() and np.allclose(expected['mean'],frame.baseline_cf,atol=1e-10),'Seasonal reference does not reconcile.')
    denom=frame.capacity_mw*frame.report_month.dt.days_in_month*24
    require(np.allclose(frame.net_generation_mwh/denom,frame.actual_capacity_factor,atol=1e-10),'PUDL generation and capacity do not reconcile.')
    model=LinearModel(artifact['models']['weather']['parameters'])
    predicted=np.clip(frame.baseline_cf+model.predict(frame[model.features]),0,1)
    require(np.allclose(predicted,frame.prediction_weather,atol=1e-10),'Frozen model predictions do not reconcile.')
    for name,column in [('pudl_seasonal','baseline_cf'),('weather','prediction_weather'),('wind_only','prediction_wind_only')]:
        mae=float((frame.actual_capacity_factor-frame[column]).abs().mean()*100)
        require(abs(mae-evaluation['scores'][name]['mae_cf_points'])<1e-9,'Published benchmark does not reconcile.')
    require(len(frame)==evaluation['rows'] and frame.plant_id_eia.nunique()==evaluation['plants'],'Study coverage changed.')
    return model

def export():
    started=read(ROOT/'evaluation-started.json');provenance=read(ROOT/'report-provenance.json')
    for filename,expected in [('test-2024.parquet',started['test_data_sha256']),('evaluation-plan.json',started['plan_sha256']),
                              ('research-weather-models.json',started['model_sha256']),('evaluation.json',provenance['evaluation_sha256'])]:
        require(digest(ROOT/filename)==expected,f'Frozen {filename} changed.')
    frame=pd.read_parquet(ROOT/'predictions-2024.parquet').sort_values(['plant_id_eia','report_month'])
    history=pd.read_parquet(ROOT/'history.parquet');evaluation=read(ROOT/'evaluation.json');artifact=read(ROOT/'research-weather-models.json')
    model=audit(frame,history,artifact,evaluation)
    names_path=ROOT/'plant-names-2024.parquet'
    if not names_path.exists():
        con=connection()
        names=con.execute("SELECT plant_id_eia,plant_name_eia FROM read_parquet(?) WHERE report_date='2024-01-01' AND plant_id_eia IN (SELECT unnest(?))",
                          [PUDL+'out_eia__yearly_plants.parquet',[int(v) for v in frame.plant_id_eia.unique()]]).df()
        names.to_parquet(names_path,index=False);con.close()
    names=pd.read_parquet(names_path).set_index('plant_id_eia').plant_name_eia.to_dict()
    stations=pd.read_csv('data/private/ml-v3-plant/isd-history.csv',dtype={'USAF':str,'WBAN':str})
    stations['id']=stations.USAF.str.zfill(6)+stations.WBAN.str.zfill(5)
    stations=stations.drop_duplicates('id').set_index('id')
    matches=pd.read_parquet(ROOT/'station-matches.parquet')
    plants=[]
    for plant_id,part in frame.groupby('plant_id_eia',sort=True):
        row=part.iloc[-1]; station_rows=[]
        for match in matches[matches.plant_id_eia.eq(plant_id)].itertuples():
            station_id=str(match.station_id);meta=stations.loc[station_id] if station_id in stations.index else None
            station_rows.append({'id':station_id,'distance_km':round(match.distance_km,2),
                'name':str(meta['STATION NAME']) if meta is not None else station_id,
                'latitude':float(meta.LAT) if meta is not None and pd.notna(meta.LAT) else None,
                'longitude':float(meta.LON) if meta is not None and pd.notna(meta.LON) else None})
        months=[]
        for _,r in part.iterrows():
            hours=int(r.report_month.days_in_month*24);denom=float(r.capacity_mw*hours)
            contributions={name:round(float(r[name]*coefficient*100),5) for name,coefficient in zip(model.features,model.coefficients)}
            raw_prediction=float(r.baseline_cf+sum(r[name]*coefficient for name,coefficient in zip(model.features,model.coefficients))+model.intercept)
            contributions['intercept']=round(model.intercept*100,5)
            contributions['physical_bound']=round((float(r.prediction_weather)-raw_prediction)*100,5)
            months.append({'month':int(r.report_month.month),'capacity_mw':float(r.capacity_mw),'hours':hours,
                'actual_mwh':round(float(r.net_generation_mwh),3),'baseline_mwh':round(float(r.baseline_cf)*denom,3),
                'weather_mwh':round(float(r.prediction_weather)*denom,3),'wind_only_mwh':round(float(r.prediction_wind_only)*denom,3),
                'baseline_cf':round(float(r.baseline_cf),8),'actual_cf':round(float(r.actual_capacity_factor),8),
                'weather_cf':round(float(r.prediction_weather),8),'wind_delta':round(float(r.wind_delta),5),
                'wind_mps':round(float(r.isd_mean_wind_mps),3),'temperature_c':round(float(r.isd_mean_temp_c),2),
                'coverage':round(float(r.isd_coverage_fraction),5),'station_count':int(r.isd_station_count),
                'reference_years':int(r.reference_count),'contributions_cf_points':contributions})
        plants.append({'id':int(plant_id),'name':names.get(int(plant_id),f'EIA plant {plant_id}'),'state':str(row.state),
            'county':str(row.county),'latitude':float(row.latitude),'longitude':float(row.longitude),
            'capacity_mw':float(row.capacity_mw),'stations':station_rows,'months':months})
    quality=read(ROOT/'data-report.json');supplement=read(ROOT/'supplemental-analysis.json')
    payload={'version':'isd-pudl-replay-v1','test_year':2024,'train_years':[2019,2020,2021,2022],
        'task':'Historical operating-plant analysis with observed weather. Not a forecast or a new-site yield estimate.',
        'benchmark':{'rows':evaluation['rows'],'plants':evaluation['plants'],'states':evaluation['states'],
            'training_rows':evaluation['historical_training_rows'],'scores':evaluation['scores'],
            'weather_gain':evaluation['primary_hypotheses']['H1'],'low_wind':evaluation['primary_hypotheses']['H2'],
            'wind_only_gain_share_pct':supplement['wind_only_share_of_observed_mae_gain_pct'],
            'weather_vs_wind_only':evaluation['weather_vs_wind_only'],'shortfalls':evaluation['unexplained_shortfalls_descriptive']},
        'quality':quality,'sources':[
            {'name':'NOAA Integrated Surface Database','url':'https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database'},
            {'name':'PUDL EIA-923 generation + EIA-860 capacity','url':'https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/eia923.html'}],
        'provenance':{'pudl_version':quality['source_version'],'model_sha256':started['model_sha256'],
            'evaluation_sha256':provenance['evaluation_sha256'],'predictions_sha256':digest(ROOT/'predictions-2024.parquet'),
            'exporter_sha256':digest(Path(__file__)),'audits':['Unique plant-months','Reported generation / capacity parity','Historical-only seasonal baselines','Frozen model prediction parity','Benchmark parity','Weather coverage gates']},
        'plants':plants}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(payload,separators=(',',':'),allow_nan=False),encoding='utf-8')
    print(f'Exported {len(plants)} plants / {len(frame)} months; {OUTPUT.stat().st_size:,} bytes. All audits passed.')

if __name__=='__main__': export()
