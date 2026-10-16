# Simple Simulator Completion Roadmap

> 2026-10-07更新：34组自动核心检验、205帧及5类网页统计核对完成，988项测试通过；旧燃料色标已修复。接触高度门槛的坡地敏感性仍未解决，优先处理该问题再科学模型冻结。详见[CORE_VALIDATION_20261007.md](CORE_VALIDATION_20261007.md)。

> 2026-10-06更新：单格材质修正、保存/撤销审计和独立重算已完成；覆盖率沿用原格、水分沿用目标材质，辐射连接经基础一致性验证后重建。978项测试通过。下一步批量框选与独立案例导出；详见[MATERIAL_CORRECTION_V3.md](MATERIAL_CORRECTION_V3.md)。

> 当前主线（2026-10-06）：完整高友村基础模拟、接触机制校核与未知材质来源管理，按用户后续简单范围要求执行。最新状态见PROJECT_PLAN 28.25–28.29；原点模式默认、有限高度带候选、974项回归通过。下方发布/风场/规划任务是各自路线的历史记录，不构成基础模拟器当前完成门槛。

> **Input policy, 2026-10-04:** aerial/RGB material identification uses existing fixed initial moisture defaults automatically; no per-cell moisture entry is required in the V3 profile workflow. Internal live/dead presets and evaporation remain. These are scenario assumptions, not image measurements; see [moisture policy and ablation](MOISTURE_DEFAULT_POLICY.md) and plan28.8. No runtime/profile changes or whole-suite rerun in this policy stage.

> **Current priority, 2026-10-04:** the user now prioritises vegetation fire simulation. [PROJECT_PLAN.md, chapter28](../PROJECT_PLAN.md#28-植被蔓延偏弱的证据核查与物理修正2026-10-04) and [the evidence/fix record](VEGETATION_SPREAD_EVIDENCE_AND_FIX.md) describe the new optional, energy-bounded near-field flame contact and actual checks (699 tests). An opt-in independent component experiment now exists; see [component evidence and limits](VEGETATION_COMPONENTS_V3.md) and plan28.5. Optional same-cell finite-area radiation now exists; see [radiation evidence and limits](COMPONENT_INTERNAL_RADIATION_V3.md) and plan28.6. Optional independent component HRR clocks with fuel-energy closure and explicit peak ceilings now exist; see [curve evidence and sensitivity](COMPONENT_BURN_CURVES_V3.md) and plan28.7. Inter-cell radiation remains frozen. The current single-state webpage does not use component results. Effective contact parameters remain synthetic; field validation is unfinished. Planning progress through P04 is retained in chapter27; its next experiments are deferred. Older scope/pending statements below are historical.

> Priority: **complete and freeze the baseline simulator before implementing PyFireCA-specific CA innovations**.
>
> Updated: 2026-08-13

> **2026-10-04 scope clarification:** this document remains the stable simulator release roadmap. The new pre-fire firebreak research sequence is defined in [PROJECT_PLAN.md, chapter 27](../PROJECT_PLAN.md#27-灾前防火线选址宽度与布局优化研究及开发路线2026-10-04). It starts with a reproducible, verified P00 research baseline and does not require completing unrelated extensions or publishing a release. Preserve the default simulator and introduce research variants explicitly; all new planning capabilities remain pending implementation.

## Goal

Deliver a small but complete, modern, reproducible wildfire simulator built on the validated PyFireCA foundations.

The target is not yet a full operational wildfire system. The first complete simulator is deliberately deterministic, static-weather, GIS-ready, scientifically traceable, and easy to audit.

## Completion definition

The baseline is complete when a user can run this workflow without writing internal framework code:

```text
configuration + raster inputs + ignition
        ↓
input validation
        ↓
standard fuel lookup
        ↓
Rothermel fire behavior
        ↓
Behave/Catchpole directional spread
        ↓
physical arrival propagation
        ↓
arrival/state/fire-footprint outputs
        ↓
run metadata + reproducible result directory
```

That end-to-end workflow is now implemented. Remaining work is release polish and freeze/readiness auditing.

## Phase S1 — stabilize current static core

**Status: complete.**

- [x] preserve synchronous CA architecture reference;
- [x] preserve static physical-arrival solver;
- [x] restrict physical baseline to immediate-neighbor propagation;
- [x] preserve north-up square-cell semantics;
- [x] keep experimental neighborhoods/interface coupling out of the default simulator;
- [x] maintain Ruff/pytest/CI quality gates.

## Phase S2 — fuel catalogue for normal use

**Status: complete for the first baseline.**

Current audited public catalogue:

```text
Anderson FM1–FM13
Scott–Burgan GR1 (101)
```

- [x] audit Anderson 13 directly from pinned Behave source;
- [x] preserve pinned source provenance;
- [x] add native-record regression tests;
- [x] verify all Anderson models convert and compute;
- [x] preserve existing FM1/FM2/GR1 Grade B regressions.

Remaining Scott–Burgan models are optional post-baseline catalogue expansion and do not block the first simulator freeze.

## Phase S3 — complete static landscape input

**Status: complete.**

Required raster layers:

```text
fuel model
1-h dead moisture
10-h dead moisture
100-h dead moisture
live herbaceous moisture
live woody moisture
midflame wind speed
wind-from direction
slope
aspect
```

Implemented:

- [x] GeoTIFF read support;
- [x] strict shape/CRS/full-affine alignment checks;
- [x] domain mask / NoData semantics;
- [x] explicit fixed units;
- [x] fail-fast errors;
- [x] north-up square metric-grid baseline;
- [x] documented preprocessing/input contract.

## Phase S4 — ignition and simulation entry point

**Status: complete.**

Public baseline API:

```text
IgnitionEvent
build_ignition_times
StaticWildfireSimulationRequest
StaticWildfireSimulationResult
run_static_wildfire_simulation
```

Supported:

- [x] one ignition cell;
- [x] multiple simultaneous ignition cells;
- [x] delayed ignition times;
- [x] duplicate-cell events with earliest-time resolution;
- [x] ignition/domain validation;
- [x] stable result object with state snapshots and summary metrics.

The default simulator uses the validated Moore-8 physical-arrival baseline and does not expose research variants.

## Phase S5 — outputs

**Status: complete for the baseline.**

Spatial outputs:

```text
outputs/
├── arrival_time.tif
├── state.tif
├── burned_mask.tif
└── perimeter.geojson
```

Run-level outputs:

```text
config.resolved.yml
metadata.json
environment.json
metrics.json
log.txt
```

Implemented semantics:

- [x] `arrival_time.tif` as float64 seconds with `-1` file NoData;
- [x] canonical terminal `state.tif`;
- [x] `uint8` 0/1 burned mask;
- [x] final burned footprint polygonization;
- [x] source CRS → WGS84 before GeoJSON serialization;
- [x] burned area / cell count / first-last arrival / runtime metrics;
- [x] Rasterio round-trip tests.

Run statistics have one canonical location at the run root; they are not duplicated in `outputs/`.

## Phase S6 — configuration and CLI

**Status: complete.**

CLI:

```bash
pyfireca validate config.yml
pyfireca run config.yml
```

Configuration version 1:

- [x] strict YAML schema implemented without a heavy config framework;
- [x] PyYAML is the only added runtime configuration dependency;
- [x] relative paths resolve against the YAML directory;
- [x] unknown/missing keys fail explicitly;
- [x] research-only switches remain absent;
- [x] console script registered in package metadata;
- [x] CLI unit tests;
- [x] real GeoTIFF CLI integration test.

CLI uses standard-library `argparse` and contains no scientific implementation logic.

## Phase S7 — reproducible run directory

**Status: complete.**

Current contract:

```text
runs/<run-id>/
├── config.resolved.yml
├── metadata.json
├── environment.json
├── metrics.json
├── log.txt
└── outputs/
    ├── arrival_time.tif
    ├── state.tif
    ├── burned_mask.tif
    └── perimeter.geojson
```

Metadata now includes:

- [x] PyFireCA/Python/platform information;
- [x] Git commit when supplied by execution environment;
- [x] resolved configuration;
- [x] raster geometry;
- [x] ignition events;
- [x] SHA-256 for every input raster;
- [x] encountered fuel models and pinned catalogue provenance;
- [x] runtime metrics.

The workflow validates inputs before creating a new run directory and refuses to silently overwrite a non-empty result directory.

## Phase S8 — examples and documentation

**Status: substantially complete; final example polish remains.**

Available:

```text
examples/minimal.py
examples/static_raster_rothermel.py
examples/static_run.yml
README.md
README.zh-CN.md
docs/RUNNING_SIMULATOR.md
docs/STATIC_RASTER_WORKFLOW.md
docs/ROTHERMEL_REFERENCE.md
docs/VALIDATION.md
```

Completed:

- [x] minimal synchronous CA architecture example;
- [x] in-memory static Rothermel raster example;
- [x] file-based version-1 YAML example;
- [x] detailed input contract;
- [x] CLI/config/output documentation;
- [x] scientific assumptions and limitations;
- [x] README links to detailed docs instead of becoming the manual.

Remaining desirable polish before freeze:

- [ ] add one explicitly heterogeneous two/three-fuel end-to-end example, or document why the existing heterogeneous integration tests are sufficient for the first tag;
- [ ] verify every documented shell command against a clean package install.

## Phase S9 — validation before baseline freeze

**Status: active final gate.**

Already satisfied:

- [x] existing Rothermel/Behave Grade B regressions remain protected;
- [x] deterministic synthetic arrival tests;
- [x] file-based GIS integration test;
- [x] output geometry/dtype round-trip tests;
- [x] CLI/config validation tests;
- [x] real YAML → GeoTIFF → simulator → output integration test;
- [x] Python 3.11/3.12/3.13 functional coverage;
- [x] separate GIS CI job.

Remaining release-readiness work:

- [ ] keep latest final commit all green, including Ruff format;
- [ ] build wheel and sdist in CI;
- [ ] install the built wheel in a clean environment and exercise `pyfireca --help`;
- [ ] verify GIS extra from a clean install path;
- [ ] add a release-readiness checklist;
- [ ] perform final docs/package audit;
- [ ] tag/freeze the first baseline only after the checklist is satisfied.

## Explicitly deferred until after baseline completion

These are not required for the first simple simulator:

```text
new PyFireCA-specific neighborhood method
adaptive/directional CA innovation
new interface-coupling research method
full Scott–Burgan 40 catalogue
WRF coupling
time-varying weather scheduler
NetCDF/xarray pipeline
rotated/non-square affine-aware geometry
crown fire
spotting
suppression
Monte Carlo
fireline-intensity/flame-length public output
FBP
Numba optimization
GPU / Torch / JAX
```

## Development rule

Until S9 is complete:

> Prefer release-readiness and reproducibility work over creating new scientific variants.

Any promising research idea should be written to `docs/FUTURE_RESEARCH.md` and deferred rather than implemented immediately.
