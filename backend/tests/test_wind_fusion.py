import json

import numpy as np
import pandas as pd
import pytest

from scripts.fusion_wind_models import Fusion, FEATURES
from scripts.selective_isd_models import features as station_features, GROUPS
from scripts.train_isd_pudl_wind import add_features
from scripts import evaluate_wind_fusion as evaluation
from backend.ml.schema import QualityGateError


def observations():
    rng = np.random.default_rng(98)
    frame = pd.DataFrame({name: rng.uniform(1, 5, 180) for name in FEATURES['all']})
    frame['plant_id_eia'] = np.repeat(np.arange(30), 6)
    frame['label_quality'] = 'gold'
    frame['residual_target'] = .03*np.sin(frame.isd_mean_temp_c) + .002*frame.era5_wind100_mean_mps
    frame['actual_capacity_factor'] = .3+frame.residual_target
    frame['isd_coverage_fraction'] = .9
    frame['isd_nearest_station_km'] = 30.
    return add_features(frame)


def test_station_transforms_are_independent_of_outcomes_and_other_rows():
    frame = observations()
    columns = sorted({name for values in GROUPS.values() for name in values})
    expected = station_features(frame)[columns]
    frame['residual_target'] = -100
    frame['actual_capacity_factor'] = 500
    pd.testing.assert_frame_equal(station_features(frame)[columns], expected)
    pd.testing.assert_frame_equal(station_features(frame.iloc[:2])[columns], expected.iloc[:2])


def test_ablated_spline_ignores_all_station_features():
    frame = observations()
    model = Fusion({'method': 'spline', 'features': 'core', 'alpha': 10}, without_isd=True).fit(frame)
    expected = model.predict(frame)
    changed = frame.copy()
    for column in changed:
        if column.startswith('isd_') or column.startswith('station_'):
            changed[column] = 100000
    np.testing.assert_allclose(model.predict(changed), expected, atol=1e-12)
    assert not any(column.startswith(('isd_', 'station_')) for column in model.features)


@pytest.mark.parametrize('without_isd', [False, True])
def test_numeric_export_matches_sklearn_inside_and_outside_knots(without_isd):
    frame = observations()
    model = Fusion({'method': 'spline', 'features': 'core', 'alpha': 10}, without_isd=without_isd).fit(frame)
    payload = json.loads(json.dumps(evaluation.spline_payload(model), allow_nan=False))
    portable = evaluation.PortableSpline(payload)
    probe = frame.copy()
    probe.loc[0, model.features] = -20.
    probe.loc[1, model.features] = 20.
    np.testing.assert_allclose(portable.predict(probe), model.predict(probe), atol=1e-10, rtol=1e-10)


def test_numeric_export_rejects_missing_weather():
    frame = observations()
    model = Fusion({'method': 'spline', 'features': 'core', 'alpha': 10}).fit(frame)
    portable = evaluation.PortableSpline(evaluation.spline_payload(model))
    frame.loc[0, 'isd_mean_wind_mps'] = np.nan
    with pytest.raises(ValueError, match='Nonfinite'):
        portable.predict(frame)


def test_release_holdouts_cannot_be_reopened(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'ROOT', tmp_path)
    monkeypatch.setattr(evaluation, 'freeze', lambda: {})
    (tmp_path/'evaluation-started.json').write_text('{}')
    with pytest.raises(QualityGateError, match='already started'):
        evaluation.run()


def test_station_coverage_downweights_distant_observations():
    frame = observations().iloc[:2].copy()
    frame.loc[:, 'isd_mean_wind_mps'] = 5.
    frame.loc[:, 'era5_wind10_mean_mps'] = 3.
    frame.loc[:, 'isd_coverage_fraction'] = .9
    frame.loc[:, 'isd_nearest_station_km'] = [0., 90.]
    transformed = station_features(frame)
    assert transformed.iloc[0].isd_weighted_wind_gap > 10*transformed.iloc[1].isd_weighted_wind_gap
