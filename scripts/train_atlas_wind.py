"""Revision 4: finer site wind climatology and previously unexamined plants."""
import hashlib
import json
import shutil
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from backend.ml import data,plant_data,protocol
from backend.ml.atlas_data import site_atlas
from backend.ml.schema import ATLAS_FEATURES,ATLAS_SCHEMA_VERSION,PHYSICAL_FEATURES,QualityGateError


def main():
    load_dotenv()
    root=Path('data/private/ml-v4');root.mkdir(exist_ok=True)
    previous=Path('data/private/ml-v3-plant')
    if (root/'evaluation-started.json').exists(): raise QualityGateError('Final tests already opened; preserve the experiment.')
    report={'status':'running','revision':4,'phases':[],'production_eligible':False,'technology':'wind'}
    def save():
        temporary=root/'pipeline-report.tmp'
        temporary.write_text(json.dumps(report,indent=2),encoding='utf-8');temporary.replace(root/'pipeline-report.json')
    def stage(phase,detail):
        report.update(phase=phase,detail=detail);save();print(f'{phase}: {detail}',flush=True)
    try:
        stage('V1','Preparing expanded reported plant labels and freezing untouched tests')
        if not (root/'labels.parquet').exists():
            labels=plant_data.clean_labels(*[pd.read_parquet(previous/name) for name in ['source-generators.parquet','source-generation.parquet','source-reporting.parquet']])
            labels.to_parquet(root/'labels.parquet',index=False)
        frame=pd.read_parquet(root/'labels.parquet')
        old=set()
        for directory in ['ml','ml-v2','ml-v3-plant']:
            old.update(pd.read_parquet(Path('data/private')/directory/'labels.parquet').plant_id_eia)
        path=root/'protocol.json'
        if path.exists(): design=json.loads(path.read_text(encoding='utf-8'))
        else:
            plants=frame.drop_duplicates('plant_id_eia').copy();plants['block']=protocol.geographic_blocks(plants)
            counts=frame[frame.report_month.dt.year.lt(2023)].groupby('plant_id_eia').size()
            eligible=plants[~plants.plant_id_eia.isin(old)&plants.plant_id_eia.map(counts).fillna(0).ge(12)]
            blocks=eligible.groupby('block').size();blocks=blocks[blocks.ge(3)].index.tolist()
            ordered=sorted(blocks,key=lambda b:hashlib.sha256(f'wind-atlas-v4:{b}'.encode()).hexdigest())
            selected=ordered[:max(3,int(np.ceil(len(ordered)*.25)))]
            fresh=plants[~plants.plant_id_eia.isin(old)]
            design={'version':'wind-atlas-v4','revision':4,'schema_version':ATLAS_SCHEMA_VERSION,
                    'created_at':datetime.now(timezone.utc).isoformat(),
                    'previously_examined_plants':sorted(int(x) for x in old),'spatial_blocks':selected,
                    'spatial_test_plants':sorted(int(x) for x in fresh[fresh.block.isin(selected)].plant_id_eia),
                    'temporal_test_plants':sorted(int(x) for x in fresh[~fresh.block.isin(selected)].plant_id_eia),
                    'training_years':[2019,2020,2021,2022],'temporal_test_year':2023,
                    'selection_rule':'Outcome-blind hash: reserve 25% (minimum three) of 2-degree blocks with at least three fresh plants having 12 historical months. Whole blocks excluded from development.',
                    'release_gate':'At least 5% MAE improvement on both untouched tests; CV no worse than Ridge and RF.',
                    'candidate_grid':[{'num_leaves':leaves,'min_child_samples':minimum,'alpha':alpha,'n_estimators':rounds,'reg_lambda':10,'feature_set':features}
                                      for leaves,minimum,alpha in [(7,60,.05),(15,60,.1),(31,100,.1)]
                                      for rounds in [150,400,800] for features in ['physical','combined']]}
            protocol.split(frame,design)
            path.write_text(json.dumps(design,indent=2),encoding='utf-8')
        report['protocol']=design
        cached=pd.read_parquet(root/'training.parquet') if (root/'training.parquet').exists() else None
        if cached is None or not set(ATLAS_FEATURES).issubset(cached.columns):
            shutil.copy2(previous/'rare-monthly.parquet',root/'rare-monthly.parquet')
            stage('V2','Joining county resource baseline')
            frame,_=plant_data.rare(root,frame)
            if not (root/'era5').exists(): shutil.copytree(previous/'era5',root/'era5')
            stage('V3','Adding hourly 100-meter wind weather for expanded plants')
            frame=plant_data.site_wind(root,frame)
            stage('V4','Sampling terrain and 250-meter Wind Atlas climatology')
            frame,_=data.terrain(root,frame,required_features=PHYSICAL_FEATURES)
            frame=site_atlas(root,frame)
            if not np.isfinite(frame[ATLAS_FEATURES]).all(axis=None): raise QualityGateError('Nonfinite atlas features.')
            frame['schema_version']=ATLAS_SCHEMA_VERSION
            frame.to_parquet(root/'training.parquet',index=False)
        else: frame=cached
        # terrain() also writes training.parquet; a complete schema check prevents partial cache reuse.
        if not set(ATLAS_FEATURES).issubset(frame.columns): raise QualityGateError('Preparation cache incomplete; atlas join required.')
        splits=protocol.split(frame,design)
        print('Split sizes: '+str([(len(x),x.plant_id_eia.nunique()) for x in splits]),flush=True)
        summary=data.quality_summary(frame)
        summary.update(sample_unit='plant-month',schema_version=ATLAS_SCHEMA_VERSION,features=ATLAS_FEATURES,
                       generators=int(frame.groupby('plant_id_eia').generators_count.max().sum()),
                       label_description='Directly reported monthly wind-only plant generation; annual-only estimates excluded',
                       states=sorted(frame.state.unique()),retained_states=sorted(frame.state.unique()),
                       era5_note='ERA5 weather uses an approximately 28 km grid; Wind Atlas climatology adds approximately 250 m site detail.')
        (root/'training-report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        report['data']=summary
        from backend.ml.plant_training import train
        result=train(frame,root,design,stage,feature_sets={'physical':PHYSICAL_FEATURES,'combined':ATLAS_FEATURES})
        report.update(evaluation=result,production_eligible=result['production_eligible'],status='validated' if result['production_eligible'] else 'rejected',detail=result['decision']);save()
        print(result['decision'],flush=True)
    except Exception as error:
        report.update(status='gate_failed' if isinstance(error,QualityGateError) else 'error',detail=str(error));save();raise


if __name__=='__main__': main()
