# 全候选搜索与平岩迁移：已完成断点

> **后续减拆续轮已完成（2026-10-30）：**平岩原17对两栋均失败，当前数量界提高为3—5。三/四栋在预定分支预算处停止，没有新增CA或更少拆除可行解；原五栋保护结果保持。新增29次搜索及1次原生恢复验收通过，见[最新完整结果与停止断点](HERITAGE_PINGYAN_COUNT_EXTENSION_20261030.md)。下文2—5和17对未决等数值保留此前冻结阶段的历史口径。


最终更新：2026-10-30。本轮达到预算与验收，状态为`complete`。生产与顺序执行器已正常退出；监督自动化`automation-3`已删除。**没有等待续跑的本轮任务，不执行历史resume入口追加预算。**

## 当前解、保护与界限

| 村落 | 可行拆除清单 | 名义面积m² | 保护验证 | 最优性与未决 |
|---|---|---:|---|---|
| 岜团 | B0061＋B0071＋B0076 | 451.240456 | 4已知设计家族×双尺度8项全通过；2次原生/剪枝验证通过 | 全331候选最少数量3已证明；同数量面积177.814102—451.240456 m²仍未决 |
| 平岩 | B0119＋B0165＋B0167＋B0177＋B0190 | 995.481072 | 8设计项＋8冻结后新落点项全通过，鼓楼B0166及桥B0035/B0036均保护；2次原生/剪枝验证通过 | 全947候选数量界2—5；17个两栋、三/四及面积更小五栋未决 |

平岩新确认共16份（基准8＋五栋8）均完整观察1200 s。基准新鼓楼源B0164在1 m下1169.36 s引燃，0.5 m未引燃，双尺度并集保留失败；五栋同源同多边形双尺度均保护。新桥源基准本身也通过，不据此新增桥侧由失败转成功的结论。B0036在15 m内没有普通建筑，其新B0039源单列为60 m压力族。

五栋实际清空985.607836 m²、共享保留9.873236 m²；其名义面积不含已按用户授权归遗产的重叠部分。实际成本、币种、住户、产权/实施资格和其他文化活动空间资料仍待补。五栋来自预先声明种子，尚未证明每项不可替代或数量最小。

## 冻结条件与已消耗预算

材料、25 m²屋面点火、1200 s、无风、完整5 m背景与声明局部1 m/0.5 m、0.5 m求积保持不变。岜团48生产＋2审计=50次CA；平岩48生产＋2审计＋16确认=66次CA，共116次。收尾汇总、地图/清单/保护表与本断点为0次新增CA。39项相关测试先前已通过，本次没有重跑无关测试。

所有原始标注、历史失效、物理结果、配置与源码快照保留。平岩0结果阶段父图容量恢复只使用原声明资源额度，旧目录与收据保留。后续休眠导致自然日耗时的说明另存历史诊断，不改变物理或预算。

## 完成产物

- [主结果与逐核心证据](HERITAGE_FULL_SEARCH_AND_PINGYAN_20261029.md)、[两村机器汇总](results/heritage-full-villages-20261029.json)、[本轮交付验收](results/heritage-full-villages-delivery-20261030.json)。
- [平岩地图](results/heritage-full-pingyan-20261029/strategy/strategy-map.png)、[PDF](results/heritage-full-pingyan-20261029/strategy/strategy-map.pdf)、[拆除清单](results/heritage-full-pingyan-20261029/strategy/demolition-list.csv)、[代价账本](results/heritage-full-pingyan-20261029/strategy/cost-summary.json)、[新确认48条逐核心记录](results/heritage-full-pingyan-20261029/strategy/confirmation-core-outcomes.csv)、[呈现收据](results/heritage-full-pingyan-20261029/presentation-receipt.json)。
- [岜团地图](results/heritage-full-batuan-20261029/strategy/strategy-map.png)及[拆除清单](results/heritage-full-batuan-20261029/strategy/demolition-list.csv)。
- [平岩17对两栋未决](results/heritage-pingyan-pair-frontier-20261029.json)、[五栋设计可行性](results/heritage-pingyan-five-feasibility-20261029.json)、[三栋失败反例](results/heritage-pingyan-triple-counterexample-20261029.json)。

## 保存位置与后续边界

原生根为`runs/research/batuan-full-bounded-20261029`、`runs/research/pingyan-full-bounded-20261029`和`runs/research/pingyan-heldout-20261029`；各自保存experiment、source-snapshot、evaluations及审计/结果。顺序执行记录`results/heritage-pingyan-sequence-20261029.json`已收尾为complete，总日志`runs/research/pingyan-sequence-20261029.log`和监督JSONL保留历史，不需重启。

后续若获得新预算，可从17对两栋等未决范围继续收紧界限，或按论文需要补充现场成本/文化空间与环境证据；当前没有自动启动这些工作。新源确认不参与本轮重选，不能改种子、替换失败或称五栋为ideal全局解。村外联合试验仍需单独统一协议；不把旧不同模型/时长结果当本轮证据。没有commit/push或外部消息。
