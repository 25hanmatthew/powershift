"""Geographic model selection followed by a single fresh evaluation."""
import hashlib
import json
from datetime import datetime, timezone

import numpy as np
from lightgbm import LGBMRegressor
from sklearn.model_selection import GroupKFold

from .protocol import geographic_blocks, split
from .schema import FEATURES, QualityGateError
from .training import estimator, export_model, metrics


def weights(frame):
    # Multiple allocated generators from one plant must not count as independent sites.
    counts = frame.groupby('plant_id_eia').plant_id_eia.transform('size').to_numpy()
    result = np.where(frame.label_quality.eq('gold'), 1., .5) / counts
    return result / result.mean()


def fit(model, name, frame):
    parameter = 'ridge__sample_weight' if name == 'ridge' else 'sample_weight'
    model.fit(frame[FEATURES], frame.residual_target, **{parameter:weights(frame)})
    return model


def block_mae(frame, prediction):
    errors = np.abs(frame.actual_capacity_factor.to_numpy() -
                    np.clip(frame.resource_expected_capacity_factor.to_numpy() + prediction, 0, 1))
    return float(frame.assign(block=geographic_blocks(frame), error=errors).groupby('block').error.mean().mean() * 100)


def model_for(name, parameters=None):
    if name != 'lightgbm':
        return estimator(name)
    return LGBMRegressor(objective='huber', metric='l1', learning_rate=.025,
                         colsample_bytree=1., random_state=42, n_jobs=4, verbosity=-1,
                         deterministic=True, force_col_wise=True, **parameters)


def bootstrap_improvement(frame, residual):
    """Plant-cluster uncertainty; months and allocated generators are not independent."""
    base = frame.resource_expected_capacity_factor.to_numpy()
    actual = frame.actual_capacity_factor.to_numpy()
    errors = frame[['plant_id_eia']].assign(base=np.abs(actual-base), model=np.abs(actual-np.clip(base+residual,0,1)))
    grouped = errors.groupby('plant_id_eia').agg(base=('base','sum'), model=('model','sum'))
    rng = np.random.default_rng(1847)
    draws = rng.integers(0, len(grouped), size=(1000,len(grouped)))
    baseline = grouped.base.to_numpy()[draws].sum(axis=1)
    corrected = grouped.model.to_numpy()[draws].sum(axis=1)
    values = (1-corrected/np.maximum(baseline,1e-9))*100
    return {'lower_pct':float(np.quantile(values,.025)), 'upper_pct':float(np.quantile(values,.975)),
            'method':'1000 plant-cluster bootstrap draws; 95% interval; descriptive, not the release threshold'}


def train(frame, directory, protocol, stage):
    marker = directory/'evaluation-started.json'
    if marker.exists():
        raise QualityGateError('Fresh tests already evaluated; use the saved result without retuning.')
    train_frame, spatial, temporal = split(frame, protocol)
    groups = geographic_blocks(train_frame)
    folds = list(GroupKFold(n_splits=5).split(train_frame, groups=groups))
    fold_audit = []
    for i,(fit_index,valid_index) in enumerate(folds):
        fit_rows, valid_rows = train_frame.iloc[fit_index], train_frame.iloc[valid_index]
        if set(geographic_blocks(fit_rows)) & set(geographic_blocks(valid_rows)):
            raise QualityGateError('Geographic CV leaked a block.')
        fold_audit.append({'fold':i, 'train_plants':sorted(int(x) for x in fit_rows.plant_id_eia.unique()),
                           'validation_plants':sorted(int(x) for x in valid_rows.plant_id_eia.unique()),
                           'train_blocks':sorted(set(geographic_blocks(fit_rows))),
                           'validation_blocks':sorted(set(geographic_blocks(valid_rows))), 'plant_overlap':0})
    stage('V5', 'Selecting models using geographic development folds; fresh tests remain sealed')
    candidates = [('ridge',None),('random_forest',None)] + [('lightgbm',p) for p in protocol['candidate_grid']]
    results = []
    for index,(name,parameters) in enumerate(candidates):
        prediction = np.zeros(len(train_frame))
        for fit_index,valid_index in folds:
            model = fit(model_for(name,parameters), name, train_frame.iloc[fit_index])
            prediction[valid_index] = model.predict(train_frame.iloc[valid_index][FEATURES])
        results.append({'model':name, 'parameters':parameters, 'grouped_cv':metrics(train_frame,prediction),
                        'macro_block_mae_cf_points':block_mae(train_frame,prediction)})
        print(f'Development candidate {index+1}/{len(candidates)}: {name}; block MAE {results[-1]["macro_block_mae_cf_points"]:.3f}', flush=True)
    selected = min((item for item in results if item['model']=='lightgbm'), key=lambda item:item['macro_block_mae_cf_points'])
    selection = {'selected':selected, 'candidates':results, 'folds':fold_audit,
                 'protocol_sha256':hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest(),
                 'training_data_sha256':hashlib.sha256((directory/'training.parquet').read_bytes()).hexdigest(),
                 'selected_at':datetime.now(timezone.utc).isoformat()}
    (directory/'development-selection.json').write_text(json.dumps(selection,indent=2,allow_nan=False),encoding='utf-8')
    # Final model is frozen and fitted before any new test outcomes are scored.
    final_models = {name:fit(model_for(name, selected['parameters'] if name=='lightgbm' else None), name, train_frame)
                    for name in ['ridge','random_forest','lightgbm']}
    # Retain the trained research model for audit, outside the deployable artifacts directory.
    research_model = directory/'development-wind.txt'
    final_models['lightgbm'].booster_.save_model(str(research_model))
    with marker.open('x',encoding='utf-8') as handle:
        json.dump({'opened_at':datetime.now(timezone.utc).isoformat(),
                   'model_sha256':hashlib.sha256(research_model.read_bytes()).hexdigest(),
                   'selection_sha256':hashlib.sha256((directory/'development-selection.json').read_bytes()).hexdigest()},handle,indent=2)
    stage('V6', 'Evaluating the frozen model once on previously unexamined plants')
    comparisons = []
    predictions = {}
    for name in ['zero_residual','ridge','random_forest','lightgbm']:
        cv = metrics(train_frame,np.zeros(len(train_frame))) if name=='zero_residual' else next(x['grouped_cv'] for x in ([selected] if name=='lightgbm' else results) if x['model']==name)
        entry = {'model':name, 'grouped_cv':cv}
        for test,rows in [('spatial',spatial),('temporal',temporal)]:
            prediction = np.zeros(len(rows)) if name=='zero_residual' else final_models[name].predict(rows[FEATURES])
            if name=='lightgbm': predictions[test]=prediction
            entry[test] = metrics(rows,prediction)
            entry[test]['by_label_quality'] = {quality:metrics(rows[rows.label_quality==quality],prediction[rows.label_quality==quality])
                                              for quality in ['gold','silver'] if (rows.label_quality==quality).sum()>=2}
            entry[test]['by_state'] = {state:metrics(rows[rows.state==state],prediction[rows.state==state])
                                      for state in sorted(rows.state.unique()) if (rows.state==state).sum()>=2}
        comparisons.append(entry)
    baseline,ml = comparisons[0],comparisons[-1]
    improvements = {test:(1-ml[test]['mae_cf_points']/max(baseline[test]['mae_cf_points'],1e-9))*100 for test in ['spatial','temporal']}
    cv_best = ml['grouped_cv']['mae_cf_points']<=min(c['grouped_cv']['mae_cf_points'] for c in comparisons[1:3])
    passed = all(value>=5 for value in improvements.values()) and cv_best
    decision = (f"Fresh spatial MAE improved {improvements['spatial']:.1f}%; fresh 2023 temporal MAE improved {improvements['temporal']:.1f}%. " +
                ('The model passed both 5% holdout gates and the baseline comparison.' if passed else
                 'The release gate did not pass; ranking corrections remain disabled. No further tuning on these tests.'))
    report = {'production_eligible':bool(passed), 'threshold_pct':5, 'improvement_pct':improvements,
              'models':comparisons, 'folds':fold_audit, 'protocol_version':protocol['version'],
              'split_design':'Five-fold 2-degree geographic-block CV. Final spatial blocks excluded from all fitting; final spatial and 2023 temporal tests contain only plants absent from the first experiment. Fixed before tuning; evaluated once.',
              'training_years':[2021,2022], 'temporal_test_year':2023,
              'spatial_blocks':int(geographic_blocks(frame).nunique()),
              'lightgbm_beats_sanity_baselines':bool(cv_best), 'selected_parameters':selected['parameters'],
              'training_plants':sorted(int(x) for x in train_frame.plant_id_eia.unique()),
              'spatial_test_plants':sorted(int(x) for x in spatial.plant_id_eia.unique()),
              'temporal_test_plants':sorted(int(x) for x in temporal.plant_id_eia.unique()),
              'previously_examined_plants':protocol['previously_examined_plants'],
              'improvement_intervals':{name:bootstrap_improvement(rows,predictions[name]) for name,rows in [('spatial',spatial),('temporal',temporal)]},
              'decision':decision}
    (directory/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    if not passed:
        return report
    return export_model(final_models['lightgbm'], train_frame, spatial, temporal, report, directory, stage)
