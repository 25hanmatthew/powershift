# Wind model validation — revision 4

The selected model passed both point-prediction release gates. Live corrections remain disabled for the current app searches.

| Test | Resource-only MAE | Model MAE | Error reduction |
|---|---:|---:|---:|
| New locations | 9.796 | 8.022 | 18.10% |
| Later year (2023) | 12.353 | 10.793 | 12.62% |

MAE is measured in capacity-factor percentage points. The required improvement was 5% on both tests. Model selection used only geographic cross-validation; final tests were opened once.

- Selected model: robust Huber regression, alpha 1, epsilon 1.1, 21 features.
- Complete data: 19,509 directly reported plant-months, 731 plants, 807 generators, 17 states, 2019–2023.
- Training: 9,563 rows from 265 plants. New-location test: 839 rows from 23 new plants in three blocks. Later-year test: 1,727 rows from 146 new plants.
- Geographic development MAE: 7.098 CF points, compared with 8.418 for resource only, 7.367 for Ridge and 8.237 for Random Forest.
- Predictors: RARE county resource baseline; ERA5 hourly wind physics; SRTM terrain; WorldCover; season; Global Wind Atlas site climatology.
- Data fixes: exclude annual-only monthly estimates, mixed-technology plants and partial operating months; remove duplicate-source ambiguity; equal weight per plant.
- Two plants excluded for incompatible atlas/weather climatology ratios. All final test memberships were preserved.
- Exported predictions and SHAP contributions reconcile. Optional intervals failed the later-year coverage check and are withheld.

## Scope and deployment

New-location evidence covers Kansas, Oklahoma and Texas, not nationwide performance. Historical inputs span 2019–2023. Current western-U.S./2024 app searches retain base estimates. Deployment requires matching candidate feature preparation inside the validated scope; simply turning on the feature flag does not manufacture those inputs.

The trained point model is in `data/private/ml-v4/artifacts/wind.json`; `manifest.json` records feature order, scope, checksum and validation. All earlier failed experiments remain intact. No training data or final test was relabeled to force a passing result.

## Sources

[PUDL / VCE RARE](https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/vcerare.html), [ERA5](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_HOURLY), [Global Wind Atlas](https://globalwindatlas.info/). Atlas features are adaptations of GWA 4.0, developed by DTU and the World Bank Group with ESMAP funding, under [CC BY 4.0](https://globalwindatlas.info/about/TermsOfUse).
