# 两座侗寨木构遗产与聚落的防火线设计

本文用 PyFireCA V3 热传播元胞自动机评估平岩和岜团的防火线。103 处普通屋面分别配对九种稳态风况，每组火情比较八种布局，共有 927 组、7,416 场终态模拟。无清理和精简布局分别有 556 组和 352 组发生指定遗产引燃。多点宽围护、均匀收窄和沿不燃地表改线在这批情景中均为零组。

[中文 Word 稿](paper/两座侗寨防火线设计_中文大纲与方法结果全文.docx) · [在线阅读全文及图表](paper/manuscript.zh.md) · [实验代码与复核说明](docs/HERITAGE_STUDY_CODE.md)

## 论文资料

| 内容 | 仓库位置 |
|---|---|
| 7 幅正文图、8 幅补充图 | [论文图件](paper/figures) |
| 正文表 1–3、补充表 S1–S7，共 16 个 CSV 表块 | [论文表格](paper/tables) |
| 25 张逐场与汇总实验表，按六组试验整理 | [原始结果表](paper/data/results) |
| 两村构造规则、元胞数量、S8 的 8 张田野照片 | [调查资料与照片](paper/materials) |
| 逐场结果、熄灭证书、材料数组和冻结时的源码 | [完整结果归档](paper/data/restored-experiments.zip) |
| 全项目统计复核记录、原空间输入与登记摘要 | [复核记录](paper/data/verification.json) · [输入索引](paper/data/input-index.json) |
| 通用 PyFireCA 模拟器 | [框架与安装](README.zh-CN.md) · [运行模拟器](docs/RUNNING_SIMULATOR.md) |

## 从干净检出复核

在仓库根目录运行下列命令。只需 Python 3.11 或更高版本的标准库即可核验主实验、论文图表和归档文件。

```sh
python3 scripts/verify_heritage_paper.py
```

程序逐场检查终态记录和熄灭证书，重建正文三张表的主要数值，并核对冻结的求解器与实验脚本。完整热传播重算所用的原始空间资料与运行方式见复核说明。建筑构造来自田野调查的类型归纳，原版求解器对所有屋面采用同一等效燃料（代码 5）。[方法与输入说明](docs/HERITAGE_STUDY_CODE.md)

## 本地研究分支的既有实验与恢复记录

- [岜团村多火源、风况与材质参数下的90分钟保护约束优化](docs/BATUAN_MULTISCENARIO_SEARCH.md)

- [岜团村折线与多路径防火线迭代优化](docs/BATUAN_ADAPTIVE_FIREBREAK_SEARCH.md)

- [岜团村统一评价接口与防火线效果试验](docs/BATUAN_INTERVENTION_EVALUATION.md)

- [传统村落元胞材质生成与防火线优化研究开发计划](docs/VILLAGE_FIRE_RESEARCH_PLAN.md)
- [版本管理与恢复](docs/VERSIONING_AND_RECOVERY.md)
- [运行岜团村交互模拟器](docs/VILLAGE_INTERACTIVE.md)

岜团村90分钟民居/风雨桥联合保护、三类线形比较及独立易引燃敏感性：[运行与恢复说明](docs/BATUAN_PROTECTION90_SEARCH.md)；[论文主线和配置准则路线](docs/BATUAN_PAPER_RESEARCH_MAINLINE.md)。
