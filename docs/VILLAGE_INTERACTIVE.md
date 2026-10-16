# 村落交互模拟

## 岜团村连续坡面接触版（当前默认）

```bash
.venv/bin/python scripts/start_batuan_simulation.py --no-browser
```

默认加载 `runs/batuan-slope-contact-20261016`，地址 `http://127.0.0.1:8851/`。配置为 `examples/village/vegetation_v3/flame-contact-batuan-slope-5m.synthetic.yml`。相邻可燃格采用 `terrain_reference: shared_face`：假定燃料沿连续坡面到达公共边，在同一边界地面上比较火焰与受体相对高度。旧版用每格中心DEM分别建立水平柱，在160,152处约±3.17 m的南北高差会切断接触图，只剩东西通道。

新闭合保留4个面邻居、原换热参数、点燃阈值和水分。全部燃料、覆盖率、DEM及辐射边数组与旧版逐字节一致。DEM继续影响辐射，接触项本身没有独立的坡度加速效应；连续坡面假设不解析格内空隙、崖壁或真实火焰流动。当前仍为5米网格的合成演示配置，未实测校准。未指定此可选配置的旧案例保持原行为。

沿用160,152起火点进行全图30分钟计算，累计引燃178格、燃尽37格，向上下左右各到达7格。首个受火结构B0120在1360秒（22分40秒）点燃；人工标签为`fengyuqiao`风雨桥，30分钟内尚未点燃普通民居。不可把结构索引统计直接解释为民居数量。全图与41×41裁剪的引燃集合一致；1秒与0.5秒时间步均引燃178格，匹配点燃时刻最大差2.5秒，燃料消耗相差约0.23%，能量检查通过。

重现对照并复用旧案例生成新完整案例（两个输出目录均须尚不存在）：

```bash
.venv/bin/python scripts/audit_batuan_contact_geometry.py \
  --output runs/batuan-geometry-audit-new --prepared-output runs/batuan-slope-contact-new
```

正式报告为 `runs/batuan-contact-geometry-audit-20261016/report.json`。新起火点、时长重算会继承案例接触配置；结果保存至新案例目录的`interactive`。也可从原始影像与标注重新生成：

```bash
.venv/bin/python scripts/prepare_batuan_simulation.py --output runs/batuan-slope-contact-new \
  --end-time-s 1800 --ignition-row 160 --ignition-col 152
```

网页重新选择相邻林木格160,151，运行60分钟的完整验证结果为`interactive/bd9190cd7eaa41489c5772db56759dc5`：累计引燃681格，435格燃尽；3栋普通建筑和1座风雨桥已点燃。风雨桥B0120首次点燃1515秒，普通建筑B0061、B0063、B0062分别在3016秒、3242秒、3498秒首次点燃。此验证使用了不同起火点，不能直接与上述160,152的30分钟计数作速度比较。界面将结构统计标为“建筑/桥”，避免把风雨桥计作民居。

## 岜团村中心高程接触版（历史对照）

```bash
.venv/bin/python scripts/start_batuan_simulation.py --prepared runs/batuan-contact-20261016 --no-browser
```

此历史对照加载 `runs/batuan-contact-20261016`，地址为 `http://127.0.0.1:8851/`。已修复辐射高程接线，并启用近场火焰接触传热。案例使用 `examples/village/vegetation_v3/flame-contact-batuan-5m.synthetic.yml` 的显式 5 米演示配置：气体温度 1100 K，换热系数 50 W/(m²·K)，接触比例 0.5；不更改原始 2 米配置，不降低点燃阈值或修改燃料水分。5 米网格的传播尺度尚未实测校准。

初始回放沿用用户运行 `92f313d1937f497d8ab023c46af97a8e` 的 160,152 起火点，时长 30 分钟，全图累计点燃 13 格、燃尽 7 格，接触传热累计约 1.104 GJ。输入的人工建筑、地表分类、燃料参数和真实 DEM 与原案例一致。原始案例及旧回放仍在原目录保留。新模拟结果保存至本目录 `interactive`；重新选点和设置时长时，求解器继承案例中的接触配置。

重新生成可运行案例：

```bash
.venv/bin/python scripts/prepare_batuan_simulation.py --output runs/batuan-contact-new \
  --end-time-s 1800 --ignition-row 160 --ignition-col 152 \
  --flame-contact-config examples/village/vegetation_v3/flame-contact-batuan-5m.synthetic.yml
```

可用 `--flame-contact-config` 指定另一份明确参数文件，或用 `--no-flame-contact` 生成辐射对照。建模时检查网格是否超过配置上限。交互界面顶部显示“近场火焰接触换热”，案例和回放 manifest 均记录实际配置及来源。

## 岜团村传播诊断（2026-10-16）

用户运行 `92f313d1937f497d8ab023c46af97a8e` 在 160,152 的林木格起火，100 分钟仅点燃起火格；火源在 860 秒燃尽。邻近可燃格与辐射边均存在，不是图层隐藏或未知地表阻断。

排查发现岜团村生成器曾把 `source_height_m + elevation` 传给辐射图构造器，而该函数内部还会乘以 0.5 得到火焰中心，使约 270 米海拔被错误减半。相邻受体仍使用完整海拔，导致虚假的百米级垂直距离。现已将地形作为独立 `ground_elevation_m` 参数传入，以 `z_target - z_source + receiver_height - half_flame*cos(tilt)` 计算相对距离；增加高程基准平移、坡度及有风参考实现一致性回归测试。该修复适用于新构建的图，旧案例和结果不自动改写。

第二个问题是岜团村的 5 米案例未启用高友村采用的近场火焰接触模块。复现命令 `.venv/bin/python scripts/audit_batuan_spread.py`，结果 `runs/batuan-spread-audit-20261016/ablations.json`。在同一起火点周围 21×21 格、1800 秒的独立诊断中：原图点燃 1 格，修复辐射高度仍 1 格，加入接触后 13 格；保留错误辐射但加入接触为 11 格。相邻格峰值辐射从 0.00934 恢复到 3.383 kW/m²，纯辐射最高邻格温度从 300.205 K 恢复至 370.934 K，仍低于林木 540 K 的引燃阈值。修复辐射后仅去掉水分仍为 1 格、最高 396.711 K，说明水分不是唯一限制。

5 米接触对照显式放宽了诊断网格上限，随后按用户要求作为独立演示配置启用（见上节），未做实测校准。现有接触配置原上限为 2 米；应在较细网格上恢复该机制、保留真实地形和合成燃料参数，做尺度与受体高度敏感性验证后再替换主案例。平地接触对照为 180 格，与真实地形 13 格的差异表明接触几何对坡地敏感；这些是机制诊断计数，不是实测传播速度或验证后的灾害预测。

## 岜团村地表推测补全版（历史辐射对照）

```bash
.venv/bin/python scripts/start_batuan_simulation.py --prepared runs/batuan-inferred-20261016 --no-browser
```

历史辐射对照案例为 `runs/batuan-inferred-20261016`，地址仍为 `http://127.0.0.1:8851/`。保留 332 栋建筑及 1 座风雨桥人工轮廓，在有效影像范围内通过暗绿色色度和固定邻域类别补全原未知区域。新增可燃林木/灌丛 17,270 格、草地/农田 605 格；3,729 格推测为裸土/硬化地表并采用不燃假设。原先的 21,604 个未分类非建筑元胞均已赋予推测类别，`unknown_policy=error` 确保不会把未分类区域静默排除。

材质索引 6、7、8 分别标记补全的林木、草地和不燃地表，材质名称含“推测补全”。人工建筑仍为索引 5，不因地表补全改变轮廓或面积。分类目录保留 `original_landcover_index.npy`、`inferred_mask.npy`、`inference_reason.npy` 和 `inference_reason.tif`；`classification_report.json` 记录规则、数量和局限。此过程是 RGB/邻域推测，没有实测分类精度，不推断真实建筑构造或实测燃料量。

重新生成默认补全版使用 `scripts/prepare_batuan_simulation.py --output <新目录>`，需要安装 `village-vision` 依赖；添加 `--keep-unknown` 可复现严格规则保留未知的版本。旧人工标注案例和模拟结果保留在 `runs/batuan-manual-20261016`。

## 岜团村人工标注版（2026-10-16）

```bash
.venv/bin/python scripts/start_batuan_simulation.py --prepared runs/batuan-manual-20261016 --no-browser
```

打开 `http://127.0.0.1:8851/`，点击建筑或植被的可燃格，再设为起火点并开始新模拟。此历史案例为 `runs/batuan-manual-20261016`，新模拟保存至该目录的 `interactive` 子目录。

人工输入是 `data/local/imagery/batuan-mosaic-20261014/batuan-composite-utm49n.json`，与同名 TIFF 的 3228 × 2903 像素一致。332 个建筑与 1 个 `fengyuqiao` 风雨桥均导入，不使用自动轮廓。两处自相交在导入副本中用 `make_valid` 修复，保留全部多边形部分；原 JSON 不改写，修复和覆盖记录在 `buildings/annotation_report.json`。所有与人工轮廓相交的有效元胞按真实交叠面积设置覆盖率，不再丢弃小于 15% 覆盖的边界元胞。

轮廓为人工标注，建筑和风雨桥暂用同一合成等效建筑燃料，不能视为已识别木材、砖混等真实构造。植被仍按 RGB 推测；裸土/硬化/水体按不燃假设，未知地表排除。使用 5 米计算网格和本地约 30 米 FABDEM；DEM 参与辐射边和求解器地形数据，不因重采样变成 5 米实测地形。默认无风和合成燃烧参数不代表现场天气或校准结果。

重新生成案例（选择尚不存在的输出目录）：

```bash
.venv/bin/python scripts/prepare_batuan_simulation.py --output runs/batuan-manual-new
.venv/bin/python scripts/start_batuan_simulation.py --prepared runs/batuan-manual-new
```

生成器可用 `--annotations` 指定新 LabelMe JSON，用 `--cell-size-m` 和 `--end-time-s` 设置网格与初始模拟时长。生成过程中校验影像尺寸、轮廓有效性、DEM 无缺值、燃烧能量守恒和辐射资源限制。地图图像直接从 TIFF 生成，不依赖先前的底图预览目录。

在项目根目录运行：

```bash
conda run --no-capture-output -n pyfireca-village pyfireca village simulate-v3 \
  runs/gaoyou-vegetation-v3/v12-gaoyou-terrain-coupled-full-domain-case \
  --initial-run runs/gaoyou-vegetation-v3/v12-gaoyou-terrain-coupled-full-domain-run \
  --output-root runs/gaoyou-interactive \
  --port 8812
```

浏览器打开 `http://127.0.0.1:8812/`。点击地图中的建筑或植被格，按“将所选格设为起火点”，输入模拟时长（分钟），再按“开始新模拟”。计算完成后会自动进入新回放；每次运行的案例和结果分别保存在 `runs/gaoyou-interactive/<作业编号>/case` 和 `run`。

时长范围为 1 秒至 500 分钟。起火点必须是案例中的已知可燃格。页面会显示运行状态，计算期间保持浏览器页面和命令进程打开。DEM 地形耦合沿用 V12 案例已生成的辐射边和高程数组；改变起火点或时长不会改变材料、天气、地形和辐射边。V12 材料及 RGB 地类仍属于演示假设。

## 岜团村底图与地表分类（2026-10-14）

```bash
.venv/bin/python scripts/start_batuan_background.py
```

打开 `http://127.0.0.1:8851/`。此模式支持原图、地表候选分类切换、缩放、拖动和投影坐标查询；不加载高友村模拟数据。现有 `8852` 预览页面使用同一份岜团村数据。

`scripts/prepare_batuan_mosaic.py` 从 `/Users/tone/Desktop/new_satellite` 拼接高清 TIFF，去除重复影像。输出位于 `data/local/imagery/batuan-mosaic-20261014`：`batuan-highres-3857.tif` 保留原始坐标系和高清像元；`batuan-highres-utm49n.tif` 是米制投影版本，缺少高清影像的区域透明；`batuan-composite-utm49n.tif` 在高清覆盖外用之前保存的低分辨率影像补齐。`source_resolution.tif` 记录来源精度（0 无数据，1 原图补齐，2 高清影像），`mosaic_report.json` 记录源文件、去重和覆盖统计。拼接图覆盖这些分块的外接矩形，不是此前大范围 TIFF 的全部范围。

`runs/batuan-landcover-20261014` 保存 2 米网格的地表候选分类 GeoTIFF、NumPy 数组和分类报告。采用可检查的 RGB 颜色规则；林木/灌丛、草地/农田、裸土、硬化地表/屋顶、水体均为候选类别，阴影及未匹配像元保留未知。没有真实材质精度验证，不生成燃料量、水分、燃烧参数或地形，也不把未知地表当作不燃。换影像后须重新生成分类，启动器检查分类源影像路径。
