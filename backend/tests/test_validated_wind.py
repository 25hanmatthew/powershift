"""Optional local acceptance check against the actual exported model, without retraining."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.demo import candidates
from backend.ml import runtime
from backend.models import Plan


def example():
    root=Path('data/private/ml-v4')
    if not (root/'artifacts/manifest.json').exists(): pytest.skip('Validated local artifact is not present.')
    loaded=runtime.load_bundle(str((root/'artifacts').resolve()),(root/'artifacts/manifest.json').stat().st_mtime_ns)
    manifest,model,_,_=loaded
    frame=pd.read_parquet(root/'training.parquet')
    protocol=json.loads((root/'protocol.json').read_text())
    frame=frame[frame.plant_id_eia.isin(protocol['spatial_test_plants'])&frame.report_month.dt.year.lt(2023)]
    features=manifest['feature_order']
    lo=np.array([manifest['feature_min'][key] for key in features]);hi=np.array([manifest['feature_max'][key] for key in features])
    matrix=frame[features].to_numpy();predictions=frame.resource_expected_capacity_factor+model.predict(matrix)
    valid=((matrix>=lo)&(matrix<=hi)).all(axis=1)&predictions.between(0,1)
    assert valid.sum()>0,'No held-out row can satisfy runtime feature bounds.'
    row=frame[valid].iloc[0]
    candidate=next(x for x in candidates('sacramento') if x['technology']=='wind')
    candidate.update(base_expected_cf=float(row.resource_expected_capacity_factor),base_expected_cf_source=manifest['baseline_id'],
                     ml_input={'schema_version':manifest['schema_version'],'baseline_id':manifest['baseline_id'],
                               'state':row.state,'year':int(row.report_month.year),
                               'features':{key:float(row[key]) for key in features}})
    return candidate,loaded


def test_actual_export_explains_prediction_and_reversibly_changes_rank_inputs(monkeypatch):
    candidate,loaded=example()
    monkeypatch.setenv('VOLRIDGE_ML_ENABLED','true')
    monkeypatch.setenv('VOLRIDGE_ML_DATA_DIR','data/private/ml-v4')
    fields=runtime.infer(candidate,loaded)
    assert fields['ml_enabled'] and fields['ml_confidence']=='MEDIUM'
    assert fields['ml_interval_low_cf'] is None
    assert sum(f['contribution_cf'] for f in fields['ml_top_factors'])+fields['ml_explanation_bias_cf']==pytest.approx(fields['ml_predicted_residual_cf'])
    enriched=runtime.enrich([candidate],Plan(mode='live',historical_intelligence=True))[0]
    adjusted=runtime.apply_correction(enriched,True)
    assert adjusted['annual_gwh']!=candidate['annual_gwh']
    restored=runtime.apply_correction(adjusted,False)
    assert restored['annual_gwh']==candidate['annual_gwh'] and restored['components']==candidate['components']


@pytest.mark.parametrize('change',[{'state':'CA'},{'year':2024}])
def test_actual_export_rejects_unvalidated_scope(change):
    candidate,loaded=example();candidate['ml_input'].update(change)
    with pytest.raises(ValueError,match='outside the validated'):
        runtime.infer(candidate,loaded)
