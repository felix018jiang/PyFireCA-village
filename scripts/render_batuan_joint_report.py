"""Inspectable satellite figures, GIS plans and a static research report."""
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
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import unary_union

from pyfireca.planning.joint import plan_from_mapping

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "data/local/imagery/batuan-mosaic-20261014/batuan-highres-utm49n.tif"


def load(path):
    return json.loads(Path(path).read_text())


def figure(bounds, buildings, *, selected=(), footprint=None, paths=(), destination):
    left, bottom, right, top = bounds
    # Square pixels in the displayed projected coordinate system.
    width = 700
    height = max(1, round(width * (top - bottom) / (right - left)))
    with rasterio.open(RASTER) as raster:
        if str(raster.crs) != "EPSG:32649":
            raise ValueError("report raster CRS mismatch")
        pixels = raster.read(
            [1, 2, 3],
            window=from_bounds(*bounds, raster.transform),
            out_shape=(3, height, width),
            boundless=True,
            fill_value=0,
        )
    canvas = Image.fromarray(np.moveaxis(pixels, 0, -1).astype("uint8")).convert("RGBA")
    overlay = Image.new("RGBA", canvas.size)
    draw = ImageDraw.Draw(overlay)

    def xy(points):
        return [
            ((x - left) / (right - left) * width, (top - y) / (top - bottom) * height)
            for x, y in points
        ]

    def polygons(geom, fill, outline):
        parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
        for polygon in parts:
            if polygon.geom_type != "Polygon":
                continue
            draw.polygon(xy(polygon.exterior.coords), fill=fill, outline=outline, width=2)
            for ring in polygon.interiors:
                draw.polygon(xy(ring.coords), fill=(0, 0, 0, 0))

    for key, polygon in buildings.items():
        if polygon.intersects(
            shape(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]
                    ],
                }
            )
        ):
            color = (255, 190, 40, 255) if key in selected else (255, 255, 255, 130)
            polygons(polygon, None, color)
            if key in selected:
                center = xy([(polygon.centroid.x, polygon.centroid.y)])[0]
                draw.text(
                    center,
                    key,
                    fill=(255, 235, 80, 255),
                    stroke_width=2,
                    stroke_fill=(0, 0, 0, 255),
                )
    if footprint is not None:
        polygons(footprint, (40, 200, 240, 90), (40, 230, 255, 255))
    for path in paths:
        draw.line(xy(path), fill=(255, 80, 110, 255), width=3)
    for key in selected:
        polygon = buildings[key]
        center = xy([(polygon.centroid.x, polygon.centroid.y)])[0]
        draw.text(center, key, fill=(255, 235, 80, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    draw.text((12, 12), "N ^   EPSG:32649", fill="white", stroke_width=2, stroke_fill="black")
    draw.text(
        (12, height - 22),
        f"width {right - left:.0f} m",
        fill="white",
        stroke_width=2,
        stroke_fill="black",
    )
    Image.alpha_composite(canvas, overlay).convert("RGB").save(destination)


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    pair_roots = [ROOT / f"runs/research/batuan-building-pairs-20261017-{r}" for r in ("r2", "r3")]
    a, b = [load(p / "report.json") for p in pair_roots]
    protocol = load(pair_roots[-1] / "protocol.json")
    structures = load(pair_roots[-1] / "buildings.source.json")["buildings"]
    polygons = {
        row["building_id"]: unary_union(
            [Polygon(p["exterior"], p["holes"]) for p in row["polygons"]]
        )
        for row in structures
    }
    cards = []
    for p in protocol["selected"]:
        geom = unary_union([polygons[k] for k in p["building_ids"]])
        x0, y0, x1, y1 = geom.bounds
        side = max(x1 - x0, y1 - y0) + 50
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        band = shape(
            next(
                r
                for r in b["records"]
                if r["pair_id"] == p["pair_id"] and r["method"] == "clear_10m" and r["dt_s"] == 1
            )["treatment"]["effective_footprint"]
        )
        png = p["pair_id"] + ".png"
        figure(
            (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2),
            polygons,
            selected=p["building_ids"],
            footprint=band,
            paths=[p["band_points_m"]],
            destination=output / png,
        )
        rows = []
        for profile, report in [("单格起火", a), ("源屋顶元胞同时起火", b)]:
            for source in p["owner_ids"]:
                values = []
                for method in ["pair_only", "mapped_ground", "clear_5m", "clear_10m"]:
                    r = next(
                        r
                        for r in report["records"]
                        if r["pair_id"] == p["pair_id"]
                        and r["source_owner"] == source
                        and r["method"] == method
                        and r["dt_s"] == 1
                    )
                    t = r["first_receiver_ignition_s"]
                    values.append("90分钟未引燃" if t is None else f"{t:.1f}秒")
                source_name = p["building_ids"][p["owner_ids"].index(source)]
                rows.append(
                    "<tr><td>"
                    + profile
                    + " / "
                    + source_name
                    + "</td>"
                    + "".join(f"<td>{x}</td>" for x in values)
                    + "</tr>"
                )
        cards.append(
            f"<article><h2>{p['pair_id']}</h2><p>轮廓间距 {p['polygon_gap_m']:.2f} m；"
            f"栅格最小中心距 {p['raster_min_center_distance_m']:.2f} m。"
            f"间隙地面是否被独立元胞解析：{p['gap_ground_resolved']}。</p>"
            f'<img src="{png}" alt="{p["pair_id"]}卫星定位与测试带">'
            "<p>黄色为选中建筑；蓝色为请求10米带与可清理地表的实际交集，红线为请求中线。建筑未清理；请求宽度不等于实际净空或防火间距。</p>"
            "<table><thead><tr><th>火源条件</th><th>仅建筑对</th><th>保留地面</th>"
            "<th>请求5米清理</th><th>请求10米清理</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></article>"
        )
    joint = ROOT / "runs/research/batuan-joint-20261017-r2"
    joint_section = "<p>联合搜索计算中，结果尚未冻结。</p>"
    if (joint / "report.json").exists():
        report = load(joint / "report.json")
        rows = []
        titles = {
            "baseline": "无新增清理",
            "outer_proxy": "仅村外",
            "inner_proxy": "仅村内",
            "joint": "联合搜索",
            "independent_sweep": "固定几何分配扫描",
            "historical_combined": "原始内外同时",
        }
        seen = set()
        features = []
        for row in report["confirmation"]:
            if not row["full_grid"]:
                continue
            rec = row["records"]
            passed = sum(x["metrics"]["ignited_structure_count"] == 0 for x in rec)
            area = rec[0]["intervention"]["cleared_area_m2"]
            zones = rec[0]["intervention"]["zone_areas_m2"]
            if row["role"] == "training":
                held = next(
                    x
                    for x in report["confirmation"]
                    if x["full_grid"] and x["label"] == row["label"] and x["role"] == "held_out"
                )["records"]
                held_pass = sum(x["metrics"]["ignited_structure_count"] == 0 for x in held)
                rows.append(
                    f"<tr><td>{html.escape(titles[row['label']])}</td>"
                    f"<td>{passed}/{len(rec)}</td><td>{held_pass}/{len(held)}</td><td>{area:.2f}</td>"
                    f"<td>{zones['outer_proxy']:.2f} / {zones['inner_proxy']:.2f}</td></tr>"
                )
            if row["label"] not in seen:
                seen.add(row["label"])
                plan = plan_from_mapping(row["plan"])
                geom = shape(rec[0]["intervention"]["effective_footprint"])
                if not geom.is_empty:
                    extent = unary_union(
                        [*polygons.values(), geom, *(LineString(p.points_m) for p in plan.paths)]
                    ).bounds
                    extent = (extent[0] - 25, extent[1] - 25, extent[2] + 25, extent[3] + 25)
                    figure(
                        extent,
                        polygons,
                        footprint=geom,
                        paths=[p.points_m for p in plan.paths],
                        destination=output / (row["label"] + ".png"),
                    )
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": rec[0]["intervention"]["effective_footprint"],
                            "properties": {
                                "label": row["label"],
                                "plan": row["plan"],
                                "cleared_area_m2": area,
                                "zone_areas_m2": zones,
                            },
                        }
                    )
        (output / "plans.utm49n.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "name": "batuan-joint-proposed-clearing",
                    "crs": {"type": "name", "properties": {"name": "EPSG:32649"}},
                    "features": features,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        images = "".join(
            f'<figure><figcaption>{titles[label]}</figcaption><img src="{label}.png" alt="{label}候选带"></figure>'
            for label in sorted(seen)
            if (output / (label + ".png")).exists()
        )
        inventory_note = ""
        inventory_path = ROOT / "docs/results/batuan-annotation-inventory-20261017.json"
        if inventory_path.exists():
            inventory = load(inventory_path)
            nested = ", ".join(x["label"] for x in inventory["unrepresented_objects"])
            inventory_note = (
                f"<p>对象核对：人工标注{inventory['annotated_objects']}个对象，"
                f"本表按{inventory['independent_grid_objects']}个独立网格对象计数。"
                f"{nested}与外层轮廓重叠，已在其他建筑格内，尚未作为独立对象评价；"
                "不据此声称已验证333栋独立建筑。"
                '<a href="local-mesh/overlap-review.svg">查看重叠标注</a> · '
                '<a href="local-mesh/">查看98项细网格核验</a></p>'
            )
        joint_section = (
            "<p>8项设计、4项新位置/风况留出；各模式最多16次候选请求。以下为完整场地90分钟结果。"
            "通过要求民居与桥均不引燃。区界仍为20米代理，非实测施工边界；固定等效木材和水分。</p>"
            "<p>比较达标方案的清理面积；尚未完成等面积预算曲线。联合配置允许选择单侧极限，不能仅凭通过率称为协同。</p>"
            "<table><thead><tr><th>方案</th><th>设计通过</th><th>留出通过</th><th>清理㎡</th>"
            "<th>村外 / 近邻㎡</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            + inventory_note
            + images
            + '<p><a href="plans.utm49n.geojson">候选方案GIS文件（UTM49N）</a></p>'
        )
    channel_section = ""
    channel_file = ROOT / "docs/results/batuan-pair-channels-20261017.json"
    if channel_file.exists():
        channels = load(channel_file)["rows"]
        lines = []
        for profile in ("single_cell", "source_roof_cells"):
            for owner in (162, 179):
                values = []
                for channel in ("both", "radiation_only", "contact_only"):
                    row = next(
                        x
                        for x in channels
                        if x["profile"] == profile
                        and x["source_owner"] == owner
                        and x["channel"] == channel
                    )
                    value = row["first_receiver_ignition_s"]
                    values.append("未引燃" if value is None else f"{value:.0f}秒")
                title = "单格" if profile == "single_cell" else "屋顶同时"
                building = "B0163" if owner == 162 else "B0180"
                lines.append(
                    f"<tr><td>{title} / {building}</td>"
                    + "".join(f"<td>{value}</td>" for value in values)
                    + "</tr>"
                )
        channel_section = (
            "<section><h2>最窄建筑对：传热通道诊断</h2>"
            "<p>5米栅格未独立解析真实3.5米间隙。以下为模型通道对照，不能作为真实安全间距的验证。</p>"
            "<table><tr><th>火源</th><th>全部通道</th><th>仅辐射</th><th>仅接触</th></tr>"
            + "".join(lines)
            + "</table></section>"
        )
    document = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>岜团村：建筑对与联合防火线试验</title><style>
body{font:16px/1.7 system-ui,sans-serif;max-width:1120px;margin:40px auto;padding:0 24px;color:#172b35;background:#f5f6f4}
h1{font-size:30px}article,section{background:white;padding:24px;margin:24px 0;border:1px solid #d9dfdf;border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;border:1px solid #dae0e0;text-align:left}th{background:#edf3f3}
img{max-width:100%;height:auto;max-height:700px}figure{display:inline-block;margin:12px 12px 12px 0;max-width:46%}
aside{border-left:4px solid #387c81;background:#eaf1ef;padding:16px}a{color:#176875}
</style><h1>岜团村：建筑对与内外防火线联合试验</h1>
<aside>真实人工轮廓，等效合成木材参数；含水率固定，不作为研究输入或优化变量。建筑对试验隔离了第三栋建筑及远场燃料，属于机制检验；联合试验保留场内全部建筑。结果不等同于实地安全宽度或施工方案。</aside>
<p>样本按几何间距与间隙燃料选择，未按燃烧结果挑选。5对×双向火源×4方法×2时间步×2火源强度，共160项90分钟对照。</p>
<p>5米与10米仅代表植被清理宽度，既不移动建筑，也不产生不透明墙。屋顶同时起火为上界压力条件，不代表实测火情。</p>
<section><h2>联合优化与独立对照</h2>"""
        + joint_section
        + "</section>"
        + channel_section
        + "".join(cards)
        + "</html>"
    )
    (output / "index.html").write_text(document)
    print(output / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
