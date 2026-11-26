# 最终候选冻结后的独立起火确认

本协议只预注册后续确认。当前没有选择新的最终方案，没有生成新源优先序、源几何、屋面网格或辐射图，也没有新增村落 CA。

现有四个历史优先序属于设计集，不能代表任意村落起火。旧平岩四个 heldout 在当时五栋方案冻结后具有独立确认意义，但 B0120、B0164 后来参与 20261103 对照与当前草带开发，不能再次用于本轮最终确认。岜团已核查的 pilot 6、c/d 18、e/f 6 个抽样均已有结果，没有可直接认证为本轮全新确认的未用抽样；其他旧资料也没有充分的“未运行、未浏览、未参与选择”证据。

## 已预注册的抽样规则

| 村落 | seed | 四个新独立 draw 的分配 |
|---|---:|---|
| 岜团 | 202611101101 | 两座核心最近邻 15m 分层各一次，背景两次 |
| 平岩 | 202611101102 | 三座核心最近邻 15m 分层各一次，背景一次 |

普通建筑先通过不重叠屋面面积至少 25m² 的几何资格检查。15m 分层按最近核心互斥分配；某核心分层为空时，使用预注册的距该核心不超过 60m 的定向压力池，该池不解释为总体风险分层。60m 池仍空则在 CA 前停止并修订协议。每个 draw 的首选池优先序后接全村全部合格普通源的随机后备优先序，调用现有 `retained_sources.with_global_fallback`。清理源建筑时，选择该 draw 的首个保留建筑，不能得到无火成功。

源锚点采用已有 SHA256 分键 PCG64，在对应源不重叠屋面均匀抽样，再构造一次 25m² 固定初火几何。新抽样可以重复建筑编号；重复几何也只记录，不根据结果换源或重抽。分层、seed、输入文件以及源生成代码均由 registration hash 绑定。双支持 1m/0.5m 是同一 draw 的模型假设检查，不计作两个独立样本。

四个 draw 是预设分层压力确认，不能估计全村任意起火下的可靠性，也不计算年火灾风险。

## 两阶段入口

脚本为 `scripts/prepare_heritage_independent_confirmation.py`。两村 registration 已保存于 `examples/research/heritage/confirmation-20261110/`；其中尚无源优先序或候选方案。最终候选必须先独立冻结，才能执行 bind：

```bash
PYTHONPATH=src /opt/anaconda3/bin/python scripts/prepare_heritage_independent_confirmation.py bind \
  --registration examples/research/heritage/confirmation-20261110/batuan-registration.json \
  --final-candidates /absolute/path/frozen-final-candidates.json \
  --output /absolute/path/independent-batuan-inputs
```

最终候选文件的最小结构如下；实际方案、草地单元和 refinement 均须在最终冻结时填入，当前没有代填未知方案。

```json
{
  "schema_version": "heritage-frozen-final-candidates-1",
  "frozen": true,
  "village": "batuan",
  "inventory_sha256": "与 registration 完全相同",
  "confirmation_registration_sha256": "registration_sha256",
  "plans": [
    {"plan_id": "已冻结方案名", "demolished_ids": [], "cleared_physical_cell_ids": []}
  ],
  "refinement_ids": []
}
```

bind 输出四份原样可复现的全村优先序、有限方案实际源矩阵及 polygon hash、共同 refinement 要求、输入/方案 hash 收据，以及可交给 `run_heritage_joint_comparison.py` 的确认配置。bind 本身不构图、不运行 CA。确认采用 `source_policy=retained_sources`、`terminal_fire_verification=true`、参考阶段 3600s、显式终态计算上限 21600s；有限上限未取得熄灭闭合时仍为 unknown。

每个情景的全部方案共享源几何与全部行动屋面的 refinement 并集。确切有限源覆盖只需各冻结方案实际选出的源；现有 joint runner 使用有限行动并集的保守优先序前缀，并额外记录这个前缀。它最多到首个不在有限行动集中的源，或最大拆除数之后一项，不为全村 2^N 优化空间细化所有后备源。额外前缀可能分割行动屋面上的非实际源 polygon，因此该共同网格属于新确认协议，不能转移旧网格的损失数值或最优性证明。

## 资源和判读

每村每方案需要 4×2=8 项 CA；最多五个已冻结方案为 40 项。可包含基线，但不能为了五方案上限删除困难 draw；若前沿超过五点，应在新源揭示前预注册分批策略，并保持共同网格与全部方案覆盖。

既有完整有效 3600s 样本给出的每方案八项求解中位时间合计约为岜团 18.5 分钟、平岩 31.6 分钟，仅供阶段计算量参考。它们不能预测终态 21600s 的传播范围与耗时。每村声明 8h 墙钟上限、50 万节点和 6000 万辐射边上限；候选冻结后必须先做容量预检，16GB 机器一次只运行一个大村求解。40 项保存数组约数百 MB，另留至少 5GB 空间；峰值内存与终态耗时由实际共同网格和传播过程决定，不能仅由旧源保证。

任一新源失守都保留在确认记录中，返回设计阶段增加场景或调整方案。根据确认结果调整后的方案需要新的独立确认，旧确认源随即属于设计信息。未完成或无闭合的结果保持 unknown，不筛掉难源，也不通过更换随机种子补成四个成功。

源资料审计、已注册输入和阶段资源统计见 `docs/results/heritage-independent-confirmation-design-20261110.json`。

## 冻结主问题的调度续跑审阅

仅把既有 scenario 集合排序为全部 h1 在先、随后 h0.5，或者按已知失败频率调整顺序，不改变每项 CA 输入、情景集合或保护约束，可以保留原物理 problem hash。MIP 的提议顺序与有限预算下找到的候选可能变化，但缓存结果、失败证书和完整可行性仍按原 scenario ID 解释。

独立 wrapper 应使用全新 Python 进程，并在任何项目 import 前固定原 snapshot 的 src/scripts；逐文件核对原源代码、输入、experiment/problem hash 和完整缓存 manifest。不得把另一个情景的快速失败写到当前请求的 scenario ID。未知和 pending 仍按原规则停止；baseline 全情景预取、通过方案的全情景检查均保留。

`ExecutionBudget` 继续读取原累计 started_checks、elapsed_s、mip_elapsed_s，不能重置 active 或绕过未闭合执行收据。延长 64 次或墙钟/MIP 限额时，应在独立扩展收据明确原限额、旧累计用量、新累计限额及授权范围；只向 controller 传入执行预算副本，不把这些字段混入物理 oracle 配置。保留旧 search.json 的 hash/副本，并在新报告引用调度/预算扩展收据。只有在这些限制下，续跑可以继续同一主问题的面积界；它不会自动完成 Pareto 前沿或终态损失确认。
