# 有限受体高度带：坡地接触候选方案

2026-10-06。候选演示端口8853；原点受体演示8851保持。修改范围限于基础单状态模型的邻格接触几何，辐射、默认水分、燃烧曲线、引燃阈值和源非辐射能量预算均不改变。

## 证据与推断的边界

[USFS细燃料热交换研究](https://research.fs.usda.gov/firelab/projects/heatexchange)支持细燃料传播中保留火焰接触与对流加热。[Sanchez-Monroy等，2019](https://research.fs.usda.gov/treesearch/58974)比较无外加风的坡地实验与WFDS：其研究条件下，0–22°以辐射加热为主，高坡度对流变得重要。因此不能从某一个接触模型的几何条件推断所有坡地传播规律。

[Gallacher等，2018](https://scholarsarchive.byu.edu/facpub/6930/)的小尺度池火实验表明，坡面会改变火焰形态、贴附和热通量；该实验不是高友村植被燃料的参数标定。

据此，本项目的工程推断是：保留接触传热，同时减少把空间受体简化为一个点造成的全有/全无跳变。下面的均匀高度带重叠公式是本项目的候选数值闭合，不是上述论文提供的公式。±0.15 m是演示假设，未由文献或实测确定；不能为让动画扩散而扩大这一值，也不模拟已解析的火焰倾斜、贴附流场或冠火。

## 几何与热量

对相邻火源i、受体j，源火焰区间为 `[ground_i, ground_i + source_height_i]`。接触受体代表高度保持现有设置，令点位置 `z_j = ground_j + contact_height_j`，半厚度为e。

当e>0：

```text
receiver_bottom = max(ground_j, z_j - e)
receiver_top = z_j + e
L = max(0, min(receiver_top, source_top) - max(receiver_bottom, source_bottom))
w_ij = L / (receiver_top - receiver_bottom)
```

受体带截断至地面以上。`w_ij`表示均匀垂向面积分布下的重叠份额，将既有几何覆盖比例乘以该值；总受体有效面积不扩大。e=0沿用原点判据；高度带小于浮点分辨率时也退回点判据。

之后继续使用原Newton接触换热、随HRR变化的活动比例、受体总接触比例≤1及源非辐射释放功率上限。部分重叠只获得部分热量，不保证达到引燃温度。没有重叠、不燃格和零HRR都不获得接触热量；不跨格跳过道路。高处树木受体仍位于2 m附近，草地不能因这个修改直接接触树冠。

可选字段 `receiver_vertical_half_extent_m` 默认0，旧配置序列化不添加该字段。有限非负数才合法；基本案例存读和网页重算保留它。部件模式显式拒绝这个基础模型字段，避免静默忽略。

## 结果及限制

159,353的17×17局部地形裁剪，600 s、1/0.25 s两种时间步：

| 半厚度e（m） | 引燃格数（两种时间步一致） |
|---:|---:|
| 0 | 1 |
| 0.05 | 1 |
| 0.10 | 1 |
| 0.15 | 3 |
| 0.20 | 3 |
| 0.30 | 6 |

e=0.15时北侧下坡草地受体与火焰区间重叠约1/3；西侧仍没有重叠，南侧高灌木仍无法接触。因此没有复制“忽略接触地形”的11格结果，没有强制所有邻格起火。不同e会改变结果，现阶段不能通过这个敏感性实验反向拟合正确的e。

完整552×352地图，同一190,391起火点和60分钟：

| 设置 | 引燃格数 | 燃尽格数 | 剩余可燃燃料（kg） |
|---|---:|---:|---:|
| 原点受体 | 1529 | 1464 | 1310114.088 |
| ±0.15 m高度带 | 2020 | 1817 | 1306820.788 |

两案46个源数组SHA-256逐项一致，仅接触配置不同。传播更多不是现实准确性的证明；地图仍有未知地表不燃补全、建筑等效材质等假设。保留独立候选端口，不将此对照当作真实野火验证。

## 使用及复现

```bash
.venv/bin/python scripts/audit_finite_receiver.py --output runs/finite-receiver-new.json
.venv/bin/python scripts/run_gaoyou_core.py --source runs/gaoyou-core-full-20261006/case --output runs/gaoyou-finite-new --end-time-s 3600 --flame-contact-config examples/village/vegetation_v3/flame-contact-finite-receiver.synthetic.yml
.venv/bin/python scripts/start_gaoyou_core.py --prepared runs/gaoyou-finite-new --port 8853
```

冻结裁剪输入依赖 `runs/core-contact-terrain-diagnostic-20261006-b/case`。文件/目录应为新输出路径；不覆盖既有结果。

正式保存的候选案例：`runs/gaoyou-finite-receiver-full-20261006/case`；结果：该目录`run/`；网页新模拟保存于该目录`interactive/`。候选配置的来源标注为synthetic。

测试检查部分重叠的准确份额、上/下坡边界、无重叠及不燃间隔、高树木受体、源热量上限、配置存读、部件模式拒绝、旧点判据兼容，以及浮点退化情形。此版本不额外要求用户逐格提供水分或高度。

全仓库969项测试通过（62.59 s，5条已有Rasterio警告）；真实浏览器全村1分钟重算保留0.15 m高度带与46个源数组，未发现浏览器错误。验证记录见候选案例目录的validation.json。
