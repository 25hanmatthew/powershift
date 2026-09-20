"""Use the same audited final-test runner for the independently frozen eastern test."""
import argparse
import json
from pathlib import Path

from backend.ml.schema import ATLAS_FEATURES, QualityGateError
from scripts import evaluate_wind_fusion as runner
from scripts.fusion_wind_models import Fusion, CORE
from scripts.station_normalized_wind import Normalized, GROUPS
from scripts.train_isd_pudl_wind import write, digest

ROOT = Path('data/private/ml-v8-eastern')


class NormalizedAdapter:
    """Retain identical baseline fitting, prediction metrics, and test gates."""
    def __init__(self, spec, without_isd=False):
        self.spec, self.without_isd = spec, without_isd

    def columns(self):
        if self.spec['method'] == 'baseline':
            return list(ATLAS_FEATURES)
        return list(CORE if self.spec['method'] == 'spline' else ATLAS_FEATURES) + ([] if self.without_isd else GROUPS[self.spec['group']])

    def fit(self, frame):
        constructor = Fusion if self.spec['method'] == 'baseline' else Normalized
        self.estimator = constructor(self.spec, without_isd=self.without_isd).fit(frame)
        self.features = self.columns()
        self.model = self.estimator.model
        return self

    def predict(self, frame):
        return self.estimator.predict(frame)


def freeze():
    selection = Path('data/private/ml-v7-station-normalized/selection.json')
    direct_path = Path('data/private/ml-v7-station-normalized/direct-development/selection.json')
    selected = json.loads(selection.read_text())['selected']
    direct = json.loads(direct_path.read_text())['selected']
    if selected is None or (direct is not None and direct['block_mae'] < selected['block_mae']):
        raise QualityGateError('Normalized spline is not the eligible development winner.')
    files = [Path(__file__), Path('scripts/evaluate_wind_fusion.py'), Path('scripts/station_normalized_wind.py'),
             Path('scripts/prepare_eastern_wind.py'), selection, direct_path, ROOT/'split-protocol.json']
    plan = {'experiment_revision': 8, 'selected': selected,
        'hashes': {str(p): digest(p) for p in files},
        'gate': 'At least 5% MAE gain over resource-only AND a positive MAE gain over identically retrained Atlas AND identical no-ISD ensemble on BOTH fresh tests; development no worse than Ridge and Random Forest.',
        'reference_years': [2016,2017,2018], 'training_years': [2019,2020,2021,2022], 'test_year': 2023,
        'normalization': 'Same-station pre-2019 reference weather; at least two valid observations for every calendar month. Reference periods never include target years.',
        'note': 'New plants only, whole spatial blocks withheld. Revision 6 tests are permanently examined. This run cannot turn on live candidate inference.'}
    path = ROOT/'final-plan.json'
    if path.exists() and json.loads(path.read_text()) != plan:
        raise QualityGateError('Eastern final plan changed after freeze.')
    if not path.exists():
        write(path, plan)
    return plan


def run():
    # The runner's data contract is a residual model; the adapter changes only
    # feature construction and fitted estimator, preserving all final-test gates.
    runner.ROOT = ROOT
    runner.Fusion = NormalizedAdapter
    runner.freeze = freeze
    runner.run()
    report = json.loads((ROOT/'evaluation.json').read_text())
    report['revision'] = 8
    report['model_format_revision'] = 6
    report['station_reference_years'] = [2016,2017,2018]
    report['previously_examined_tests_used_for_development'] = ['wind-fusion-v6']
    write(ROOT/'evaluation.json',report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--freeze',action='store_true')
    args = parser.parse_args()
    if args.freeze:
        freeze()
        print('Eastern final model and comparisons frozen; tests remain unopened.')
    else:
        run()
