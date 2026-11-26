# 90分钟联合保护算法机制图（v2）

- 位图：`batuan-protection90-method-v2.png`，1760×792，220 dpi。
- 可编辑矢量：`batuan-protection90-method-v2.svg`，文本保持为SVG文本。
- 生成脚本：`scripts/draw_batuan_protection_method.py`，Matplotlib 3.10；本机可用 `/opt/anaconda3/bin/python scripts/draw_batuan_protection_method.py` 重建。

建议图注：**Protection-constrained firebreak design for timber heritage villages.** Spatial evidence defines the material cells and protected structures. Every corridor proposal is re-evaluated by the thermal, finite-fuel CA. A feasibility-first objective requires zero house and bridge ignitions through the declared 90-minute horizon across the training scenarios, and then minimizes the union of actual eligible clearance. Position, orientation, width, endpoints, bends, and multiple-path combinations are explored by beam search and local refinement. Selected layouts are frozen before new-ignition and ignition-temperature-rise sensitivity checks. Dashed modules denote subsequent model validation and transferable rule extraction; any physical-model update requires a new optimization and validation cycle.

说明：H为引燃民居数，B为引燃风雨桥数，A_clear为有效清理并集面积。房屋/地类/路径小图是机制示意，不是已完成的分类图或最优施工方案。实线表示程序模块已实现，不表示当前长算例已经完成；具体数值以正式报告的completed状态和独立核验为准。现有岜团村评价器为静风、无飞火、代理分区和合成燃料参数，图中未将未实现的动态风、飞火与跨村落规则作为既有能力。新的小幅扰动只用于独立敏感性，不反馈本轮选线。

本图对应自由几何、保护优先和面积细化阶段；旧 `batuan-firebreak-method-v1.png` 对应24候选枚举历史版本，保留但不代表当前算法。
