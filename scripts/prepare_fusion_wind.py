"""Reserve previously unexamined Midwest plants for one-shot wind-fusion evaluation."""
import hashlib,json,shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from dotenv import load_dotenv
from backend.ml import data,plant_data,protocol
from backend.ml.atlas_data import site_atlas
from backend.ml.schema import PHYSICAL_FEATURES,ATLAS_FEATURES,QualityGateError
from scripts.train_isd_pudl_wind import add_features,write,digest

ROOT=Path('data/private/ml-v6-fusion')
STATES=['IA','MN','IL']

def prepare():
 load_dotenv('.env');ROOT.mkdir(exist_ok=True)
 if (ROOT/'evaluation-started.json').exists():raise QualityGateError('Final evaluation already opened.')
 new=ROOT/'fresh';new.mkdir(exist_ok=True)
 if (new/'complete.parquet').exists():return pd.read_parquet(new/'complete.parquet')
 print('Preparing untouched Midwest labels',flush=True)
 previous=Path('data/private/ml-v3-plant')
 path=new/'source-generators.parquet'
 if not path.exists():
  con=data.connection()
  frame=con.execute("""SELECT plant_id_eia,generator_id,report_date AS report_month,capacity_mw,
   latitude,longitude,state,county,technology_description,generator_operating_date,generator_retirement_date
   FROM read_parquet(?) WHERE state IN ('IA','MN','IL') AND report_date>='2019-01-01' AND report_date<'2024-01-01'""",[data.PUDL+'out_eia__monthly_generators.parquet']).df()
  frame.to_parquet(path,index=False);con.close()
 label_path=new/'labels.parquet'
 if label_path.exists():frame=pd.read_parquet(label_path)
 else:
  frame=plant_data.clean_labels(pd.read_parquet(path),pd.read_parquet(previous/'source-generation.parquet'),pd.read_parquet(previous/'source-reporting.parquet'))
  old=set(pd.read_parquet('data/private/ml-v4/labels.parquet').plant_id_eia)
  if set(frame.plant_id_eia)&old:raise QualityGateError('New evaluation plants already examined.')
  # Require historical coverage without selecting on measured model error.
  months=frame[frame.report_month.dt.year.lt(2023)].groupby('plant_id_eia').size()
  future=set(frame[frame.report_month.dt.year.eq(2023)].plant_id_eia)
  plants=frame.drop_duplicates('plant_id_eia');plants=plants[plants.plant_id_eia.map(months).ge(24)&plants.plant_id_eia.isin(future)]
  chosen=[]
  for _,group in plants.groupby('state'):
   chosen+=sorted(group.plant_id_eia,key=lambda x:hashlib.sha256(f'fusion-v6:{x}'.encode()).hexdigest())[:30]
  frame=frame[frame.plant_id_eia.isin(chosen)].copy();frame.to_parquet(label_path,index=False)
  combined=pd.concat([pd.read_parquet('data/private/ml-v5-isd/training.parquet'),frame],ignore_index=True)
  design=protocol.freeze(combined,old,ROOT/'split-protocol.json',version='wind-fusion-v6',training_years=[2019,2020,2021,2022])
  write(ROOT/'source-config.json',{'states':STATES,'maximum_plants_per_state':30,'pudl_version':data.PUDL_VERSION,'fresh_labels_sha256':digest(label_path),'old_v4_evaluation_sha256':digest(Path('data/private/ml-v4/evaluation.json')),'old_v5_evaluation_sha256':digest(Path('data/private/ml-v5-isd/evaluation.json'))})
 print(f'Fresh labels: {len(frame)} months, {frame.plant_id_eia.nunique()} plants',flush=True)
 rare=new/'rare-monthly.parquet'
 if not rare.exists():
  print('Reading matched Midwest PUDL / VCE resource baseline',flush=True)
  con=data.connection()
  table=con.execute("""SELECT state,place_name,county_id_fips,date_trunc('month',datetime_utc) AS report_month,
   avg(capacity_factor_onshore_wind) AS resource_expected_capacity_factor,count(capacity_factor_onshore_wind) AS baseline_hours
   FROM read_parquet(?) WHERE state IN ('IA','MN','IL') AND report_year BETWEEN 2019 AND 2023 AND county_id_fips IS NOT NULL GROUP BY ALL""",[data.PUDL+'out_vcerare__hourly_available_capacity_factor.parquet']).df();con.close()
  table['county_key']=table.place_name.str.lower().str.replace(r'[^a-z]','',regex=True).str.removesuffix('county');table.to_parquet(rare,index=False)
 frame,_=data.rare(new,frame)
 if not (new/'isd-history.csv').exists():shutil.copy2(previous/'isd-history.csv',new/'isd-history.csv')
 (new/'isd').mkdir(exist_ok=True)
 for cache in [previous/'isd',Path('data/private/ml-v5-isd/isd')]:
  for file in cache.glob('*.parquet'):
   if not (new/'isd'/file.name).exists():shutil.copy2(file,new/'isd'/file.name)
 print('Preparing ISD and ERA5 for reserved plants',flush=True)
 with ThreadPoolExecutor(max_workers=2) as pool:
  weather=pool.submit(data.weather,new,frame,years=[2019,2020,2021,2022,2023],max_workers=6,prefer_archive=True)
  wind=plant_data.site_wind(new,frame)
  observed=weather.result()
 frame=wind.merge(observed[['plant_id_eia','report_month']+[c for c in observed if c.startswith('isd_')]],on=['plant_id_eia','report_month'],validate='one_to_one')
 print('Sampling terrain and Wind Atlas',flush=True)
 frame,_=data.terrain(new,frame,required_features=PHYSICAL_FEATURES)
 frame=add_features(site_atlas(new,frame));frame.to_parquet(new/'complete.parquet',index=False)
 combined=pd.concat([pd.read_parquet('data/private/ml-v5-isd/training.parquet'),frame],ignore_index=True)
 design=json.loads((ROOT/'split-protocol.json').read_text())
 train,spatial,temporal=protocol.split(combined,design)
 combined.to_parquet(ROOT/'complete.parquet',index=False)
 print('Prepared splits '+str([(len(x),x.plant_id_eia.nunique()) for x in [train,spatial,temporal]]),flush=True)
 return frame

if __name__=='__main__':prepare()
