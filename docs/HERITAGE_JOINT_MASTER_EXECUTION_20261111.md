# 联合行动主问题执行器

`scripts/run_heritage_joint_master.py` 是独立 producer，调用 `planning/engines/joint_search.py` 与新的 `joint_action_prototype.py`。它不改旧 snapshot，不导入旧屋面失败约束，也不把旧 3600s 上界转写为完整火灾可行上界。

## 执行协议

默认保护准则为 strict：同一登记方案必须在全部源与 1m、0.5m 两种模型支持上通过严格熄灭闭合，且所有保护核心从未点火。首次核心点火可立即判保护失败并停止该情景；此时火损仍是部分观测，不能作为完整 C/N/A 点。未闭合时间 cap 或其他无效观测为 unknown，停止当前搜索，不用于证明可行或失败。

显式 `terminal_validation_scope="finite_mask_conditioned"` 登记独立的有限域数学判据：固定原生网格和 masks，不模拟域外传播与外部热/火反馈。它只允许原生 invalid 为空或唯一 `fire_reached_physical_domain_edge`；其他无效原因均不准入。源码和物理原生问题 SHA 进入新的 analysis problem，完整原生问题另存于 experiment。

该模式创建新的 `heritage-finite-domain-mip-receipt-1`，以 analysis problem 为 MIP 问题。它没有原生 `invalid_reasons` 字段，也不把该字段设为空；原生完整 check、原生 strict summary、`native_invalid_reasons`、真实状态/历史 peak、源及 lineage 均保留。核心安全的通过仍要求真实严格熄灭证书。核心已经点火的前缀仅能判保护失败，`fire_loss_complete=False`；唯一 edge 的失败只排除精确方案，不能带 strict joint 强约束。原生有效且核心失败的观测可在新 analysis problem 下重新生成 joint 强约束。此模式不能用于证明域足够大或现实安全。

每个 scenario 使用同一原始 5m 网格、屋面清理并集成本、冻结源 footprint、固定热支持和积分成员。计算 cap 为 172800s；真正止火时刻保留，不伪装已经运行到 cap。延长终止时间可能改变旧 cap 附近最后一步的裁切，因此旧终态值及旧最优性不转移。

完整行动域由所有普通建筑和所有合法整 5m 非屋面可燃父格组成。每个草格施工成本是 25m²；covered 燃料面积只用于火损。总施工面积 cap 先约束 joint_C，再通过 roof_C≤joint_C 保证同 cap 下的 retained-source 屋面投影覆盖。cap 在新问题中尚无全情景可行方案时只是声明域边界。

可选 `grass_cell_ids` 会缩小登记草格集合；报告分别标明登记域最小面积与全合法草格域，避免把局部草带搜索称为完整联合优化。一个 active engine 控制图与状态内存，情景顺序为全部 1m 后全部 0.5m。

## 文件与恢复

配置 schema 为 `heritage-joint-action-master-config-1`。岜团 strict 草案为 `examples/research/heritage/area-20261110/batuan-joint-action-master-20261111.json`，有限域条件草案为 `examples/research/heritage/area-20261110/batuan-joint-action-finite-mask-master-20261111.json`；两者目前均未 prepare 或启动大村 CA。原 seed 只作为待验证候选。既有长 cap 原生观测若需导入，必须另做源、partition、时间协议和 native lineage 的交叉核验；当前执行器不静默导入旧缓存。

```sh
PYTHONPATH=src /opt/anaconda3/bin/python scripts/run_heritage_joint_master.py \
  --config /absolute/path/to/config.json \
  --output /absolute/path/to/new-run --prepare-only
PYTHONPATH=src /opt/anaconda3/bin/python scripts/run_heritage_joint_master.py \
  --config /absolute/path/to/config.json \
  --output /absolute/path/to/new-run --resume
```

prepare 只构造几何 partition 与容量上界，不构造辐射图或运行 CA。它复制全部本地 pyfireca 源文件及依赖脚本，冻结配置、输入、source protocol、共同 action-universe 和每情景 partition SHA。resume 自动进入该副本，并核验源码及所有输入。

每个 native 观测保存 check、evaluation、curves、fire-loss、layout、final-state、loss-basis 及完成 manifest。载入时核验新问题、行动、情景、D/G 投影、源、物理 partition、实际时间、真实核心点火、状态与历史 peak 指纹；通过方案另核验严格止火证书。失败强约束只接受新 joint schema 和对应 native case/evaluation/physical-request 绑定。

源为空、计算资源不足等在 native callback 之前产生的 unknown 也有独立持久化收据。重启不会丢失 unknown 后重新悄悄消耗 CA；started query slots、完整 native CA 数和启动状态不确定的资源失败分别计数。总预算沿 execution-budget 累加，不重置。

MIP 只返回面积下界与待验证方案，不判断物理可行。已通过方案保留为上界；限时 MIP 再次给出同一已知方案且 gap 尚未闭合时，停止待算，不重复 CA。稀疏矩阵资源上限导致计算 unknown，不能转为物理失败。

## 当前验收

2026-11-11 的 strict 首轮联合回归为 45 项通过：7 项新 driver 测试、19 项成熟 area controller 测试、19 项 joint prototype 测试。条件模式再扩展到 12 项 driver 验收，覆盖真实物理边界源、原生 unknown/edge 标志保持、独立闭合通过、核心受火前缀失败而火损不完整、cap 已到仍未闭合为 unknown，以及 strict/conditional 两套真实小模型 prepare→冻结源码子进程 resume→重复 resume。另验双支持逻辑成本一致、native cache/状态篡改、持久化 unknown 和 MIP 上下界闭合。没有运行大村 CA。

条件模式最终联合回归为 47 项通过（18.71s）：12 项 driver、19 项成熟 area controller、16 项 boundary scope；Ruff 通过。条件模式全村只读登记见 `docs/results/heritage-joint-master-finite-mask-registration-20261111.json`，4.42s、0CA，确认相同 78717 行动和源前缀，保存源文件清单、native predecessor SHA、新 analysis SHA 与边界合同。该只读登记仍未运行容量 preflight 或物理评价。

只读岜团登记审计见 `docs/results/heritage-joint-master-registration-20261111.json`：331 个屋面行动、78386 个整格草行动，共 78717 个；四族源前缀为 5/4/4/4，原物理准备参照匹配。该审计没有建立大村辐射或接触图，亦未证明 seed 可行。

面积闭合仅提供声明模型、源、支持和登记行动空间中的最小 C 左端锚。其他 C/N/A 权衡点应保留在独立完整终态档案中，不能从主问题面积证书推导整个 Pareto 前沿。
