# 断点：用户要求明天继续

用户于2026-11-10明确要求“打个断点，明天继续，现在没额度了”。**所有村落计算和子任务已停止，不自动恢复。** 三个求解进程均以SIGINT正常退出，预算active全部为null，没有pending半成品目录。完整情景结果保留；中断中的单次求解没有伪装完成，恢复时重新计算且不退还已消耗调用槽。

机器断点含预算、完整manifest及元数据哈希：[heritage-paused-checkpoint-20261110.json](results/heritage-paused-checkpoint-20261110.json)。

## 已确认的目标

- 暂无真实火灾/材料调查，先完成**模型内优化**。
- 用户选择**保留清理面积与火损的最优权衡方案，之后再选**，不强行选单一赢家。
- 核心遗产保护为硬约束；C为实际施工清理几何并集，N为火致燃尽原始屋面归属数（下/上界），A为全模型域燃尽面积。终端与3600 s阶段结果分池。
- 未取得完整全局最优或完整Pareto前沿证明，不能写成已找到三项目标同时最小。

## 暂停时的真实运行状态

| 运行 | 已消耗调用槽 | 完整原生情景 | 已验证结论 |
|---|---:|---:|---|
| 岜团全331屋面动作、四保留源家族×双支持 | 29 | 28 | 原三栋B0061/B0071/B0076八项3600 s全部通过；面积下界86.96290868455935、上界443.8419687401409 m²，未闭合 |
| 平岩全947屋面动作、四保留源家族×双支持 | 8 | 7 | 仍在无干预八项预取；background-h0.5未完成，尚无完整新可行种子 |
| 岜团五组联合清理、完整火灾 | 2 | 1 | 第一项roof-seed/B0075-known-h1达到21600 s仍燃烧，终端unknown；第二项被中断 |

每个运行均有一个已消耗但未完成的调用槽，预算不重置。累计执行时间分别4948.649813833006、3442.3009229580057、533.3987848749966秒。

**明天优先处理的新增发现：**岜团屋面参照在6小时仍有7460.952788473564 m²活动燃烧，累计引燃142736.38718787738 m²、已燃尽135275.4343994038 m²；两个核心在已观察6小时内未引燃，但没有整场闭合证书。因此21600 s不足以完成这一项全火灾损失评价。应决定更长终端上限/有效续算方案，并创建新的冻结协议，不能编辑现有冻结config冒充原实验；也不能把unknown当成功或最终火损。当前第一项实际求解约362.6秒，延长物理观察期的计算代价可评估。

## 已完成并保留的结果

- 固定两源面积阶段：岜团48项，界137.3385359914043—443.8419687401409 m²；平岩48项，界296.31335571698554—974.4542322625402 m²。两者均未证明面积最优，后续重点是全保留源问题。
- 新物理归属N重算：两村原库存均有原屋面表示。平岩固定两源下974.454 m²对照在阶段C/N/A均优于985.608 m²原五栋，但仍燃烧，不能转成终端结论。见`docs/results/heritage-area-{batuan,pingyan}-phase-archive-20261110.json`。
- 六项数值对照、24旧轨迹全域账本、严格时间闭合小控制/真实30万节点末态核验均完成。求积严格匹配对照表明B0121峰温差仅−0.0178533 K，不能解释1 m/0.5 m支持差异；0.25 m源一小时尚未烧完。见`heritage-area-numerics-audit-20261110.json`。
- 新N保持共享物理屋面的原始归属，删除零面积标签不能改善火损数量。严格闭合、终端runner、Pareto模块及factory完成测试；终端联合兼容96项、scheduler4项、recount guard4项通过。
- 岜团五组联合方案及不可变seed收据已冻结；地图和GIS位于`runs/research/heritage-joint-terminal-batuan-20261110/action-maps/`。C分别为443.842、543.842、543.842、543.842、330.671 m²，保护结果尚未完成。
- 独立新源确认已注册seed，但未揭示新优先序；需最终候选冻结后再bind。见`HERITAGE_INDEPENDENT_CONFIRMATION_PROTOCOL_20261110.md`。

## 恢复入口与注意事项

用户明确恢复后，先核对机器断点哈希及预算active=null，不重跑已完成情景。

全屋面master可用新调度包装器优先跑全部h1再h0.5；它只导入原source-snapshot、保持原物理problem SHA、复用原缓存和累计预算，另存调度收据。真实48项缓存inspect-only及小问题面积最优性一致性测试已通过，适合避免先花数分钟算h0.5后被另一源h1迅速否定。

```bash
python scripts/resume_heritage_area_schedule.py --run runs/research/heritage-retained-area-batuan-20261110 --config examples/research/heritage/area-20261110/batuan-retained-area-master.json
python scripts/resume_heritage_area_schedule.py --run runs/research/heritage-retained-area-pingyan-20261110 --config examples/research/heritage/area-20261110/pingyan-retained-area-master.json
```

若需追加预算，包装器支持显式累计`--max-cumulative-checks`/`--max-cumulative-wall-s`/`--max-cumulative-mip-s`，保留旧消耗和新收据，不能改原config或把消耗清零。当前原上限各64槽/14400秒/1200秒MIP。

原岜团联合批次可以按下列命令恢复缓存，但**应先处理6小时不足的发现**，不要盲目把40项全部跑到同样不足的上限：

```bash
PYTHONPATH=runs/research/heritage-joint-terminal-batuan-20261110/source-snapshot/src python runs/research/heritage-joint-terminal-batuan-20261110/source-snapshot/scripts/run_heritage_joint_comparison.py --config examples/research/heritage/area-20261110/batuan-joint-terminal-comparison.json --output runs/research/heritage-joint-terminal-batuan-20261110 --resume
```

运行使用`/opt/anaconda3/bin/python`（当前默认python），有SciPy；`.venv`缺SciPy。16 GB内存，磁盘约78 GB余量；不要同时堆叠多个大图重建。生产副本`heritage-joint-terminal-producer-20261110`有169文件；实际run snapshot有113个pyfireca文件加5脚本，当前终端路径导入闭包已审阅，缺的failure_cuts仅父搜索分支使用且本终端路径不走。

## 被暂停的开发子任务

- `/root/methods_audit`：`scripts/summarize_heritage_joint_pareto.py`与`tests/planning/test_joint_pareto_cli.py`已写，曾完成小型真实native端到端0CA验证；最后一轮6项测试交接未收到完成消息，明天先检验，不假定全通过。它核验原snapshot、原生state/peak闭合证书后再调用纯Pareto模块。
- `/root/research_context`：`src/pyfireca/planning/joint_action_prototype.py`已写但**未完成验收，禁止生产使用**。拟把全部合法整格植被也纳入真正联合面积master：岜团78386草格+331屋面、平岩212887草格+947屋面；必须新cut schema/能力与小powerset真实CA证明，不能套旧屋面cut。考虑从既有baseline原生末态0CA重建联合失效证书，避免重跑八项。源覆盖用roof投影且roofC≤jointC，总cap与源prefix一致。
- `/root/results_audit`：四项recount guard测试已完成，无剩余运行。

完整论证、源规则与证据入口见`HERITAGE_AREA_OPTIMIZATION_RESULTS_20261110.md`、`HERITAGE_RETAINED_AREA_PROTOCOL_20261110.md`、`HERITAGE_JOINT_TERMINAL_PROTOCOL_20261110.md`。保留全部既有未提交改动；不要批量清理工作区或历史快照。
