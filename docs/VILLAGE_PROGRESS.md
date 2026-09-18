# Village-fire development progress

> Updated: 2026-09-16
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
- Next implementation task: add a distinct convective transfer channel and firebrand/spot-ignition events, then calibrate local material and transport parameters before any risk-oriented Gaoyou run.
