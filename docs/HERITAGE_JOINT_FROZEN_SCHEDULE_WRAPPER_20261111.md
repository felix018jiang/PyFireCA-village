# 原生联合比较的外置计划优先调度

`scripts/resume_heritage_joint_schedule.py` 用于已经冻结、已经产生 case 的联合比较。它在 fresh 进程中验证并导入该批原 source snapshot，唯一替换是模块内 `execution_checks` 的遍历顺序：完整相同的原 selected case 集合按计划优先排列。`expected_checks`、物理输入、原 native problem、case reader、`resume`、累计预算及上限都沿用原实现。

检查和登记不运行 CA，不构造新的辐射图，不复制或重标 native case。已完成 manifest 逐份核验并登记 SHA。恢复时，旧 case 必须仍在且未改变；后来正常新增的原生 case 可以继续复用。查询计数与 elapsed 只许增加，不能小于登记快照，不提供退回已消耗槽位或修改原 cap 的接口。

```sh
# 检查可行性；active worker 只读，不发信号
/opt/anaconda3/bin/python scripts/resume_heritage_joint_schedule.py \
  --run /absolute/native-run --config /absolute/original-config.json \
  --inspect-only --output /absolute/inspection.json

# 在原 archive 外冻结 wrapper、顺序、旧 P、输入/源码与预算收据
/opt/anaconda3/bin/python scripts/resume_heritage_joint_schedule.py \
  --run /absolute/native-run --config /absolute/original-config.json \
  --register-only --receipt-dir /absolute/external-schedule

# 使用登记的 wrapper 副本；原 worker 必须已正常交回预算
/opt/anaconda3/bin/python /absolute/external-schedule/resume_heritage_joint_schedule.py \
  --resume --receipt /absolute/external-schedule/receipt.json
```

默认计划优先级沿用原 `config.plans` 顺序。可在检查/登记时用 `--priority-plan-ids` 提供精确全排列；不改变物理方案列表本身。

`handoff_ready` 是登记时状态。只要原 budget 仍 active，或存在 `.pending` native 产物，恢复即拒绝。wrapper 不向进程发送信号，也不能给一个已经运行且没有 pause hook 的旧 worker 补装“本 case 完成后暂停”功能。需要控制器确认已有干净边界；不能通过强杀长时间运行的 CA 来假装无损交接。

岜团薄带批的冻结外置收据位于 `runs/research/heritage-thin-plan-first-schedule-20261111/receipt.json`。登记时已有 6 个完整 case，原预算为 `started_checks=7`、`elapsed_s=2058.3128793750075`、`active=null`，一项已消耗的槽位没有退回。原问题仍为 `0adf5bad01dae3c6bdd665c822fc8c0227e9245b5a9bd9e56d850d36fafd6897`。下一组未算 case 是 `.01` 方案在 B0071、B0062、B0061 三个 family 的 h1/h0.5 六项，之后才进入其他薄带计划。

5 项回归覆盖精确 case 排列、fresh import 隔离、真实小模型 clean post-persist 边界上的缓存复用及预算继承、重复恢复不重复查询、active worker 拒绝接管且预算不变、修改 wrapper 拒绝执行。独立只读审阅未发现 P1。
