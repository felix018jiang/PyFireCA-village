# Village-fire development progress

> Updated: 2026-09-21
>
> Plan of record: [`PROJECT_PLAN.md`](../PROJECT_PLAN.md)

## Baseline

- Starting revision: `3ffe93ddb4b6956f5942405e365f6f0bfdaab4c6`.
- Reproducible Conda environment: `environment.yml`, environment name `pyfireca-village`, Python 3.13 with development, GIS and village extras.
- Before village changes: 333 tests passed; 5 Rasterio tests skipped because the optional GIS extra was not installed; Ruff check and format passed.

## Implemented

- T01: strict material catalogue, square-grid contract, village cell states, dynamic-state validation.
- T02: finite-fuel combustion, interval HRR, exponential effective-dose integration.
- T03: synchronous solver, direct/delayed ignition, finite burn duration, exposure ignition, deterministic cause precedence, sampled snapshots.
- T04: deterministic metric-radius exposure edges, wind-from conversion, outgoing-weight budget.
- T05 first supported input path: ordinary Pillow-readable image plus metric affine reference, local building polygons, exact per-cell material CSV, building/grid overlap audit, prepared numerical arrays.
- T06 first CLI/output path: `village prepare`, `village validate`, `village run`; final arrays, dynamic samples, cell/building CSV, run metadata.
- T07 static viewer: portable local assets, Canvas map, satellite/background image, building outlines, state layers, time controls and per-cell query. `village view` serves the result on loopback.
- T05 projected GIS adapters: projected RGB GeoTIFF imagery, WGS84 Polygon/MultiPolygon building GeoJSON reprojection, and exactly aligned integer material GeoTIFF plus code-map YAML.
- Scale guard: pre-allocation exposure-edge upper bound, a default five-million-edge limit, memory estimate in `village validate`, and persisted estimates in run metadata.
- Satellite-only trial path: `village bootstrap` builds explicitly synthetic building, material, ignition and weather inputs around a real projected GeoTIFF.
- LabelMe adapter: `building` rectangles/polygons in image pixels are transformed with the matching GeoTIFF affine. Bootstrap rasterizes those observed footprints into synthetic wood-material cells and selects the direct ignition from a building cell. Multi-building cells are retained and reported as a quality warning.
- `effective_exposure_ca_v2` physics foundation: strict evidence-catalog loader that preserves `null`; moisture-basis conversion; reduced surface heat balance; event-split water heating/evaporation; temperature ignition; energy-consistent growth/steady/decay burn curves; explicit receptor-area radiation; thermal timestep bound; and an independent synchronous v2 solver with an explicit piloted-ignition mask.
- `village validate-materials-v2` reports catalog readiness and missing runtime parameters without inventing defaults.
- Strict `village-v2-case-1` runtime workflow separates material, construction, transport and scenario values; derives steady burn duration from each cell's fuel-energy budget; rejects missing values and unsupported ignition modes; and writes state, temperature, water, fuel, HRR and ignition-cause arrays.
- `village validate-v2` and `village run-v2` expose that workflow through the CLI. The auditable two-cell fixture under `examples/village/v2_two_cells/` is explicitly marked as synthetic and has no claimed material calibration.
- V2 effective point-source transport derives directed radiation fractions from 3-D source/receiver distance, finite near-field regularization, atmospheric extinction, calibrated wind-driven flame tilt, receiver area and accumulated line-of-sight cell transmissivity. It rejects outgoing energy fractions above unity instead of silently normalizing them. Explicit diagnostic edges remain supported.
- `village validate-prepared-v2` and `village run-prepared-v2` resolve strict v2 material/construction mappings against an existing prepared grid. Building blocker cells come from the observed `building_overlap.csv`; the chosen background/building transmissivities and transport calibration are retained in the scenario/run record.
- The viewer now accepts `village-viewer-2` and switches among state, equivalent surface temperature and cell HRR layers. Temperature uses a linear scale, HRR uses `log1p`, units and extrema come from the manifest, and cell inspection reports both quantities.
- Self-contained synthetic two-building example under `examples/village/two_buildings/`.

## Verified in this development session

- Village tests: 20 passed before building-overlap output was added; rerun the current suite after every subsequent change.
- Full synthetic CLI path completed: prepare → validate → run, producing 20 ignited cells and 88 kg synthetic consumed fuel under the fixture parameters.
- Browser QA in Chrome confirmed image/building/state alignment, time stepping from 0 to 6 seconds, B001 cell selection, state change statistics and fuel percentage updates.
- Conda verification: Python 3.13.15; the complete suite has 391 passing tests with Rasterio enabled, and Ruff passes. The v1 synthetic example completed inside the Conda environment and was saved under `runs/conda-two-buildings-20260916/`.
- Projected GIS and bootstrap module verification: 24 village tests passed. A real high-resolution Gaoyou satellite GeoTIFF was bootstrapped at 10 m resolution into a 143 × 207 grid (29,601 cells), 1,148 synthetic buildings and 584,340 actual exposure edges. Prepare took 7.55 s, validation 1.47 s, and the 120 s scenario run 2.21 s on the development machine. It produced 2,682 ignited cells and a browser replay under `runs/gaoyou-synthetic-trial/`; those fire results have no empirical meaning because every non-imagery input is synthetic.
- Real Gaoyou annotation trial: matching 2040 × 1293 GeoTIFF plus 341 LabelMe building shapes, prepared at 2 m into a 387 × 610 grid (236,070 cells). Direct ignition cell `(6, 425)` intersects observed building `B0332` and uses the synthetic wood material. The graph has 4,699,486 edges; the 60 s run completed in 5.30 s, ignited 1,718 cells, and is stored under `runs/gaoyou-labelme-building-ignition/`. The 2,016 multi-building cells reflect dense/overlapping annotations and are retained as a quality warning. Fire results remain synthetic because materials and coefficients are placeholders.
- Central-building long trial: LabelMe bootstrap now chooses the building nearest the mean of all building centroids, then the cell with the largest overlap in that building. For Gaoyou this selects `B0233` at cell `(193, 285)`, close to grid center `(193.5, 305)`. The 300 s run completed in 11.25 s, generated 61 frames, ignited 93,470 cells, and is stored under `runs/gaoyou-labelme-central-300s/`. At 300 s, 25,968 cells were still burning; the run ended at the requested time rather than quiescence.
- The supplied `material-catalog-2.yaml` passes structural validation: 22 evidence entries, `production_ready: false`, and zero runnable village fuel materials. Every village fuel entry still has required `null` fields, so it is not used to run Gaoyou. Focused v2 tests cover catalog semantics, moisture conversion, irreversible evaporation, burn-energy conservation, geometric radiation, obstruction, wind tilt, synchronous thermal ignition, strict runtime loading and reproducible outputs.
- `material-catalog-3` now provides parameter-level evidence records with exact units, source IDs, page/table locators, measured/derived/assumed classification and specimen conditions. It contains 10 verified values for reference Douglas fir, southern pine and laminated bamboo specimens, and restricts numeric sources to supported research-publication types. Its 22-category Gaoyou inventory keeps local fuel parameters null. `validate-materials-v3` reports both sourced values and missing runtime material parameters; no v3 values are inserted into the v2 solver. See `docs/VILLAGE_MATERIAL_EVIDENCE.md`.
- The point-source reference run completed under `runs/v2-point-source-reference-20260917/`: the direct source ignited at 0 s, the receptor ignited thermally at 2 s, and both consumed their finite synthetic fuel budgets. The parameters have numerical-test provenance only.
- The prepared Gaoyou v2 integration run completed under `runs/gaoyou-v2-synthetic-30s/`: 387 × 610 cells, 2,822,874 generated radiation edges, 16 replay frames, 880 ignited cells and 2,339.248 kg synthetic fuel consumption at 30 s. Browser QA confirmed the satellite/building overlay and state/temperature/HRR switching. These fire values have no empirical meaning because all v2 profiles and transport coefficients are synthetic.
- The same Gaoyou integration scenario now accepts user-facing minute fields while retaining SI seconds internally. The 30-minute run under `runs/gaoyou-v2-synthetic-30min/` contains 61 half-minute frames and records both `end_time_s: 1800` and `end_time_min: 30`. Browser QA confirmed `0.00–30.00 min` display. All 236,070 cells eventually ignited, which exposes the current synthetic assumption that both background and buildings are burnable; it is not a real spread prediction.
- The corrected 30-minute run under `runs/gaoyou-v2-building-physics-v2-30min/` treats 208,329 unclassified background cells as inert and only 27,741 observed building cells as burnable. The building profile now includes 10% dry-basis moisture, evaporation, a 523 K ignition threshold, convection, reradiation, backing loss and an approximately 13-minute finite burn curve. It produced 201 ignited building cells: at 30 minutes, 48 were burned out, 153 burning, 214 heating and 27,326 unburned. Ignitions ranged from 0 to 29.8 minutes instead of consuming the whole grid by 9.8 minutes. These parameters remain an uncalibrated behavioral fixture.
- The current long run under `runs/gaoyou-v2-building-physics-150min/` extends that corrected scenario to 150 minutes while retaining a 1 s solver step. It stores 61 replay frames at 2.5-minute intervals. A total of 4,175 building cells ignited; at 150 minutes, 3,597 were burned out, 578 burning, 461 heating and 23,105 unburned. Building aggregation reported 38 burned-out houses, 24 burning houses, 12 heating houses and 267 unburned houses. The output is about 665 MB. These figures verify long-duration state transitions and browser replay, but the synthetic parameters remain uncalibrated.
- The final extended run under `runs/gaoyou-v2-building-physics-500min/` covers 500 minutes with 30,000 one-second steps and 61 replay frames. The solver now always appends the exact final state even when minute-to-second conversion introduces floating-point roundoff. At 500 minutes all 8,843 ignited cells had burned out, 18,898 building cells remained unburned, and the 208,329 background cells remained inert. Building aggregation reported 118 affected/burned-out houses and 223 unburned houses. The run consumed 371,406 kg of synthetic fuel and occupies about 665 MB. These values remain an uncalibrated behavioral fixture rather than a fire-risk prediction.
- Viewer building status is derived from `building_overlap.csv`. Building outlines change state without filling over the original cell-state layer; the inspector reports house state, and current/affected house counters are available for newly generated viewers.

## Current limits / next task

- Input preparation supports the local-image path and projected GIS path. Geographic or non-`uint8` imagery reprojection, polygon material rasterization, explicit line barriers and user-supplied domain clipping remain.
- The viewer is a completed-run replay. T08/T09 case import, click-to-ignite editing and asynchronous web-run service remain.
- The v1 effective exposure coefficients and all example values are synthetic. The v2 kernel, prepared Gaoyou grid, building-derived blocker mask and browser viewer are connected, but the evidence catalog still has no complete local material profile.
- Dynamic sample times use the first committed solver state at or after the requested interval. Early source exhaustion writes the final cooled state; denser analytic post-stop samples remain future work.
- Next implementation task is chapter 23 V04: implement height-aware radiation transport, resource estimation and reference-vs-vectorized edge checks using the V03 physics arrays. Convection, firebrands and crown layering remain later independent work.

## 2026-09-21 — Chapter 23 V00/V01

V00 froze the existing `runs/gaoyou-hypothetical-multimaterial/` comparison without modifying it. The working tree was already dirty and all existing edits were retained. The baseline revision was `1dc3f6c3b0d1ccd13c1ce85493759c4c9ec29832`; file hashes are stored in `docs/baselines/gaoyou-hypothetical-multimaterial-v00.sha256`. `conda run -n pyfireca-village pytest -q` completed with 398 passing tests and 15 Rasterio deprecation warnings. The existing RGB material classifier and all three building profiles remain explicitly synthetic; sparse and dense vegetation remain inert in the old scenario.

V01 added `src/pyfireca/village/vegetation_prepare.py` and the `village prepare-vegetation-spatial` command. It performs actual raster and vector reprojection, carries the source alpha/dataset mask into `domain_mask`, preserves Polygon holes and MultiPolygon parts, computes per-building intersections and union coverage, applies auditable building priority in mixed cells, and keeps unresolved non-building cells as unknown. Focused tests in `tests/village/test_vegetation_prepare.py` cover VT01, VT04 and the coverage portion of VT03.

The Gaoyou image centre selects EPSG:32649. EPSG:3857 has an 11.2455% centre scale error; UTM 49N has -0.01961%, and a 2 m eastward geodesic projects to 1.9996078 m. The resulting 2 m grid is 352 × 552. It contains 189,150 valid-domain cells, 5,154 NoData/outside cells, 341 buildings, 10,259 partial building boundary cells and no union coverage above 1. The 74,829 unresolved domain cells intentionally block final simulation.

B0233 was reselected from its transformed world geometry at new cell `(176, 258)`, whose centre is `(371461.0, 2874597.0) m` and whose B0233 overlap is 4 m². The old `(193, 285)` index was not reused.

Final V01 artifacts are under `runs/gaoyou-vegetation-v3/v01-spatial-utm49n-final/`; no fire simulation was run. The sibling directories without `-final` are earlier non-overwritten diagnostic builds. The final report records a five-point coordinate roundtrip error of `1.92e-9` pixel, projected-versus-geodesic building area error of `-0.03920%`, and cell-coverage area closure error of `-3.27e-7 m²`. Key SHA256 values are:

- `manifest.json`: `da4a6ece02500cf1bed284ac364cb3326aa84482f556f4936fcb1159003bbedd`
- `quality_report.json`: `009cbb16b6c82c047166500805146c61b76a0bb00a97c8c89de59ab63a8b35d4`
- `landcover.tif`: `9f55f6319f81d9cfda70393b41331cbf2a910f6a41b4beb95b3cd7d3c2d772a4`
- `buildings.json`: `06e40a15916ea3ca7b5fba3e670ce8dda49d37380f8e59154a53d38cd1f4e0aa`

The model contract, assumptions, commands and V02 handoff are documented in `docs/VILLAGE_VEGETATION_MODEL.md`.

Final verification after V01: `conda run -n pyfireca-village pytest -q` passed 402 tests with 17 Rasterio deprecation warnings; `conda run -n pyfireca-village ruff check src tests` and `git diff --check` passed; every V00 checksum revalidated successfully.

## 2026-09-21 — Chapter 23 V02

V02 added strict `vegetation_materials.py` and `vegetation_config.py` loaders plus a complete synthetic example under `examples/village/vegetation_v3/`. Every physical value carries an exact unit and provenance string. Grass, shrub, forest-floor, tree-equivalent and building fuels have independent profiles with explicit live/dead dry-mass fractions and moisture; bare/road has an explicit inert profile. The loaders reject missing keys, unknown keys, wrong units, invalid fractions, duplicate land-cover mappings, unsupported scope flags and synthetic values falsely marked production-ready.

The preflight applies V01 coverage once to dry mass, pyrolysable mass, active area and energy. It checks the integral of each growth/steady/decay HRR curve against its dry-fuel energy and refuses over-budget curves rather than truncating them. Direct ignition is resolved from world coordinates and rejected with row/column context when it targets unknown, NoData, outside or non-burnable cells. Focused tests complete VT06, VT13 and the remaining V02 coverage-budget part of VT03.

The final Gaoyou report is `runs/gaoyou-vegetation-v3/v02-profile-validation-synthetic-final/`. It resolves B0233 to cell `(176, 258)` and reports 114,321 burnable classified cells while retaining 74,829 unknown cells as a simulation blocker. Forest-floor area remains zero because V01 cannot observe it. `simulation_executed` is false and `production_ready` is false.

V02 artifact hashes:

- `profiles.synthetic.yml`: `5b67fb83c6ea6d4dfc3a95d70b8debde3f455de1cf7c7799c9ac2679e43dcd5d`
- `gaoyou-v02.synthetic.yml`: `c72c87759c06f43ac048b641dd64317cf283dd927a19ed78b0d33bb8e843886b`
- validation `manifest.json`: `a640f6a7990c1e7331364568d135c58759e46f6aa9fedc768c4cd10fc8695e8b`
- final `budget_report.json`: `4e8f2bd1f0807fc9f9ce7747d6e431f39c67f3b880f4a06e5bf0c7f101417e6a`

## 2026-09-21 — Chapter 23 V03

V03 added `src/pyfireca/village/physics_v3.py`. It resolves V01 coverage and V02 profiles into cell-area quantities without reusing the ground-cell fuel twice. The arrays retain live/dead dry fuel, live/dead water, total moisture, pyrolysable mass, active/receiver area, height, HRR curve, available energy and evidence/profile IDs. The implementation reuses the v2 exact burn-curve integrator but does not run a solver.

Focused V03 tests cover VT03, VT05 and VT07: 5 tests passed. The complete suite now passes 413 tests with the existing 17 Rasterio deprecation warnings; Ruff and `git diff --check` pass. The static Gaoyou package is `runs/gaoyou-vegetation-v3/v03-physics-synthetic-final/`; `simulation_executed` is false. It contains 1,941,412.23 kg initial dry fuel, 1,318,242.74 kg pyrolysable dry fuel, 478,623.61 kg water and exact zero total HRR energy closure in the report.

V03 artifact hashes:

- `manifest.json`: `3b1d5f8bb7115991dbd2d400d541ca30eb14cc8a8e4c4f2222a9bca1d86203a9`
- `physics_report.json`: `71df291a1f606eab0083dcfb1aa3c23a2bb8a5f81e7fb4bc1ed089dc9e25c17e`
- `arrays/initial_dry_fuel_mass_kg.npy`: `786e6a4bf79e612336d568be229aed0e552bc4521c2217698476bf53ff9e0946`
- `arrays/water_mass_kg.npy`: `7163c6bc38b917b180431922aa0ca6da6acd4cea0ae14153d124aec327bd446a`
