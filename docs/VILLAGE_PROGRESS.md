# Village-fire development progress

> Updated: 2026-10-05
>
> Plan of record: [`PROJECT_PLAN.md`](../PROJECT_PLAN.md)

## Current task: component browser replay and ignition selection (2026-10-05)

Independent components are now visible on the physical grid with mass-weighted
fuel/moisture, valid unburned temperatures, partial-burn state, time-censored
ignition details and raw component cards. Opt-in interactive runs preserve saved
physics configurations, imagery/labels/crop metadata and building IDs, with explicit
model matching and resource gates. 39 new checks and 791 repository tests pass;
actual desktop/mobile browser selection, submission and wheel resource checks pass.
See [replay contract](COMPONENT_VIEWER_V3.md), plan28.12 and the validation JSON in
`runs/component-viewer-validation-20261005.json`. Dynamic flame/wind and independent
field validation are the next work; historical pending statements below remain
records of their earlier stage.

## Previous finite receiver geometry and simpler defaults (2026-10-04)

The next geometry stage is now implemented: opt-in complete finite radiation
replaces inherited same/inter-cell edges, with clipped visible receiver polygons,
segmented flame-height integration and explicit terrain/receiver visibility.
50 fixed solves and 752 tests pass; 2996 non-timing files match across formal runs.
Upper fuels remain unignited; small-scene times shift slightly later. Large-domain
and field validation remain open. See [finite graph](FINITE_COMPONENT_RADIATION_V3.md)
and plan28.11. Next: expose actual component states in the browser before extending
dynamic flames/wind. The following 721-test paragraph records the preceding stage.

M02 `--moisture` is now optional with packaged defaults and explicit overrides;
evidence-only and unknown-material gates remain. V3 viewers display initial moisture.
Optional same-cell receiver rectangles now have orientation, offsets, aspect ratio
and one/two-sided reception; defaults preserve existing weights and solver states.
40 fixed geometry solves pass time-step, road and budget checks. The upper building
parts remain unignited; that negative result is retained. 721 repository tests pass.
Formal repeats compare 2303 non-timing files byte-for-byte. See
[receiver geometry](COMPONENT_RECEIVER_SURFACES_V3.md),
[default input implementation](MOISTURE_DEFAULT_POLICY.md#7-默认入口与网页展示已实现2026-10-04)
and plan28.9–28.10. Next: inter-cell finite surfaces, visibility and terrain/walls.

## Previous stage: image-based material inputs and fixed moisture defaults (2026-10-04)

V3 profiles already supply fixed initial live/dead moisture automatically. The user
should identify materials, not enter moisture per cell or implement remote-sensing
moisture retrieval before running. Internal pools and evaporation remain; default
values are synthetic scenario assumptions. A new read-only audit exports the existing
table and compares conserved-total-water pooling against the current defaults.
14 solves include a retained zero-water time-step failure and finer follow-up. No
solver/profile/viewer change or whole-suite rerun was made in this stage. See
[MOISTURE_DEFAULT_POLICY.md](MOISTURE_DEFAULT_POLICY.md) and plan28.8.

## Previous stage: independent component burn clocks (2026-10-04)

Optional `--component-curves-config` now resolves prescribed per-component HRR clocks
against the existing fuel-energy budget. Explicit peak-PUA ceilings reject inconsistent
inputs. The clocks change peak power, not fuel mass, area, moisture or ignition thresholds.
60 fixed clock-sensitivity solves preserve identical geometry graphs; all 30 time-step
comparisons and road/wet controls pass. Gaoyou's half-clock case adds one tree-surface and
three grass ignitions; upper tree/building parts remain unignited. These are synthetic
sensitivity results, not fitted material properties or field validation. Whole-repository
checks: 699 passed, five existing dependency warnings. See
[COMPONENT_BURN_CURVES_V3.md](COMPONENT_BURN_CURVES_V3.md) and plan28.7.

## Previous stage: finite-area radiation within component cells (2026-10-04)

Optional `--internal-radiation-config` now adds a closed diffuse cylinder to finite horizontal
receiver-patch exchange for two-component cells. It shares each source's existing radiative
budget with the frozen inter-cell edges. Overlap, unresolved third-layer visibility, failed
quadrature and source-budget/resource overruns fail explicitly. This is a synthetic geometric
experiment; inter-cell visibility and field calibration remain unfinished. See
[COMPONENT_INTERNAL_RADIATION_V3.md](COMPONENT_INTERNAL_RADIATION_V3.md) and plan28.6.

In56 synthetic runs,8 fixed crop runs and2 finer integration references, dt masks all agreed
(maximum timing difference0.75s). The close/extremely dry stress case ignited an upper component
without contact; restoring moisture or increasing the gap prevented ignition. Finer integration
changed its ignition time by1.75s. Gaoyou counts stayed8 shrub/12 surface/5 base cells of ONE
building, with no upper ignition. Full repository: **685 passed**,5 dependency warnings.
Next: observed component geometry/HRR, inter-cell finite-area visibility and a component replay.

## Previous completed stage: spread evidence and component partitions

The user prioritised improving the fire simulator before extending the planning study.
Current instructions are in plan chapter28 and
[VEGETATION_SPREAD_EVIDENCE_AND_FIX.md](VEGETATION_SPREAD_EVIDENCE_AND_FIX.md).
The original V3 provided zero convective gain. A bounded, explicit flame-contact closure now
adds Newton heating between face-adjacent fuels, with target-area and source-energy caps,
terrain/receiver-height checks, and independent energy diagnostics. Parameters are synthetic,
not fitted wildfire predictions; no original ignition thresholds or radiative fractions were raised.

The closure is saved through bundles/crops, run-v3 and simulate-v3 support
`--flame-contact-config`, and outputs/preflight identify it. Old bundles stay radiation-only.
Integer-temperature truncation in V2/V3 and invalid oversized radiation offsets in tiny domains
were fixed. Forty controlled runs passed time-step/count and energy checks. The existing289-cell
terrain crop changed from one to eight ignited shrub cells, with no building ignitions; this is
workflow verification using RGB/synthetic fuels, not validation against a real fire.

The next minimal component stage is implemented via `run-components-v3`, with independent
surface/upper fuel and combustible building base/upper states. It partitions existing live/dead
fuel and water, conserves per-cell HRR and energy, and caps all horizontal/vertical contact
by each component source's one nonradiative budget. It does not enable new combustible masonry.
Radiation is a conservative partition of the old cell graph; internal radiation and component
view factors remain unfinished. The existing single-state viewer does not consume these results.

See [VEGETATION_COMPONENTS_V3.md](VEGETATION_COMPONENTS_V3.md) and plan28.5. In64 synthetic
and4 fixed crop runs, both dt masks agreed (maximum ignition-time difference0.75s). The crop
ignited8 shrub cells,12 tree surface components, and5 combustible-base cells belonging to one
building; all upper tree/building components remained unignited. These are synthetic hypotheses,
not field validation. Full repository: **665 passed**,5 dependency warnings. Next: supported
component parameters/HRR, component radiation and dynamic flame geometry, independent calibration,
and component-specific replay. Existing DEM/UI/W01 work is retained; planning E5 remains deferred.

The W00 entry below is historical; W01 interfaces already exist in the working tree, while
coupled dynamic wind, crown transition and firebrands remain separate unfinished stages.

## Current extension: W00 complete, W01 next (2026-09-30)

The wind, flame-tilt and firebrand extension follows plan chapter 26. Its exact input/output
contract is [VILLAGE_WIND_FIREBRAND_CONTRACT.md](VILLAGE_WIND_FIREBRAND_CONTRACT.md).
The new physics is not implemented yet. Earlier progress entries below are historical.

W00 verified the existing Gaoyou V12 DEM bundle (5,447,216 stored edges), its elevation hash
and the manifest hash recorded by the historical V12 diagnostic. The current transport builder
still lacks a DEM argument; the previous implementation was located in a read-only stash,
without restoring it. W02 must restore the required capability deliberately.

`scripts/audit_village_w00.py` generated `runs/w00-gaoyou-baseline-20260930/`: two identical
30-second cropped runs have identical output hashes; a different ignition runs to 17 seconds.
The original case files are unchanged. These crops omit external edges and do not establish
full-domain equivalence. The baseline report hash is
`2b1927d97619acd2ac35238cd28c82e0806c71cb321f84da6aa6b2dcff4d44ec`.

Targeted solver/transport/physics/config tests: **46 passed**. New tests cover DEM round-trip,
cropping and integrity, legacy format compatibility, and a real HTTP job with a changed ignition
and duration. The current API implementation now rejects unsupported extra fields rather than
silently ignoring wind inputs. Ruff check/format, JS syntax and diff checks passed. The existing
8812 server was not restarted; its UI and old DEM replay remain accessible.

Next: W01 strict wind parsing and vector interpolation. W02 is needed before changed winds
can affect radiative transport; no new wind or firebrand controls are advertised as active yet.

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


2026-10-05：W02-a已加入独立部位火焰轴诊断及CLI `inspect-component-flames-v3`。瞬时规定HRR、干质量流率、AGA倾角、明确参考高度/折减、DEM基点和熄灭边界可审查；33项新测试通过。该诊断不改变求解器、传播或热传输，轴长/占地仍为显式合成假设，动态有限面传热待接。详见[说明](FLAME_AXIS_DIAGNOSTIC_V3.md)与PROJECT_PLAN 28.13。

2026-10-05后续：W02-b旋转闭合有限圆柱表面基础已实现，重新计算局部临界分段，保持面积/法向；26项新测试通过，与W02-a合计59项。含遮挡的刚体旋转不变，示例积分收敛。任意倾斜源连续相交门禁与步内动态传热仍待实现，不能称为风耦合蔓延。最终诊断d/e及源码快照保留。

2026-10-05后续：W02-c连续倾斜圆柱/矩形相交门禁完成，源积分前拒绝重叠受体与遮挡。新增14项测试，与W02-a/b合计73项。覆盖中部穿越且角点全部外部、端盖/侧面接触、旧门禁对照及随机查错。最终合成诊断f/g有当前源码快照；地形体积、源间几何和步内动态热传输仍待开发。

最终验收：864 passed、5条既有Rasterio警告；f/g两份诊断118文件相同，116个源码与全部输入哈希一致。记录见`runs/flame-geometry-validation-20261005.json`。

2026-10-05：W02-d/e已补地形体积门禁和动态辐射步内积分独立联测，33+36专项测试及1项轴坐标基连续性检查通过。贴地闭合圆柱倾斜入地下已被检出；联测采用明确1 m抬高合成源。原时间不收敛保留，局部细分后原容差通过；尚未改变CA传播。详见[说明](DYNAMIC_RADIATION_TIME_V3.md)与PROJECT_PLAN 28.16–28.17。

最终验收：934 passed，5条既有Rasterio警告；独立联测a/b 120文件相同，源码/脚本哈希核对通过。结果见`runs/dynamic-radiation-validation-20261005.json`，仍未接CA热状态。

2026-10-05用户收敛范围：基础四功能优先，使用单状态V3+固定材质水分+辐射/近邻接触。动态风/包络/有限面精化转为可选研究，不是完成前置条件。新增一键6案例基础验收和6项端到端测试；未验收剪切包络草稿已撤回。详见[基础范围](CORE_SIMULATOR_SCOPE.md)和PROJECT_PLAN 28.18。

基础范围验收：6个完整场景通过，正式产物`runs/core-acceptance-20261005-d/`及`runs/core-validation-20261005.json`；全仓库940 passed、5条既有Rasterio警告（59.01 s）。简单交互服务8849明确使用单状态V3，真实HTTP接口重选起火格/180 s重算成功；默认材质水分，无需高级几何。a–c为开发输出。未提交或推送。


### 28.19 基础模拟器接回村落地图（2026-10-05）

新增 `examples/village/vegetation_v3/run_core_map.py`，保留已有17×17裁剪案例的全部物理数组、水分、地形和图片定位，生成草地/灌木/树木/建筑四种起火对照。正式产物 `runs/village-core-20261005-b/`（a开发输出）。20分钟分别点燃3/8/193/164格，树木起火点燃86个建筑材质格；仅合成参数检查，不代表实测速度或结构损失。草地/灌木跨材质传播弱仍未解决，已定位源高度0.35/1.2 m与树木/建筑受体2 m的接触可达性限制；下一步校核低处可燃受体，保留简单模型，不盲目加大传播系数。

网页8850显示村落图片裁剪与建筑轮廓，可点击重选起火格；修复案例20分钟被初始化为10分钟。真实HTTP重算成功，6项基础测试通过，四种案例数组与父案例一致、a/b数值结果一致；lint通过。原8849四格案例服务保留。详见 `docs/CORE_SIMULATOR_SCOPE.md`。未提交或推送。


### 28.20 接触与辐射受体高度分离（2026-10-05）

完成基础单状态模型的可选按材质接触高度覆盖，不改变辐射图、默认水分、燃料或点火阈值；旧配置无覆盖时完全兼容。多部件模式显式拒绝此覆盖，继续使用自身部位定义。新增建筑低处可燃点0.2 m合成演示配置与可复现高度/时间步敏感性脚本。

正式产物 `runs/village-core-low-building-20261005-b/`；a开发输出。草地/灌木起火20分钟点燃166/164格，原基线3/8格。四组演示点燃89个建筑材质格，对应一个建筑标注，不是89栋建筑。0.1/0.2/0.5 m与0.25/0.125 s检查显示高度门槛明显，减半时间步点燃格数一致；不能把效果增强认定为现实验证。全仓库948 passed、5条既有Rasterio警告（61.37 s）；HTTP重算保存覆盖项，原数组全部不变，a/b结果一致。

网页8851显示低处受体演示并明确标注高度假设；8850基线保留。下一项是依据真实外墙/屋顶材质选择正式默认建筑受体；已询问用户，因为俯视照片不足以确认低处是否可燃，不能直接把合成高度推广到整个村落。基本机制开发与验证已完成，等待材质信息，不再扩展复杂几何或风。详见 `docs/LOW_BUILDING_CONTACT_V3.md`。


### 28.21 材质调查后补，继续基础生命周期与网页统计（2026-10-05）

用户确认建筑外墙/屋顶材质将于实地调查补充。该信息不再作为开发阻碍；当前继续使用明确标注的合成演示受体，待调查后更新正式参数。不扩大核心模型范围。

四种起火位置均延长到3600 s：点燃215格且全部燃尽；52个可燃格未点燃、保留851.76 kg燃料，最终平均HRR为0。另有29格仍处于受热状态，未达到点火条件。正式产物 `runs/village-core-lifecycle-20261005-b/`（a开发输出）及 `runs/core-lifecycle-validation-20261005.json`。核验燃料单调、燃尽时钟、耗尽质量为零及最终热释放为零。当前结果仍依赖演示材质，不能视为现实损失预测。

网页新增当前受热格、已燃尽格和剩余可燃燃料总质量，统计放在左侧以适配窄窗口。导出静态初始可燃燃料float64并按每格质量×剩余比例求和，不采用格比例平均。老回放缺少质量时显示破折号；独立部件模式隐藏基础统计，保持自身统计逻辑。修复新增说明段后部件模型说明定位不正确的问题。

3项新增测试验证质量加权、域外/未知排除及平铺/分块导出；全仓库951 passed、5条既有Rasterio警告（60.80 s）。真实HTTP分块重算保存质量文件和统计模块；浏览器末帧实查60分钟、215格燃尽、851.8 kg。8851已更新为60分钟基础演示，8850基线保留。未提交或推送。


### 28.22 窄窗口选格查询与点火信息的时间一致性（2026-10-05）

左侧起火选择区新增所选格行列、材质、当前状态与剩余燃料比例；窄窗口隐藏右侧查询栏时也能辨认所选格。检查发现基础回放会在起火之前显示最终记录的点火时间及原因；现在统一按回放当前时间判断事件是否已发生，屏蔽未来信息，同时保留点火后查询。

真实浏览器查询灌木8,8：0 s显示未燃和100%燃料，点火时间/原因均为破折号；400 s显示燃烧和84.4%燃料，点火5.85 min、原因热引燃，与求解器351.25 s吻合。HTTP重选灌木起火后，全部源数组（材质、水分、地形、传热等）与原案例一致。29项相关JS与输出测试通过；未修改物理求解器，无需重复上轮951项全仓库测试。记录 `runs/core-selection-validation-20261005.json`，截图 `runs/core-selection-20261005.jpg`。

8851保持60分钟演示并更新网页；参数仍是合成假设。实地材质后补，不阻碍开发；当前四项基础功能已具备并通过生命周期与回放核验，后续围绕实际发现的核心问题改进，不额外扩展复杂模块。


### 28.23 燃尽时间查询与HRR时间含义（2026-10-05）

新增首次燃尽时间查询，并在窄窗口选格摘要显示已发生的点燃/燃尽时间。未发生的燃尽事件保持隐藏，继续防止未来结果提前出现在回放查询中。

基础viewer的HRR现明确标注 `mean_over_last_solver_step_ending_at_sample`，网页显示“上一步平均热释放率”。求解器保持原值：草地恰在138.833333 s燃尽时，最后一步平均HRR约1000 W，表示该步内已释放热量；延后1 s为0。不能通过把末步HRR强制设零来破坏原始结果含义。新增两项边界测试检查燃尽质量、时钟及导出HRR不被改写。

73项相关求解器、基础生命周期和网页回放/导出测试通过（5.04 s），lint与差异检查通过。真实浏览器灌木8,8在0 s隐藏燃尽时间；600 s显示燃尽、0%燃料、9.85 min，与求解器591.25 s一致，HRR为0 W。真实HTTP分块重算包含明确HRR时间契约。验证 `runs/core-burnout-query-validation-20261005.json`，截图 `runs/core-burnout-query-20261005.jpg`。

8851网页已更新；未改变燃烧公式、默认材质水分或演示高度假设，实地数据仍后补。


### 28.24 基础交互兼容性、短时运行与失败恢复（2026-10-06）

修复服务器共享资源遗漏：服务器对旧回放提供最新app.js时，新增core-metrics.mjs也必须由相同共享资源处理器提供，避免旧保存结果缺少模块而加载失败。基础统计模块现在与网页脚本一致，使用JavaScript MIME及no-store响应。物理模型保持不变。

新增两项基础交互测试，涵盖缺少统计模块的旧viewer、1秒HTTP重算、不可燃格/非法时长/额外参数拒绝、失败后释放运行槽并成功重试、重试全部原数组保持不变。14项相关交互/输出测试通过（5.18 s）。实际地图API拒绝不可燃格，1秒重算得到[0,1]秒两帧，结果提供最新版共享模块；记录 `runs/core-interactive-reliability-20261006.json`。8851服务已重新启动；当天新输出位于 `runs/village-core-lifecycle-interactive-20261006/`。

当前基础传播、引燃、燃料消耗燃尽、传热与网页重算均已可运行；材质真实参数仍等待后续实地补充，继续使用带标注的演示假设。

全仓库回归：955 passed、5条既有Rasterio警告（60.50 s）；lint及差异检查通过。未提交或推送。


### 28.25 完整高友村基础系统与按需回放（2026-10-06）

基础网页现使用完整552×352网格、2 m原胞，覆盖约1104×704 m，保留卫星图、341个建筑标注及地形。此前17×17局部案例只能检验机制，不能代表全村系统完成；本轮已补齐完整村落计算与交互流程。

新增完整地图准备/计算脚本scripts/run_gaoyou_core.py及启动入口scripts/start_gaoyou_core.py。60分钟完整模拟累计引燃1529格、燃尽1464格、末时刻仍燃烧65格，剩余燃料1310114.088 kg。案例及结果存于runs/gaoyou-core-full-20261006；8851提供完整地图，独立重算结果写入runs/gaoyou-core-full-interactive-20261006。

为避免全村回放一次装入所有时间帧，基础分块viewer改为按需取当前帧，动态缓存限制为每字段两块，保留旧平面输出与部件回放兼容。新增缩放、拖动平移及全图复位；进度回调报告模拟时间和写结果阶段，不改变物理求解。

真实浏览器验证初始/中途/末帧、原胞生命周期查询、地图放大与平移；选灌木190,393执行1分钟全村重算后自动打开独立结果，无浏览器错误。全仓库957项测试通过（62.50 s），5条既有Rasterio警告；lint与差异检查通过。新增测试覆盖分块缓存、长度/帧范围验证及失败重试，并确认进度回调与原结果、能量预算一致。

基础软件功能已完成，科学预测尚需现场校准。材质默认水分、RGB分类、低处建筑接触高度仍是演示假设；74829格未辨明地表演示补为不燃，需特别避免把这种补全当作实测。实地材料按用户约定后补，不作为开发阻塞。操作与复现说明见docs/GAOYOU_CORE_SYSTEM.md。未提交或推送。

补充验收：启动入口在8852临时端口返回完整网格配置后正常退出；网页灌木重算案例46个源数组逐项SHA-256一致。连续播放、拖动时间轴暂停及按钮图标同步经真实浏览器验证；最终交互相关16项测试通过（5.20 s）。完整地图及火场截图、system-validation.json保存在runs/gaoyou-core-full-20261006。


### 28.26 保存结果检索与重启恢复（2026-10-06）

新增/api/runs及网页“已保存模拟”入口，从磁盘读取已完成结果的轻量元数据，列出初始演示和最近50组同模型、同地理网格的结果。无需加载模拟数组，不依赖上一个服务进程的jobs字典；忽略未完成、损坏与地图不匹配的文件。默认启动脚本复用既有完整村落交互结果目录，支持--output-root自定义，避免重启后历史列表消失。

真实浏览器在8851重启后恢复10组用户/开发交互结果，打开190,393的1分钟灌木模拟并返回60分钟初始演示；没有改动物理公式或源数组。17项交互与回放相关测试通过（5.63 s），涵盖重启恢复、运行中结果排除、损坏文件及异模型/异地理网格过滤、恢复后HTTP读取。启动脚本在临时8852端口同样恢复10组结果。lint、JS语法与差异检查通过。验证记录runs/gaoyou-saved-runs-validation-20261006.json，使用说明docs/GAOYOU_CORE_SYSTEM.md已更新。未提交或推送。


### 28.27 传播速度、拓扑与接触地形机制校核（2026-10-06）

完成7类场景×3时间步的21组定量对照，采用与完整村落一致的接触专用0.2 m建筑高度覆盖，辐射高度不变。连续草地/灌木/树木可传播，三类植被均可点燃邻接建筑等效燃料；1与0.25 s邻格点燃时间差最多0.5 s，结局一致。数值速度仅为合成模型输出，未作为真实野火校准值。

完整地图四邻593连通区、八邻369连通区，最大八邻70214格。多个起火点在同一大连通区却传播差异显著，排除“全是地图断路”的简单归因。草地159,353有三个可燃边邻，但地形/受体高度判据排除所有接触方向；固定17×17裁剪和辐射边，仅平坦化接触地形的600 s诊断对照由1格变11格。这是机制敏感性证据，未把平坦对照用于正式系统，也未修改公式。

网页新增基于初始燃料的边邻/对角可燃数，并明确74829格未知地表按不燃演示补全；不修改地图类别。18项相关测试通过（3.32 s），Ruff、JS语法及差异检查通过。说明docs/CORE_PROPAGATION_AUDIT.md；脚本scripts/audit_core_propagation.py和scripts/audit_contact_terrain.py；输出runs/core-propagation-audit-20261006-c及runs/core-contact-terrain-diagnostic-20261006-b。下一项物理校核针对坡地接触几何，不能用删地形或强制传播代替依据。未提交或推送。


### 28.28 有限受体高度带候选模型与全村对照（2026-10-06）

查阅USFS细燃料热交换、Sanchez-Monroy等2019坡地WFDS与实验对照、Gallacher等2018坡面池火实验。证据支持保留接触与坡度影响，但不标定本项目参数。实现简单候选闭合：受体代表点扩为有限垂向带，与源火焰区间的重叠长度占带宽比例用于原接触几何权重；地面以下截断，不改变辐射或热量预算。可选receiver_vertical_half_extent_m默认0，旧结果不变，候选±0.15 m为明确合成假设。树木高处限制、不燃间隙、部分重叠热量缩减、源非辐射上限和部件模式显式拒绝均经测试。

159,353局部600 s，半厚度0/.05/.10/.15/.20/.30 m分别引燃1/1/1/3/3/6格，1与.25 s时间步结果一致，不按动画扩散反向拟合参数。完整高友村60分钟候选引燃2020、燃尽1817、剩余燃料1306820.788 kg；原点方案1529/1464/1310114.088 kg。两案46个源数组SHA-256一致，仅接触配置不同；传播增加不等于现实准确性提升。

候选独立网页8853，案例runs/gaoyou-finite-receiver-full-20261006；8851原点演示和历史结果保留。真实网页1分钟重算保留高度带及46个源数组，无浏览器错误。模型说明显示高度带假设。修正模拟时长浮点尾数显示及1秒输入最小值。全仓库969项测试通过（62.59 s），5条既有Rasterio警告；lint、JS语法与差异检查通过。文档docs/FINITE_RECEIVER_CONTACT_V3.md及脚本scripts/audit_finite_receiver.py提供依据、公式、局限和复现。未提交或推送。


### 28.29 扩大接触验证与未知地表来源恢复（2026-10-06）

完成196组合成对照（7材质组合×7坡角×2受体模式×2时间步）及4起火点的16组冻结局部对照。坡角辐射几何同步重建，并用解析距离独立测试。212组点燃结局在1/.25 s一致，匹配点火最大差5.167 s。高度带对部分跨材质组合减少点燃，190,391裁剪由164降161；不能以扩散更多选默认。原点模式保持默认，有限带仍候选，复杂场景与实测验证尚未完成。

恢复原始v01来源：74829待核实、91570 RGB推测、22751建筑标注、5154域外；识别补全数据把未知误标为RGB的来源问题。新增可选material_evidence_index静态uint8数组，案例、裁剪、导出和重算携带，不参与物理。独立证据案例runs/gaoyou-evidence-full-20261006-b保存46个原数组和所有回放分块，哈希逐项一致。8853新增证据图层及选格来源查询；未知格仍按原不燃假设，尚未加入材质编辑或自动分类。

974项全仓库测试通过（62.89 s），5条已有Rasterio警告。新增证据不变性、物理不变性、存读/裁剪/回放、输入拒绝及坡地辐射距离检查。说明docs/RECEIVER_MATRIX_AND_MATERIAL_EVIDENCE.md、结果runs/receiver-matrix-20261006。8853复用原候选interactive历史目录；8851保持。未提交或推送。


### 28.30 可撤销材质修正及独立重算（2026-10-06）

8853新增选格材质修正：选择已有材质、填写依据、保存草稿、撤销最近一次修改；磁盘日志保留前后材质、依据、时间与撤销历史。草稿按基础案例全部数组与manifest的SHA-256隔离，带版本冲突检查，最多500次未撤销修正。原始案例和旧回放不修改，用户建议不标为实测。

勾选“将修正草稿用于新模拟”后，生成独立案例与回放。沿用原格覆盖率和目标材质固定水分，按覆盖率缩放燃料/水量/面积/功率/能量；验证现有材质参数组同质，拒绝模糊参数组。使用哈希匹配的原配置快照，先重建并逐项验证基础辐射连接完全一致，再根据新材质重建连接。当前支持静风、透明路径、原点辐射几何，不增添新传播机制；缺少依据配置或验证不一致则失败，保留原案例。受体高度带接触候选仍保留原配置。新增证据码3“人工修正建议(未实测)”。

完整352×552村落网页验收：24,77从未知不燃改为草地试验假设，干燃料2.4 kg、水分0.30，新连接5447264（原5447216）；其余所有原胞物理参数相同，基础46个原数组哈希不变。1秒独立模拟完成，结果d7361911b55b4a2e8480b2668ea94d21记录修正依据并标记历史。验收建议随后撤销，演示结果保留用于检查，不作为实测。修正结果需返回基础案例再修改/运行，防止在修正回放中误用基础参数。

978项全仓库测试通过（63.57 s，5条既有Rasterio警告）。覆盖保存/重启恢复/撤销审计/非法输入/版本冲突/来源限制/运行互斥/独立修正计算/新增辐射源及配置不一致拒绝。JS语法、Ruff与差异检查通过。说明docs/MATERIAL_CORRECTION_V3.md；验收runs/gaoyou-evidence-full-20261006-b/material-correction-validation.json。下一步批量框选修正及独立案例导出，不把复杂火焰、风或精细建筑模型加入完成门槛。未提交或推送。


### 28.31 自动核心验收与默认模型边界敏感性（2026-10-07）

按用户要求跳过材质修正/方案导出，完成代表性场景→热量与燃料约束→局部时间步→全村及网页回放。共34组模拟：7组合成机制控制、5个41×41局部场景×3时间步、5组352×552全村20分钟、4组灌木终点复核及3组草地几何敏感性对照。基础燃烧参数及原点受体默认均未改。

全村起火点/点燃格：草地147,266→1；灌木139,263→161；树木208,308→13；建筑176,276→217；植被建筑交界146,286→238。燃料非负/不增加、不燃格无引燃、燃尽后无持续HRR、水量有界、能量分配闭合均通过；提高引燃阈值的控制在源燃尽后单调冷却。全部205帧、六个末帧字段与求解器序列化结果一致。真实网页切换5组历史、核对始/中/终点及未来事件不提前显示，终点统计一致。

局部1/.5/.25 s采用事先声明的工程容差：小于50格点燃集合完全相同，大集合Jaccard≥.98，匹配点燃时刻95分位相对差≤5%，耗燃料相对差≤2%。均通过，但不表示严格逐格相同。灌木.25 s在1200 s恰多点燃1格；延长至1260 s并加.125 s，四时间步均182格，差异由终点截断解释。

关键未解决模型局限：草地点有4个可燃边邻，点接触几何却无出边；上坡受体超过火焰柱顶约0.0000122 m，下坡受体位于柱底之下。接触受体高度仅降低0.0001 m后点燃由1变11；辐射和燃料参数未变。DEM为float32，不能据此断言真实燃料不传播或反向拟合高度。候选有限高度带有独立既有记录，尚不升级默认。软件/时间步验收通过，科学模型冻结暂缓，下一步优先处理接触几何边界鲁棒性与有效受体范围依据，后续实测校准仍待采集。

发现并修复旧回放燃料图层缺陷：旧manifest无minimum/maximum，切换燃料时toFixed报错并遗留温度色标。增加兼容的0–1燃料比例范围、0–100%色标及无燃料格透明掩膜。浏览器复测燃料图层正常，无新增错误；新增旧manifest范围、非法色标、坏物理预算和坏回放分块拒绝测试。988项全仓库测试通过（87.09 s，5条既有Rasterio警告），Ruff/JS语法/diff检查通过。

结果runs/gaoyou-core-validation-20261006，说明docs/CORE_VALIDATION_20261007.md；默认验收回放8854可切换5场景，原8853候选系统保留。脚本scripts/validate_gaoyou_core.py、audit_core_validation_followup.py、start_core_validation.py可复现。未提交或推送。
