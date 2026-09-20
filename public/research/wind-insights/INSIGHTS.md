# What ISD and PUDL tell us about wind generation

**Three main findings from a fresh 2024 test: observed weather improves estimates, weak-wind months have clear generation shortfalls, and a simple wind response captures most of the measured benefit.**

This round trained 20 additional model configurations using only historical PUDL generation and NOAA ISD weather. It concerns existing operating wind plants with known history, not proposed sites or rooftop solar. No ERA5, Wind Atlas or RARE baseline enters this new model.

## 1. Weather improves estimates beyond generation history alone

On **3,299 months from 278 plants in 28 states**, adding ISD weather reduced mean absolute error from **6.14 to 5.17 capacity-factor percentage points: 15.7% lower error**. The 98.33% geographic-block bootstrap range is **11.5% to 20.1%** improvement. Resampling whole states also retains a positive interval (11.2% to 21.9%).

PUDL supplies each plant's normal output for that calendar month, using 2019–2022. ISD supplies observed weather departures. This shows that the weather data add useful information beyond merely knowing a plant's usual seasonal performance. It is a retrospective weather adjustment: target-month weather is observed, not forecast.

The separately reported 2022 same-month reference has MAE 7.54, versus 5.17 for the weather model on the same 3,297 available rows. Two missing 2022 observations are excluded from both sides of that comparison.

## 2. Unusually weak winds line up with substantial generation shortfalls

For **144 plants that experienced both low-wind and near-normal months**, the within-plant difference in generation anomaly was **6.79 CF percentage points lower** in low-wind months. The adjusted interval is **-8.08 to -5.51 points**. Low-wind months averaged -7.65 points below their historical seasonal baseline, compared with -0.86 in near-normal months for these paired plants.

The fixed low-wind threshold is a departure of at least 0.15 below the usual calendar month's normalized wind ratio; near-normal is within ±0.05. Ratios use the station's 2016–2018 annual mean as the unit, so this is not a claim that all those months were exactly 15% less windy than their seasonal mean. Low-wind observations occur in all 12 test months.

## 3. The wind signal does most of the predictive work

A model using only the wind departure has MAE **5.19**, close to **5.17** for the full weather-response model. That simple model captures **98.2% of the observed MAE improvement** over the seasonal-history baseline. The full model's additional gain over wind-only is 0.34%, with an interval spanning -1.59% to 2.32%. We cannot establish a consistent extra benefit from the more complex model on this test.

Station normalization beats the raw-wind model by 1.80% in point estimates, but its interval includes no improvement. We do not claim that normalization is universally superior.

## Cold-weather finding: useful case evidence, limited generality

For 49 paired plants, months at least 3°C colder than their historical calendar-month mean have a **3.74-point additional shortfall** relative to near-normal-temperature months, after the wind-only adjustment. Its adjusted spatial-bootstrap interval is -6.77 to -1.87 points. However, **45 of 53 cold observations occur in January 2024**. This is primarily evidence about that period; it does not prove a general cold-weather penalty or identify turbine icing as the cause. There are too few independent cold episodes for a broad seasonal conclusion.

## What the weather model still cannot explain

Of 417 months at least 10 CF points below historical seasonal output, 218 remain more than 10 points below the weather-adjusted prediction. These are candidates for investigation, not diagnosed outages or curtailment. Missing weather detail, plant changes and model error can also contribute.

## How the evidence was tested

- Training: 12,036 monthly observations from 278 plants, 2019–2022; 20 robust-linear, ridge and spline configurations.
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
