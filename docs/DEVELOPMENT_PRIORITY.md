# Development Priority

> 2026-10-06更新：单格材质修正、保存/撤销审计和独立重算已完成；覆盖率沿用原格、水分沿用目标材质，辐射连接经基础一致性验证后重建。978项测试通过。下一步批量框选与独立案例导出；详见[MATERIAL_CORRECTION_V3.md](MATERIAL_CORRECTION_V3.md)。

> 当前主线（2026-10-06）：完整高友村基础模拟、接触机制校核与未知材质来源管理，按用户后续简单范围要求执行。最新状态见PROJECT_PLAN 28.25–28.29；原点模式默认、有限高度带候选、974项回归通过。下方发布/风场/规划任务是各自路线的历史记录，不构成基础模拟器当前完成门槛。

> **Input policy, 2026-10-04:** aerial/RGB material identification uses existing fixed initial moisture defaults automatically; no per-cell moisture entry is required in the V3 profile workflow. Internal live/dead presets and evaporation remain. These are scenario assumptions, not image measurements; see [moisture policy and ablation](MOISTURE_DEFAULT_POLICY.md) and plan28.8. No runtime/profile changes or whole-suite rerun in this policy stage.

> **Current priority, 2026-10-04:** the user now prioritises vegetation fire simulation. [PROJECT_PLAN.md, chapter28](../PROJECT_PLAN.md#28-植被蔓延偏弱的证据核查与物理修正2026-10-04) and [the evidence/fix record](VEGETATION_SPREAD_EVIDENCE_AND_FIX.md) describe the new optional, energy-bounded near-field flame contact and actual checks (699 tests). An opt-in independent component experiment now exists; see [component evidence and limits](VEGETATION_COMPONENTS_V3.md) and plan28.5. Optional same-cell finite-area radiation now exists; see [radiation evidence and limits](COMPONENT_INTERNAL_RADIATION_V3.md) and plan28.6. Optional independent component HRR clocks with fuel-energy closure and explicit peak ceilings now exist; see [curve evidence and sensitivity](COMPONENT_BURN_CURVES_V3.md) and plan28.7. Inter-cell radiation remains frozen. The current single-state webpage does not use component results. Effective contact parameters remain synthetic; field validation is unfinished. Planning progress through P04 is retained in chapter27; its next experiments are deferred. Older scope/pending statements below are historical.

> Effective: 2026-08-13
>
> This file overrides older "immediate next research" wording in historical notes when those documents have not yet been refreshed.

> **2026-10-04 scoped update:** the user has requested a pre-fire planning research roadmap in [PROJECT_PLAN.md, chapter 27](../PROJECT_PLAN.md#27-灾前防火线选址宽度与布局优化研究及开发路线2026-10-04). For that workstream, P00 baseline/evidence verification precedes P01–P06; a public release/tag is not a prerequisite. The release priorities below continue to govern the stable simulator, rather than blocking isolated planning research. This update records a plan, not completed implementation. Existing village W-series work and uncommitted changes must be preserved.

## Current priority

**Freeze the simple static baseline wildfire simulator before implementing a new PyFireCA-specific CA method.**

The ordinary simulator workflow is now implemented end to end. The current development order is therefore:

```text
1. preserve validated behavior/propagation foundations
2. keep all functional/GIS/package CI green
3. finish package metadata and release documentation
4. complete the release-readiness audit
5. choose/freeze the first baseline tag
6. only then reopen the paper-innovation branch
```

## Innovation ideas

Current innovation ideas remain recorded in:

```text
docs/FUTURE_RESEARCH.md
```

They include lattice-bias theory, neighborhood/directional refinement, and heterogeneous interface-coupling questions.

Until the baseline is frozen:

- record new ideas rather than implementing them;
- do not add extended/adaptive neighborhoods as default simulator features;
- do not change source-cell edge coupling invisibly;
- do not expose research variants in version-1 YAML/CLI;
- keep research benchmarks separate from the release-readiness path.

## Implemented simulator path

The current baseline already provides:

```text
YAML + 10 aligned GeoTIFFs + ignition events
→ strict validation
→ audited fuel catalogue
→ Albini-adjusted Rothermel
→ Behave/Catchpole directional spread
→ physical earliest arrival
→ GeoTIFF / WGS84 GeoJSON outputs
→ resolved config / hashes / metadata / metrics / log
```

CLI:

```bash
pyfireca validate config.yml
pyfireca run config.yml
```

Current audited catalogue is sufficient for the first baseline:

```text
Anderson FM1–FM13
Scott–Burgan GR1 (101)
```

## Current implementation work

Follow:

```text
docs/RELEASE_CHECKLIST.md
docs/SIMULATOR_ROADMAP.md
```

Remaining work is release-readiness rather than new simulation science:

```text
clean built-package end-to-end test
→ package metadata audit
→ license decision
→ stale-doc/repository audit
→ all-green release-candidate commit
→ baseline tag/freeze
```

Static north-up square metric rasters are sufficient for this first release. Do not let full Scott–Burgan coverage, rotated grids, dynamic WRF coupling, crown fire, spotting, suppression, Monte Carlo, FBP, or performance optimization delay the baseline freeze.
