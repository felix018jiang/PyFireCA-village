# 岜团村多情景保护约束优化（2026-10-17）

本轮目标是在指定的全部设计情景下，90分钟内民居与风雨桥均不引燃，然后最小化植被有效清理面积。外侧与建筑近邻分别搜索；沿用建筑20m缓冲代理区，不宣称已调查村界或施工可用地。保留全部房屋与桥。

## 预先声明的情景

设计：父网格(163,151)、(160,151)、(168,143)、(170,159)四个植被火源 × 静风/东南来风2m/s/西南来风4m/s × 原点燃温升/降低5%温升，共24个90分钟独立单火源情景。前两个包括已知桥失效源，后两个扩展南侧林地的侧翼。它们仍不代表全村全部来火方向。

留出：(171,146)、(171,156)两个新植被火源 × 南风3m/s/北风3m/s × 两组点燃温升，共8个。协议冻结后，搜索只读其火源坐标以禁止清理火源，不读取其模拟结果；两区全部搜索结束并冻结selection后才评价留出，不据其结果重选。本轮若用其结果继续开发，下一轮须另设未见验证。

所有风速与材料扰动都是合成压力测试，不是岜团村实测分布，也不赋予年风险概率。温升式为 `Tamb+factor*(Tign-Tamb)`，不改燃料、水分、热释放、接触传热。材质扰动目前限定点燃温升，尚未覆盖全部材质参数不确定性。

## 风况实际进入模型

基准case原风速与倾斜系数均0。新环境副本启用已有V3静态风致辐射源几何闭合：`theta=atan(0.2*wind_speed)`，将火焰半高处辐射点沿下风向平移并改变高度，重新计算有地形的辐射图。0.2s/m为明确声明的合成系数，未经本村校准。风向为相对网格北的气象来向。

本轮不增加风驱接触、飞火、动态地形风场。不能将仅修改风速但系数0的重复仿真当作风况试验；专项测试验证相反风向的实际边权不同、静风副本精确复现父输入、原case不变。

## 优化与精确可行性

每区两个独立随机种子，分别64次唯一候选请求（含物理/火源约束拒绝），共256次上限。两区各128次。几何为自由类别，最多3条带、每条5节点、宽2.5—30m、长10—240m。沿用束搜索和模式扰动，同时允许折点增删、路径组合/删除。历史布局、确定性加宽/延长及折线/辅助带变体仅作为预声明初始化，不把旧结果当作新环境的成功证书。

每个候选按固定顺序检查设计情景。一旦民居或桥被引燃，该方案已经被严格证明不满足“全部零引燃”，可以停止余下情景；仅将已评价损失作为**未达标方案的启发式下界**，不将其写成全情景最坏值/平均值。只有24个全部算完且均零引燃才允许保护达标。达标方案的保护得分一致，按有效清理面积选最小者。若没有达标方案，明确报告未找到可行解，不能改成最小损失方案已保护成功。

第一次失效提前筛除不等于提前终止物理时间：每个实际调用的CA仍计算到5400s。无干预、历史两区方案和冻结新方案最终都补齐24设计+8留出情景，以便完整对照。任何燃烧到局部窗边界的评价均无效，不能用被截断的传播宣称保护成功。

物理缓存包含案例、代码、风/点燃参数、火源、干预几何、区掩码与建筑类别；逐条哈希核对、文件锁防止并发重复求解。源码逐文件快照，原case48文件及建筑标注、旧研究报告逐文件锁定；冻结后改动源码或输入拒绝恢复。

## 复现与回退

```bash
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/run_batuan_multiscenario_search.py \
  --output runs/research/batuan-multiscenario-20261017-r1 --phase prepare
# prepare只运行一次；之后search可用同参重建搜索轨迹并复用签名一致的缓存。
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/run_batuan_multiscenario_search.py \
  --output runs/research/batuan-multiscenario-20261017-r1 --phase search --workers 4
PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/run_batuan_multiscenario_search.py \
  --output runs/research/batuan-multiscenario-20261017-r1 --phase finalize --workers 4
```

更新前标签 `batuan-before-multiscenario-20261017` → `0de2e37`。独立Git备份 `/Users/tone/Documents/github/PyFireCA-backups/batuan-before-multiscenario-20261017/repository.bundle` 已验证。旧8856报告和全部旧实验不覆盖。在另一目录查看旧版：

```bash
git worktree add --detach ../PyFireCA-before-multiscenario batuan-before-multiscenario-20261017
```

本文件先登记方法和协议；完成结果另行追加，不能在计算前声称找到达标或最优方案。
