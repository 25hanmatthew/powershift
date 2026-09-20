import numpy as np
import pandas as pd
import pytest

from scripts import prepare_eastern_wind as preparation
from scripts.evaluate_normalized_wind import NormalizedAdapter
from scripts.station_normalized_wind import Normalized, NORMALIZED
from scripts.fusion_wind_models import FEATURES
from backend.ml.schema import QualityGateError


def locations():
    frame = pd.DataFrame({'plant_id_eia': np.arange(40), 'latitude': 36+2*np.repeat(np.arange(8),5),
                          'longitude': -80., 'state': np.repeat(list('ABCDEFGH'),5)})
    return frame


def test_eastern_reserve_is_outcome_blind_and_keeps_whole_blocks(tmp_path,monkeypatch):
    frame = locations()
    frame['actual_capacity_factor'] = .3
    first = tmp_path/'first'; first.mkdir()
    monkeypatch.setattr(preparation,'ROOT',first)
    a = preparation.reserve(frame,set())
    second = tmp_path/'second'; second.mkdir()
    monkeypatch.setattr(preparation,'ROOT',second)
    frame['actual_capacity_factor'] = .99
    b = preparation.reserve(frame,set())
    assert a['spatial_test_plants'] == b['spatial_test_plants']
    assert len(a['spatial_test_plants']) >= 15
    assert len(a['temporal_test_plants']) >= 15
    assert len(a['spatial_blocks']) >= 3
    assert not set(a['spatial_test_plants'])&set(a['temporal_test_plants'])


def test_eastern_reserve_refuses_old_plants_and_small_samples(tmp_path,monkeypatch):
    monkeypatch.setattr(preparation,'ROOT',tmp_path)
    with pytest.raises(QualityGateError,match='Previously examined'):
        preparation.reserve(locations(),{0})
    with pytest.raises(QualityGateError,match='15 fresh'):
        preparation.reserve(locations().iloc[:5],set())


def test_evaluation_adapter_keeps_normalized_model_predictions():
    rng = np.random.default_rng(21)
    columns = list(dict.fromkeys(FEATURES['all']+NORMALIZED))
    frame = pd.DataFrame({c:rng.normal(size=150) for c in columns})
    frame['plant_id_eia'] = np.repeat(np.arange(30),5)
    frame['label_quality'] = 'gold'
    frame['residual_target'] = .03*frame.station_wind_anomaly
    spec = {'method':'spline','group':'all','alpha':100}
    direct = Normalized(spec).fit(frame)
    adapted = NormalizedAdapter(spec).fit(frame)
    np.testing.assert_allclose(direct.predict(frame),adapted.predict(frame),atol=1e-12)
    ablated = NormalizedAdapter(spec,without_isd=True)
    assert not set(ablated.columns())&set(NORMALIZED)
