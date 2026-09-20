"""Publish measured ISD/PUDL findings and a local, interactive research report."""
import html
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.wind_insight_data import ROOT
from scripts.train_isd_pudl_wind import write, digest
from scripts.train_wind_insights import metrics
from scripts.evaluate_wind_insights import bootstrap_gain

PUBLIC = Path('public/research/wind-insights')


def report():
    result = json.loads((ROOT/'evaluation.json').read_text())
    test = pd.read_parquet(ROOT/'predictions-2024.parquet')
    history = pd.read_parquet(ROOT/'history.parquet')
    h1,h2,h3 = [result['primary_hypotheses'][k] for k in ['H1','H2','H3']]
    scores = result['scores']
    base,wind,weather = [scores[name]['mae_cf_points'] for name in ['pudl_seasonal','wind_only','weather']]
    captured = 100*(base-wind)/(base-weather)
    cold = test[test.temperature_delta_c.le(-3)]
    cold_months = cold.groupby(cold.report_month.dt.month).size().to_dict()
    # Complete the predeclared 2022 reference comparison on its available paired
    # rows. Two missing 2022 observations are excluded from EVERY paired model.
    last = history[history.report_month.dt.year.eq(2022)].set_index(['plant_id_eia','calendar_month']).actual_capacity_factor
    previous = pd.MultiIndex.from_frame(test[['plant_id_eia','calendar_month']]).map(last).to_numpy(dtype=float)
    available = np.isfinite(previous)
    part = test[available]
    supplemental = {'purpose':'Predeclared 2022-reference comparison on common available rows; no model reselection.',
        'rows':int(available.sum()),'missing_2022_rows':int((~available).sum()),
        'last_observed_2022':metrics(part,previous[available]),'weather':metrics(part,part.prediction_weather.to_numpy()),
        'gain':bootstrap_gain(part,previous[available],part.prediction_weather.to_numpy()),
        'cold_event_months':{str(k):int(v) for k,v in cold_months.items()},'wind_only_share_of_observed_mae_gain_pct':captured}
    write(ROOT/'supplemental-analysis.json',supplemental)
    PUBLIC.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
        'axes.spines.left':False,'axes.edgecolor':'#ccd3c8','text.color':'#23352c','axes.labelcolor':'#41584a','xtick.color':'#41584a','ytick.color':'#41584a'})
    fig,axes = plt.subplots(1,2,figsize=(12,4.7),gridspec_kw={'width_ratios':[1.25,1]},layout='constrained')
    bins = result['wind_bins_descriptive']
    values = [b['mean_cf_anomaly_points'] for b in bins]
    axes[0].bar(np.arange(5),values,color=['#bb7651','#d3a482','#b8c5ac','#76a986','#346e50'],width=.68)
    axes[0].axhline(0,color='#82907d',linewidth=.8)
    axes[0].set_xticks(np.arange(5),['Much\nless wind','Less\nwind','Near\nnormal','More\nwind','Much\nmore wind'])
    axes[0].set_ylabel('Output departure from seasonal history (CF points)')
    axes[0].set_title('Observed wind and generation move together',loc='left',fontweight='bold',pad=19)
    axes[0].set_ylim(-10,7)
    for i,(value,b) in enumerate(zip(values,bins)):
        axes[0].text(i,value+(.4 if value>=0 else -.45),f'{value:+.1f}',ha='center',va='bottom' if value>=0 else 'top',fontweight='bold')
    labels = ['PUDL seasonal history','+ ISD wind response','Full ISD weather model']
    axes[1].barh(np.arange(3),[base,wind,weather],color=['#c4cabb','#6e9b77','#244f38'],height=.54)
    axes[1].invert_yaxis(); axes[1].set_yticks(np.arange(3),labels)
    axes[1].set_xlim(0,7.2); axes[1].set_xlabel('2024 mean absolute error (CF points; lower is better)')
    axes[1].set_title('Weather improves the historical estimate',loc='left',fontweight='bold',pad=19)
    for i,value in enumerate([base,wind,weather]):
        axes[1].text(value+.12,i,f'{value:.2f}',va='center',fontweight='bold')
    fig.savefig(PUBLIC/'findings.png',dpi=170,facecolor='white')
    fig.savefig(PUBLIC/'findings.svg',facecolor='white')
    plt.close(fig)
    chart = (PUBLIC/'findings.svg').read_text(encoding='utf-8')
    chart = chart[chart.index('<svg'):]
    markdown = f'''# What ISD and PUDL tell us about wind generation

**Three main findings from a fresh 2024 test: observed weather improves estimates, weak-wind months have clear generation shortfalls, and a simple wind response captures most of the measured benefit.**

This round trained 20 additional model configurations using only historical PUDL generation and NOAA ISD weather. It concerns existing operating wind plants with known history, not proposed sites or rooftop solar. No ERA5, Wind Atlas or RARE baseline enters this new model.

## 1. Weather improves estimates beyond generation history alone

On **{result['rows']:,} months from {result['plants']} plants in {result['states']} states**, adding ISD weather reduced mean absolute error from **{base:.2f} to {weather:.2f} capacity-factor percentage points: {h1['gain_pct']:.1f}% lower error**. The 98.33% geographic-block bootstrap range is **{h1['lower_pct']:.1f}% to {h1['upper_pct']:.1f}%** improvement. Resampling whole states also retains a positive interval ({result['state_cluster_sensitivity']['lower_pct']:.1f}% to {result['state_cluster_sensitivity']['upper_pct']:.1f}%).

PUDL supplies each plant's normal output for that calendar month, using 2019–2022. ISD supplies observed weather departures. This shows that the weather data add useful information beyond merely knowing a plant's usual seasonal performance. It is a retrospective weather adjustment: target-month weather is observed, not forecast.

The separately reported 2022 same-month reference has MAE {supplemental['last_observed_2022']['mae_cf_points']:.2f}, versus {supplemental['weather']['mae_cf_points']:.2f} for the weather model on the same {supplemental['rows']:,} available rows. Two missing 2022 observations are excluded from both sides of that comparison.

## 2. Unusually weak winds line up with substantial generation shortfalls

For **{h2['paired_plants']} plants that experienced both low-wind and near-normal months**, the within-plant difference in generation anomaly was **{abs(h2['difference_cf_points']):.2f} CF percentage points lower** in low-wind months. The adjusted interval is **{h2['lower_cf_points']:.2f} to {h2['upper_cf_points']:.2f} points**. Low-wind months averaged {h2['event_cf_points']:.2f} points below their historical seasonal baseline, compared with {h2['normal_cf_points']:.2f} in near-normal months for these paired plants.

The fixed low-wind threshold is a departure of at least 0.15 below the usual calendar month's normalized wind ratio; near-normal is within ±0.05. Ratios use the station's 2016–2018 annual mean as the unit, so this is not a claim that all those months were exactly 15% less windy than their seasonal mean. Low-wind observations occur in all 12 test months.

## 3. The wind signal does most of the predictive work

A model using only the wind departure has MAE **{wind:.2f}**, close to **{weather:.2f}** for the full weather-response model. That simple model captures **{captured:.1f}% of the observed MAE improvement** over the seasonal-history baseline. The full model's additional gain over wind-only is {result['weather_vs_wind_only']['gain_pct']:.2f}%, with an interval spanning {result['weather_vs_wind_only']['lower_pct']:.2f}% to {result['weather_vs_wind_only']['upper_pct']:.2f}%. We cannot establish a consistent extra benefit from the more complex model on this test.

Station normalization beats the raw-wind model by {result['normalized_vs_raw_wind']['gain_pct']:.2f}% in point estimates, but its interval includes no improvement. We do not claim that normalization is universally superior.

## Cold-weather finding: useful case evidence, limited generality

For {h3['paired_plants']} paired plants, months at least 3°C colder than their historical calendar-month mean have a **{abs(h3['difference_cf_points']):.2f}-point additional shortfall** relative to near-normal-temperature months, after the wind-only adjustment. Its adjusted spatial-bootstrap interval is {h3['lower_cf_points']:.2f} to {h3['upper_cf_points']:.2f} points. However, **{cold_months.get(1,0)} of {len(cold)} cold observations occur in January 2024**. This is primarily evidence about that period; it does not prove a general cold-weather penalty or identify turbine icing as the cause. There are too few independent cold episodes for a broad seasonal conclusion.

## What the weather model still cannot explain

Of {result['unexplained_shortfalls_descriptive']['months_below_historical_cf_by_10_points']} months at least 10 CF points below historical seasonal output, {result['unexplained_shortfalls_descriptive']['months_still_below_weather_model_by_10_points']} remain more than 10 points below the weather-adjusted prediction. These are candidates for investigation, not diagnosed outages or curtailment. Missing weather detail, plant changes and model error can also contribute.

## How the evidence was tested

- Training: {result['historical_training_rows']:,} monthly observations from {result['historical_training_plants']} plants, 2019–2022; 20 robust-linear, ridge and spline configurations.
- Four leave-year-out development folds. Each fold's plant/calendar-month baselines exclude the full validation year; fitting rows also exclude their own target year from baseline construction.
- Final 2024 test: opened once after models, three primary hypotheses and thresholds were frozen. Existing earlier test reports remain intact.
- Surface stations retain the same identities as the historical joins, stay within 100 km, and require at least 70% hourly wind and temperature coverage with original quality filtering. No weather is fabricated for missing stations.
- Three primary comparisons use 4,000 geographic-block bootstrap draws with 98.33% intervals, a Bonferroni adjustment. Sensitivity analysis, bins, state slices and model ablations are secondary.
- The figures show associations. One fresh year cannot prove causation, future-year reliability or performance at new sites. The cold result is especially concentrated in a single period.
- No live site ranking or feature flag was changed. The numeric research models and seasonal reference table are saved separately.

## Implications for PowerShift

For a city or utility planning around an existing wind project, use historical output as a seasonal baseline and observed wind to explain deviations. Present the weather-adjusted estimate alongside the historical expectation. Flag large remaining discrepancies for review, without labelling their cause. The simpler wind response is a strong starting point; more features did not provide a clearly established extra benefit here.

## Sources and reproducibility

[NOAA ISD documentation](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database) describes the observed station weather. [PUDL EIA-923 documentation](https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/eia923.html) describes plant generation records. EIA-860 operating capacity is used to calculate capacity factors; directly reported wind-only monthly records are retained.

Models: `data/private/ml-v9-insights/research-weather-models.json`. Reference: `seasonal-reference.parquet`. Full scores, uncertainty and primary hypotheses: `evaluation.json`. The 2022 comparison and cold-period concentration are in `supplemental-analysis.json`. Paired records and frozen hashes are retained beside them. The interactive report is at `/research/wind-insights/` in the local app.
'''
    (ROOT/'INSIGHTS.md').write_text(markdown,encoding='utf-8')
    Path('reports').mkdir(exist_ok=True)
    Path('reports/ISD_PUDL_INSIGHTS.md').write_text(markdown,encoding='utf-8')
    public_data = {'National':{'rows':result['rows'],'plants':result['plants'],'base':base,'weather':weather}}
    for state,s in result['by_state'].items():
        public_data[state]={'rows':s['weather']['rows'],'plants':s['weather']['plants'],'base':s['pudl_seasonal']['mae_cf_points'],'weather':s['weather']['mae_cf_points']}
    dataset_json = json.dumps(public_data).replace('<','\\u003c')
    options = ''.join(f'<option value="{html.escape(state)}">{html.escape(state)}</option>' for state in public_data)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PowerShift | ISD + PUDL findings</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f4ec;color:#1d3528;font:16px/1.65 system-ui,sans-serif}}main{{max-width:1180px;margin:auto;padding:36px 30px 70px}}header{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #cbd2c4;padding-bottom:22px;margin-bottom:48px}}a{{color:#2c6543;text-underline-offset:4px}}.brand{{font-size:22px;font-weight:750;text-decoration:none;letter-spacing:-1px}}.eyebrow{{font-size:11px;letter-spacing:2px;font-weight:700;color:#5d7665}}h1{{font-size:clamp(36px,6vw,66px);line-height:1.05;letter-spacing:-3px;font-weight:600;max-width:900px;margin:15px 0 25px}}h2{{font-size:23px;font-weight:600;line-height:1.3;letter-spacing:-.5px}}p{{color:#536556}}.intro{{max-width:760px;font-size:18px}}.pills{{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 38px}}.pill{{border:1px solid #b9c8b0;border-radius:30px;padding:6px 14px;font-size:12px;background:#e5eadb}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.card{{background:#fff;border:1px solid #d7dfd0;border-radius:17px;padding:25px}}.card strong{{display:block;font-size:45px;line-height:1.2;font-weight:600;letter-spacing:-2px;margin:14px 0}}.card p{{font-size:14px;margin-bottom:0}}.number{{color:#708670;font-size:12px}}section{{margin-top:24px}}.chart{{background:white;border:1px solid #d7dfd0;border-radius:18px;padding:25px 18px 16px}}.chart svg{{width:100%;height:auto}}.note{{font-size:12px;color:#667563}}.section-grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}.caution{{background:#eee6d6;border:1px solid #d6c8ad;border-radius:17px;padding:25px}}.caution h2{{color:#75573a}}.explorer{{background:#193d2b;color:#eef5e8;border-radius:17px;padding:26px}}.explorer p,.explorer .note{{color:#c0d0bf}}select{{font:inherit;background:#f2f5e9;color:#203c2b;border:0;border-radius:8px;padding:7px 14px;margin-left:12px}}.barrow{{margin-top:18px;font-size:13px}}.track{{background:#ffffff16;border-radius:5px;margin-top:5px;overflow:hidden}}.bar{{min-width:2px;height:13px;background:#cce697;transition:width .2s}}.bar.old{{background:#92a28c}}.value{{float:right;font-weight:650}}details{{border-top:1px solid #cbd2c4;padding:20px 0;margin-top:24px}}summary{{cursor:pointer;font-weight:650}}li{{margin:9px 0;color:#536556}}footer{{border-top:1px solid #cbd2c4;margin-top:35px;padding-top:20px;font-size:12px;color:#667563}}@media(max-width:800px){{.cards,.section-grid{{grid-template-columns:1fr}}main{{padding:22px 18px}}header{{margin-bottom:34px}}h1{{letter-spacing:-1.5px}}.chart{{padding:16px 5px}}}}</style></head><body><main>
<header><a class="brand" href="/">PowerShift</a><span class="eyebrow">RESEARCH / OBSERVED WIND PERFORMANCE</span></header>
<span class="eyebrow">NOAA ISD + PUDL</span><h1>What the weather explains.<br>What it doesn’t.</h1>
<p class="intro">Two public datasets connect the weather a wind plant experienced with the electricity it actually produced. These findings come from a new, untouched 2024 test.</p>
<div class="pills"><span class="pill">{result['plants']} operating plants</span><span class="pill">{result['states']} states · {result['rows']:,} plant-months</span><span class="pill">20 new model configurations</span><span class="pill">Research · live rankings unchanged</span></div>
<div class="cards"><article class="card"><span class="number">01 / USE WEATHER, NOT JUST HISTORY</span><strong>{h1['gain_pct']:.1f}%</strong><h2>Lower estimation error</h2><p>PUDL seasonal history: {base:.2f} CF points of error. With ISD weather: {weather:.2f}. Adjusted uncertainty range: {h1['lower_pct']:.1f}–{h1['upper_pct']:.1f}% improvement.</p></article>
<article class="card"><span class="number">02 / WEAK WIND HAS A MEASURABLE GAP</span><strong>−{abs(h2['difference_cf_points']):.1f} pts</strong><h2>Lower output anomaly</h2><p>Low-wind versus near-normal months, compared within {h2['paired_plants']} plants and against each month’s historical output. This is an association, not a causal estimate.</p></article>
<article class="card"><span class="number">03 / THE SIMPLE SIGNAL GOES FAR</span><strong>{captured:.0f}%</strong><h2>Of the observed gain</h2><p>The wind-only model captures almost all of the full model’s improvement. The extra benefit from more weather features is not clearly established.</p></article></div>
<section class="chart">{chart}<p class="note">Left: descriptive averages of plant-level anomalies by wind band; plant membership differs by band. The separate paired test compares the same 144 plants. Wind departures use the station’s historical annual mean as their unit. Right: all models scored on the same 2024 months.</p></section>
<section class="section-grid"><article class="caution"><span class="eyebrow">COLD WEATHER / A LIMITED CASE STUDY</span><h2>An additional {abs(h3['difference_cf_points']):.1f}-point gap—but mostly one cold period.</h2><p>After the wind-only adjustment, unusually cold months show lower output for {h3['paired_plants']} paired plants. Yet {cold_months.get(1,0)} of {len(cold)} cold observations are in January 2024. That supports investigating this period, not claiming a universal cold-weather penalty or diagnosing icing.</p></article>
<article class="explorer"><h2>Explore the geographic slices</h2><label for="region">Region <select id="region">{options}</select></label><p id="coverage"></p><div class="barrow">PUDL seasonal baseline <span class="value" id="baseval"></span><div class="track"><div class="bar old" id="basebar"></div></div></div><div class="barrow">ISD + PUDL weather model <span class="value" id="modelval"></span><div class="track"><div class="bar" id="modelbar"></div></div></div><p id="gain"></p><p class="note">MAE in capacity-factor percentage points. State slices are descriptive, not separately validated claims; small samples can be misleading.</p></article></section>
<details><summary>How we kept the test independent</summary><ul><li>Models trained on 2019–2022; 2024 was opened once after model choice and three primary hypotheses were frozen.</li><li>Each seasonal baseline excludes the row’s own target year. Development folds exclude their entire validation year.</li><li>Same station identities as historical joins; observed-hour coverage ≥70%, distance ≤100 km, original weather-quality filters.</li><li>4,000 geographic-block bootstrap draws; 98.33% intervals account for the three primary comparisons. Whole-state sensitivity also supports the main improvement.</li><li>This is retrospective weather adjustment for known operating wind plants. It is not an advance weather forecast, a new-site yield model, or causal proof.</li></ul></details>
<section><h2>The product implication</h2><p>Show a plant’s usual seasonal output beside a weather-adjusted expectation. Flag large remaining gaps for investigation. In this test, {result['unexplained_shortfalls_descriptive']['months_still_below_weather_model_by_10_points']} severe shortfall months still sit more than 10 CF points below the model’s estimate; these data alone cannot establish why.</p></section>
<footer>Measured research results · <a href="https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database">NOAA ISD</a> · <a href="https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/eia923.html">PUDL EIA-923</a> · <a href="findings.png" download>Download figure</a> · <a href="INSIGHTS.md">Full methodology and findings</a><p>Data and model artifacts are retained locally. No synthetic generation values or fabricated weather observations are used.</p></footer>
<script>const slices={dataset_json};function update(){{const s=slices[document.getElementById('region').value],m=Math.max(s.base,s.weather)*1.15;document.getElementById('coverage').textContent=`${{s.plants}} plants · ${{s.rows}} months`;document.getElementById('baseval').textContent=s.base.toFixed(2);document.getElementById('modelval').textContent=s.weather.toFixed(2);document.getElementById('basebar').style.width=`${{100*s.base/m}}%`;document.getElementById('modelbar').style.width=`${{100*s.weather/m}}%`;const g=100*(1-s.weather/s.base);document.getElementById('gain').textContent=`${{Math.abs(g).toFixed(1)}}% ${{g>=0?'lower':'higher'}} error in this slice`;}}document.getElementById('region').addEventListener('change',update);update();</script></main></body></html>'''
    (PUBLIC/'index.html').write_text(page,encoding='utf-8')
    shutil.copy2(ROOT/'INSIGHTS.md',PUBLIC/'INSIGHTS.md')
    write(ROOT/'report-provenance.json',{'evaluation_sha256':digest(ROOT/'evaluation.json'),'model_sha256':digest(ROOT/'research-weather-models.json'),
        'html_sha256':digest(PUBLIC/'index.html'),'report_sha256':digest(ROOT/'INSIGHTS.md'),
        'note':'Report generation adds only prespecified reference completion and descriptive diagnostics; no retraining or final-test reselection.'})
    print('Measured insights report: '+str((PUBLIC/'index.html').resolve()))


if __name__=='__main__':
    report()
