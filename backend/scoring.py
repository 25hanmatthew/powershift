"""Pure deterministic screening. No service calls or model-generated physics."""
from math import asin, cos, radians, sin, sqrt
from shapely.geometry import Point, shape, box
from .models import Plan, REGIONS
from .ml.runtime import apply_correction
from . import operating_evidence

def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(radians, [*a, *b])
    return 6371 * 2 * asin(sqrt(sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2))

def in_geography(candidate, plan):
    p = Point(candidate['longitude'], candidate['latitude'])
    geom = shape(candidate['geometry']) if candidate.get('geometry') else p
    if not box(*REGIONS[plan.region]['bounds']).covers(geom):
        return False
    if plan.polygon and not shape(plan.polygon).covers(geom):
        return False
    if plan.radius_km and plan.center:
        coords = list(geom.exterior.coords) if geom.geom_type == 'Polygon' else [(p.x, p.y)]
        if any(haversine(plan.center, coord) > plan.radius_km for coord in coords):
            return False
    return True

def exclusions(c, plan):
    k, reasons = plan.constraints, []
    if not in_geography(c, plan): reasons.append('Outside analysis boundary')
    if plan.technology != 'auto' and c['technology'] != plan.technology: reasons.append('Technology filter')
    if k.exclude_protected and c['protected_overlap_pct'] > 0: reasons.append('Protected land')
    if c['grid_distance_km'] > k.max_grid_km: reasons.append('Grid distance')
    if not c.get('surface_type') and c['slope_deg'] > k.max_slope_deg: reasons.append('Slope limit')
    if k.zero_new_land and (not c.get('developed_surface_verified', False) or c['technology'] != 'solar'):
        reasons.append('No verified developed footprint')
    if c['capacity_mw'] < k.min_capacity_mw: reasons.append('Minimum capacity')
    if c.get('excluded_land_cover', False): reasons.append('Incompatible land cover')
    reasons.extend(c.get('screening_reasons', []))
    return reasons

def rank_candidates(candidates, plan: Plan):
    weights = plan.weights.model_dump()
    total = sum(weights.values())
    if total == 0:
        weights = {k: 1 for k in weights}
        total = len(weights)
    evidence_data = operating_evidence.load() if plan.mode == 'live' else None
    eligible, excluded = [], []
    for raw in candidates:
        c = apply_correction(raw, plan.historical_intelligence)
        original_resource = c['components']['resource']
        c = operating_evidence.apply_policy(c, plan, evidence_data)
        c['score_before_operating'] = round((sum(c['components'][k]*v for k,v in weights.items()) + (original_resource-c['components']['resource'])*weights['resource']) / total, 2)
        reasons = exclusions(c, plan)
        if reasons:
            excluded.append({**c, 'exclusion_reasons': reasons})
            continue
        c['score'] = round(sum(c['components'][k] * v for k,v in weights.items()) / total, 2)
        eligible.append(c)
    eligible.sort(key=lambda c: (-c['score'], c['id']))
    selected, capacity, used = [], 0, set()
    for c in eligible:
        if capacity >= plan.target_mw: break
        # Alternative technologies on one cell cannot both be built.
        if c['site_id'] in used: continue
        used.add(c['site_id'])
        selected.append(c['id'])
        capacity += c['capacity_mw']
    for i,c in enumerate(eligible):
        c['rank'], c['selected'] = i+1, c['id'] in selected
    chosen = [c for c in eligible if c['selected']]
    return {
        'candidates': eligible, 'excluded': excluded, 'selected_ids': selected,
        'operating_evidence': operating_evidence.summary(plan, evidence_data),
        'portfolio': {'capacity_mw': round(capacity, 2), 'target_mw': plan.target_mw,
            'target_met': capacity >= plan.target_mw, 'shortfall_mw': round(max(0, plan.target_mw-capacity), 2),
            'site_count': len(chosen), 'solar_mw': round(sum(c['capacity_mw'] for c in chosen if c['technology']=='solar'), 2),
            'wind_mw': round(sum(c['capacity_mw'] for c in chosen if c['technology']=='wind'), 2),
            'annual_gwh': round(sum(c['annual_gwh'] for c in chosen), 3 if any(c.get('surface_type') for c in chosen) else 1),
            'mean_score': round(sum(c['score'] for c in chosen)/max(1,len(chosen)), 1),
            'mean_grid_km': round(sum(c['grid_distance_km'] for c in chosen)/max(1,len(chosen)), 1)},
        'verification': [
            {'label': 'Capacity target', 'passed': capacity >= plan.target_mw, 'detail': f'{capacity:g} / {plan.target_mw:g} MW'},
            {'label': 'Geographic boundary', 'passed': all(in_geography(c,plan) for c in chosen), 'detail': 'All selected footprints inside boundary'},
            {'label': 'Protected land', 'passed': all(c['protected_overlap_pct']==0 for c in chosen), 'detail': 'Hard exclusion enabled' if plan.constraints.exclude_protected else 'Exclusion disabled by user'},
            {'label': 'Grid proximity', 'passed': all(c['grid_distance_km']<=plan.constraints.max_grid_km for c in chosen), 'detail': f'Within {plan.constraints.max_grid_km:g} km'},
            ({'label':'Structural suitability','passed':False,'detail':'Roof load, pitch, shading and parking clearance need site verification'} if any(c.get('surface_type') for c in chosen) else {'label': 'Terrain', 'passed': all(c['slope_deg']<=plan.constraints.max_slope_deg for c in chosen), 'detail': f'Slope ≤ {plan.constraints.max_slope_deg:g}°'}),
        ],
    }

def components(resource, grid_km, slope, natural_pct, developed_pct):
    clamp = lambda n: round(max(0, min(100, n)), 2)
    return {'resource': clamp(resource), 'grid': clamp(100-grid_km*4),
        'environment': clamp(100-natural_pct*.85), 'buildability': clamp(100-slope*4), 'reuse': clamp(developed_pct)}
