"""Historical regional evidence used by the screening policy, not a new-site forecast.

The frozen 2024 replay is never retrained here. The explicit policy discounts the
wind resource *score* by the mean observed weather-downside share of nearby plants.
It never changes physical generation, project cash flow, or capacity targets.
"""
import json
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ARTIFACT = Path(__file__).resolve().parent.parent / 'data/public/wind-fleet-2024.json'
POLICY = 'isd-pudl-regional-downside-v1'


@lru_cache(maxsize=2)
def _read(path, stamp):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load():
    try:
        return _read(str(ARTIFACT), ARTIFACT.stat().st_mtime_ns)
    except (OSError, ValueError):
        return None


def distance(a, b):
    x, y, u, v = map(radians, [*a, *b])
    return 12742 * asin(min(1, sqrt(sin((v-y)/2)**2 + cos(y)*cos(v)*sin((u-x)/2)**2)))


def complete(plant):
    rows = plant['months']
    return len(rows) == 12 and {r['month'] for r in rows} == set(range(1, 13)) and all(
        r['coverage'] >= .7 and r['reference_years'] >= 3 and r['station_count'] >= 1
        and 0 <= r['actual_cf'] <= 1 and 0 <= r['weather_cf'] <= 1 for r in rows
    ) and sum(r['baseline_mwh'] for r in rows) > 0


def evidence_at(longitude, latitude, data=None):
    data = load() if data is None else data
    base = {'policy': POLICY, 'year': 2024, 'radius_km': 100, 'plant_count': 0,
            'complete_plants': 0, 'plant_months': 0, 'available': False,
            'weather_downside_share': None, 'plants': [], 'investigations': []}
    if not data:
        return {**base, 'reason': 'Audited ISD/PUDL observations unavailable. No score adjustment.'}
    nearby = [(p, distance((longitude, latitude), (p['longitude'], p['latitude'])))
              for p in data['plants']]
    nearby = sorted([(p, km) for p, km in nearby if km <= 100], key=lambda pair: pair[1])
    qualified = [p for p, _ in nearby if complete(p)]
    shares = [sum(max(0, r['baseline_mwh']-r['weather_mwh']) for r in p['months']) /
              sum(r['baseline_mwh'] for r in p['months']) for p in qualified]
    # Spatially distinct plants are needed; co-located IDs cannot meet the gate.
    locations = {(round(p['longitude'], 2), round(p['latitude'], 2)) for p in qualified}
    available = len(qualified) >= 3 and len(locations) >= 3
    plants = []; investigations = []
    for p, km in nearby:
        full = complete(p)
        flagged = [r for r in p['months'] if r['actual_cf'] < r['weather_cf']-.1]
        item = {'id': p['id'], 'name': p['name'], 'distance_km': round(km, 1),
                'latitude': p['latitude'], 'longitude': p['longitude'],
                'months': len(p['months']), 'complete': full, 'flagged_months': len(flagged),
                'actual_mwh': round(sum(r['actual_mwh'] for r in p['months']), 1),
                'baseline_mwh': round(sum(r['baseline_mwh'] for r in p['months']), 1),
                'weather_mwh': round(sum(r['weather_mwh'] for r in p['months']), 1),
                'flagged_gap_mwh': round(sum(r['weather_mwh']-r['actual_mwh'] for r in flagged), 1),
                'stations': [{'id': s['id'], 'name': s['name'], 'distance_km': s['distance_km']} for s in p['stations']]}
        plants.append(item)
        if full and len(flagged) >= 3:
            investigations.append(item)
    investigations.sort(key=lambda p: (-p['flagged_gap_mwh'], p['id']))
    return {**base, 'year': data['test_year'], 'plant_count': len(nearby),
            'complete_plants': len(qualified), 'plant_months': len(qualified)*12,
            'available': available, 'weather_downside_share': round(sum(shares)/len(shares), 8) if available else None,
            'reason': ('Regional historical downside is used as a wind-ranking preference, not a forecast.' if available else
                       'Need 3 spatially distinct plants with all 12 qualified months within 100 km. No score adjustment.'),
            'plants': plants, 'investigations': investigations,
            'model_sha256': data['provenance']['model_sha256'],
            'predictions_sha256': data['provenance']['predictions_sha256']}


def apply_policy(candidate, plan, data):
    c = candidate
    c.pop('operating_evidence', None)
    if plan.mode != 'live' or c.get('provenance') != 'computed' or c['technology'] != 'wind':
        return c
    evidence = evidence_at(c['longitude'], c['latitude'], data)
    c['operating_evidence'] = {k: v for k, v in evidence.items() if k not in ('investigations', 'plants')}
    c['operating_evidence']['plant_ids'] = [p['id'] for p in evidence['plants'] if p['complete']]
    active = plan.use_operating_evidence and evidence['available']
    original = c['components']['resource']
    c['operating_evidence'].update(applied=active, resource_before=original)
    if evidence['plant_count']:
        c['evidence_ids'] = list(dict.fromkeys([*c.get('evidence_ids', []), 'isd', 'pudl']))
    if active:
        c['components']['resource'] = round(original*(1-evidence['weather_downside_share']), 2)
    c['operating_evidence']['resource_after'] = c['components']['resource']
    return c


def summary(plan, data):
    if plan.mode != 'live':
        return None
    if plan.polygon:
        from shapely.geometry import shape
        center = shape(plan.polygon).centroid
        return evidence_at(center.x, center.y, data)
    if plan.center:
        return evidence_at(*plan.center, data)
    from .models import REGIONS
    w, s, e, n = REGIONS[plan.region]['bounds']
    return evidence_at((w+e)/2, (s+n)/2, data)


DATASETS = [
    {'id': key, 'name': name, 'provider': provider, 'sensor': 'Reported generation' if key == 'pudl' else 'Surface weather stations',
     'space_derived': False, 'access_method': 'Audited local ISD/PUDL join', 'asset_id': None,
     'resolution_m': None, 'vintage': '2024 replay; seasonal baseline 2019–2022',
     'variables': variables, 'metrics': ['Regional wind downside', 'Existing-plant investigation priorities'],
     'energy_types': ['wind'], 'source_url': url,
     'description': 'Joined plant output and weather inform wind ranking and existing-asset investigation priorities.',
     'limitations': '278 studied plants, not a complete inventory. Historical regional evidence is not a new-site generation forecast.'}
    for key, name, provider, variables, url in [
        ('isd', 'NOAA Integrated Surface Database', 'NOAA NCEI', ['wind', 'temperature', 'quality coverage'], 'https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database'),
        ('pudl', 'PUDL / EIA plant generation and capacity', 'Catalyst Cooperative / EIA', ['reported generation', 'capacity', 'plant location'], 'https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/eia923.html')]]
