# 岜团村联合清理方案：优化与对照的终端结果（2026-11-11）

在同一冻结模型、四个保留源家族和双屋面尺度组成的8个情景中，两方案的16份原生结果均达到严格灭尽闭合，鼓楼B0073与风雨桥B0120均未引燃。几何优化方案同时降低施工面积、最坏原屋面归属燃尽计数上界和全域燃尽面积，因此在本次同情景比较中严格支配预设几何对照。

| 指标 | 几何优化方案 | 预设几何对照 |
|---|---:|---:|
| 施工面积 C（m²） | 4,775.72 | 9,083.11 |
| 最坏全域燃尽面积 A（m²） | 324.09 | 662.73 |
| 最坏原归属燃尽计数 N（下界，上界） | [2, 5] | [5, 7] |
| 核心未引燃且严格闭合 | 8/8 | 8/8 |

施工面积减少 **4,307.39 m²（47.42%）**，最坏全域燃尽面积减少 **338.63 m²（51.10%）**，最坏N上界由7降至5。这里的最坏值分别对8情景取最大，N与A的最大值不必来自同一情景。

## 地图与完整行动清单

两图采用相同显示范围，仅标注核心与实际火源。金色的四处火源是不同情景的备选位置，每个情景仅点燃其中一处的25 m²片区。橙色为实际清空屋面并集，蓝色为整5 m格植被清理，紫色为共享保留屋面，虚线为计划涉及的建筑标注轮廓；这些轮廓不等于全部物理屋面已消失。GeoJSON采用经度、纬度坐标（EPSG:4326），面积在原投影米制坐标中计算。

### 几何优化方案

![几何优化方案位置图](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.png)

[PDF地图](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.pdf) · [GeoJSON](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.geojson) · [完整屋面ID与面积分摊CSV](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.roof-ids.csv) · [完整植被格ID与坐标CSV](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.grass-ids.csv) · [成本及输入收据](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b0-w15.01.json)

### 预设几何对照

![预设几何对照位置图](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.png)

[PDF地图](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.pdf) · [GeoJSON](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.geojson) · [完整屋面ID与面积分摊CSV](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.roof-ids.csv) · [完整植被格ID与坐标CSV](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.grass-ids.csv) · [成本及输入收据](/Users/tone/Documents/github/PyFireCA/output/pdf/heritage-balanced-20261111/roof-seed-core-source-hull-ring-b10-w20.json)

优化方案涉及19个计划屋面标注ID，其中6个物理屋面完全清空、13个仍有共享保留部分；对照相应为30、18、12个。两方案分别清理78格（1,950 m²）和146格（3,650 m²）植被。CSV中的屋面清理面积按ID排序去重分摊，用于面积核对，不代表独立产权或整栋损毁面积。植被类别1为草地/农田等效燃料，类别4为林木/灌丛等效燃料，均来自准备数据中的RGB推测；CSV保留原类别编号。

## 八情景逐项结果

每表8行均通过核心未引燃、物理状态有效与严格灭尽闭合检查。C为施工面积，N为原始屋面所有者在实际保留物理片区上的燃尽归属下上界，A为全域累计燃尽的物理覆盖面积；核心最高温取两个核心的最大历史有效温度。全部16例非屋面燃尽面积均为0，且引燃面积与燃尽面积在终端相等。

### 几何优化方案

| 源家族 / 屋面尺度 | 实际火源 | C（m²） | N下上界 | A（m²） | 核心最高温（K） | 灭尽时刻（s） |
|---|---|---:|---:|---:|---:|---:|
| B0075-known / 1 m | B0075 | 4,775.72 | [1, 3] | 86.02 | 416.59 | 2002.72 |
| B0075-known / 0.5 m | B0075 | 4,775.72 | [1, 3] | 86.02 | 373.15 | 2927.72 |
| B0071-known / 1 m | B0067 | 4,775.72 | [2, 4] | 324.09 | 327.52 | 2226.72 |
| B0071-known / 0.5 m | B0067 | 4,775.72 | [2, 4] | 324.09 | 327.76 | 2315.72 |
| B0062-known / 1 m | B0062 | 4,775.72 | [2, 5] | 188.24 | 382.03 | 1647.36 |
| B0062-known / 0.5 m | B0062 | 4,775.72 | [2, 5] | 188.24 | 389.83 | 1730.36 |
| B0061-known / 1 m | B0063 | 4,775.72 | [2, 5] | 188.24 | 373.15 | 1984.72 |
| B0061-known / 0.5 m | B0063 | 4,775.72 | [2, 5] | 188.24 | 373.15 | 2152.72 |

### 预设几何对照

| 源家族 / 屋面尺度 | 实际火源 | C（m²） | N下上界 | A（m²） | 核心最高温（K） | 灭尽时刻（s） |
|---|---|---:|---:|---:|---:|---:|
| B0075-known / 1 m | B0075 | 9,083.11 | [2, 3] | 158.19 | 455.30 | 1793.36 |
| B0075-known / 0.5 m | B0075 | 9,083.11 | [2, 3] | 158.19 | 432.82 | 2108.72 |
| B0071-known / 1 m | B0067 | 9,083.11 | [5, 7] | 662.73 | 366.70 | 2754.36 |
| B0071-known / 0.5 m | B0067 | 9,083.11 | [5, 7] | 662.73 | 373.15 | 2863.36 |
| B0062-known / 1 m | B0062 | 9,083.11 | [2, 5] | 188.24 | 382.03 | 1647.36 |
| B0062-known / 0.5 m | B0062 | 9,083.11 | [2, 5] | 188.24 | 389.83 | 1730.36 |
| B0061-known / 1 m | B0063 | 9,083.11 | [2, 5] | 188.24 | 373.15 | 1984.72 |
| B0061-known / 0.5 m | B0063 | 9,083.11 | [2, 5] | 188.24 | 373.15 | 2152.72 |

## 指标与结论范围

- C按实际清空屋面矢量并集加整格植被施工占地计费；每格25 m²。燃料覆盖面积另列，既不将空地当燃料，也不把草地稀疏覆盖比例当作施工占地。
- N使用原始所有者与实际物理清理掩码；仅删除标注ID不会抹去共享保留屋面的火损。它是模型屋面归属计数区间，不能直接称为真实整栋建筑烧毁栋数。A按原始不重叠物理节点面积唯一计数，清理面积另计，不重复计为火灾燃尽面积。
- 历史源家族中的B0071、B0061分别因保留源规则替换为B0067、B0063；两方案每个同情景的实际火源、25 m²起火片区、初始源预算、共同网格与积分成员均相同。四个历史源家族乘以1 m/0.5 m两种支持，不是8个独立随机抽样；此次没有新增独立火源抽样。
- 实际灭尽发生在1,647.36至2,927.72 s。原始`reached_requested_horizon=False`标记全部保留；终端有效性来自绑定实际状态与历史峰温的严格灭尽证书。172,800 s是计算上限，证书闭合未来事件并不代表逐帧模拟了未来温度。
- 核心保护准则是无引燃，而非无热暴露。优化方案鼓楼和桥的最高温分别为416.59 K、389.83 K；对照分别为455.30 K、389.83 K。
- 优化方案在预注册的51个几何参数组合中取得最小C与最小内域容量代理，且通过全部8个情景的核心保护约束；因此，它在这51个模板内也是满足指定约束的最小施工面积方案。在本次两个方案的终端C/N/A比较中，它严格支配对照。另外49个模板尚未运行终端CA，不能用内域容量代理认定其真实N/A被支配；51模板的完整C/N/A前沿仍未建立。完整联合行动空间的最优性也未证明，证据未扩展至其他起火源、风场或现场条件；旧3600 s屋面面积证书不转用于终端联合策略。

## 数据与复核

[16份原生结果逐项审计](/Users/tone/Documents/github/PyFireCA/docs/results/heritage-balanced-outcomes-audit-20261111.json) · [不可变生产配置](/Users/tone/Documents/github/PyFireCA/examples/research/heritage/area-20261110/batuan-balanced-optimized-longcap-comparison-20261111.json) · [51点几何网格结果](/Users/tone/Documents/github/PyFireCA/docs/results/heritage-batuan-balanced-geometry-grid-20261111.json) · [交付文件与SHA清单](/Users/tone/Documents/github/PyFireCA/docs/results/heritage-balanced-delivery-20261111.json)

地图、清单和本说明新增CA次数均为0；原生结果、原始标记和旧几何生成器均未修改。
