"""Fail-closed inference. Heavy dependencies load only for an enabled, validated artifact."""
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path

from .schema import BASELINE_ID, FEATURES, SCHEMA_VERSION, SCHEMAS, MLFields


def enabled():
    return os.getenv('VOLRIDGE_ML_ENABLED','false').strip().lower() in {'true','1','yes'}


def directory():
    return Path(os.getenv('VOLRIDGE_ML_DATA_DIR') or 'data/private/ml')


def read_report(name):
    try: return json.loads((directory()/name).read_text(encoding='utf-8'))
    except (OSError,ValueError): return None


def validate_manifest(manifest):
    if manifest.get('feature_order') not in SCHEMAS.get(manifest.get('schema_version'),[]):
        raise ValueError('Model feature schema is incompatible.')
    if manifest.get('technology')!='wind' or manifest.get('baseline_id')!=BASELINE_ID:
        raise ValueError('Model technology or resource baseline is incompatible.')
    benchmark=manifest.get('benchmark',{})
    improvements=benchmark.get('improvement_pct',{})
    if not manifest.get('production_eligible') or not benchmark.get('production_eligible') or not benchmark.get('selected_beats_sanity_baselines',benchmark.get('lightgbm_beats_sanity_baselines')):
        raise ValueError('Model did not pass held-out validation.')
    if any(not isinstance(improvements.get(split),(int,float)) or not math.isfinite(improvements[split]) or improvements[split]<5 for split in ['spatial','temporal']):
        raise ValueError('Model does not meet the 5% holdout improvement requirement.')


@lru_cache(maxsize=2)
def load_bundle(path,mtime):
    root=Path(path)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest)
    model_format=manifest.get('model_format','lightgbm_text')
    if model_format not in ['linear_json','lightgbm_text']: raise ValueError('Unsupported model artifact format.')
    filename='wind.json' if model_format=='linear_json' else 'wind.txt'
    if hashlib.sha256((root/filename).read_bytes()).hexdigest()!=manifest['model_sha256']:
        raise ValueError('Model artifact checksum does not match its manifest.')
    if model_format=='linear_json':
        from .linear_model import LinearModel,LinearExplanation,OffsetModel
        point=LinearModel(json.loads((root/'wind.json').read_text(encoding='utf-8')))
        explainer=LinearExplanation(point)
    else:
        import lightgbm as lgb
        import shap
        point=lgb.Booster(model_file=str(root/'wind.txt'))
        explainer=shap.TreeExplainer(point)
    if point.feature_name()!=manifest['feature_order']: raise ValueError('Model feature order is incompatible.')
    intervals=None
    if manifest.get('intervals_available'):
        if model_format=='linear_json':
            offsets=json.loads((root/'intervals.json').read_text(encoding='utf-8'))['offsets']
            if len(offsets)!=2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in offsets) or offsets[0]>offsets[1]:
                raise ValueError('Invalid linear prediction interval offsets.')
            intervals=tuple(OffsetModel(point,offset) for offset in offsets)
        else: intervals=tuple(lgb.Booster(model_file=str(root/f'{name}.txt')) for name in ['lower','upper'])
    return manifest,point,explainer,intervals


def bundle():
    root=directory()/'artifacts'
    return load_bundle(str(root.resolve()),(root/'manifest.json').stat().st_mtime_ns)


def status():
    report=read_report('pipeline-report.json')
    revision=(report or {}).get('revision',1)
    sources=[{'name':'PUDL / VCE RARE','url':'https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/vcerare.html'}]
    if revision<4: sources.append({'name':'NOAA ISD','url':'https://registry.opendata.aws/noaa-isd/'})
    if revision>=3: sources.append({'name':'ERA5 hourly weather','url':'https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_HOURLY'})
    if revision>=4: sources.append({'name':'Global Wind Atlas · DTU / World Bank','url':'https://globalwindatlas.info/'})
    sources.append({'name':'USGS SRTM / ESA WorldCover','url':'https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v200'})
    manifest=read_report('artifacts/manifest.json')
    available=False;loaded=False;reason='No validated wind model is available.'
    if manifest:
        try:
            validate_manifest(manifest);available=True
            if enabled(): bundle();loaded=True
            reason='Validated wind model loaded.' if loaded else 'Validated model available; the server feature flag is off.'
        except Exception:
            reason='The model could not be validated or loaded. Base scoring is unchanged.'
    return {'feature_enabled':enabled(),'model_available':available,'model_loaded':loaded,
            'schema_version':manifest.get('schema_version') if available else (read_report('training-report.json') or {}).get('schema_version',SCHEMA_VERSION),'technology':'wind',
            'model_version':manifest.get('model_version') if available else None,
            'status':report.get('status','not_trained') if report else 'not_trained',
            'reason':reason,'pipeline':report,'validation':read_report('evaluation.json'),
            'training':read_report('training-report.json'),'explanations':(read_report('explanations.json') or [])[:20],
            'sources':sources}


def infer(candidate, loaded):
    manifest,point,explainer,intervals=loaded
    import numpy as np
    inputs=candidate.get('ml_input') or {}
    scope=manifest.get('validation_scope')
    if scope and (inputs.get('state') not in scope['states'] or inputs.get('year') not in scope['years']):
        raise ValueError('This candidate is outside the validated historical geography or time window.')
    schema=manifest.get('schema_version',SCHEMA_VERSION)
    feature_names=manifest.get('feature_order',FEATURES)
    if inputs.get('schema_version')!=schema:
        raise ValueError('Matching historical weather features are not prepared for this candidate.')
    # A residual learned against RARE cannot be silently applied to the ERA5 screening curve.
    if inputs.get('baseline_id')!=BASELINE_ID or candidate.get('base_expected_cf_source')!=BASELINE_ID:
        raise ValueError('The candidate resource baseline differs from the validated RARE baseline.')
    features=inputs.get('features',{})
    if any(not isinstance(features.get(name),(int,float)) or not math.isfinite(features[name]) for name in feature_names):
        raise ValueError('Required historical weather or terrain features are missing.')
    if 'isd_coverage_fraction' in feature_names and (features['isd_coverage_fraction']<.7 or features['isd_station_count']<1 or features['isd_nearest_station_km']>100):
        raise ValueError('Nearby ground weather observations do not meet coverage requirements.')
    base=features['resource_expected_capacity_factor']
    if not 0<=base<=1 or abs(base-candidate.get('base_expected_cf',-1))>1e-6:
        raise ValueError('The inference baseline does not match the candidate estimate.')
    row=np.array([[features[name] for name in feature_names]],dtype=float)
    minimum=np.array([manifest['feature_min'][name] for name in feature_names])
    maximum=np.array([manifest['feature_max'][name] for name in feature_names])
    scale=np.maximum(maximum-minimum,.001)
    outside=np.maximum(np.maximum(minimum-row[0],row[0]-maximum),0)/scale
    if np.max(outside)>.15: raise ValueError('Candidate features are outside the validated training range.')
    residual=float(point.predict(row)[0]);corrected=base+residual
    if not math.isfinite(residual) or not 0<=corrected<=1:
        raise ValueError('Predicted capacity factor is outside physical bounds.')
    shap_values=np.asarray(explainer.shap_values(row))[0]
    bias=float(np.asarray(explainer.expected_value).reshape(-1)[0])
    if not np.isclose(shap_values.sum()+bias,residual,atol=1e-6):
        raise ValueError('The explanation does not reconcile with the prediction.')
    low=high=None
    if intervals:
        low_res=float(intervals[0].predict(row)[0]);high_res=float(intervals[1].predict(row)[0])
        if not all(math.isfinite(x) for x in [low_res,high_res]) or not low_res<=residual<=high_res or high_res-low_res>.25:
            raise ValueError('The prediction interval is too wide or inconsistent.')
        low=max(0.,base+low_res);high=min(1.,base+high_res)
    factors=sorted(zip(feature_names,shap_values),key=lambda item:abs(item[1]),reverse=True)
    top=[{'feature':key,'contribution_cf':float(value)} for key,value in factors[:4]]
    top.append({'feature':'Other features','contribution_cf':float(sum(value for _,value in factors[4:]))})
    return MLFields(ml_enabled=True,ml_model_version=manifest['model_version'],ml_base_expected_cf=base,
            ml_predicted_residual_cf=residual,ml_corrected_expected_cf=corrected,
            ml_interval_low_cf=low,ml_interval_high_cf=high,ml_confidence='HIGH' if intervals else 'MEDIUM',
            ml_top_factors=top,ml_training_similarity_score=float(max(0,1-np.max(outside))),
            ml_adjustment_cf=residual,ml_explanation_bias_cf=bias).model_dump()


def enrich(candidates,plan):
    requested=bool(plan.historical_intelligence)
    loaded=None;unavailable=None
    if requested and enabled() and plan.mode=='live':
        try: loaded=bundle()
        except Exception: unavailable='No validated model could be loaded. Base estimate retained.'
    output=[]
    for raw in candidates:
        fields=MLFields().model_dump()
        if requested:
            reason=('Historical Intelligence is disabled on the server.' if not enabled() else
                    'Historical corrections are not applied to synthetic demonstration sites.' if plan.mode!='live' else
                    'The historical model supports wind only.' if raw['technology']!='wind' else unavailable)
            if not reason:
                try: fields=infer(raw,loaded)
                except ValueError as error: reason=str(error)
                except Exception: reason='Historical inference was unavailable. Base estimate retained.'
            if reason: fields.update(ml_confidence='LOW',ml_fallback_reason=reason)
        output.append({**raw,**fields})
    return output


def apply_correction(raw,requested):
    """Pure scoring hook; zero adjustment leaves all physical measurements unchanged."""
    candidate={**raw,'components':dict(raw['components'])}
    base_resource=raw.get('base_resource_score',raw['components']['resource'])
    base_generation=raw.get('base_annual_gwh',raw['annual_gwh'])
    candidate['base_resource_score']=base_resource;candidate['base_annual_gwh']=base_generation
    candidate['components']['resource']=base_resource;candidate['annual_gwh']=base_generation
    if not requested:
        candidate.update(ml_enabled=False,ml_confidence=None,ml_fallback_reason=None)
    if requested and raw.get('ml_enabled') and raw.get('ml_confidence') in {'HIGH','MEDIUM'} and raw['technology']=='wind':
        corrected=raw.get('ml_corrected_expected_cf')
        if isinstance(corrected,(int,float)) and math.isfinite(corrected) and 0<=corrected<=1:
            # Inverse of the existing wind CF curve, then the existing resource normalization.
            candidate['components']['resource']=round(max(0,min(100,(corrected/.075-1)*20)),2)
            candidate['annual_gwh']=round(raw['capacity_mw']*8760*corrected/1000,1)
    return candidate
