"""Summarize completed measured experiments, including failed comparisons."""
import json
from pathlib import Path

from scripts.train_isd_pudl_wind import write, digest


def report():
    root = Path('data/private/ml-v8-eastern')
    result = json.loads((root/'evaluation.json').read_text())
    prior = json.loads(Path('data/private/ml-v6-fusion/evaluation.json').read_text())
    dev = json.loads(Path('data/private/ml-v7-station-normalized/selection.json').read_text())
    source = json.loads((root/'source-config.json').read_text())
    if source['old_v6_evaluation_sha256'] != digest(Path('data/private/ml-v6-fusion/evaluation.json')):
        raise ValueError('Prior evaluation changed.')
    passed = result['research_validation_passed']
    lines = ['# ISD / PUDL wind training results', '',
        f"**Latest independent comparison: {'passed' if passed else 'did not pass'} all fixed research gates.** Live corrections remain disabled.", '',
        'The selected model combines an Atlas-based robust regression with a spline model that uses NOAA station observations normalized against the same stations in 2016–2018. PUDL provides reported monthly plant generation, operating capacity and the county resource baseline.', '',
        '## Untouched eastern test', '',
        '| Model | New-location MAE | 2023 MAE |', '| --- | ---: | ---: |']
    titles = {'resource_only':'Uncorrected resource baseline', 'previous_v4_artifact':'Previously saved model',
              'atlas_retrained':'Atlas model retrained on identical rows', 'fusion':'Selected normalized ISD / PUDL ensemble',
              'without_isd':'Identical ensemble without ISD'}
    for model,label in titles.items():
        values = [result['fresh_tests'][test][model]['mae_cf_points'] for test in ['spatial','temporal']]
        lines.append(f'| {label} | {values[0]:.5f} | {values[1]:.5f} |')
    lines += ['', 'MAE is in capacity-factor percentage points; lower is better. Relative improvements are:', '',
              '| Compared with | New locations | 2023 |', '| --- | ---: | ---: |']
    for model,label in titles.items():
        if model=='fusion':
            continue
        values = [result['improvement_pct'][test][model] for test in ['spatial','temporal']]
        lines.append(f'| {label} | {values[0]:+.2f}% | {values[1]:+.2f}% |')
    d = result['data']
    lines += ['', f"Fitting uses {d['training_rows']:,} plant-months from {d['training_plants']} plants and {d['training_blocks']} geographic blocks. The new-location test covers {d['spatial_plants']} plants in {d['spatial_blocks']} whole held-out blocks. The 2023 test covers {d['temporal_plants']} plants. Final test plants were never used in earlier release evaluations.", '',
              '## Fixed research gates', '']
    for gate,value in result['gates'].items():
        lines.append(f"- {gate.replace('_',' ')}: {'PASS' if value else 'FAIL'}")
    lines += ['', 'Passing requires at least 5% improvement over the resource baseline on both tests, a positive improvement over both the identically retrained Atlas model and the matched no-ISD model on both tests, and development error no worse than Ridge or Random Forest.', '',
              'The spatial sample still has few independent geographic blocks. Descriptive uncertainty intervals are retained in `evaluation.json`; these point estimates are not a nationwide guarantee. No prediction interval is exposed.', '',
              f"The later-year incremental ISD gain has a descriptive 95% plant-bootstrap range of {result['comparative_intervals']['temporal']['without_isd']['lower_pct']:+.2f}% to {result['comparative_intervals']['temporal']['without_isd']['upper_pct']:+.2f}%. The interval includes no improvement, so passing the point-estimate gates does not establish a reliable gain in every region or year.", '',
              '## All methods and data', '',
              '68 base configurations plus fixed blends and ablations were examined across development rounds: robust linear models, splines, kernel approximations, histogram and LightGBM boosting, cross-fitted error correction, selective station features, pre-training station normalization, and direct-generation Extra Trees.', '',
              'The earlier raw-ISD fusion beat the previously saved model by about 0.8% on the first fresh tests, but lost to an identically retrained Atlas model and to its no-ISD ablation. That failed experiment is preserved in `../ml-v6-fusion/RESULTS.md`.', '',
              f"Station normalization then improved adaptive development row MAE from {dev['baseline']['mae_cf_points']:.5f} to {dev['selected']['mae_cf_points']:.5f}, and equal-block MAE from {dev['baseline']['block_mae']:.5f} to {dev['selected']['block_mae']:.5f}. The former Midwest holdouts were thereafter treated as examined development data. They were not called fresh again.", '',
              'New eastern test membership was selected using only plant identifiers and geography. The initial within-state grouping was too sparse, so a region-wide, outcome-blind selection reserved at least 15 plants and three blocks before any model was scored. The final model, comparison rules, fitted coefficients and data hashes were fixed before either eastern test was opened.', '',
              '## Application status', '',
              'Both ISD and PUDL are implemented as actual training inputs and their complete joins, models and comparisons are saved. The app continues to use base resource screening. A research result does not enable live corrections: candidate inputs still need the matching historical RARE baseline, station normalization and a validated geographic/time window. The current western presets and default 2024 searches are incompatible with this historical model.', '',
              'This work concerns wind-plant performance. It does not train solar, geothermal or hydro models and does not make city coverage universal.', '',
              '## Evidence', '',
              '- `research-fusion.json`: fitted numeric model coefficients.',
              '- `evaluation.json`: every model comparison, gate and uncertainty interval.',
              '- `final-plan.json`, `evaluation-started.json`, `split-protocol.json`: frozen choices and hashes.',
              '- `spatial-predictions.parquet`, `temporal-predictions.parquet`: paired outcomes and predictions.',
              '- `../ml-v7-station-normalized/research-wind.json`: separately saved development model.', '']
    text = '\n'.join(lines)
    (root/'RESULTS.md').write_text(text,encoding='utf-8')
    Path('data/private/WIND_TRAINING_RESULTS.md').write_text(text,encoding='utf-8')
    write(root/'summary.json',{'configuration_count':68,'research_validation_passed':passed,'live_corrections_enabled':False,
          'dataset_roles':{'PUDL':'Reported generation, capacity and resource baseline','NOAA ISD':'Observed weather and pre-training station climatology'},
          'latest_improvement_pct':result['improvement_pct'],'previous_improvement_pct':prior['improvement_pct'],
          'report_sha256':digest(root/'RESULTS.md')})
    print(text[:2500])


if __name__=='__main__':
    report()
