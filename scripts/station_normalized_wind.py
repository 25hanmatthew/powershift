"""Use pre-training ISD climatology to remove station exposure differences."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler, SplineTransformer
from threadpoolctl import threadpool_limits

from backend.ml.data import aggregate_station, decode_isd_archive
from backend.ml.schema import ATLAS_FEATURES, QualityGateError
from backend.ml.retraining import weights, block_mae
from backend.ml.training import metrics
from scripts.fusion_wind_models import Fusion, CORE
from scripts.selective_isd_models import out_of_block
from scripts.train_isd_pudl_wind import write, digest

ROOT = Path('data/private/ml-v7-station-normalized')
WEATHER = ['isd_mean_wind_mps', 'isd_wind_std_mps', 'isd_wind_p10_mps', 'isd_wind_p90_mps', 'isd_mean_temp_c']
NORMALIZED = ['station_wind_ratio', 'station_wind_anomaly', 'station_std_ratio', 'station_p10_ratio', 'station_p90_ratio', 'station_temp_anomaly']
GROUPS = {'wind': NORMALIZED[:2], 'distribution': NORMALIZED[:5], 'all': NORMALIZED,
          'temperature': ['station_temp_anomaly']}
SPECS = [dict(method=m, group=g, alpha=a) for m in ['huber', 'spline'] for g in GROUPS for a in [10, 100]]


def climatology(months):
    """Only pre-2019 station measurements enter fixed reference values."""
    if not months.report_month.dt.year.between(2016, 2018).all():
        raise QualityGateError('Station climatology must precede all target years.')
    months = months[months.isd_coverage_fraction.ge(.7)].copy()
    months['month'] = months.report_month.dt.month
    counts = months.groupby('station_id').size()
    complete = months.groupby(['station_id', 'month']).size().unstack(fill_value=0)
    eligible = set(complete.index[(complete.ge(2).sum(axis=1) == 12)]) & set(counts[counts.ge(24)].index)
    months = months[months.station_id.isin(eligible)]
    annual = months.groupby('station_id').isd_mean_wind_mps.mean().rename('clim_wind').reset_index()
    monthly = months.groupby(['station_id', 'month'])[WEATHER].mean().add_prefix('clim_').reset_index()
    result = monthly.merge(annual, on='station_id', validate='many_to_one')
    return result[result.clim_wind.ge(.5)]


def transform_station(months, climate):
    months = months.copy()
    months['month'] = months.report_month.dt.month
    result = months.merge(climate, on=['station_id', 'month'], validate='many_to_one')
    result['station_wind_ratio'] = result.isd_mean_wind_mps/result.clim_wind
    result['station_wind_anomaly'] = (result.isd_mean_wind_mps-result.clim_isd_mean_wind_mps)/result.clim_wind
    for tail in ['std', 'p10', 'p90']:
        result[f'station_{tail}_ratio'] = result[f'isd_wind_{tail}_mps']/result.clim_wind
    result['station_temp_anomaly'] = result.isd_mean_temp_c-result.clim_isd_mean_temp_c
    return result[result.isd_coverage_fraction.ge(.7)].copy()


def prepare(source=Path('data/private/ml-v6-fusion/complete.parquet'), sources=None, directory=ROOT, target_years=(2019, 2020, 2021, 2022)):
    directory.mkdir(exist_ok=True)
    if (directory/'training.parquet').exists():
        return pd.read_parquet(directory/'training.parquet')
    frame = pd.read_parquet(source)
    frame = frame[frame.report_month.dt.year.isin(target_years)].copy()
    sources = sources or [Path('data/private/ml-v5-isd'), Path('data/private/ml-v6-fusion/fresh')]
    matches = pd.concat([pd.read_parquet(p/'station-matches.parquet') for p in sources]).drop_duplicates(['plant_id_eia', 'station_id'])
    matches = matches[matches.plant_id_eia.isin(frame.plant_id_eia)]
    matches.to_parquet(directory/'station-matches.parquet', index=False)
    plan = {'reference_years': [2016, 2017, 2018], 'target_years': list(target_years),
            'station_ids': sorted(matches.station_id.unique().tolist()), 'source_sha256': digest(source), 'specifications': SPECS,
            'purpose': 'Development search. Revision 6 holdouts are now examined and are NEVER claimed fresh here. No new final test is opened by this script.',
            'reference_quality': 'At least 24 pre-2019 months; at least two observations for every calendar month; 70% hourly coverage; same station identities as target weather.'}
    write(directory/'plan.json', plan)
    cache = directory/'isd-reference'; cache.mkdir(exist_ok=True)
    pairs = [(station, year) for station in plan['station_ids'] for year in plan['reference_years']]
    errors = []
    def fetch(pair):
        station, year = pair
        target = cache/f'{station}-{year}.parquet'
        if target.exists():
            return pd.read_parquet(target)
        try:
            response = httpx.get(f'https://www.ncei.noaa.gov/pub/data/noaa/{year}/{station[:6]}-{station[6:]}-{year}.gz', timeout=30)
            response.raise_for_status()
            result = aggregate_station(decode_isd_archive(response.content))
            if result.empty:
                return result
            result['station_id'] = station
            result.to_parquet(target, index=False)
            return result
        except (httpx.HTTPError, ValueError, OSError) as error:
            errors.append({'station': station, 'year': year, 'error': type(error).__name__})
            return pd.DataFrame()
    reference = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, result in enumerate(pool.map(fetch, pairs)):
            if len(result):
                reference.append(result)
            if (i+1)%30 == 0:
                print(f'Pre-training station-years: {i+1}/{len(pairs)}', flush=True)
    if not reference:
        raise QualityGateError('No pre-training ISD reference data.')
    climate = climatology(pd.concat(reference, ignore_index=True))
    climate.to_parquet(directory/'climatology.parquet', index=False)
    observed = []
    for station in plan['station_ids']:
        for year in target_years:
            for location in sources:
                path = location/'isd'/f'{station}-{year}.parquet'
                if path.exists():
                    observed.append(pd.read_parquet(path))
                    break
    normalized = transform_station(pd.concat(observed, ignore_index=True), climate)
    joined = matches.merge(normalized, on='station_id', validate='many_to_many')
    rows = []
    for (plant, month), group in joined.groupby(['plant_id_eia', 'report_month']):
        w = 1/np.maximum(group.distance_km, 1)**2
        row = {'plant_id_eia': plant, 'report_month': month}
        row.update({name: float(np.average(group[name], weights=w)) for name in NORMALIZED})
        rows.append(row)
    result = frame.merge(pd.DataFrame(rows), on=['plant_id_eia', 'report_month'], validate='one_to_one')
    retained = len(result)/len(frame)
    write(directory/'data-report.json', {'source_rows': len(frame), 'retained_rows': len(result), 'retained_fraction': retained,
          'plants': int(result.plant_id_eia.nunique()), 'reference_stations': int(climate.station_id.nunique()), 'download_errors': errors})
    if retained < .9 or not np.isfinite(result[NORMALIZED]).all(axis=None):
        raise QualityGateError('Station normalization needs finite values and at least 90% paired-row retention.')
    result.to_parquet(directory/'training.parquet', index=False)
    print(f'Normalized ISD rows: {len(result)}, plants: {result.plant_id_eia.nunique()}', flush=True)
    return result


class Normalized:
    def __init__(self, spec, without_isd=False):
        self.spec, self.without_isd = spec, without_isd

    def fit(self, frame):
        s = self.spec
        self.columns = list(ATLAS_FEATURES if s['method'] == 'huber' else CORE)
        if not self.without_isd:
            self.columns += GROUPS[s['group']]
        if s['method'] == 'huber':
            self.model = make_pipeline(StandardScaler(), HuberRegressor(alpha=s['alpha'], epsilon=1.1, max_iter=2500))
            key = 'huberregressor__sample_weight'
        else:
            self.model = make_pipeline(SplineTransformer(n_knots=4, degree=2, include_bias=False, extrapolation='linear'), StandardScaler(), Ridge(alpha=s['alpha']))
            key = 'ridge__sample_weight'
        with threadpool_limits(limits=1):
            self.model.fit(frame[self.columns], frame.residual_target, **{key: weights(frame)})
        return self

    def predict(self, frame):
        with threadpool_limits(limits=1):
            return self.model.predict(frame[self.columns])


def search(frame):
    base_path = ROOT/'baseline.npz'
    if base_path.exists():
        base = np.load(base_path)['prediction']
    else:
        base = out_of_block(frame, lambda: Fusion({'method': 'baseline', 'features': 'atlas'}))
        np.savez_compressed(base_path, prediction=base)
    baseline = {**metrics(frame, base), 'block_mae': block_mae(frame, base)}
    results = []
    for i, spec in enumerate(SPECS):
        path = ROOT/f'candidate-{i:02d}.npz'
        if path.exists():
            prediction = np.load(path)['prediction']
        else:
            prediction = out_of_block(frame, lambda: Normalized(spec))
            np.savez_compressed(path, prediction=prediction)
        for fraction in [.25, .5, 1.]:
            blended = (1-fraction)*base + fraction*prediction
            results.append({'index': i, 'spec': spec, 'blend': fraction, **metrics(frame, blended), 'block_mae': block_mae(frame, blended)})
        print(f'Normalized candidate {i+1}/{len(SPECS)}: '+json.dumps(results[-1]), flush=True)
        write(ROOT/'progress.json', {'completed': i+1, 'baseline': baseline, 'results': results})
    eligible = sorted([r for r in results if r['mae_cf_points'] < baseline['mae_cf_points'] and r['block_mae'] < baseline['block_mae']], key=lambda r:r['block_mae'])
    selected, ablations = None, []
    for entry in eligible[:5]:
        prediction = out_of_block(frame, lambda: Normalized(entry['spec'], without_isd=True))
        prediction = (1-entry['blend'])*base + entry['blend']*prediction
        score = {**metrics(frame, prediction), 'block_mae': block_mae(frame, prediction)}
        ablations.append({'candidate': entry, 'without_isd': score})
        if selected is None and entry['mae_cf_points'] < score['mae_cf_points'] and entry['block_mae'] < score['block_mae']:
            selected = entry
    write(ROOT/'selection.json', {'baseline': baseline, 'ranking': sorted(results, key=lambda r:r['block_mae']), 'ablations': ablations, 'selected': selected, 'production_eligible': False})
    print('Station-normalized selection: '+json.dumps(selected), flush=True)


if __name__ == '__main__':
    search(prepare())
