"""Offline historical wind pipeline. Stops at the first failed quality gate."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from dotenv import load_dotenv


def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('data/private/ml'))
    parser.add_argument('--max-plants',type=int,default=60)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=True)
    report={'status':'running','phase':'V1','started_at':datetime.now(timezone.utc).isoformat(),
            'phases':[], 'production_eligible':False,'technology':'wind'}
    def save():
        temp=args.directory/'pipeline-report.tmp'
        temp.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        temp.replace(args.directory/'pipeline-report.json')
    def stage(phase,label):
        report.update(phase=phase,detail=label);save();print(f'{phase}: {label}',flush=True)
    try:
        from backend.ml import data
        configuration={'pudl_version':data.PUDL_VERSION,'years':data.YEARS,'states':data.STATES,
                       'max_plants':args.max_plants,'schema_version':data.SCHEMA_VERSION}
        config_path=args.directory/'input-config.json'
        if config_path.exists() and json.loads(config_path.read_text())!=configuration:
            from backend.ml.schema import QualityGateError
            raise QualityGateError('Input configuration changed. Use a new output directory so cached joins cannot be mixed.')
        config_path.write_text(json.dumps(configuration,indent=2),encoding='utf-8')
        stage('V1','Preparing clean PUDL generator-month labels')
        frame=data.labels(args.directory,args.max_plants)
        report['data']=data.quality_summary(frame);report['phases'].append({'phase':'V1','status':'passed','rows':len(frame)})
        stage('V2','Joining RARE county-month resource baselines')
        frame,coverage=data.rare(args.directory,frame)
        report['phases'].append({'phase':'V2','status':'passed','join_coverage':coverage})
        stage('V3','Aggregating quality-controlled NOAA ISD station observations')
        frame=data.weather(args.directory,frame)
        report['phases'].append({'phase':'V3','status':'passed','rows':len(frame)})
        stage('V4','Joining shared Earth Engine terrain and land-cover features')
        frame,summary=data.terrain(args.directory,frame)
        report['data']=summary;report['phases'].append({'phase':'V4','status':'passed','rows':len(frame)})
        if args.prepare_only:
            report.update(status='prepared',detail='Training table ready; no model enabled.');save();return
        stage('V5','Comparing zero residual, Ridge and Random Forest with grouped validation')
        from backend.ml.training import train
        evaluation=train(frame,args.directory,stage)
        report.update(evaluation=evaluation,production_eligible=evaluation['production_eligible'],
                      status='validated' if evaluation['production_eligible'] else 'rejected',
                      detail=evaluation['decision'])
        save()
        print(report['detail'],flush=True)
    except Exception as error:
        from backend.ml.schema import QualityGateError
        report.update(status='gate_failed' if isinstance(error,QualityGateError) else 'error',
                      detail=str(error) if isinstance(error,QualityGateError) else f'{type(error).__name__}: data preparation failed; no model was enabled.',
                      error_type=type(error).__name__)
        save();print(report['detail'],flush=True)
        raise SystemExit(2)


if __name__=='__main__': main()
