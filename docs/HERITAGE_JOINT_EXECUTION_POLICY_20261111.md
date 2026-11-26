# 联合比较的执行顺序与严格反例裁剪

新增控制仅作用于 `execution_checks` 和执行管理，不修改 `expected_checks`、物理方案列表、源协议、共同网格、有限候选清单证书或每个方案完整 8 情景的要求。默认仍为原先按 source、scale、plan 的登记顺序，不裁剪。

```json
{
  "execution_case_order": "plans_first",
  "execution_priority_plan_ids": ["safe-anchor", "thin-0.01", "thin-2.5", "thin-5", "thin-10"],
  "stop_plan_after_protection_failure": true
}
```

`execution_priority_plan_ids` 只在 `plans_first` 下使用，必须是所有登记方案名的精确全排列。缺失、重复、未知 ID 均拒绝；省略时使用原 `config.plans` 顺序。它不改变 `config.plans` 本身。已有 `execution_case_selection` 仍只筛选登记 case，之后按所选执行顺序运行。

失败裁剪是保护约束的逻辑反例：同一方案在任一登记情景中出现有效核心点火，则该方案不满足“全部情景核心安全”。允许作为裁剪依据的原生记录必须：

- 经原 `read_complete` 核验本 native problem、方案、情景、行动和全部产物哈希。
- 原生状态为 `failed`，原生 invalid 为空，source 验证为真，parent 未变化。
- saved layout 的核心 owner 节点在 final state 中存在真实有限点火时间，且 `0 ≤ ignition_time ≤ actual_time`；与 record 和 check 的核心失守身份相同。
- 终端比较还须是原生严格闭合、完整且核心失败的 terminal 结果。

`unknown`、源缺失、未闭合或仅边界失效的结果不能裁剪，包括物理已灭但原生仍为 edge-invalid unknown 的记录。已存在的有效反例在恢复时重新核验，避免重复计算该方案其他情景。

跳过决定写入独立 `execution-skips.json`，绑定 native problem SHA、执行顺序、每个跳过 case 和失败反例的 manifest/check SHA。恢复与 summary 会重新验证反例，篡改或丢失依据即拒绝。该文件不生成 CA 记录、不计作完成情景、不消耗新的 query slot，不替代缺失火损。

因此可能出现：安全锚先完成 4 个情景，另一方案首个情景严格失败后余下 3 个跳过。此时 native 完成数为 5/8，整体 `complete=false`；安全锚可有完整正例，另一方案保护约束为 failed，其余情景和全情景火损仍不完整。累计查询和墙钟预算在重复恢复时继续累计，不重置。

验证包含 13 项新增测试及原 45 项比较/终端/有限清单回归：真实小模型实现上述 5-query 行为；未闭合 unknown 的 8 个情景全部执行；真实 edge-invalid unknown 仍全部执行；重复恢复不增加 query、不改变原 manifest 或跳过收据。与旧 169 源 producer 的 imports、物理 oracle 构造、mesh、source 和 terminal 调用 AST 保持一致。新生产批须重新冻结源码和问题，原快照不改。
