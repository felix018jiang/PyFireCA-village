# 冻结候选清单的物理等价合并

`scripts/merge_heritage_joint_pareto.py` 对同村不同候选清单作零 CA 后处理。默认严格判据；`--temporal-mode finite_domain_closed` 明确建立另一分析池。两种判据不能混合。

每份输入先经 `summarize_heritage_joint_pareto.py` 在原始源码快照的独立进程中核验配置、全部冻结输入、完整产物清单、原生状态与历史峰值、源条件和闭合证书。有限域视图由新判据独立派生，原始 `unknown`、边界 invalid 和原 native problem SHA 始终保留。

跨清单不以村名或起火建筑相同放行。工具逐项要求：

- 原 parent request 的网格、所有 mask、材料/热参数数组、contact、firebrands、场景时钟、原传输边和全部其他字段相同；transport、inventory 和核心身份相同。
- 完整火源 family 协议、draw、全优先序、全部预备源几何、几何迭代顺序、共同 refinement 相同；每份实际评价的源独立符合 first-retained 规则。相同 family、尺度和实际源的面积/燃料/能量预算必须完全相同。
- h、求积间距、dt、参考与终端 cap 相同；全部 `src/` 源文件 SHA 完全相同。runner 的物理构造函数调用、mesh、点火、终端调用及 imports 的 AST 相同。
- 每份已保存结果的 common baseline layout SHA、节点顺序、physical cell、component、坐标、面积分数、原 owner、干预前 covered area 和冻结积分 mask 相同。原 owner 从不可变 component 恢复；仅删共享标签不能改变物理归属计数。

不同方案的干预后 request SHA 本来可以不同，不能用它要求所有方案相等。原生 reader 验证各自声明的屋面 owner-AND 清理和整 5 m 草格清理。共同离散算子由相同确定性代码和相同全部输入论证；本工具不重建逐边辐射图，也不把现存证书冒换为新证书。

只允许明确列出的资格、候选清单、追溯和计数字段不同。任何新物理字段或不一致叶项默认拒绝。新合并收据绑定所有原 native/analysis problem SHA、完整物理协议、每份独立报告和当前审计工具 SHA。

输入 JSON 为：

```json
[
  {"run_root": "/absolute/run-A", "config": "/absolute/config-A.json"},
  {"run_root": "/absolute/run-B", "config": "/absolute/config-B.json"}
]
```

相对路径以登记 JSON 所在目录为基准：

```sh
/opt/anaconda3/bin/python scripts/merge_heritage_joint_pareto.py \
  --catalogs /absolute/catalogs.json \
  --temporal-mode finite_domain_closed \
  --output /absolute/merged-analysis.json
```

默认必须恰好有 8 个登记情景。完整性按每个方案单独核验；缺失、未闭合或核心失败不能进入可行前沿。同名方案按 `batchN::原名` 保留独立身份，原始 summary 中的名称及 problem SHA 不变。C/N/A、N 上下界敏感性和未决潜在支配来源在合并集合中重新计算，不添加拆除数量到纯火损 N。

输出只表示已完整评价集合的非支配点。未决方案仍保留；旧屋面面积锚不转移为联合清理面积最优证明，合并收据不证明全局 Pareto 前沿已找齐或现场安全。

## 跨批逐 case 补齐

`scripts/compose_heritage_joint_cases.py` 在上述严格物理审计基础上，按完全相同的屋面行动、草格行动、清理后地表规则和重叠规则组合互补情景。完整方案仍须全部 8 个原生 case 闭合且核心安全；1–7 个 case 只作为未决方案的已有证据。

同动作、同情景若重复，必须拥有完全相同的干预后 physical request SHA（包括完整辐射边）、状态与历史峰值的数组指纹、实际终点时间和步数、源几何和面积/燃料/能量预算、核心观测及 C/N/A。差异直接报矛盾，不挑选更有利的一份。重复原生记录只去重用于聚合，各自 native problem SHA、analysis problem SHA、清单、状态与原始 invalid 均保留。

时钟修复只有显式提供 `--clock-compatibility` 才能桥接。工具验证外置兼容审计封印，并再次读取两份原生 solver 全部字节：唯一允许差异是在旧 `abs(time-due)>tolerance` 必抛分支内添加 4 行注释与“采样点仍在未来则等待”。移除此精确片段后，两文件必须逐字相同，真正越过采样点的原异常不变。旧成功执行从未进入该分支，因此在同输入、同运行环境下可逐步归纳证明其轨迹逐位不变。共同模型明确使用新异常扩展核；旧异常失败的 partial 文件不属于可组合的完成 case。

各库版本和解释器也须有外置控制器追溯收据，使用 `--runtime-attestation` 登记。工具核验收据、依据文件哈希、覆盖的 native problem 和原生读取器实际 Python/NumPy/SciPy/Shapely/GEOS/机器版本。报告明确区分控制器的历史执行追溯与原档案自身携带的观测；不把当前读取环境冒称为旧档案原始字段。

```sh
/opt/anaconda3/bin/python scripts/compose_heritage_joint_cases.py \
  --catalogs /absolute/old-and-new-catalogs.json \
  --clock-compatibility docs/results/v3-sampling-clock-error-path-audit-20261111.json \
  --runtime-attestation docs/results/heritage-native-runtime-attestation-20261111.json \
  --temporal-mode finite_domain_closed \
  --output /absolute/case-composition.json
```

输出的 `action-...` 是共同物理行动 ID，`plan_aliases` 逐项指向各批原方案名和原问题。新的 case-composition analysis problem 与原生问题分开，不改写或重新标记原生 summary、manifest、状态文件，也不转移全局最优性。
