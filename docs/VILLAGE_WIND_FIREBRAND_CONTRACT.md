# W00：风场、火焰倾斜与火星传播契约

更新：2026-09-30。依据：[PROJECT_PLAN.md 第 26 章](../PROJECT_PLAN.md#26-风场火焰倾斜与飞火传播2026-09-30-新增计划)。

**状态：接口设计冻结，物理实现尚未接入。** 本文件规定 W01–W05 的字段与兼容语义；下列新配置不能交给当前求解器运行。公式出处和适用边界沿用计划中的 R1–R6，不新增高友村经验系数。

## 1. 版本与旧结果兼容

- 现有 `village-v3-case-1` 使用已存储的 `ExposureEdges`；缺少新配置表示沿用旧物理，不表示重新生成“无风”边。即使关闭新机制，也不能覆盖用户旧案例的风效应。
- 新 bundle 使用 `village-v3-case-2`，新增必填 `physics_extensions`、`transport_geometry`。当前 loader 已拒绝该版本，直到 W01/W02 接通；禁止往旧 manifest 的 metadata 塞入参数并声称已生效。
- 新输出用 `village-viewer-4`；保留旧 viewer-3 读取路径。新增字段缺失时，旧回放不显示不存在的风箭头或火星。
- 未实现的模型/字段必须拒绝。现有 `/api/run` 只接受 `row`、`col`、`end_time_s`；新接口计划为 `/api/v2/run`，避免旧服务忽略新参数。
- 关闭所有新机制且使用同一旧边图时，所有物理数组、事件和采样时间应与旧入口一致。新风输入要求具备边重建/动态更新能力；不具备时拒绝运行。

## 2. 证据参数的共同结构

本节冻结新增参数类型 `Quantity`，不改写现有材料库的 `EvidencedValue`：

```yaml
value: 0.0
unit: m/s
evidence: synthetic
source: "synthetic:W01_zero_wind_fixture"
```

四个键必填，额外键拒绝。`value` 是有限数，bool 不当数字；`unit` 必须精确匹配契约。`evidence` 仅允许 `synthetic`、`measured`、`literature`、`calibrated`；`source` 非空，分别指向合成说明、测量记录、文献定位或标定产物。枚举不自动授予可信性。模型引用与系数证据分开，例如 I1 公式来自 R1，但未标定的阈值仍是 synthetic。

内部一律 SI；UI 分钟只在边界转换成秒。风场表里的每个样点共用该表的单位和证据记录，避免重复嵌套。

## 3. `physics_extensions` 的精确字段

顶层字段固定为 `schema_version: village-wind-firebrands-1`、`wind`、`flame_tilt`、`firebrands`、`seed`。所有对象拒绝未知字段；`seed` 是 `[0, 2^63-1]` 内的整数，不能是 bool。

### 3.1 wind

| mode | 必填字段及类型 | 校验 |
|---|---|---|
| `legacy_edges` | 只有 `mode` | 仅复用已有边；不可为新颗粒提供风，`firebrands.enabled` 必须为 false |
| `uniform` | `mode`、`speed_m_s: Quantity`、`from_deg: Quantity`、`reference_height_m: Quantity`、`direction_reference` | 单位 m/s、deg、m；速度 ≥0、角度 [0,360)、参考高度 >0 |
| `timeseries` | `mode`、`samples`、`reference_height_m: Quantity`、`direction_reference`、`evidence`、`source` | samples 为 `{time_s, speed_m_s, from_deg}` 列表；时间严格递增，覆盖 `[0,end_time_s]`，其余数值范围同 uniform |
| `grid_uv`（W05） | `mode`、`manifest_path`、`sha256` | 相对路径按配置文件目录解析；输入 manifest 及数组写入新 bundle，运行不依赖外部可变文件 |

`direction_reference` 首版仅允许 `grid_north`：来向角相对计算投影的网格北。来自真北的气象资料要在适配器中旋转到网格坐标并保存转换说明，禁止把真北与网格北无条件等同。W01 使用逐分量 u/v 时间插值；精确样点取原值，不外推。

`grid_uv` manifest 冻结为 `schema_version: village-wind-grid-1`，字段 `grid`（与案例相同的米制网格结构）、`time_s`（升序数组）、`reference_height_m`、`u_path`、`v_path`、`u_sha256`、`v_sha256`、`evidence`、`source`。u/v 是 `<f8` NPY，形状 `(nt, height, width)`，单位 m/s，按网格东/北分量存储。第一版要求与案例网格一致，无缺测；时刻覆盖全程。格心之间双线性插值、边界半格内取最近边缘值，域外颗粒按离域处理，不对域外延拓风场。竖直风固定零。

### 3.2 flame_tilt

- 关闭形式：`{enabled: false}`，无其他键。与 `legacy_edges` 联用时保留旧边原状；与新风模式联用时生成竖直源的新边，二者不能混淆。
- 开启字段：`enabled: true`、`model: aga_piecewise_v1`、`air_density_kg_m3: Quantity`、`source_geometry_path`、`source_geometry_sha256`。空气密度 >0；不能与 `legacy_edges` 联用。
- `source_geometry_path` 指向几何 manifest，版本 `village-flame-geometry-1`。必填 `characteristic_length_m`、`flame_axis_length_m`、`flame_base_height_m`、`burning_footprint_area_m2`，每项为 `{path, sha256, unit, evidence, source}`。数组是 `<f8`、与案例同形状；前三项单位 m，末项 m²。活跃源长度/面积 >0，基部高度 ≥0；非源允许零。
- `m_dot''` 为求解器原胞本地燃烧质量流率除以 `burning_footprint_area_m2`；火星分流质量不算本地燃烧。不得用含火星转移的总燃料扣减率代替。火焰轴长与竖直高度不同。

### 3.3 firebrands

- 关闭形式：`{enabled: false}`，无其他键。
- 开启字段：`enabled: true`、`model: terminal_linear_v1`、`air_density_kg_m3: Quantity`、`sources`、`receivers`、`max_active_particles`、`max_output_bytes`、`trajectory_sample_interval_s`。末三项为正整数/正整数/正有限数，计算资源与显示采样分开。不能与 `legacy_edges` 联用。
- `sources` 是列表，按 `material_index` 唯一选择；每项包含 `material_index`（非负整数）、`state`（`flaming` 或 `glowing`），以及下列 Quantity：

| 字段 | 单位 | 范围 |
|---|---|---|
| `release_mass_fraction` | `1` | [0,1] |
| `particle_mass_kg` | `kg` | >0 |
| `projected_area_m2` | `m2` | >0 |
| `drag_coefficient` | `1` | >0 |
| `release_height_m` | `m` | >0；有效抬升高度，非自动树高 |
| `mass_loss_rate_kg_s` | `kg/s` | >0 |
| `active_lifetime_s` | `s` | >0 |

固定沉降速度取释放质量和面积计算。未在 sources 中的材料不释放颗粒。一种材料先固定一种颗粒类别；混合粒径/状态分布另行版本化。发光颗粒寿命必须与相应实验状态一致，不能由明火公式冒充。

`receivers` 按 `(material_index, particle_state)` 唯一匹配，每项必填：

- `material_index`、`particle_state`、`fuel_bed_description`（非空文本）、`contact_area_m2: Quantity`（>0）。
- `moisture_min_dry_basis`、`moisture_max_dry_basis`：单位 `kg/kg` 的 Quantity，0≤min≤max；用于标定适用性检查，不作自创水分修正乘子。未知或范围外不能运行“已标定”模式，需明确更换参数方案。
- `model: linear_mass_v1` 时只另收 `critical_mass_kg: Quantity`（>0），只允许 flaming。
- `model: logistic_v1` 时另收 `intercept`（Quantity，单位 `1`）、`terms`。terms 是 `{variable, coefficient, valid_min, valid_max}` 列表，变量不重复，系数可有正负号且必须有限。截距和系数各自带证据；不能自动从明火阈值生成阴燃系数。

logistic 首版允许变量：`landed_mass_kg`（系数单位 `1/kg`）、`fuel_moisture_dry_basis`（系数单位 `1`）、`wind_speed_m_s`（系数单位 `s/m`）。这是本项目缩减变量的拟合接口，**不是直接复刻 R5 所有表格行**。使用论文以 g、百分数拟合的系数前必须换算；变量集不符时重新拟合或标为 synthetic。成功事件要有持续明火起燃证据；仅观测阴燃时不得直接产生明火 CA 状态。

## 4. transport_geometry 与缓存

新 bundle 必须保存 `{schema_version: village-transport-geometry-1, mode, provenance, inputs_sha256}`。mode 为 `legacy_precomputed` 或 `dem_dynamic`；provenance 说明构建器版本和原始输入；inputs_sha256 至少映射 DEM、网格、材料、源几何、风、遮挡与辐射参数。哈希是文件字节 SHA256；若另报数组规范字节 hash，字段名明确写 `canonical_sha256`，两者不混比。

有高程数组只证明可显示高程。`dem_dynamic` 还要求源/接收点高程进入边权；不得仅凭 `ground_elevation_m is not None` 就认定新版 DEM 物理正确。旧 V12 作为已存边基线保留，其既有 viewer 标记不作为新构建器证明。

## 5. 输出与同步语义

- `firebrand_events.jsonl`：`event_id`、`particle_id`、`source_cell_id`、`time_s`、`kind`、`x_m`、`y_m`、`z_m`、`mass_kg`、`particle_state`。kind 固定 `released/landed/extinguished/exited_domain`；落地行另有 `target_cell_id`、`p_ignite`（未评估时 null）、`ignited` 和 `reason`。单颗粒落地不重复抽签。
- `firebrand_budget.csv`：每个输出时刻的累计转移质量、在途/待释放质量、空中消耗、落地剩余、域外及失活残余质量与对应化学能。质量项互斥，不重复计数；落地后的剩余燃烧去向另记。
- `ignition_cause` 旧编码保持 `0=none, 1=direct, 2=thermal`，新增 `3=firebrand`。同一时刻多机制竞争优先级固定 `direct > thermal > firebrand`，其他落地证据仍保留。
- `wind_samples` 保存真实输出时刻的风输入/引用；`flame_tilt` 若输出必须注明模型与角度参考。时间终点保存未落地颗粒，不能提前丢弃。
- 新起燃源在下一时间层开始传播；精确落地/天气/点火事件切分积分区间。随机数按 seed 与稳定事件 ID 产生，时间步、采样和遍历顺序不改变抽签。
- 统计局部起燃不写入虚构的整格受热温度。W04 在接入前补充亚格起燃到既有增长曲线的具体更新规则，并以测试固定；本契约不声称已解决该物理闭合。

## 6. W00 的核查与复现

现有 API 拒绝尚未支持的风场字段；DEM round-trip、裁剪、校验失败、旧格式兼容及实际 HTTP 新模拟纳入 `tests/village/test_solver_v3.py`。不需要本机高友村私有数据即可运行这些测试。

本机 DEM 案例基线工具：

```bash
PYTHONPATH=src conda run --no-capture-output -n pyfireca-village python scripts/audit_village_w00.py \
  runs/gaoyou-vegetation-v3/v12-gaoyou-terrain-coupled-full-domain-case \
  runs/w00-gaoyou-baseline-20260930
```

重跑时换一个全新输出目录。工具保存输入及代码 hash，裁剪起火点周围 17×17 格，执行两次相同 30 s 模拟与一次换起火点的 17 s 模拟，保存数组 hash 并检查原案例未改。裁剪只保留内部边，不能当作全域模拟的等价结果。

W00 报告：`runs/w00-gaoyou-baseline-20260930/baseline.json`。报告明确当前 builder 不接受 DEM；下一步 W01 实现统一风输入，W02 再补齐 DEM 构建和动态边权。已在只读 stash 中找到旧 DEM 构建实现可供比较，未恢复 stash 或覆盖当前代码。
