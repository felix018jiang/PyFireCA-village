# 建筑低处可燃受体：基础模型的接触高度分离

## 证据与采用范围

[NIST：WUI火灾传播](https://www.nist.gov/el/fire/resources/hazard-mitigation-methodology-hmm/how-fire-spreads-wui)说明植被、木堆和建筑火焰可直接接触并引燃结构可燃部件。该来源支持保留直接接触机制，**不提供0.2 m高度或本项目接触换热参数的标定值**。同一来源指出飞火是重要的建筑起火途径；当前简单模拟器未实现飞火，不能据此预测完整建筑损失风险。

[Ritter等（2023）：垂直与水平冠层燃料连续性](https://research.fs.usda.gov/treesearch/66560)支持地表到树冠传播受燃料结构和连通性控制。因此本轮不把树木受体统一移到地面，不声称模拟了冠火转化。

## 修改

原接触可达性使用与辐射共用的一个受体代表高度。现在允许接触配置按材质ID另设接触代表点高度；省略时完全沿用旧逻辑。辐射暴露边、辐射受体高度、燃料质量、水分、点火阈值、燃烧曲线均不改变。

对相邻源i和目标j，接触可达条件仍为：

`ground_i <= ground_j + contact_height_j <= ground_i + source_height_i`。

可达边采用既有有效接触换热：`P_ij = fraction_ij * covered_area_j * h * max(T_gas - T_j, 0)`；接触比例随源HRR活动降低，同一受体的总接触比例不超过1，每个源分配的热量不超过其非辐射释放功率。没有额外创造热量，没有绕过水分蒸发或点火温度。

演示配置 `examples/village/vegetation_v3/flame-contact-low-building.synthetic.yml` 在标准材质ID5（建筑等效燃料）设置0.2 m低处可燃受体，其余材质保持原值。0.2 m是明确的合成场景假设，不能从俯视图判断建筑是否具有对应可燃部件。低点接触仍加热单一等效原胞，并非真实低处构件与整栋建筑之间的内部传播模拟。接触面积仍使用原有效面积，尚未标定低处可燃部件面积。

配置新增可选 `receiver_height_overrides_m: [[5, 0.2]]`。不接受重复ID、布尔值、负数、非有限高度或未知配置字段。旧配置保持兼容。多部件模式有自身高度配置，显式拒绝此单状态覆盖项，避免静默忽略。

## 复现与对照

```sh
.venv/bin/python examples/village/vegetation_v3/run_core_map.py \
  runs/gaoyou-vegetation-contact-crop-20261004-b/contact-dt0.25-case \
  --output runs/core-low-building-demo \
  --flame-contact-config examples/village/vegetation_v3/flame-contact-low-building.synthetic.yml
.venv/bin/python examples/village/vegetation_v3/audit_contact_height.py \
  runs/village-core-20261005-b \
  --output runs/contact-height-new.json
```

正式对照输出 `runs/village-core-low-building-20261005-b/`，a为开发输出，a/b结果一致。原 `runs/village-core-20261005-b/` 保留。34×34 m裁剪、20分钟：

| 起火材质 | 原点燃格数 | 低处受体点燃格数 | 低处受体燃尽格数 |
|---|---:|---:|---:|
| 草地 | 3 | 166 | 76 |
| 灌木 | 8 | 164 | 77 |
| 树木 | 193 | 209 | 86 |
| 建筑 | 164 | 165 | 75 |

低处受体四个案例均点燃89个建筑材质格，这些格对应裁剪中的同一个建筑标注，不能解读为89栋建筑。传播增加源自假设改变，不是新的现实验证。

敏感性检查使用0.1/0.2/0.5 m与0.25/0.125 s时间步。草地在0.1/0.2 m时点燃166格，0.5 m时仍为3格；灌木分别164/164/163格。时间步减半保持各组点燃总格数一致。硬高度门槛仍是点受体简化的局限，不能仅根据动画挑选高度。能量预算闭合并不证明参数准确。

正式网页演示为8851，原基线8850保留。页面显示接触高度假设，重选起火位置后继续保存配置。全仓库948项测试通过（5条既有Rasterio警告）；验证原物理数组不变、树木高度限制、地形阻断、配置存读及能量预算。

下一步正式默认材质选择依赖建筑外墙/屋顶实际材质信息；未获信息前只使用带标注的演示假设，不把所有建筑认定为低处可燃。


2026-10-05用户补充：建筑真实材质将在实地调查时收集；开发不再等待此信息。上述演示假设继续明确标注，后续替换。8851现已延长至60分钟以检查燃尽过程，并提供基本燃料质量统计。
