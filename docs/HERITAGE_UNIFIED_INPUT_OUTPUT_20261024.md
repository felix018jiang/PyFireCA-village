# 统一村落输入与防火策略输出

完成日期：2026-10-25，沿用2026-10-24冻结输入批次。本轮完成“统一资料接入 → 已准备物理案例核对 → 最小干预搜索 → 地图、建筑清单与代价导出”的可运行闭环。岜团实案复用108份保存评价，新增CA求解为0；保留原材料、25 m²屋面点火、1200 s、无风、1 m/0.5 m及0.5 m辐射求积规则，没有启动331栋穷举。

## 入口与运行

统一清单为[岜团输入](../examples/research/heritage/batuan-strategy-input-20261024.json)。所有路径相对于各自清单文件解析，村名、核心ID、候选ID和代价从输入读取。

```sh
.venv/bin/python scripts/run_heritage_strategy.py \
  --manifest examples/research/heritage/batuan-strategy-input-20261024.json \
  --output runs/research/batuan-strategy-export-new
```

输出目录必须是新目录。默认地图字体为当前macOS的黑体；其他环境通过`--font`指定支持中文的字体文件。依赖复用已有`gis`、`village`、`planning`环境。

当前入口使用`search.mode=saved_physical_checks`，只读取已有物理评价，缺少的检查返回未知并阻止相应最优性认证。4096是缓存查询预算，不是允许新增4096次模拟。新村落先用现有GIS与V3准备模块形成物理案例，再冻结情景并按预算生成评价；本轮没有新增一套影像分类器，也不从RGB自动猜测材质参数、遗产身份或费用。

## 统一输入协议

| 字段 | 内容与核查 |
|---|---|
| `village_id`、`place_name`、`spatial_crs` | 村落身份；建筑、影像参考及物理案例使用同一个以米为单位的投影坐标系 |
| `inputs.imagery_tiff` | 已配准TIFF；校验CRS、尺寸、仿射变换、建筑覆盖范围及其在物理案例中的来源哈希；地图使用显式准备的8位RGB前三波段 |
| `inputs.image_reference`、`annotations`、`footprints` | 影像参考、LabelMe标注和已有世界坐标轮廓；核对稳定ID、几何和`gulou`/`fengyuqiao`标签，改变的几何不能套用旧评价 |
| `inputs.materials` | [材质绑定](../examples/research/heritage/batuan-strategy-materials.json)：`profile_library`、完整`material_profiles`、显式`roof_profile_id`；沿用现有带单位和来源的材料库 |
| `inputs.heritage` | [遗产与资格清单](../examples/research/heritage/batuan-heritage-inventory.json)：`targets`、禁拆项、源保留项、候选资格及证据。遗产目标不允许进入拆除候选 |
| `inputs.prepared_case` | 已准备V3案例，包含物理数组、地形、背景分类与接触/辐射设置；输入材质重新解析的33项参数须与这些数组相符，不覆盖或重调原参数 |
| `search` | 保存物理问题及补齐目录、显式搜索候选和查询预算。核对案例、建筑清单、传播参数和物理问题指纹；候选扩展需另建物理问题 |
| `costs`、`pending_data` | 无经济资料时`costs=null`，明确待补事项；有完整声明代价才启用数量优先、代价次之 |

当前屋面物理模型仍是明确声明的等效建筑材质：各屋面从同一已准备材料假设继承参数。背景可以有多种材料与证据等级。独立建筑温度不意味着已经支持逐栋不同构造的完整物理标定；异质屋面材料需在后续模型支持范围内接入，不能只换输入标签就宣称完成。

费用文件沿用`values`（每个搜索候选的完整正有限数）、`unit`（可比单位/币种），另要求`evidence`注明调查或估算来源与口径。费用缺项或缺单位报错。提供数字只表示“声明费用”；本轮岜团没有填入虚构价格或把未知费用记成零。当前次目标仍为名义屋面面积，实际补偿/搬迁费待补。

## 本轮交付文件

完整目录：[岜团策略包](results/heritage-strategy-batuan-20261024/validation.json)。

| 输出 | 用途 |
|---|---|
| [策略图PNG](results/heritage-strategy-batuan-20261024/strategy-map.png)、[PDF](results/heritage-strategy-batuan-20261024/strategy-map.pdf) | 原始TIFF底图、全村位置、逐核心局部图、拆除与共享保留屋面，带比例尺和坐标参考 |
| [建筑策略GeoJSON](results/heritage-strategy-batuan-20261024/strategy-buildings.geojson) | 全部333对象及保护/拆除/保留动作、搜索资格范围和材质绑定 |
| [清空与共享保留GeoJSON](results/heritage-strategy-batuan-20261024/clearance.geojson) | 实际清空屋面和必须保留的共享屋面；没有额外画出未经验证的连续隔离墙 |
| [拆除清单CSV](results/heritage-strategy-batuan-20261024/demolition-list.csv) | 建筑ID、名义面积、分配清空面积、与保留建筑的共享面积、费用待补状态和资格证据 |
| [代价汇总JSON](results/heritage-strategy-batuan-20261024/cost-summary.json) | 数量、面积、费用/单位/来源及缺失项；未知费用和住户数为`null`，CSV留空 |
| [逐核心保护CSV](results/heritage-strategy-batuan-20261024/heritage-protection.csv) | 实际源、被拆源替代情况、尺度、峰温、首次引燃和模拟时长 |
| [输入收据](results/heritage-strategy-batuan-20261024/input-receipt.json)、[坐标说明](results/heritage-strategy-batuan-20261024/spatial-reference.json)、[完整搜索结果](results/heritage-strategy-batuan-20261024/strategy-result.json) | 输入/物理问题指纹、材料证据、情景与最优性范围；导出验证还保存源代码与产物哈希、原生评价引用 |

GeoJSON使用经度、纬度顺序的EPSG:4326，便于GIS交换；所有面积在原EPSG:32649中计算。面积属性不从经纬度直接求取。共享屋面按“仍有任一保留所有者就保留燃料”导出；多栋拟拆轮廓重叠时，清空面积按ID顺序分配到不重叠片区，供清单求和，不当作真实产权划分。

## 岜团实案验收

333建筑对象、两核心、331个暂定可拆对象接入成功；**搜索仍限声明的8候选、4个已知设计火源**。统一输入得到的案例与物理问题指纹分别仍为`78c97d31…501c`和`913365a1…e642`。33项物理数组与原材质解析结果匹配，TIFF/标注/核心身份及库存指纹一致。

| 建筑 | 名义面积 m² | 分配清空 m² | 与保留建筑共享 m² | 经济费用 |
|---|---:|---:|---:|---|
| B0061 | 201.661054 | 197.846013 | 3.815041 | 待补 |
| B0071 | 216.698662 | 213.170816 | 3.527846 | 待补 |
| B0076 | 32.880740 | 32.825139 | 0.055600 | 待补 |
| 合计 | **451.240456** | **443.841969** | **7.398487** | 待补 |

导出面积与全部8份最佳方案原生评价账本在10⁻⁶ m²容差内一致。108条物理记录全部命中、0缺失；复现8候选内数量最少、同数量面积最小的原三栋方案。该108条是精确搜索的物理缓存，不是重新进行108份独立随机确认。

| 已知设计火源标签 | 三栋方案实际起火建筑 | 鼓楼峰温 1 m / 0.5 m，K | 风雨桥峰温 1 m / 0.5 m，K | 1200 s引燃 |
|---|---|---:|---:|---|
| B0075-known | B0075 | 429.72 / 373.15 | 300.00 / 300.00 | 两核心均无 |
| B0071-known | B0067（顺延） | 327.52 / 327.18 | 300.00 / 300.00 | 两核心均无 |
| B0062-known | B0062 | 300.00 / 300.00 | 373.15 / 373.15 | 两核心均无 |
| B0061-known | B0063（顺延） | 300.00 / 300.00 | 373.15 / 373.15 | 两核心均无 |

全部16行“情景—核心”结果与保存的原生评价一致，不能把被拆源视为没有火，也不把两尺度当成两次独立抽样。前期新落点保护证据仍见[108份独立确认](HERITAGE_TRIPLE_VALIDATION_20261024.md)和[B0063补充](HERITAGE_B0063_ADDBACK_20261024.md)，本轮没有增加新的独立火灾样本。

相关26项检查通过：新输入配准/CRS/材质/遗产/候选冲突、共享与重叠面积、GeoJSON往返、缺失费用和声明费用导出，以及已有目标/精确搜索/补齐逻辑。地图已人工检查，静态检查通过。当前验收完成。

## 后续接入

第二村落的数据盘点和最低要求见[第二村落准备清单](HERITAGE_SECOND_VILLAGE_DATA.md)。下一阶段先核定该村核心遗产及同坐标输入、资格/材料和情景资料，再按明确预算验证迁移。经济实测资料、跨村物理证据和更大候选搜索分别推进，不将当前8候选证明外推成全331栋最优，也不启动村外试验。
