# 高友村现场材料调查输入

现场调查文件只记录材料身份、所在部位和空间位置。调查者不需要填写燃烧热、比热、辐射率、HRRPUA、引燃温度或传播系数；这些内容由后续材料卡和构造预设解析。

当前输入格式是 `village-field-survey-1` YAML。最小记录需要五项：

- `observation_id`：调查记录唯一编号；
- `object_id`：建筑、墙段、院落或植被区域编号；
- `component`：`facade`、`roof`、`interior_wall`、`contents`、`ground` 或 `vegetation`；
- `geometry_ref`：指向建筑、多边形、墙段或 GIS 图层对象的稳定引用；
- `material_class`：第一版材料类别 ID。

可选的 `quantity_band` 只描述数量或构造厚度，例如 `thin`、`standard`、`thick`、`low`、`medium`、`high`。它不描述干湿。`subtype` 和 `notes` 可记录树种、覆层、复合结构或现场说明。

## 材料类别

| ID | 现场选择含义 |
|---|---|
| `wood_unspecified` | 树种未知的木材 |
| `bamboo_unspecified` | 工艺未知的竹材 |
| `thatch_straw` | 茅草或秸秆 |
| `masonry_brick` | 砖砌体 |
| `concrete_mortar` | 混凝土或水泥砂浆 |
| `stone` | 石材 |
| `metal_sheet` | 单层金属板 |
| `grass` | 草地 |
| `shrub` | 灌木 |
| `tree_equivalent` | 树木枝叶等效燃料 |
| `litter` | 枯枝落叶 |
| `mixed_contents` | 室内混合物品 |
| `composite_unknown` | 未知复合材料 |
| `unknown` | 无法判断的材料 |

砖、水泥、石材和单层金属板在当前材料模型中可以映射为零燃料，但这不代表它们没有受热、隔热、破坏或遮挡作用。室内砖墙也不代表室内家具和木楼板不存在；这些部位必须单独调查。

## 含水状态

调查文件不允许 `condition`、`dry`、`wet` 或类似干湿档位。材料类别是相对稳定的身份，含水率是带时间的场景状态。后续场景会引用独立的初始水分状态包；火灾受热期间的蒸发由求解器计算。当前版本不模拟降雨湿润、空气吸湿或天气变化自动改变材料水量。

## 现场示例

```yaml
schema_version: village-field-survey-1
survey_id: gaoyou-field-survey-example
records:
  - observation_id: OBS-001
    object_id: B0233
    component: facade
    geometry_ref: wall-segments/B0233-east
    material_class: wood_unspecified
  - observation_id: OBS-002
    object_id: B0233
    component: interior_wall
    geometry_ref: buildings/B0233
    material_class: masonry_brick
  - observation_id: OBS-003
    object_id: YARD-001
    component: ground
    geometry_ref: survey-polygons/YARD-001
    material_class: concrete_mortar
```

这些几何引用是格式示例。它们必须在后续空间适配阶段与真实建筑标注、墙段或调查多边形连接，不能直接当作高友村已有数据。

验证示例：

```bash
conda activate pyfireca-village
python -c 'from pyfireca.village.survey import load_field_survey; s=load_field_survey("examples/village/material_cards/survey.example.yml"); print(s.survey_id, len(s.records))'
```

M00 只验证结构和稳定枚举。M01 才将类别连接到逐参数证据卡，M02 才解析构造数量和初始水分状态，M05 才把墙段及院落几何分配到高友村原胞。

## M01 材料卡库

最小材料卡库位于
[`examples/village/material_cards/catalog.yml`](../examples/village/material_cards/catalog.yml)，
由 `village-material-cards-1` 解析器校验。它只负责把现场类别连接到参考试件、文献角色和适用边界，不复制燃烧热、HRR、引燃温度或含水率数值；这些数值仍须在后续构造预设和场景水分包中独立解析。

卡片的 `evidence_status` 有四种含义：

- `reference_only`：有可追溯的参考试件，但不能当作高友村实测；当前为木材和层压竹参考卡；
- `pending_full_text`：已登记方法或分类来源，仍缺少足以生成当地数值卡的原文条件、试件或构造信息；
- `scenario_assumed`：只允许在明确标记的演示场景中使用；当前库不预置此类卡；
- `field_observation_only`：现场观察结果，尚未能映射为燃烧参数；混合物品、未知复合材料和未知类别使用此状态。

砖砌体、混凝土/水泥砂浆、石材和金属板使用不燃候选分类，但这只约束燃料贡献。分类文件的产品和最终使用条件必须成立；它不意味着墙体不传热、不受损或其背后的木构件和室内物品不燃。`production_ready` 仍固定为 `false`，任何缺少来源、适用边界或实测条件的类别都不能进入真实全村运行。

验证材料卡库：

```bash
conda activate pyfireca-village
python -c 'from pyfireca.village.material_cards import load_material_card_library; c=load_material_card_library("examples/village/material_cards/catalog.yml"); print(len(c.cards), c.evidence_status_counts)'
```

M01 完成后，M02 才会增加数量档位、构造预设和独立的初始水分状态解析；现场调查文件仍不增加干湿选项。
