"""Frozen, one-shot comparison on new Midwest plants; never promotes live scoring."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from backend.ml.linear_model import LinearModel, payload_from_pipeline
from backend.ml.protocol import split, geographic_blocks
from backend.ml.retraining import weights, block_mae
from backend.ml.schema import ATLAS_FEATURES, QualityGateError
from backend.ml.training import metrics
from scripts.fusion_wind_models import Fusion
from scripts.train_isd_pudl_wind import add_features, write, digest, folds

ROOT = Path('data/private/ml-v6-fusion')


class PortableSpline:
    """Numeric-only degree-two spline model with linear boundary extrapolation."""
    def __init__(self, payload):
        self.payload = payload
        if payload['degree'] != 2 or payload['extrapolation'] != 'linear':
            raise ValueError('Unsupported spline schema.')
        self.features = payload['feature_order']
        self.splines = []
        for entry in payload['splines']:
            knots, basis = np.asarray(entry['knots'], dtype=float), np.asarray(entry['basis'], dtype=float)
            if knots.ndim != 1 or basis.ndim != 2 or len(knots) != len(basis)+3 or len(knots) < 6:
                raise ValueError('Invalid spline knot dimensions.')
            if not np.isfinite(knots).all() or not np.isfinite(basis).all() or np.any(np.diff(knots) < 0):
                raise ValueError('Invalid spline knots or basis.')
            # sklearn also retains constant input columns with repeated knots.
            # Its fitted spline uses construct_fast for this valid zero-basis case.
            self.splines.append(BSpline.construct_fast(knots, basis, 2, extrapolate=False))
        self.coefficients = np.asarray(payload['coefficients'], dtype=float)
        self.intercept = float(payload['intercept'])
        dimension = sum(s.c.shape[1]-1 for s in self.splines)
        if len(self.splines) != len(self.features) or self.coefficients.shape != (dimension,) or not np.isfinite(self.coefficients).all() or not np.isfinite(self.intercept):
            raise ValueError('Invalid spline dimensions or parameters.')

    def predict(self, frame):
        x = frame[self.features].to_numpy(dtype=float)
        if not np.isfinite(x).all():
            raise ValueError('Nonfinite spline inputs.')
        blocks = []
        for index, spline in enumerate(self.splines):
            bounded = np.clip(x[:, index], spline.t[2], spline.t[-3])
            values = spline(bounded) + spline(bounded, nu=1)*(x[:, index]-bounded)[:, None]
            blocks.append(values[:, :-1])
        return np.concatenate(blocks, axis=1)@self.coefficients + self.intercept


def spline_payload(model):
    spline, scaler, regression = [step[1] for step in model.model.steps]
    coefficients = regression.coef_/scaler.scale_
    return {'feature_order': model.features, 'degree': 2, 'extrapolation': 'linear',
            'splines': [{'knots': s.t.tolist(), 'basis': s.c.tolist()} for s in spline.bsplines_],
            'coefficients': coefficients.tolist(), 'intercept': float(regression.intercept_-scaler.mean_@coefficients)}


def freeze():
    selection = ROOT/'selective-development/selection.json'
    selected = json.loads(selection.read_text())['selected']
    if selected is None or selected['family'] != 'fusion' or selected['spec']['method'] != 'spline':
        raise QualityGateError('No eligible spline fusion candidate selected in development.')
    source_files = [Path(__file__), Path('scripts/fusion_wind_models.py'), Path('scripts/selective_isd_models.py'),
                    Path('scripts/prepare_fusion_wind.py'), selection, ROOT/'split-protocol.json']
    plan = {'selected': selected, 'script_and_selection_sha256': {p.name: digest(p) for p in source_files},
            'gate': '5% row MAE improvement over the resource baseline on BOTH fresh tests; better row MAE than the identically retrained Atlas model AND the identical no-ISD ensemble on BOTH tests; development row MAE no worse than Ridge and Random Forest.',
            'note': 'A research validation gate only. Live site inference remains off until matching candidate inputs and geographic/time compatibility are implemented.',
            'intervals': 'No calibrated prediction interval claim. Comparative cluster bootstrap intervals are descriptive.',
            'old_model_sha256': digest(Path('data/private/ml-v4/artifacts/wind.json'))}
    path = ROOT/'final-plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise QualityGateError('Final comparison changed after freezing. Do not reopen the tests.')
    if not path.exists():
        write(path, plan)
    return plan


def score(frame, prediction):
    return {**metrics(frame, prediction), 'macro_block_mae_cf_points': block_mae(frame, prediction)}


def comparative_interval(frame, candidate, reference, spatial):
    base = frame.resource_expected_capacity_factor.to_numpy()
    actual = frame.actual_capacity_factor.to_numpy()
    group = geographic_blocks(frame) if spatial else frame.plant_id_eia
    errors = pd.DataFrame({'group': group.to_numpy(), 'candidate': np.abs(actual-np.clip(base+candidate, 0, 1)),
                           'reference': np.abs(actual-np.clip(base+reference, 0, 1))}).groupby('group').sum()
    draws = np.random.default_rng(1847).integers(0, len(errors), size=(2000, len(errors)))
    improvements = 100*(1-errors.candidate.to_numpy()[draws].sum(axis=1)/np.maximum(errors.reference.to_numpy()[draws].sum(axis=1), 1e-12))
    return {'lower_pct': float(np.quantile(improvements, .025)), 'upper_pct': float(np.quantile(improvements, .975)),
            'independent_clusters': len(errors), 'method': '2000 geographic-block draws' if spatial else '2000 plant-cluster draws',
            'caution': 'Descriptive only; very few spatial blocks cannot establish broad geographic reliability.' if spatial else 'Descriptive only.'}


def run():
    plan = freeze()
    if (ROOT/'evaluation-started.json').exists():
        raise QualityGateError('Fresh evaluation already started; preserve its saved result.')
    frame = add_features(pd.read_parquet(ROOT/'complete.parquet'))
    design = json.loads((ROOT/'split-protocol.json').read_text())
    train, spatial, temporal = split(frame, design)
    selected = plan['selected']; spec = selected['spec']; fraction = selected['blend']
    required = list(dict.fromkeys(ATLAS_FEATURES + Fusion(spec).columns()))
    for part in [train, spatial, temporal]:
        if part.duplicated(['plant_id_eia', 'report_month']).any() or not np.isfinite(part[required]).all(axis=None):
            raise QualityGateError('Duplicate observations or nonfinite features.')
        if not (part.isd_coverage_fraction.ge(.7)&part.isd_nearest_station_km.le(100)).all():
            raise QualityGateError('ISD coverage or station distance gate failed.')
    names = ['resource_only', 'atlas_retrained', 'fusion', 'without_isd', 'ridge', 'random_forest']
    cv = {name: np.zeros(len(train)) for name in names}
    for i, (a, b) in enumerate(folds(train, 5)):
        fit, valid = train.iloc[a], train.iloc[b]
        base = Fusion({'method': 'baseline', 'features': 'atlas'}).fit(fit).predict(valid)
        cv['atlas_retrained'][b] = base
        cv['fusion'][b] = (1-fraction)*base + fraction*Fusion(spec).fit(fit).predict(valid)
        cv['without_isd'][b] = (1-fraction)*base + fraction*Fusion(spec, without_isd=True).fit(fit).predict(valid)
        with threadpool_limits(limits=1):
            ridge = make_pipeline(StandardScaler(), Ridge(alpha=10))
            ridge.fit(fit[ATLAS_FEATURES], fit.residual_target, ridge__sample_weight=weights(fit))
            forest = RandomForestRegressor(n_estimators=160, min_samples_leaf=12, max_features=.8, n_jobs=3, random_state=42)
            forest.fit(fit[ATLAS_FEATURES], fit.residual_target, sample_weight=weights(fit))
            cv['ridge'][b] = ridge.predict(valid[ATLAS_FEATURES])
            cv['random_forest'][b] = forest.predict(valid[ATLAS_FEATURES])
        print(f'Frozen candidate / expanded development fold {i+1}/5 completed', flush=True)
    development = {name: score(train, prediction) for name, prediction in cv.items()}
    write(ROOT/'expanded-development.json', development)
    base = Fusion({'method': 'baseline', 'features': 'atlas'}).fit(train)
    full = Fusion(spec).fit(train)
    ablated = Fusion(spec, without_isd=True).fit(train)
    payload = {'version': 'wind-fusion-research-v6', 'fraction': fraction,
               'atlas': payload_from_pipeline(base.model, train, base.features),
               'isd_spline': spline_payload(full), 'no_isd_spline': spline_payload(ablated),
               'production_eligible': False, 'feature_sources': {'labels': 'PUDL EIA-923 / EIA-860', 'station_weather': 'NOAA ISD'}}
    portable_base = LinearModel(payload['atlas'])
    portable_full, portable_ablated = PortableSpline(payload['isd_spline']), PortableSpline(payload['no_isd_spline'])
    for fitted, portable in [(full, portable_full), (ablated, portable_ablated)]:
        if not np.allclose(fitted.predict(train), portable.predict(train), atol=1e-10, rtol=1e-10):
            raise QualityGateError('Numeric spline artifact parity failed.')
    write(ROOT/'research-fusion.json', payload)
    # All configuration and all fitted numeric coefficients are fixed before scoring either final test.
    write(ROOT/'evaluation-started.json', {'started_at': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': digest(ROOT/'final-plan.json'), 'model_sha256': digest(ROOT/'research-fusion.json'),
          'data_sha256': digest(ROOT/'complete.parquet'), 'development_sha256': digest(ROOT/'expanded-development.json')})
    old = LinearModel(json.loads(Path('data/private/ml-v4/artifacts/wind.json').read_text()))
    tests, intervals, improvements = {}, {}, {}
    for name, part in [('spatial', spatial), ('temporal', temporal)]:
        baseline = portable_base.predict(part[portable_base.features])
        estimates = {'resource_only': np.zeros(len(part)), 'atlas_retrained': baseline,
                     'fusion': (1-fraction)*baseline+fraction*portable_full.predict(part),
                     'without_isd': (1-fraction)*baseline+fraction*portable_ablated.predict(part),
                     'previous_v4_artifact': old.predict(part[old.features])}
        tests[name] = {model: score(part, prediction) for model, prediction in estimates.items()}
        improvements[name] = {ref: 100*(1-tests[name]['fusion']['mae_cf_points']/tests[name][ref]['mae_cf_points']) for ref in estimates if ref != 'fusion'}
        intervals[name] = {ref: comparative_interval(part, estimates['fusion'], estimates[ref], name == 'spatial') for ref in improvements[name]}
        output = part[['plant_id_eia', 'report_month', 'state', 'latitude', 'longitude', 'actual_capacity_factor', 'resource_expected_capacity_factor']].copy()
        for model, prediction in estimates.items():
            output[model+'_residual'] = prediction
        output.to_parquet(ROOT/f'{name}-predictions.parquet', index=False)
        print(f'Fresh {name}: '+json.dumps(improvements[name]), flush=True)
    gates = {'resource_improvement': all(v['resource_only'] >= 5 for v in improvements.values()),
             'beats_retrained_atlas': all(v['atlas_retrained'] > 0 for v in improvements.values()),
             'isd_ablation_improves': all(v['without_isd'] > 0 for v in improvements.values()),
             'development_baselines': all(development['fusion']['mae_cf_points'] <= development[n]['mae_cf_points'] for n in ['ridge', 'random_forest'])}
    evaluation = {'revision': 6, 'research_validation_passed': all(gates.values()), 'production_eligible': False,
                  'live_corrections_enabled': False, 'gates': gates, 'selected': selected, 'development': development,
                  'fresh_tests': tests, 'improvement_pct': improvements, 'comparative_intervals': intervals,
                  'data': {'training_rows': len(train), 'training_plants': int(train.plant_id_eia.nunique()), 'training_blocks': int(geographic_blocks(train).nunique()),
                           'training_states': sorted(train.state.unique().tolist()), 'training_years': design['training_years'],
                           'spatial_plants': int(spatial.plant_id_eia.nunique()), 'spatial_blocks': int(geographic_blocks(spatial).nunique()),
                           'temporal_plants': int(temporal.plant_id_eia.nunique()), 'temporal_year': 2023},
                  'limitations': ['Wind-only operational plant-month output; not solar, hydro, geothermal, or urban rooftop suitability.',
                                  'Small fresh spatial sample. No claim of coverage for every city.',
                                  'Same-county RARE baseline required; live ERA5 screening estimates cannot substitute.',
                                  'No prediction interval calibrated or exposed. No live promotion from this research script.']}
    write(ROOT/'evaluation.json', evaluation)
    print('Gates: '+json.dumps(gates), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--freeze', action='store_true')
    args = parser.parse_args()
    if args.freeze:
        freeze()
        print('Final model and comparison rules frozen; fresh tests remain unopened.')
    else:
        run()
