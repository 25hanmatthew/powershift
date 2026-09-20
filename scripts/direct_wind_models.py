"""Separate development experiment: direct observed capacity factor versus residual fitting."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from backend.ml.schema import ATLAS_FEATURES, QualityGateError
from backend.ml.retraining import weights, block_mae
from backend.ml.training import metrics
from scripts.station_normalized_wind import ROOT, NORMALIZED
from scripts.train_isd_pudl_wind import ISD_FEATURES, write, digest
from scripts.selective_isd_models import out_of_block

SPECS = [dict(method='hist', loss=loss, leaves=leaves) for loss in ['squared_error', 'absolute_error'] for leaves in [7, 15]]
SPECS += [dict(method='lightgbm', loss=loss, leaves=leaves) for loss in ['regression', 'huber', 'regression_l1'] for leaves in [7, 15]]
SPECS += [dict(method='extra', minimum=minimum) for minimum in [10, 30]]


class DirectWind:
    def __init__(self, spec, without_isd=False):
        self.spec, self.without_isd = spec, without_isd

    def fit(self, frame):
        self.columns = ATLAS_FEATURES + ([] if self.without_isd else NORMALIZED + ISD_FEATURES)
        s = self.spec
        if s['method'] == 'hist':
            self.model = HistGradientBoostingRegressor(loss=s['loss'], max_iter=250, learning_rate=.04,
                max_leaf_nodes=s['leaves'], min_samples_leaf=80, l2_regularization=10, early_stopping=False, random_state=42)
        elif s['method'] == 'lightgbm':
            self.model = LGBMRegressor(objective=s['loss'], alpha=.1, n_estimators=350, learning_rate=.035,
                num_leaves=s['leaves'], min_child_samples=100, reg_lambda=10, n_jobs=3,
                verbosity=-1, random_state=42, deterministic=True, force_col_wise=True)
        else:
            self.model = ExtraTreesRegressor(n_estimators=240, min_samples_leaf=s['minimum'], max_features=.8,
                                            n_jobs=3, random_state=42)
        with threadpool_limits(limits=1):
            self.model.fit(frame[self.columns], frame.actual_capacity_factor, sample_weight=weights(frame))
        return self

    def predict(self, frame):
        with threadpool_limits(limits=1):
            # All experiments expose residuals so common metrics and clipping stay identical.
            return self.model.predict(frame[self.columns])-frame.resource_expected_capacity_factor.to_numpy()


def search():
    root = ROOT/'direct-development'; root.mkdir(exist_ok=True)
    source = ROOT/'training.parquet'
    frame = pd.read_parquet(source)
    plan = {'specifications': SPECS, 'data_sha256': digest(source),
            'purpose': 'Adaptive geographic development only; direct PUDL observed output is the target. No final test used.',
            'selection': 'Minimum macro-block MAE among candidates better than the paired Atlas model on row and block MAE. Require a matched no-ISD ablation improvement.'}
    if (root/'plan.json').exists() and json.loads((root/'plan.json').read_text()) != plan:
        raise QualityGateError('Direct model plan changed.')
    write(root/'plan.json', plan)
    base = np.load(ROOT/'baseline.npz')['prediction']
    baseline = {**metrics(frame, base), 'block_mae': block_mae(frame, base)}
    results = []
    for i, spec in enumerate(SPECS):
        target = root/f'candidate-{i:02d}.npz'
        if target.exists():
            prediction = np.load(target)['prediction']
        else:
            prediction = out_of_block(frame, lambda: DirectWind(spec))
            np.savez_compressed(target, prediction=prediction)
        for fraction in [.25, .5, 1.]:
            blended = (1-fraction)*base + fraction*prediction
            results.append({'index': i, 'spec': spec, 'blend': fraction, **metrics(frame, blended), 'block_mae': block_mae(frame, blended)})
        write(root/'progress.json', {'completed': i+1, 'total': len(SPECS), 'results': results})
        print(f'Direct target {i+1}/{len(SPECS)}: '+json.dumps(results[-1]), flush=True)
    eligible = sorted([r for r in results if r['mae_cf_points'] < baseline['mae_cf_points'] and r['block_mae'] < baseline['block_mae']], key=lambda r:r['block_mae'])
    selected, ablations = None, []
    for entry in eligible[:3]:
        prediction = out_of_block(frame, lambda: DirectWind(entry['spec'], without_isd=True))
        prediction = (1-entry['blend'])*base+entry['blend']*prediction
        score = {**metrics(frame, prediction), 'block_mae': block_mae(frame, prediction)}
        ablations.append({'candidate': entry, 'without_isd': score})
        if selected is None and entry['mae_cf_points'] < score['mae_cf_points'] and entry['block_mae'] < score['block_mae']:
            selected = entry
    write(root/'selection.json', {'baseline': baseline, 'ranking': sorted(results, key=lambda r:r['block_mae']), 'ablations': ablations, 'selected': selected, 'production_eligible': False})
    print('Direct-target selection: '+json.dumps(selected), flush=True)


if __name__ == '__main__':
    search()
