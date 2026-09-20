import numpy as np
import pandas as pd
import pytest
from scripts.train_isd_pudl_wind import add_features, quality, folds, FEATURE_SETS
from backend.ml.protocol import geographic_blocks
from backend.ml.schema import QualityGateError


def sample():
 n=120
 frame=pd.DataFrame({f:np.ones(n) for f in FEATURE_SETS['isd_physics']})
 frame['plant_id_eia']=np.arange(n)
 frame['latitude']=30+(np.arange(n)%20)*2
 frame['longitude']=-110
 frame['report_month']=pd.Timestamp('2021-01-01')
 frame['actual_capacity_factor']=.3
 frame['residual_target']=.1
 return frame


def test_no_outcome_identifiers_in_feature_sets():
 for fields in FEATURE_SETS.values():
  assert not set(fields)&{'plant_id_eia','actual_capacity_factor','net_generation_mwh','residual_target','latitude','longitude','report_year'}


def test_derived_weather_does_not_depend_on_targets():
 frame=sample();first=add_features(frame)
 frame['actual_capacity_factor']=.9;frame['residual_target']=-.5
 pd.testing.assert_frame_equal(first[FEATURE_SETS['isd_physics']],add_features(frame)[FEATURE_SETS['isd_physics']])


@pytest.mark.parametrize('field,value', [('isd_coverage_fraction',.2),('isd_nearest_station_km',101),('era5_wind10_mean_mps',np.nan),('report_month',pd.Timestamp('2023-01-01'))])
def test_weather_and_temporal_gates(field,value):
 frame=sample();frame.loc[0,field]=value
 with pytest.raises(QualityGateError):quality(frame)


def test_duplicate_labels_rejected():
 frame=sample()
 with pytest.raises(QualityGateError,match='Duplicate'):quality(pd.concat([frame,frame.iloc[:1]]))


def test_nested_geographic_partitions_do_not_overlap():
 frame=sample();quality(frame)
 for fit,validation in folds(frame,5):
  assert not set(geographic_blocks(frame.iloc[fit]))&set(geographic_blocks(frame.iloc[validation]))
  for a,b in folds(frame.iloc[fit],3):
   inner=frame.iloc[fit]
   assert not set(geographic_blocks(inner.iloc[a]))&set(geographic_blocks(inner.iloc[b]))
