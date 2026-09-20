"""Offline residual regression with untouched spatial and temporal test sets."""
import hashlib
import json
from datetime import datetime, timezone

import numpy as np
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from lightgbm import LGBMRegressor, early_stopping, log_evaluation

from .schema import FEATURES, SCHEMA_VERSION, BASELINE_ID, QualityGateError


def metrics(frame, residual):
    actual=frame.actual_capacity_factor.to_numpy()
    prediction=np.clip(frame.resource_expected_capacity_factor.to_numpy()+residual,0,1)
    correlation=spearmanr(actual,prediction).statistic if len(frame)>1 else np.nan
    return {'rows':len(frame),'plants':int(frame.plant_id_eia.nunique()),
            'mae_cf_points':float(mean_absolute_error(actual,prediction)*100),
            'rmse_cf_points':float(np.sqrt(mean_squared_error(actual,prediction))*100),
            'r2':float(r2_score(actual,prediction)) if len(frame)>1 else None,
            'spearman':float(correlation) if np.isfinite(correlation) else None}


def splits(frame):
    # Approximately 150-220 km blocks in the western US. No coordinates enter the model.
    blocks=(np.floor(frame.latitude/2).astype(int).astype(str)+':'+np.floor(frame.longitude/2).astype(int).astype(str))
    if blocks.nunique()<5: raise QualityGateError('Need at least five geographic blocks for an independent spatial holdout.')
    fit_indices,spatial_indices=next(GroupShuffleSplit(n_splits=1,test_size=.25,random_state=42).split(frame,groups=blocks))
    development=frame.iloc[fit_indices]
    spatial=frame.iloc[spatial_indices]
    train=development[development.report_month.dt.year<2023]
    temporal=development[development.report_month.dt.year==2023]
    # Spatial evaluation uses the same historical years as fit; temporal is separately held out.
    spatial=spatial[spatial.report_month.dt.year<2023]
    if train.plant_id_eia.nunique()<15 or spatial.plant_id_eia.nunique()<5 or len(temporal)<120:
        raise QualityGateError('Insufficient independent train/spatial/temporal samples after blocking.')
    if set(train.plant_id_eia)&set(spatial.plant_id_eia): raise QualityGateError('Spatial split leaks a plant.')
    return train.copy(),spatial.copy(),temporal.copy(),blocks


def estimator(name):
    if name=='ridge': return make_pipeline(StandardScaler(),Ridge(alpha=10))
    if name=='random_forest': return RandomForestRegressor(n_estimators=160,min_samples_leaf=12,max_features=.8,n_jobs=4,random_state=42)
    return LGBMRegressor(objective='huber',n_estimators=500,learning_rate=.035,num_leaves=15,
                         min_child_samples=30,colsample_bytree=.85,reg_lambda=2,
                         random_state=42,n_jobs=4,verbosity=-1)


def fit(model,name,frame,validation=None):
    weights=np.where(frame.label_quality=='gold',1.,.5)
    if name=='ridge':
        model.fit(frame[FEATURES],frame.residual_target,ridge__sample_weight=weights)
    elif name=='lightgbm' and validation is not None:
        model.fit(frame[FEATURES],frame.residual_target,sample_weight=weights,
                  eval_set=[(validation[FEATURES],validation.residual_target)],eval_metric='l1',
                  callbacks=[early_stopping(35,verbose=False),log_evaluation(0)])
    else: model.fit(frame[FEATURES],frame.residual_target,sample_weight=weights)
    return model


def train(frame,directory,stage):
    train_frame,spatial,temporal,blocks=splits(frame)
    folds=list(GroupKFold(n_splits=5).split(train_frame,groups=train_frame.plant_id_eia))
    fold_audit=[]; comparisons=[]
    for name in ['zero_residual','ridge','random_forest','lightgbm']:
        if name=='lightgbm': stage('V6','Training LightGBM Huber and evaluating untouched holdouts')
        estimates=np.zeros(len(train_frame)); rounds=[]
        for fold,(fit_index,valid_index) in enumerate(folds):
            fit_rows=train_frame.iloc[fit_index];valid_rows=train_frame.iloc[valid_index]
            overlap=set(fit_rows.plant_id_eia)&set(valid_rows.plant_id_eia)
            if overlap: raise QualityGateError('A plant appears in both grouped training and validation.')
            if name=='zero_residual':
                fold_audit.append({'fold':fold,'train_plants':sorted(int(x) for x in fit_rows.plant_id_eia.unique()),
                                   'validation_plants':sorted(int(x) for x in valid_rows.plant_id_eia.unique()),'plant_overlap':0})
                continue
            model=fit(estimator(name),name,fit_rows,valid_rows)
            estimates[valid_index]=model.predict(valid_rows[FEATURES])
            if name=='lightgbm': rounds.append(model.best_iteration_)
        entry={'model':name,'grouped_cv':metrics(train_frame,estimates)}
        if name=='zero_residual': final_model=None
        else:
            final_model=estimator(name)
            if rounds: final_model.set_params(n_estimators=max(20,int(np.median(rounds))))
            final_model=fit(final_model,name,train_frame)
        predictions={}
        for split,rows in [('spatial',spatial),('temporal',temporal)]:
            predictions[split]=np.zeros(len(rows)) if final_model is None else final_model.predict(rows[FEATURES])
            entry[split]=metrics(rows,predictions[split])
            entry[split]['by_label_quality']={quality:metrics(rows[rows.label_quality==quality],predictions[split][rows.label_quality==quality])
                                             for quality in ['gold','silver'] if (rows.label_quality==quality).sum()>=2}
        comparisons.append(entry)
        if name=='lightgbm': point=final_model;point_predictions=predictions
    baseline=comparisons[0]; ml=comparisons[-1]
    improvements={split:1-ml[split]['mae_cf_points']/max(baseline[split]['mae_cf_points'],1e-9) for split in ['spatial','temporal']}
    cv_best=ml['grouped_cv']['mae_cf_points']<=min(c['grouped_cv']['mae_cf_points'] for c in comparisons[1:3])
    passed=all(value>=.05 for value in improvements.values()) and cv_best
    report={'production_eligible':bool(passed),'threshold_pct':5,'improvement_pct':{key:value*100 for key,value in improvements.items()},
            'models':comparisons,'folds':fold_audit,
            'split_design':'Five-fold plant-grouped CV; untouched 2-degree spatial blocks; untouched 2023 temporal test. No random row split.',
            'training_years':[2021,2022],'temporal_test_year':2023,'spatial_blocks':int(blocks.nunique()),
            'lightgbm_beats_sanity_baselines':bool(cv_best),
            'training_plants':sorted(int(x) for x in train_frame.plant_id_eia.unique()),
            'spatial_test_plants':sorted(int(x) for x in spatial.plant_id_eia.unique()),
            'decision':'Passed both independent holdouts and grouped model comparison.' if passed else
            'Ranking corrections disabled: LightGBM must improve MAE by at least 5% on both holdouts and beat Ridge/Random Forest in grouped CV.'}
    (directory/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    if not passed: return report  # hard stop: no production artifact or explanations fabricated
    return export_model(point, train_frame, spatial, temporal, report, directory, stage)


def export_model(point, train_frame, spatial, temporal, report, directory, stage):
    stage('V7','Validating SHAP contributions and exporting the wind model')
    import shap
    explainer=shap.TreeExplainer(point)
    sample=spatial.iloc[:min(100,len(spatial))]
    contributions=np.asarray(explainer.shap_values(sample[FEATURES]))
    bias=float(np.asarray(explainer.expected_value).reshape(-1)[0])
    if not np.allclose(contributions.sum(axis=1)+bias,point.predict(sample[FEATURES]),atol=1e-6):
        raise QualityGateError('SHAP contributions do not sum to the predicted residual.')
    artifacts=directory/'artifacts';artifacts.mkdir(exist_ok=True)
    point.booster_.save_model(str(artifacts/'wind.txt'))
    explanations=[]
    for index,(_,row) in enumerate(sample.iterrows()):
        explanations.append({'plant_id_eia':int(row.plant_id_eia),'report_month':str(row.report_month.date()),
            'base_cf':float(row.resource_expected_capacity_factor),'actual_cf':float(row.actual_capacity_factor),
            'predicted_residual_cf':float(contributions[index].sum()+bias),'bias_cf':bias,
            'factors':[{'feature':name,'contribution_cf':float(value)} for name,value in zip(FEATURES,contributions[index])]})
    (directory/'explanations.json').write_text(json.dumps(explanations,indent=2),encoding='utf-8')
    stage('V9','Checking 80% residual intervals on held-out data')
    interval_models=[]
    for alpha in [.1,.9]:
        parameters={**point.get_params(), 'objective':'quantile', 'alpha':alpha, 'metric':'quantile'}
        model=LGBMRegressor(**parameters)
        fit(model,'quantile',train_frame)
        interval_models.append(model)
    intervals={}
    for split,rows in [('spatial',spatial),('temporal',temporal)]:
        lo=interval_models[0].predict(rows[FEATURES]);hi=interval_models[1].predict(rows[FEATURES])
        intervals[split]={'coverage':float(np.mean((rows.residual_target>=lo)&(rows.residual_target<=hi))),
                          'mean_width_cf_points':float(np.mean(hi-lo)*100),'crossing_fraction':float(np.mean(lo>hi))}
    interval_valid=all(.7<=item['coverage']<=.9 and item['crossing_fraction']==0 for item in intervals.values())
    if interval_valid:
        for model,name in zip(interval_models,['lower','upper']): model.booster_.save_model(str(artifacts/f'{name}.txt'))
    digest=hashlib.sha256((artifacts/'wind.txt').read_bytes()).hexdigest()
    manifest={'schema_version':SCHEMA_VERSION,'feature_order':FEATURES,'baseline_id':BASELINE_ID,
              'technology':'wind','model_version':'wind-'+digest[:12],'model_sha256':digest,
              'trained_at':datetime.now(timezone.utc).isoformat(),'training_years':[2021,2022],
              'production_eligible':True,'benchmark':report,'intervals':intervals,'intervals_available':interval_valid,
              'feature_min':train_frame[FEATURES].min().to_dict(),'feature_max':train_frame[FEATURES].max().to_dict(),
              'minimum_weather_coverage':.7,'maximum_station_km':100,'maximum_interval_width_cf':.25,
              'shap_additivity_max_error':float(np.max(np.abs(contributions.sum(axis=1)+bias-point.predict(sample[FEATURES]))))}
    (artifacts/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False),encoding='utf-8')
    report['intervals']=intervals;report['intervals_available']=interval_valid
    report['model_version']=manifest['model_version'];report['shap_additivity_passed']=True
    (directory/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    return report
