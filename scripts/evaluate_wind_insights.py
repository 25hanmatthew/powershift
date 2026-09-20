"""One-time 2024 evaluation of frozen ISD/PUDL models and prespecified findings."""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.ml.linear_model import LinearModel
from backend.ml.protocol import geographic_blocks
from backend.ml.schema import QualityGateError
from scripts.evaluate_wind_fusion import PortableSpline
from scripts.train_wind_insights import prepare_rows, metrics, InsightModel
from scripts.wind_insight_data import ROOT
from scripts.train_isd_pudl_wind import write, digest

DRAWS = 4000
ALPHA = .05/3


def predict(model,frame):
    fitted = PortableSpline(model['parameters']) if model['format']=='spline' else LinearModel(model['parameters'])
    residual = fitted.predict(frame) if model['format']=='spline' else fitted.predict(frame[fitted.features])
    return np.clip(frame.baseline_cf.to_numpy()+residual,0,1)


def bootstrap_gain(frame,baseline,prediction,group='block'):
    actual = frame.actual_capacity_factor.to_numpy()
    key = geographic_blocks(frame) if group=='block' else frame.state
    totals = pd.DataFrame({'group':key.to_numpy(),'base':np.abs(actual-baseline),'model':np.abs(actual-prediction)}).groupby('group').sum()
    rng = np.random.default_rng(1948)
    draws = rng.integers(0,len(totals),size=(DRAWS,len(totals)))
    gains = 100*(1-totals.model.to_numpy()[draws].sum(axis=1)/totals.base.to_numpy()[draws].sum(axis=1))
    return {'gain_pct':float(100*(1-totals.model.sum()/totals.base.sum())),
            'lower_pct':float(np.quantile(gains,ALPHA/2)),'upper_pct':float(np.quantile(gains,1-ALPHA/2)),
            'confidence':1-ALPHA,'clusters':len(totals),'cluster_unit':group}


def paired_contrast(frame,event,normal,outcome):
    positive = frame[event].groupby('plant_id_eia')[outcome].mean()
    reference = frame[normal].groupby('plant_id_eia')[outcome].mean()
    pair = pd.concat([positive.rename('event'),reference.rename('normal')],axis=1).dropna()
    pair['difference'] = (pair.event-pair.normal)*100
    metadata = frame.drop_duplicates('plant_id_eia').set_index('plant_id_eia')
    pair['block'] = geographic_blocks(metadata.loc[pair.index]).to_numpy()
    pair['state'] = metadata.loc[pair.index,'state']
    output = {'paired_plants':len(pair),'blocks':int(pair.block.nunique()),'event_months':int(event.sum()),'normal_months':int(normal.sum())}
    if len(pair)<30 or pair.block.nunique()<5:
        return {**output,'status':'insufficient_coverage'},pair.reset_index()
    totals = pair.groupby('block').difference.agg(['sum','count'])
    draws = np.random.default_rng(4821).integers(0,len(totals),size=(DRAWS,len(totals)))
    values = totals['sum'].to_numpy()[draws].sum(axis=1)/totals['count'].to_numpy()[draws].sum(axis=1)
    lower,upper = np.quantile(values,[ALPHA/2,1-ALPHA/2])
    return {**output,'difference_cf_points':float(pair.difference.mean()),'event_cf_points':float(pair.event.mean()*100),
            'normal_cf_points':float(pair.normal.mean()*100),'lower_cf_points':float(lower),'upper_cf_points':float(upper),
            'confidence':1-ALPHA,'negative_difference_supported':bool(upper<0),'status':'measured'},pair.reset_index()


def freeze():
    files = [Path(__file__),Path('scripts/train_wind_insights.py'),Path('scripts/wind_insight_data.py'),
             ROOT/'study-plan.json',ROOT/'training-plan.json',ROOT/'selection.json',ROOT/'research-weather-models.json']
    plan = {'hashes':{str(p):digest(p) for p in files},'test_year':2024,'primary_tests':3,'bootstrap_draws':DRAWS,
        'alpha_per_primary_test':ALPHA,'minimum_paired_plants':30,'minimum_contrast_blocks':5,
        'H1_pass':'Weather-model MAE reduction with a positive Bonferroni-adjusted geographic-block interval.',
        'H2_H3_pass':'Negative within-plant contrast with a negative upper Bonferroni-adjusted geographic-block interval.',
        'secondary':'Wind/raw model comparisons, state-level sensitivity, descriptive bins and unexplained shortfall counts are secondary; no additional primary claims.',
        'freshness':'2024 never used by previous model development; existing plants are intentionally known. No new-location generalization claim.'}
    path = ROOT/'evaluation-plan.json'
    if path.exists() and json.loads(path.read_text())!=plan:
        raise QualityGateError('Frozen insight evaluation changed.')
    if not path.exists():
        write(path,plan)
    return plan


def evaluate():
    freeze()
    if (ROOT/'evaluation-started.json').exists():
        raise QualityGateError('2024 insights already evaluated. Retain the measured result.')
    history = pd.read_parquet(ROOT/'history.parquet')
    raw = pd.read_parquet(ROOT/'test-2024.parquet')
    if not raw.report_month.dt.year.eq(2024).all():
        raise QualityGateError('Wrong final test year.')
    test = prepare_rows(raw,history,minimum=3)
    if len(test)<1200 or test.plant_id_eia.nunique()<100:
        raise QualityGateError('Too little qualified 2024 baseline coverage.')
    artifact = json.loads((ROOT/'research-weather-models.json').read_text())
    fit = prepare_rows(history,history,leave_self_out=True,minimum=2)
    for name,model in artifact['models'].items():
        rebuilt = InsightModel(model['spec']).fit(fit)
        if not np.allclose(predict(model,fit),rebuilt.predict(fit),atol=1e-9,rtol=1e-9):
            raise QualityGateError('Exported weather-response model parity failed.')
    write(ROOT/'evaluation-started.json',{'started_at':datetime.now(timezone.utc).isoformat(),
        'test_data_sha256':digest(ROOT/'test-2024.parquet'),'plan_sha256':digest(ROOT/'evaluation-plan.json'),
        'model_sha256':digest(ROOT/'research-weather-models.json')})
    estimates = {'pudl_seasonal':test.baseline_cf.to_numpy()}
    estimates.update({name:predict(model,test) for name,model in artifact['models'].items()})
    last = history[history.report_month.dt.year.eq(2022)].assign(calendar_month=lambda x:x.report_month.dt.month)
    lookup = last.set_index(['plant_id_eia','calendar_month']).actual_capacity_factor
    recent = pd.MultiIndex.from_frame(test[['plant_id_eia','calendar_month']]).map(lookup).to_numpy(dtype=float)
    scores = {name:metrics(test,pred) for name,pred in estimates.items()}
    if np.isfinite(recent).all():
        scores['last_observed_2022'] = metrics(test,recent)
        estimates['last_observed_2022'] = recent
    for name,pred in estimates.items():
        test['prediction_'+name] = pred
    test['wind_adjusted_error'] = test.actual_capacity_factor-test.prediction_wind_only
    test['weather_adjusted_error'] = test.actual_capacity_factor-test.prediction_weather
    h1 = bootstrap_gain(test,estimates['pudl_seasonal'],estimates['weather'])
    h1['supported'] = h1['lower_pct']>0
    h2,pairs2 = paired_contrast(test,test.wind_delta.le(-.15),test.wind_delta.abs().le(.05),'cf_anomaly')
    h3,pairs3 = paired_contrast(test,test.temperature_delta_c.le(-3),test.temperature_delta_c.abs().le(1),'wind_adjusted_error')
    pairs2.to_parquet(ROOT/'wind-contrast-pairs.parquet',index=False)
    pairs3.to_parquet(ROOT/'cold-contrast-pairs.parquet',index=False)
    bin_names = ['Much less wind','Less wind','Near normal','More wind','Much more wind']
    test['wind_band'] = pd.cut(test.wind_delta,[-np.inf,-.15,-.05,.05,.15,np.inf],labels=bin_names)
    bins = []
    for name in bin_names:
        part = test[test.wind_band.eq(name)]
        per_plant = part.groupby('plant_id_eia').cf_anomaly.mean()
        bins.append({'band':name,'months':len(part),'plants':len(per_plant),'mean_cf_anomaly_points':float(per_plant.mean()*100) if len(per_plant) else None})
    unexplained = test[test.cf_anomaly.lt(-.10)]
    unexplained_count = int(unexplained.weather_adjusted_error.lt(-.10).sum())
    states = {state:{name:metrics(part,part['prediction_'+name].to_numpy()) for name in ['pudl_seasonal','weather','wind_only']} for state,part in test.groupby('state')}
    result = {'version':9,'test_year':2024,'rows':len(test),'plants':int(test.plant_id_eia.nunique()),'states':int(test.state.nunique()),
        'blocks':int(geographic_blocks(test).nunique()),'historical_training_rows':len(fit),'historical_training_plants':int(fit.plant_id_eia.nunique()),
        'scores':scores,'primary_hypotheses':{'H1':h1,'H2':h2,'H3':h3},'wind_bins_descriptive':bins,
        'state_cluster_sensitivity':bootstrap_gain(test,estimates['pudl_seasonal'],estimates['weather'],group='state'),
        'weather_vs_wind_only':bootstrap_gain(test,estimates['wind_only'],estimates['weather']),
        'normalized_vs_raw_wind':bootstrap_gain(test,estimates['raw_wind'],estimates['wind_only']),
        'unexplained_shortfalls_descriptive':{'months_below_historical_cf_by_10_points':len(unexplained),'months_still_below_weather_model_by_10_points':unexplained_count,
            'note':'Unexplained by this model; cannot identify curtailment, outages, icing, or turbine faults from these data alone.'},
        'by_state':states,'live_scoring_enabled':False,'new_site_model':False,
        'limitations':['Observed target-month weather is required: retrospective adjustment, not an advance weather forecast.',
                       'Known operating plants with at least three years of same-month history; not rooftop or new-site evidence.',
                       'One fresh year. Geographic dependence and plant operational changes remain. These are associations, not causal proof.',
                       'Primary intervals control three prespecified comparisons approximately by Bonferroni-adjusted cluster bootstrap; secondary findings are exploratory.']}
    test.to_parquet(ROOT/'predictions-2024.parquet',index=False)
    write(ROOT/'evaluation.json',result)
    print(json.dumps({k:result[k] for k in ['rows','plants','states','scores','primary_hypotheses','normalized_vs_raw_wind','unexplained_shortfalls_descriptive']},indent=2),flush=True)


if __name__=='__main__':
    evaluate()
