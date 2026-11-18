"""Render georeferenced imagery and strategy footprints without running CA."""

# ruff: noqa: RUF001

from __future__ import annotations

from math import floor, log10
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject
from shapely.geometry import box
from shapely.ops import unary_union

from pyfireca.planning.strategy_io import demolition_geometry, write_json


def render_strategy_map(inputs, replay, output, font_path):
    font_path = Path(font_path)
    if not font_path.is_file():
        raise FileNotFoundError(f"map font missing; provide --font: {font_path}")

    def font(size):
        return ImageFont.truetype(str(font_path), size)

    selected = set(replay["best_ids"])
    inventory = inputs.inventory
    targets = {t.building_id: t for t in inventory.targets}
    _, ledger, cleared, shared = demolition_geometry(inventory, selected)
    width, height = 2400, 1430
    canvas = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    ink, muted = "#173545", "#435967"
    secondary = "面积" if inputs.costs is None else "声明代价"
    draw.text(
        (45, 28),
        inputs.manifest["place_name"]
        + "｜核心遗产保护与"
        + ("最少拆除策略" if replay["optimality_proven"] else "当前拆除候选策略"),
        font=font(50),
        fill=ink,
    )
    draw.text(
        (45, 103),
        f"统一影像、建筑轮廓、材质与遗产清单 → 冻结情景评价 → 数量优先、{secondary}次之",
        font=font(29),
        fill=muted,
    )
    legend = [
        ("#dd614a", "拆除屋面"),
        ("#2ca58a", "核心遗产"),
        ("#d99c24", "其他搜索候选"),
        ("#bb73c4", "共享屋面保留"),
    ]
    for x, (colour, label) in zip((45, 500, 960, 1540), legend, strict=True):
        draw.rectangle((x, 174, x + 30, 204), fill=colour)
        draw.text((x + 43, 171), label, font=font(28), fill=ink)
    all_roofs = unary_union([b.geometry for b in inventory.buildings])
    views = [("A  全村干预位置", all_roofs.buffer(12).bounds)]
    for target in inventory.targets:
        geometry = inventory.by_id[target.building_id].geometry
        nearby = [
            inventory.by_id[i].geometry
            for i in selected
            if inventory.by_id[i].geometry.distance(geometry) <= 60
        ]
        focus = unary_union([geometry, *nearby]).buffer(18)
        role = "鼓楼" if target.role == "gulou" else "风雨桥"
        views.append((role + " " + target.building_id, focus.bounds))
    panels = []
    margin, gap = 45, 32
    panel_width = (width - 2 * margin - gap * (len(views) - 1)) // len(views)
    with rasterio.open(inputs.paths["imagery_tiff"]) as source:
        if source.count < 3 or any(t != "uint8" for t in source.dtypes[:3]):
            raise ValueError("map requires an explicitly prepared uint8 RGB TIFF")
        for number, (title, bounds) in enumerate(views):
            left, top, pw, ph = margin + number * (panel_width + gap), 285, panel_width, 875
            draw.text((left, 235), title, font=font(30), fill=ink)
            xmin, ymin, xmax, ymax = bounds
            scale = min(pw / (xmax - xmin), ph / (ymax - ymin))
            cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
            span_x, span_y = pw / scale, ph / scale
            xmin, ymin, xmax, ymax = (
                cx - span_x / 2,
                cy - span_y / 2,
                cx + span_x / 2,
                cy + span_y / 2,
            )
            rgb = np.full((3, ph, pw), 245, dtype=np.uint8)
            reproject(
                rasterio.band(source, (1, 2, 3)),
                rgb,
                dst_transform=from_bounds(xmin, ymin, xmax, ymax, pw, ph),
                dst_crs=inputs.manifest["spatial_crs"],
                resampling=Resampling.bilinear,
                dst_nodata=245,
            )
            panel = Image.fromarray(np.moveaxis(rgb, 0, 2)).convert("RGBA")
            overlay = Image.new("RGBA", panel.size)
            painter = ImageDraw.Draw(overlay)
            window = box(xmin, ymin, xmax, ymax)

            def xy(x, y, xmin=xmin, ymax=ymax, scale=scale):
                return (x - xmin) * scale, (ymax - y) * scale

            def paint(
                geometry,
                colour,
                stroke,
                thickness,
                *,
                window=window,
                size=panel.size,
                overlay=overlay,
                painter=painter,
                xy=xy,
            ):
                clipped = geometry.intersection(window)
                parts = (
                    [clipped] if clipped.geom_type == "Polygon" else getattr(clipped, "geoms", ())
                )
                for polygon in parts:
                    if polygon.geom_type != "Polygon" or polygon.is_empty:
                        continue
                    # A per-polygon mask preserves imagery through courtyard holes.
                    mask = Image.new("L", size)
                    brush = ImageDraw.Draw(mask)
                    exterior = [xy(x, y) for x, y in polygon.exterior.coords]
                    brush.polygon(exterior, fill=colour[3])
                    for ring in polygon.interiors:
                        brush.polygon([xy(x, y) for x, y in ring.coords], fill=0)
                    tint = Image.new("RGBA", size, (*colour[:3], 0))
                    tint.putalpha(mask)
                    overlay.alpha_composite(tint)
                    painter.line(exterior, fill=stroke, width=thickness)

            for b in inventory.buildings:
                important = b.building_id in targets or b.building_id in selected
                colour = (
                    (44, 165, 138, 130)
                    if b.building_id in targets
                    else ((221, 97, 74, 135) if b.building_id in selected else (255, 255, 255, 10))
                )
                stroke = (
                    "#2ca58a"
                    if b.building_id in targets
                    else "#dd614a"
                    if b.building_id in selected
                    else (
                        "#e8b13b"
                        if b.building_id in replay["strategy_result"]["candidate_ids"]
                        else "#e2e8e9"
                    )
                )
                paint(b.geometry, colour, stroke, 4 if important else 1)
            paint(shared, (187, 115, 196, 220), "#762e86", 3)
            panel = Image.alpha_composite(panel, overlay)
            label_draw = ImageDraw.Draw(panel)
            occupied = []
            for identifier in sorted(targets.keys() | selected):
                point = inventory.by_id[identifier].geometry.representative_point()
                if not window.covers(point) or (number == 0 and identifier not in targets):
                    continue
                px, py = xy(point.x, point.y)
                label = ("保护 " if identifier in targets else "拆 ") + identifier
                bbox = label_draw.textbbox((0, 0), label, font=font(24))
                tw, th = bbox[2] + 12, bbox[3] + 10
                tx = min(max(8, px + 15 if px < pw / 2 else px - tw - 15), pw - tw - 8)
                ty = min(max(12, py - th - 12), ph - th - 12)
                while any(box(tx, ty, tx + tw, ty + th).intersects(r) for r in occupied):
                    ty += th + 8
                occupied.append(box(tx, ty, tx + tw, ty + th))
                label_draw.line((px, py, tx + tw / 2, ty + th / 2), fill="#ffffff", width=2)
                label_draw.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=4, fill="#ffffff")
                label_draw.text((tx + 6, ty + 2), label, font=font(24), fill=ink)
            bar = 10 ** floor(log10(span_x * 0.20))
            bar *= 2 if span_x * 0.20 / bar >= 2 else 1
            label_draw.rectangle((15, ph - 78, 32 + bar * scale, ph - 14), fill="#ffffff")
            label_draw.text((23, ph - 76), f"{bar:g} m", font=font(23), fill=ink)
            label_draw.line((23, ph - 25, 23 + bar * scale, ph - 25), fill=ink, width=4)
            label_draw.text(
                (pw - 42, 14), "N ↑", font=font(22), fill="white", stroke_width=2, stroke_fill=ink
            )
            canvas.paste(panel.convert("RGB"), (left, top))
            draw.rectangle((left, top, left + pw, top + ph), outline="#b4c0c6", width=2)
            panels.append(
                {
                    "title": title,
                    "world_bounds_m": [xmin, ymin, xmax, ymax],
                    "pixel_rect": [left, top, pw, ph],
                }
            )
    cost = (
        "待补"
        if inputs.costs is None
        else (f"{replay['strategy_result']['best_declared_cost']:g} {inputs.costs['unit']}")
    )
    protocol = inputs.receipt["frozen_experiment"]
    scales = " / ".join(f"{s:g} m" for s in protocol["scales_m"])
    proof = (
        "已证明声明候选范围内最优"
        if replay["optimality_proven"]
        else "最少拆除栋数已证明，同数量面积仍有未决方案"
        if replay["strategy_result"].get("minimum_count_proven", False)
        else "当前可行方案，最优性尚未证明"
    )
    lines = [
        f"拆除 {'＋'.join(sorted(selected))}｜{len(selected)} 栋｜"
        f"名义面积 {ledger['nominal_footprint_area_m2']:.2f} m²",
        f"实际清空 {cleared.area:.2f} m²｜共享屋面保留 {shared.area:.2f} m²｜经济成本：{cost}",
        f"本图对应 {len(replay['strategy_result']['candidate_ids'])} 候选、"
        f"{len(inputs.experiment['problem']['fires'])}个已知设计火源；"
        f"各核心在 {scales}、{protocol['horizon_s']:g} s 内均未引燃。",
        proof + "；实施资料见清单。物理参数未改变；"
        f"本次 {replay.get('new_physical_CA_checks', 0)} 次火灾求解。",
    ]
    for y, line in zip((1190, 1244, 1300, 1355), lines, strict=True):
        draw.text((45, y), line, font=font(29 if y < 1300 else 25), fill=ink if y < 1300 else muted)
    output = Path(output)
    canvas.save(output / "strategy-map.png")
    canvas.save(output / "strategy-map.pdf", resolution=180)
    write_json(
        output / "map-reference.json",
        {
            "source_tiff_sha256": inputs.receipt["input_file_sha256"]["imagery_tiff"],
            "crs": inputs.manifest["spatial_crs"],
            "panels": panels,
            "width_px": width,
            "height_px": height,
            "font_path": str(font_path),
            "shows_geographic_roof_outlines": True,
        },
    )
