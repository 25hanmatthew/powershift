import numpy as np
import pandas as pd
import pytest

from backend.ml.schema import QualityGateError
from scripts.wind_insight_data import RAW
from scripts.station_normalized_wind import NORMALIZED
from scripts.train_wind_insights import prepare_rows, reference, GROUPS, InsightModel
from scripts.evaluate_wind_insights import predict, paired_contrast, bootstrap_gain


def history():
    rng = np.random.default_rng(44)
    rows = []
    for plant in range(36):
        for year in range(2019,2023):
            for month in [1,2,3]:
                row = {name:float(rng.uniform(1,3)) for name in RAW+NORMALIZED}
                row.update(plant_id_eia=plant,report_month=pd.Timestamp(year=year,month=month,day=1),
                    actual_capacity_factor=.25+.02*row['station_wind_ratio'],latitude=30+2*(plant//3),
                    longitude=-100,state='X',label_quality='gold')
                rows.append(row)
    return pd.DataFrame(rows)


def test_target_generation_does_not_enter_its_baseline_or_predictors():
    frame = history()
    before = prepare_rows(frame,frame,leave_self_out=True,minimum=2)
    frame.loc[0,'actual_capacity_factor'] += .2
    after = prepare_rows(frame,frame,leave_self_out=True,minimum=2)
    names = list(dict.fromkeys(sum(GROUPS.values(),[])))+['baseline_cf']
    np.testing.assert_allclose(before.iloc[0][names].astype(float),after.iloc[0][names].astype(float),atol=1e-12)
    assert after.iloc[0].cf_anomaly != before.iloc[0].cf_anomaly


def test_final_year_never_enters_reference():
    frame = history()
    frame.loc[0,'report_month'] = pd.Timestamp('2024-01-01')
    with pytest.raises(QualityGateError,match='outside training'):
        reference(frame)


def test_final_predictions_do_not_use_target_generation():
    frame = history()
    fit = prepare_rows(frame,frame,leave_self_out=True,minimum=2)
    model = InsightModel({'group':'interactions','method':'huber','alpha':10}).fit(fit)
    query = frame[frame.report_month.dt.year.eq(2022)].copy()
    query['report_month'] += pd.DateOffset(years=2)
    first = prepare_rows(query,frame)
    query['actual_capacity_factor'] = .99
    second = prepare_rows(query,frame)
    np.testing.assert_allclose(model.predict(first),model.predict(second),atol=1e-12)


@pytest.mark.parametrize('method',['huber','spline'])
def test_research_export_matches_fitted_predictions(method):
    frame = history()
    fit = prepare_rows(frame,frame,leave_self_out=True,minimum=2)
    model = InsightModel({'group':'wind','method':method,'alpha':100}).fit(fit)
    np.testing.assert_allclose(model.predict(fit),predict(model.export(fit),fit),atol=1e-10)


def test_paired_contrast_does_not_mix_different_plants():
    frame = history().iloc[:50].copy()
    frame['cf_anomaly'] = -.1
    event = frame.plant_id_eia.eq(0)
    normal = frame.plant_id_eia.ne(0)
    result,pairs = paired_contrast(frame,event,normal,'cf_anomaly')
    assert result['status']=='insufficient_coverage'
    assert len(pairs)==0


def test_cluster_gain_preserves_identity_comparison():
    frame = history()
    prediction = np.full(len(frame),.3)
    result = bootstrap_gain(frame,prediction,prediction)
    assert result['gain_pct']==0
    assert result['lower_pct']==0
    assert result['upper_pct']==0
