"""Draw frozen layouts on real imagery and export all geometry classes for GIS.

These are clearance maps, not simulated terminal fire states.
"""

from __future__ import annotations

import json
from pathlib import Path

import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.windows import from_bounds
from render_batuan_intervention_report import geographic_feature
from run_batuan_adaptive_search import dump
from shapely.geometry import shape


def layouts(study, report):
    study = Path(study)
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
            raise ValueError("layout imagery CRS mismatch")
        rgb = raster.read(
            [1, 2, 3],
            window=from_bounds(left, bottom, right, top, raster.transform),
            out_shape=(3, height, width),
            resampling=rasterio.enums.Resampling.bilinear,
        )
    background = Image.fromarray(rgb.transpose(1, 2, 0)).convert("RGBA")
    structures = json.loads((Path(meta["spatial_assets_dir"]) / "buildings.json").read_text())[
        "buildings"
    ]

    def xy(point):
        return (
            (point[0] - left) / (right - left) * width,
            (top - point[1]) / (top - bottom) * height,
        )

    output = study / "layouts"
    output.mkdir(exist_ok=True)
    filenames = {}
    font = ImageFont.load_default(size=14)
    for label, plan in report["chosen_plans"].items():
        if not any(label.startswith(z + "_") for z in ("outer_proxy", "inner_proxy")):
            continue
        record = report["controls"][label][0]
        treatment = record["intervention"]
        footprint = shape(treatment["effective_footprint"])
        overlay = Image.new("RGBA", background.size)
        brush = ImageDraw.Draw(overlay)
        polygons = [footprint] if footprint.geom_type == "Polygon" else list(footprint.geoms)
        for polygon in polygons:
            if polygon.geom_type != "Polygon":
                continue
            brush.polygon([xy(p) for p in polygon.exterior.coords], fill=(25, 211, 198, 120))
            for hole in polygon.interiors:
                brush.polygon([xy(p) for p in hole.coords], fill=(0, 0, 0, 0))
        image = Image.alpha_composite(background, overlay)
        brush = ImageDraw.Draw(image)
        for structure in structures:
            for polygon in structure["polygons"]:
                brush.line([xy(p) for p in polygon["exterior"]], fill=(255, 255, 255, 185), width=1)
        for path in plan["paths"]:
            brush.line([xy(p) for p in path["points_m"]], fill=(38, 250, 230), width=2)
        offsets = ((5, -21), (-25, -21), (5, 5), (-25, 5))
        for i, scenario in enumerate(
            report["protocol"]["training"] + report["protocol"]["held_out"]
        ):
            x, y = xy(scenario["ignitions"][0]["point_m"])
            color = (239, 95, 64) if i < 2 else (74, 171, 255)
            brush.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
            dx, dy = offsets[i]
            brush.text(
                (x + dx, y + dy),
                ("T" if i < 2 else "V") + str(i % 2 + 1),
                font=font,
                fill=color,
                stroke_width=1,
                stroke_fill=(15, 20, 25),
            )
        filename = label + ".jpg"
        image.convert("RGB").save(output / filename, quality=93)
        filenames[label] = "layouts/" + filename
        properties = {
            "label": label,
            "status": "hypothetical_vegetation_clearance",
            "cleared_area_m2": treatment["cleared_area_m2"],
            "source_crs": grid["crs"],
            "study_role": report.get("role", "frozen_geometry_family_representative"),
            "training_goal_met": all(
                r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"] == 0
                for r in report["controls"][label]
            ),
            "held_out_goal_met": all(
                r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"] == 0
                for r in report["validation"][label]
            ),
            "sensitivity_goal_met": all(
                r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"] == 0
                for r in report["sensitivity"][label]
            ),
        }
        dump(
            output / (label + "-clearing.geojson"),
            geographic_feature(treatment["effective_footprint"], properties, grid["crs"]),
        )
        lines = [
            geographic_feature(
                {"type": "LineString", "coordinates": p["points_m"]},
                {**properties, "path_index": i, "width_m": p["width_m"]},
                grid["crs"],
            )
            for i, p in enumerate(plan["paths"])
        ]
        dump(
            output / (label + "-centerlines.geojson"),
            {"type": "FeatureCollection", "features": lines},
        )
    return filenames
