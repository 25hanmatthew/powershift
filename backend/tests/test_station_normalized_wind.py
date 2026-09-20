import numpy as np
import pandas as pd
import pytest

from scripts.station_normalized_wind import climatology, transform_station, NORMALIZED
from backend.ml.schema import QualityGateError


def reference():
    months = pd.date_range('2016-01-01', '2018-12-01', freq='MS')
    return pd.DataFrame({'station_id': 'station-a', 'report_month': months,
        'isd_mean_wind_mps': 4., 'isd_wind_std_mps': 2., 'isd_wind_p10_mps': 1.,
        'isd_wind_p90_mps': 7., 'isd_mean_temp_c': 10., 'isd_coverage_fraction': .9})


def test_station_reference_cannot_include_target_years():
    frame = reference()
    frame.loc[0, 'report_month'] = pd.Timestamp('2019-01-01')
    with pytest.raises(QualityGateError, match='precede'):
        climatology(frame)


def test_reference_requires_every_season_and_observed_hours():
    frame = reference()
    frame.loc[frame.report_month.dt.month.eq(1), 'isd_coverage_fraction'] = .2
    assert climatology(frame).empty


def test_normalization_uses_same_station_pretraining_climate():
    climate = climatology(reference())
    months = reference().iloc[:1].copy()
    months['report_month'] = pd.Timestamp('2023-01-01')
    months['isd_mean_wind_mps'] = 6.
    months['isd_mean_temp_c'] = 15.
    result = transform_station(months, climate).iloc[0]
    assert result.station_wind_ratio == 1.5
    assert result.station_wind_anomaly == .5
    assert result.station_temp_anomaly == 5.
    assert np.isfinite(result[NORMALIZED].astype(float)).all()
    months['station_id'] = 'unmatched-station'
    assert transform_station(months, climate).empty


def test_uniform_station_exposure_scaling_cancels():
    old = reference()
    current = old.iloc[:1].copy()
    current['report_month'] = pd.Timestamp('2022-01-01')
    expected = transform_station(current, climatology(old))[NORMALIZED]
    wind = ['isd_mean_wind_mps', 'isd_wind_std_mps', 'isd_wind_p10_mps', 'isd_wind_p90_mps']
    old[wind] *= 2
    current[wind] *= 2
    pd.testing.assert_frame_equal(transform_station(current, climatology(old))[NORMALIZED], expected)
