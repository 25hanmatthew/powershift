import copy
import json
import math
import pytest
from fastapi.testclient import TestClient
from backend.demo import candidates
from backend.models import Plan
from backend.scoring import rank_candidates
from backend.ml import runtime
from backend.ml.schema import MLFields, FEATURES, SCHEMA_VERSION, BASELINE_ID
from backend.main import app


def scoring_snapshot(result):
    return result['portfolio'],result['selected_ids'],[(c['id'],c['score'],c['components']) for c in result['candidates']]


def test_flag_off_preserves_existing_rankings_and_clears_old_corrections(monkeypatch):
    monkeypatch.setenv('VOLRIDGE_ML_ENABLED','false')
    plan=Plan(historical_intelligence=True)
    raw=candidates(plan.region)
    expected=rank_candidates(raw,Plan())
    poisoned=copy.deepcopy(raw)
    for item in poisoned: item.update(ml_enabled=True,ml_confidence='HIGH',ml_corrected_expected_cf=.9)
    enriched=runtime.enrich(poisoned,plan)
    assert scoring_snapshot(rank_candidates(enriched,plan))==scoring_snapshot(expected)
    assert all(not c['ml_enabled'] and c['ml_adjustment_cf']==0 for c in enriched)
    assert all(c['ml_corrected_expected_cf'] is None for c in enriched)
    assert raw==candidates(plan.region)


def test_enabled_missing_model_falls_back_without_training_or_crashing(monkeypatch,tmp_path):
    monkeypatch.setenv('VOLRIDGE_ML_ENABLED','true')
    monkeypatch.setenv('VOLRIDGE_ML_DATA_DIR',str(tmp_path))
    plan=Plan(mode='live',historical_intelligence=True)
    raw=candidates(plan.region)
    enriched=runtime.enrich(raw,plan)
    assert all(c['ml_confidence']=='LOW' and not c['ml_enabled'] for c in enriched)
    assert not runtime.status()['model_loaded']
    assert scoring_snapshot(rank_candidates(enriched,plan))==scoring_snapshot(rank_candidates(raw,Plan()))
    assert list(tmp_path.iterdir())==[]


def test_disabled_default_fields_are_nullable():
    fields=MLFields().model_dump()
    assert fields['ml_enabled'] is False and fields['ml_adjustment_cf']==0
    assert all(value is None for key,value in fields.items() if key not in ['ml_enabled','ml_adjustment_cf'])


def valid_manifest():
    return {'schema_version':SCHEMA_VERSION,'feature_order':FEATURES,'technology':'wind','baseline_id':BASELINE_ID,
            'production_eligible':True,'benchmark':{'production_eligible':True,'lightgbm_beats_sanity_baselines':True,
                                                  'improvement_pct':{'spatial':8,'temporal':9}}}


@pytest.mark.parametrize('change',[
    {'schema_version':'unknown'}, {'feature_order':list(reversed(FEATURES))},
    {'baseline_id':'era5'}, {'production_eligible':False},
    {'benchmark':{'production_eligible':True,'lightgbm_beats_sanity_baselines':True,'improvement_pct':{'spatial':4.9,'temporal':30}}},
    {'benchmark':{'production_eligible':True,'lightgbm_beats_sanity_baselines':True,'improvement_pct':{'spatial':math.nan,'temporal':30}}},
])
def test_release_gate_rejects_incompatible_or_unvalidated_models(change):
    runtime.validate_manifest(valid_manifest())
    with pytest.raises(ValueError): runtime.validate_manifest({**valid_manifest(),**change})


def test_plant_model_schemas_require_an_exact_known_feature_set():
    from backend.ml.schema import PLANT_SCHEMA_VERSION,PLANT_FEATURES,PHYSICAL_FEATURES,ATLAS_SCHEMA_VERSION,ATLAS_FEATURES
    for schema,features in [(PLANT_SCHEMA_VERSION,PLANT_FEATURES),(PLANT_SCHEMA_VERSION,PHYSICAL_FEATURES),
                            (ATLAS_SCHEMA_VERSION,ATLAS_FEATURES),(ATLAS_SCHEMA_VERSION,PHYSICAL_FEATURES)]:
        manifest={**valid_manifest(),'schema_version':schema,'feature_order':features}
        runtime.validate_manifest(manifest)
        with pytest.raises(ValueError,match='schema'):
            runtime.validate_manifest({**manifest,'feature_order':features[:-1]})


def test_low_confidence_cannot_change_score_or_generation():
    raw=next(c for c in candidates('sacramento') if c['technology']=='wind')
    raw.update(ml_enabled=True,ml_confidence='LOW',ml_corrected_expected_cf=.1)
    actual=runtime.apply_correction(raw,True)
    assert actual['components']==raw['components'] and actual['annual_gwh']==raw['annual_gwh']


def test_toggling_off_restores_base_without_accumulating_corrections():
    raw=next(c for c in candidates('sacramento') if c['technology']=='wind')
    raw.update(ml_enabled=True,ml_confidence='HIGH',ml_corrected_expected_cf=.3)
    adjusted=runtime.apply_correction(raw,True)
    assert adjusted['components']['resource']!=raw['components']['resource']
    assert runtime.apply_correction(adjusted,True)['components']==adjusted['components']
    restored=runtime.apply_correction(adjusted,False)
    assert restored['components']==raw['components'] and restored['annual_gwh']==raw['annual_gwh']


def test_baseline_mismatch_rejected_before_model_prediction():
    candidate={'ml_input':{'schema_version':SCHEMA_VERSION,'baseline_id':'era5'}}
    with pytest.raises(ValueError,match='baseline differs'):
        runtime.infer(candidate,({},None,None,None))


def test_api_status_is_readable_without_optional_model(monkeypatch,tmp_path):
    monkeypatch.setenv('VOLRIDGE_ML_ENABLED','false')
    monkeypatch.setenv('VOLRIDGE_ML_DATA_DIR',str(tmp_path))
    with TestClient(app) as client:
        result=client.get('/api/intelligence')
        assert result.status_code==200 and result.json()['model_loaded'] is False
        assert result.json()['validation'] is None


def test_isd_qc_scaling_and_duplicate_hours():
    pd=pytest.importorskip('pandas')
    from backend.ml.data import aggregate_station
    raw=pd.DataFrame({'DATE':['2021-01-01T00:00:00','2021-01-01T00:30:00','2021-01-01T01:00:00','2021-01-01T02:00:00'],
        'WND':['000,1,N,0100,1','000,1,N,0100,1','000,1,N,0200,5','000,1,N,9999,9'],
        'TMP':['+0100,1','+0100,1','+0200,5','+9999,9']})
    result=aggregate_station(raw).iloc[0]
    assert result.isd_mean_wind_mps==15
    assert result.isd_mean_temp_c==15
    assert result.isd_coverage_fraction==pytest.approx(2/(31*24))


def test_real_benchmark_has_disjoint_plants_and_rejection_is_honest():
    # Optional local integration check; public source data is not required for the core suite.
    from pathlib import Path
    path=Path('data/private/ml/evaluation.json')
    if not path.exists(): pytest.skip('Offline evaluation is not present.')
    report=json.loads(path.read_text())
    assert not set(report['training_plants'])&set(report['spatial_test_plants'])
    for fold in report['folds']:
        assert not set(fold['train_plants'])&set(fold['validation_plants'])
    if any(value<5 for value in report['improvement_pct'].values()):
        assert report['production_eligible'] is False
