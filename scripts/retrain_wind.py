"""One-shot wind retraining with new plants and a frozen evaluation protocol."""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('data/private/ml-v2'))
    parser.add_argument('--previous', type=Path, default=Path('data/private/ml'))
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    from backend.ml import data, protocol
    from backend.ml.schema import QualityGateError
    import pandas as pd
    args.directory.mkdir(parents=True, exist_ok=True)
    if (args.directory/'evaluation-started.json').exists():
        raise SystemExit('This fresh test has already been opened. Its saved result cannot be tuned or overwritten.')
    report = {'status':'running', 'phase':'V1', 'phases':[], 'production_eligible':False,
              'started_at':datetime.now(timezone.utc).isoformat(), 'technology':'wind'}
    def save():
        target = args.directory/'pipeline-report.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(target)
    def stage(phase, detail):
        report.update(phase=phase, detail=detail)
        save()
        print(f'{phase}: {detail}', flush=True)
    try:
        configuration = {'pudl_version':data.PUDL_VERSION, 'years':data.YEARS, 'states':data.STATES,
                         'max_plants':10000, 'schema_version':data.SCHEMA_VERSION, 'protocol':'wind-retrain-v2',
                         'previous_directory':str(args.previous.resolve())}
        config = args.directory/'input-config.json'
        if config.exists() and json.loads(config.read_text(encoding='utf-8')) != configuration:
            raise QualityGateError('Input configuration changed; do not mix cached experiments.')
        config.write_text(json.dumps(configuration, indent=2), encoding='utf-8')
        stage('V1', 'Expanding to all eligible western wind plants and freezing fresh tests')
        frame = data.labels(args.directory, 10000)
        old = pd.read_parquet(args.previous/'labels.parquet').plant_id_eia.unique()
        design = protocol.freeze(frame, old, args.directory/'protocol.json')
        report['protocol'] = design
        report['phases'].append({'phase':'V1', 'status':'passed', 'rows':len(frame)})
        # Reuse source-only caches, never old fitted models or joined feature tables.
        for name in ['rare-monthly.parquet', 'isd-history.csv']:
            if (args.previous/name).exists() and not (args.directory/name).exists():
                shutil.copy2(args.previous/name, args.directory/name)
        if (args.previous/'isd').exists():
            shutil.copytree(args.previous/'isd', args.directory/'isd', dirs_exist_ok=True)
        stage('V2', 'Joining versioned RARE resource baselines')
        frame, coverage = data.rare(args.directory, frame)
        report['phases'].append({'phase':'V2', 'status':'passed', 'join_coverage':coverage})
        stage('V3', 'Preparing NOAA observations for the expanded plant sample')
        frame = data.weather(args.directory, frame)
        report['phases'].append({'phase':'V3', 'status':'passed', 'rows':len(frame)})
        stage('V4', 'Preparing shared terrain features and checking fresh test coverage')
        frame, summary = data.terrain(args.directory, frame)
        protocol.split(frame, design)
        report['data'] = summary
        report['phases'].append({'phase':'V4', 'status':'passed', 'rows':len(frame)})
        if args.prepare_only:
            report.update(status='prepared', detail='Expanded data prepared; fresh tests remain unopened.')
            save()
            return
        from backend.ml.retraining import train
        evaluation = train(frame, args.directory, design, stage)
        report.update(evaluation=evaluation, production_eligible=evaluation['production_eligible'],
                      status='validated' if evaluation['production_eligible'] else 'rejected', detail=evaluation['decision'])
        save()
        print(report['detail'], flush=True)
    except Exception as error:
        report.update(status='gate_failed' if isinstance(error, QualityGateError) else 'error',
                      detail=str(error) if isinstance(error, QualityGateError) else f'{type(error).__name__}: retraining stopped; base scoring retained.')
        save()
        print(report['detail'], flush=True)
        raise


if __name__ == '__main__':
    main()
