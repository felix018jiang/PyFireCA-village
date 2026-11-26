# 防火线研究的近邻方法与可发展的贡献

日期：2026-10-17。状态：文献核对与研究设计；飞火开发、岜团村新数值试验已暂停。本文不报告新的模拟结果，也不宣称已证实算法原创性。

**后续研究定位更新（同日）**：用户明确选择以内外防火线协同优化方法为主线，飞火作为可靠性因素。本备忘录保留此前近邻核对及当时的建议；最新定位、现有程序差距与开发优先级见[传统村落内外防火线协同设计](TRADITIONAL_VILLAGE_JOINT_FIREBREAK_METHOD_20261017.md)。

## 结论

“元胞自动机＋随机起火/天气＋模拟驱动的防火线优化”已有明确且较完整的方法谱系。当前项目可以继续作为研究平台，但不能把这一组合本身作为主要原创贡献。最小清理面积、建筑保护、飞火失效、生态约束也分别存在很接近的先例。

建议推进的问题是：**在飞火跨越外部阻隔并可能触发村内木建筑连锁燃烧时，达到整村联合保护可靠度所需的最小土地干预是多少，何时单靠植被干预不能达标？** 这是待检验的研究主线；尚不能据此次检索声称首次提出。

## 检索与阅读范围

通过 Exa 与网页检索交叉核对，重点比较下列 10 项近邻工作。其中 6 项核对方法正文（含一项强化学习论文的公开预印本）；其余明确标为摘要或机构记录。查询覆盖 simulation-based firebreak placement、Cell2Fire stochastic optimization、spotting failure、structure protection minimum treatment area、heritage wildfire optimization、fuel treatment/home ignition zone 等，并沿近邻参考文献向早期方法回溯。这不是穷尽检索或按系统综述规范完成的证据合成。

### 1. Palacios-Meneses 等，2023，预印本

[Comparison of metaheuristics for the firebreak placement problem: a simulation-based optimization approach](https://arxiv.org/html/2311.17393v1)

阅读：方法正文。Cell2Fire 元胞模拟；随机起火与风况；以重复模拟的平均烧毁元胞数评价方案；遗传算法和 GRASP 调整处理位置；面积比例约束；比较成组形状和分散处理。直接覆盖我们原先的基本闭环。其期刊发表版本未在本轮核实，不按已发表期刊论文引用。

### 2. Vilches 等，公开预印本（链接目录 2023，后续文献标注 2024）

[A two-stage stochastic programming approach incorporating spatially-explicit fire scenarios for optimal firebreak placement](https://optimization-online.org/wp-content/uploads/2023/11/FPP_2Stage_SP_EJOR-6_compressed.pdf)

阅读：方法正文。用 Cell2Fire 构造情景和传播树，两阶段混合整数规划结合期望烧毁损失与 CVaR。还用新情景以及放回 Cell2Fire 的动态模拟检验方案。因此，“考虑尾部风险”“加入留出情景”“优化后再动态复算”均有先例。文件名中的 EJOR 不能作为已被该期刊录用的证据。

### 3. Murray 等，2025，Applied Soft Computing 175:113043

[Deep reinforcement learning for optimal firebreak placement in forest fire prevention](https://www.sciencedirect.com/science/article/pii/S1568494625003540)

[公开预印本：Advancing Forest Fire Prevention](https://arxiv.org/html/2404.08523v1)

阅读：公开预印本方法正文、正式版摘要；未核对正式版全部改动。预印本用 CNN 与 DQN 系列逐步选择处理元胞，结合 Cell2Fire 和随机起火/天气进行评价。改用强化学习本身不足以区分我们的研究。

### 4. Nguyen 等，2024，PLOS ONE

[An optimization model to prioritize fuel treatments within a landscape fuel break network](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0313591)

阅读：方法正文。在既有防火线网络中选择处理段，通过火场与阻隔网络的空间交集、火焰长度阈值和混合整数规划最大化受保护面积或 WUI 价值。它主要是既有网络投资选择，保护评价并不等同于显式模拟每栋木建筑的点燃与连锁传播。

### 5. Yemshanov 等，2026，PLOS Complex Systems 3(8):e0000117

[A network optimization framework for fuel break planning in wildfire-prone landscapes: Accounting for fuel break failures and wildlife conservation goals](https://journals.plos.org/complexsystems/article?id=10.1371/journal.pcsy.0000117)

阅读：方法正文。Burn-P3 随机火情构造传播图；依预测最大飞火距离与带宽判断阻隔是否有效；加入全景观失效限制、CVaR 和驯鹿迁徙路线约束。其战略规划图节点为 400 ha，火情模拟分辨率为 1 ha。与我们可能的逐建筑动态受火评价存在尺度和机制表示差异，但“飞火＋宽度＋可靠性＋生态”这一概括已有强重合，不能据此宣称首次。

### 6. Bar Massada、Radeloff、Stewart，2011，International Journal of Wildland Fire 20:59–68

[Allocating fuel breaks to optimally protect structures in the wildland–urban interface](https://research.fs.usda.gov/treesearch/37386)

[机构提供的全文](https://research.fs.usda.gov/download/treesearch/37386.pdf)

阅读：方法与讨论正文。对单栋或成组建筑构造包围处理带，在不同聚类方案中最小化总处理面积；实例使用 30 m 带宽。作者已指出飞火和圈内起火会削弱保护，不能把最小面积等同于最佳防护。因此，“围护所有建筑并减少占地”以及“防火线不能消除飞火风险”的一般判断也不是新贡献。

### 7. Carrasco 等，2023，Journal of Environmental Management 342:118087

[A firebreak placement model for optimizing biodiversity protection at landscape scale](https://doi.org/10.1016/j.jenvman.2023.118087)

阅读：摘要与引言，未完成方法全文核对。明确权衡清理导致的直接生物多样性损失和防火带来的保护收益。不能把添加生态目标作为独立原创点。

### 8. Alcasena 等，2022，Journal of Environmental Management 309:114650

[Contrasting the efficiency of landscape versus community protection fuel treatment strategies to reduce wildfire exposure and risk](https://research.fs.usda.gov/treesearch/65114)

阅读：机构记录与摘要。比较景观火源区、住宅点燃区及二者组合处理对建筑损失的影响。因此，“村外＋近建筑协同”有重要先例；需要具体到不同传播机制、联合保护事件和决策算法才能形成进一步贡献。

### 9. Qin、Trouvé，2026，Fire Safety Journal 162:104792

[Simulations of firebrand ignition in landscape-scale fire spread models](https://doi.org/10.1016/j.firesaf.2026.104792)

阅读：出版社摘要与研究要点，未取得方法全文。已有生成—输运—点燃框架，区分野地单个有焰飞火与 WUI 阴燃飞火积聚及阴燃到有焰转变延迟。“细分飞火生成、飞行和建筑受体”不能直接当作新机制；我们应优先验证并适配已有模型。

### 10. Lee 等，2026，Sustainability 18(8):3835

[Digital Twin-Based Wildfire Simulation on a 1 m DEM and Adaptive Water-Mist Optimization for Heritage Protection: Bogwangsa Temple, South Korea](https://doi.org/10.3390/su18083835)

[作者机构记录](https://pure.skku.edu/en/publications/digital-twin-based-wildfire-simulation-on-a-1-m-dem-and-adaptive-/)

阅读：机构摘要；出版社全文访问受限，未核对方法细节。摘要报告 1 m 元胞、蒙特卡洛火情、遗产建筑到火概率阈值与最小成本喷头配置。该文已使“元胞＋随机火情＋遗产保护概率约束”的概括不足以支撑我们的原创性；到火概率与建筑实际点燃概率应明确区分。

## 推荐的具体方法增量

### A. 研究对象：跨尺度的失效链

分别记录地表连续传播、辐射/接触引燃、飞火引燃、建筑再辐射/再发射的路径和时序。检验“外部阻隔被跨越以后，少数受体建筑是否主导整村级联风险”。新增价值必须来自校准后的预测或决策改善，不能仅因为状态数更多。

卫星图只能为燃料类别和覆盖提供输入；含水率、负荷、木构围护脆弱性、飞火积聚条件需要实测、可靠文献与不确定性区间。不得为突出防火线作用而定向降低点燃阈值。

### B. 决策目标：整村保护的最小干预前沿

设 L 为可施工防火带的集合，A(L) 为全部带状区域并集的实际清理面积，不能重复计交叠面积。设 J 为受保护民居与风雨桥集合，T_j 为对象首次有焰点燃时间，无点燃记为无穷；s 表示预先规定的起火、气象与材质情景，omega 表示飞火等随机性。

目标示意：

    A*(r) = min_L A(L)
    subject to max_s P_omega[min_{j in J} T_j(L,s,omega) <= 90 min] <= r
               L belongs to the buildable, heritage-preserving intervention set

约束控制的是“至少一栋民居或桥被引燃”的联合事件，不是各栋概率的简单平均。r 为可讨论的失效容许度，不擅自称为正式安全标准。情景最大值只对所定义的情景集合成立；不能外推为所有可能天气。该目标形式沿用成熟的可靠性优化思想，本身不构成新数学方法。

通过改变 r、受火强度和村落形态，研究最小干预前沿、方案形态转变和不能满足目标的区域。90 分钟先保留为现有研究时窗，之后给出响应时间依据并检验其他时窗。

### C. 求解机制：由失效路径驱动候选生成与情景增补

1. 使用经校准的模拟器评价现有方案和基线，提取最早失效受体及其上游火源、飞火通道和村内级联路径。
2. 根据失效机制提出位置、分段宽度、折线节点及多带组合变化；允许删除低收益段，避免只能不断扩大处理面积。
3. 对候选方案采用配对随机流比较，使用快速模型筛选，最后必须回到完整动态模型评价；代理模型的漏判安全率须单独核验。
4. 自适应增补困难起火/气象情景及罕见飞火事件，直到达到预设搜索预算与验证规则。重要性抽样若用于估计概率，必须使用正确权重。
5. 在独立、冻结的测试集中验证最终方案及其概率上界。测试失效后若回到搜索，该测试集即已用于开发，须另设最终测试集。

以上组件分别也有成熟先例。算法贡献需要进一步证明：相同模拟预算与保护目标下，是否比通用 GA/GRASP、图方法和当前搜索更快找到更小且在独立情景中可靠的方案。不能因起了新名字就视为创新。

### D. 无法达标时的处理与证据边界

启发式搜索找不到解不能证明不存在解；“清理所有可清理植被仍失败”若无单调性证明，也不能自动证明所有子集都失败。

可探索条件性不可行证据：当某条源到受体的直接飞火路径在所有允许的植被干预下均不变，且必然触发目标失效时，它可作为该情景下的机制证据。需要明确源燃烧、局地风、受体暴露等是否真的不受干预影响，并对概率目标给出足够的风险下界。此处尚未完成数学证明或实证验证。

如仅靠防火线不能达标，再比较符合遗产保护要求的近建筑可逆措施。这是另一个明确干预类别，不能把建筑材料修改隐藏在防火线方案中。

## 关键实验与判定

- 基线：无处理、等面积固定缓冲带、等面积随机/贪心、模拟驱动 GA/GRASP、可复现的图优化近邻，以及现有岜团村方案。
- 公平比较同时给出固定面积下的保护效果、固定可靠度下的清理面积，以及相同计算预算下的结果。
- 消融：去除飞火、去除建筑级联、去除失效路径引导、固定宽度/直线限制；检验哪个组件改变了决策而不只改变动画。
- 独立观测检验到火时间、点燃、飞火落区及建筑传播；区分代码验证、参数校准、外部有效性验证。
- 岜团村用于机制开发；多个独立村落及形态控制实验用于检验条件规律和外推。不能用同一村的多个起火点替代跨村验证。
- 面积只是土地干预代理量，不能直接等同于生态损害；若主张生态收益，需要植被、栖息地、碳或恢复等证据。
- “零次失效”须报告试验数、情景范围和不确定性；不能写成绝对安全。小规模问题若有可证明下界/精确最优解，可用于检验搜索质量；大规模结果按已找到最优方案报告。

## 暂定贡献表述

我们拟研究木质传统村落在地表传播、飞火跨越与建筑级联共同作用下的防护机制，并建立以整村联合点燃风险为约束、以实际清理面积为目标的设计方法。重点检验由失效路径引导的搜索能否在相同计算资源下改善保护—占地前沿，以及该前沿如何随风况、燃料和村落形态变化。**这些是待验证的增量贡献，尚不能宣称首创或已获实证支持。**

对于 Nature Sustainability，建议将主线放在文化遗产保护与土地/生态干预之间可检验、可推广的适应决策规律上，模拟器和优化器服务这一问题。这是研究定位建议，不代表达到该刊录用要求。[期刊官方研究范围](https://www.nature.com/natsustain/aims)。

## 版本与断点

已完成的多情景版本：`794cd8e`，标签 `batuan-multiscenario-20261017`。
飞火开发前回退标签：`batuan-before-firebrands-20261017`。
飞火草稿断点：`8827ad6`；见 [开发检查点](FIREBRANDS_IMPLEMENTATION_CHECKPOINT_20261017.md)。草稿尚未完成全流程验收，没有新的岜团村飞火优化结论。
