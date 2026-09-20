"""Select physical wind residual models without exposing the final test outcomes."""
import hashlib
import json
from datetime import datetime, timezone

import numpy as np
from lightgbm import LGBMRegressor
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge,HuberRegressor
from threadpoolctl import threadpool_limits

from .plant_data import FEATURES, WIND_FEATURES, SCHEMA_VERSION
from .schema import BASELINE_ID, PHYSICAL_FEATURES as PHYSICAL, QualityGateError
from .protocol import split, geographic_blocks
from .retraining import weights, block_mae, bootstrap_improvement
from .training import estimator, metrics



def features_for(parameters,feature_sets=None):
    feature_sets=feature_sets or {'physical':PHYSICAL,'combined':FEATURES}
    return feature_sets['physical' if parameters and parameters.get('feature_set')=='physical' else 'combined']


def make_model(name, parameters):
    if name=='ridge' and parameters:
        return make_pipeline(StandardScaler(),Ridge(alpha=parameters['alpha']))
    if name=='huber':
        return make_pipeline(StandardScaler(),HuberRegressor(alpha=parameters['alpha'],epsilon=parameters['epsilon'],max_iter=2000,tol=1e-6))
    if name!='lightgbm': return estimator(name)
    parameters={key:value for key,value in parameters.items() if key!='feature_set'}
    return LGBMRegressor(objective='huber',metric='l1',learning_rate=.025,colsample_bytree=1.,
                         n_jobs=4,verbosity=-1,random_state=42,deterministic=True,force_col_wise=True,**parameters)


def fit(model,name,frame,features):
    weight_key={'ridge':'ridge__sample_weight','huber':'huberregressor__sample_weight'}.get(name,'sample_weight')
    with threadpool_limits(limits=1,user_api='blas'):
        model.fit(frame[features],frame.residual_target,**{weight_key:weights(frame)})
    return model


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def uncertainty(frame,prediction,test):
    clustered=frame.assign(plant_id_eia=geographic_blocks(frame)) if test=='spatial' else frame
    result=bootstrap_improvement(clustered,prediction)
    result['method']='1000 geographic-block bootstrap draws; 95% descriptive interval' if test=='spatial' else result['method']
    return result


def train(frame,directory,protocol,stage,development_only=False,feature_sets=None):
    marker=directory/'evaluation-started.json'
    if marker.exists(): raise QualityGateError('Final plant tests already evaluated; do not retune or overwrite.')
    training,spatial,temporal=split(frame,protocol)
    folds=list(GroupKFold(n_splits=5).split(training,groups=geographic_blocks(training)))
    audit=[]
    for index,(fit_index,valid_index) in enumerate(folds):
        a,b=training.iloc[fit_index],training.iloc[valid_index]
        if set(geographic_blocks(a)) & set(geographic_blocks(b)): raise QualityGateError('Geographic fold overlap.')
        audit.append({'fold':index,'train_plants':sorted(int(x) for x in a.plant_id_eia.unique()),
                      'validation_plants':sorted(int(x) for x in b.plant_id_eia.unique()),
                      'train_blocks':sorted(set(geographic_blocks(a))),'validation_blocks':sorted(set(geographic_blocks(b))),'plant_overlap':0})
    stage('V5','Comparing reported-output models in geographic folds; fresh tests unopened')
    candidates=[('ridge',None),('random_forest',None)]+[('lightgbm',p) for p in protocol['candidate_grid']]
    candidates += [('ridge',{'alpha':alpha,'feature_set':features}) for alpha in [.1,10,100,1000] for features in ['physical','combined']]
    candidates += [('huber',{'alpha':alpha,'epsilon':epsilon,'feature_set':features})
                   for alpha in [.001,1] for epsilon in [1.1,1.35,1.75] for features in ['physical','combined']]
    search={'candidates':candidates,'selection':'Minimum geographic-block development MAE; final thresholds unchanged.',
            'reason':'Expanded to robust and regularized linear models after development-only comparison; final tests remain unopened.'}
    search_path=directory/'search-plan.json'
    if search_path.exists() and json.loads(search_path.read_text(encoding='utf-8'))!=json.loads(json.dumps(search)):
        raise QualityGateError('The saved development search plan changed.')
    write_json(search_path,search)
    results=[];oof={}
    for index,(name,parameters) in enumerate(candidates):
        features=features_for(parameters,feature_sets)
        prediction=np.zeros(len(training))
        for fit_index,valid_index in folds:
            model=fit(make_model(name,parameters),name,training.iloc[fit_index],features)
            prediction[valid_index]=model.predict(training.iloc[valid_index][features])
        result={'model':name,'parameters':parameters,'features':features,'grouped_cv':metrics(training,prediction),
                'macro_block_mae_cf_points':block_mae(training,prediction),'candidate_index':index}
        results.append(result);oof[index]=prediction
        write_json(directory/'development-progress.json',{'completed':len(results),'total':len(candidates),'candidates':results})
        print(f'Plant candidate {index+1}/{len(candidates)}: {name}; geographic MAE {result["macro_block_mae_cf_points"]:.3f}',flush=True)
    selected=min((r for r in results if r['model']!='random_forest'),key=lambda r:r['macro_block_mae_cf_points'])
    selection={'selected':selected,'candidates':results,'folds':audit,'selected_at':datetime.now(timezone.utc).isoformat(),
               'protocol_sha256':hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest(),
               'search_plan_sha256':hashlib.sha256(search_path.read_bytes()).hexdigest(),
               'training_data_sha256':hashlib.sha256((directory/'training.parquet').read_bytes()).hexdigest()}
    write_json(directory/'development-selection.json',selection)
    if development_only:
        return {'production_eligible':False,'development_only':True,'selection':selected,
                'decision':'Development comparison complete. Fresh final tests remain unopened.'}
    best_tree=min((r for r in results if r['model']=='lightgbm'),key=lambda r:r['macro_block_mae_cf_points'])
    specifications={'ridge':results[0],'random_forest':results[1],'lightgbm':best_tree,'selected':selected}
    final={key:fit(make_model(spec['model'],spec['parameters']),spec['model'],training,spec['features']) for key,spec in specifications.items()}
    research=directory/('development-wind.txt' if selected['model']=='lightgbm' else 'development-wind.json')
    if selected['model']=='lightgbm': final['selected'].booster_.save_model(str(research))
    else:
        from .linear_model import payload_from_pipeline
        write_json(research,payload_from_pipeline(final['selected'],training,selected['features']))
    with marker.open('x',encoding='utf-8') as handle:
        json.dump({'opened_at':datetime.now(timezone.utc).isoformat(),'model_sha256':hashlib.sha256(research.read_bytes()).hexdigest(),
                   'selection_sha256':hashlib.sha256((directory/'development-selection.json').read_bytes()).hexdigest()},handle,indent=2)
    stage('V6','Evaluating the frozen plant model on fresh geographic and later-year tests')
    comparisons=[];point_predictions={}
    predictions=[]
    for name in ['zero_residual','ridge','random_forest','lightgbm','selected']:
        prediction=np.zeros(len(training)) if name=='zero_residual' else oof[specifications[name]['candidate_index']]
        entry={'model':name,'model_name':selected['model'] if name=='selected' else name,'grouped_cv':metrics(training,prediction)}
        entry['grouped_cv']['by_state']={state:metrics(training[training.state==state],prediction[training.state==state]) for state in sorted(training.state.unique())}
        for test,rows in [('spatial',spatial),('temporal',temporal)]:
            features=specifications[name]['features'] if name!='zero_residual' else FEATURES
            values=np.zeros(len(rows)) if name=='zero_residual' else final[name].predict(rows[features])
            entry[test]=metrics(rows,values)
            entry[test]['by_state']={state:metrics(rows[rows.state==state],values[rows.state==state]) for state in sorted(rows.state.unique())}
            entry[test]['by_label_quality']={'gold':metrics(rows,values)}
            if name=='selected':
                point_predictions[test]=values
                predictions.append(rows[['plant_id_eia','report_month','state','resource_expected_capacity_factor','actual_capacity_factor']].assign(test=test,predicted_residual_cf=values))
        comparisons.append(entry)
    import pandas as pd
    pd.concat(predictions,ignore_index=True).to_parquet(directory/'test-predictions.parquet',index=False)
    base,ml=comparisons[0],comparisons[-1]
    improvements={test:100*(1-ml[test]['mae_cf_points']/base[test]['mae_cf_points']) for test in ['spatial','temporal']}
    cv_best=ml['grouped_cv']['mae_cf_points']<=min(m['grouped_cv']['mae_cf_points'] for m in comparisons[1:3])
    passed=all(v>=5 for v in improvements.values()) and cv_best
    report={'production_eligible':bool(passed),'threshold_pct':5,'improvement_pct':improvements,
            'models':comparisons,'folds':audit,'protocol_version':protocol['version'],'revision':protocol.get('revision',3),
            'schema_version':protocol.get('schema_version',SCHEMA_VERSION),
            'selected_model':selected['model'],'selected_parameters':selected['parameters'],'feature_order':selected['features'],
            'label_unit':'reported wind-only plant-month','training_years':protocol['training_years'],'temporal_test_year':2023,
            'split_design':f"Five-fold 2-degree geographic-block development CV. Fresh final spatial blocks excluded from training; 2023 temporal test uses previously unexamined plants. All {len(protocol['previously_examined_plants'])} earlier plants excluded from final tests. Model fixed before opening tests once.",
            'selected_beats_sanity_baselines':bool(cv_best),
            'training_plants':sorted(int(x) for x in training.plant_id_eia.unique()),
            'spatial_test_plants':sorted(int(x) for x in spatial.plant_id_eia.unique()),
            'temporal_test_plants':sorted(int(x) for x in temporal.plant_id_eia.unique()),
            'previously_examined_plants':protocol['previously_examined_plants'],
            'improvement_intervals':{test:uncertainty(rows,point_predictions[test],test) for test,rows in [('spatial',spatial),('temporal',temporal)]},
            'decision':f"Reported-output model: spatial MAE improved {improvements['spatial']:.1f}%; later-year MAE improved {improvements['temporal']:.1f}%. "+
                       ('Both 5% holdout gates and the development baseline comparison passed.' if passed else 'The release gate did not pass; corrections remain disabled.')}
    write_json(directory/'evaluation.json',report)
    if not passed: return report
    return export(final['selected'],training,spatial,temporal,selected['features'],report,directory,stage,oof[selected['candidate_index']])


def export(point,training,spatial,temporal,features,report,directory,stage,oof):
    stage('V7','Checking model explanations and preparing the validated artifact')
    import shap
    linear=report['selected_model'] in ['ridge','huber']
    artifacts=directory/'artifacts';artifacts.mkdir(exist_ok=True)
    if linear:
        from .linear_model import LinearModel,LinearExplanation,payload_from_pipeline
        payload=payload_from_pipeline(point,training,features)
        write_json(artifacts/'wind.json',payload)
        portable=LinearModel(payload)
        if not np.allclose(portable.predict(training[features]),point.predict(training[features]),atol=1e-12):
            raise QualityGateError('Portable linear predictions do not match training implementation.')
        point=portable
        explainer=LinearExplanation(point)
        official=shap.LinearExplainer((point.coefficients,point.intercept),(point.background,training[features].cov().to_numpy()))
        if not np.allclose(official.shap_values(spatial.iloc[:100][features]),explainer.shap_values(spatial.iloc[:100][features]),atol=1e-10):
            raise QualityGateError('Linear SHAP does not match the reference implementation.')
    else:
        point.booster_.save_model(str(artifacts/'wind.txt'))
        explainer=shap.TreeExplainer(point)
    sample=spatial.iloc[:100]
    values=np.asarray(explainer.shap_values(sample[features]))
    bias=float(np.asarray(explainer.expected_value).reshape(-1)[0])
    error=float(np.max(np.abs(values.sum(axis=1)+bias-point.predict(sample[features]))))
    if error>1e-6: raise QualityGateError('SHAP contributions do not reconcile with point predictions.')
    explanations=[{'plant_id_eia':int(row.plant_id_eia),'report_month':str(row.report_month.date()),
                  'base_cf':float(row.resource_expected_capacity_factor),'actual_cf':float(row.actual_capacity_factor),
                  'predicted_residual_cf':float(values[i].sum()+bias),'bias_cf':bias,
                  'factors':[{'feature':name,'contribution_cf':float(value)} for name,value in zip(features,values[i])]}
                  for i,(_,row) in enumerate(sample.iterrows())]
    write_json(directory/'explanations.json',explanations)
    stage('V9','Checking optional 80% prediction intervals')
    quantiles=[];offsets=None
    if linear:
        offsets=np.quantile(training.residual_target.to_numpy()-oof,[.1,.9])
    else:
        for alpha in [.1,.9]:
            model=LGBMRegressor(**{**point.get_params(),'objective':'quantile','alpha':alpha,'metric':'quantile'})
            quantiles.append(fit(model,'quantile',training,features))
    intervals={}
    for test,rows in [('spatial',spatial),('temporal',temporal)]:
        lo,hi=([point.predict(rows[features])+offset for offset in offsets] if linear else [model.predict(rows[features]) for model in quantiles])
        intervals[test]={'coverage':float(np.mean((rows.residual_target>=lo)&(rows.residual_target<=hi))),
                         'mean_width_cf_points':float(np.mean(hi-lo)*100),'crossing_fraction':float(np.mean(lo>hi))}
    valid=all(.7<=item['coverage']<=.9 and item['crossing_fraction']==0 for item in intervals.values())
    if valid:
        if linear: write_json(artifacts/'intervals.json',{'offsets':offsets.tolist(),'method':'10th/90th percentiles of geographic out-of-fold residual errors'})
        else:
            for name,model in zip(['lower','upper'],quantiles): model.booster_.save_model(str(artifacts/f'{name}.txt'))
    digest=hashlib.sha256((artifacts/('wind.json' if linear else 'wind.txt')).read_bytes()).hexdigest()
    report.update(intervals=intervals,intervals_available=valid,model_version='wind-plant-'+digest[:12],shap_additivity_passed=True)
    manifest={'schema_version':report['schema_version'],'feature_order':features,'baseline_id':BASELINE_ID,'technology':'wind',
              'model_format':'linear_json' if linear else 'lightgbm_text','model_family':report['selected_model'],
              'model_version':report['model_version'],'model_sha256':digest,'trained_at':datetime.now(timezone.utc).isoformat(),
              'training_years':report['training_years'],'production_eligible':True,'benchmark':report,
              'intervals':intervals,'intervals_available':valid,'feature_min':training[features].min().to_dict(),
              'feature_max':training[features].max().to_dict(),'minimum_weather_coverage':.7,'maximum_station_km':100,
              'maximum_interval_width_cf':.25,'shap_additivity_max_error':error,'sample_unit':'plant-month'}
    manifest['validation_scope']={'states':sorted(set(spatial.state)&set(temporal.state)),
                                  'years':sorted(training.report_month.dt.year.unique().tolist()+[report['temporal_test_year']]),
                                  'note':'Fresh geographic evidence is limited to these states; matching monthly RARE and weather inputs are required.'}
    write_json(artifacts/'manifest.json',manifest);write_json(directory/'evaluation.json',report)
    return report
