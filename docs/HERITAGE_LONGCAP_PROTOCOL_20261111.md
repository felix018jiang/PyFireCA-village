# 完整火灾长时限验证：2026-11-11

岜团首个 `roof-seed / B0075-known-h1` 原生结果已到 21600 s，但仍有
7460.952788473564 m² 活动燃烧。这个结果保留为终端 unknown；原运行、配置、
预算及已完成 manifest 不修改，也不把两个核心在已观察六小时内未点燃当作整场保护成功。

本轮先使用现有冻结求解器从零运行更长的时间协议，不为继续实验改动传播物理。

- 新配置：`examples/research/heritage/area-20261110/batuan-joint-longcap-comparison-20261111.json`
- 新冻结 producer：`runs/research/heritage-joint-longcap-producer-20261111/`
- 准备收据：`docs/results/heritage-joint-longcap-preparation-20261111.json`
- 可复现准备器：`scripts/prepare_heritage_joint_longcap.py`

新 cap 为 172800 s（48 个物理小时），显式 wall 预算同为 172800 s。五组行动、
四个保留源家族、1/0.5 m 两种支持、原参考 3600 s 检查点、q、dt、候选全集、
固定细分集合、保留源覆盖和全部物理参数不变。169 个 Python 源文件逐字节等于原
冻结 producer。新 problem 的终端时间协议绑定新 cap；此前已耗 2 个槽和
533.3987848749966 s wall 的旧预算保留在原档及新准备收据中。

新运行仍在严格 extinction certificate 成立时提前结束。实际时间单独保存；到 cap
仍未闭合为 unknown。核心失守后继续计算损失。physical invalid 的情景不能进入可行前沿。
本准备没有启动大村 CA，实际启动与并行资源由 root 协调。

## 当前 V3 的续算边界

`solver_v3._run_resolved_v3_simulation` 当前固定调用 `_initial_state(request)`，没有
受支持的恢复入口。对于本批 **firebrands=None、全部 prescribed ignition 在 t=0、
静态物理输入与已解析传播算子不变** 的条件，完整 V3State 包含未来热演化所需的
温度/有效掩码、燃料、水、状态、燃烧年龄、事件时间和累计热量。但直接替换 state
仍不足以声称原生输出无损续算，还需恢复或重建以下内容：

1. 原始 post-intervention initial fuel、initial heated water，而非把当前剩余量当初值。
   其中 initial fuel 还参与每步 fuel tolerance；原始预算可由 loss-basis 和同一 request 重建。
2. `ignition_cause`，完整 `V3EnergyBudget` 累计值及
   `energy_limited_contact_source_steps`，以保持能量闭合和消耗统计。
3. 历史逐节点有效温度峰值，使后半段不抹掉此前遗产受热最大值。
4. 已应用事件索引、下一采样索引和真实 step index。若采样开启，从索引 0 恢复会导致
   “已经跨过精确采样时间”的错误；重复应用 t=0 事件也不能重置已有 cause。
5. 相同屋面分片、原始归属、冻结积分 membership、source footprint、radiation edges
   与 contact operator，并逐 hash 验证实际保存的输入和状态。

飞火的随机状态、待落地事件和质量/能量转移另有隐藏状态，不在本续算可行范围内。
本批没有飞火，因此不存在该项恢复需求。

最关键的时钟限制是：原 21600 s 是 `min(end_time - time, ...)` 的数值截断点。
从保存的 21600 s 状态继续，仅能自然等价于**明示在 21600 s 分段的时钟协议**，
不能直接宣称等于一口气从零运行到 172800 s 的轨迹。若以后实现恢复，必须对所有
对照使用相同强制 checkpoint，并以真实小模型验证 split/full 的事件、燃料、水、
累计能量、peak、cause 和能量预算一致，另存恢复收据。不能把先前 cap 当作隐形 checkpoint。

当前先从零运行新长 cap，比修改恢复入口和做上述验证更快形成可审查结果。
旧首项求解 wall 约 362.6 s；这不是重新运行已知不足的全部 40 项 21600 s。
旧时限结局、旧最优性或旧 prefix 状态不转移到新时间协议。

## C/N/A 与只读归档

`scripts/summarize_heritage_joint_pareto.py` 在独立解释器中加载每个原运行自己的
已核验 source-snapshot，调用原 runner 的 read/terminal observation 验证，检查
实际 native state、历史 peak 和闭合证书。当前 pure Pareto 实现独立记录源码 hash。

C 使用原 owner 分片的实际清理几何并集，再加每个完整草地 parent 的 25 m²施工
footprint；covered fuel 面积独立保留。原生细分求和与 canonical 几何 C 的浮点差异逐项
留账，绑定容差为 abs 1e-6 / rel 1e-10；支配比较仍按传入 canonical C 精确比较。
N 为实际残余屋面上的原始归属燃尽数量下/上界，A 为整个物理域燃尽面积。

保留源 cap 仍只限制 roof geometry cost；总 C 加入草地后可高于该 cap。旧 roof-only、
原四源、3600 s 面积证明只作参考，不构成联合行动或长终端时间协议的面积最优证明。
有限已评估方案的 Pareto 档案不声称为完整全局前沿。未评价、缺场景、cap 未闭合、
unknown 和失守方案明确列出，均不进入满足核心保护硬约束的前沿。
