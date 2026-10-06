# 高友村 V3 交互模拟

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
