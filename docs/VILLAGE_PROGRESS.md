# Village-fire development progress

> Updated: 2026-09-22
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
- V05 now supplies the shared building–vegetation dynamic kernel on frozen synthetic small domains. Full-village workflow/output/viewer wiring is deferred to V06/M04, and Gaoyou remains blocked by 74,829 unresolved cells pending M05 field/spatial mapping. Convection and surface reradiation are active; environmental rewetting, firebrands, crown layering and structural damage remain later work.
- V06/M04 small-domain path now supplies `workflow_v3.py`, `outputs_v3.py`, `village-v3-case-1` bundles, `validate-v3`, `run-v3` and `view-v3`. V09/M06 adds `inspect-v3` and optional frame-chunk viewer output; V10 adds `preflight-v3` for full-domain classification, evidence and resource gates. The V3 viewer keeps V1/V2 compatibility and adds material/land-cover layers, dynamic fuel and heated-surface water, temperature-valid masks, ignition metadata, and separate vegetation counters. Hypothetical full-village execution is complete; research-grade evidence-checked Gaoyou materials are still pending.

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

## 2026-09-22 — Chapter 23 V04

V04 added `src/pyfireca/village/transport_v3.py`. It builds one directed radiation graph for every classified burnable building and vegetation cell, using each source's effective flame height, each target's receiver height/area, the configured wind tilt, atmospheric extinction and an explicit per-cell path transmissivity array. The implementation includes a direct small-grid oracle and an offset-vectorized row-block builder. Both use the same Bresenham intermediate paths and reject any source whose combined outgoing fraction exceeds one instead of normalizing it.

The new preflight counts candidate pairs with the real masks and boundaries before allocation. It checks both edge and memory limits, reports the 16-byte final edge layout, a conservative 64-byte-per-candidate construction peak, and the 33-byte-per-cell-frame replay basis. Resource failures state the estimated count/memory and do not modify the radius, grid or vegetation mask.

The first full Gaoyou graph attempt correctly failed: the synthetic shrub/tree receiver-area ratios of 1.2/1.5 allowed tree cell `(229, 504)` to allocate `1.29082468046` of its radiative power. The rejected forecast was preserved under `runs/gaoyou-vegetation-v3/v04-transport-resource-forecast-prebudget/`. The two synthetic ratios were revised to 1.0 with provenance `synthetic:v04_shared_radiation_budget_safe_bound`; fuel, moisture, HRR and ignition parameters were unchanged. Revalidated V02/V03 packages are under `v02-profile-validation-synthetic-v04-final/` and `v03-physics-synthetic-v04-final/`.

The accepted resource and graph report is `runs/gaoyou-vegetation-v3/v04-transport-resource-forecast-final/resource_report.json`. It built 4,486,644 edges between 114,321 classified burnable sources/targets. The maximum outgoing fraction is `0.8644472043124856`. Final edge storage is 71.786 MB, conservative construction peak is 287.145 MB, and a 101-frame in-memory replay estimate is 647.615 MB; combined estimate is 934.760 MB, within the configured 5,000,000-edge/2,048-MB limits. The assessment explicitly assumes path transmissivity 1.0 pending a calibrated blocker configuration and does not run a fire simulation.

Focused VT09/10/12/14 verification passes 5 tests. Updated artifact hashes are:

- `profiles.synthetic.yml`: `ab44393f578c76762d95d8d606e8c5cca701d76d9928142f518edbbec27b48d7`
- V02 revalidation `manifest.json`: `41ede993eb3514c9bac02f862f917446be97680bd975c32951919136ad748bc2`
- V03 revalidation `manifest.json`: `67f5a4b1fff2fb59d0e8f435f5e6eb9ab5b0f6ab5452636e81d24ded472ecaf4`
- V04 `manifest.json`: `d92ceee38f4507f11e702db0caa8383adaf7a4474528c2102bdfa7edd7e1dfe9`
- V04 `resource_report.json`: `50eedc2a4838e157d6ea71e5090fe68e75c910d715aa52077b7bcfb4897a9673`

V05 handoff: implement `solver_v3.py` and pass VT02/08/11/15/17 on frozen small-domain fixtures before any full-village dynamic run. The 74,829 unknown cells remain excluded from this forecast and continue to block a final full-village claim.

## 2026-09-22 — Chapter 24 M00

M00 added the first low-burden field survey contract in `src/pyfireca/village/survey.py`. A record now requires only `observation_id`, `object_id`, `component`, `geometry_ref` and `material_class`; optional `quantity_band`, `subtype` and `notes` describe construction information without asking for physical coefficients. Stable component IDs are `facade`, `roof`, `interior_wall`, `contents`, `ground` and `vegetation`. Stable material IDs cover wood, bamboo, thatch/straw, brick masonry, concrete/mortar, stone, metal sheet, grass, shrub, tree-equivalent fuel, litter, mixed contents, composite unknown and unknown.

The survey schema deliberately rejects `condition`, `dry`, `wet` and other moisture-band fields. Initial moisture is reserved for a separate time-dependent scenario package; fire-driven surface evaporation remains a solver concern. A concrete field guide is in `docs/VILLAGE_FIELD_SURVEY.md`, and the independent example is `examples/village/material_cards/survey.example.yml`.

M00 tests in `tests/village/test_survey.py` cover the example, stable enumerations, explicit unknown material, invalid component, missing geometry, duplicate observation IDs, optional quantity information, empty records and rejection of moisture fields. The new parser does not resolve evidence cards, construct fuel masses or run a fire simulation. M01 is next: build the minimal traceable material-card inventory and document which categories are still `pending_full_text` or `scenario_assumed`.

## 2026-09-22 — Chapter 24 M01

M01 added the strict `village-material-cards-1` inventory loader in `src/pyfireca/village/material_cards.py` and the traceable category library at `examples/village/material_cards/catalog.yml`. The library covers all 14 field material classes from M00 exactly once, records allowed components and `burnable`/`inert`/`unknown` classification, and attaches a source role and limitation to every non-empty reference. It intentionally contains no numerical physical parameters, moisture bands, or hidden defaults; an unknown key such as `parameters` or `condition` is rejected.

Two reference-only cards preserve the existing Douglas-fir/southern-pine and laminated-bamboo profile IDs without claiming that they are local measurements. Grass, shrub, tree-equivalent and litter cards point to the USDA standard fuel-model method while remaining `pending_full_text`; brick, concrete/mortar, stone and metal use the official reaction-to-fire classification as a bounded classification reference and also remain pending for assembly heat-transfer data. The EUR-Lex classification source is used only to document its product/end-use boundary, never to create an HRR or ignition value.

M01 tests in `tests/village/test_material_cards.py` cover complete category coverage, evidence-status counts, source/profile references, duplicate/missing-class behavior, strict rejection of numerical or dry/wet fields, unknown sources, and the permanent `production_ready: false` gate. The library is an inventory, not a runnable scenario; no existing run directory was modified and no full-village fire simulation was executed.

Verification for M01:

- `conda run -n pyfireca-village pytest -q tests/village/test_material_cards.py tests/village/test_survey.py`
- `conda run -n pyfireca-village ruff check src/pyfireca/village/material_cards.py tests/village/test_material_cards.py`

M02 handoff: add explicit assembly presets, quantity/coverage resolution and a separate initial-moisture state package. Preserve the card IDs and source roles; do not copy numeric values into the card selector or add field dry/wet choices.

## 2026-09-22 — Chapter 24 M02

M02 added three strict layers without changing the existing v2/v3 loaders:

- `src/pyfireca/village/assembly_presets.py` loads `village-assembly-presets-1`. Quantity bands hold fuel load, effective heated dry mass, active/receiver area ratios and heights. Base material/assembly values carry units and provenance. `steady_time_s` is prohibited in YAML and is derived only from the available dry-fuel energy and the growth/decay curve.
- `src/pyfireca/village/moisture.py` loads `village-moisture-initialization-1`. Initial water is a separate dry-basis state package with an explicit missing-value policy; the survey still has no dry/wet or condition field.
- `src/pyfireca/village/material_resolution.py` resolves a survey plus geometry areas into auditable quantities, source IDs, parameter provenance, assumptions, missing-value/error reports and a canonical `configuration_sha256`. It requires an explicit quantity band and area, detects multiple active components on one geometry reference, and rejects fuel curves that exceed the available energy budget.

The synthetic M02 examples are in `examples/village/material_cards/assemblies.synthetic.yml` and `moisture.initial.synthetic.yml`. Every assembly and moisture value is marked `scenario_assumed`; these files are parser fixtures and do not make the Gaoyou run evidence-checked. Inert brick, concrete/mortar, stone and metal presets resolve to zero fuel only when their explicit standard quantity band is selected; this does not remove heat transfer or structural effects from the future model.

M02 tests in `tests/village/test_material_resolution.py` cover parser strictness, mass/water conversion, energy-budget-derived steady time, deterministic hashes, missing quantity/moisture, inert material handling, active-component conflicts and evidence-mode rejection of synthetic assumptions. Focused M00–M02 tests pass; no existing simulation result was overwritten and no full-village dynamic simulation was run.

M03 handoff: use the resolved records to build a small-domain adapter into V03 cell arrays and V04 transport inputs. Preserve the one-time coverage/mass conversion, active-component decisions, source IDs and configuration hash; keep unknown or unresolved observations out of a research run instead of substituting a burnable default.

## 2026-09-22 — Chapter 24 M03

M03 added `src/pyfireca/village/survey_adapter.py`. It consumes only a runnable `ResolvedSurveyCase` plus an explicit `geometry_ref_by_cell`, `coverage_fraction` and `blocker_transmissivity` array. Each active observation is applied once; its resolved total area must close against the covered cell areas before dry fuel, live/dead mass, water, active/receiver area, HRR and energy are distributed to V03 arrays. Unassigned positive-coverage cells and overlapping active geometries fail before an array is returned.

The adapter returns validated `V3CellPhysicsArrays`, material-card profile IDs, evidence indices, a burnable source mask, a receiver target mask that can include inert receiving surfaces, the unchanged V04 transmissivity array and an `adapter_sha256`. The M02 assembly presets now carry explicit live/dead dry-mass fractions (summing to one); the adapter does not infer those fractions internally. It only prepares static inputs and does not advance ignition or state transitions.

The V04 small-domain path is covered by `tests/village/test_survey_adapter.py`: wood fuel mass is conserved across a partially covered two-cell geometry, brick remains a zero-fuel receiver, the direct V04 transport builder accepts the resulting masks, area closure is enforced, and unmatched positive coverage is rejected. Full-village V03/V04 packages and dynamic fire runs remain untouched.

V04 handoff: use the adapter result to build a frozen small-domain comparison fixture with explicit background transmissivity and run the V05 synchronous solver tests. Keep source/target masks, blocker assumptions and adapter hash in the fixture; do not infer blocker transmission from `burnable` or from material class.

## 2026-09-22 — Chapter 25 V05

V05 added `src/pyfireca/village/solver_v3.py`, the only V3 dynamic solver. It consumes V03/M03 cell physics and the single V04 radiation graph without filtering heat calculations by `building_id`. Buildings, grass, shrub, tree-equivalent fuel and litter use one synchronous clock and one outgoing-radiation budget. Known inert cells may receive radiation but cannot ignite or emit; unknown, NoData/outside, and known combustible cells retain distinct states and validation rules.

The solver separates total inventory water from the effective heated-surface water, integrates the finite-fuel HRR curve over every actual interval, commits ignition/burnout synchronously, cools susceptible surfaces after heat input falls, splits at delayed ignition and curve-end events, and samples exact requested times. Newly ignited cells emit from the next time step. Temperatures are explicitly marked invalid after ignition or burnout. Energy diagnostics retain released, radiative, intercepted, absorbed, inert-receiver and inactive-combustible terms.

The frozen small-domain fixture is `examples/village/vegetation_v3/v05-small-domain.synthetic.yml`, SHA256 `26f3452d692793ebc683519756b74c1d1c653860e6e1accdc0fc5a2442b35ed6`. In its strong three-cell grass chain, ignition occurs at 0.00/0.75/1.50 s and burnout at 2.25/3.00/3.75 s; 3.0 kg fuel releases 3,000 J, of which 160 J is intercepted and 2,840 J remains unintercepted. Its weak building-to-grass case reaches 302.5 K and remains heating without ignition. These values are synthetic numerical fixtures and were not tuned to expand Gaoyou spread.

`tests/village/test_solver_v3.py` and the M03 integration test in `tests/village/test_survey_adapter.py` cover O-T01–08/10 and VT02/08/10/11/15/17, including mixed building/vegetation propagation, within-tree cell independence, cross-road transfer, unknown/NoData, coverage scaling, cooling, delayed ignition, exact sampling, building-ID invariance, adapter equivalence, energy accounting and timestep/edge-order convergence.

No full-village 500-minute run was executed, no prior result was overwritten, and no commit or push was made. V06/M04 is now implemented for the synthetic small-domain path; M05 must map surveyed materials and resolve the 74,829 Gaoyou unknown cells before a research full-village run can be claimed.

## 2026-09-22 — V06/M04 small-domain workflow and viewer

V06/M04 added `src/pyfireca/village/workflow_v3.py` and `src/pyfireca/village/outputs_v3.py`. A `village-v3-case-1` bundle serializes the already-resolved V3 request: grid, masks, material/land-cover/building indexes, every V3 physics array, radiation edges, scenario events, unknown policy, and human-readable labels. Loading reconstructs `V3SimulationRequest` and runs the same strict checks; it does not select hidden material defaults.

The CLI now exposes `village validate-v3`, `village run-v3`, and `village view-v3`. A V3 run writes `run.json`, `parameters.resolved.yml`, `material_outcomes.json`, `energy_budget.csv`, final/event/snapshot arrays and a `village-viewer-3` directory. Existing V1/V2 manifests remain accepted. The web replay can switch among state, material, land-cover, temperature, HRR, remaining fuel and effective heated-surface water; it reports coverage, material/land-cover labels, water, valid-temperature state, first ignition time/cause, and separate building/vegetation counts.

The V3 bundle-to-viewer round-trip is covered by the additional V06 test in `tests/village/test_solver_v3.py`. It preserves non-building fuel metadata and checks binary frame sizes, labels, energy output and the explicit `includes_nonbuilding_fuels` run flag. This is a synthetic small-domain verification only; no Gaoyou V3 dynamic run, 500-minute run, or browser server session was started, and no existing result was changed.

V07/M05 handoff: create the first real Gaoyou V3 case from the UTM49N spatial package plus surveyed/explicitly assumed material selections, report all remaining unknown cells, choose the B0233 ignition from transformed world coordinates, and only then run the new CLI. V08/V09 must add block-streamed large-domain output and inspect the V3 replay at first, middle and final frames.

## 2026-09-22 — V07/M05 classified Gaoyou bundle and V08 crop checks

`src/pyfireca/village/gaoyou_v3.py` now assembles the frozen Gaoyou V01 spatial package, V03 physics arrays and V04 transport assumptions into a `village-v3-case-1` bundle. It rebuilds the directed graph and checks that its `4,486,644` edges match the V04 resource report. The output is `runs/gaoyou-vegetation-v3/v07-gaoyou-classified-v3-case/`; its manifest SHA256 is `5b74a4a521c064512b05918baef66fb79ee593c2196e4af29199d7805c053600`.

The bundle has a 352 × 552 UTM49N grid, 114,321 classified burnable cells, 341 building labels, 5,154 outside/NoData cells and 74,829 unresolved domain cells. It uses `unknown_policy: exclude` only for a clearly labelled classified-area demonstration; unknown cells remain `UNKNOWN`, are not source/target nodes and are reported in metadata. The B0233 ignition comes from `ignition.json` at world coordinates `(371461.0, 2874597.0)` and grid cell `(176, 258)`, not from an old hard-coded row/column. The bundle remains synthetic because its profiles and blocker transmissivity are synthetic assumptions; source hashes are stored in `source_manifest.json`.

`crop_v3_case_bundle` preserves world origin and remaps radiation-edge IDs for V08 small domains. Two new 600 s runs were made in new directories:

- `v08-gaoyou-b0233-crop-case` → `v08-gaoyou-b0233-run`: 33 × 33 cells, 864 classified burnable cells, 225 unknown cells, 34,558 edges, 11 frames, 21 ignited cells and 85.338727 kg consumed fuel. The crop contains 863 building cells and only one grass cell; that exterior grass cell was not heated within the 8 m synthetic radius, so no cross-class ignition is claimed.
- `v08-gaoyou-grass-rich-crop-case` → `v08-gaoyou-grass-rich-run`: 41 × 41 cells, 1,598 classified burnable cells, 83 unknown cells, 67,606 edges, 11 frames, one directly ignited/finished grass cell, six heated shrub cells, no indirect ignition and 2.28 kg consumed fuel. This is a valid no-propagation result under the frozen parameters; thresholds were not changed to force spread.

Both runs write `village-viewer-3` outputs with material, land-cover, temperature, HRR, fuel, surface-water and ignition metadata layers. No full-village dynamic run or 500-minute run was performed, and no old result was overwritten. V09/M06 now needs visual first/middle/final-frame inspection and large-domain streaming before any research full-village claim.

## 2026-09-22 — V09/M06 replay audit, spatial-output fixes and frame chunks

V09/M06 added `src/pyfireca/village/inspection_v3.py` and the `village inspect-v3` command. The command checks the viewer manifest, validates every representative first/middle/final frame through memory-mapped reads, records state counts and optional cell attributes, and compares `viewer` state/fuel values with `outputs/state_samples.npy` and `outputs/fuel_fraction_samples.npy`. The two Gaoyou small-domain reports are:

- `runs/gaoyou-vegetation-v3/v09-inspection/b0233-with-background-run2.json`
- `runs/gaoyou-vegetation-v3/v09-inspection/grass-rich.json`

Both report `visual_acceptance: true` with `state_samples_match: true` and `fuel_samples_match: true`. The B0233 run was regenerated in `runs/gaoyou-vegetation-v3/v09-gaoyou-b0233-with-background-run2/` after fixing two viewer-only mappings: V01 manifests store `background.png` under `artifacts`, and prepared building overlaps must subtract `crop_rows`/`crop_cols` before writing local cell IDs. The solver event and fuel totals are unchanged: 21 ignited cells and 85.338727 kg consumed fuel.

`outputs_v3.py` now accepts `chunk_frames` and writes dynamic state, fuel, temperature, validity, HRR and surface-water files under `viewer/chunks/` without creating a complete dynamic `np.stack` for the viewer output. The web client reads either the legacy flat files or the chunk manifest. A chunked Gaoyou result is at `runs/gaoyou-vegetation-v3/v09-gaoyou-b0233-chunked-run/`, generated with `--chunk-frames 4`; its manifest has 3 state chunks and it passes the same replay audit. Chrome was opened to `http://127.0.0.1:8797/` for inspection. This reduces output-side peak memory but the solver still retains its sampled snapshots; full-village execution remains blocked by 74,829 unknown cells and unresolved material evidence. No old result was overwritten and no commit or push was performed.

## 2026-09-22 — V10 full-domain preflight gate

`src/pyfireca/village/preflight_v3.py` and `village preflight-v3` now audit a serialized V3 case without constructing or running a fire simulation. The report counts known, unknown, outside/NoData, burnable, non-building burnable, source and receiver cells; groups classified area and initial dry fuel by material; estimates graph storage, construction peak, solver replay, viewer dynamic output and the requested 500-minute/5-minute sampling window; and emits explicit research-gate reasons. A case can be a runnable classified-area demonstration while remaining blocked for a research full-village claim.

The Gaoyou report is `runs/gaoyou-vegetation-v3/v10-preflight/gaoyou-v07.json`. Its 352 × 552 grid has 189,150 domain cells, 114,321 classified burnable cells, 91,570 non-building burnable cells, 74,829 unknown cells and 5,154 outside/NoData cells. The graph has 4,486,644 edges (71.786 MB final storage); the conservative construction-plus-101-frame solver replay estimate is 934.760 MB, below the 5,000,000-edge and 2,048 MB limits. The 353.245 MB viewer dynamic estimate assumes 18 bytes per cell-frame for the six V3 dynamic arrays.

The report status is `blocked` for research full-village execution for four reasons: `unknown_cells=74829`, `scenario_contains_synthetic_assumptions`, `simulation_scope=classified_area_only` and `unknown_policy=exclude`. No threshold, material coefficient or propagation radius was changed to pass the gate. The next handoff is field/material mapping or an explicitly reviewed unknown-area policy, followed by a new bundle with `unknown_policy=error` and an evidence level appropriate to the intended run. No full-village dynamic simulation was executed, no old result was overwritten and no commit or push was made.

V10 unknown-area review adds `src/pyfireca/village/unknown_review_v3.py` and `village review-unknown-v3`. It computes 4-neighbor connected components, 8-neighbor adjacency to known material labels, projected area and descriptive RGB/Excess-Green statistics from the prepared satellite background. The command writes `unknown_review.json` and `unknown_components.csv` without changing any serialized case array. The latest Gaoyou review is `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-with-survey-template/`: 74,829 unknown cells (299,316 m²), 2,278 components, EPSG:32649 at 2 m cells. The largest two components contain 24,268 and 23,334 cells; their adjacent known labels are mostly grass and building-equivalent fuel, which is a field-review prioritization signal rather than an automatic classification.

The report offers three explicit handoff policies: keep unknown and block research, assign unknown to an inert class only in a separately named demonstration bundle, or map reviewed regions to surveyed material classes. RGB summaries are descriptive only; no threshold, coefficient or propagation rule was changed. The generated `unknown_component_survey.template.yml` contains one `material_class: unknown` field-survey record per component and is parseable by the existing survey loader; it is a blank field-work template, not a classification. The latest JSON SHA256 is `8886874f609b11961c96457bb4761878e8d0b990424030cdc96b8622220f8878`, the CSV SHA256 is `957a3e33eae40cd7f5a6da1bc21e0e4874641aa14b67c2936b26b07242318fcc`, and the template SHA256 is `f7ad724d73564fc30cfe04d22c251a6b18d1096e6cdfa006304afd2a249ba544`.

`test_solver_v3.py` now includes a synthetic one-unknown-cell check that verifies component output, background statistics, output files and the no-mutation contract. The complete suite after this change is `481 passed, 19 warnings`; Ruff, formatting, JavaScript syntax and `git diff --check` also pass.

现场模板的下一道门禁由 `survey_mapping_v3.py` 和 `village validate-unknown-mapping-v3` 提供。它要求 CSV 中每个未知分区都有至少一条匹配的 `v3-unknown-component/Cxxxx` 调查记录，允许用 `/part-*` 后缀拆分混合区，并将 `unknown` 与 `composite_unknown` 保持为阻断项；它只检查空间引用和材料身份，不解析燃烧参数、不写原胞数组。对当前未填写模板的验证结果是 `blocked`，2,278/2,278 分区已引用但仍有 2,278 条未解析材料，报告为 `mapping-validation-template.json`。只有材料身份完成后，才进入 M02 卡片/构造预设解析和新的 V3 bundle 构建。

M02 解析门禁由 `resolution_workflow_v3.py` 和 `village resolve-survey-v3` 提供。它要求独立的 `village-geometry-areas-1` 面积文件，并复用现有材料卡、构造预设和初始水分包；输出完整的参数来源、问题列表、配置 hash 和输入 hash，但不生成原胞数组。当前高友村模板解析报告为 `blocked`，2,278 条 `unknown` 记录均未进入可运行参数，报告在 `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-m02-template/resolved-template.json`。同目录的 `unknown_geometry_areas.template.yml` 提供了按未知分区面积生成的初始模板；如果现场拆分分区，必须同步修改几何面积，不能把父分区面积重复分配给多个子记录。

M03 空间审查补充了 `unknown_component_id.npy`，将 2,278 个 4 邻域分区逐格写回 352 × 552 栅格，并在 `validate-unknown-mapping-v3` 中核对 raster 与 CSV 的分区 ID、格数和映射完整性。最新高友村 M03 目录为 `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-m03-spatial-review/`；当前模板的空间映射完整但材质仍全部未知，因此验证状态仍为 `blocked`。这一步仍不替换 V01/V03 材质数组，避免把分区统计误当成正式原胞参数。

在用户明确要求先使用卫星推测后，新增 `rgb_hypothesis_v3.py` 和 `village complete-rgb-hypothesis-v3`。它复制 V01 空间包到新目录，只将原 74,829 个 unknown 域内格设为 `bare_or_road`、覆盖率 1.0、证据 `rgb_hypothesis`，保留建筑标注及已有草/灌/树 RGB 候选；不修改原 V01 包，不创建燃烧参数。产物为 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-spatial/`，最终域内 189,150 格均有材质索引，裸地/道路推测 74,829 格，报告 SHA256 为 `4b91f92866ce7a7e2de5a036b65118d426e2d568a2f421f2020e447627d7af24`。

尝试将该推测地图接入动态 V3 全域图时，保留原 8 m 辐射半径和 5,000,000 边上限会得到 5,447,216 条候选边，因 `RESOURCE_LIMIT` 被拒绝；没有提高上限、缩小半径或覆盖旧结果。当前交付是可审查的完整假设材质地图，动态全域运行仍需单独资源决策，且只能称为 `hypothetical_only`。

## 2026-09-22 — V10 全域资源敏感性评估（未运行火灾）

为决定 RGB 推测地图能否进入动态求解器，新增 `src/pyfireca/village/resource_sensitivity_v3.py` 和 `village resource-sensitivity-v3`。它读取已复制的 RGB 完成空间包与冻结的 V03 物理数组，使用与 V04 构图器完全相同的候选源—受体计数器；只计算资源，不构造边、不启动求解器，也不改变传播阈值、材料系数或 8 m 当前半径。诊断半径 4/6/8/10 m 只是对照情景，不是自动调参。

真实高友村报告为 `runs/gaoyou-vegetation-v3/v10-rgb-resource-sensitivity/report.json`，SHA256 为 `821ae5191ef4b9549c38f5a0d594528eff99f190196466a788bbcebb647629a8`。输入是 `v10-rgb-hypothesis-spatial`、`v03-physics-synthetic-v04-final` 和 `case.snapshot.yml`；500 分钟、5 分钟采样对应 101 帧。完整推测域包含 189,150 个受体，其中 114,321 个可燃源、74,829 个惰性受体，5,154 个域外/NoData 格仍不参与计算。

| 诊断半径 | 候选边上限 | 构图峰值 | 构图+101 帧求解器回放 | 当前门禁 |
|---:|---:|---:|---:|---|
| 4 m | 1,366,863 | 87.479 MB | 735.094 MB | 通过 |
| 6 m | 3,182,983 | 203.711 MB | 851.326 MB | 通过 |
| **8 m（当前）** | **5,447,216** | **348.622 MB** | **996.237 MB** | **边数超 5,000,000，阻断** |
| 10 m | 9,058,037 | 579.714 MB | 1,227.330 MB | 边数超限，阻断 |

当前 8 m 情景的内存估算仍低于 2,048 MB；阻断原因只有候选边门限。若要保留 8 m 物理情景，下一步应由用户审阅后将资源配置显式提高到至少 `max_edge_count=5,447,216`（建议留出审计余量），再重新生成 bundle 并做构图/预检；本轮没有替换配置，也没有生成动态全域结果。若不提高边数上限，4 m 或 6 m 只能作为另行批准的敏感性情景，不能悄悄替代当前 8 m 案例。

新增测试验证惰性受体计数和“只报告不构图”契约。当前完整测试为 `482 passed, 19 warnings`；资源报告生成命令、输入 hash 和结果 hash 已写入上述产物。下一交接是 V10/M06 资源策略审阅，之后才可执行一次新的动态 bundle 构图；仍不能把 RGB 推测或 synthetic profile 当作真实预测证据。

## 2026-09-22 — V10 全域 RGB 推测演示完成

资源策略采用显式覆盖而不是修改原始配置：`build-gaoyou-rgb-v3` 新增 `--max-edge-count` 和 `--max-memory-mb`，本轮使用 `max_edge_count=6,000,000`、`max_memory_mb=2,048`。原始 `case.snapshot.yml`、V04 资源报告和旧 bundle 均未改写。构建时修正了一个接线问题：材质/地类索引必须从 RGB 完成的 V01 空间包读取，不能继续读取 V03 中保留 `-1` 的原始 unknown 索引，否则惰性受体没有正的接收面积。

新的 500 分钟 bundle 为 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-full-domain-case-6m/`，manifest SHA256 为 `a938c671a06d424ccb078f8697183860a2892aaac16d5a459b44523df5775a90`。它使用 EPSG:32649、2 m 格网、B0233 世界坐标起火点 `(176, 258)`，共有 5,447,216 条边、189,150 个已知受体、114,321 个可燃源和 74,829 个不燃 RGB 推测受体。预检报告为 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-full-domain-preflight-6m/report.json`，资源门禁已通过；研究门禁仍明确阻断 `scenario_contains_synthetic_assumptions` 和 `simulation_scope=hypothetical_full_domain`。

先完成 30 秒烟测：`v10-rgb-hypothesis-smoke-run-6m/` 产生 7 帧、1 个直接起火原胞和 48 个受热原胞；首/中/末帧审计通过。随后完成 500 分钟全域演示：

- 结果目录：`runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-full-domain-run-6m/`
- `run.json` SHA256：`714cc7a036ca2163ce0d1261a55a8b418430e41008ffc541f988823c9e323b9a`
- 运行状态：`completed`；结束时间 30,000 s；101 帧；30411 个实际时间步
- 首次点燃原胞：6,314；消耗可热解燃料：215,329.386906 kg
- 末帧状态：74,829 个惰性背景格、108,007 个未燃可燃格、6,314 个燃尽格、5,154 个域外/NoData 格
- 分块 viewer 目录约 521 MB；审计报告 `v10-rgb-hypothesis-full-domain-inspection-6m.json` 的 `visual_acceptance=True`、`state_samples_match=True`、`fuel_samples_match=True`

网页服务已启动在 `http://127.0.0.1:8801/`，用于检查这次全域演示。该结果可以用于网页和算法流程验收，但不能解释为高友村真实火灾预测：RGB 分类、燃烧 profile 和路径透射率仍是 synthetic/假设输入。下一步应做求解器性能优化和材料证据替换，再重新生成研究级 bundle；不应直接用这次结果校准阈值或证明现实传播范围。

网页初次没有底图的原因是运行命令没有传入 `--prepared`，V3 输出因此按兼容模式生成了 `background: null` 和默认 `image_ref`。已修复 bundle 元数据自动携带空间资产目录的逻辑，并对本次已有 viewer 做了只读资源修复：复制 `background.png`、真实 `image_ref.json`、建筑几何和 341 个建筑原胞索引，未修改任何状态、燃料或模拟数组。修复后通过 `curl` 验证 `background.png` 返回 200，重新运行 `inspect-v3` 仍为 `visual_acceptance=True`。完整测试现为 `483 passed, 19 warnings`。

## 2026-09-22 — V10 活跃火源传热索引

全村 500 分钟演示暴露出一个纯计算瓶颈：旧求解器每个时间步都会对全部 5,447,216 条辐射边计算一次，即使当时只有一个原胞放热。`exposure.py` 新增 `SourceIndexedExposure`。由于 `ExposureEdges` 已按 `(source, target)` 排序，它只需一个长度为 `cell_count + 1` 的偏移数组；稀疏火线仅累计当前正功率源的边片段，活跃边达到全图 25% 时回退到原向量化实现。辐射边、边权、HRR、时间步、点燃判据和材料参数均未改变。

真实 8 m 高友村图的内核基准记录在 `runs/gaoyou-vegetation-v3/v10-active-source-benchmark/report.json`（SHA256 `ebe0a0d693f8146822ba6acd371f96a68d6c421201465870fd3a23106de98cdc`）。单个活跃源由约 20.20 ms 降至 0.174 ms（116.4×），1,000 个源为 14.3×，10,000 个源仍为 1.54×。30 秒完整求解器的三次运行中位数由 2.072 s 降至 1.521 s（1.36×），见 `solver-smoke-timing.json`。

优化后的新结果在 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-smoke-run-indexed/`，没有覆盖原烟测。与旧烟测比较的 12 个输出数组全部精确相同，`run.json` 也相同；回归报告 SHA256 为 `6d01d752cce2eb767ea515cda6c1f4795c966bd4e7d2c04caa15c47261d75fbb`，网页首/中/末帧检查继续通过。完整测试为 `488 passed, 19 warnings`，Ruff、格式检查、JavaScript 语法和 `git diff --check` 均通过。现有 500 分钟结果没有重复生成；下一个性能工作是量化每步对全部未燃/受热原胞做热平衡计算的成本，再决定是否引入可证明等价的活动受体集合。

全域页面随后出现“底图消失”的视觉问题。底图文件、清单路径、仿射参数和 HTTP 响应均正常；直接检查 `background.png` 仍是正确的彩色高友村卫星影像。问题由三项共同造成：页面的卫星底图开关一度关闭；开启后底图仍以 0.78 透明度绘制并被 74,829 个不燃格的半透明灰色状态层覆盖；普通 Chrome 标签页还缓存了修复前的 `image_ref.json`，使底图被绘制到当前网格范围之外，因此切换开关没有视觉变化。现已让底图按原色不透明绘制，将不燃状态覆盖透明度从 115/255 降到 28/255，未燃仍保持透明；JSON 空间清单使用 `cache: no-store`，底图和模块 URL 带版本号。受热、燃烧、燃尽及 unknown 的颜色不变。无缓存 Chrome 验证截图显示彩色卫星图、341 个建筑轮廓和起火格正确对齐；修改同时写入源网页资源和该全域 viewer 资产。

### V10 热平衡向量化回归

性能剖析显示 `advance_surface_heating_and_evaporation` 原先在每个时间步通过 Python 循环逐个处理约 114,000 个受体，30 秒烟测累计约 342 万次 Python 原胞迭代。现已将“冷却 → 加热至沸点 → 蒸发 → 干质量升温”四个守恒分支改为 NumPy 掩膜计算，分支顺序和水量不可恢复规则保持不变。

新的 `v10-rgb-hypothesis-smoke-run-vectorized/` 仍产生 1 个直接点燃原胞、0.046125 kg 消耗燃料和 7 帧结果；与活跃火源索引版本的 12 个输出数组逐字节一致，`run.json` 也一致，viewer 检查 `visual_acceptance=True`。求解器单独运行中位约 0.59 s，较向量化传热前约 1.52 s 提升约 2.6 倍；34 个物理/求解器测试通过。新的剖析热点为燃料曲线积分、状态数组复制和求解器主循环，下一步应针对这些部分做等价性基准，不得用减少状态或采样精度替代优化。

随后将有限燃料曲线改为只对当前 `burning=True` 的原胞计算两个反导函数，非燃烧原胞直接保持零放热；密集燃烧时仍使用相同的逐元素曲线公式。新结果在 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-smoke-run-sparse-hrr/`，与向量化热平衡结果的 12 个数组和 `run.json` 全部精确一致，首/中/末帧审计通过。30 秒求解器剖析总耗时约 0.42 s，HRR 积分约 0.03 s；完整测试仍为 `488 passed, 19 warnings`，Ruff、格式、JavaScript 语法和差异检查通过。现阶段不需要为性能重复生成 500 分钟结果；下一项只在必要时分析状态数组复制和采样分配。

状态与采样基准记录在 `runs/gaoyou-vegetation-v3/v10-active-source-benchmark/state-sampling-benchmark.json`，SHA256 为 `8b6e1f93ff2e92739d0858f9cfb6634a68478b6f04f4950d948a920433450433`。同一 30 秒案例在 5 s、30 s 和不采样模式下的无 profiler 中位耗时分别约为 0.405、0.445 和 0.433 s，采样不是当前 CPU 热点。快照 payload 为每帧约 6.61 MB；7 帧约 46.2 MB，按 500 分钟 101 帧约 667 MB。当前实现仍可运行，但后续全域重跑应优先考虑求解器到 viewer 的流式快照写入，避免在内存中长期保留所有帧。

## 2026-09-23 — V10 流式快照写入

`solver_v3.py` 新增可选 `V3SnapshotSink`，`outputs_v3.py` 新增 `V3StreamingSnapshotSink`。默认 V3 求解器仍保留内存快照；只有 `village run-v3 --chunk-frames N` 走流式路径：每个精确采样帧写入临时 staging 的固定 dtype 分块，同时生成 `state_samples.npy`、`fuel_fraction_samples.npy`，然后复制到新的 run 目录并由 viewer manifest 引用。六类动态数据和既有分块协议完全保留，未删除温度有效掩膜或改变采样频率。sink 在关闭时校验预期帧数，工作流结束后自动清理临时目录。

本轮 V3 round-trip 测试新增了流式/内存输出逐数组比较；V3 测试 `27 passed`，完整测试 `488 passed, 19 warnings`，Ruff、格式、JavaScript 语法和 `git diff --check` 均通过。新的高友村 30 秒独立烟测目录为 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-smoke-run-streamed/`，命令使用 `--chunk-frames 2`，7 帧、1 个直接起火原胞、0.046125 kg 消耗燃料。`village inspect-v3` 的结果为 `visual_acceptance=True`、`state_samples_match=True`、`fuel_samples_match=True`；状态、燃料比例和采样时间与 `v10-rgb-hypothesis-smoke-run-vectorized/` 完全一致。旧烟测、旧全域 500 分钟结果和既有网页服务均未覆盖或重写。

这一步只解决快照的内存生命周期，不改变火焰传播、材料参数、资源门禁或 unknown 策略。V06 下一步仍需用户审阅 synthetic/RGB 假设和未知区域处理；在此之前不执行新的 500 分钟全村模拟。若获准重跑，应使用流式分块输出并保存新的资源、耗时、manifest hash 与首/中/末帧审计。

## 2026-09-23 — V06 状态提交热点基准

流式快照完成后，对同一个 `v10-rgb-hypothesis-smoke-case-6m` 进行了 cProfile/tracemalloc 对照，产物为 `runs/gaoyou-vegetation-v3/v10-active-source-benchmark/state-commit-profile.json`，SHA256 为 `8a2d8799f4eb559525f690ef221e096ebd430f402bf97f71332152b606756c7d`。5 s、30 s 和不采样模式均为 30 个物理时间步，分别保留 7、2、0 帧；tracemalloc 峰值约为 107.3 MB、74.3 MB 和 64.2 MB。

剖析的主要 CPU 时间仍在表面热平衡、净表面热通量、有限燃料区间积分和稳定时间步限制。NumPy 数组复制一次烟测约 0.012–0.014 s，采样数量明显影响内存峰值，但没有显示出值得现在重构数组布局的 CPU 收益。因此没有引入活动受体裁剪或改变同步提交规则。后续要优化状态提交，必须先在更长时长/更大域上证明它成为主要热点，并逐时间步比较温度、燃料、水量、状态和能量数组。

网页验收时发现独立 smoke bundle 没有保存 `spatial_assets_dir`，因此第一次流式结果的 manifest 为 `background: null`；这属于输出输入接线问题，不是求解器或分块协议问题。保留该诊断目录后，使用同一案例显式传入 `v10-rgb-hypothesis-spatial` 生成 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-smoke-run-streamed-background/`，底图、建筑几何和 B0233 起火格对齐，后端审计不变。带底图 viewer 已在本机 `http://127.0.0.1:8802/` 提供检查；原无底图目录未删除或覆盖。

## 2026-09-23 — V06 全域流式 500 分钟演示

在用户要求继续推进后，使用现有 RGB 推测材质 bundle 和显式空间包完成新的全域流式运行：`runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-full-domain-run-streamed-6m/`。场景时长 30,000 s（500 min），每 300 s 采样，共 101 帧、30,411 个物理时间步；6,314 个原胞首次点燃，消耗可热解燃料 `215329.386906 kg`。运行使用 `--chunk-frames 8`，没有在求解器结果对象中保留全部动态帧。

`inspect-v3` 报告 `visual_acceptance=True`、`state_samples_match=True`、`fuel_samples_match=True`。新结果与既有 `v10-rgb-hypothesis-full-domain-run-6m/` 的 12 类最终/采样数组逐项比较，均为 `equal_nan=True` 且最大绝对差为 0。manifest、inspection 和 run hash 分别为 `195d3ff69f6c1c25fb4aff6a008f5b82ff54db35df38186f1fa994e43c987c1a`、`f3b075080e0a069cc9d101ffe1e989f2abb06c8402b340999ff32dec9226bc75`、`714cc7a036ca2163ce0d1261a55a8b418430e41008ffc541f988823c9e323b9a`。全域网页已在 `http://127.0.0.1:8803/` 启动，底图、341 个建筑轮廓、B0233 起火格和 101 帧时间轴已通过截图核验。

该结果只是当前 RGB 分类、synthetic profile 和路径透射率假设下的可复现演示，不能解除 unknown 区域现场核查、材料证据和真实透射率校准门禁，也不能用于校准传播阈值。旧 500 分钟目录未覆盖，未执行 commit 或 push。
