# PowerShift: Signal in the Noise

## The story

Renewable-energy planning needs both new capacity and a clear view of the energy existing assets actually deliver. A weak generation month alone cannot tell a planner whether the weather was unusually poor or whether the gap deserves closer investigation. PowerShift joins reported power-plant generation from PUDL with quality-filtered NOAA ISD weather, establishes each plant's seasonal history, and makes the remaining discrepancy visible on a map.

The intended users are regional energy planners and renewable-asset analysts. One search now combines new-build screening with existing-generation investigation actions. A transparent regional downside preference can change wind rankings using the joined ISD/PUDL evidence. The historical model is not promoted into a new-site output predictor.

Both ISD and PUDL appear in the [official challenge catalog](https://drive.google.com/file/d/1dUiHEbzi3ljph8geUyZck6tLffQwFkwt/view). No result guarantees a competition outcome; the strongest case is a useful, measured signal with an honest demonstration.

## A two-minute demonstration

1. Run the Sacramento regional demo. Show the solar, wind, environmental and infrastructure screening on the main map.
2. In the same shortlist, show **Learn from existing generation**. Four nearby studied wind plants have 48 qualified monthly records. Foundation Superior Farms has three monthly discrepancies larger than 10 capacity-factor points after weather adjustment, so PowerShift recommends requesting operating records alongside developing new sites.
3. Open **How these sources change the decision**. Inspect each plant's seasonal reference, weather estimate, reported output and station matches. The study's local equal-plant mean weather-downside share is about 11.0%.
4. Explain the ranking rule: wind resource score is multiplied by (1 - nearby historical downside share) before applying energy-output weight. The factor is candidate-specific and requires three complete, spatially distinct plant-years within 100 km. The switch reverses this preference without altering measurements.
5. Be explicit: Sacramento's sampled wind sites already fail the wind-resource screen. ISD/PUDL adds an investigation action here, while the solar shortlist remains unchanged. Do not claim this demo proves a changed construction choice. The deterministic regression test demonstrates an actual selection change on a controlled candidate fixture (wind resource 90 to 72, below solar 80).
6. Open the methodology link for the frozen model's held-out results. Distinguish the validated historical estimation result from the new, unvalidated regional ranking preference.

The 100 km historical-evidence neighborhood and the 40 km new-site boundary serve different purposes and are labeled separately. The investigation queue never reduces the new-capacity target, assumes recoverable output, or changes project cash flow.

## Exact decision policy

For plant p, downside_p = sum_month max(0, seasonal_MWh - weather_estimate_MWh) / sum_month seasonal_MWh. The candidate's downside is the equal-plant mean within 100 km. Resource_score_after = resource_score_before * (1 - downside). The weighted total score then follows the normal user priorities. This is a conservative preference for regions with less historical weather-related downside; it is not an estimated probability, a forecast loss factor, or a demonstrated improvement in new-site selection. One observed year is insufficient to establish long-run regional risk. Missing evidence is neutral, solar is unchanged, and the underlying generation and finance assumptions are preserved.

## The three defensible findings

Capacity factor is generation as a share of continuous full-power output. A change from 40% to 30% is a decrease of 10 capacity-factor percentage points.

**Weather adds useful signal.** On 3,299 held-out 2024 plant-months from 278 previously known plants in 28 states, adding ISD weather to a PUDL seasonal-history reference reduces mean absolute capacity-factor error from 6.14 to 5.17 percentage points, a 15.7% reduction. The 98.33% geographic-block bootstrap improvement interval is 11.5–20.1%, adjusted for three prespecified primary tests. Target-month weather is observed; this is retrospective estimation, not forecasting.

**Weak-wind months have a measurable within-plant relationship with output.** Across 144 plants observed in both low-wind and near-normal months, low-wind months have 6.79 fewer capacity-factor percentage points of generation anomaly on average. The study uses station-normalized departures with a fixed threshold. Do not translate that threshold into an exact percentage reduction in physical wind speed, or describe the association as causal.

**A simple signal does nearly all the work.** Wind alone captures 98.2% of the full model's observed error reduction. The additional gain from the more complex weather model is uncertain. This fits the challenge's emphasis on finding what matters: the best research conclusion is not always the most complicated model.

## How the implementation supports the judging criteria

| Criterion | What to demonstrate |
| --- | --- |
| Originality | Connect new-build planning with weather-aware review of existing generation. Show how the same shortfall looks different after joining two datasets. |
| Technical excellence | Versioned PUDL queries, selective station downloads, cached Parquet intermediates, reported wind-only labels, quality codes, hourly deduplication, station-distance/coverage gates, leak-resistant seasonal baselines, frozen model artifacts and reconciliation checks. |
| Insight | Lead with the held-out error reduction, the paired low-wind result and the wind-only ablation. Show an example where weather creates a flag as well as removing one. |
| Execution | One main map and shortlist, with explicit score effects, a reversible risk preference, existing-plant actions and expandable source evidence. No separate operating-wind mode. |

## Research boundaries

- The study covers a qualified sample, not all US plants. Its 28-state coverage does not prove generalization to unseen sites.
- The test contains known operating plants in a later year. Geographic bootstrap clusters quantify dependence and uncertainty; they are not a held-out-geography deployment test.
- The model's weather adjustment is not a causal attribution. Remaining differences can reflect model error, unmeasured weather, plant changes, operations and reporting issues.
- The review threshold is a fixed 10 capacity-factor-point screen, not a calibrated anomaly probability. No claim of fault-detection accuracy is made.
- Missing months are not zero. Incomplete plant-years cannot contribute to the ranking factor or investigation queue.
- Do not call the discrepancy recoverable energy, avoided construction, savings or diagnosed curtailment. Those claims require operations data and a separate validation study.
- Do not claim the entire 600 GB ISD archive was processed. The workflow selectively accesses the weather observations needed for the study; 339 stations were matched for this study.
- The existing new-site model correction flag remains unchanged. The new regional ranking preference uses historical outputs descriptively; its impact on future siting quality has not been validated.

## Reproducibility and implementation

- `scripts/export_wind_fleet.py` rebuilds the public replay from the existing frozen study. It does not train or reselect models. It verifies frozen input/model/evaluation hashes, reported generation versus capacity, historical-only baselines, predictions, weather gates and published metrics before exporting.
- `data/public/wind-fleet-2024.json` contains only the public-data-derived replay needed by the UI, with source provenance. The app can serve this approximately 2 MB artifact without loading the private training tables or downloading source data during the demonstration.
- `GET /api/wind-fleet` serves the audited artifact. Normal city, regional and live analysis now attach operating evidence through `rank_candidates`; candidate score adjustments occur before shortlist selection. Client and server reranking preserve the toggle, and exports include evidence provenance.
- `backend/tests/test_wind_fleet.py` checks the published replay and rejects corrupted measurements or test-year leakage. `backend/tests/test_operating_decisions.py` and `src/ranking.test.ts` prove the policy can change portfolio selection, is reversible, cannot compound, preserves physical energy and capacity, and cannot override hard exclusions.
- The frozen research process and full results remain documented in `reports/ISD_PUDL_INSIGHTS.md` and the in-app `/research/wind-insights/` report.

## Suggested closing line

“PowerShift turns weather and power-generation records into a question an energy planner can act on: is a disappointing month consistent with the weather, or does it deserve a closer look? We tested the signal on a held-out year, exposed what the model still misses, and made every result traceable back to the underlying observations.”
