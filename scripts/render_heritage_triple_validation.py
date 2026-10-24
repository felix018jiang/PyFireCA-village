"""Map the frozen three-building candidate and independent fire-source bank."""

from __future__ import annotations

import argparse
import json

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon, box
from shapely.ops import unary_union


def render(roots, output):
    inventories = [json.loads((root / "inventory.json").read_text()) for root in roots]
    assert all(i == inventories[0] for i in inventories)
    buildings = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventories[0]["buildings"]
    }
    cores = {"B0073", "B0120"}
    removed = {"B0061", "B0071", "B0076"}
    samples = []
    for root, color in zip(roots, ("#2464aa", "#804ab4"), strict=True):
        bank = json.loads((root / "realized-sources.json").read_text())
        seen = set()
        for plan_id, rows in bank.items():
            for r in rows:
                key = tuple(r["anchor_m"])
                if key not in seen:
                    samples.append((key, color, plan_id != "baseline"))
                    seen.add(key)
    canvas = Image.new("RGB", (1900, 1220), "white")
    draw = ImageDraw.Draw(canvas)

    def font(size):
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", size)

    draw.text(
        (35, 22),
        "Frozen internal-firebreak candidate: B0061 + B0071 + B0076",
        font=font(29),
        fill="#172d40",
    )
    draw.text(
        (35, 67),
        "Independent confirmation | 18 draws per arm | 3 arms | two roof scales | 108 simulations",
        font=font(22),
        fill="#475569",
    )
    draw.text(
        (35, 102),
        "Source buildings may repeat; all 22 realized ignition polygons are new. "
        "Map shows positions, not protection outcomes.",
        font=font(20),
        fill="#475569",
    )

    all_bounds = unary_union(list(buildings.values())).bounds
    panels = [
        (
            (35, 175, 1035, 1130),
            box(all_bounds[0] - 20, all_bounds[1] - 20, all_bounds[2] + 20, all_bounds[3] + 20),
            "Village and background sources",
        ),
        ((1090, 175, 1865, 605), box(347230, 2869735, 347307, 2869810), "Drum tower / B0073"),
        (
            (1090, 675, 1865, 1130),
            box(347267, 2869670, 347358, 2869756),
            "Wind-and-rain bridge / B0120",
        ),
    ]
    for (left, top, right, bottom), window, title in panels:
        xmin, ymin, xmax, ymax = window.bounds
        scale = min((right - left) / (xmax - xmin), (bottom - top) / (ymax - ymin))
        ox = left + ((right - left) - (xmax - xmin) * scale) / 2
        oy = top + ((bottom - top) - (ymax - ymin) * scale) / 2

        def xy(x, y, ox=ox, oy=oy, xmin=xmin, ymax=ymax, scale=scale):
            return ox + (x - xmin) * scale, oy + (ymax - y) * scale

        draw.rectangle((left, top, right, bottom), fill="#f8fafc", outline="#cbd5e1")
        draw.text((left, top - 30), title, font=font(21), fill="#172d40")
        for bid, geometry in buildings.items():
            clipped = geometry.intersection(window)
            pieces = [clipped] if clipped.geom_type == "Polygon" else getattr(clipped, "geoms", ())
            fill = "#f1b8a7" if bid in removed else "#8cd1bc" if bid in cores else "#d8dfe5"
            edge = "#b23b23" if bid in removed else "#167458" if bid in cores else "#9ba9b5"
            for polygon in pieces:
                if polygon.geom_type != "Polygon" or polygon.is_empty:
                    continue
                draw.polygon(
                    [xy(x, y) for x, y in polygon.exterior.coords], fill=fill, outline=edge, width=2
                )
                for ring in polygon.interiors:
                    draw.polygon([xy(x, y) for x, y in ring.coords], fill="#f8fafc")
        for (x, y), color, replacement in samples:
            if xmin <= x <= xmax and ymin <= y <= ymax:
                u, v = xy(x, y)
                draw.ellipse(
                    (u - 5, v - 5, u + 5, v + 5),
                    fill="white" if replacement else color,
                    outline=color,
                    width=2,
                )
        for bid in sorted(cores | removed):
            point = buildings[bid].representative_point()
            if window.covers(point):
                u, v = xy(point.x, point.y)
                draw.text(
                    (u + 7, v + 5),
                    bid,
                    font=font(19 if left > 1000 else 15),
                    fill="#102b3e",
                    stroke_width=2,
                    stroke_fill="white",
                )
        bar_m = 100 if left < 1000 else 10
        u, v = left + 20, bottom - 25
        draw.line((u, v, u + bar_m * scale, v), fill="#172d40", width=4)
        draw.text((u, v - 25), f"{bar_m} m", font=font(17), fill="#172d40")
    draw.text(
        (35, 1165),
        "Green: protected heritage   Red: frozen demolition   "
        "Blue/purple: cohorts c/d   Hollow: replacement source",
        font=font(21),
        fill="#172d40",
    )
    canvas.save(output)


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs=2, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.roots, args.output)
