# Development Priority

> **2026-10-23 P1 机制隔离已完成：**源内传播、源/受体辐射取样和临界二次反馈已通过 72 份对照区分；296 项相关测试通过。当前只推进下一处具体修正：以原核的面积积分替代屋面代表点辐射构图，再核查源内接触面积/传播距离闭合；保留原材料和固定起火多边形，不同时扩展优化、立面或流体模型。见[证据、验收与复现](HERITAGE_MECHANISMS_20261023.md)。

> **2026-10-23 当前 P1 优先项：**固定平方米的矢量点火及局部尺度核查已实施，34 份对照表明源内后续火势和部分核心响应仍有明显尺度依赖。先隔离源内蔓延/辐射空间取样的影响，并在相同源区、布局下配对手工方案；不以一次粗网格未引燃作为保护成功。技术点火规则、接受门槛和缺失项见[本轮结果](HERITAGE_IGNITION_SCALE_20261023.md)。全套 1238 项测试通过；真实可拆资格与固定源仍待定，复杂优化继续后置。

> **2026-10-23 屋面热模拟更新：**独立组件温度与燃烧时钟、实际几何传热、单整栋拆除和核心专用评价已接通。全套 1226 项测试、10 份完整模拟恢复通过，见[结果与缺失数据](HERITAGE_ROOF_THERMAL_20261023.md)。近期继续 P1：核定初始起火屋面范围、普通建筑重叠语义与可拆资格，再验证真实手工方案。正式固定源继续待定；暂不进入 P2 或复杂优化。下方“下一项接入热状态”为此前阶段安排。

> **2026-10-23 空间准备更新：**B0024 已同步，完整粗网格案例保留 333 个对象；鼓楼 B0073 在 0.25 m、风雨桥 B0120 在 1 m 通过局部几何分隔和半格偏移检查。详见[结果与复现](HERITAGE_GEOMETRY_20261023.md)。下一项是接入独立建筑/屋面组件热状态并验证传热，而非全村直接采用 0.25 m；可拆资格与固定研究源继续待定，暂不开展复杂优化。

> **2026-10-23 P0/P1 已开始实施：**数据审计与合成单方案建筑拆除评价闭环已完成，见[实施记录和缺失数据](HERITAGE_P0_P1_20261023.md)。鼓楼 B0073（用户新增 `gulou` 标签）及风雨桥 B0120 已识别；可拆资格、固定源仍待定。近期优先同步 B0024 最新几何、处理核心共享温度格，再做真实手工方案对照。P2 枚举和复杂优化尚未开始。

> **2026-10-23 当前研究优先级（仅设计，尚未实现）：**按用户确认，主线转为村内木建筑传播下的鼓楼与风雨桥保护，允许核定普通建筑拆除、退让或调整。先完成固定核心对象、真实整栋拆除、核心专用评价及小规模最少拆除搜索，再扩展邻域搜索、退让与连续廊道。详见[研究设计](HERITAGE_FIREBREAK_RESEARCH_DESIGN.md)和[分阶段开发计划](HERITAGE_FIREBREAK_DEVELOPMENT_PLAN.md)。本轮只修改文档，没有新增模拟或测试结果；旧版禁止主体建筑拆除、先做村内外植被联合优化的要求不再约束这一研究主线。以下保留历史阶段记录。

> **2026-10-17 多情景阶段完成：**[保护约束优化](BATUAN_MULTISCENARIO_SEARCH.md)已完成24设计情景直接搜索、8留出及两方案64次全场地复核，全部民居/桥90分钟零引燃。后续优先实测材质/燃烧参数、调查可清理域、扩展来火方向与飞火机制，再做数值收敛和跨村落外部验证。静态风致辐射倾斜已实际参与；风驱接触、飞火和实测校准仍待后续。下方为历史优先级记录。

> **2026-10-16 90分钟联合目标阶段完成：**576次请求、独立检验、全图复核和组件删除控制已完成。下一步优先将多情景零引燃约束和冗余路径删除直接纳入搜索，再推进材料标定、风/飞火及跨村落配置准则。详见[结果与恢复](BATUAN_PROTECTION90_SEARCH.md)及[论文主线](BATUAN_PAPER_RESEARCH_MAINLINE.md)。下方为此前完成记录。


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
