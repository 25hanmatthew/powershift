"""Development-only search for selective station observations and robust fusion."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler, SplineTransformer
from threadpoolctl import threadpool_limits

from backend.ml.schema import ATLAS_FEATURES, QualityGateError
from backend.ml.retraining import weights, block_mae
from backend.ml.training import metrics
from scripts.fusion_wind_models import Fusion, CORE
from scripts.train_isd_pudl_wind import folds, write, digest

GROUPS = {
    'temperature': ['isd_mean_temp_c'],
    'mean': ['isd_mean_wind_mps'],
    'variability': ['isd_wind_std_mps'],
    'tails': ['isd_wind_p10_mps', 'isd_wind_p90_mps'],
    'shape': ['isd_relative_std', 'isd_relative_p10', 'isd_relative_p90'],
    'quality_gaps': ['isd_weighted_wind_gap', 'isd_weighted_temp_gap'],
    'shape_temperature': ['isd_relative_std', 'isd_relative_p10', 'isd_relative_p90', 'isd_mean_temp_c'],
}
SPECS = [dict(method='huber', group=g, alpha=a) for g in GROUPS for a in [1, 100]]
SPECS += [dict(method='spline', group=g, alpha=10) for g in ['temperature', 'shape', 'shape_temperature', 'quality_gaps']]


def features(frame):
    """Same-row weather transforms; no target or test-batch statistics."""
    result = frame.copy()
    denominator = np.maximum(result.isd_mean_wind_mps, .5)
    result['isd_relative_std'] = result.isd_wind_std_mps / denominator
    result['isd_relative_p10'] = result.isd_wind_p10_mps / denominator
    result['isd_relative_p90'] = result.isd_wind_p90_mps / denominator
    reliability = result.isd_coverage_fraction * np.exp(-result.isd_nearest_station_km / 30)
    result['isd_weighted_wind_gap'] = reliability * (result.isd_mean_wind_mps-result.era5_wind10_mean_mps)
    result['isd_weighted_temp_gap'] = reliability * (result.isd_mean_temp_c-result.era5_temperature_c)
    return result


class Selective:
    def __init__(self, spec, without_isd=False):
        self.spec, self.without_isd = spec, without_isd

    def fit(self, frame):
        spec = self.spec
        self.columns = list(ATLAS_FEATURES if spec['method'] == 'huber' else CORE)
        if not self.without_isd:
            self.columns += GROUPS[spec['group']]
        if spec['method'] == 'huber':
            self.model = make_pipeline(StandardScaler(), HuberRegressor(alpha=spec['alpha'], epsilon=1.1, max_iter=2500))
            key = 'huberregressor__sample_weight'
        else:
            self.model = make_pipeline(SplineTransformer(n_knots=4, degree=2, include_bias=False, extrapolation='linear'), StandardScaler(), Ridge(alpha=spec['alpha']))
            key = 'ridge__sample_weight'
        with threadpool_limits(limits=1):
            self.model.fit(features(frame)[self.columns], frame.residual_target, **{key: weights(frame)})
        return self

    def predict(self, frame):
        with threadpool_limits(limits=1):
            return self.model.predict(features(frame)[self.columns])


def out_of_block(frame, factory):
    prediction = np.zeros(len(frame))
    for a, b in folds(frame, 5):
        prediction[b] = factory().fit(frame.iloc[a]).predict(frame.iloc[b])
    return prediction


def search():
    root = Path('data/private/ml-v6-fusion/selective-development')
    root.mkdir(exist_ok=True)
    source = Path('data/private/ml-v5-isd/training.parquet')
    frame = pd.read_parquet(source)
    plan = {'specifications': SPECS, 'source_sha256': digest(source), 'selection': 'Minimum geographic block MAE among candidates improving both row and block MAE over the fixed baseline. ISD ablation must also improve both. Adaptive development only; fresh tests stay closed.'}
    if (root/'plan.json').exists() and json.loads((root/'plan.json').read_text()) != plan:
        raise QualityGateError('Development plan changed.')
    write(root/'plan.json', plan)
    base = np.load(root.parent/'development/candidate-00.npz')['prediction']
    results = []
    predictions = {}
    for index, spec in enumerate(SPECS):
        path = root/f'candidate-{index:02d}.npz'
        if path.exists():
            pred = np.load(path)['prediction']
        else:
            pred = out_of_block(frame, lambda: Selective(spec))
            np.savez_compressed(path, prediction=pred)
        predictions[index] = pred
        for fraction in [.25, .5, 1.]:
            blended = (1-fraction)*base + fraction*pred
            results.append({'family': 'selective', 'index': index, 'spec': spec, 'blend': fraction, **metrics(frame, blended), 'block_mae': block_mae(frame, blended)})
        print(f'{index+1}/{len(SPECS)} {spec}: row={metrics(frame,pred)["mae_cf_points"]:.4f} block={block_mae(frame,pred):.4f}', flush=True)
        write(root/'progress.json', {'completed': index+1, 'total': len(SPECS), 'results': results})
    old = json.loads((root.parent/'development/selection.json').read_text())
    results += [dict(r, family='fusion') for r in old['ranking']]
    eligible = sorted([r for r in results if r['mae_cf_points'] < old['baseline']['mae_cf_points'] and r['block_mae'] < old['baseline']['block_mae']], key=lambda r:r['block_mae'])
    # Check the best five unique architectures, retaining every ablation result.
    ablations, seen = [], set()
    selected = None
    for entry in eligible:
        key = json.dumps([entry['family'], entry['spec']], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 5:
            break
        constructor = Selective if entry['family'] == 'selective' else Fusion
        ablated = out_of_block(frame, lambda: constructor(entry['spec'], without_isd=True))
        fraction = entry.get('blend', 1.)
        ablated = (1-fraction)*base + fraction*ablated
        score = {**metrics(frame, ablated), 'block_mae': block_mae(frame, ablated)}
        ablations.append({'candidate': entry, 'without_isd': score})
        print('Ablation: '+json.dumps(ablations[-1]), flush=True)
        if selected is None and entry['mae_cf_points'] < score['mae_cf_points'] and entry['block_mae'] < score['block_mae']:
            selected = entry
        np.savez_compressed(root/f'ablation-{len(ablations)}.npz', prediction=ablated)
    write(root/'selection.json', {'baseline': old['baseline'], 'ranking': eligible, 'ablations': ablations, 'selected': selected, 'production_eligible': False})
    print('Selected: '+json.dumps(selected), flush=True)


if __name__ == '__main__':
    search()
