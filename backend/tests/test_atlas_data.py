import numpy as np
import pandas as pd
import pytest

from backend.ml.atlas_data import site_atlas
from backend.ml.schema import QualityGateError


def test_atlas_ratio_excludes_later_year_and_clips_proxy(tmp_path):
    pd.DataFrame({'plant_id_eia':[1],'gwa_wind100_mps':[8.],
                  'gwa_wind100_mean_2km_mps':[8.],'gwa_wind100_std_2km_mps':[.2]}).to_parquet(tmp_path/'atlas.parquet')
    pd.DataFrame({'plant_id_eia':[1,1,1],'report_month':pd.to_datetime(['2019-01-01','2022-01-01','2023-01-01']),
                  'era5_wind100_mean_mps':[4.,8.,100.],'era5_hours':[744,744,744]}).to_parquet(tmp_path/'site-wind.parquet')
    frame=pd.DataFrame({'plant_id_eia':[1,1],'era5_power_proxy_cf':[.2,.8]})
    result=site_atlas(tmp_path,frame)
    assert np.allclose(result.gwa_wind_ratio,8/6)
    assert np.allclose(result.gwa_scaled_power_proxy_cf,[.2*(8/6)**3,1.])


def test_atlas_cache_cannot_silently_drop_new_plants(tmp_path):
    pd.DataFrame({'plant_id_eia':[1]}).to_parquet(tmp_path/'atlas.parquet')
    with pytest.raises(QualityGateError,match='membership'):
        site_atlas(tmp_path,pd.DataFrame({'plant_id_eia':[1,2]}))


def test_incompatible_climatology_cannot_silently_enter_model(tmp_path):
    pd.DataFrame({'plant_id_eia':[1],'gwa_wind100_mps':[12.],
                  'gwa_wind100_mean_2km_mps':[12.],'gwa_wind100_std_2km_mps':[.2]}).to_parquet(tmp_path/'atlas.parquet')
    pd.DataFrame({'plant_id_eia':[1],'report_month':pd.to_datetime(['2019-01-01']),
                  'era5_wind100_mean_mps':[2.],'era5_hours':[744]}).to_parquet(tmp_path/'site-wind.parquet')
    with pytest.raises(QualityGateError,match='95% coverage'):
        site_atlas(tmp_path,pd.DataFrame({'plant_id_eia':[1],'era5_power_proxy_cf':[.2]}))
