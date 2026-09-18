# PyFireCA

**面向林火蔓延模拟的模块化、经过验证、GIS 就绪的 Python 元胞自动机框架。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](#安装)
[![CI](https://github.com/hujinghaoabcd/PyFireCA/actions/workflows/ci.yml/badge.svg)](https://github.com/hujinghaoabcd/PyFireCA/actions/workflows/ci.yml)
[![Status](https://img.shields.io/badge/status-alpha-orange)](#项目状态)

[English](README.md) · [运行模拟器](docs/RUNNING_SIMULATOR.md) · [总体设计](docs/DESIGN.md) · [验证方案](docs/VALIDATION.md) · [当前状态](docs/STATUS.md) · [交接文档](docs/HANDOFF.md)

## 为什么做 PyFireCA？

许多林火 CA 实现会把火行为公式、栅格几何、传播逻辑、GIS 输入输出和实验代码耦合在一起。PyFireCA 将这些职责拆开，使火行为模型、传播语义、空间数据契约和用户工作流可以分别验证、维护和扩展。

当前优先级非常明确：**先把一个简单、完整、现代化、可复现的林火模拟器基线做扎实；新的 PyFireCA 自研 CA 方法先记录，不混入默认实现。**

## 当前基线能力

PyFireCA 已经具备一条端到端静态栅格工作流：

```text
YAML 配置 + 对齐 GeoTIFF + 点火事件
                ↓
             输入验证
                ↓
        已审计标准燃料模型
                ↓
    Albini-adjusted Rothermel
                ↓
 Behave/Catchpole 方向性表面火传播
                ↓
       物理最早到达时间传播
                ↓
 arrival / state / burned footprint
                ↓
   配置、环境、哈希与运行元数据
```

当前已经实现：

- 经固定 Behave 参考验证的 Rothermel 火行为链；
- 风、坡度、动态草本 curing 和可选 wind-speed limit；
- Behave/Catchpole 椭圆及 `FromIgnitionPoint` 非主传播方向 ROS；
- 已审计 Anderson **FM1–FM13** 与 Scott–Burgan **GR1 (101)**；
- 静态空间异质栅格环境；
- 单点、多点以及延迟点火；
- Moore-8 baseline 下的物理最早到达时间传播；
- arrival/state/burned-mask GeoTIFF；
- WGS84 burned-footprint GeoJSON；
- resolved config、输入 SHA-256、燃料 provenance、运行环境、metrics 和 log；
- Python API 与 `pyfireca validate/run` CLI；
- Python 3.11–3.13、GIS、回归与 Behave reference CI。

## 安装

运行文件型 GIS 模拟器：

```bash
git clone https://github.com/hujinghaoabcd/PyFireCA.git
cd PyFireCA
python -m pip install -e ".[gis]"
```

开发环境：

```bash
python -m pip install -e ".[dev,gis]"
```

## 快速开始

以 [`examples/static_run.yml`](examples/static_run.yml) 为配置模板，将路径替换为十个已经对齐的 GeoTIFF，然后先验证：

```bash
pyfireca validate examples/static_run.yml
```

通过后运行：

```bash
pyfireca run examples/static_run.yml
```

输出目录：

```text
runs/static-example/
├── config.resolved.yml
├── metadata.json
├── environment.json
├── metrics.json
├── log.txt
└── outputs/
    ├── arrival_time.tif
    ├── state.tif
    ├── burned_mask.tif
    └── perimeter.geojson
```

## 村落二维元胞自动机（开发中）

仓库现已包含第一条可运行的村落二次开发路径：二维逐格材质、有限燃料、有效暴露引燃、建筑轮廓统计和浏览器动态回放。当前模型与示例参数均处于研究原型/合成演示阶段。

安装村落输入依赖后运行自包含案例：

```bash
conda env create -f environment.yml
conda activate pyfireca-village
pyfireca village prepare examples/village/two_buildings/case.yml --output prepared/two-buildings
pyfireca village validate prepared/two-buildings --scenario examples/village/two_buildings/scenario.yml
pyfireca village run prepared/two-buildings --scenario examples/village/two_buildings/scenario.yml --output runs/two-buildings
pyfireca village view runs/two-buildings
```

如果当前只有真实卫星 GeoTIFF，可先生成一套明确标记为占位数据的整村试验输入：

```bash
pyfireca village bootstrap /path/to/village.tif \
  --output work/village-bootstrap \
  --cell-size-m 10 \
  --end-time-s 120 \
  --sample-interval-s 5
pyfireca village prepare work/village-bootstrap/case.synthetic.yml \
  --output work/village-prepared
pyfireca village validate work/village-prepared \
  --scenario work/village-bootstrap/scenario.synthetic.yml
pyfireca village run work/village-prepared \
  --scenario work/village-bootstrap/scenario.synthetic.yml \
  --output runs/village-trial
pyfireca village view runs/village-trial
```

如果建筑标注是与影像同尺寸的 LabelMe JSON，可在生成试验案例时直接加入：

```bash
pyfireca village bootstrap /path/to/village.tif \
  --labelme-buildings /path/to/buildings.json \
  --output work/village-labelme \
  --cell-size-m 2
```

目前支持 LabelMe 的 `building` 标签、`polygon` 和 `rectangle` 图形。程序使用 GeoTIFF
的 affine 把像素轮廓转为米制坐标，将建筑覆盖元胞设为合成木质材料，并保证自动生成的
直接点火元胞位于中心建筑轮廓内。中心建筑定义为“距全部建筑质心平均位置最近的建筑”，
再选择该建筑覆盖面积最大的元胞作为点火格。密集标注导致一个元胞与多栋建筑相交时，prepare
会在质量报告中写入 `MULTI_BUILDING_CELLS` 警告，同时保留所有建筑对应关系。

`bootstrap` 要求 GeoTIFF 至少包含三个 `uint8` RGB 波段，并带有以米为单位的投影 CRS。
没有提供 LabelMe 时，它只把卫星影像视为观测数据；提供 LabelMe 时，卫星影像和建筑
几何为观测输入。逐格材料类别、燃料参数、点火和天气仍是 `synthetic` 占位内容，只用于
验证尺度、性能和完整工作流，不能用于风险结论。
正式 GIS 输入也已支持 WGS84 Polygon/MultiPolygon 建筑 GeoJSON，以及与计算网格严格
同 CRS、同 shape、同 affine 的整数分类材质 GeoTIFF 和代码映射 YAML。

详细需求、科学边界和任务顺序见 [`PROJECT_PLAN.md`](PROJECT_PLAN.md)，实际开发进度见 [`docs/VILLAGE_PROGRESS.md`](docs/VILLAGE_PROGRESS.md)。

v2 材料证据库可以独立校验。校验只检查 schema、单位、来源引用和缺失值，不会把
`null` 自动替换为零：

```bash
pyfireca village validate-materials-v2 /path/to/material-catalog-2.yaml
```

`effective_exposure_ca_v2` 的首个数值内核已经包含表面热平衡、水分升温和蒸发、温度
引燃、受燃料总能量约束的增长—稳定—衰减曲线，以及跨元胞辐射输入。当前证据库是
`evidence_seed_not_calibrated`，需要补齐本地材料和建筑构造参数后才能接入高友村正式场景。

仓库还提供明确标记为合成数值测试的两元胞 v2 案例。运行配置把材料参数、建筑构造
参数、传热核和场景参数分开；稳定燃烧时长由逐格燃料总能量自动反算，避免燃烧曲线
释放的能量超过燃料预算。下面的案例使用几何生成的等效点火焰辐射核：

```bash
conda activate pyfireca-village
pyfireca village validate-v2 \
  examples/village/v2_two_cells/case.point-source.synthetic.yml
pyfireca village run-v2 \
  examples/village/v2_two_cells/case.point-source.synthetic.yml \
  --output runs/v2-point-source-reference
```

输出包含逐时刻状态、温度、剩余水分、剩余燃料、HRR，以及每格的点火原因和时间。
该案例中的数值只用于验证算法和文件流程，不能作为木材、竹材或茅草的物性参数。

点火焰核使用以下固定几何辐射份额：

\[
w_{ij}=\frac{A_{r,j}}{4\pi(R_{ij}^2+r_0^2)}
\exp(-\kappa R_{ij})\prod_{c\in L_{ij}}\tau_c
\]

其中火焰有效位置可按风向和标定的倾斜系数移动；路径中的原胞透射率 `tau` 逐格相乘。
程序不对权重重新归一化，并拒绝单个火源向外分配超过 100% 辐射功率的配置。这个核仍是
降阶模型：火焰高度、近场正则尺度、透射率和风致倾斜系数都需要单独标定。

v2 也可以直接使用 `village prepare` 生成的村落空间数据。场景文件按 prepared manifest
中的材料 ID 提供完整的 v2 材料与构造参数；建筑遮挡格来自 `building_overlap.csv`，因此
可追溯到准备阶段的建筑标注几何：

```bash
pyfireca village validate-prepared-v2 \
  runs/gaoyou-labelme-central-300s/prepared \
  --scenario examples/village/gaoyou_v2_scenario.synthetic.yml

pyfireca village run-prepared-v2 \
  runs/gaoyou-labelme-central-300s/prepared \
  --scenario examples/village/gaoyou_v2_scenario.synthetic.yml \
  --output runs/gaoyou-v2-building-physics-500min

pyfireca village view runs/gaoyou-v2-building-physics-500min --port 8794
```

v2 浏览器回放可以在火灾状态、等效表面温度和原胞 HRR 之间切换，并在元胞查询面板
显示对应数值。场景可使用 `end_time_min` 和 `sample_interval_min` 配置面向用户的分钟单位；
求解器内部仍转换成秒以保持 SI 公式一致。示例仍使用合成系数，只用于验证高友村尺度
的数据和计算链路。当前示例总时长为 500 分钟，内部时间步为 1 秒，浏览器回放约每
8.33 分钟保存一帧，共 61 帧；降低回放采样频率不会改变求解器的逐秒物理计算。

高友村 v2 合成场景只把观测建筑覆盖格作为可燃原胞，未分类背景保持不燃。默认图层仍
使用原来的不燃、未燃、受热、燃烧、燃尽状态；房屋状态通过轮廓颜色和查询面板显示，
不会用面填充遮住原胞状态。

输入单位、NoData 语义、点火配置和输出说明见 [`docs/RUNNING_SIMULATOR.md`](docs/RUNNING_SIMULATOR.md)。

## 科学架构

```text
GIS / EnvironmentalData
          ↓
      LandscapeInput
          ↓
   每个格点 Rothermel inputs
          ↓
     FireBehaviorModel
          ↓
     方向性表面火 ROS
          ↓
 edge travel time = distance / ROS
          ↓
     earliest arrival
          ↓
   FireState / GIS outputs
```

项目仍保留原始同步 CA reference path，用于验证 CA 架构和离散规则；它不会被偷偷赋予一个物理 `dt`，也不会替代当前经过验证的物理 arrival baseline。

详细设计见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 输入数据契约

第一版文件工作流要求所有栅格满足：north-up、方形像元、米制 cell size、完全一致的 shape/CRS/affine alignment。

十个输入层及单位：

```text
fuel model                   整数代码
1-h dead moisture            fraction
10-h dead moisture           fraction
100-h dead moisture          fraction
live herbaceous moisture     fraction
live woody moisture          fraction
midflame wind speed          m/s
meteorological wind-from     degrees
slope                        degrees
aspect                       degrees
```

PyFireCA 不会静默把百分比湿度、百分比坡度、10 m 风、弧度或错位栅格自动转换成“看起来能跑”的输入。

## 验证

科学验证是项目一级能力。当前固定参考包括 USFS Fire Lab Behave 的基础 Rothermel ROS、风坡组合、GR1 动态 curing 以及 off-axis directional spread。外部 reference 保存固定上游 revision 和证据等级。

见 [`docs/VALIDATION.md`](docs/VALIDATION.md) 与 [`docs/ROTHERMEL_REFERENCE.md`](docs/ROTHERMEL_REFERENCE.md)。

## 论文创新线

格网方向偏差、扩展/动态邻域、异质介质 interface coupling 等潜在论文方向已经单独保存到 [`docs/FUTURE_RESEARCH.md`](docs/FUTURE_RESEARCH.md)，**当前只记录，不继续实现**。

当前开发优先级见 [`docs/SIMULATOR_ROADMAP.md`](docs/SIMULATOR_ROADMAP.md)：先完成并冻结简单 simulator baseline，再回来开展自己的 CA 创新。

## 开发阶段文档

以下文档持续与代码同步：

- [`docs/DESIGN.md`](docs/DESIGN.md)：架构与设计决策；
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)：开发路线；
- [`docs/STATUS.md`](docs/STATUS.md)：当前仓库真实状态；
- [`docs/HANDOFF.md`](docs/HANDOFF.md)：详细开发交接；
- [`docs/SESSION_LOG.md`](docs/SESSION_LOG.md)：阶段开发记录；
- [`docs/VALIDATION.md`](docs/VALIDATION.md)：科学与数值验证；
- [`docs/FUTURE_RESEARCH.md`](docs/FUTURE_RESEARCH.md)：暂缓实施的论文创新线。

## 引用

软件引用元数据见 [`CITATION.cff`](CITATION.cff)。

## 项目状态

PyFireCA 当前为 **Alpha research software**。静态 baseline simulator 已能够从 YAML + GeoTIFF 完成端到端模拟；当前剩余工作集中在文档、release-quality 集成检查和少量 baseline polish，然后冻结第一版简单模拟器。

动态天气/WRF、crown fire、spotting、suppression、Monte Carlo、FBP、GPU backend 以及 PyFireCA 自研新 CA 方法都不属于当前 baseline release 的阻塞项。
