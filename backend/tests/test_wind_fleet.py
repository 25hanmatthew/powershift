import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from scripts.export_wind_fleet import audit

def test_public_replay_reconciles_without_private_training_files():
    with TestClient(app) as client:
        response=client.get('/api/wind-fleet')
    assert response.status_code==200
    data=response.json();assert len(data['plants'])==data['benchmark']['plants']==278
    months=[m for p in data['plants'] for m in p['months']]
    assert len(months)==data['benchmark']['rows']==3299
    for plant in data['plants']:
        assert len({m['month'] for m in plant['months']})==len(plant['months'])
        assert all(s['distance_km']<=100 for s in plant['stations'])
    for row in months:
        assert row['coverage']>=.7 and row['reference_years']>=3
        assert row['actual_mwh']==pytest.approx(row['capacity_mw']*row['hours']*row['actual_cf'],abs=.01)
        assert sum(row['contributions_cf_points'].values())==pytest.approx((row['weather_cf']-row['baseline_cf'])*100,abs=.0001)
    base=np.mean([abs(m['actual_cf']-m['baseline_cf']) for m in months])*100
    weather=np.mean([abs(m['actual_cf']-m['weather_cf']) for m in months])*100
    assert base==pytest.approx(data['benchmark']['scores']['pudl_seasonal']['mae_cf_points'],abs=1e-5)
    assert 100*(1-weather/base)==pytest.approx(data['benchmark']['weather_gain']['gain_pct'],abs=1e-4)

def fixture():
    history=pd.DataFrame({'plant_id_eia':[1]*3,'report_month':pd.to_datetime(['2019-01-01','2020-01-01','2021-01-01']),'actual_capacity_factor':[.3,.4,.5]})
    frame=pd.DataFrame({'plant_id_eia':[1],'report_month':pd.to_datetime(['2024-01-01']),'actual_capacity_factor':[.3],
        'baseline_cf':[.4],'capacity_mw':[1.],'net_generation_mwh':[223.2],'isd_coverage_fraction':[.9],
        'isd_nearest_station_km':[20.],'isd_station_count':[1],'label_quality':['gold'],'wind_delta':[.05],'prediction_weather':[.45],'prediction_wind_only':[.43]})
    model={'models':{'weather':{'parameters':{'feature_order':['wind_delta'],'coefficients':[1.],'intercept':0.,'background_mean':[0.]}}}}
    evaluation={'rows':1,'plants':1,'scores':{k:{'mae_cf_points':v} for k,v in [('pudl_seasonal',10),('weather',15),('wind_only',13)]}}
    return frame,history,model,evaluation

@pytest.mark.parametrize('field,value',[('baseline_cf',.5),('prediction_weather',.9),('net_generation_mwh',999),('isd_coverage_fraction',.5),('isd_nearest_station_km',120)])
def test_export_rejects_corrupt_or_inadequate_measurements(field,value):
    frame,history,model,evaluation=fixture();audit(frame,history,model,evaluation)
    frame.loc[0,field]=value
    with pytest.raises(ValueError):audit(frame,history,model,evaluation)

def test_export_rejects_test_year_leakage():
    frame,history,model,evaluation=fixture();history.loc[0,'report_month']=pd.Timestamp('2024-01-01')
    with pytest.raises(ValueError,match='test year'):audit(frame,history,model,evaluation)
