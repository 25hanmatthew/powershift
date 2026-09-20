"""Learn explainable within-plant weather responses without target-year baselines."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge, HuberRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler, SplineTransformer
from threadpoolctl import threadpool_limits

from backend.ml.schema import QualityGateError
from backend.ml.retraining import weights
from backend.ml.protocol import geographic_blocks
from backend.ml.linear_model import payload_from_pipeline
from scripts.evaluate_wind_fusion import spline_payload
from scripts.wind_insight_data import ROOT, freeze, RAW
from scripts.station_normalized_wind import NORMALIZED
from scripts.train_isd_pudl_wind import write, digest

WEATHER = RAW+NORMALIZED+['cold_degrees']
GROUPS = {
    'raw': ['raw_wind_delta'],
    'wind': ['wind_delta'],
    'shape': ['wind_delta','std_delta','p10_delta','p90_delta'],
    'all': ['wind_delta','std_delta','p10_delta','p90_delta','temp_delta','cold_delta'],
    'interactions': ['wind_delta','std_delta','p10_delta','p90_delta','temp_delta','cold_delta','wind_cf','wind_nonlinear'],
}
SPECS = [dict(group=g,method=m,alpha=a) for g in GROUPS for m,a in [('ridge',10),('ridge',100),('huber',10),('spline',100)]]


def decorate(frame):
    frame = frame.copy()
    frame['calendar_month'] = frame.report_month.dt.month
    frame['cold_degrees'] = np.maximum(-frame.isd_mean_temp_c,0)
    return frame


def reference(frame):
    if not frame.report_month.dt.year.between(2019,2022).all():
        raise QualityGateError('Historical baseline includes a year outside training.')
    frame = decorate(frame)
    values = ['actual_capacity_factor']+WEATHER
    grouped = frame.groupby(['plant_id_eia','calendar_month'])
    sums = grouped[values].sum().add_prefix('sum_')
    sums['reference_count'] = grouped.size()
    return sums.reset_index()


def prepare_rows(query, history, leave_self_out=False, minimum=3):
    query = decorate(query)
    result = query.merge(reference(history),on=['plant_id_eia','calendar_month'],validate='many_to_one')
    n = result.reference_count.to_numpy() - int(leave_self_out)
    result = result[n>=minimum].copy()
    n = result.reference_count.to_numpy() - int(leave_self_out)
    for name in ['actual_capacity_factor']+WEATHER:
        total = result['sum_'+name].to_numpy()
        if leave_self_out:
            total = total-result[name].to_numpy()
        result['reference_'+name] = total/n
    result['baseline_cf'] = result.reference_actual_capacity_factor
    result['cf_anomaly'] = result.actual_capacity_factor-result.baseline_cf
    mapping = {'wind_delta':'station_wind_ratio','std_delta':'station_std_ratio','p10_delta':'station_p10_ratio',
               'p90_delta':'station_p90_ratio','temp_delta':'station_temp_anomaly','raw_wind_delta':'isd_mean_wind_mps',
               'temperature_delta_c':'isd_mean_temp_c','cold_delta':'cold_degrees'}
    for target,source in mapping.items():
        result[target] = result[source]-result['reference_'+source]
    result['wind_cf'] = result.wind_delta*result.baseline_cf
    result['wind_nonlinear'] = result.wind_delta*np.abs(result.wind_delta)
    if not np.isfinite(result[list(dict.fromkeys(sum(GROUPS.values(),[])))+['baseline_cf','cf_anomaly']]).all(axis=None):
        raise QualityGateError('Nonfinite insight features.')
    return result


class InsightModel:
    def __init__(self,spec):
        self.spec = spec
        self.features = GROUPS[spec['group']]

    def fit(self,frame):
        s = self.spec
        if s['method']=='spline':
            self.model = make_pipeline(SplineTransformer(n_knots=4,degree=2,include_bias=False,extrapolation='linear'),StandardScaler(),Ridge(alpha=s['alpha']))
            key = 'ridge__sample_weight'
        elif s['method']=='huber':
            self.model = make_pipeline(StandardScaler(),HuberRegressor(alpha=s['alpha'],epsilon=1.35,max_iter=2000))
            key = 'huberregressor__sample_weight'
        else:
            self.model = make_pipeline(StandardScaler(),Ridge(alpha=s['alpha']))
            key = 'ridge__sample_weight'
        with threadpool_limits(limits=1):
            self.model.fit(frame[self.features],frame.cf_anomaly,**{key:weights(frame)})
        return self

    def predict(self,frame):
        with threadpool_limits(limits=1):
            return np.clip(frame.baseline_cf.to_numpy()+self.model.predict(frame[self.features]),0,1)

    def export(self,frame):
        return {'spec':self.spec,'format':'spline' if self.spec['method']=='spline' else 'linear',
                'parameters':spline_payload(self) if self.spec['method']=='spline' else payload_from_pipeline(self.model,frame,self.features)}


def metrics(frame,prediction):
    error = np.abs(frame.actual_capacity_factor.to_numpy()-prediction)
    plants = frame[['plant_id_eia']].assign(error=error).groupby('plant_id_eia').error.mean()
    return {'rows':len(frame),'plants':int(frame.plant_id_eia.nunique()),'mae_cf_points':float(error.mean()*100),
            'macro_plant_mae_cf_points':float(plants.mean()*100),
            'rmse_cf_points':float(np.sqrt(np.mean((frame.actual_capacity_factor.to_numpy()-prediction)**2))*100)}


def train():
    freeze()
    if (ROOT/'evaluation-started.json').exists():
        raise QualityGateError('2024 test already opened; no model reselection.')
    history = pd.read_parquet(ROOT/'history.parquet')
    plan = {'specifications':SPECS,'history_sha256':digest(ROOT/'history.parquet'),'selection':'Minimum equal-plant MAE across four leave-year-out folds. Select overall weather, wind-only, and raw-wind models separately.',
        'features':GROUPS,'minimum_reference_years_for_final':3,'model_script_sha256':digest(Path(__file__)),
        'task':'Operating-site weather adjustment. Baseline uses only historical PUDL values; observed target-year ISD is needed at inference.'}
    path = ROOT/'training-plan.json'
    if path.exists() and json.loads(path.read_text())!=plan:
        raise QualityGateError('Historical model-search plan changed.')
    if not path.exists():
        write(path,plan)
    partitions = []
    for year in [2019,2020,2021,2022]:
        previous = history[history.report_month.dt.year.ne(year)]
        fitting = prepare_rows(previous,previous,leave_self_out=True,minimum=2)
        validation = prepare_rows(history[history.report_month.dt.year.eq(year)],previous,minimum=2)
        if fitting.plant_id_eia.nunique()<80 or len(validation)<500:
            raise QualityGateError('Historical year fold is too small.')
        partitions.append((fitting,validation))
    validation = pd.concat([p[1] for p in partitions],ignore_index=True)
    results = []
    for i,spec in enumerate(SPECS):
        pred = np.concatenate([InsightModel(spec).fit(fit).predict(valid) for fit,valid in partitions])
        np.savez_compressed(ROOT/f'development-{i:02d}.npz',prediction=pred)
        entry = {'index':i,'spec':spec,**metrics(validation,pred)}
        results.append(entry)
        print(f'Weather response {i+1}/{len(SPECS)}: {spec}, MAE={entry["mae_cf_points"]:.3f}',flush=True)
    selected = {
        'weather':min([r for r in results if r['spec']['group']!='raw'],key=lambda r:r['macro_plant_mae_cf_points']),
        'wind_only':min([r for r in results if r['spec']['group']=='wind'],key=lambda r:r['macro_plant_mae_cf_points']),
        'raw_wind':min([r for r in results if r['spec']['group']=='raw'],key=lambda r:r['macro_plant_mae_cf_points'])}
    selection = {'selected':selected,'ranking':sorted(results,key=lambda r:r['macro_plant_mae_cf_points']),
                 'seasonal_baseline':metrics(validation,validation.baseline_cf.to_numpy()),'fresh_validation':False}
    write(ROOT/'selection.json',selection)
    validation.to_parquet(ROOT/'development-rows.parquet',index=False)
    fitting = prepare_rows(history,history,leave_self_out=True,minimum=2)
    models = {name:InsightModel(entry['spec']).fit(fitting).export(fitting) for name,entry in selected.items()}
    write(ROOT/'research-weather-models.json',{'production_eligible':False,'task':plan['task'],'models':models,'training_rows':len(fitting),'training_plants':int(fitting.plant_id_eia.nunique())})
    reference(history).to_parquet(ROOT/'seasonal-reference.parquet',index=False)
    print('Selected weather-response models: '+json.dumps(selected),flush=True)


if __name__=='__main__':
    train()
