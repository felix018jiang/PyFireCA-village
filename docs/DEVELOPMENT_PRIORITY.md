# Development Priority

> **2026-10-16 最新阶段完成：**[折线与多路径迭代优化](BATUAN_ADAPTIVE_FIREBREAK_SEARCH.md)已通过岜团村两区各64次请求、留出/延时、全图和恢复验证。防火效果优先、完全同效时减少面积，保留帕累托备选；无货币成本。下一步扩大来火方向、独立种子和几何细化范围，并用实测候选区与风/飞火能力检验外推，不以本轮局部最好值代替实地最优。下方R1纯效果记录保留为历史阶段。

> **2026-10-16 R1/R2首版已完成：**后续按[接口与试验记录](BATUAN_INTERVENTION_EVALUATION.md)及[研究计划](VILLAGE_FIRE_RESEARCH_PLAN.md)推进R3缓存/批量、扩大林地来火方向与实测候选域。当前第一轮只按防火有效性排序；清理面积与经济性放在第二轮。24候选试验不等同于完成实地村内外最优防火线规划。

> **2026-10-16 当前执行顺序：**按用户确定的三模块架构，执行[研究开发计划](VILLAGE_FIRE_RESEARCH_PLAN.md)。下一任务为R1统一评价契约和R2单条干预闭环；A1材质标注与规则评测可并行。随后R3批量评估、B村外优化、C村内组团优化；不等待所有动态风/飞火/组件功能完成。下文按日期保留历史阶段要求。版本基线与恢复见[版本管理](VERSIONING_AND_RECOVERY.md)。

> 2026-10-07边界候选：独立连续点门禁已实现，固定1厘米过渡半宽，54次求解及53项测试完成。五类0.1毫米微扰通过；草地毫米级时刻/厘米级集合仍失败，不能升级默认。原高度带树木案例新增两格实为草地，精确逐步温度确认498.71→500.53 K跨阈值并形成新增路径。下一项检查DEM支持尺度/相关误差与接触几何、草地临界受热，不按结果扩大参数。详见[BOUNDARY_SMOOTHING_20261007.md](BOUNDARY_SMOOTHING_20261007.md)。

> 2026-10-07扩大筛查：五类起火场景100组微扰/时间步对照及12组后续诊断完成。统一±0.15 m高度带改善部分场景，但树木毫米扰动仍改变集合，交界30分钟仍仅1格，不能升级默认。下一项是区分数值平滑与接触面积重分配，明确按材质有效受体范围，追踪热阈值/路径。详见[CONTACT_ROBUSTNESS_20261007.md](CONTACT_ROBUSTNESS_20261007.md)。

> 2026-10-07诊断更新：已确认ASTER 30 m→2 m双线性DEM的float32量化与点受体二值门禁共同导致亚毫米边界敏感性；28组接触单机制模拟与全村拓扑检查完成。有限带候选在本轮微扰下较稳定，未升级默认；下一步建立有依据的连续接触闭合并扩大微扰验证。详见[DEM_CONTACT_DIAGNOSTIC_20261007.md](DEM_CONTACT_DIAGNOSTIC_20261007.md)。

> 2026-10-07更新：34组自动核心检验、205帧及5类网页统计核对完成，988项测试通过；旧燃料色标已修复。接触高度门槛的坡地敏感性仍未解决，优先处理该问题再科学模型冻结。详见[CORE_VALIDATION_20261007.md](CORE_VALIDATION_20261007.md)。

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
