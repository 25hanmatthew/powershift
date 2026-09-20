"""Development-only ISD/PUDL ablation; never reopens or replaces a release holdout."""
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from backend.ml import data
from backend.ml.protocol import geographic_blocks
from backend.ml.retraining import weights, block_mae
from backend.ml.training import metrics
from backend.ml.schema import ATLAS_FEATURES, FEATURES, QualityGateError

ISD_FEATURES = [f for f in FEATURES if f.startswith('isd_')]
DERIVED = ['station_era5_wind_gap_mps', 'station_era5_temp_gap_c', 'station_wind_variability', 'station_wind_range_mps']
FEATURE_SETS = {'atlas': ATLAS_FEATURES, 'isd': ATLAS_FEATURES+ISD_FEATURES, 'isd_physics': ATLAS_FEATURES+ISD_FEATURES+DERIVED}
SPECS = [{'model': model, 'features': features} for features in FEATURE_SETS for model in ['huber', 'ridge', 'lightgbm']]


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_features(frame):
    frame = frame.copy()
    frame['station_era5_wind_gap_mps'] = frame.isd_mean_wind_mps-frame.era5_wind10_mean_mps
    frame['station_era5_temp_gap_c'] = frame.isd_mean_temp_c-frame.era5_temperature_c
    frame['station_wind_variability'] = frame.isd_wind_std_mps/np.maximum(frame.isd_mean_wind_mps, .5)
    frame['station_wind_range_mps'] = frame.isd_wind_p90_mps-frame.isd_wind_p10_mps
    return frame


def quality(frame):
    if frame.duplicated(['plant_id_eia', 'report_month']).any():
        raise QualityGateError('Duplicate plant-months in paired experiment.')
    if not np.isfinite(frame[FEATURE_SETS['isd_physics']]).all(axis=None):
        raise QualityGateError('Nonfinite paired features.')
    if not (frame.isd_coverage_fraction.ge(.7)&frame.isd_nearest_station_km.le(100)).all():
        raise QualityGateError('Station observations failed distance or hourly coverage gates.')
    if frame.plant_id_eia.nunique()<100 or geographic_blocks(frame).nunique()<15:
        raise QualityGateError('Insufficient independent plants or geographic blocks.')
    if not frame.report_month.dt.year.between(2019, 2022).all():
        raise QualityGateError('Later-year release data entered development.')


def prepare(root):
    previous = Path('data/private/ml-v4')
    design = json.loads((previous/'protocol.json').read_text(encoding='utf-8'))
    table = pd.read_parquet(previous/'training.parquet')
    development = table[~geographic_blocks(table).isin(design['spatial_blocks']) & table.report_month.dt.year.isin(design['training_years'])].copy()
    config = {'version': 'wind-isd-pudl-development-v5', 'production_eligible': False,
              'purpose': 'Paired nested geographic CV on previous development rows only; no fresh-release claim.',
              'source_training_sha256': digest(previous/'training.parquet'), 'prior_evaluation_sha256': digest(previous/'evaluation.json'),
              'source_protocol_sha256': digest(previous/'protocol.json'), 'pudl_version':data.PUDL_VERSION,
              'years':[2019,2020,2021,2022], 'specifications': SPECS,
              'selection': 'Three inner geographic folds select minimum macro-block MAE; five outer geographic folds assess selection.',
              'weights':'Equal total fitting weight per plant', 'original_rows':len(development),
              'excluded_spatial_blocks':design['spatial_blocks'], 'schema':FEATURE_SETS}
    frozen = root/'protocol.json'
    if frozen.exists() and json.loads(frozen.read_text(encoding='utf-8')) != config:
        raise QualityGateError('Frozen experiment configuration changed; use a new experiment directory.')
    if not frozen.exists(): write(frozen, config)
    target=root/'training.parquet'
    if target.exists():
        frame=pd.read_parquet(target);quality(frame);return frame,config
    cache=Path('data/private/ml-v3-plant')
    if not (root/'isd-history.csv').exists(): shutil.copy2(cache/'isd-history.csv', root/'isd-history.csv')
    (root/'isd').mkdir(exist_ok=True)
    for file in (cache/'isd').glob('*.parquet'):
        if not (root/'isd'/file.name).exists(): shutil.copy2(file,root/'isd'/file.name)
    print(f'Preparing ISD observations for {len(development)} plant-months / {development.plant_id_eia.nunique()} plants',flush=True)
    frame=add_features(data.weather(root,development,years=config['years'],max_workers=6,prefer_archive=True))
    quality(frame)
    frame.to_parquet(target,index=False)
    write(root/'data-report.json',{'rows':len(frame),'plants':int(frame.plant_id_eia.nunique()),'blocks':int(geographic_blocks(frame).nunique()),
         'retained_fraction':len(frame)/len(development),'states':frame.groupby('state').plant_id_eia.nunique().to_dict(),
         'isd_station_distance_km':frame.isd_nearest_station_km.describe().to_dict(), 'minimum_hourly_coverage':float(frame.isd_coverage_fraction.min()),
         'data_sha256':digest(target), 'sources':{'labels':'PUDL EIA-923 wind-only monthly plant generation / EIA-860 operating capacity',
         'weather':'NOAA ISD full archives / equivalent Global Hourly records, quality codes 1 and 5, one vote per hour',
         'baseline':'PUDL VCE RARE; same county-month baseline for every comparison'}, 'production_eligible':False})
    return frame,config


def fit(spec, frame):
    name=spec['model']
    if name=='huber': model=make_pipeline(StandardScaler(),HuberRegressor(alpha=1,epsilon=1.1,max_iter=2000,tol=1e-6))
    elif name=='ridge': model=make_pipeline(StandardScaler(),Ridge(alpha=100))
    else: model=LGBMRegressor(objective='regression_l1',n_estimators=300,num_leaves=15,min_child_samples=100,
            learning_rate=.035,reg_lambda=10,n_jobs=4,verbosity=-1,random_state=42,deterministic=True,force_col_wise=True)
    key={'huber':'huberregressor__sample_weight','ridge':'ridge__sample_weight'}.get(name,'sample_weight')
    with threadpool_limits(limits=1,user_api='blas'):
        model.fit(frame[FEATURE_SETS[spec['features']]],frame.residual_target,**{key:weights(frame)})
    return model


def folds(frame,n):
    for a,b in GroupKFold(n_splits=n).split(frame,groups=geographic_blocks(frame)):
        if set(geographic_blocks(frame.iloc[a])) & set(geographic_blocks(frame.iloc[b])):
            raise QualityGateError('Geographic fold overlap.')
        if set(frame.iloc[a].plant_id_eia)&set(frame.iloc[b].plant_id_eia):
            raise QualityGateError('Plant overlap across geographic folds.')
        yield a,b


def select(frame):
    results=[]
    for spec in SPECS:
        predictions=np.zeros(len(frame))
        for a,b in folds(frame,3):
            model=fit(spec,frame.iloc[a]);predictions[b]=model.predict(frame.iloc[b][FEATURE_SETS[spec['features']]])
        results.append({'spec':spec,'block_mae':block_mae(frame,predictions)})
    return {group:min([r for r in results if (r['spec']['features']=='atlas')==(group=='atlas')],key=lambda r:r['block_mae'])['spec'] for group in ['atlas','isd']},results


def train(root,frame,config):
    marker=root/'training-started.json'
    if marker.exists(): raise QualityGateError('This experiment already started training. Preserve its results; do not retune this run.')
    write(marker,{'started_at':datetime.now(timezone.utc).isoformat(),'protocol_sha256':digest(root/'protocol.json'),'data_sha256':digest(root/'training.parquet')})
    predictions={name:np.zeros(len(frame)) for name in ['atlas','isd','v4_fixed','resource_only']};audit=[]
    for fold,(a,b) in enumerate(folds(frame,5)):
        fit_rows,validation=frame.iloc[a],frame.iloc[b]
        chosen,inner=select(fit_rows)
        chosen['v4_fixed']={'model':'huber','features':'atlas'}
        for name,spec in chosen.items():
            model=fit(spec,fit_rows);predictions[name][b]=model.predict(validation[FEATURE_SETS[spec['features']]])
        entry={'fold':fold,'fit_blocks':sorted(set(geographic_blocks(fit_rows))),'validation_blocks':sorted(set(geographic_blocks(validation))),
              'chosen':chosen,'inner_search':inner,'validation_rows':len(b),'validation_plants':int(validation.plant_id_eia.nunique()),
              'scores':{name:block_mae(validation,predictions[name][b]) for name in predictions}}
        audit.append(entry);write(root/'progress.json',{'completed_folds':len(audit),'total_folds':5,'folds':audit})
        print(f'Outer fold {fold+1}/5: '+str(entry['scores']),flush=True)
    scores={name:{**metrics(frame,values),'macro_block_mae_cf_points':block_mae(frame,values)} for name,values in predictions.items()}
    improvement={name:100*(1-scores['isd']['mae_cf_points']/scores[name]['mae_cf_points']) for name in ['atlas','v4_fixed','resource_only']}
    selected,final_search=select(frame)
    spec=selected['isd'];model=fit(spec,frame);features=FEATURE_SETS[spec['features']]
    if spec['model']=='lightgbm':
        artifact=root/'research-wind.txt';model.booster_.save_model(str(artifact))
    else:
        from backend.ml.linear_model import payload_from_pipeline,LinearModel
        artifact=root/'research-wind.json';payload=payload_from_pipeline(model,frame,features);write(artifact,payload)
        if not np.allclose(LinearModel(payload).predict(frame[features]),model.predict(frame[features]),atol=1e-10):
            raise QualityGateError('Portable model parity failed.')
    output=frame[['plant_id_eia','report_month','state','actual_capacity_factor','resource_expected_capacity_factor']].copy()
    for name,values in predictions.items():output[name+'_residual']=values
    output.to_parquet(root/'development-predictions.parquet',index=False)
    by_state={state:{name:metrics(frame[frame.state==state],values[frame.state.eq(state)]) for name,values in predictions.items()} for state in sorted(frame.state.unique())}
    unchanged=digest(Path('data/private/ml-v4/evaluation.json'))==config['prior_evaluation_sha256']
    if not unchanged:raise QualityGateError('Prior release evaluation changed.')
    report={'status':'development_complete','production_eligible':False,'selected':spec,'features':features,'scores':scores,
        'isd_mae_improvement_pct_vs':improvement,'folds':audit,'by_state':by_state,'final_development_search':final_search,
        'rows':len(frame),'plants':int(frame.plant_id_eia.nunique()),'geographic_blocks':int(geographic_blocks(frame).nunique()),
        'artifact':str(artifact),'artifact_sha256':digest(artifact),'prior_release_preserved':unchanged,
        'decision':'Research model trained with ISD and PUDL. Nested geographic development results do not replace untouched spatial and later-year release tests. Live corrections remain off.',
        'completed_at':datetime.now(timezone.utc).isoformat()}
    write(root/'evaluation.json',report);print(json.dumps({'scores':scores,'isd_mae_improvement_pct_vs':improvement,'selected':spec}),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--directory',type=Path,default=Path('data/private/ml-v5-isd'));args=parser.parse_args()
    root=args.directory;root.mkdir(parents=True,exist_ok=True)
    if (root/'evaluation.json').exists():raise QualityGateError('Completed experiment is immutable.')
    try:
        frame,config=prepare(root)
        if args.prepare_only: print(f'Data ready: {len(frame)} rows / {frame.plant_id_eia.nunique()} plants',flush=True);return
        report=train(root,frame,config);write(root/'pipeline-report.json',report)
    except Exception as error:
        write(root/'failure.json',{'error':str(error),'production_eligible':False,'at':datetime.now(timezone.utc).isoformat()});raise

if __name__=='__main__':main()
