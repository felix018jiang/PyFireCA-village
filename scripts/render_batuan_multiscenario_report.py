"""Inspectable scenario matrix, real-imagery layouts and transparent live progress."""

# ruff: noqa: E501, RUF001
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.windows import from_bounds
from render_batuan_intervention_report import geographic_feature
from run_batuan_adaptive_search import digest, dump
from shapely.geometry import shape

LABELS = {
    "baseline": "无防火线",
    "previous_outer_proxy": "上一轮村外 1019㎡",
    "previous_inner_proxy": "上一轮村内 544㎡",
    "outer_proxy": "本轮村外候选",
    "inner_proxy": "本轮村内候选",
}
STYLE = """
:root{color-scheme:light;--ink:#182a30;--teal:#067e76;--red:#a32d32}
*{box-sizing:border-box}body{margin:0;background:#f4f6f4;color:var(--ink);font:15px/1.65 system-ui,-apple-system,sans-serif}
main{max-width:1240px;margin:auto;padding:32px 24px 72px}h1{font-size:30px;line-height:1.3;margin:8px 0 16px}
h2{font-size:22px;margin:30px 0 14px}p{max-width:1040px}a{color:#006f69;text-underline-offset:3px}
.kicker{color:var(--teal);font-size:13px;letter-spacing:1px}.card{background:white;border:1px solid #dbe3df;border-radius:10px;padding:20px;margin:20px 0}
.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:9px 8px;border-bottom:1px solid #e5ebe8;text-align:center;white-space:nowrap}th{background:#edf3ef}th:first-child,td:first-child{text-align:left}
.pass{color:#087064;background:#effaf5}.fail{color:var(--red);background:#fff1ee}.subtle{color:#586a6d;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.grid img{width:100%;display:block}figure{margin:0}figcaption{padding:8px 0}code{font-size:12px;overflow-wrap:anywhere}
.tag{display:inline-block;border:1px solid #c6d8d0;border-radius:4px;padding:2px 8px;color:#12645c}nav{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}
@media(max-width:700px){main{padding:24px 14px}h1{font-size:24px}.card{padding:14px}.grid{grid-template-columns:1fr}th,td{padding:8px 6px}table{font-size:12px}}
"""


def maps(study, report):
    manifest = json.loads((study / "search-case/manifest.json").read_text())
    grid, meta = manifest["grid"], manifest["metadata"]
    size = grid["cell_size_m"]
    left, top = grid["x_min_m"], grid["y_max_m"]
    right, bottom = left + grid["width"] * size, top - grid["height"] * size
    width, height = 810, round(810 * grid["height"] / grid["width"])
    source = next(
        Path(p) for p in meta["sources_sha256"] if p.endswith("batuan-composite-utm49n.tif")
    )
    with rasterio.open(source) as raster:
        if str(raster.crs) != grid["crs"]:
            raise ValueError("imagery CRS mismatch")
        rgb = raster.read(
            [1, 2, 3],
            window=from_bounds(left, bottom, right, top, raster.transform),
            out_shape=(3, height, width),
            resampling=rasterio.enums.Resampling.bilinear,
        )
    background = Image.fromarray(rgb.transpose(1, 2, 0)).convert("RGBA")
    buildings = json.loads((Path(meta["spatial_assets_dir"]) / "buildings.json").read_text())[
        "buildings"
    ]

    def xy(p):
        return ((p[0] - left) / (right - left) * width, (top - p[1]) / (top - bottom) * height)

    output = study / "layouts"
    output.mkdir(exist_ok=True)
    font = ImageFont.load_default(size=14)
    links = {}
    for label in ("outer_proxy", "inner_proxy", "previous_outer_proxy", "previous_inner_proxy"):
        plan = report["plans"][label]
        record = report["evaluations"][label]["training"]["records"][0]
        treatment = record["intervention"]
        footprint = shape(treatment["effective_footprint"])
        overlay = Image.new("RGBA", background.size)
        brush = ImageDraw.Draw(overlay)
        polygons = [footprint] if footprint.geom_type == "Polygon" else list(footprint.geoms)
        for polygon in polygons:
            if polygon.geom_type != "Polygon":
                continue
            brush.polygon([xy(p) for p in polygon.exterior.coords], fill=(20, 224, 195, 140))
            for hole in polygon.interiors:
                brush.polygon([xy(p) for p in hole.coords], fill=(0, 0, 0, 0))
        image = Image.alpha_composite(background, overlay)
        brush = ImageDraw.Draw(image)
        for building in buildings:
            for polygon in building["polygons"]:
                brush.line([xy(p) for p in polygon["exterior"]], fill=(255, 255, 255, 180), width=1)
        for path in plan["paths"]:
            brush.line([xy(p) for p in path["points_m"]], fill=(20, 255, 220), width=2)
        for role, key, color in (
            ("T", "training", (255, 110, 60)),
            ("V", "held_out", (70, 175, 255)),
        ):
            cells = {tuple(s["parent_cell"]): s["points_m"][0] for s in report["protocol"][key]}
            for i, point in enumerate(cells.values()):
                x, y = xy(point)
                brush.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
                brush.text(
                    (x + 5, y + 3),
                    f"{role}{i + 1}",
                    font=font,
                    fill=color,
                    stroke_width=1,
                    stroke_fill=(0, 0, 0),
                )
        filename = label + ".jpg"
        image.convert("RGB").save(output / filename, quality=93)
        properties = {
            "label": label,
            "cleared_area_m2": treatment["cleared_area_m2"],
            "evidence": "hypothetical_not_surveyed",
            "source_crs": grid["crs"],
            "design_goal_met": report["evaluations"][label]["training"]["screen"][
                "protection_goal_met"
            ],
            "held_out_goal_met": report["evaluations"][label]["held_out"]["screen"][
                "protection_goal_met"
            ],
        }
        dump(
            output / (label + "-clearing.geojson"),
            geographic_feature(treatment["effective_footprint"], properties, grid["crs"]),
        )
        lines = [
            geographic_feature(
                {"type": "LineString", "coordinates": p["points_m"]},
                {**properties, "width_m": p["width_m"], "path_index": i},
                grid["crs"],
            )
            for i, p in enumerate(plan["paths"])
        ]
        dump(
            output / (label + "-centerlines.geojson"),
            {"type": "FeatureCollection", "features": lines},
        )
        links[label] = "layouts/" + filename
    return links


def cell(record):
    metrics = record["metrics"]
    houses, bridges = metrics["ignited_house_count"], metrics["ignited_bridge_count"]
    kind = "fail" if houses + bridges else "pass"
    return f'<td class="{kind}">{houses} / {bridges}</td>'


def summary(evaluation):
    rows = evaluation["records"]
    expected = evaluation["screen"]["expected_scenario_ids"]
    if len(rows) != len(expected) or [r["scenario_id"] for r in rows] != expected:
        raise ValueError("published comparison requires every declared scenario in order")
    passes = sum(
        r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"] == 0
        for r in rows
    )
    kind = "pass" if passes == len(rows) else "fail"
    return f'<td class="{kind}">{passes} / {len(rows)}</td>'


def render(study):
    study = Path(study)
    protocol = json.loads((study / "protocol.json").read_text())
    body = '<div class="kicker">PYFIRECA · 岜团村 · 90 MIN · 2026-10-17</div>'
    body += "<h1>多起火点 × 风况 × 材质参数<br>保护约束下的防火线几何优化</h1>"
    body += "<p>先满足所有设计情景中民居与风雨桥均不引燃，再减少植被实际清理面积。分别搜索建筑外侧与建筑近邻；保留全部建筑与跨带辐射。</p>"
    body += '<nav><a href="#comparison">结果对照</a><a href="#layouts">卫星布局</a><a href="#matrix">逐情景结果</a><a href="#scope">模型与版本</a><a href="protocol.json">冻结协议 JSON</a></nav>'
    if not (study / "report.json").exists():
        records = list((study / "evaluation-cache").glob("*.json"))
        body += f'<section class="card"><span class="tag">计算进行中，尚无最终结论</span><p>已保存 {len(records)} 条物理评价记录（含签名核对后的设计缓存复用）。每区 2 个搜索种子，每次最多64个候选请求；每个达标候选必须通过全部24个设计情景。8个留出情景待选线冻结后评价。</p></section>'
        for p in sorted(study.glob("*history*.jsonl")):
            rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
            evaluated = [r for r in rows if r["event"] == "evaluation"]
            passed = [r for r in evaluated if r.get("effectiveness", [1])[0] == 0]
            body += f"<p><code>{html.escape(p.name)}</code>：已完成{len(evaluated)}次请求，{len(passed)}个全设计情景达标记录。</p>"
        refresh = '<meta http-equiv="refresh" content="20">'
    else:
        report = json.loads((study / "report.json").read_text())
        selection = json.loads((study / "selection.json").read_text())
        if (
            digest(selection) != report["selection_sha256"]
            or digest(protocol) != selection["protocol_sha256"]
        ):
            raise ValueError("frozen selection checksum mismatch")
        refresh = ""
        body += '<section class="card" id="comparison"><h2>保护与面积对照</h2><p class="subtle">通过数指90分钟内民居和桥均未引燃的情景数。设计24项用于搜索；留出8项在选线冻结后评价，不用于重选。全是确定性压力测试，不解释为真实世界成功概率。</p><div class="scroll"><table><tr><th>方案</th><th>清理㎡</th><th>设计通过</th><th>留出通过</th></tr>'
        order = (
            "baseline",
            "previous_outer_proxy",
            "outer_proxy",
            "previous_inner_proxy",
            "inner_proxy",
        )
        for label in order:
            evaluation = report["evaluations"][label]
            area = evaluation["training"]["records"][0]["intervention"]["cleared_area_m2"]
            body += (
                f"<tr><td>{LABELS[label]}</td><td>{area:,.2f}</td>"
                + summary(evaluation["training"])
                + summary(evaluation["held_out"])
                + "</tr>"
            )
        body += '</table></div><p><a href="report.json">全部原始指标</a> · <a href="selection.json">验证前冻结选线</a></p></section>'
        if (study / "full-validation.json").exists():
            full = json.loads((study / "full-validation.json").read_text())
            body += '<section class="card"><h2>完整场地复核</h2>'
            for label, row in full["plans"].items():
                body += f"<p>{LABELS[label]}：完整312×347网格，{row['passed']}/{row['total']}项零引燃；局部/完整引燃指标一致：{row['crop_full_match']}。</p>"
            body += "</section>"
        else:
            body += '<p class="subtle">当前表为140×140扩展域结果；完整场地复核如未完成，不据此宣称全村全部建筑已获保护。</p>'
        links = maps(study, report)
        body += '<section id="layouts"><h2>真实卫星影像上的候选布局</h2><p class="subtle">青色为实际可清理植被；白线为人工建筑标注，橙点T为设计火源，蓝点V为留出火源。面积按剔除建筑、不燃地表和区外地表后的交集计算。</p><div class="grid">'
        for label in ("outer_proxy", "inner_proxy", "previous_outer_proxy", "previous_inner_proxy"):
            body += f'<figure><img src="{links[label]}" alt="{LABELS[label]}卫星清理布局"><figcaption>{LABELS[label]} · <a href="layouts/{label}-clearing.geojson">清理面GIS</a> · <a href="layouts/{label}-centerlines.geojson">中线与宽度GIS</a></figcaption></figure>'
        body += '</div></section><section class="card" id="matrix"><h2>逐情景引燃数量</h2><p class="subtle">每格为“民居 / 风雨桥”；任一非零即未达标。风向均为来向，点燃温升系数0.95表示温升降低5%。</p>'
        for suite, title in (("training", "24项设计情景"), ("held_out", "8项未见情景")):
            body += (
                f'<h3>{title}</h3><div class="scroll"><table><tr><th>火源 · 风况 · 温升系数</th>'
                + "".join(f"<th>{LABELS[label]}</th>" for label in order)
                + "<th>清理㎡</th></tr>"
            )
            for i, row in enumerate(protocol[suite]):
                env = protocol["environments"][row["environment_id"]]
                r, c = row["parent_cell"]
                name = f"({r},{c}) · {env['wind_speed_m_s']:g}m/s/{env['wind_from_deg']:g}° · {env['ignition_rise_factor']:g}"
                body += (
                    f"<tr><td>{name}</td>"
                    + "".join(
                        cell(report["evaluations"][label][suite]["records"][i]) for label in order
                    )
                    + "<td>同方案固定</td></tr>"
                )
            body += "</table></div>"
        body += "</section>"
        if (study / "replays.json").exists():
            body += '<section class="card"><h2>90分钟回放</h2><ul>'
            for row in json.loads((study / "replays.json").read_text()):
                body += f'<li><a href="{row["viewer"]}/">{html.escape(row["label"])}</a> · {html.escape(row["scenario_id"])}</li>'
            body += "</ul></section>"
    body += '<section class="card" id="scope"><h2>如何解释本轮结果</h2><p>已有V3风致辐射源倾斜闭合实际参与传热：θ=atan(0.2×风速)，并重新构建辐射图。0.2s/m与风况是合成压力测试；未增加风驱接触或飞火。原基准案例与参数未改。</p><p>5m元胞中的部分清理使用剩余燃料均匀缩放；本轮“村内”是建筑20m近邻的植被清理，未模拟拆房或木结构改造。模型尚未经本村实测校准，有限搜索无全局最优证明，结果不能直接作为施工标准。</p><p>提前发现一次保护失败即可淘汰该候选；未算完的损失只是下界。仅全部24项完成且为零才能计作达标。最终对照表已补齐每个方案全部32项。</p><p>更新前回退标签：<code>batuan-before-multiscenario-20261017</code> → <code>0de2e37</code>。旧报告：<a href="http://127.0.0.1:8856/">上一轮90分钟对照</a>。</p></section>'
    page = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{refresh}<title>岜团村多情景防火线优化</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>'
    (study / "index.html").write_text(page)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("study", type=Path)
    render(p.parse_args().study)
