"""Render an inspectable comparison from saved solver arrays and imagery."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.warp import transform_geom
from rasterio.windows import from_bounds


def geographic_feature(geometry, properties, source_crs):
    """RFC7946 coordinates plus an explicit native copy for metric calculations."""
    empty = geometry["type"] == "GeometryCollection" and not geometry["geometries"]
    return {
        "type": "Feature",
        "geometry": None if empty else transform_geom(source_crs, "EPSG:4326", geometry),
        "properties": {**properties, "geometry_crs": "EPSG:4326"},
        "projected_geometry": geometry,
        "projected_crs": source_crs,
    }


def render(study):
    study = Path(study).resolve()
    report = json.loads((study / "report.json").read_text())
    comparisons = report["full_domain_comparison"]
    minutes = report["protocol"]["training_scenarios"][0]["horizon_s"] / 60
    first_root = study / "full" / next(iter(comparisons))
    manifest = json.loads((first_root / "case/manifest.json").read_text())
    grid, metadata = manifest["grid"], manifest["metadata"]
    zone_path = study / "search-zones.geojson"
    zone = json.loads(zone_path.read_text())
    native_zone = zone.get("projected_geometry", zone["geometry"])
    zone_path.write_text(
        json.dumps(
            geographic_feature(native_zone, {"status": "proxy"}, grid["crs"]),
            ensure_ascii=False,
            indent=2,
        )
    )
    source = next(
        Path(p) for p in metadata["sources_sha256"] if p.endswith("batuan-composite-utm49n.tif")
    )
    structures = json.loads((Path(metadata["spatial_assets_dir"]) / "buildings.json").read_text())[
        "buildings"
    ]
    r0, r1, c0, c1 = 132, 186, 123, 181
    size = grid["cell_size_m"]
    x0, x1 = grid["x_min_m"] + c0 * size, grid["x_min_m"] + c1 * size
    y0, y1 = grid["y_max_m"] - r1 * size, grid["y_max_m"] - r0 * size
    width, height = 696, 648
    with rasterio.open(source) as image:
        if str(image.crs) != grid["crs"]:
            raise ValueError("imagery and simulation CRS differ")
        data = image.read(
            [1, 2, 3],
            window=from_bounds(x0, y0, x1, y1, image.transform),
            out_shape=(3, height, width),
            resampling=rasterio.enums.Resampling.bilinear,
        )
    background = Image.fromarray(data.transpose(1, 2, 0)).convert("RGBA")

    def xy(point):
        x, y = point
        return ((x - x0) / (x1 - x0) * width, (y1 - y) / (y1 - y0) * height)

    titles = {"no-clearing": "无防火线"}
    for zone, label in [("outer_proxy", "外围候选带"), ("inner_proxy", "建筑近邻候选带")]:
        titles[report["selected"][zone]["representative"]] = label
    single_id = report["single_strip_training"][0]["intervention_id"]
    titles[single_id] = "预先指定的单条清理带"
    cards, rows = [], []
    for plan_id, item in comparisons.items():
        root = study / "full" / plan_id
        state = np.load(root / "run/outputs/state_final.npy")
        fraction = np.load(root / "cleared_fraction.npy")
        overlay = Image.new("RGBA", background.size)
        draw = ImageDraw.Draw(overlay)
        colors = {2: (222, 180, 57, 100), 3: (239, 79, 38, 175), 4: (48, 35, 30, 180)}
        for rr in range(r0, r1):
            for cc in range(c0, c1):
                rect = (
                    (cc - c0) * width / (c1 - c0),
                    (rr - r0) * height / (r1 - r0),
                    (cc - c0 + 1) * width / (c1 - c0),
                    (rr - r0 + 1) * height / (r1 - r0),
                )
                color = colors.get(int(state[rr, cc]))
                if color:
                    draw.rectangle(rect, fill=color)
                if fraction[rr, cc] > 0:
                    draw.rectangle(rect, fill=(28, 225, 220, int(120 * fraction[rr, cc])))
        image = Image.alpha_composite(background, overlay)
        draw = ImageDraw.Draw(image)
        for structure in structures:
            for polygon in structure["polygons"]:
                draw.line([xy(p) for p in polygon["exterior"]], fill=(255, 255, 255, 190), width=1)
        point = xy((grid["x_min_m"] + 151.5 * size, grid["y_max_m"] - 160.5 * size))
        draw.ellipse(
            (point[0] - 6, point[1] - 6, point[0] + 6, point[1] + 6), outline=(255, 88, 55), width=2
        )
        image.convert("RGB").save(study / (plan_id + ".jpg"), quality=92)
        m = item["evaluation"]["metrics"]
        area = item["evaluation"]["intervention"]["cleared_area_m2"]
        treatment = item["evaluation"]["intervention"]
        properties = {
            k: v for k, v in treatment.items() if k not in {"effective_footprint", "strips"}
        }
        (root / "intervention.geojson").write_text(
            json.dumps(
                geographic_feature(treatment["effective_footprint"], properties, grid["crs"]),
                ensure_ascii=False,
                indent=2,
            )
        )
        title = titles[plan_id]
        delay = (
            "未引燃"
            if m["first_house_ignition_s"] is None
            else f"{m['first_house_ignition_s'] / 60:.2f} 分钟"
        )
        href = f"full/{plan_id}/run/viewer/"
        cards.append(f'''<article><h2>{html.escape(title)}</h2>
        <div class="numbers"><b>{m["ignited_house_count"]} 栋民居</b><span>{m["ignited_bridge_count"]} 座风雨桥</span>
        <span>{m["ignited_building_area_m2"]:.1f} m² 建筑受火面积</span></div>
        <img src="{html.escape(plan_id)}.jpg" alt="{html.escape(title)}，同一起火点{minutes:g}分钟模拟">
        <p>首次引燃民居：{delay}；清理面积 {area:.1f} m²（仅记录）。</p>
        <a href="{html.escape(href)}">打开逐帧回放 →</a></article>''')
        held = report["held_out"][plan_id]["metrics"]
        rows.append(
            f"<tr><td>{html.escape(title)}</td><td>{m['ignited_house_count']}</td>"
            f"<td>{m['ignited_building_area_m2']:.1f}</td><td>{held['ignited_house_count']}</td>"
            f"<td>{held['ignited_building_area_m2']:.1f}</td></tr>"
        )
    feasible = len(report["ranked"]) - 2
    longer = report.get("extended_horizon_validation", {})
    longer_rows = []
    for plan_id, record in longer.items():
        metrics = record["metrics"]
        longer_rows.append(
            f"<tr><td>{html.escape(titles[plan_id])}</td>"
            f"<td>{metrics['ignited_house_count']}</td>"
            f"<td>{metrics['ignited_building_area_m2']:.1f}</td></tr>"
        )
    longer_table = (
        (
            "<h2>留出情景延长至90分钟的局部域复核</h2>"
            "<p class='note'>保持已选方案，不重新搜索。三个方案均未触及81×81裁剪边界；"
            "本表与上方完整网格60分钟回放使用不同起火点和时限。</p>"
            "<table><tr><th>方案</th><th>受火民居</th><th>建筑受火面积 m²</th></tr>"
            + "".join(longer_rows)
            + "</table>"
        )
        if longer
        else ""
    )
    content = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>岜团村防火线有效性对照</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#111711;color:#eeeee4;font:15px/1.65 system-ui,sans-serif}}
    main{{max-width:1480px;margin:auto;padding:28px}}h1{{font-size:30px;margin:0 0 10px}}h2{{font-size:19px;margin:0 0 10px}}
    .intro{{color:#b8c2b2;max-width:1100px}}.badge{{color:#eac663;font-size:12px;letter-spacing:2px}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:24px}}article{{padding:18px;background:#1c231a;border:1px solid #3b4535}}
    img{{display:block;width:100%;margin:14px 0}}a{{color:#eac663}}.numbers{{display:flex;flex-wrap:wrap;gap:10px 20px}}
    .numbers b{{color:#f0c75c}}article p{{color:#bdc5b8;font-size:13px}}table{{width:100%;border-collapse:collapse;margin:20px 0}}
    td,th{{text-align:left;padding:10px;border-bottom:1px solid #3b4535}}.note{{color:#bdc5b8}}
    @media(max-width:800px){{main{{padding:16px}}.grid{{grid-template-columns:1fr}}table{{font-size:12px}}}}
    </style><main><div class="badge">PYFIRECA / INTERVENTION STUDY / R1 R2</div>
    <h1>岜团村防火线有效性对照</h1>
    <p class="intro">第一轮只比较防火效果，清理面积和经济成本不参与排序。下列地图使用同一起火点
    （第160行，第151列）及同一{minutes:g}分钟时限，全部来自完整岜团村网格的实际求解。青色为假设清理区域，
    橙色为燃烧，深色为燃尽，黄色为受热，白线为人工结构轮廓。</p>
    <p class="note">搜索在固定的150米单条候选带中改变位置、方向及5/10米宽度：{feasible}个可行搜索候选，
    2个训练起火情景和1个留出情景。外围/建筑近邻是以人工建筑20米缓冲区划分的试验区，未作为实测村界。
    候选带避开全部建筑和起火格；被建筑、天然不燃地表或试验区截断后可能不连续。</p>
    <table><thead><tr><th>方案</th><th>展示情景受火民居</th><th>建筑受火面积 m²</th>
    <th>留出情景受火民居</th><th>留出面积 m²</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
    <div class="grid">{"".join(cards)}</div>
    {longer_table}
    <p class="note">最优仅指已列出的有限候选与局部无风情景。燃烧参数为合成假设，近场接触采用连续坡面模型；
    辐射跨带传播保留，但半径截断为15米，尚无飞火。本轮结果不能确定强风森林火灾的施工线位或充分宽度。</p>
    <p><a href="report.json">完整评价与排名 JSON</a> · <a href="protocol.json">预先声明的试验范围与排序规则</a></p>
    </main></html>"""
    (study / "index.html").write_text(content)
    print(study / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    render(parser.parse_args().study)
