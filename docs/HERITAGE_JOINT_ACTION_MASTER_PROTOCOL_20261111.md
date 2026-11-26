# 屋面与整草格联合面积主问题

本轮原型独立放在 `planning/joint_action_prototype.py`，不改变已冻结的屋面优化、屋面失败证书或正在运行的联合终态比较。旧屋面下界只属于屋面行动空间，不能转写为建筑与植被联合下界。

## 行动与成本

联合行动包含全部登记普通建筑行动以及全部 eligible 原始 5m 非屋面格。屋面物理原子只有在全部原 owner 被清理后才计入施工几何面积；植被行动命名为 `G<physical_cell_id>`，每格施工成本为完整 25m²，即使该格只有部分燃料覆盖。任何与屋面相交的父格、未知格、不可燃格或分裂后的非整格不进入这类植被行动。

| 村落 | 屋面行动 | 合法植被整格行动 | 联合行动数 | 正成本屋面共享 AND 辅助量 |
|---|---:|---:|---:|---:|
| 岜团 | 331 | 78,386 | 78,717 | 148 |
| 平岩 | 947 | 212,887 | 213,834 | 556 |

这些数值由原 parent masks、building index、covered area 和存档 immutable component 原 owner 恢复核查，未构造大村网格/辐射图。完整行动范围仍是登记模型中的整建筑与整 5m 非屋面格，不能解释为现场任意宽度或任意形状的清理空间。

共同施工成本由未干预 base layout 计算，所有源和支持尺度共用一个逻辑 action-universe SHA。每个冻结 scenario 的 node group、component、物理父格与几何另有 partition SHA；MIP 必须显式接收 scenario→partition 映射，不能把一个源/尺度的物理网格哈希套给其他源/尺度。

保留源覆盖沿用同一总成本 cap 的屋面投影证明：`roof_C ≤ joint_C ≤ cap`，因此按 roof_C 推得的 retained-source 前缀覆盖所有 joint_C 合法方案。调用 oracle 前仍必须用 joint_C 检查同一个 cap（包括同一显式数值容差）；不能只检查 roof_C。cap 尚未由本联合模型下完整可行方案验证时，只是声明的搜索域上限。

## 独立的联合失败约束

新 schema 为 `heritage-joint-physical-node-removal-cut-1`，旧 whole-building schema 不接受 G 行动，也不复用旧 certificate SHA。

对于已有失败方案 A，一个 D 只有在包含 A、且没有完全清除任何 guarded action group 时，才由该证书证明继续失败。屋面 node 映射为剩余 owner 的 AND；植被 node 映射为对应 G 单例。清理共享标签中的一个 owner 不能当作物理清除。

guard 包含每个实际燃烧 node 的全部可能辐射接收者（以物理几何半径保守覆盖）与接触邻居，包括已经升温但尚未点火的接收者。候选域中其他可清除 node 必须具有严格零累计热输入且仍为环境温度；若它们可能控制全场热步长，也加入 guard。两村原始 eligible 草格在 300K、固定 contact h 下的最小 ambient CFL 约为 6.56823s，大于当前 max_dt=1s；这项数值只能减少当前常规冷草 guard，不能取消一般 CFL 检查。

证明前提是固定物理 partition、固定积分成员、固定 calm/transparent/no-firebrand kernel、同一时钟和 0 时刻单一屋面起火。实现只接受现有 `refined_contact_operator` 与带 readonly frozen node mask 的面积辐射 builder。该 contact builder 在 parent 参数非空时，即使邻接宽度为零也保留 operator，因此全局 CFL 使用的 contact h 不会因删掉最后一条边而消失；此行为另有回归测试。

新证书核对实际 core 点火、record/source/实际时间、保留参数及 mask、节点清除和 owner metadata；并绑定 native case、evaluation、physical-request、逻辑 universe、scenario partition 与积分 mask 指纹。几何 zone/证书生成不需要重建辐射图，但导入旧 native artifact 时仍须单独证明输入等价、核验 manifest，并生成新的联合证书。

## 稀疏 MIP 与资源边界

原先每个 exact no-good 含全行动域会产生约 `plan_count × action_count` 个非零元。原型引入一个共享计数量 `t=Σx`；对已失败精确方案 A 使用 `t−2Σ_A x ≥ 1−|A|`。全域稠密计数等式只出现一次，其余 no-good 与已清理行动数成正比。已有强物理失败约束的方案无需再添重复 exact no-good。

在 64 个无强证书失败、每个方案不超过 64 项行动的示例下，计数等式、总 C cap、屋面 AND 逻辑和这些 no-good 的合计非零元约为岜团 163,000、平岩 436,000，加上实际物理 cut 的稀疏字面量。CSC 数组仅是数 MB 量级；Python 构造、HiGHS presolve/分支和 CA 状态还会增加内存，不能据此保证完整求解时间。prototype 明确限制 sparse matrix 5,000,000 非零元，超限是计算 unknown，不是物理失败。

正成本且未被任何约束要求的远处草变量可由求解器 presolve 置零，这是当前数学松弛的等价简化，不是根据火灾单调性删除物理候选。所有登记行动仍保留在问题与成本清单中。

known-passed 完整方案不从此 master 排除，可以直接将已知上界与 MIP 下界相遇；若限时求解仅返回已知可行方案且 gap 尚未闭合，controller 应停止为待算，不能重复相同 CA。新的 joint driver 负责预算、source cap、逐情景物理评价和上下界闭合；MIP 本身返回的未知计划不能当作保护通过。

## 验收与接入顺序

小模型对 1m、0.5m 两种支持以及开启真实 contact 的 1m 支持，分别完整枚举屋面与近/远草格的三行动 powerset。每个被新 cut 排除的方案都核对实际失败、core outcomes、实际结束时间以及全部保留节点 native state 数组逐位相同。另检查共享 owner、warm-unlit grass、低 CFL 冷草、可移除源与实际备用源、跨 scenario 指纹、source-empty unknown、MIP 对穷举，以及 1024 草变量/64 no-good 的线性稀疏增长。

生产接入应先冻结共同 base 成本与每个 scenario 的 source-prefix/refinement/积分 mask，再做容量预检；16GB 机器只保留一个 active engine。完整可行上界必须由全部声明源/支持情景的终态闭合检查获得。以更长计算 cap 直接求解并在首次 core 受火时早停可以节省失败评价；通过情景仍需严格熄灭闭合。

旧 3600s 首次 core 失败早停档案可作为后续 0CA 导入候选：只有实际失败时刻严格早于旧、新 end，且源、参数、采样/步长、物理 partition 和 kernel 都等价，才能证明两种 end 在该失败前缀没有参与步长裁切。旧 3600s 完整终点可能含末步裁切，不能直接换成更长 horizon 的原生状态。任何导入均应独立收据与新 schema，不能改旧档案或旧 SHA。

上下界闭合只证明声明源、支持、数值参数及登记联合行动空间中的最小施工面积锚。C/N/A 的全局 Pareto 前沿、任意起火位置与现实安全仍是不同任务；最终继续保留完整评价集合中的非支配权衡方案。
