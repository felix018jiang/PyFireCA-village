# Project status

> **2026-10-16 R1/R2实施：**统一情景/结果协议、V3评价适配器与独立植被清理带已接通。按用户最新要求只按防火效果排序；岜团村24候选、2训练/1留出情景、4份完整网格回放及90分钟局部域复核完成。全套回归1036项通过，另5项入口/地理导出验证通过；原case48个文件哈希不变。见[接口与试验记录](BATUAN_INTERVENTION_EVALUATION.md)。现有候选区与燃料仍是假设，正式森林大火多风/飞火与村内外全局优化尚未完成。

> **2026-10-16 当前状态：**岜团村连续坡面接触案例可运行，卫星材质与防火规划三模块进入独立研究开发路线，具体缺口和次序见[研究开发计划](VILLAGE_FIRE_RESEARCH_PLAN.md)。规划已落文档，统一动态评估接口与优化接入仍待开发。模拟器基线提交`f6c1632`、标签`batuan-simulator-20261016`已保存；本次全套回归1021 passed、9条依赖弃用警告。影像/案例已另做逐文件校验的本地备份，见[版本管理与恢复](VERSIONING_AND_RECOVERY.md)。下文保留各阶段历史记录。

> **Latest replay, 2026-10-05:** component states now have physical-grid browser replay and opt-in ignition/duration selection, with full saved configuration restoration and explicit model matching. Fixed material moisture needs no per-cell entry; partial burn does not imply building loss. 791 repository tests pass, plus actual desktop/mobile browser submission and wheel checks. See [component replay](COMPONENT_VIEWER_V3.md) and plan28.12. Earlier next-viewer statements below are historical. Dynamic flame/wind and field calibration remain open.

> **Latest geometry, 2026-10-04:** opt-in complete finite component radiation now replaces inherited edges, with analytic shadow clipping, segmented source-height quadrature, opaque receiver and stepwise terrain visibility. 50 fixed solves including two finer references pass; 752 tests pass. Formal repeats compare 2996 non-timing files. These are small synthetic scenes, not large-domain/field validation. See [finite graph](FINITE_COMPONENT_RADIATION_V3.md) and plan28.11. Next: component state viewer, then dynamic flame/wind with stated support domains.

> **Latest implementation, 2026-10-04:** M02 survey resolution now uses packaged material moisture defaults when `--moisture` is omitted, with explicit overrides and evidence gates preserved. V3 viewers show initial moisture without requiring cell inputs. Optional finite receiver orientation/position now affects same-cell component radiation; 40 fixed comparisons and 721 repository tests pass. Inter-cell geometry remains frozen; see [receiver geometry](COMPONENT_RECEIVER_SURFACES_V3.md) and plan28.9–28.10. Earlier no-runtime-change statements below describe their historical policy stage.

> **Input policy, 2026-10-04:** aerial/RGB material identification uses existing fixed initial moisture defaults automatically; no per-cell moisture entry is required in the V3 profile workflow. Internal live/dead presets and evaporation remain. These are scenario assumptions, not image measurements; see [moisture policy and ablation](MOISTURE_DEFAULT_POLICY.md) and plan28.8. No runtime/profile changes or whole-suite rerun in this policy stage.

> **Current priority, 2026-10-04:** the user now prioritises vegetation fire simulation. [PROJECT_PLAN.md, chapter28](../PROJECT_PLAN.md#28-植被蔓延偏弱的证据核查与物理修正2026-10-04) and [the evidence/fix record](VEGETATION_SPREAD_EVIDENCE_AND_FIX.md) describe the new optional, energy-bounded near-field flame contact and actual checks (699 tests). An opt-in independent component experiment now exists; see [component evidence and limits](VEGETATION_COMPONENTS_V3.md) and plan28.5. Optional same-cell finite-area radiation now exists; see [radiation evidence and limits](COMPONENT_INTERNAL_RADIATION_V3.md) and plan28.6. Optional independent component HRR clocks with fuel-energy closure and explicit peak ceilings now exist; see [curve evidence and sensitivity](COMPONENT_BURN_CURVES_V3.md) and plan28.7. Inter-cell radiation remains frozen. The current single-state webpage does not use component results. Effective contact parameters remain synthetic; field validation is unfinished. Planning progress through P04 is retained in chapter27; its next experiments are deferred. Older scope/pending statements below are historical.

> Updated: 2026-08-13

> **Planning update, 2026-10-04 (not a new implementation audit):** [PROJECT_PLAN.md, chapter 27](../PROJECT_PLAN.md#27-灾前防火线选址宽度与布局优化研究及开发路线2026-10-04) defines the requested pre-fire placement/width/layout research workstream. P00–P06 are pending. Its scoped priority supersedes the historical blanket research freeze below, without changing the stable runtime or claiming that historical test results were rerun. The working-tree village W01 interface must be distinguished from actual wind/firebrand coupling.

PyFireCA is currently an **alpha, static raster wildfire simulator and CA research framework**. The baseline simulator is complete enough to run reproducible GIS experiments, while CA-method innovation remains intentionally deferred until the simulator and behavior-model layer are stable.

## Current stable architecture

```text
GIS / raster inputs
      ↓
model-specific behavior inputs
      ↓
┌──────────────────────────────┐
│ Fire behavior                │
│ Rothermel                    │
│ Canadian FBP                 │
│ Van Wagner + Cruz component  │
└──────────────┬───────────────┘
               ↓
       directional ROS
               ↓
    StaticArrivalTimeSolver
               ↓
  arrival / state / footprint
               ↓
GeoTIFF / GeoJSON / metadata
```

The classic synchronous CA reference path (`RasterGrid`, `Neighborhood`,
`TransitionRule`, `Simulation`) remains separate from the physical
arrival-time wildfire baseline.

## Implemented behavior models

### Rothermel

Status: **integrated simulator path**.

Includes:

- audited Anderson FM1-FM13 fuel models plus GR1;
- dynamic herbaceous curing;
- no-wind/no-slope Rothermel ROS;
- wind/slope effects and vector combination;
- directional surface-fire ellipse;
- static heterogeneous raster provider;
- YAML / CLI / GIS workflow.

### Canadian FBP

Status: **integrated simulator path**.

Implemented directly inside PyFireCA; no runtime dependency on Cell2Fire,
cffdrs, or another FBP package.

Includes:

- C1-C7, D1/D2, M1-M4, O1a/O1b, S1-S3, NF/WA semantics;
- FFMC/BUI fire-weather inputs;
- 10-m wind and slope-vector interaction;
- head/back ROS;
- fuel consumption;
- foliar moisture;
- crown fraction burned and C6 crown correction;
- fire intensity;
- FBP head/back/flank ellipse;
- per-cell spatial provider and cache;
- static raster arrival simulation;
- model-specific YAML schema;
- CLI `validate` / `run` workflow;
- real Rasterio end-to-end tests.

### Van Wagner + Cruz crown fire

Status: **implemented and tested component; not yet composed into the default raster surface-fire simulator**.

The current component implements crown initiation, active/passive crown ROS, and
crown intensity. A full surface+crown raster workflow requires an explicit
canopy data contract and validated surface-intensity coupling.

### KITRAL

Status: **reference audited, not implemented**.

Do not wrap C2FK. Pin primary equations, coefficients, calibration domain, and
independent validation fixtures before adding a self-contained KITRAL model.

## Runtime dependency policy

Behavior equations must be owned by PyFireCA. External wildfire simulators are
reference/oracle material only.

Current core runtime dependencies remain:

```text
numpy
PyYAML
```

GIS extra:

```text
rasterio
```

No `cffdrs`, Cell2Fire, Pyretechnics, SimFire, or other behavior package is a
PyFireCA runtime dependency.

## User-facing file workflows

### Rothermel version-1 YAML

Legacy version-1 files remain valid and omit the `behavior` block.

### FBP version-1 YAML

FBP files explicitly declare:

```yaml
behavior:
  model: fbp
  julian_day: 180
```

with model-specific FBP rasters.

Both use:

```bash
pyfireca validate config.yml
pyfireca run config.yml
```

and produce the same result-directory contract:

```text
config.resolved.yml
metadata.json
environment.json
metrics.json
log.txt
outputs/
  arrival_time.tif
  state.tif
  burned_mask.tif
  perimeter.geojson
```

## Validation status

Current CI gates include:

```text
Ruff lint
Ruff format
Python 3.11
Python 3.12
Python 3.13
GIS / Rasterio workflows
wheel + sdist build
clean wheel install
console-script smoke
MIT distribution metadata
```

FBP additionally has:

- Wotton-2009 scalar regression cases;
- direction/ellipse tests;
- spatial-provider tests;
- real GeoTIFF + YAML + CLI end-to-end tests.

## Research line status

The CA-method innovation line is **recorded but frozen** in
`FUTURE_RESEARCH.md`.

Stored topics include:

```text
lattice bias / directional discretization
VN4 vs Moore8 analytical error
cell-size vs direction-set convergence
heterogeneous interface coupling
future extended/dynamic neighborhoods
```

Do not resume these merely because FBP is now implemented. The simulator and
behavior model layer should remain the active engineering priority until the
next explicit research phase.

## Current documentation of record

```text
DESIGN.md
BEHAVIOR_MODELS.md
BEHAVIOR_MODEL_HANDOFF.md
RUNNING_SIMULATOR.md
VALIDATION.md
FUTURE_RESEARCH.md
HANDOFF.md
STATUS.md
```

## Immediate next work

1. Keep the new FBP/Rothermel multi-model workflow fully green in CI.
2. Synchronize README and simulator documentation with the multi-model state.
3. Decide whether the next behavior task is:
   - complete Scott-Burgan fuel catalogue for Rothermel, or
   - define canopy raster contract and compose the existing Cruz crown component.
4. Audit KITRAL from primary literature before implementation.
5. Continue to defer new CA-method innovation until explicitly restarted.

## Release

No release/tag should be created yet. The project remains at `0.1.0a0` during
active development.


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


### 28.32 DEM精度与接触几何边界诊断（2026-10-07）

核实完整案例高程逐项等于ASTER GDEM 30M双线性重采样2 m的float32栅格，非FABDEM候选。草地147,266上坡受体超柱顶0.012207毫米，小于该高程float32存储间隔0.061035毫米；下坡受体低于源柱底约5厘米，属于模型几何间隙。默认点受体采用二值门禁，微小扰动会增删传播边。全村float64共同减500 m接触图完全相同，不能靠提高计算精度或减小时间步解决此闭合问题。

28组冻结41×41裁剪、20分钟、辐射不变的接触对照：默认1格；受体降低0.1毫米11格；固定DEM噪声幅度0.1毫米正/反号为3/1格。半厚度15厘米候选23格，在正负0.1毫米、1毫米和1厘米6组扰动下仍23格，但非经验校准。三组0.25 s结果与1 s一致，全部燃料/水量/能量约束通过。完整接触图中5238对可燃有向邻格距点门禁边界≤0.1毫米；草地受体降低0.1毫米改变1786个边槽。

正式报告runs/dem-contact-diagnostic-20261007-b/report.json；脚本scripts/audit_dem_contact_sensitivity.py；说明docs/DEM_CONTACT_DIAGNOSTIC_20261007.md。相关43项测试及Ruff通过。只做诊断，不修改求解器、默认参数或原案例；后续优先有依据的连续接触/有限受体范围及扩大微扰验收，有限带尚不升级默认。未提交或推送。


### 28.33 五类场景接触微扰与候选筛查（2026-10-07）

事先冻结容差，完成5场景×2模型×10组=100组对照：每模型基线、逐格正负0.1毫米/1毫米/1厘米、平滑平面正负1厘米及0.25 s时间步。每次微扰与同模型基线比较，辐射边不变。原点/候选微扰点燃范围：草地1–11/23，灌木161–164/181，树木13/16–19，建筑215–217/213–214，交界238/1。全部10组细时间步通过工程容差，非逐格一致；100组未烧到裁剪边界，输入哈希复核不变。

统一半厚度0.15 m不能升级默认：候选草地负1厘米集合不变，但点火时刻95分位相对变化6.2766%超过5%标准；候选树木正1毫米增加2格，延长至1260/1800 s的10组诊断仍17/19/16差异，非当前终点截断。交界另外2组延长至30分钟，原点634格、候选1格。该起火格为草地，四邻建筑；候选增加上坡接触边，同时把原三方向几何权重降至0.5903/0.6254/0.7921，未引燃的四邻采样有效表面温度均低于520 K。高度带同时改变接触面积和共享源预算，不能当作纯数值容差。更多传播不代表现实更准。

112组燃料/水量/燃尽HRR/能量检查通过；43项相关测试、Ruff及diff检查通过。正式产物runs/contact-robustness-20261007；脚本scripts/audit_contact_robustness.py及audit_contact_robustness_followup.py；说明docs/CONTACT_ROBUSTNESS_20261007.md。默认求解器/参数/案例未改。下一项明确按材质有效受热范围与接触面积，区分数值平滑和面积重新分配，继续追踪树木热阈值/路径，不按原点点燃数量反向拟合。未提交或推送。


### 28.34 独立连续边界候选与逐热步阈值追踪（2026-10-07）

按数值平滑与面积带分离实现scripts/contact_boundary_experiment.py：相对高差的柱内有符号边界距离，固定1厘米过渡半宽smoothstep，远离边界保留原点权重，原源预算/面积上限/点火规则不变。只是独立实验注入算子，无正式配置/网页改动；1厘米不是实测DEM误差或物理受体厚度。现有各材质高度与面积均synthetic，不能推导正式受体上下界。

5场景各10组=50组筛查，另2组原高度带重跑识别差异格。新候选草地11格、扰动1–11格，仅4/8通过；灌木164、树木13、建筑215–217、交界238，后四类均8/8通过。五类正负0.1毫米均通过，五组0.25 s通过。草地正负1毫米集合仍11格，但时刻95分位相对变化8.72%/13.19%；1厘米逐格扰动1/6格，仍失败，未反向扩宽参数或升级默认。

原±15厘米树木起火案例新增两格实际为草地21,24及22,24。新增独立观察脚本scripts/trace_contact_threshold.py，正式2组逐热步核验结果与原全域点燃/燃料数组完全相同。第一格无扰动真实温度最高498.71 K（30秒采样曾仅487.26），正1毫米时640 s达到500.53 K跨500 K阈值；当步吸收辐射19.31 kW、接触3.10 kW、表面损失21.24 kW、净加热1.164 kW。它随后成为第二格新接触源，第二格661 s点燃。几何0/1跳变与真实热阈值分叉需分别处理，不能通过大幅改物理受热面积消除全部离散结果差异。

正式主流程52次求解+2次逐热步核验，共54次；新增10项测试合计53 passed，Ruff/diff通过。结果runs/boundary-smoothing-20261007，正式threshold-trace-verified.json；说明docs/BOUNDARY_SMOOTHING_20261007.md。下一项DEM支持尺度/空间相关扰动与接触几何匹配及草地临界受热；保持默认模型与既有网页不变。未提交或推送。
