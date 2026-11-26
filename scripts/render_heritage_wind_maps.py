"""Draw the registered strategy geometries and paired source samples on a metric map."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape
from shapely.ops import unary_union

VILLAGES = {"pingyan": "平岩", "batuan": "岜团"}
PLANS = {
    "baseline": "无清理",
    "heritage-narrow": "遗产窄围护",
    "heritage-wide": "遗产宽围护",
    "multipoint-narrow": "多点窄围护",
    "multipoint-wide": "多点宽围护",
}
COLORS = {
    "building": "#d3d6d0",
    "heritage": "#146459",
    "nominal_band": "#397bb1",
    "cleared_roof": "#b45a33",
    "cleared_ground": "#d7ab5e",
    "fixed_ignition": "#4d3990",
    "random_ignition": "#212b32",
}
INK, MUTED, PAPER = "#24362e", "#4c6058", "#fafaf6"


def font(size):
    for path in (
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("A Chinese text font is required to render the strategy maps")


def polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def panel(features, bounds, size, plan=None):
    """One map with square metres at a constant scale, grid north up."""
    width, height = size
    x0, y0, x1, y1 = bounds
    scale = 0.90 * min(width / max(1.0, x1 - x0), height / max(1.0, y1 - y0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def xy(point):
        return (width / 2 + (point[0] - cx) * scale, height / 2 - (point[1] - cy) * scale)

    tile = Image.new("RGB", size, "#f4f5ef")
    draw = ImageDraw.Draw(tile)
    order = ["building", "cleared_ground", "cleared_roof", "heritage", "nominal_band"]
    for layer in order:
        selected = [
            g
            for p, g in features
            if p["layer"] == layer and ("plan_id" not in p or p["plan_id"] == plan)
        ]
        if not selected:
            continue
        # The construction layers have holes where a roof or ground parcel is retained.
        mask = Image.new("L", size, 0)
        brush = ImageDraw.Draw(mask)
        for polygon in polygons(unary_union(selected)):
            rings = [polygon.exterior, *polygon.interiors]
            for n, ring in enumerate(rings):
                coords = [xy(p) for p in ring.coords]
                if layer == "nominal_band":
                    brush.line(coords, fill=215, width=2)
                else:
                    brush.polygon(coords, fill=0 if n else 255)
        tile.paste(COLORS[layer], (0, 0, width, height), mask)
    if plan is None:
        for properties, g in features:
            layer = properties["layer"]
            if layer not in {"fixed_ignition", "random_ignition"}:
                continue
            p = g.representative_point()
            x, y = xy((p.x, p.y))
            if layer == "fixed_ignition":
                draw.polygon(
                    [(x, y - 7), (x + 7, y), (x, y + 7), (x - 7, y)],
                    fill=COLORS[layer],
                    outline="white",
                    width=2,
                )
            else:
                draw.ellipse(
                    (x - 6, y - 6, x + 6, y + 6), fill=COLORS[layer], outline="white", width=2
                )
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]
    bar = max(n for n in nice if n * scale <= 0.25 * width)
    bottom = height - 16
    draw.rectangle((10, bottom - 32, 36 + bar * scale, bottom + 6), fill=PAPER)
    draw.line((17, bottom, 17 + bar * scale, bottom), fill=INK, width=3)
    draw.text((17, bottom - 24), f"{bar} m", fill=INK, font=font(18))
    return tile


def render_maps(folder, registration):
    for village, setup in registration["villages"].items():
        source = json.loads((folder / f"{village}-layouts.geojson").read_text())
        features = [(f["properties"], shape(f["geometry"])) for f in source["features"]]
        all_bounds = unary_union(
            [g for p, g in features if p["layer"] in {"building", "heritage"}]
        ).bounds
        detail_bounds = unary_union(
            [
                g
                for p, g in features
                if p["layer"] in {"heritage", "nominal_band", "cleared_roof", "cleared_ground"}
            ]
        ).bounds
        image = Image.new("RGB", (1940, 1150), PAPER)
        draw = ImageDraw.Draw(image)
        draw.text(
            (38, 24), f"{VILLAGES[village]}村防火线布局与模拟起火样本", fill=INK, font=font(39)
        )
        draw.text(
            (38, 83),
            "全村图标记预先固定和独立抽取的起火斑块。右侧四图采用相同视窗及比例。网格北向上。",
            fill=MUTED,
            font=font(23),
        )
        draw.text((38, 164), "全村与起火样本", fill=INK, font=font(27))
        image.paste(panel(features, all_bounds, (525, 820)), (38, 212))
        configs = {p["plan_id"]: p for p in setup["plans"]}
        for i, plan in enumerate(p for p in PLANS if p != "baseline"):
            config = configs[plan]
            x, y = 582 + (i % 2) * 670, 144 + (i // 2) * 452
            draw.text((x, y), f"{PLANS[plan]}   {config['width_m']:g} m", fill=INK, font=font(26))
            draw.text(
                (x, y + 36),
                f"实际清理 {config['construction_m2']:,.1f} m²",
                fill=MUTED,
                font=font(20),
            )
            image.paste(panel(features, detail_bounds, (646, 368), plan), (x, y + 70))
        legend = [
            ("building", "普通屋面"),
            ("heritage", "遗产屋面"),
            ("nominal_band", "名义带边界"),
            ("cleared_roof", "清理屋面"),
            ("cleared_ground", "非屋面可燃地表格"),
            ("fixed_ignition", "固定起火斑块"),
            ("random_ignition", "随机起火斑块"),
        ]
        x, y = 45, 1093
        for layer, title in legend:
            if layer == "nominal_band":
                draw.line((x, y + 10, x + 32, y + 10), fill=COLORS[layer], width=3)
            elif layer == "fixed_ignition":
                draw.polygon(
                    [(x + 12, y), (x + 22, y + 10), (x + 12, y + 20), (x + 2, y + 10)],
                    fill=COLORS[layer],
                )
            elif layer == "random_ignition":
                draw.ellipse((x + 4, y, x + 24, y + 20), fill=COLORS[layer])
            else:
                draw.rectangle((x, y, x + 28, y + 19), fill=COLORS[layer])
            draw.text((x + 40, y - 3), title, font=font(22), fill=INK)
            x += 250
        image.save(folder / f"{village}-strategies.png")
