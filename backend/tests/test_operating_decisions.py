import copy
import pytest
from shapely.geometry import box, mapping
from backend import operating_evidence as oe
from backend.demo import candidates
from backend.models import Plan
from backend.scoring import rank_candidates


def dataset():
    # Equal seasonal output; measured weather reduces expected output by 20%.
    rows = [dict(month=i, baseline_mwh=100, weather_mwh=80, actual_mwh=50,
                 actual_cf=.25, weather_cf=.4, coverage=.9, station_count=1,
                 reference_years=4) for i in range(1, 13)]
    return {'test_year': 2024, 'provenance': {'model_sha256': 'frozen', 'predictions_sha256': 'audited'},
            'plants': [dict(id=i, name=f'Plant {i}', longitude=-121.4+i*.03,
                            latitude=38.6, months=copy.deepcopy(rows),
                            stations=[dict(id='station', name='ISD station', distance_km=10)]) for i in range(3)]}


def sites():
    seed = next(c for c in candidates('sacramento') if c['technology'] == 'solar')
    def make(id, technology, resource):
        return {**copy.deepcopy(seed), 'id': id, 'site_id': id, 'technology': technology,
                'longitude': -121.4, 'latitude': 38.6, 'geometry': mapping(box(-121.41,38.59,-121.39,38.61)),
                'provenance': 'computed', 'capacity_mw': 10, 'annual_gwh': 20, 'protected_overlap_pct': 0, 'grid_distance_km': 1,
                'slope_deg': 0, 'excluded_land_cover': False,
                'components': dict(resource=resource, environment=80, grid=80, buildability=80, reuse=80)}
    return [make('wind', 'wind', 90), make('solar', 'solar', 80)]


def plan(**kwargs):
    return Plan(region='sacramento', mode='live', target_mw=10,
                weights=dict(resource=100, environment=0, grid=0, buildability=0, reuse=0),
                polygon=mapping(box(-121.8,38.3,-121,38.9)), **kwargs)


def test_join_changes_actual_selected_portfolio_without_inventing_energy(monkeypatch):
    monkeypatch.setattr(oe, 'load', dataset)
    original = sites(); before = copy.deepcopy(original)
    off = rank_candidates(original, plan(use_operating_evidence=False))
    on = rank_candidates(original, plan())
    assert off['selected_ids'] == ['wind']
    assert on['selected_ids'] == ['solar']
    wind = next(c for c in on['candidates'] if c['technology'] == 'wind')
    assert wind['score_before_operating'] == 90 and wind['score'] == 72
    assert wind['annual_gwh'] == 20 and wind['capacity_mw'] == 10
    assert wind['operating_evidence']['plant_ids'] == [0,1,2]
    assert {'isd','pudl'} <= set(wind['evidence_ids'])
    assert original == before
    # Repeated ranking never compounds the penalty, and switching off restores scores.
    again = rank_candidates(on['candidates'], plan())
    assert [c['score'] for c in again['candidates']] == [80,72]
    restored = rank_candidates(on['candidates'], plan(use_operating_evidence=False))
    assert restored['selected_ids'] == ['wind'] and restored['candidates'][0]['score'] == 90


def test_local_gate_missing_month_and_distance():
    data = dataset()
    assert oe.evidence_at(-121.4,38.6,data)['weather_downside_share'] == pytest.approx(.2)
    data['plants'][0]['months'].pop()
    assert not oe.evidence_at(-121.4,38.6,data)['available']
    assert oe.evidence_at(-80,25,data)['plant_count'] == 0
    data = dataset()
    for p in data['plants']: p['longitude'] = -121.4
    assert not oe.evidence_at(-121.4,38.6,data)['available']


def test_investigation_requires_repetition_and_does_not_count_as_capacity(monkeypatch):
    monkeypatch.setattr(oe, 'load', dataset)
    result = rank_candidates(sites(), plan())
    assert len(result['operating_evidence']['investigations']) == 3
    assert result['portfolio']['capacity_mw'] == 10
    data = dataset()
    for p in data['plants']:
        for r in p['months'][2:]: r['actual_cf'] = r['weather_cf']
    assert oe.evidence_at(-121.4,38.6,data)['investigations'] == []


def test_evidence_cannot_bypass_hard_constraints_or_penalize_solar(monkeypatch):
    monkeypatch.setattr(oe, 'load', dataset)
    raw = sites(); raw[0]['protected_overlap_pct'] = 1
    result = rank_candidates(raw, plan())
    assert result['candidates'][0]['score'] == 80
    assert 'Protected land' in result['excluded'][0]['exclusion_reasons']
    monkeypatch.setattr(oe, 'load', lambda: None)
    result = rank_candidates(sites(), plan())
    assert result['candidates'][0]['score'] == 90
    assert not result['operating_evidence']['available']


def test_real_sacramento_evidence_is_traceable():
    evidence = oe.evidence_at(-121.49,38.58)
    assert evidence['available'] and evidence['complete_plants'] == 4
    assert evidence['plant_months'] == 48
    assert evidence['weather_downside_share'] == pytest.approx(.10963252, abs=1e-7)
    assert [p['name'] for p in evidence['investigations']] == ['Foundation Superior Farms']
    assert evidence['investigations'][0]['flagged_months'] == 3
