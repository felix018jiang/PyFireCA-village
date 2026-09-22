# 村落植被—建筑原胞火灾模型

> 状态：V00、V01 已完成；V02 物理参数配置尚未开始。  
> 更新日期：2026-09-21。  
> 计划依据：`PROJECT_PLAN.md` 第 23 章。

## 1. 文档边界

本文记录已经实现的能力、数据假设和验收证据。当前 V01 产物是空间数据包，不是火灾预测结果。它可以标出建筑和 RGB 推测的植被候选格，但在 V02 给各类别建立严格、可追溯的燃料 profile 之前，不得直接用于全村植被燃烧模拟。

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

V03 物理数组依然不能直接进入求解器：unknown 原胞尚未得到数据或明确策略，且 V05 联合求解器尚未实现。报告标明 `simulation_executed: false`。

### 6.2 V03 测试退出条件

新增 [`test_physics_v3.py`](../tests/village/test_physics_v3.py)，验证了覆盖率一次换算、湿度增加时蒸发能量增加、单格 HRR 积分与燃料热值相等、数组守恒关系、Unicode profile ID 和输出不可覆盖。VT03、VT05、VT07 已通过。

## 7. V04 高度相关传热与资源限制（已完成）

新增 [`transport_v3.py`](../src/pyfireca/village/transport_v3.py)。草地、灌木、树木等效燃料和建筑使用同一张有向辐射图，不按类别重复分配源功率。每条边使用源格的 `source_height_m`、目标格的 `receiver_height_m` 和 `receiver_area_m2` 计算三维距离；无风时有效点源位于配置火焰高度的一半。风倾斜仍采用显式的静态降阶参数。

实现同时提供逐边枚举参考函数和按偏移、按行分块的向量化构图函数。每个整数偏移只生成一次，Bresenham 中间路径按逐格透射率相乘，零透射边不存储。不同 `block_rows` 必须生成相同的 source、target 和 weight 数组。道路等不燃格不成为源或目标，但在透射率为 1 时辐射允许跨越；超过物理半径不建边。

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

## 8. V05 交接条件

下一步新增 `solver_v3.py`。它应把 V03 的有限燃料、含水率和热平衡数组与 V04 的统一辐射边连接起来，完成同步提交、首次点燃/燃尽时钟、冷却、延迟火源和精确采样。V05 先在冻结的强/弱跨类别小图上完成 VT02、VT08、VT11、VT15 和 VT17，不能直接运行全村 500 分钟。74,829 个 unknown 原胞仍是正式全村模拟的阻断项；V04 的资源预测只包含已分类可燃格，不表示 unknown 已解决。
