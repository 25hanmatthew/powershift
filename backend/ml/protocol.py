"""Freeze a new evaluation before fitting; previously inspected plants cannot be tests."""
import hashlib
import json
from datetime import datetime, timezone

import numpy as np

from .schema import QualityGateError


def geographic_blocks(frame):
    return (np.floor(frame.latitude / 2).astype(int).astype(str) + ':' +
            np.floor(frame.longitude / 2).astype(int).astype(str))


def freeze(frame, previous_plants, path, *, version='wind-retrain-v2', training_years=None, candidate_grid=None):
    # Only identifiers and geography are read here, never outcome or weather columns.
    plants = frame[['plant_id_eia', 'state', 'latitude', 'longitude']].drop_duplicates('plant_id_eia').copy()
    plants['block'] = geographic_blocks(plants)
    plants['fresh'] = ~plants.plant_id_eia.isin(previous_plants)
    selected = set()
    for state, group in plants.groupby('state', sort=True):
        counts = group.groupby('block').fresh.sum()
        eligible = [block for block, count in counts.items() if count >= 3]
        if len(eligible) < 2:
            continue  # retain sparse states in development, without claiming a state test
        seed = 'wind-v2' if version == 'wind-retrain-v2' else version
        ordered = sorted(eligible, key=lambda b: hashlib.sha256(f'{seed}:{state}:{b}'.encode()).hexdigest())
        selected.add(ordered[0])
    spatial = plants[plants.fresh & plants.block.isin(selected)]
    temporal = plants[plants.fresh & ~plants.block.isin(selected)]
    if spatial.plant_id_eia.nunique() < 10 or temporal.plant_id_eia.nunique() < 10:
        raise QualityGateError('Fresh spatial and temporal tests each require at least ten plants.')
    protocol = {
        'version': version, 'created_at': datetime.now(timezone.utc).isoformat(),
        'previously_examined_plants': sorted(int(p) for p in previous_plants),
        'spatial_blocks': sorted(selected),
        'spatial_test_plants': sorted(int(p) for p in spatial.plant_id_eia),
        'temporal_test_plants': sorted(int(p) for p in temporal.plant_id_eia),
        'training_years': training_years or [2021, 2022], 'temporal_test_year': 2023,
        'selection_rule': 'Outcome-blind SHA256 block selection within states; whole blocks removed from training; only new plants evaluated.',
        'cv': 'Five-fold geographic-block CV; fixed rounds selected by macro-block MAE; no early stopping on scored folds.',
        'weighting': 'Equal total weight per plant, then gold=1 / silver=0.5; normalized to mean one.',
        'candidate_grid': candidate_grid or [{'num_leaves': leaves, 'min_child_samples': minimum, 'alpha': alpha,
                            'n_estimators': rounds, 'reg_lambda': 10}
                           for leaves, minimum, alpha in [(7, 40, .05), (7, 80, .1), (15, 40, .1), (15, 80, .2)]
                           for rounds in [100, 300, 600]],
        'release_gate': 'At least 5% row MAE improvement on BOTH fresh tests and CV row MAE no worse than Ridge and Random Forest.',
        'one_shot': True,
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding='utf-8'))
        if {k:v for k,v in existing.items() if k != 'created_at'} != {k:v for k,v in protocol.items() if k != 'created_at'}:
            raise QualityGateError('Frozen evaluation protocol changed. Do not reuse these tests for a new experiment.')
        return existing
    path.write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    return protocol


def split(frame, protocol):
    blocks = geographic_blocks(frame)
    development = ~blocks.isin(protocol['spatial_blocks'])
    train = frame[development & frame.report_month.dt.year.isin(protocol['training_years'])].copy()
    spatial = frame[frame.plant_id_eia.isin(protocol['spatial_test_plants']) &
                    frame.report_month.dt.year.isin(protocol['training_years'])].copy()
    temporal = frame[development & frame.plant_id_eia.isin(protocol['temporal_test_plants']) &
                     frame.report_month.dt.year.eq(protocol['temporal_test_year'])].copy()
    if set(geographic_blocks(train)) & set(geographic_blocks(spatial)):
        raise QualityGateError('Geographic test blocks overlap training.')
    for rows in [spatial, temporal]:
        if set(rows.plant_id_eia) & set(protocol['previously_examined_plants']):
            raise QualityGateError('Previously examined plants leaked into the fresh tests.')
        if rows.plant_id_eia.nunique() < 10 or len(rows) < 120:
            raise QualityGateError('Fresh test coverage fell below ten plants / 120 rows after quality filtering.')
    if geographic_blocks(train).nunique() < 5 or train.plant_id_eia.nunique() < 30:
        raise QualityGateError('Insufficient geographically independent development data.')
    return train, spatial, temporal
