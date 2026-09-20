"""ISD/PUDL fusion candidates. Development search is distinct from fresh evaluation."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler,SplineTransformer
from sklearn.linear_model import Ridge,HuberRegressor
from sklearn.ensemble import HistGradientBoostingRegressor,ExtraTreesRegressor
from sklearn.kernel_approximation import Nystroem
from lightgbm import LGBMRegressor
from threadpoolctl import threadpool_limits
from scripts.train_isd_pudl_wind import ISD_FEATURES,DERIVED,add_features,write,folds,digest
from backend.ml.schema import ATLAS_FEATURES,QualityGateError
from backend.ml.retraining import weights,block_mae
from backend.ml.training import metrics

CORE=['resource_expected_capacity_factor','era5_power_proxy_cf','era5_wind100_mean_mps','era5_wind100_std_mps','gwa_wind_ratio','gwa_wind100_mean_2km_mps','era5_temperature_c','month_sin','month_cos']
WEATHER=['isd_mean_wind_mps','isd_wind_std_mps','isd_wind_p10_mps','isd_wind_p90_mps','isd_mean_temp_c']
FEATURES={'atlas':ATLAS_FEATURES,'all':ATLAS_FEATURES+ISD_FEATURES+DERIVED,'core':CORE+ISD_FEATURES+DERIVED,
          'station':['resource_expected_capacity_factor','gwa_wind100_mean_2km_mps','elevation_m','month_sin','month_cos']+ISD_FEATURES}
SPECS=[{'method':'baseline','features':'atlas'}]
SPECS += [{'method':'spline','features':f,'alpha':a} for f in ['core','station'] for a in [10,100,1000]]
SPECS += [{'method':'kernel','features':'core','gamma':g,'alpha':a} for g in [.02,.08] for a in [1,10]]
SPECS += [{'method':'hist','features':f,'leaves':l} for f in ['core','all'] for l in [7,15]]
SPECS += [{'method':'robust','features':f,'alpha':a} for f in ['core','station'] for a in [10,100]]
SPECS += [{'method':'boosted_correction','features':'core','shrink':s} for s in [.2,.5,1.0]]

class Fusion:
 def __init__(self,spec,without_isd=False):self.spec=spec;self.without_isd=without_isd
 def columns(self):
  columns=FEATURES[self.spec['features']]
  return [c for c in columns if not (self.without_isd and (c.startswith('isd_') or c in DERIVED))]
 def fit(self,frame):
  s=self.spec;self.features=self.columns();name=s['method'];w=weights(frame);y=frame.residual_target
  with threadpool_limits(limits=1):
   if name=='baseline':self.model=make_pipeline(StandardScaler(),HuberRegressor(alpha=1,epsilon=1.1,max_iter=2500));key='huberregressor__sample_weight'
   elif name=='robust':self.model=make_pipeline(StandardScaler(),HuberRegressor(alpha=s['alpha'],epsilon=1.1,max_iter=2500));key='huberregressor__sample_weight'
   elif name=='spline':self.model=make_pipeline(SplineTransformer(n_knots=4,degree=2,include_bias=False,extrapolation='linear'),StandardScaler(),Ridge(alpha=s['alpha']));key='ridge__sample_weight'
   elif name=='kernel':self.model=make_pipeline(StandardScaler(),Nystroem(gamma=s['gamma'],n_components=160,random_state=42),Ridge(alpha=s['alpha']));key='ridge__sample_weight'
   elif name=='hist':self.model=HistGradientBoostingRegressor(loss='absolute_error',max_iter=220,learning_rate=.04,max_leaf_nodes=s['leaves'],min_samples_leaf=80,l2_regularization=10,early_stopping=False,random_state=42);key='sample_weight'
   elif name=='boosted_correction':
    self.base=Fusion({'method':'baseline','features':'atlas'}).fit(frame)
    cross=np.zeros(len(frame))
    for a,b in folds(frame,3):cross[b]=Fusion({'method':'baseline','features':'atlas'}).fit(frame.iloc[a]).predict(frame.iloc[b])
    # Train the correction on out-of-block baseline errors, not overfit residuals.
    correction=frame.copy();correction['base_prediction']=cross
    self.features=self.features+['base_prediction']
    self.model=LGBMRegressor(objective='regression_l1',n_estimators=180,num_leaves=7,min_child_samples=120,learning_rate=.025,reg_lambda=30,n_jobs=3,verbosity=-1,random_state=42,deterministic=True,force_col_wise=True)
    self.model.fit(correction[self.features],y-cross,sample_weight=w);return self
   else:raise ValueError(name)
   self.model.fit(frame[self.features],y,**{key:w})
  return self
 def predict(self,frame):
  with threadpool_limits(limits=1):
   if self.spec['method']=='boosted_correction':
    base=self.base.predict(frame);x=frame.copy();x['base_prediction']=base
    return base+self.spec['shrink']*self.model.predict(x[self.features])
   return self.model.predict(frame[self.features])

def search(root,frame):
 root.mkdir(parents=True,exist_ok=True)
 config={'specifications':SPECS,'source_sha256':digest(Path('data/private/ml-v5-isd/training.parquet')),
         'purpose':'Adaptive development search only. Do not interpret this reused CV as fresh validation. Final Midwest tests stay unopened.'}
 if (root/'search-plan.json').exists() and json.loads((root/'search-plan.json').read_text())!=config:raise QualityGateError('Search plan changed.')
 if not (root/'search-plan.json').exists():write(root/'search-plan.json',config)
 predictions={};results=[]
 for index,spec in enumerate(SPECS):
  path=root/f'candidate-{index:02d}.npz'
  if path.exists():pred=np.load(path)['prediction']
  else:
   pred=np.zeros(len(frame))
   for a,b in folds(frame,5):pred[b]=Fusion(spec).fit(frame.iloc[a]).predict(frame.iloc[b])
   np.savez_compressed(path,prediction=pred)
  predictions[index]=pred
  score={'index':index,'spec':spec,**metrics(frame,pred),'block_mae':block_mae(frame,pred)};results.append(score)
  write(root/'progress.json',{'completed':len(results),'total':len(SPECS),'results':results})
  print(f'{index+1}/{len(SPECS)} {spec}: row={score["mae_cf_points"]:.4f} block={score["block_mae"]:.4f}',flush=True)
 # Fixed shrinkage ensembles retain a meaningful station-model contribution.
 for i in range(1,len(SPECS)):
  for fraction in [.25,.5,.75]:
   pred=(1-fraction)*predictions[0]+fraction*predictions[i]
   results.append({'index':i,'blend':fraction,'spec':SPECS[i],**metrics(frame,pred),'block_mae':block_mae(frame,pred)})
 ranked=sorted(results[1:],key=lambda r:r['block_mae'])
 write(root/'selection.json',{'ranking':ranked,'baseline':results[0],'selected':ranked[0],'production_eligible':False})
 print('Best development candidate: '+json.dumps(ranked[0]),flush=True)

if __name__=='__main__':search(Path('data/private/ml-v6-fusion/development'),pd.read_parquet('data/private/ml-v5-isd/training.parquet'))
