# 高友村材料参数证据库

当前清单为 [`examples/village/material-catalog-3.gaoyou-evidence.yml`](../examples/village/material-catalog-3.gaoyou-evidence.yml)，其状态是 `evidence_inventory_not_calibrated`，`production_ready: false`。它保留已有 22 个材料类别，但不是可直接驱动高友村模拟的真实参数包。除已逐项核对的外部参考试件外，本地材料数值保持 `null`。现有 500 分钟模拟依然使用独立的 `synthetic_wood` 场景参数。

M01 新增的类别选择层是 [`examples/village/material_cards/catalog.yml`](../examples/village/material_cards/catalog.yml)，由 `village-material-cards-1` 加载器校验。它与本参数级目录分离：材料卡只保存现场类别、允许部位、参考 profile ID、证据状态和限制，不复制任何数值。木材、层积竹的参考试件沿用本目录的 profile ID；草、灌木、树木等效燃料和枯落物只登记 USDA 标准燃料模型的**方法来源**，暂不把标准模型当作高友村实测。砖、混凝土/水泥砂浆、石材和金属板登记 [Commission Decision 96/603/EC](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:01996D0603-20030612) 的产品分类边界，但仍不能由分类直接生成热传递或燃烧数值。

## 每个数值必须可追溯

`material-catalog-3` 的每一个非空数值都必须记录：

| 字段 | 含义 |
| --- | --- |
| `value` | 数值，必须有限且非负；比例不得大于 1 |
| `unit` | 与参数名规定的单位严格一致 |
| `source_id` | 指向目录顶部的来源记录 |
| `locator` | 论文页码及表、图、公式编号，或现场记录编号 |
| `method` | `measured` 实测、`derived` 推导、`assumed` 假设、`field_survey` 现场调查 |
| `conditions` | 试件、厚度、含水率、方向、外部热流及限制条件 |

缺值写 `null`，不能写 `0` 充数。`reference_only` 表示外部参考试件，不是高友村材料替代品。`village_candidate` 仅表示计划调查的本地类别，不能仅凭这个标记投入运行。参数完整性报告只检查 v2 求解器所需的**材料参数**；建筑构造、受热面积、阻隔与传播标定、独立验证仍必须另做，因此本版目录拒绝 `production_ready: true`。

## 已迁入并逐项定位的参考参数

本轮只采用同行评审论文、政府研究机构保存的论文和正式试验标准。数值必须能定位到原文表格或公式；二手网页、厂商宣传值和无试验条件的汇总表不进入目录。

来源记录强制包含完整引文和 `kind`。当前可接受的数值来源类型为 `peer_reviewed_journal_article`、`peer_reviewed_conference_paper` 和 `government_research_publication`；校验器会拒绝厂商网页等未支持类型。正式标准可约束试验方法，但没有给出材料实测值时不作为数值来源。

| 参考试件 | 已收录参数 | 原文位置与限制 |
| --- | --- | --- |
| 烘干花旗松 | 点火温度 623 K；临界热流 13,000 W/m²；表观热惯量 158,000 J²/(m⁴·K²·s)；吸收率 0.88 | [Janssens 1991](https://publications.iafss.org/publications/fss/3/167/view/fss_3-167.pdf) 表 3、表 4和第 175 页。点火温度是测量均值；临界热流和热惯量由点火关系推导；吸收率是作者假设。试件为烘干花旗松、平均厚 16.8 mm、竖直锥形量热。 |
| 南方松 | 干质量损失基准有效燃烧热 16.26 MJ/kg | [Tran 与 White 1992](https://research.fs.usda.gov/treesearch/5952) 表 4 第 202 页的 55.5 kW/m² 行及公式 5–7。试件为 150×150×64 mm 厚木块、含水率约 8–9%。该量随外部热流改变，所以只保存这一试验行，不把它声明为通用常数。 |
| 含胶层积毛竹 | 含水率 10.5%；有效燃烧热 13 MJ/kg；临界热流 6 kW/m²；近似点火温度 570.15 K；50 kW/m² 下第一 HRR 峰值 235 kW/m² | [Xu 等 2017](https://par.nsf.gov/servlets/purl/10063494) 表 2 第 164 页及公式 3、4。试件为 100×100×50 mm 商品层积毛竹、聚氨酯胶、水平锥形量热、横纹受热。临界热流和点火温度均为模型推导值。 |

花旗松原表热惯量单位是 kJ²/(m⁴·K²·s)，目录换算为 J²/(m⁴·K²·s)，因此 `0.158` 变为 `158000`。竹材论文表 2 的 `235 kW·s¹ᐟ²/m²` 是热响应参数（TRP）；论文公式把它定义为 `sqrt(kρc) × (Tign − T0)`，它不是热惯量 `kρc`。由于原表没有提供可与该拟合逐项配对的初始温度，竹材的 `thermal_inertia_J2_m4_K2_s` 继续为 `null`，不能直接把 235 平方后写入。

同样，南方松在 55.5 kW/m² 行的 114.7 kW/m² 是从点火到试验结束的平均 HRR，不是峰值 HRR；该字段仍为 `null`。这些空值是有证据支持的拒填，不是遗漏。

[ISO 5660-1](https://www.iso.org/standard/57957.html) 是锥形量热的试验方法依据，但标准本身不提供高友村材料参数。因此标准只用于约束后续试验方法，不从中生成材料数值。

可用下列命令校验字段、单位、来源引用和缺失情况：

```bash
conda activate pyfireca-village
pyfireca village validate-materials-v3 \
  examples/village/material-catalog-3.gaoyou-evidence.yml
```

## 后续填报顺序

1. 对每栋建筑或代表性样本，记录树种/竹种、构件位置、厚度、密度、覆层、老化状态与含水率；把照片和测量编号关联到建筑 ID。卫星影像不能替代这些调查。
2. 测量单位地面面积的可参与燃烧干质量，并记录面积换算方式；与屋面外露面积分别存储。材料常数不能替代构造燃料负荷。
3. 针对当地主要木材、竹材及屋面材料，在多个辐射热流和含水率条件下测点火时间、质量损失和 HRR 时间序列；记录重复样本、方向与厚度。[ISO 5660-1](https://www.iso.org/standard/57957.html) 描述锥形量热方法及点火和热释放测量。
4. 逐参数写入原始来源定位及测量/推导方法。保持原始试验曲线独立存档；峰值 HRR、增长/衰减时间等由曲线拟合时，应保存拟合脚本、算法版本和误差。不同外热流的曲线不能无说明地平均。
5. 先验证单材料点火和燃烧，再验证构造及原胞间传播。完成这些工作并建立材料到建筑/原胞的空间映射后，才考虑替换 `synthetic_wood` 场景。

本证据库是 v2 材料目录的**研究数据升级**，不改变 `effective_exposure_ca_v2` 现有求解公式，也不自动把参考数值注入模拟。当前共收录 10 个带逐参数来源的数值。`critical_heat_flux_W_m2` 和 `thermal_inertia_J2_m4_K2_s` 尚未参与当前求解器的点火判定；即使填入真实值，也不会自行改变当前模拟。
