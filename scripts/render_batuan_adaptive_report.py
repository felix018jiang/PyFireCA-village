"""Inspect adaptive search histories, area/protection tradeoffs and real CA maps."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds

LABELS = {
    "baseline": "无干预",
    "legacy_outer_proxy": "原24候选：外围最优",
    "legacy_inner_proxy": "原24候选：建筑近邻最优",
    "adaptive_outer_proxy": "迭代优化：外围区",
    "adaptive_inner_proxy": "迭代优化：建筑近邻区",
}


def tradeoff_svg(search):
    records = [r for r in search["records"] if r["status"] == "ok"]
    xmax = max(1, max(r["cleared_area_m2"] for r in records))
    ymax = max(1, max(r["effectiveness"][0] for r in records))
    circles = []
    for r in records:
        x, y = 50 + r["cleared_area_m2"] / xmax * 500, 235 - r["effectiveness"][0] / ymax * 190
        selected = r["plan_id"] in search["selected_ids"]
        pareto = r["plan_id"] in search["pareto_ids"]
        color = "#f8bf65" if selected else ("#54dfca" if pareto else "#687e90")
        tip = html.escape(
            f"{r['plan_id']}: area={r['cleared_area_m2']:.2f} m²; "
            f"effectiveness={r['effectiveness']}"
        )
        circles.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{6 if selected else 4}" fill="{color}"><title>{tip}</title></circle>'
        )
    return f"""<svg viewBox="0 0 600 290" role="img" aria-label="清理面积和最差情景受火民居的比较">
    <path d="M50 30V235H570" fill="none" stroke="#97a9b5"/>
    <text x="50" y="20" fill="#d7e3eb" font-size="13">最差情景受火民居（完整排序含另外4项）</text>
    <text x="45" y="255" fill="#aabecb" font-size="12">0</text>
    <text x="8" y="45" fill="#aabecb" font-size="12">{ymax:g}</text>
    <text x="440" y="273" fill="#aabecb" font-size="13">清理面积 ≤ {xmax:.0f} m²</text>
    {"".join(circles)}</svg>"""


def map_image(study, entry, filename, report):
    plan_id = entry["evaluation"]["intervention_id"]
    root = study / "full" / plan_id
    manifest = json.loads((root / "case/manifest.json").read_text())
    grid, meta = manifest["grid"], manifest["metadata"]
    r0, r1, c0, c1 = report["protocol"]["window"]
    size = grid["cell_size_m"]
    left, right = grid["x_min_m"] + c0 * size, grid["x_min_m"] + c1 * size
    top, bottom = grid["y_max_m"] - r0 * size, grid["y_max_m"] - r1 * size
    width, height = 810, round(810 * (r1 - r0) / (c1 - c0))
    source = next(
        Path(p) for p in meta["sources_sha256"] if p.endswith("batuan-composite-utm49n.tif")
    )
    with rasterio.open(source) as image:
        if str(image.crs) != grid["crs"]:
            raise ValueError("imagery and simulation CRS differ")
        rgb = image.read(
            [1, 2, 3],
            window=from_bounds(left, bottom, right, top, image.transform),
            out_shape=(3, height, width),
            resampling=rasterio.enums.Resampling.bilinear,
        )
    background = Image.fromarray(rgb.transpose(1, 2, 0)).convert("RGBA")
    state = np.load(root / "run/outputs/state_final.npy")
    fraction = np.load(root / "cleared_fraction.npy")
    overlay = Image.new("RGBA", background.size)
    draw = ImageDraw.Draw(overlay)
    colors = {2: (222, 180, 57, 100), 3: (239, 79, 38, 175), 4: (48, 35, 30, 160)}
    for r in range(r0, r1):
        for c in range(c0, c1):
            rect = (
                (c - c0) * width / (c1 - c0),
                (r - r0) * height / (r1 - r0),
                (c - c0 + 1) * width / (c1 - c0),
                (r - r0 + 1) * height / (r1 - r0),
            )
            if int(state[r, c]) in colors:
                draw.rectangle(rect, fill=colors[int(state[r, c])])
            if fraction[r, c] > 0:
                draw.rectangle(rect, fill=(28, 225, 220, int(155 * fraction[r, c])))
    image = Image.alpha_composite(background, overlay)
    draw = ImageDraw.Draw(image)

    def xy(point):
        return (
            (point[0] - left) / (right - left) * width,
            (top - point[1]) / (top - bottom) * height,
        )

    structures = json.loads((Path(meta["spatial_assets_dir"]) / "buildings.json").read_text())[
        "buildings"
    ]
    for structure in structures:
        for polygon in structure["polygons"]:
            draw.line([xy(p) for p in polygon["exterior"]], fill=(255, 255, 255, 180), width=1)
    for path in entry["evaluation"]["intervention"].get("paths", []):
        draw.line([xy(p) for p in path["points_m"]], fill=(82, 250, 238, 240), width=2)
    image.convert("RGB").save(study / filename, quality=93)


def render(study):
    study = Path(study).resolve()
    report = json.loads((study / "report.json").read_text())
    if report["status"] not in ("completed", "completed_no_replays"):
        raise ValueError("report is not complete")
    rows, cards, sections = [], [], []
    for label, records in report["controls"].items():
        score = [r["metrics"]["ignited_house_count"] for r in records]
        area = records[0]["intervention"]["cleared_area_m2"]
        held = report["validation"][label]["held_out"]["metrics"]["ignited_house_count"]
        extended = report["validation"][label]["extended"]["metrics"]["ignited_house_count"]
        paths = len(report["chosen_plans"][label]["paths"])
        rows.append(
            f"<tr><td>{LABELS[label]}</td><td>{paths}</td><td>{score}</td><td>{area:.2f}</td><td>{held}</td><td>{extended}</td></tr>"
        )
    for label, entry in report["full_domain_comparison"].items():
        if not entry["viewer"]:
            continue
        filename = label + ".jpg"
        map_image(study, entry, filename, report)
        m = entry["evaluation"]["metrics"]
        plan_id = entry["evaluation"]["intervention_id"]
        cards.append(
            f'<article><h2>{LABELS[label]}</h2><p>完整网格：{m["ignited_house_count"]} 栋民居受火，{m["ignited_bridge_count"]} 座桥受火；建筑受火面积 {m["ignited_building_area_m2"]:.1f} m²。</p><img src="{filename}" alt="卫星底图上的实际火灾求解与清理区域"><p>青色：清理元胞；细青线：提出路径。橙色：燃烧；深色：燃尽；黄色：受热；白线：建筑。</p><a href="full/{plan_id}/run/viewer/">逐帧回放 →</a> · <a href="full/{plan_id}/intervention.geojson">干预范围 GeoJSON</a></article>'
        )
    for zone, search in report["searches"].items():
        title = "外围区" if zone == "outer_proxy" else "建筑近邻区"
        history_rows = "".join(
            f"<tr><td>{h['generation']}</td><td>{h['evaluations']}</td><td>{h['step_m']:g}</td><td>{html.escape(str(h['best_effectiveness']))}</td><td>{h['best_cleared_area_m2']:.2f}</td></tr>"
            for h in search["history"]
        )
        front = [r for r in search["records"] if r["plan_id"] in search["pareto_ids"]]
        pareto_rows = "".join(
            f"<tr><td>{html.escape(r['plan_id'])}</td><td>{html.escape(str(r['effectiveness']))}</td><td>{r['cleared_area_m2']:.2f}</td><td>{len(r['plan']['paths'])}</td></tr>"
            for r in sorted(front, key=lambda r: r["cleared_area_m2"])
        )
        feasible = sum(r["status"] == "ok" for r in search["records"])
        sections.append(
            f"<section><h2>{title}：迭代与面积—保护效果比较</h2><p>{len(search['records'])} 次唯一方案请求，{feasible} 个通过物理/边界检查，{search['geometric_rejections']} 次几何提案被过滤；停止原因 {search['stop_reason']}。</p>{tradeoff_svg(search)}<p>黄色：最终选中；青色：已访问方案中的帕累托解；灰色：其他已评价方案。悬停查看完整指标。图上仅投影显示第一个防火指标。</p><table><tr><th>代数</th><th>累计请求</th><th>步长 m</th><th>当前最优防火指标</th><th>面积 m²</th></tr>{history_rows}</table><details><summary>展开已访问方案中的效果—面积帕累托表</summary><table><tr><th>方案</th><th>完整防火指标</th><th>面积 m²</th><th>路径数</th></tr>{pareto_rows}</table></details></section>"
        )
    config = report["protocol"]["config"]
    minutes = report["protocol"]["training"][0]["horizon_s"] / 60
    long_minutes = report["protocol"]["extended_horizon_s"] / 60
    objective = (
        "先按五级防火指标排序，同等效果下选最小实际清理面积"
        if config["objective"] == "effectiveness_then_area"
        else "只按五级防火指标排序；面积仅记录"
    )
    content = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>岜团村 · 折线与多带迭代优化</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#111b23;color:#e6edf3;font:15px/1.7 system-ui,sans-serif}}main{{max-width:1440px;padding:28px;margin:auto}}h1{{font-size:29px}}h2{{font-size:20px}}p{{color:#b9cad6}}a{{color:#54dfca}}table{{width:100%;border-collapse:collapse;font-size:14px;margin:18px 0}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #34444e}}section,article{{padding:22px;background:#1a2933;border:1px solid #34444e;margin:20px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}}img{{width:100%;display:block}}svg{{max-width:700px;width:100%}}summary{{cursor:pointer;color:#54dfca}}code{{color:#f8bf65}}.scroll{{overflow:auto}}
    </style><main><h1>岜团村：折线与多条防火线的迭代优化</h1><p>{objective}，不引入货币成本。每区最多 {config["max_paths"]} 条路径，每条最多 {config["max_vertices"]} 个节点，宽度 {config["width_min_m"]:g}–{config["width_max_m"]:g} 米。位置、方向、宽度、端点、转折点及组合均可调整；不是从原24条中仅做重新排序。</p><p>两搜索情景均为 {minutes:g} 分钟；选中方案冻结后再评估独立起火点和 {long_minutes:g} 分钟延时。最终选中方案在完整312×347网格上复核与回放；表格训练/留出/延时列来自声明的搜索域。清理面积按有效植被清理范围的并集计量。</p><div class="scroll"><table><tr><th>方案</th><th>路径数</th><th>两训练情景受火民居</th><th>清理面积 m²</th><th>留出受火民居</th><th>延时留出受火民居</th></tr>{"".join(rows)}</table></div><div class="grid">{"".join(cards)}</div>{"".join(sections)}<section><h2>本轮适用范围</h2><p>搜索域：<code>{report["protocol"]["search_domain"]}</code>，窗口 <code>{report["protocol"]["window"]}</code>。当前火源仍为既有案例的南侧林地局部压力测试；外围和建筑近邻使用20米建筑缓冲代理区，尚非调查村界。折线路径缓冲形成连续提出走廊，但有效清理受建筑、不燃地表和代理区裁剪，可能出现中断。当前无动态风、无飞火，燃料参数仍含合成假设。</p><p>这里的最优是本次计算预算内访问到的最好方案，帕累托前沿也仅针对已访问方案。即使训练时完全并列，留出和延时结果仍可能不同，验证结果未用于重新挑选方案。未声明全局最优或实地施工线位。</p></section><p><a href="report.json">完整结果 JSON</a> · <a href="protocol.json">试验协议</a> · <a href="history.jsonl">迭代日志</a> · <a href="outer_proxy-search.json">外围全部提案</a> · <a href="inner_proxy-search.json">近邻全部提案</a></p></main></html>"""
    (study / "index.html").write_text(content)
    print(study / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    render(parser.parse_args().study)
