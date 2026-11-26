# 采样时钟修复后的分病例续跑设计

本文件只登记审核设计，不修改 producer、solver、缓存或预算，不启动 CA。旧岜团 long-cap 批次目前只有 9 个完整原生病例；第 10 项在同一 exact-sample 检查处两次异常退出。旧 9 项不等于原屋面 seed 已完成 8 情景，也不能据此形成 seed 可行上界。

最小兼容修复应把修改限制在旧实现本来就要抛异常的分支：保持 `state.time_s + tolerance < due` 的旧等待分支以及旧正常 capture 分支原样；只有旧 `abs(state.time_s-due)>tolerance` 且 `state.time_s<due` 时改为等待。真正越过采样时刻仍抛原异常。这样不需要把两种浮点表达式假定为代数等价。

对于一个旧版正常结束的病例，每次 capture 调用都经过旧等待分支或旧正常 capture 分支；新实现逐分支给出相同结果。由同一初态归纳，事件、采样索引、每一步 dt、热/燃料/水状态、点火、最大有效温度和止火证书都保持相同。该结论中的“正常结束”是程序没有异常，既包括保护通过，也包括核心已燃而继续计算到止火的完整病例。它不是把所有旧病例宣称为保护通过。

对于旧版会提前误抛异常的正 delta 病例，新版是在原来没有原生结果的路径上完成计算。不能把新版结果登记为旧 nativeP 的输出。修复仍需通过精确浮点边界、真正 overshoot、旧成功轨迹逐数组相同和 tiny native CA 回归，冻结只包含该改动的新 producer，再登记新的 native problem。

后续最小执行方案为：

1. 只读锁定原声明 40 个 `(plan_id, scenario_id, D, G)`，逐项验证旧 9 份 complete manifest、原 producer/input/source hashes、真实 state/peak 和证书，登记旧失败启动尝试与已花 wall time。旧记录和旧预算不修改。
2. 生成独立的“剩余病例清单”，包含集合差的 31 项，并绑定原 40 项声明、旧 9 项 manifest SHA 和新修复 producer SHA。保持原全部源目录、共同 mesh、冻结积分成员、q、dt、172800s cap、D/G 和全火灾评价方式。不能用只保留部分源的配置缩小共同准备网格。
3. 新 runner 或独立调度 wrapper 只运行该 31 项。它需要显式支持病例子集，并把子集收据与预算纳入新 prepare manifest；不得靠更改旧 `complete.json`、复制旧 check 为新 P，或把旧通过状态伪装成新 native cache 来跳过 9 项。当前冻结 runner 不具备该桥接语义，因此本轮不实施。
4. 原 9 项保留各自旧 nativeP、evaluation ID 和证书；新 31 项保留新 nativeP。独立兼容收据核验 solver AST 差异确实只扩展旧必抛异常路径，其他物理源码与所有输入相同，再把旧“正常执行”病例作为新成功路径的等价观测。这个收据只许可病例级汇集，不导入失败 cut 或转移旧最优性。
5. 在新兼容模型池中合并完整 40 项并计算 C/N/A。未闭合 cap、其他 invalid 或缺失病例仍列为 unknown；finite-mask 条件分析和原 strict 池继续分开。旧 9 项不是新增 CA，新 31 项和旧失败尝试分别记账，wall time 合并可追溯，不重置已花预算。

直接用当前跨 catalog 默认等价检查会因 solver 文件字节不同而拒绝，应保持这一默认拒绝。未来放行必须使用显式的、只认可本次 success-path-preserving 时钟修复的兼容验证器与新 analysis/equivalence problem，而不是泛化为“时间参数差不多”或“火损相近”。
