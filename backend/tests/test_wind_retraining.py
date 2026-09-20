"""Guard the fresh holdouts against tuning leakage and duplicated plant weight."""
import json
import pytest

pd = pytest.importorskip('pandas')
pytest.importorskip('lightgbm')
from backend.ml.protocol import freeze, split, geographic_blocks
from backend.ml.retraining import weights, train
from backend.ml.schema import QualityGateError


def sample():
    rows=[]
    for region,state in enumerate(['CA','OR','WA']):
        for block in range(4):
            for plant in range(12):
                for year in [2021,2022,2023]:
                    for month in range(1,13):
                        rows.append({'plant_id_eia':region*1000+block*100+plant,
                                     'state':state, 'latitude':30+region*10+block*2+.2, 'longitude':-120.1,
                                     'report_month':pd.Timestamp(year,month,1), 'actual_capacity_factor':.2})
    return pd.DataFrame(rows)


def test_protocol_is_outcome_blind_and_rejects_changes(tmp_path):
    frame=sample()
    old=[0,100,1000,2000]
    first=freeze(frame,old,tmp_path/'protocol.json')
    frame.actual_capacity_factor=999
    assert freeze(frame,old,tmp_path/'protocol.json')==first
    with pytest.raises(QualityGateError,match='protocol changed'):
        freeze(frame,old+[1],tmp_path/'protocol.json')


def test_fresh_tests_exclude_old_plants_and_whole_spatial_blocks(tmp_path):
    frame=sample()
    old=[0,100,1000,2000]
    protocol=freeze(frame,old,tmp_path/'protocol.json')
    fit,spatial,temporal=split(frame,protocol)
    assert not set(geographic_blocks(fit))&set(geographic_blocks(spatial))
    assert not set(fit.plant_id_eia)&set(spatial.plant_id_eia)
    assert not set(old)&set(spatial.plant_id_eia)
    assert not set(old)&set(temporal.plant_id_eia)
    assert set(fit.report_month.dt.year)=={2021,2022}
    assert set(temporal.report_month.dt.year)=={2023}


def test_quality_filter_cannot_silently_remove_test_coverage(tmp_path):
    frame=sample()
    protocol=freeze(frame,[],tmp_path/'protocol.json')
    remaining=frame[~frame.plant_id_eia.isin(protocol['spatial_test_plants'])]
    with pytest.raises(QualityGateError,match='coverage fell'):
        split(remaining,protocol)


def test_allocated_generators_do_not_overweight_a_plant():
    frame=pd.DataFrame({'plant_id_eia':[1]*12+[2]*24+[3]*12,
                        'label_quality':['silver']*36+['gold']*12})
    frame['weight']=weights(frame)
    totals=frame.groupby('plant_id_eia').weight.sum()
    assert totals[1]==pytest.approx(totals[2])
    assert totals[3]==pytest.approx(totals[1]*2)
    assert frame.weight.mean()==pytest.approx(1)


def test_evaluated_holdouts_cannot_be_reopened(tmp_path):
    marker=tmp_path/'evaluation-started.json'
    marker.write_text(json.dumps({'opened_at':'earlier'}),encoding='utf-8')
    with pytest.raises(QualityGateError,match='already evaluated'):
        train(None,tmp_path,{},lambda *args:None)
    assert list(tmp_path.iterdir())==[marker]


def test_local_retraining_audit_and_release_decision():
    import hashlib
    from pathlib import Path
    directory=Path('data/private/ml-v2')
    if not (directory/'evaluation.json').exists():
        pytest.skip('Expanded offline evaluation is not present.')
    report=json.loads((directory/'evaluation.json').read_text(encoding='utf-8'))
    selection=json.loads((directory/'development-selection.json').read_text(encoding='utf-8'))
    marker=json.loads((directory/'evaluation-started.json').read_text(encoding='utf-8'))
    assert marker['selection_sha256']==hashlib.sha256((directory/'development-selection.json').read_bytes()).hexdigest()
    assert marker['model_sha256']==hashlib.sha256((directory/'development-wind.txt').read_bytes()).hexdigest()
    assert selection['protocol_sha256']==hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest()
    assert report['selected_parameters']==selection['selected']['parameters']
    assert not set(report['training_plants'])&set(report['spatial_test_plants'])
    for name in ['spatial_test_plants','temporal_test_plants']:
        assert not set(report[name])&set(report['previously_examined_plants'])
    for fold in report['folds']:
        assert not set(fold['train_blocks'])&set(fold['validation_blocks'])
    baseline=next(m for m in report['models'] if m['model']=='zero_residual')
    model=next(m for m in report['models'] if m['model']=='lightgbm')
    for name in ['spatial','temporal']:
        improvement=(1-model[name]['mae_cf_points']/baseline[name]['mae_cf_points'])*100
        assert report['improvement_pct'][name]==pytest.approx(improvement)
    if any(v<5 for v in report['improvement_pct'].values()):
        assert not report['production_eligible']
        assert not (directory/'artifacts'/'manifest.json').exists()
