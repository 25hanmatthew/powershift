# PowerShift

PowerShift is an AI-assisted renewable-energy siting platform built with React/TypeScript, MapLibre, Three.js, and Python FastAPI. It combines Earth-observation data, weather records, land-cover maps, transmission infrastructure, protected-area boundaries, mapped buildings and parking areas, and historical power-plant performance.

One OpenAI GPT-6 Astra agent interprets plain-language goals and calls tools to search, inspect, compare, and rerank sites. Geospatial screening and suitability scores are calculated in code. The app brings a ranked shortlist, on-map 3D equipment concepts, preliminary financial estimates, and project-builder discovery into one workspace. It does not claim to replace engineering, permitting, or interconnection studies.

## Assistant and model configuration

The assistant, planner/verifier, and builder web search default to **GPT-6 Astra (`gpt-6-astra`)** through the OpenAI Responses API. Set server-side credentials in `.env`:

```dotenv
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-6-astra
# Optional assistant-only override; blank inherits OPENAI_MODEL.
OPENAI_CHAT_MODEL=
```

Astra requests use `reasoning.effort: low` for interactive planning. Bounded assistant and builder responses allow up to 8,192 output tokens, including reasoning; this is a ceiling, not a requested answer length. Strict structured output and validated tool arguments remain in place. Conversations use `store: false`; encrypted reasoning items are carried between tool rounds within the request. Unsupported sampling parameters are not sent. Explicit model overrides remain supported, and failures do not silently switch models. Restart the backend after changing `.env`. See the [official Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra).

Searches run from the assistant. Explicit location-based planning requests repeat the search even when that location is already loaded. Returned measurements are reviewed across up to 20 actual sites, with variable camera pacing and compact evidence-check rows in chat. The map then zooms out and opens the shortlist. These animated checks present computed results; they are not additional LLM tool calls. ISD/PUDL operating insights appear in chat. Resetting the conversation clears results, selected equipment, boundaries, and conversation context, returning to a neutral US overview. Nearby markers separate with connector lines so every site remains selectable.

## Sacramento regional demo

The default request is “What is the best way to increase renewable energy in Sacramento, CA and surrounding areas?” It compares rooftop solar, parking solar, ground-mounted solar and onshore wind inside a clearly labeled 40 km radius around the resolved Census city center. Requests containing “surrounding areas” or “nearby areas” use this regional search; ordinary city requests retain city boundaries.

The pipeline screens the 500 largest matching mapped urban surfaces and a 7 × 7 grid of potential 2 km land cells, retaining only complete cells within the radius. It uses OSM, NASA POWER, WorldCover, ERA5, SRTM/Copernicus elevation, VIIRS, national HIFLD and PAD-US. The sidebar reports each approach's eligible count, best initial score and exclusion reasons, with eight distinct shortlisted locations by default. Scores balance resource, environment, grid proximity, buildability and land reuse; they do not optimize project economics. Land cells that overlap screened urban surfaces are excluded to prevent double-counting. Wind must meet a 5.8 m/s mean 100 m wind threshold; land cells must have no more than 5% built-up cover.

The first regional request can take several minutes. Successful physical observations are cached independently for reuse; mapped surfaces refresh after seven days. External failures do not substitute synthetic data. The regional comparison is a sample, not a complete inventory of available parcels, and the existing site design, economics and builder tools remain available from the shortlist. ISD/PUDL evidence now supplies a transparent regional wind-risk preference and an existing-plant investigation queue in the assistant conversation. The historical model is not used to forecast new-site generation.

## ISD + PUDL in the decision flow

The normal search automatically joins its location to the audited 2024 ISD/PUDL study. Existing plants with at least three months more than 10 capacity-factor points below the weather-adjusted estimate become investigation actions in the assistant conversation. No shortfall is credited as recoverable energy or used to reduce the requested new capacity.

For a live wind candidate, at least three spatially distinct studied plants within 100 km must have all 12 qualified months. For each plant, sum the monthly positive differences between the PUDL seasonal reference and the ISD weather-adjusted estimate, then divide by annual reference generation. The equal-plant mean is a historical downside share. The default policy multiplies the candidate's resource score by (1 - that share), then applies normal priority weights. The evidence toggle in chat restores unadjusted scoring instantly. This is an explicit regional planning preference, not a validated new-site prediction. Solar output, wind output and financial estimates are unchanged; missing local evidence produces no score adjustment. Hard exclusions always take precedence.

`backend/operating_evidence.py` uses the audited public artifact `data/public/wind-fleet-2024.json`. `python -m scripts.export_wind_fleet` rebuilds it from the frozen study and checks source/model hashes, baselines, predictions and benchmark parity. No retraining or test-set model selection occurs. Decisions retain the policy version, matched plant IDs and model/prediction hashes. See [the Voloridge walkthrough](reports/VOLORIDGE_DEMO.md) for a demonstration and claim boundaries.

## Run locally

Requires Node.js 20.19+ or 22.12+ and Python 3.12+. From this directory on Windows:

```powershell
npm install
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
npm start
```

Open **http://127.0.0.1:5180**. The API and interactive endpoint documentation run at **http://127.0.0.1:8011/docs**. Both servers bind only to localhost. `npm start` builds and serves the optimized frontend, and stops both processes on Ctrl+C. Use `npm run start:dev` for both servers with frontend hot reload. When running the optimized frontend, rebuild with `npm run build` and refresh to see code changes.

On macOS/Linux, use `.venv/bin/python` instead of `.venv/Scripts/python.exe`. The launcher detects the platform.

Separate development terminals:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011 --reload
npm run dev
```

Production build, served locally by FastAPI:

```powershell
npm run build
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
```

Then open http://127.0.0.1:8011. Restart FastAPI after the first build so it mounts `dist`. This is a single-user local application; add authentication, authorization, rate limiting, TLS, and production task storage before internet deployment.

## Demonstration versus live measurements

The initial workspace submits the Sacramento regional request through the assistant using live providers and reusable cached observations. The "Try demo" button repeats that same live request. It is not a synthetic-data shortcut. A working OpenAI key and configured data services are needed; failures are shown explicitly.

Synthetic fixtures remain available to legacy API tests and demonstration endpoints. Their satellite basemap is real imagery, but their candidate measurements are synthetic and are labeled as such. City searches support US locations using Census boundaries; the regional mode covers a 40 km radius. Coverage depends on available source data. The older preset-based analysis API remains limited to its documented regions. Solar and wind screening are implemented; hydropower and geothermal are not yet supported.

No Earth Engine credentials, Elasticsearch credentials, OpenAI key, or private national source files are bundled. Resetting chat does not delete downloaded datasets or cached physical observations.

## Configure live services

Copy `.env.example` to `.env`, fill in the server-only configuration, and restart the backend. `.env`, credentials, caches and private source data are ignored by git. Never put service keys in a `VITE_` variable.

| Integration | Configuration | Behavior |
| --- | --- | --- |
| Earth Engine | `EARTH_ENGINE_PROJECT`; run `earthengine authenticate`, or set `GOOGLE_APPLICATION_CREDENTIALS` to a service-account JSON file | Computes WorldCover fractions, SRTM slope, cloud-coverage-masked VIIRS radiance and mean hourly 100 m ERA5 wind speed. Your project must be registered for Earth Engine. |
| Elasticsearch | `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY` | Creates/updates `energy_datasets` and `energy_candidates`, indexes measurements, and performs separate dataset and spatial searches. Key needs index creation/mapping, write, refresh and search permissions. |
| Elastic semantic search | `ELASTIC_INFERENCE_ID` | An existing, compatible text embedding inference endpoint enables `semantic_text` + lexical retrieval fused with RRF. Without it, the adapter uses explicitly identified lexical search, which does not meet the hybrid-search acceptance requirement. Use an Elasticsearch version/license supporting these features. |
| OpenAI | `OPENAI_API_KEY`, optional `OPENAI_MODEL` and `OPENAI_CHAT_MODEL` | Assistant, planner/verifier, and builder search use the Responses API with `gpt-6-astra` by default. Requires API access to the configured model. Voice input is not exposed in the current UI. |
| HIFLD | `HIFLD_GEOJSON`, `HIFLD_VINTAGE` | A regional GeoJSON FeatureCollection of transmission LineStrings/MultiLineStrings in WGS84. Default labeled vintage: 2022-10-24. |
| PAD-US | `PADUS_GEOJSON`, `PADUS_VINTAGE` | Regional protected Polygon/MultiPolygon GeoJSON in WGS84. Default version: 4.1. All supplied protected boundaries are conservatively excluded. |

Place regional files in `data/private/`. Include the entire intended study area for protected polygons and a substantial buffer around it for transmission lines. The application cannot infer whether an incomplete regional download omits protected areas. Do not label arbitrary lines/polygons as these inventories. Exact source metadata, URLs and limitations are recorded in `data/datasets.json`.

For the national HIFLD GeoJSON and PAD-US 4.1 geodatabase downloads, extract their archives into `data/private/source/` and run:

```powershell
.venv/Scripts/python.exe -m pip install -r scripts/requirements-data.txt
.venv/Scripts/python.exe -m scripts.import_ground
```

The importer writes `transmission.geojson`, `protected.geojson`, and an `import-manifest.json` in `data/private/`. It covers all three supported region presets, includes a three-degree transmission buffer, reprojects to WGS84, repairs invalid geometry, and preserves boundary detail without simplification. PAD-US Fee, Designation, Easement, and Marine polygons are included regardless of GAP status for conservative screening. Proclamation and planning outlines are omitted because they do not represent internal ownership. Original source files are retained. The national downloads do not expand the app's supported region presets.

After setup, open **Configure → Live data** and run analysis. “Configured” means settings are present; actual connectivity is tested by the run. Missing or failed services never substitute synthetic observations. If an exact matching **computed** cache exists, the app can instead return a prominently labeled stale cached analysis with its original timestamp.

Precompute the flagship region after connecting services:

```powershell
.venv/Scripts/python.exe -m scripts.precompute
```

This runs live analysis and OpenAI verification, saves observations and the result, and prints the run ID. It exits unsuccessfully on failure or a stale fallback. No claim is made that this real precomputation has already been performed.

## Historical Intelligence wind add-on

**New operating-plant insights (revision 9):** A separate ISD/PUDL weather-response model trains on 2019–2022 and tests once on 3,299 unseen 2024 months from 278 known plants in 28 states. Twenty additional configurations were compared with whole validation years excluded from seasonal baseline construction. Adding observed ISD weather to PUDL plant/calendar-month history reduces capacity-factor MAE from **6.14 to 5.17 points (15.75%)**; the adjusted geographic bootstrap improvement interval is 11.53–20.15%. Low-wind versus near-normal months differ by **6.79 CF points** within 144 paired plants. A wind-only response captures about 98% of the observed model gain. The cold-period contrast is concentrated in January 2024 and is not a general icing or cold-loss diagnosis.

Open [the interactive local insights report](http://127.0.0.1:5180/research/wind-insights/) or read [the full findings](reports/ISD_PUDL_INSIGHTS.md). It includes actual state slices, uncertainty, failed/uncertain secondary claims, and a downloadable chart. This is retrospective analysis of **existing plants with historical output**, distinct from the new-site residual model below. It does not use ERA5, Wind Atlas or RARE as predictors. The regional downside policy described above now influences live wind resource scores; the model does not forecast new-site generation. Artifacts and frozen study plans are in `data/private/ml-v9-insights/`; `scripts/report_wind_insights.py` regenerates the report without retraining or reopening model selection. Plot generation additionally needs `scripts/requirements-reports.txt`.

**Latest ISD/PUDL research (revision 8): passed the fixed independent-test gates.** Across 68 model configurations plus blends and ablations, the selected model combines an Atlas robust regression with a spline model using NOAA ISD observations normalized against the same stations' 2016–2018 history. PUDL supplies reported generation, capacity and the resource baseline. Training uses 13,379 plant-months from 360 plants in 27 states. Fresh eastern tests contain 16 plants in three spatial blocks and 48 plants in 2023. Relative to retraining the existing Atlas approach on identical rows, MAE improves **0.15% spatially and 4.47% temporally**. Relative to the identical ensemble without ISD, improvement is **2.28% and 0.18%**, respectively. The smaller gains remain uncertain; the temporal ISD bootstrap interval includes no improvement. Full results, including failed earlier attempts, are in [the training report](data/private/WIND_TRAINING_RESULTS.md). The fitted numeric model is `data/private/ml-v8-eastern/research-fusion.json`. This research pass does not activate live corrections or expand city coverage.

Development experiments and their inputs are preserved under `ml-v6-fusion`, `ml-v7-station-normalized`, and `ml-v8-eastern`. The eastern model and rules were frozen before scoring; the final tests refuse to reopen. `scripts/station_normalized_wind.py` builds station-specific pre-training references, `scripts/direct_wind_models.py` records the direct-generation comparison, and `scripts/evaluate_normalized_wind.py` runs the frozen final evaluation. `scripts/report_wind_training.py` regenerates the measured report from saved results. Use new experiments with new independent tests for further model selection.

**ISD/PUDL follow-up (revision 5, research only):** A paired nested geographic experiment trained on 9,523 plant-months / 265 plants / 69 blocks across 17 states. NOAA ISD was joined to the current PUDL-label + ERA5 + Wind Atlas pipeline, retaining 99.58% of development rows. Nine fixed model/feature configurations were assessed with five outer geographic folds and three inner selection folds. Adding ISD increased MAE from 7.140 to 7.315 capacity-factor percentage points (2.45% worse); the existing approach is retained. The ISD research model is saved in `data/private/ml-v5-isd/research-wind.json`, with the full comparison in `data/private/ml-v5-isd/RESULTS.md`. These development scores are not comparable to revision 4’s separate release holdouts. No release tests were reopened, no live feature flag changed, and city coverage did not expand. Reproduce a new, separately scoped development run with `python -m scripts.train_isd_pudl_wind --directory <new-directory>`; completed runs refuse to overwrite themselves.


**Existing application artifact: revision 4, validation passed.** Revision 4 selected Huber regression with 21 inputs (including Wind Atlas), alpha 1 and epsilon 1.1. Spatial MAE fell from 9.796 to 8.022 CF percentage points (**18.10% improvement**); later-year MAE fell from 12.353 to 10.793 points (**12.62% improvement**). The exported point model and SHAP explanations passed consistency checks. The app shows these measured results; the newer research comparison is saved separately as described above.

The complete feature table contains **19,509 reported plant-months from 731 plants** (807 generators); two of 733 input plants were excluded because Wind Atlas / ERA5 climatology ratios exceeded the fixed [0.25, 4] plausibility range. Training uses 9,563 rows / 265 plants, the new-location test 839 rows / 23 plants, and the 2023 test 1,727 rows / 146 plants. Other rows are intentionally outside the frozen train/test partitions.

Optional 80% prediction intervals achieved 76.2% spatial and 69.1% temporal coverage; the latter failed the 70–90% acceptance band, so intervals are not exposed. Model outputs therefore carry medium confidence. The spatial improvement's descriptive bootstrap range is +4.9% to +26.9%; temporal is +9.5% to +15.7%. The spatial test contains only three geographic blocks, so its uncertainty estimate is limited.

**Live corrections remain off.** The local `.env` points to `data/private/ml-v4`, with `VOLRIDGE_ML_ENABLED=false`. Current app presets are western U.S. regions and default to 2024. This model's fresh location tests cover Kansas, Oklahoma and Texas, and its matched historical baseline ends in 2023. Runtime rejects inputs outside the validated geography/time window. Matching candidate feature preparation is still required before deploying corrections to live searches. Actual exported-model integration tests verify reversible score changes for compatible historical inputs; no new-site prediction is fabricated.

Wind-model development runs offline and retains every experiment, including failures. The web panel reads the actual training and evaluation reports. Corrections stay disabled unless the selected model improves MAE by at least 5% on both geographic and later-year tests and matches or beats the development reference models. More fitting rounds cannot substitute for passing an untouched test.

The latest pipeline adds three substantive improvements:

- **Reported plant labels:** one observation per wind-only plant-month; capacity comes from units operating for the complete month. EIA reporting modes M and AM provide actual monthly generation. Mode A annual-only reporters are excluded because their monthly values are estimated. Mixed-technology plants, duplicate source keys, invalid capacity factors, and incomplete operating months are rejected.
- **Matched wind physics:** hourly ERA5 wind at 100 m and 10 m heights, variability, generic power-curve proxies, shear, temperature and pressure. ERA5 has an approximately 28 km horizontal grid; 100 m denotes measurement height. The earlier NOAA ground-station features remain in the archived comparison but are omitted from revision 4.
- **Local climatology:** Global Wind Atlas 4.0 wind speed at approximately 250 m spacing, summarized at the plant point and over a 2 km footprint. A ratio to the 2019–2022 ERA5 climatology and a clipped cubic power proxy are additional predictors. The 2023 test year does not enter that climatology ratio. These derived covariates are not turbine-specific engineering yields.

Revision 4 uses clean labels from 733 plants in 17 states, over 2019–2023. Equal total weight per plant prevents plants with more monthly observations dominating fitting. Five geographic-block folds compare 40 predefined configurations of LightGBM, Ridge, robust Huber regression, and a Random Forest reference. Selection uses mean geographic-block MAE. Plant identity, coordinates, year, label quality, generator age and nameplate capacity are not predictors.

Final test membership is frozen before fitting. Whole 2-degree geographic test blocks are removed from development. Every plant examined in earlier experiments is excluded from the final tests. The new plants available for revision 4 are in Kansas, Oklahoma and Texas; its final validation does not establish nationwide or western-state reliability. Temporal tests use 2023; prior years of temporal-test plants may enter training. Each final test is opened once, after the selected model and configuration are saved. A failed experiment cannot be rerun with different tuning on those same tests.

Earlier results are preserved: revision 2 used allocated generator labels and achieved 0.85% spatial / 13.95% temporal improvement, failing the geographic gate. Revision 3 switched to reported plant output and selected robust Huber regression, achieving 2.4% spatial / 7.2% temporal improvement, again failing the geographic gate. Different experiments use different held-out plants; these percentages are not a like-for-like progress chart.

Offline commands, from the project directory:

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements-ml.txt
# Completed experiments refuse to reopen their tests:
.venv/Scripts/python.exe -m scripts.train_wind
.venv/Scripts/python.exe -m scripts.retrain_wind
.venv/Scripts/python.exe -m scripts.improve_wind
.venv/Scripts/python.exe -m scripts.train_atlas_wind
```

The private experiment directories are `data/private/ml`, `ml-v2`, `ml-v3-plant`, and `ml-v4`. Revision 4 reuses revision 3's versioned PUDL source tables and monthly weather caches, then fetches features for additional plants. It does not require another credential. NOAA full-ISD gzip ingestion was verified against the equivalent CSV and applies the same wind-quality and hourly coverage rules.

`protocol.json` records test membership and search rules; `development-selection.json` records every candidate's development scores; `evaluation-started.json` binds the selected model and configuration by SHA256; `evaluation.json` and `pipeline-report.json` contain measured results. Research models live outside `artifacts/` and cannot be loaded by the application. Eligible exports validate exact feature order, baseline identity, checksum, and SHAP additivity. Portable linear models use numeric JSON coefficients, not executable serialization. Optional intervals are withheld if held-out coverage fails.

`VOLRIDGE_ML_ENABLED=false` remains the default. Missing artifacts, failed validation, incompatible inputs and unfamiliar feature ranges retain base scoring. The app never trains or downloads training data during an analysis. A RARE-trained residual requires matching RARE baseline inputs; the existing ERA5 screening curve is not interchangeable. Candidate inference preparation remains gated behind successful model validation. HIFLD transmission and PAD-US protected land remain siting constraints, not wind-model training labels.

Sources: [PUDL data access](https://docs.catalyst.coop/pudl/en/v2026.9.0/data_access.html), [VCE RARE](https://docs.catalyst.coop/pudl/en/v2026.9.0/data_sources/vcerare.html), [NOAA ISD](https://registry.opendata.aws/noaa-isd/), [ERA5 hourly](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_HOURLY), and [Global Wind Atlas](https://globalwindatlas.info/).

Global Wind Atlas 4.0 is developed by the Technical University of Denmark with the World Bank Group and funding from ESMAP. The sampled and aggregated wind features are adaptations of that dataset under [CC BY 4.0](https://globalwindatlas.info/about/TermsOfUse). Reference: Davis et al. (2023), [The Global Wind Atlas: A high-resolution dataset of climatologies and associated web-based application](https://doi.org/10.1175/BAMS-D-21-0075.1). This project is not endorsed by the data providers or Voloridge.

## Map workspace

The satellite map fills the workspace. Use the floating search bar to start an analysis; the sliders button opens mission parameters. The portfolio summary stays compact, and **Your shortlist** opens ranked sites. Selecting a map marker or shortlist card flies to that location and opens a small preview; **Inspect site & evidence** opens the full detail view. Save, export, pipeline insights, evidence, and model validation remain available through compact controls.

A search makes a roughly five-second camera tour through up to three points inside the selected region or custom boundary, then frames the selected portfolio. This is a visual orientation sequence, not a claim that those points are live evaluation results. Results are delivered immediately; the tour does not delay backend work. Skip flight or manual map interaction interrupts motion. If analysis takes longer, the camera waits; failures stop the tour. Reduced-motion preferences disable the animated flight.

## 3D project studio and economics

Loading optimizations: the dark raster source is inactive while satellite imagery is shown; map controls and candidate layers initialize when the style is ready, without waiting for every external tile. Project code preloads when a site is selected. The map starts at the requested regional bounds instead of loading a second initial camera position. City-label tiles stop at close equipment zoom levels.

Terrain sampling computes visible DEM coverage once per model update and caches repeated coordinates within that batch. It retains MapLibre's original elevation interpolation and all equipment and shadow detail. MapLibre is pinned to 5.24.0 because this optimization uses its terrain sampling interface; verify numerical parity when upgrading. A browser comparison against `queryTerrainElevation` at 300 points across three camera settings produced identical heights. Local timing measurements and their limits are recorded in [the loading report](reports/loading-performance.md).

Select a map marker or shortlist entry, then **View project on 3D map**. The installation is a georeferenced Three.js custom layer on the existing MapLibre map, sharing its camera, WebGL canvas and depth buffer. There is no separate model viewport or isolated ground tile. Satellite imagery continues across the surrounding region, while [Mapterhorn elevation tiles](https://mapterhorn.com/attribution) provide terrain at 1× vertical scale. Equipment samples terrain elevation at its geographic position. Pan, right-drag to orbit, zoom between the region and individual equipment, or use Equipment, Overview and Plan view. The mountain control also enables 3D terrain outside project mode. Models, detailed settings and calculations load only when a project is opened.

- Solar: 650 W modules, 40 modules per 26 × 4.8 m table, 25° fixed tilt facing south, and a 1.30 DC/AC ratio. Row pitch is adjustable from 8–22 m.
- Wind: generic 6 MW turbines, 115 m hub height and 170 m rotor diameter. Adjustable spacing starts at 5 rotor diameters laterally and 7 longitudinally. This orientation does not infer prevailing wind direction.
- Solar tables follow lateral ground slope, access corridors follow sampled elevations, and the electrical compound has an illustrative level foundation. Lighting casts shadows onto a transparent terrain surface above the satellite map. This is a proposed equipment visualization, not photogrammetry, an as-built model or a civil grading design. The terrain status reports missing elevation data; missing samples use zero elevation and must not be interpreted as surveyed ground.
- Whole units fit inside the outer polygon with a concept buffer and avoid polygon holes. The scene includes central access corridors and an electrical compound where space permits. Buffers are layout assumptions, not jurisdictional setbacks. Equipment count determines concept capacity, capped at the screening estimate; wider spacing can reduce capacity. No wake, shading, land-use exclusion raster, grading, or interconnection design is solved by this layout.

A compact side panel (bottom sheet on mobile) keeps **Economics & benefit** beside the interactive map. It follows that concept capacity. First-year energy scales the selected site's screening estimate by the concept/screening capacity ratio and optional extra curtailment. Subsequent years apply degradation. The panel displays upfront investment, annual operating cash, cumulative cash, NPV, simple payback, discounted lifetime energy cost, lifetime generation, and an editable avoided-emissions scenario. Export scenario downloads the coordinates, equipment specifications, layout, assumptions, annual cash flows, source and limitations as JSON.

Construction defaults use [EIA's capacity-weighted averages for generators installed in 2024](https://www.eia.gov/electricity/generatorcosts/), published July 6, 2026: solar $1,865/kW and onshore wind $1,882/kW. The solar value is the all-PV average, not a quote for this fixed-tilt design. Other defaults are hypothetical assumptions: $55/MWh energy price, $25/kW-year solar or $50/kW-year wind O&M, 10% construction contingency, 7% real discount rate, 30 years, 0.5%/0.2% annual solar/wind degradation, and 0.35 t CO₂/MWh displaced. O&M covers routine operation, without a second annual land charge. Unpriced site/grid additions are represented by an editable extra allowance; zero does not mean no upgrades are needed.

Values use constant 2024 USD and pre-tax, unlevered cash flow. NPV discounts annual revenue minus O&M and a final-year provision of 5% of base construction cost. Payback is the first undiscounted cash recovery, interpolated within a year; it can remain unreached. Energy cost divides discounted total costs by discounted generation. Taxes, credits, subsidies, debt, storage, inflation, price escalation and actual PPA terms are not modeled. Avoided emissions are an assumption-based scenario, not measured reductions. Synthetic candidates remain labeled in the studio and export.

## Core analysis stages

1. **Planner:** parse capacity, solar/wind intent, known region, grid distance and zero-new-land requests. OpenAI is optional for structured request translation; it never computes physical metrics.
2. **Discovery:** query the curated registry through Elastic. Live analysis prepares both technologies so technology changes need no new satellite query.
3. **Earth analysis:** create up to 25 approximately 2 × 2 km screening cells within the preset/custom boundary, sample orbital products, obtain NASA POWER climatology, and join regional transmission and protected geometry. Each cell produces alternative solar/wind candidates. Missing measurements are dropped; an empty analysis fails explicitly.
4. **Ranking:** apply hard exclusions, normalize nonnegative weights, sort by weighted score, and greedily select whole sites until the target is met. A site cannot be selected twice across technologies. If capacity is insufficient, the app reports the shortfall.
5. **Verifier:** audit target, boundary, protected land, grid and slope. Deterministic verification remains authoritative. Any OpenAI audit that changes IDs, datasets or constraint results is rejected.

### Deterministic assumptions

- WorldCover: 2021 built-up fraction; natural habitat classes 10/20/30/90/95/100; snow/water/wetland/mangrove classes 70/80/90/95 count as incompatible. More than 1% incompatible cover excludes a cell. Wind additionally excludes more than 20% built-up cover.
- SRTM: mean slope in degrees; WorldCover/SRTM/VIIRS zonal statistics use a **100 m analysis scale**, explicitly coarser than native WorldCover resolution.
- ERA5: compute `sqrt(u100² + v100²)` per hour, then the period mean. This is modeled reanalysis, not a direct satellite wind observation.
- Solar resource: NASA POWER `ALLSKY_SFC_SW_DWN` annual climatology; retain provider-returned period and API metadata. Nearby requests round to 0.1° and cache. This product is too coarse for parcel-level yield claims.
- Ground geometry: local azimuthal equidistant projection for transmission distance and protected overlap area. The default exclusion rejects **any** positive protected overlap. Distance is from cell centroid; proximity is not interconnection capacity.
- Capacity proxies: usable cell area × 35 MW/km² for solar, or × 5 MW/km² for wind. Solar annual generation uses an assumed 0.8 performance ratio (capacity factor capped at 0.32); wind uses `clamp((speed−3) × 0.075, 0, 0.5)`. These are transparent screening assumptions, not turbine/panel engineering designs.
- Component anchors, each clamped to 0–100: solar resource `(irradiance−3)×30`; wind resource `(speed−4)×20`; grid `100−4×km`; environment `100−0.85×natural_cover_pct`; buildability `100−4×slope_deg`; reuse `developed_pct`. These fixed anchors permit deterministic comparison and are not economic valuations.
- All-zero weights use equal weighting; tie breaks use candidate ID. Hard exclusions are never traded for score.
- Zero-new-land is enforced strictly: it requires a verified developed footprint. WorldCover built-up pixels alone do not qualify, so rural cell searches yield no eligible candidates under that constraint. The Urban solar search uses mapped building and parking footprints; mapped existing use does not verify engineering suitability. Hydro retrofit and geothermal remain unimplemented.

### Cache and AI calls

SQLite under `.cache/` stores normalized region/geometry/date/source-file keys, physical metrics, and run results. Capacity, technology filters, soft weights and hard-filter thresholds are excluded from the physical-analysis key. Slider changes rerank synchronously in the browser; saving/exporting persists the same ranking through a pure Python endpoint with no provider requests.

OpenAI is the only AI provider used for planning, verification, voice transcription and builder search. Screening and ranking remain deterministic. The verifier receives source prose and a structured contract with exact candidate IDs, dataset IDs and constraints. There is no external compression service or duplicate A/B audit call.

## Checks

```powershell
npm run build
npm test
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Tests cover hard exclusions, protected footprints, grid limits, polygon/radius validity, footprint containment, duplicate technology prevention, infeasible targets, zero-new-land, weight normalization, cache keys, request parsing, SSE stages, exports, cached reranking, and explicit failure/stale fallback behavior. Provider responses in tests are mocks, not live-integration validation.

## Project layout

```text
src/                 React workspace, assistant chat, 3D map, accessible dialogs and instant ranking
backend/             FastAPI, deterministic scoring, geospatial analysis and adapters
backend/tests/       Core and API acceptance tests
data/datasets.json   Versioned evidence registry
public/              Brand mark and PCM microphone AudioWorklet
scripts/             Local launcher and live precompute command
```

## Primary API and data references

- [Earth Engine WorldCover v200](https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v200), [SRTM](https://developers.google.com/earth-engine/datasets/catalog/USGS_SRTMGL1_003), [VIIRS](https://developers.google.com/earth-engine/datasets/catalog/NOAA_VIIRS_DNB_MONTHLY_V1_VCMSLCFG), [ERA5 hourly fields](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_HOURLY)
- [NASA POWER API](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/)
- [Elastic hybrid retrieval](https://www.elastic.co/docs/solutions/search/hybrid-search) and [geo_shape mappings](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/geo-shape)
- [OpenAI realtime speech API](https://developers.openai.com/api/docs/guides/realtime-transcription)

Map attribution is displayed in the map: Esri/Maxar/Earthstar satellite imagery, OpenStreetMap and CARTO labels. External basemap tiles and optional Google Fonts require network access; the rest of demonstration mode runs against the local server.


## Urban solar discovery

Use the main text search: **Find the 5 best rooftops for solar in Sacramento, CA**, **3 parking lots in Reno, NV**, or **2 parking structures in Spokane, WA**. Searches always use live data, irrespective of previous demo settings. Five recommendations are shown by default; request between 1 and 20. An optional capacity such as “500 kW” sets a portfolio target; otherwise the metric shows combined shortlist potential. No additional credentials are required.

`POST /api/city/search` resolves the named city or Census place through U.S. Census TIGERweb boundaries and requires every recommended footprint to lie completely inside it. City search accepts all 50 US states and DC, including Census-designated places and county subdivisions when there is no matching place. Include the state for ambiguous names. Honolulu resolves to Urban Honolulu, not the much larger administrative area. Another city is never substituted. Outside the original regional presets, screening reads local windows from the national HIFLD transmission index and PAD-US geodatabase. Missing national sources stop screening rather than implying zero protection. Oversized footprint queries split into cached tiles; every tile must succeed before results are returned. Citywide discovery covers mapped commercial/public or named buildings and parking, screens the 250 largest qualifying surfaces, then returns the requested shortlist. This is not an exhaustive roof inventory. The older neighborhood API remains available with its 36 km² limit.

`POST /api/urban/search` queries OpenStreetMap via Overpass, caches raw responses for seven days, preserves polygon holes, removes overlapping outlines, and screens the 250 largest complete mapped surfaces of at least 200 m². OSM coverage is not exhaustive. Capacity uses 45% usable roof/deck area or 55% parking area, 200 W DC/m² and 1.30 DC/AC. Decks count only their top footprint. NASA POWER climatology supplies a coarse energy scenario with a 0.8 performance ratio; HIFLD and PAD-US retain transmission-proximity and protected-area checks. Failed providers never produce synthetic results.

Urban 3D concepts place individual 650 W modules on the mapped footprint, using mapped height, inferred floor height, or an explicitly disclosed 8 m assumption. Roofs are flat illustrative extrusions; parking canopies assume 4.5 m clearance. Roof pitch, structural strength, obstacles, existing solar, shading, parking circulation, ownership and available distribution capacity are not measured. A structural-suitability check remains unresolved. Costs default to editable **scenario assumptions**, $3,000/kW AC rooftop or $4,000/kW AC parking, not quotes. Reinforcement, reroofing, local tariffs and utility upgrades need site-specific input.


### City planning workspace and surrounding buildings

The app opens a **live Sacramento city search with five rooftop recommendations** and existing-surface reuse enabled. The Sacramento demo button returns to this example from any city. It uses live data, not synthetic fixtures. Nationwide name search removes the regional restriction, but results still depend on mapped footprints and source availability, and do not extend the wind model's validated geography. The primary user is a city sustainability planner identifying roofs and parking areas for follow-up assessment. These are mapped opportunities, not a verified inventory of city-owned assets. The main interface uses one plain-text city request; regional solar/wind workflows remain available through the API.

Surrounding buildings load independently through OpenFreeMap's OpenMapTiles vector source (`https://tiles.openfreemap.org/planet`) and a native MapLibre fill-extrusion layer. Coverage continues as the user pans beyond the analyzed neighborhood. Provider render heights may derive from mapped heights, levels, or defaults; they are visual context, not surveyed geometry. The **3D buildings** map button toggles the layer. Opening a project replaces its context shell and contained parts with the selected concept, keeping nearby buildings visible. Courtyards remain open. Broader map views show fewer numbered pins to keep the neighborhood readable.

Source documentation: https://openfreemap.org/quick_start/ and https://maplibre.org/maplibre-gl-js/docs/examples/display-buildings-in-3d/. No additional credentials or language-model calls are needed for the city building layer. Provider availability and mapping coverage still apply; these buildings do not add structural checks or a building-shadow energy simulation.


### Renewable options and revised solar models

The **Energy options** menu supplies editable plain-text examples for rooftop solar, solar parking canopies and **onshore wind**. Hydro and geothermal are explicitly unavailable pending flow/head/habitat and subsurface/well data. No unsupported generation estimates are shown.

A request such as `3 wind sites in Reno, NV` reuses the live Earth Engine land pipeline within the exact Census city boundary. Complete 2 km screening cells use ERA5 100 m wind, WorldCover, SRTM, VIIRS, HIFLD and PAD-US; the city wind filter assumes at least 5.8 m/s mean wind and at most 5% built-up cover, with the existing protected-area, slope and grid filters. The wind-speed threshold is informed by [EIA wind siting guidance](https://www.eia.gov/energyexplained/wind/where-wind-power-is-harnessed.php); the built-up threshold is a conservative project assumption, not a code requirement. No suitable cells is a valid result. Site setbacks, parcels, turbine-specific yield, turbulence, noise and aviation constraints remain unassessed. It does not model rooftop wind or apply the historical model outside its validated scope. Wind-only city searches skip NASA solar requests and reuse cached physical measurements.

Urban solar geometry now aligns arrays to the longest mapped footprint edge. Rooftops use 10-degree modules with low mounting and gaps between array blocks. Parking/deck canopies use complete 32-module bays, 5-degree tilt, shared columns and beams, and 6 m conceptual driving aisles. Surface parking retains the aerial imagery without an artificial concrete slab. At least 4.5 m clearance is modeled over sampled bay corners; this is not a surveyed clearance guarantee. Footprint alignment is not a surveyed stall layout or energy-optimal azimuth. Whole-bay/module counts cap concept capacity; economics follow that count. The equipment camera moves closer to make mounting and panel details visible.

Map mouse controls: left-drag pans, middle-button (scroll-wheel) drag rotates and tilts, and wheel scrolling zooms. Middle drag captures the pointer until release; losing focus cancels the gesture.


### Project builders

Select a live site and choose **Find project builders**, or **Builders** in the 3D toolbar. `/api/suppliers/search` accepts only the saved run and site IDs. The server derives solar/wind technology, rooftop/canopy/ground-mount layout, screening capacity and city from that site. Users cannot override technology to get unrelated contractors.

On-demand web search through the existing `OPENAI_API_KEY` / `OPENAI_MODEL` finds up to four construction firms from company-published sources. Equipment-only distributors, directories and residential-only installers for commercial projects are excluded by search instructions. Every accepted result must match the project technology, declare construction services, and include a consulted source on its own website. The cited page is fetched and checked for technology, mounting type and construction-service terms; unreadable or unsupported pages are excluded. These checks establish source provenance, not independent verification of every claim. Exact capacity, licensing and availability require confirmation. Service areas are shown without invented office distances. Results and source/contact details stay in the app; there is no external-search fallback. Equivalent project searches are cached for seven days; failed searches are not cached. Provider failures show an explicit retry state.


Map interaction refinements: wheel/trackpad zoom uses higher sensitivity, zoom buttons move two levels, and the camera supports zoom level 22. Native gesture-start events cancel pending tour timers without calling `stop()`, which would cancel the zoom gesture. Left-drag pan remains accelerated. The normal building-height status caption is hidden; tile failures still appear.

The project layer now persists through row-spacing changes and swaps geometry in place after a short input debounce. Lighting changes update the existing scene. Shadow passes preserve the map-owned framebuffer and viewport. Builder details include source-page service keywords, published email when found, site-specific scope questions, and a locally copied inquiry draft; no messages are sent.


Buildings include illustrative window rows, facade frames, floor bands, plinths and roof parapets. Selected project shells retain footprint courtyards; no decorative rooftop equipment is added to the solar layout. Nearby detail loads only at zoom 17 and above, reuses the visible vector footprints and heights, and is capped at 70 buildings / 16,000 instances with two instanced detail meshes. No extra building service or credential is required. These architectural details are procedural visual context, not a survey or verified facade inventory.

### National ground-data setup

Keep the original national downloads in `data/private/source/`, then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_national_ground
```

This creates `data/private/national/transmission.gpkg` with a spatial index, without clipping national coverage. PAD-US is queried directly from `data/private/source/PADUS4_1Geodatabase.gdb` (Fee, Designation, Easement and Marine layers, excluding Proclamation outlines). Override the paths with `HIFLD_NATIONAL` and `PADUS_NATIONAL`. National archives, indices, and caches remain private and ignored by Git. For northern Alaska wind screening, Copernicus GLO-30 supplies terrain beyond SRTM's latitude coverage. City-level screening still requires mapped footprints and complete resource measurements; an empty shortlist is valid, not proof that a city cannot use renewable energy.
