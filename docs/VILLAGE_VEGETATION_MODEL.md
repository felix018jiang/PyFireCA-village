# 村落植被—建筑原胞火灾模型

> 状态：V00–V10 与材料简化 M00–M05 的小域路径、全域预检、unknown 审查和 M02 解析门禁已完成，V3 viewer 已支持首/中/末帧审计和分块回放写出；全域研究运行仍被 unknown 与材料证据门禁阻止。
> 更新日期：2026-09-22。
> 计划依据：`PROJECT_PLAN.md` 第 23–25 章。

## 1. 文档边界

本文记录已经实现的能力、数据假设和验收证据。V05 已把建筑、草地、灌木、树木等效燃料和枯落物接入同一同步求解器，但当前验收范围仍是冻结的小域合成夹具。高友村 V01–V04 产物是静态空间、物理数组和传播图，不是新的火灾预测结果；74,829 个 unknown 原胞尚未通过实地调查或明确策略解决，因此不得将小域验收解释为全村模型已标定。

## 2. V00 冻结基线

基线 Git 提交为 `1dc3f6c3b0d1ccd13c1ce85493759c4c9ec29832`。执行时工作区已有未提交修改；这些修改全部保留，没有 reset、checkout 或覆盖既有 `runs/` 结果。文件级校验值保存在 [`baselines/gaoyou-hypothetical-multimaterial-v00.sha256`](baselines/gaoyou-hypothetical-multimaterial-v00.sha256)。

V00 开始时的既有工作区摘要如下；它将本轮文件与用户此前修改区分开：

```text
 M .gitignore
 M PROJECT_PLAN.md
 M README.zh-CN.md
 M docs/VILLAGE_PROGRESS.md
 M src/pyfireca/village/cli.py
 M src/pyfireca/village/outputs.py
 M src/pyfireca/village/web/app.js
?? docs/VILLAGE_MATERIAL_EVIDENCE.md
?? examples/village/material-catalog-3.gaoyou-evidence.yml
?? src/pyfireca/village/hypothetical.py
?? src/pyfireca/village/materials_v3.py
?? tests/village/test_materials_v3.py
```

在 Conda 环境 `pyfireca-village` 中执行：

```bash
conda run -n pyfireca-village pytest -q
```

V00 基线结果为 `398 passed, 15 warnings`。警告均来自 Rasterio 的弃用提示，没有测试失败。现有高友村对照目录 `runs/gaoyou-hypothetical-multimaterial/` 保持可读，源配置、prepared 清单、主要结果数组和网页清单均已记录 SHA256。

### 2.1 基线已有能力

- v2 以原胞为最小计算单元，包含未燃、受热、燃烧和燃尽状态，以及有限燃料预算。
- 受热端包含显热升温、水分升温和蒸发、环境对流、表面再辐射、背衬损失与温度阈值点燃。
- 燃烧端使用增长—稳定—衰减 HRR 曲线，并用可热解质量和燃烧热约束总能量。
- 传播端包含有限半径的点源辐射、三维源—受体距离、建筑透射率、风致火焰倾斜参数和统一的出射能量预算检查。
- 高友村已有 2 m 名义网格、341 个 LabelMe 建筑、B0233 建筑起火和 500 分钟建筑场景回放。

### 2.2 基线明确不具备的能力

- `assumed_sparse_vegetation` 和 `assumed_dense_vegetation` 在对照场景中均为 `burnable: false`；草、灌、树和林下层没有实际燃烧 profile。
- 旧网格直接在 EPSG:3857 上把坐标单位当米使用。高友村中心的 Web Mercator 尺度因子为 1.112455，名义 2 m 并不等于 2 m 地面距离。
- RGB 颜色不能证明物种、含水率、燃料负荷、建筑构造或材料。旧分类和三种屋面 profile 都属于合成假设。
- 旧场景统一采用 6 m 有效火焰高度、8.1 m 作用半径、无风及经验透射率，未经过高友村火灾数据标定。
- 飞火、树冠分层、Rothermel 连续前沿和动态遮挡尚未实现。

### 2.3 合成建筑 profile 审查

`scenario.v2.hypothetical.yml` 已用 `rgb_inspired_hypothetical_materials_not_field_observations` 标明证据等级。木屋面、瓦覆木结构和茅草屋面中的燃料负荷、峰值 HRRPUA、增长/衰减时长、有效受热质量、活跃面积及可热解比例是集成测试参数。部分热值、吸收率或点燃温度的量级参考了外部试件，但组合后的 profile 不是高友村实测构造。曾为获得可观察传播而调整的瓦覆木结构 profile 尤其只能作为行为夹具，不能称为标定值或风险预测输入。

## 3. V01 坐标和空间属性

实现入口：

```bash
conda run -n pyfireca-village pyfireca village prepare-vegetation-spatial \
  /Users/tone/Desktop/label_satellite/高友村.tif \
  --labelme-buildings /Users/tone/Desktop/label_satellite/高友村.json \
  --output runs/gaoyou-vegetation-v3/v01-spatial-utm49n-final \
  --cell-size-m 2 \
  --ignition-building-id B0233
```

此命令拒绝覆盖已有输出目录，并且只准备空间数据，不运行求解器。

### 3.1 投影选择与像元转换

输入影像中心为 `(109.7161889°E, 25.9844947°N)`，自动选择 WGS 84 / UTM zone 49N（EPSG:32649）。影像 RGB 使用双线性重采样，alpha/数据集有效掩膜使用最近邻重采样；建筑的每个外环、内洞和 MultiPolygon 部件使用 `pyproj` 做坐标转换。整个流程转换实际坐标和像元值，不是只替换 CRS 标签。

| 指标 | EPSG:3857 输入 | EPSG:32649 工作网格 |
|---|---:|---:|
| 中心尺度因子 | 1.112455149 | 0.999803895 |
| 相对尺度误差 | +11.2455% | -0.01961% |
| 2 m 测地线在投影中的长度 | — | 1.9996078 m |
| 五点坐标往返最大误差 | — | 1.92×10⁻⁹ pixel |
| 建筑并集投影面积相对测地面积误差 | — | -0.03920% |

新网格为 `352 × 552`，每格 `2 m × 2 m`，单格面积 4 m²。旧网格的 `(193, 285)` 不能复用。

### 3.2 原胞覆盖、优先级和证据

每个建筑与候选原胞做真实多边形相交。`building_overlap.csv` 保存每座建筑的相交面积；逐格建筑覆盖率使用所有相交几何的并集计算，因此即使标注重叠也不会超过 1。建筑只要与有效原胞有非零面积交集，就按建筑优先；原胞内被舍弃的非建筑面积记入 `discarded_area_fraction.npy`，便于审计混合边界。

非建筑有效原胞仅用归一化 Excess Green `(2G-R-B)/(R+G+B+1)` 产生草、灌、树木等效燃料的候选假设。低绿色响应不能可靠区分道路、裸地、阴影和屋面，因此保留为 unknown。RGB 也看不到树冠下的林下枯落物，所以 `forest_floor` 保留未解析状态。NoData/透明像元在 `domain_mask` 中为 false，不会被分类为道路或燃料。

证据编码分三类：`observed_annotation`、`rgb_hypothesis` 和 `unknown`。V02/V07 之前 unknown 不会被静默改成 inert，也不会补齐燃烧参数。

### 3.3 高友村 V01 质量结果

最终输出目录：`runs/gaoyou-vegetation-v3/v01-spatial-utm49n-final/`。同级的 `v01-spatial-utm49n/` 和 `v01-spatial-utm49n-r2/` 是开发中逐次增加质量诊断产生的只读预备构建；后续阶段必须使用 `-final`。

| 项目 | 结果 |
|---|---:|
| 有效域原胞 | 189,150 |
| 域外/NoData 原胞 | 5,154 |
| 已分类原胞 | 114,321 |
| 保留 unknown 原胞 | 74,829 |
| 建筑 | 341 |
| 建筑相交记录 | 24,648 |
| 部分建筑覆盖边界格 | 10,259 |
| 最大建筑并集覆盖率 | 1.0 |
| 因建筑优先而舍弃的底层面积 | 20,030.21 m² |
| 建筑并集投影面积 | 70,973.790476 m² |
| 覆盖率反算面积闭合误差 | -3.27×10⁻⁷ m² |

RGB 假设分类得到 59,505 个草候选格、21,042 个灌木候选格和 11,023 个树木等效候选格；建筑格为 22,751。`bare_or_road` 和 `forest_floor` 目前均未被凭空指定。空间包通过 V01 验收，但因 74,829 个 unknown 和尚无 V02 profile，`valid_for_final_simulation` 必须保持 false。

B0233 的投影质心为 `(371460.9881, 2874597.9757) m`。新网格按“最大建筑交叠面积，再取最接近质心”选择起火格 `(row=176, col=258)`，格心 `(371461.0, 2874597.0) m`，与 B0233 的交叠为完整 4 m²。这证明起火点由世界坐标重新求得，没有复用旧行列号。

## 4. V01 文件契约

- `manifest.json`：阶段、格网、类别 ID、源文件 SHA256 和产物索引。
- `quality_report.json`：尺度误差、域/未知数、覆盖率、丢弃面积和起火格。
- `classification_report.json`、`classification_rules.yml`：分类数量、算法阈值和假设边界。
- `background.png`、`image_ref.json`：重投影背景和 EPSG:32649 仿射关系。
- `buildings.json`、`building_overlap.csv`：保留拓扑的建筑几何和逐格交叠。
- `landcover.tif`、`evidence.tif`、`coverage.tif`：与新网格一致的 GeoTIFF。
- `arrays/*.npy`：NoData 域、known、分类、证据、覆盖率和舍弃面积数组，全部禁用 pickle。
- `ignition.json`：B0233 的世界坐标、选择方法、新行列和交叠证据。

## 5. V02 严格配置与合成 profile（已完成）

V02 新增 [`vegetation_materials.py`](../src/pyfireca/village/vegetation_materials.py) 和 [`vegetation_config.py`](../src/pyfireca/village/vegetation_config.py)。前者严格读取燃料 profile，后者将 profile 与 V01 网格连接并检查起火位置、分类、覆盖率、天气、传播设置和资源上限。两个加载器都使用精确键集合，缺值、额外字段、错误单位、非有限数、越界比例和重复类别均直接报错，不提供物理默认值。

可运行配置契约位于：

- [`profiles.synthetic.yml`](../examples/village/vegetation_v3/profiles.synthetic.yml)
- [`gaoyou-v02.synthetic.yml`](../examples/village/vegetation_v3/gaoyou-v02.synthetic.yml)

所有物理数值都写成 `{value, unit, provenance}`。当前所有 profile 仍是 `synthetic_v02_integration_profile`，库保持 `production_ready: false`。V01 的 `rgb_hypothesis` 没有被升级成现场观察证据。

### 5.1 profile 参数与单位

| 组 | 字段与单位 |
|---|---|
| 干燃料 | `fuel_load_kg_m2`：kg/m²；`live_dry_mass_fraction`、`dead_dry_mass_fraction`、`pyrolysable_dry_mass_fraction`：1 |
| 水分 | `live_moisture_content_dry_basis`、`dead_moisture_content_dry_basis`：kg/kg_dry |
| 放热与点燃 | `heat_of_combustion_J_kg_dry`：J/kg_dry；`ignition_temperature_K`：K；`peak_hrrpua_W_m2`：W/m²_active |
| 燃烧曲线 | `growth_time_s`、`steady_time_s`、`decay_time_s`：s |
| 表面热平衡 | `effective_heated_dry_mass_kg_m2`：kg/m²_covered_ground；`specific_heat_J_kg_K`：J/(kg·K)；`radiative_fraction`、`absorptivity`、`emissivity`：1；换热/背衬系数：W/(m²·K) |
| 几何 | `receiver_area_ratio`、`active_surface_area_ratio`：有效面积/覆盖地表面积；`source_height_m`、`receiver_height_m`：m |

### 5.2 面积、质量和能量预算

对原胞覆盖率 `c`、原胞面积 `A_cell` 和燃料负荷 `w`：

```text
A_covered = A_cell × c
m_dry = A_covered × w
m_pyrolysable = m_dry × f_pyrolysable
m_water = m_dry × (f_live × M_live + f_dead × M_dead)
E_available = m_pyrolysable × H_c,dry
A_active = A_covered × active_surface_area_ratio
E_curve = q_peak × A_active × (t_growth/3 + t_steady + t_decay/2)
```

覆盖率只在 `A_covered` 中乘一次。加载时强制 `f_live + f_dead = 1`，并在单元面积尺度检查 `E_curve ≤ E_available`。小于浮点容差的差异归一为严格闭合；超预算直接报出 profile ID、曲线能量和可用能量，不自动裁剪峰值或时长。

| profile | 负荷 kg/m² | 活/死比例 | 加权干基含水率 | 可用能量 MJ/m²覆盖地面 | 源高度 m |
|---|---:|---:|---:|---:|---:|
| grass | 0.6 | 0.40 / 0.60 | 0.3000 | 10.260 | 0.35 |
| shrub | 2.0 | 0.55 / 0.45 | 0.4940 | 32.400 | 1.20 |
| forest_floor | 1.2 | 0.05 / 0.95 | 0.1725 | 20.520 | 0.25 |
| tree_equivalent | 8.0 | 0.65 / 0.35 | 0.6375 | 108.000 | 4.00 |
| building | 18.0 | 0.00 / 1.00 | 0.1000 | 175.608 | 6.00 |

这些数值只用于检查软件的质量、含水率、热量和几何链路，不代表高友村实测燃料。

### 5.3 高友村 V02 预检结果

最终报告目录为 `runs/gaoyou-vegetation-v3/v02-profile-validation-synthetic-final/`。报告只做配置与预算验证，`simulation_executed: false`。较早的同名无 `-final` 目录是未归一浮点闭合差异的预备报告，后续只使用 `-final`。

| 类别 | 原胞数 | 覆盖面积 m² | 初始干燃料 kg | 可热解干燃料 kg |
|---|---:|---:|---:|---:|
| grass | 59,505 | 238,020.00 | 142,812.00 | 135,671.40 |
| shrub | 21,042 | 84,168.00 | 168,336.00 | 151,502.40 |
| forest_floor | 0 | 0.00 | 0.00 | 0.00 |
| tree_equivalent | 11,023 | 44,092.00 | 352,736.00 | 264,552.00 |
| building | 22,751 | 70,973.79 | 1,277,528.23 | 766,516.94 |

B0233 世界坐标仍解析到 `(176, 258)` 的 `synthetic_building`。直接火源若落在 unknown、NoData、域外或不燃 profile，加载器会报告具体 ignition 序号和行列并拒绝配置。74,829 个 unknown 原胞继续阻止正式模拟。林下 profile 已定义，但当前影像无法识别林下层，因此面积保持 0。

V02 测试覆盖 VT06 和 VT13，并补充 VT03 的覆盖面积一次换算：超预算曲线、缺失含水率、unknown 起火点、错误单位和输出覆盖均有明确失败路径。

## 6. V03 原胞物理数组（已完成）

[`physics_v3.py`](../src/pyfireca/village/physics_v3.py) 已将 V01 覆盖率和 V02 profile 解析为逐格数组。每个可燃原胞分别保留活/死干燃料质量、活/死水量、总水量、可热解质量、有效受热质量、受热面积、接收面积、源/受体高度、HRR、燃烧曲线时间和证据索引。profile ID 采用固定宽度 Unicode 数组保存，所有 `.npy` 均使用 `allow_pickle=False`。

单格换算为：

```text
A_covered = A_cell × coverage_fraction
m_dry = A_covered × fuel_load
m_live = m_dry × live_fraction
m_dead = m_dry × dead_fraction
m_pyrolysable = m_dry × pyrolysable_fraction
m_water = m_live × M_live + m_dead × M_dead
A_active = A_covered × active_surface_area_ratio
A_receiver = A_covered × receiver_area_ratio
peak_HRR = peak_HRRPUA × A_active
E_available = m_pyrolysable × heat_of_combustion
E_curve = ∫HRR dt
```

覆盖率只出现在 `A_covered` 一次。建筑 B0233 的半覆盖边界格会得到半覆盖质量和半覆盖受热面积；不会把 2 m 原胞的完整燃料重复分配给建筑和背景。

V03 复用了 v2 的 `burn_interval_energy_and_hrr`，并新增水分蒸发能量需求函数。当前 V03 不推进温度、不改变状态、不构建传播图，也不运行全村火灾。

### 6.1 V03 高友村静态产物

输出目录：`runs/gaoyou-vegetation-v3/v03-physics-synthetic-final/`。

| 项目 | 数值 |
|---|---:|
| 初始干燃料 | 1,941,412.23 kg |
| 可热解干燃料 | 1,318,242.74 kg |
| 活干燃料 | 378,988.00 kg |
| 死干燃料 | 1,562,424.23 kg |
| 总水量 | 478,623.61 kg |
| 可用燃烧能量 | 2.2394629797869×10¹³ J |
| HRR 曲线配置能量 | 2.2394629797869×10¹³ J |
| 能量闭合误差 | 0 J |
| 可燃原胞 | 114,321 |
| unknown 原胞 | 74,829 |

V03 报告生成时只验证静态数组，因此标明 `simulation_executed: false`。这些数组现在可以作为 V05 求解器输入；高友村的 74,829 个 unknown 原胞仍未得到数据或明确策略，所以本轮没有把该静态产物直接用于全村动态计算。

### 6.2 V03 测试退出条件

新增 [`test_physics_v3.py`](../tests/village/test_physics_v3.py)，验证了覆盖率一次换算、湿度增加时蒸发能量增加、单格 HRR 积分与燃料热值相等、数组守恒关系、Unicode profile ID 和输出不可覆盖。VT03、VT05、VT07 已通过。

## 7. V04 高度相关传热与资源限制（已完成）

新增 [`transport_v3.py`](../src/pyfireca/village/transport_v3.py)。草地、灌木、树木等效燃料和建筑使用同一张有向辐射图，不按类别重复分配源功率。每条边使用源格的 `source_height_m`、目标格的 `receiver_height_m` 和 `receiver_area_m2` 计算三维距离；无风时有效点源位于配置火焰高度的一半。风倾斜仍采用显式的静态降阶参数。

实现同时提供逐边枚举参考函数和按偏移、按行分块的向量化构图函数。每个整数偏移只生成一次，Bresenham 中间路径按逐格透射率相乘，零透射边不存储。不同 `block_rows` 必须生成相同的 source、target 和 weight 数组。V04 当时的高友村资源预测只把已分类可燃格设为目标；V05/M03 接口已允许道路等已知不燃表面作为接收辐射但不放热的目标。只要路径透射率非零，辐射可以跨越这些原胞；超过物理半径仍不建边。

构图前精确统计掩膜和边界条件下的候选源—目标对，再检查 `max_edge_count` 和保守峰值内存。估算采用最终边数组 16 bytes/edge、构图峰值 64 bytes/candidate edge；回放驻留估算按每帧四个 float64 场加一个 uint8 状态，即 33 bytes/cell/frame。超限在分配边数组前报错，不自动删植被、缩小半径或改变格宽。

所有目标共享同一源预算，逐源强制 `sum(weight) <= 1 + 1e-12`。首次高友村预检发现旧合成灌木/树木接收面积比分别为 1.2/1.5 时，一个树木源会分配 1.29082468046 倍辐射功率，因此构图被拒绝。V04 将这两个合成值约束为 1.0，并标记 `synthetic:v04_shared_radiation_budget_safe_bound`；没有修改燃料量、HRR、点燃温度或含水率。旧失败报告保存在 `runs/gaoyou-vegetation-v3/v04-transport-resource-forecast-prebudget/`。

最终高友村 V04 报告位于 `runs/gaoyou-vegetation-v3/v04-transport-resource-forecast-final/`。本阶段显式使用全路径透射率 1.0 的合成预测，因为逐类别遮挡率尚无校准配置；该假设记录在报告中，不能解释为真实建筑或植被遮挡。

| 项目 | 结果 |
|---|---:|
| 格网原胞 | 194,304 |
| 统一源/目标原胞 | 114,321 / 114,321 |
| 半径内整数偏移 | 48 |
| 实际构建边 | 4,486,644 |
| 最终边数组上限 | 71.786 MB |
| 构图峰值估算 | 287.145 MB |
| 101 帧驻留回放估算 | 647.615 MB |
| 构图加驻留回放估算 | 934.760 MB |
| 最大逐源辐射分配 | 0.8644472043 |
| 配置上限 | 5,000,000 edges / 2,048 MB |

[`test_transport_v3.py`](../tests/village/test_transport_v3.py) 覆盖 VT09、VT10、VT12 和 VT14：混合目标统一预算、道路跨越、不同火焰/接收高度手算、遮挡路径、分块大小一致、无重复/越界边和资源预检均已通过。

## 8. V05 同步联合求解器（已完成）

新增 [`solver_v3.py`](../src/pyfireca/village/solver_v3.py)。它直接消费 V03/M03 的逐格物理数组和 V04 的单一有向辐射图。建筑 ID、材料 ID 和地类 ID 只作为可追溯元数据；热计算不按 `building_id` 过滤。所有可燃建筑、草地、灌木、树木等效燃料和枯落物共用同一时间步、辐射预算、热平衡、温度点燃、有限燃料和燃尽规则。

### 8.1 状态与水量语义

求解器明确区分四类空间输入：域外/NoData 为 `OUTSIDE`，域内未解析格为 `UNKNOWN`，已知不燃受体为 `INERT`，已知可燃格从 `UNBURNED` 开始。`unknown_policy: error` 会在运行前拒绝任何 unknown；`exclude` 只用于受控实验，并保留其 `UNKNOWN` 状态。全部已知可燃格必须同时属于源和目标掩膜；不燃表面可以只作为目标接收辐射，永远不放热和点燃。

V03 的 `water_mass_kg` 是原胞总库存水量，结果中以 `initial_total_water_kg` 原样保留。参与表面引燃热平衡的初始水量单独定义为：

```text
m_surface_water = m_effective_heated_dry × moisture_content_dry_basis
```

它以 `heated_water_remaining_kg` 推进，蒸发后不恢复，也不能大于总库存水量。温度只对未燃/受热表面有物理意义；原胞点燃或燃尽后，`temperature_valid_mask` 置为 false，避免把保留的数组值误解为火焰或炭层温度。

### 8.2 同步推进顺序

每个时间步先冻结旧时刻的燃烧源，再对完整增长—稳定—衰减曲线做区间 HRR 积分并扣减燃料。源功率经过同一张 V04 图一次性分配，接收功率再进入表面热平衡。所有温度、水量、燃料、点燃和燃尽结果最后同步提交，因此新点燃原胞从下一步才开始放热，遍历顺序不会改变结果。

步长取用户上限、下一个延迟火源、下一个采样时刻、热稳定上限和最近曲线终点的最小值。这样可精确落在延迟点火、燃尽和采样时刻。热输入消失后，未燃/受热原胞通过对流、再辐射和背衬导热冷却。直接点火与热引燃同刻发生时，直接点火原因优先。能量诊断分别记录释放、辐射/非辐射、截获/未截获、可燃受体吸收/未吸收、不燃受体接收和非受热可燃格接收量。

### 8.3 冻结夹具和小域诊断

冻结夹具为 [`v05-small-domain.synthetic.yml`](../examples/village/vegetation_v3/v05-small-domain.synthetic.yml)，SHA256 为 `26f3452d692793ebc683519756b74c1d1c653860e6e1accdc0fc5a2442b35ed6`。其中强边权重为 0.08，弱边权重为 0.005；所有燃烧和热参数均标记为 synthetic，只用于数值验收，不能作为高友村材料参数。

| 诊断 | 结果 |
|---|---:|
| 强边三格草地链点燃时刻 | 0.00 / 0.75 / 1.50 s |
| 强边三格草地链燃尽时刻 | 2.25 / 3.00 / 3.75 s |
| 最终燃尽格 | 3 / 3 |
| 消耗可热解燃料 | 3.0 kg |
| 释放能量 | 3,000 J |
| 截获 / 未截获辐射 | 160 / 2,840 J |
| 弱边建筑→草地目标 | 302.5 K，保持 `HEATING`，未点燃 |

[`test_solver_v3.py`](../tests/village/test_solver_v3.py) 覆盖 O-T01–O-T07、O-T10 与 VT02、VT10、VT11、VT15、VT17，包括草地链式传播、建筑与植被双向传播、同一树对象不整片同时点燃、跨混凝土道路辐射、unknown/NoData、不完整覆盖、冷却、建筑 ID 不影响动力学、延迟火源、精确采样、步长/边序收敛和能量分配。O-T08 位于 [`test_survey_adapter.py`](../tests/village/test_survey_adapter.py)，验证同一物理输入经 M03 适配和直接 V03 数组进入求解器时结果一致。

### 8.4 V06/M04 小域输出与网页接线（已完成小域路径）

新增 [`workflow_v3.py`](../src/pyfireca/village/workflow_v3.py) 和 [`outputs_v3.py`](../src/pyfireca/village/outputs_v3.py)。`village-v3-case-1` bundle 保存完整 V3 请求的掩膜、物理数组、辐射边、场景和材料标签；`validate-v3` 可以独立加载并重新执行严格校验，`run-v3` 只接受已序列化且已验证的请求，不在 CLI 中偷偷补材料默认值。

V3 结果目录包含 `run.json`、`parameters.resolved.yml`、`material_outcomes.json`、`energy_budget.csv`、最终状态/事件/水量数组、采样快照和 `viewer/`。网页协议为 `village-viewer-3`，在保留 V1/V2 读取能力的同时增加材料分区、地类分区、剩余燃料、表层水量、温度有效掩膜、首次点燃、燃尽和点燃原因。材料与地类是静态图层，状态、温度、HRR、燃料和表层水量是动态图层；建筑统计与建筑外植被统计分别显示。

V3 bundle 和 viewer 的端到端测试在 [`test_solver_v3.py`](../tests/village/test_solver_v3.py) 中，验证独立保存—加载—运行、V3 manifest、标签、动态二进制长度、材料结果和能量输出。V09/M06 另外验证按帧块写出和网页读取；块写出降低输出阶段峰值内存，但求解器当前仍保留采样快照，因此不能把它等同于完整大域流式求解。

V07/M05 已从高友村 V01–V04 静态包生成一个“已分类区域演示” bundle：保留真实 UTM49N 原点、B0233 世界坐标火源和 V04 辐射边，明确标记 74,829 个 unknown 以及合成 profile/RGB 假设。V08 已从该 bundle 截取真实世界坐标小域并完成建筑起火、草地起火各一个 600 s 演示；结果如无跨类点燃也原样保留。V09/M06 新增 `inspection_v3.py` 和 `village inspect-v3`，对首帧、中间帧、末帧的二进制长度、状态/燃料后端数组一致性和指定原胞属性进行审计；`outputs_v3.py` 支持通过 `--chunk-frames` 将动态 viewer 数据按帧块写出，网页会合并并校验各块。V01 的嵌套底图清单已修正，裁剪小域建筑归属也按裁剪窗口重映射。

V09/M06 产物包括：带卫星底图的 B0233 小域结果 `runs/gaoyou-vegetation-v3/v09-gaoyou-b0233-with-background-run2/`，分块回放结果 `runs/gaoyou-vegetation-v3/v09-gaoyou-b0233-chunked-run/`，以及首/中/末帧审计报告 `runs/gaoyou-vegetation-v3/v09-inspection/b0233-chunked.json` 和 `b0233-with-background-run2.json`。Chrome 当前打开的分块 V3 页面为 `http://127.0.0.1:8797/`。两种输出均复现 11 帧、21 个点燃原胞和 85.338727 kg 消耗燃料；审计 `state_samples_match=True`、`fuel_samples_match=True`。

V10 预检报告为 `runs/gaoyou-vegetation-v3/v10-preflight/gaoyou-v07.json`。整村资源估算在默认 5,000,000 边和 2,048 MB 限制内，但报告明确阻止研究级全村运行：74,829 个 unknown 尚未有现场材料或审查后的覆盖策略，当前场景仍含 synthetic profile/RGB 假设，范围仍是 `classified_area_only`，并使用 `unknown_policy=exclude`。预检不修改阈值、半径或材料系数，也不执行求解器。正式全村运行必须使用新材料/unknown 决策重新生成 bundle，并将策略固定为可审计的输入状态。

随后新增 `unknown_review_v3.py` 与 `village review-unknown-v3`，专门为现场复核提供空间清单，不给 unknown 原胞偷偷赋予材质。它以 4 邻域提取连通分区，以 8 邻域统计相邻已知材质，并从准备包的卫星底图计算描述性的 RGB 与 Excess-Green 分位数；输出 `unknown_review.json` 和 `unknown_components.csv`，同时写入准备包/案例清单/底图 hash 和 `no_case_arrays_modified: true`。高友村最新报告在 `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-with-survey-template/`：74,829 格、299,316 m²、2,278 个分区，最大两个分区分别为 24,268 格和 23,334 格。相邻标签以草地和建筑等效燃料为主，这只能用于安排现场核查优先级，不能视作自动分类证据。该目录另有 `unknown_component_survey.template.yml`，按 `village-field-survey-1` 生成 2,278 条仍为 `material_class: unknown` 的待调查记录；它只提供稳定的分区/几何引用，不产生材质结论。报告列出 `strict_error`、单独演示用 `explicit_inert_demo` 和现场映射 `field_material_mapping` 三种策略；研究级运行仍需现场材料映射和新的 `unknown_policy=error` bundle。
随后新增 `survey_mapping_v3.py` 与 `village validate-unknown-mapping-v3` 作为调查填写门禁。它要求每个未知分区至少有一条 `v3-unknown-component/Cxxxx` 记录，允许 `/part-*` 拆分混合区域；`unknown` 和 `composite_unknown` 始终阻断后续解析。当前模板验证为 `blocked`：2,278 个分区均已引用，但 2,278 条记录仍未填写材质。该命令只验证调查空间引用和材料身份，不生成参数、不改变 V3 数组；材料填写完成后才可进入 M02 证据/构造预设解析。

M02 解析现在由 `resolution_workflow_v3.py` 与 `village resolve-survey-v3` 接线。它读取独立的几何面积 YAML，调用现有材料卡、构造预设和初始水分包，输出 `village-resolved-survey-case-1` 内容、每条问题、配置 hash 和输入文件 hash；缺少材质、数量档位、面积、初始水分或唯一构造预设时保持阻断，不补零、不选 `standard`、不写 V3 原胞数组。高友村模板的解析报告位于 `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-m02-template/resolved-template.json`，状态为 `blocked`；`unknown_geometry_areas.template.yml` 仅是按分区面积生成的待编辑模板，分区拆分后必须重新给子几何提供不重复的面积。
M03 空间审查将未知连通分区写为 `unknown_component_id.npy`，并在 `validate-unknown-mapping-v3` 中逐格核对分区 ID 与 CSV 格数。高友村最新 M03 审查目录是 `runs/gaoyou-vegetation-v3/v10-unknown-review/gaoyou-m03-spatial-review/`；模板目前 2,278/2,278 分区引用完整，但所有材质仍为 `unknown`，所以不能进入参数解析或正式 bundle。该栅格只是审查索引，不会覆盖 V01/V03 的 `landcover_index` 或物理数组。
用户选择先按卫星图片做演示推测后，`rgb_hypothesis_v3.py` 将 V01 unknown 域内格统一映射为 `bare_or_road`，并把证据标为 `rgb_hypothesis`。这是低 Excess-Green 的惰性背景假设，用于补齐材质索引，不区分道路、裸土、屋顶、阴影和被冠层遮挡的植被；建筑轮廓、已分类草/灌/树格保持原值。结果位于 `runs/gaoyou-vegetation-v3/v10-rgb-hypothesis-spatial/`，74,829 格被补为推测裸地/道路，最终有效域 189,150 格都有索引；原 V01 包不变，也没有产生燃烧参数。

将这份地图接入动态 V3 时，建筑外不燃格若也作为辐射受体，原 8 m 半径会产生 5,447,216 条候选边，超过 5,000,000 边门限。因此这次只交付静态假设材质地图，动态全域运行没有通过资源门禁；不能通过放宽门限或缩短传播半径来掩盖该限制。

### 8.6 RGB 完成图的资源敏感性（V10，静态诊断）

`resource_sensitivity_v3.py` 使用完成后的 `known_mask` 作为受体掩膜、V03 的可热解燃料数组作为源掩膜，并复用 `transport_v3.py` 的候选边计数。报告阶段不分配边数组、不运行 V3 求解器，因此不会改变旧结果。当前 2 m、352 × 552 网格在 500 分钟、5 分钟采样下有 101 帧；全域 189,150 格中，114,321 格是可燃源，74,829 格是只接收热量的惰性背景格。

报告 [`v10-rgb-resource-sensitivity/report.json`](../runs/gaoyou-vegetation-v3/v10-rgb-resource-sensitivity/report.json) 的 SHA256 为 `821ae5191ef4b9549c38f5a0d594528eff99f190196466a788bbcebb647629a8`。当前 case snapshot 的 8 m 半径需要 5,447,216 条候选边，构图峰值约 348.622 MB，连同求解器回放约 996.237 MB；2,048 MB 内存上限足够，但 5,000,000 边上限不足。资源策略的最小配置值是 `max_edge_count >= 5,447,216`，而不是改变燃烧阈值、删除建筑外格或缩短半径。4 m、6 m、10 m 的估算只用于审查敏感性，不能自动替换活动情景。

在审阅并显式提高边数上限之前，不得构建新的全域动态图。即使构图通过，RGB 类别和 synthetic profile 仍须在输出中标为 `hypothetical_only`/`synthetic`；本报告不是火灾预测，也没有证明真实村落会传播到某个范围。

### 8.7 V10 全域 RGB 推测演示结果

为验证资源方案，`build-gaoyou-rgb-v3` 支持只对新 bundle 生效的资源上限覆盖。本轮使用 6,000,000 条边和 2,048 MB，原始 V04 snapshot 保持 5,000,000 条边不变。新的全域 bundle 通过资源预检并生成了 5,447,216 条边；研究预检仍因 synthetic 假设和 `hypothetical_full_domain` 范围保持阻断。

30 秒烟测和 500 分钟全域演示都调用同一个 V3 联合求解器。500 分钟结果有 101 帧、30,411 个实际时间步、6,314 个首次点燃原胞和 215,329.386906 kg 消耗燃料；分块 viewer 的首/中/末帧后端审计全部通过。网页服务地址为 `http://127.0.0.1:8801/`。这些数值只能说明当前假设输入在程序中可复现，不能作为真实火灾范围或材料燃烧系数的证据。

本次网页初始没有卫星底图，是因为 `run-v3` 调用没有携带 `--prepared`，输出层按无空间资产模式写入了 `background: null`。现在 V3 bundle 元数据保存 `spatial_assets_dir`，`run_v3_case_bundle` 在未显式传入 `--prepared` 时自动恢复底图、仿射定位、建筑几何和建筑原胞索引；当前全域 viewer 也已补齐这些资源。底图只负责显示，不参与任何燃烧计算。

### 8.8 活跃火源传热索引

V3 的物理计算仍是第 23.5/25.5 节定义的同步辐射预算。实现层现在为按源排序的边表建立累计偏移：本时间步 HRR 大于零的原胞只访问自己的出边并在共同受体上累加；当活跃边达到总边数的 25% 时使用原来的全边 `bincount` 路径。两条路径都使用相同的 `radiative_power[source] × edge.weight`，不会裁剪低功率源、缩短半径或改变能量分配。

真实高友村图在 0/1/10/100/1,000/10,000/30,000 个活跃源下均与全边计算通过 `rtol=1e-14, atol=1e-9` 比较。30 秒烟测的最终数组和全部采样数组与优化前精确相同。该优化只减少无效边遍历，不改变当前结果的 `synthetic_profiles_with_rgb_hypothesis_inert_completion` 证据等级。

### 8.9 表面热平衡的向量化

`advance_surface_heating_and_evaporation` 现在用数组掩膜执行与原实现相同的事件顺序。负净热通量先按总热容冷却并限制到环境温度；正净热通量先消耗升至沸点所需的显热，再消耗汽化潜热，最后才加热无水干质量。库存水量和表层水量的边界语义没有改变。该优化只减少 Python 循环，不引入近似、跳过受体或改变热平衡公式。

高友村 30 秒烟测的 12 个输出数组与此前活跃火源索引版本逐字节一致，单元测试覆盖水分蒸发和冷却不回水。后续性能分析需继续区分燃料曲线积分、状态复制和采样开销；任何活动受体裁剪仍需先证明逐步温度、燃料、状态和能量数组等价。

### 8.10 稀疏有限燃料积分

V3 每步的有限燃料 HRR 预算现在只对 `burning=True` 原胞计算增长、稳定和衰减曲线的区间积分。非燃烧原胞的释放能量、质量损失和平均 HRR 仍为零；燃烧原胞的曲线反导、燃料上限截断和质量换算没有改变。这样不会把建筑外受体排除在热计算之外，只跳过没有放热的源。

真实高友村 30 秒烟测的状态、燃料、水量、温度、事件和能量数组均与前一版本逐字节一致。该优化只适用于源项积分；目标受热、冷却、点燃和燃尽仍按联合求解器的同步顺序执行。

### 8.11 状态快照内存边界

采样间隔改变的是输出快照数量，不改变 V3 的内部时间步或物理更新。当前求解器每个快照保留状态、温度、温度有效性、表层水、剩余燃料和区间 HRR 六个数组；高友村 194,304 格的单帧约占 6.61 MB。500 分钟、5 分钟采样的 101 帧约占 667 MB，因此下一版大域工作流应将快照写入分块文件后释放内存，并让 viewer 直接读取块清单。

该内存优化不能改变采样时刻、数组 dtype、状态编码或后端审计值。小域必须先比较内存快照与流式文件逐帧完全一致，再接入高友村全域重跑。

### 8.12 流式快照实现（V10）

`solver_v3.py` 的 `run_v3_simulation` 接受可选 `snapshot_sink` 和 `retain_snapshots`。sink 在求解器到达精确采样时刻时收到一份不可变快照；`retain_snapshots=False` 时，求解器只保留最终状态和能量账目，不在结果对象中积累动态帧。默认参数仍是旧行为，方便小域测试和现有调用方。

`outputs_v3.py` 的 `V3StreamingSnapshotSink` 为 `run-v3 --chunk-frames N` 提供实现。它按固定帧数写出 `state`、`fuel`、`temperature`、`temperature_valid`、`hrr` 和 `surface_water` 六类 C 顺序二进制块，并在临时目录中维护两个磁盘背 NumPy 数组。输出层关闭并校验 sink 后，复制这些数组和块到新的 run 目录；manifest 中的 `sample_times_s`、`frame_count`、dtype、块起始帧、字节数和单位与内存模式一致。临时目录不作为结果产物，失败或成功退出后均不覆盖已有 run。

这条路径的验证不是只看文件存在：V3 bundle round-trip 测试逐元素比较内存与流式的状态样本、燃料比例和采样时间；高友村 30 秒 smoke run 通过 `inspect-v3` 的后端一致性与视觉验收。流式路径只降低快照保留的峰值内存，不能减少物理时间步、跳过建筑外可燃或不燃受体、修改辐射预算，也不能把低频采样当作物理加速。全村 500 分钟重跑仍需新的材料证据和 unknown 决策批准。

### 8.13 状态提交热点基准（V06）

流式实现之后，使用同一 30 秒高友村 smoke bundle 对 5 s、30 s 和不采样运行做 cProfile/tracemalloc。结果保存在 [`state-commit-profile.json`](../runs/gaoyou-vegetation-v3/v10-active-source-benchmark/state-commit-profile.json)，SHA256 为 `8a2d8799f4eb559525f690ef221e096ebd430f402bf97f71332152b606756c7d`。三种模式各执行 30 个物理时间步，快照数量为 7、2、0，内存峰值约 107.3 MB、74.3 MB、64.2 MB。

当前主要耗时仍来自表面热平衡、净表面热通量、燃料曲线积分和时间步上限计算；数组 `copy` 的一次烟测累计约 0.012–0.014 s。采样会提高内存峰值，但在这个域和时长上没有显示出 CPU 主导的状态提交热点，所以没有为了“优化”而改变状态提交、活动受体集合或采样规则。后续只有在更大域/更长时长中确认数组提交成为主热点，才允许设计临时数组复用，并必须通过逐时间步等价性测试。

网页验收时发现独立 smoke bundle 没有保存 `spatial_assets_dir`，因此第一次流式结果的 manifest 为 `background: null`；这属于输出输入接线问题，不是求解器或分块协议问题。保留该诊断目录后，使用同一案例显式传入 `v10-rgb-hypothesis-spatial` 生成 `v10-rgb-hypothesis-smoke-run-streamed-background/`，底图、建筑几何和 B0233 起火格对齐，后端审计不变。带底图 viewer 已在本机 `http://127.0.0.1:8802/` 提供检查；全村 500 分钟结果仍未重跑。

### 8.14 全域流式演示结果（V06）

用户要求继续后，使用 `v10-rgb-hypothesis-full-domain-case-6m`、显式 `v10-rgb-hypothesis-spatial` 和 `--chunk-frames 8` 生成 `v10-rgb-hypothesis-full-domain-run-streamed-6m/`。结果为 500 min、5 min 采样、101 帧、30,411 个物理时间步和 6,314 个首次点燃原胞；viewer 采用 8 帧分块。它与既有 500 min 结果的最终状态、温度、水量、燃料、事件、能量以及 101 帧状态/燃料样本逐项相同（`equal_nan=True`，最大绝对差为 0）。

该目录的 `inspect-v3` 通过视觉和后端一致性检查，网页在 `http://127.0.0.1:8803/` 提供底图和 101 帧回放。运行没有改变辐射边、材料参数、点燃阈值或 unknown 策略；所有分类和燃烧参数仍标记为 hypothetical/synthetic。这个结果用于软件流程验收，不能作为高友村现实火灾预测或实测材料证据。
