# 平岩与岜团防火线实验的代码与复核

本页对应《两座侗寨木构遗产与聚落的防火线设计》的中文审阅稿。论文目录提供 15 幅原图、可编辑的 Word 稿及其 Markdown 版本，以及正文和补充材料的全部表格。[论文正文](../paper/manuscript.zh.md) · [Word 稿](../paper/两座侗寨防火线设计_中文大纲与方法结果全文.docx)

## 复核论文结果

从仓库根目录运行。第一个命令只用 Python 标准库。第二个命令可另存八种布局的终态引燃场数。

```sh
python3 scripts/verify_heritage_paper.py
python3 scripts/verify_heritage_paper.py --output verified-totals.json
```

程序读取归档中的 7,416 条主试验原始终态记录及其证书，按村落、起火屋面与风况配成 927 组。它检查每组均有八种布局，核对起火足迹、初始能量和熄灭条件，重算正文表 1 的分层引燃场数、表 2 的烧尽与总损失及表 3 的配对差、抽样标准误和增减场数。另核对 Word 中的 15 幅插图、16 个表块和 5 个公式，以及数据包中冻结源码和 25 张实验表的校验码。[便携复核程序](../scripts/verify_heritage_paper.py) · [实验归档](../paper/data/restored-experiments.zip)

## 实验与文件

| 实验或输入 | 计算与制图 | 结果 |
|---|---|---|
| 建筑及地表元胞 | [影像分类与地形](../scripts/prepare_batuan_simulation.py)、[屋面几何](../scripts/prepare_heritage_geometry.py)、[田野构造归类](../scripts/apply_heritage_construction_types.py) | [构造规则、元胞统计、照片](../paper/materials) |
| 局部选址，24 场双尺度对照 | [准备布局](../scripts/prepare_heritage_position_controls_20261103.py)、[运行固定方案](../scripts/run_heritage_fixed_comparison.py)、[按两种尺度汇总](../scripts/summarize_heritage_followup_20261103.py) | [4 张表](../paper/data/results/local-position) |
| 一小时先导试验与传播半径 | [准备](../scripts/prepare_heritage_wind_strategies.py)、[运行](../scripts/run_heritage_wind_strategies.py)、[汇总](../scripts/summarize_heritage_wind_strategies.py) | [7 张表](../paper/data/results/pilot-hour) |
| 先导试验追踪至熄灭 | [续算](../scripts/run_heritage_wind_extinction.py)、[配对汇总](../scripts/summarize_heritage_wind_extinction.py) | [2 张表](../paper/data/results/pilot-terminal) |
| 五种标准布局，4,635 场 | [冻结留出面板](../scripts/prepare_heritage_wind_validation.py)、[运行](../scripts/run_heritage_wind_validation.py)、[分层及加权汇总](../scripts/summarize_heritage_wind_validation.py) | [4 张表](../paper/data/results/standard) |
| 精简布局，新增 927 场 | [运行](../scripts/run_heritage_geometry_validation.py)、[继续及复核](../scripts/resume_heritage_geometry_validation.py)、[配对汇总](../scripts/summarize_heritage_geometry_validation.py) | [4 张表](../paper/data/results/compact) |
| 中等预算的两条路线，新增 1,854 场 | [选线](../scripts/prepare_heritage_midbudget_routes.py)、[运行](../scripts/run_heritage_midbudget_routes.py)、[汇总](../scripts/summarize_heritage_midbudget_routes.py)、[制图](../scripts/plot_heritage_midbudget_routes.py) | [4 张表](../paper/data/results/midbudget) |
| 传热与终态判定 | [热求解器](../src/pyfireca/village/solver_v3.py)、[风致辐射](../scripts/heritage_wind_radiation.py)、[C++ 内核](../scripts/heritage_wind_terminal_kernel.cpp)、[熄灭证书](../src/pyfireca/village/extinction_v3.py) | [完整数值复核记录](../paper/data/verification.json) |

全域模拟通过配准影像和邻域规则给地表分类，利用人工勾绘的轮廓确定建筑位置。平岩普通民居归为瓦面木构，岜团普通民居归为含木构件的混合型。鼓楼、风雨桥分别归为瓦覆木构、瓦覆木廊。田野归类适用于 1,283 个屋面标签的 28,716 个基准建筑元胞，分类标签在原模拟完成后补入清单。模拟统一使用代码 5，引燃阈值为 520 K，有效燃烧面积上的峰值功率为 300 kW/m²，干燃料负荷为 18 kg/m²，干基含水率为 0.10。[热参数表](../paper/tables/补充表S1b_等效热参数.csv) · [构造统计](../paper/tables/补充表S7_田野构造及元胞数.csv)

## 作者工作区中的完整数值复核

项目代码使用 Python 3.11 或更高版本。运行原生热传播内核需要可在命令行调用的 C++17 编译器。材料清单、留出火情、原始屋面矢量和网格由登记文件绑定。仓库中的结果归档收录逐场结果、证书、材料数组与汇总表。完整原坐标底图、原始几何登记及双尺度节点快照保存在原始研究工作区。原生求解使用原登记的目录及物理输入。[登记资料索引](../paper/data/input-index.json)

```sh
python3 -m pip install -e '.[dev,gis,village,village-vision,planning]' matplotlib
python3 scripts/audit_heritage_original_records.py --project /path/to/PyFireCA --output native-audit.json
```

这个命令用原汇总器重建六组试验的 25 张表，比较字节校验码，并从原几何重新核验两村建筑元胞的赋值。若需核对调查原片，可增加 `--photo-root /path/to/drone_photos`。仓库中的八张出版用照片保留原分辨率、颜色和显示方向，已移除定位和设备元数据。原片和出版照片的两组校验码见照片索引。从八张照片重新生成补充图 S8 的命令如下。[照片及校验码](../paper/materials/photos) · [原生复核程序](../scripts/audit_heritage_original_records.py)

```sh
python3 scripts/render_heritage_field_plate.py --font "/path/to/Arial Bold.ttf" --output figure-S8.jpeg
```

论文引用的基础求解器提交为 `bd37601b0b2920485e5739ba407f9d4dc27caff9`。本次归档另含中等预算两路线、田野构造处理与出版结果复核程序。PyFireCA 原项目的记录保留在软件引用文件中。[基础求解器提交](https://github.com/felix018jiang/PyFireCA-village/commit/bd37601b0b2920485e5739ba407f9d4dc27caff9) · [软件引用](../CITATION.cff)
