"""Revision 3: reported plant generation, expanded geography, and matched wind physics."""
import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('data/private/ml-v3-plant'))
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--develop-only',action='store_true',help='Compare development candidates without opening either final test.')
    args=parser.parse_args()
    root=args.directory;root.mkdir(parents=True,exist_ok=True)
    if (root/'evaluation-started.json').exists():
        raise SystemExit('Final tests have already been opened; retain the saved result without retuning.')
    from backend.ml import data,plant_data,protocol
    from backend.ml.schema import QualityGateError
    import pandas as pd
    report={'status':'running','phase':'V1','phases':[],'production_eligible':False,
            'started_at':datetime.now(timezone.utc).isoformat(),'technology':'wind','revision':3}
    def save():
        tmp=root/'pipeline-report.tmp'
        tmp.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        tmp.replace(root/'pipeline-report.json')
    def stage(phase,detail):
        report.update(phase=phase,detail=detail);save();print(f'{phase}: {detail}',flush=True)
    try:
        config={'pudl_version':data.PUDL_VERSION,'years':plant_data.YEARS,'states':plant_data.STATES,
                'schema_version':plant_data.SCHEMA_VERSION,'label_unit':'reported monthly wind-only plant',
                'reporting_frequency':['M','AM'],'max_plants_per_state':40,'features':plant_data.FEATURES}
        path=root/'input-config.json'
        if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=config:
            raise QualityGateError('Source configuration changed; cached experiment cannot be mixed.')
        path.write_text(json.dumps(config,indent=2),encoding='utf-8')
        stage('V1','Preparing directly reported monthly generation for wind-only plants')
        frame=plant_data.labels(root)
        label_report=data.quality_summary(frame)
        label_report.update(sample_unit='plant-month',sample_stage='before weather and terrain filtering',
                            reporting_modes=frame.reporting_frequency_code.value_counts().to_dict(),
                            annual_only_monthly_estimates_excluded=True,maximum_plants_per_state=40)
        (root/'labels-report.json').write_text(json.dumps(label_report,indent=2),encoding='utf-8')
        report['data']=label_report
        old=set()
        for previous in ['ml','ml-v2']:
            old.update(pd.read_parquet(Path('data/private')/previous/'labels.parquet').plant_id_eia.unique())
        # Finite candidate budget fixed before outcomes in either final test are scored.
        grid=[{'num_leaves':leaves,'min_child_samples':minimum,'alpha':alpha,'n_estimators':rounds,
               'reg_lambda':10,'feature_set':features}
              for leaves,minimum,alpha in [(7,60,.05),(15,60,.1),(31,100,.1)]
              for rounds in [150,400,800] for features in ['physical','combined']]
        design=protocol.freeze(frame,old,root/'protocol.json',version='wind-plant-v3',training_years=[2019,2020,2021,2022],candidate_grid=grid)
        report['protocol']=design
        report['phases'].append({'phase':'V1','status':'passed','rows':len(frame)})
        stage('V2','Joining expanded county resource baselines')
        frame,coverage=plant_data.rare(root,frame)
        report['phases'].append({'phase':'V2','status':'passed','join_coverage':coverage})
        previous=Path('data/private/ml-v2')
        if not (root/'isd-history.csv').exists(): shutil.copy2(previous/'isd-history.csv',root/'isd-history.csv')
        shutil.copytree(previous/'isd',root/'isd',dirs_exist_ok=True)
        stage('V3','Preparing NOAA station records and hourly 100-meter ERA5 wind features')
        with ThreadPoolExecutor(max_workers=2) as pool:
            wind_future=pool.submit(plant_data.site_wind,root,frame)
            frame=data.weather(root,frame,years=plant_data.YEARS,max_workers=8,prefer_archive=True)
            report['phases'].append({'phase':'V3','status':'passed','rows':len(frame)})
            stage('V4','Joining terrain and checking complete plant-level features')
            frame,_=data.terrain(root,frame)
            wind=wind_future.result()[['plant_id_eia','report_month']+plant_data.WIND_FEATURES]
        frame=frame.merge(wind,on=['plant_id_eia','report_month'],validate='one_to_one')
        import numpy as np
        if not np.isfinite(frame[plant_data.FEATURES]).all(axis=None): raise QualityGateError('Plant feature table contains missing or nonfinite values.')
        protocol.split(frame,design)
        frame['schema_version']=plant_data.SCHEMA_VERSION
        frame.to_parquet(root/'training.parquet',index=False)
        summary=data.quality_summary(frame)
        summary.update(sample_unit='plant-month',generators=int(frame.groupby('plant_id_eia').generators_count.max().sum()),
                       label_description='Reported wind-only plant generation; monthly reporters; complete operating months',
                       states=plant_data.STATES,retained_states=sorted(frame.state.unique().tolist()),
                       schema_version=plant_data.SCHEMA_VERSION,features=plant_data.FEATURES,pudl_version=data.PUDL_VERSION,
                       era5_resolution_m=27830,era5_note='100 m is wind height, not horizontal resolution; ERA5 grid is approximately 28 km.')
        (root/'training-report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        report['data']=summary;report['phases'].append({'phase':'V4','status':'passed','rows':len(frame)})
        if args.prepare_only:
            report.update(status='prepared',detail='Reported plant data and physical wind features prepared; final tests unopened.');save();return
        from backend.ml.plant_training import train
        evaluation=train(frame,root,design,stage,development_only=args.develop_only)
        if evaluation.get('development_only'):
            report.update(development=evaluation['selection'],status='development_ready',detail=evaluation['decision'])
        else:
            report.update(evaluation=evaluation,production_eligible=evaluation['production_eligible'],
                          status='validated' if evaluation['production_eligible'] else 'rejected',detail=evaluation['decision'])
        save();print(report['detail'],flush=True)
    except Exception as error:
        report.update(status='gate_failed' if isinstance(error,QualityGateError) else 'error',
                      detail=str(error) if isinstance(error,QualityGateError) else f'{type(error).__name__}: preparation stopped; base rankings retained.')
        save();raise


if __name__=='__main__': main()
