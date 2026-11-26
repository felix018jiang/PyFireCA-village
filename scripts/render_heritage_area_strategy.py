"""Export inspectable roof/vegetation action maps without running fire CA.

Read a frozen master plus search, or a frozen joint comparison configuration.
Maps distinguish selected object outlines from the physical roof area cleared.
The geographic export uses RFC 7946 longitude/latitude coordinates.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as PlotPath
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import transform, unary_union

from pyfireca.planning.strategy_io import demolition_geometry, write_json
from pyfireca.planning.study_io import load_study


def polygon_patch(geometry, offset, **kwargs):
    """Preserve courtyard holes using opposite ring winding."""
    parts = [geometry] if geometry.geom_type == "Polygon" else getattr(geometry, "geoms", ())
    for polygon in parts:
        if polygon.geom_type != "Polygon" or polygon.is_empty:
            continue
        vertices, codes = [], []
        polygon = orient(polygon, sign=1.0)
        for ring in (polygon.exterior, *polygon.interiors):
            xy = np.asarray(ring.coords)[:, :2] - offset
            vertices.extend(xy)
            codes.extend(
                [PlotPath.MOVETO, *([PlotPath.LINETO] * (len(xy) - 2)), PlotPath.CLOSEPOLY]
            )
        yield PathPatch(PlotPath(vertices, codes), **kwargs)


def actual_sources(config_path, config, plan):
    """Label actual first-retained roofs from an immutable declared reference."""
    if not config.get("reference_master_experiment_path"):
        raise ValueError("actual-source labels require a frozen master experiment reference")
    path = (config_path.parent / config["reference_master_experiment_path"]).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config["reference_master_experiment_sha256"]:
        raise ValueError("actual-source map reference changed")
    experiment = json.loads(raw)
    sources, footprints = {}, {}
    for label, fire in experiment["problem"]["fires"].items():
        priority = fire["protocol"]["draws"][0]["priority_ids"]
        identifier = next((i for i in priority if i not in plan["demolished_ids"]), None)
        if identifier is None or identifier not in fire["footprints"]:
            raise ValueError("actual map source is outside the immutable reference prefix")
        sources.setdefault(identifier, []).append(label)
        footprints[identifier] = shape(fire["footprints"][identifier]["geometry"])
    return sources, footprints


def export_path(stem, suffix):
    # A decimal-width plan ID such as w15.01 is a basename, not a .01 extension.
    return Path(str(stem) + suffix)


def render(
    config_path,
    output,
    *,
    search_path=None,
    plan_id=None,
    font_path,
    status_label,
    label_mode="selected",
    title=None,
    common_views=False,
):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, _, inventory = load_study(study)
    if config.get("source_policy") == "area_bounded_retained_sources":
        forbidden = set(inventory.protected_ids) | {t.building_id for t in inventory.targets}
        candidates = tuple(
            sorted(
                b.building_id
                for b in inventory.buildings
                if b.source_label == "building" and b.building_id not in forbidden
            )
        )
        inventory = replace(inventory, fixed_source_ids=(), candidate_ids=candidates)
    if search_path is not None:
        search_path = Path(search_path).resolve()
        search_bytes = search_path.read_bytes()
        search = json.loads(search_bytes)
        plans = [
            {"plan_id": f"roof-{n:02d}", **row, "cleared_physical_cell_ids": []}
            for n, row in enumerate(search["verified_feasible_plans"], 1)
        ]
    else:
        plans = config["plans"]
    view_plans = plans
    if plan_id is not None:
        plans = [p for p in plans if p["plan_id"] == plan_id]
    if not plans:
        raise ValueError("no matching plan is available for export")
    font_path = Path(font_path)
    if not font_path.is_file():
        raise FileNotFoundError(font_path)
    font = FontProperties(fname=str(font_path))
    output.mkdir(parents=True, exist_ok=True)
    grid = bundle.request.grid
    project = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    targets = {t.building_id: t for t in inventory.targets}
    all_roofs = unary_union([b.geometry for b in inventory.buildings])
    offset = np.floor(np.array(all_roofs.bounds[:2]) / 100) * 100
    palette = {
        "roof": "#D66B42",
        "grass": "#4286B5",
        "core": "#157F70",
        "source": "#D9AF52",
        "shared": "#9C69B3",
        "other": "#DCE2E3",
        "ink": "#253E48",
    }
    outputs = []

    for plan in plans:
        identifier = plan["plan_id"]
        if Path(identifier).name != identifier:
            raise ValueError("plan_id must be a file-safe basename")
        selected = set(plan["demolished_ids"])
        sources, ignition_footprints = (
            actual_sources(config_path, config, plan) if label_mode == "core-sources" else ({}, {})
        )
        cells = sorted(set(plan.get("cleared_physical_cell_ids", [])))
        rows, ledger, cleared, shared = demolition_geometry(inventory, selected)
        roof_cells = set()
        # Exact geometry eligibility is checked by the simulation engine. Export
        # additionally refuses cells touching any known roof or leaving the grid.
        grass, grass_rows = [], []
        for cell in cells:
            if (
                isinstance(cell, bool)
                or not isinstance(cell, int)
                or not 0 <= cell < grid.height * grid.width
            ):
                raise ValueError("invalid physical vegetation cell ID")
            r, c = divmod(cell, grid.width)
            x, y = grid.center(r, c)
            half = grid.cell_size_m / 2
            geometry = box(x - half, y - half, x + half, y + half)
            if geometry.intersection(all_roofs).area > 0:
                roof_cells.add(cell)
            eligible = (
                bundle.request.domain_mask[r, c]
                and bundle.request.known_mask[r, c]
                and bundle.request.burnable_mask[r, c]
                and bundle.request.building_index[r, c] < 0
                and bundle.request.parameters.covered_area_m2[r, c] > 0
            )
            if not eligible:
                raise ValueError("vegetation cell is not known burnable nonroof ground")
            grass.append(geometry)
            lon, lat = project.transform(x, y)
            grass_rows.append(
                {
                    "physical_cell_id": cell,
                    "parent_row": r,
                    "parent_col": c,
                    "center_E_m": x,
                    "center_N_m": y,
                    "longitude": lon,
                    "latitude": lat,
                    "construction_footprint_area_m2": grid.cell_area_m2,
                    "covered_fuel_area_m2": float(bundle.request.parameters.covered_area_m2[r, c]),
                    "landcover_index": int(bundle.request.landcover_index[r, c]),
                    "known_burnable_nonroof": True,
                }
            )
        if roof_cells:
            raise ValueError(f"vegetation cells overlap roofs: {sorted(roof_cells)}")
        grass_union = unary_union(grass)
        ground_area = len(cells) * grid.cell_area_m2
        cost = ledger["cleared_roof_union_area_m2"] + ground_area
        if "cleared_area_m2" in plan and not np.isclose(
            cost, plan["cleared_area_m2"], rtol=1e-9, atol=1e-7
        ):
            raise ValueError("saved area and exported physical action geometry differ")
        action_union = unary_union([cleared, grass_union])
        if not np.isclose(action_union.area, cost, rtol=1e-9, atol=1e-7):
            raise ValueError("roof and ground action footprints overlap")
        views = [("全村位置", all_roofs.buffer(15).bounds, None)]
        source_nearest_core = {
            identifier: min(
                targets,
                key=lambda core: (
                    inventory.by_id[identifier].geometry.distance(inventory.by_id[core].geometry),
                    core,
                ),
            )
            for identifier in sources
        }
        view_selected = (
            set().union(*(set(p["demolished_ids"]) for p in view_plans))
            if common_views
            else selected
        )
        view_grass = grass
        if common_views:
            view_grass = []
            for cell in sorted(
                {c for p in view_plans for c in p.get("cleared_physical_cell_ids", [])}
            ):
                r, c = divmod(cell, grid.width)
                x, y = grid.center(r, c)
                half = grid.cell_size_m / 2
                view_grass.append(box(x - half, y - half, x + half, y + half))
        for target in inventory.targets:
            core = inventory.by_id[target.building_id].geometry
            nearby = [
                inventory.by_id[i].geometry
                for i in view_selected
                if inventory.by_id[i].geometry.distance(core) < 35
            ]
            nearby.extend(g for g in view_grass if g.distance(core) < 35)
            views.append(
                (
                    ("鼓楼" if target.role == "gulou" else "风雨桥") + " " + target.building_id,
                    unary_union([core, *nearby]).buffer(18).bounds,
                    target.building_id,
                )
            )
        fig, axes = plt.subplots(1, len(views), figsize=(16, 7.2), layout="constrained")
        for number, (ax, (view_title, bounds, view_core)) in enumerate(
            zip(np.atleast_1d(axes), views, strict=True)
        ):
            window = box(*bounds)
            for b in inventory.buildings:
                if not b.geometry.intersects(window):
                    continue
                core = b.building_id in targets
                for patch in polygon_patch(
                    b.geometry,
                    offset,
                    facecolor=palette["core"]
                    if core
                    else palette["source"]
                    if b.building_id in sources
                    else palette["other"],
                    edgecolor="white",
                    linewidth=0.35,
                    zorder=1,
                ):
                    ax.add_patch(patch)
            for geometry in ignition_footprints.values():
                for patch in polygon_patch(
                    geometry,
                    offset,
                    facecolor="none",
                    edgecolor="#75541E",
                    linewidth=1.0,
                    zorder=7,
                ):
                    ax.add_patch(patch)
            for geometry, colour, z in (
                (cleared, palette["roof"], 3),
                (shared, palette["shared"], 4),
                (grass_union, palette["grass"], 5),
            ):
                for patch in polygon_patch(
                    geometry, offset, facecolor=colour, edgecolor="white", linewidth=0.55, zorder=z
                ):
                    ax.add_patch(patch)
            for i in sorted(selected):
                for patch in polygon_patch(
                    inventory.by_id[i].geometry,
                    offset,
                    facecolor="none",
                    edgecolor=palette["roof"],
                    linewidth=1,
                    linestyle="--",
                    zorder=6,
                ):
                    ax.add_patch(patch)
            if label_mode == "core-sources":
                labels = (
                    set(targets)
                    if view_core is None
                    else {view_core, *(s for s in sources if source_nearest_core[s] == view_core)}
                )
            else:
                labels = set(targets) | (selected if number else set())
            for i in sorted(labels):
                point = inventory.by_id[i].geometry.representative_point()
                if not window.covers(point):
                    continue
                x, y = np.array([point.x, point.y]) - offset
                label = ("保护 " if i in targets else "火源 " if i in sources else "计划 ") + i
                ax.annotate(
                    label,
                    (x, y),
                    xytext=(12, 12),
                    textcoords="offset points",
                    fontproperties=font,
                    fontsize=8,
                    color=palette["ink"],
                    bbox={
                        "facecolor": "white",
                        "edgecolor": "#D9E0E1",
                        "boxstyle": "round,pad=.25",
                    },
                    arrowprops={"arrowstyle": "-", "color": "#6F838B", "lw": 0.6},
                    zorder=10,
                )
            xmin, ymin, xmax, ymax = bounds
            ax.set_xlim(xmin - offset[0], xmax - offset[0])
            ax.set_ylim(ymin - offset[1], ymax - offset[1])
            ax.set_aspect("equal")
            ax.set_facecolor("#F7F9F9")
            ax.set_title(
                view_title,
                fontproperties=font,
                fontsize=12,
                color=palette["ink"],
                loc="left",
                pad=12,
            )
            ax.tick_params(labelsize=7, colors="#6A7B82", length=2)
            ax.set_xlabel(f"E − {offset[0]:.0f} m", fontsize=8, color="#6A7B82")
            if number == 0:
                ax.set_ylabel(f"N − {offset[1]:.0f} m", fontsize=8, color="#6A7B82")
            for spine in ax.spines.values():
                spine.set_color("#D8E0E2")
            ax.annotate(
                "N",
                xy=(0.93, 0.94),
                xytext=(0.93, 0.83),
                xycoords="axes fraction",
                ha="center",
                va="center",
                fontsize=10,
                color=palette["ink"],
                arrowprops={"arrowstyle": "-|>", "color": palette["ink"]},
            )
        place = config.get(
            "place_name",
            {"batuan": "岜团村", "pingyan": "平岩村"}.get(config.get("village_id"), "村落"),
        )
        fig.suptitle(
            f"{place}｜{title or identifier}   施工面积 {cost:.2f} m²\n{status_label}",
            fontproperties=font,
            fontsize=15,
            color=palette["ink"],
        )
        legend = [
            Patch(facecolor=palette[k], label=v)
            for k, v in (
                ("core", "重点遗产"),
                ("source", "实际火源建筑"),
                ("roof", "实际清空屋面"),
                ("grass", "整格植被清理"),
                ("shared", "共享保留屋面"),
                ("other", "其他建筑"),
            )
        ]
        legend.extend(
            [
                Line2D([0], [0], color=palette["roof"], linestyle="--", label="计划建筑轮廓"),
                Line2D([0], [0], color="#75541E", label="25 m² 起火片区"),
            ]
            if sources
            else []
        )
        fig.legend(handles=legend, loc="outside lower center", ncols=4, prop=font, frameon=False)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for ax in np.atleast_1d(axes):
            placed = []
            for annotation in ax.texts:
                if annotation.get_text() == "N":
                    continue
                for dx, dy in (
                    (12, 12),
                    (12, 32),
                    (-95, 12),
                    (-95, 32),
                    (12, -22),
                    (-95, -22),
                    (12, 52),
                    (-95, 52),
                ):
                    annotation.set_position((dx, dy))
                    annotation.update_positions(renderer)
                    annotation.update_bbox_position_size(renderer)
                    label_box = (
                        annotation.get_bbox_patch().get_window_extent(renderer).expanded(1.08, 1.2)
                    )
                    if (
                        ax.bbox.contains(label_box.x0, label_box.y0)
                        and ax.bbox.contains(label_box.x1, label_box.y1)
                        and not any(label_box.overlaps(previous) for previous in placed)
                    ):
                        placed.append(label_box)
                        break
        stem = output / identifier
        fig.savefig(export_path(stem, ".png"), dpi=190, facecolor="white")
        fig.savefig(export_path(stem, ".pdf"), facecolor="white")
        plt.close(fig)
        features = []
        for geometry, properties in (
            (
                cleared,
                {"action": "physical_roof_clearance", "selected_building_ids": sorted(selected)},
            ),
            (shared, {"action": "retained_shared_roof"}),
            *(
                (
                    g,
                    {
                        "action": "whole_parent_vegetation_clearance",
                        "physical_cell_id": c,
                        "construction_area_m2": grid.cell_area_m2,
                    },
                )
                for c, g in zip(cells, grass, strict=True)
            ),
            *(
                (
                    inventory.by_id[i].geometry,
                    {"action": "protected_heritage", "building_id": i, "role": targets[i].role},
                )
                for i in targets
            ),
            *(
                (
                    inventory.by_id[row["building_id"]].geometry,
                    {
                        **row,
                        "action": "planned_roof_annotation_action",
                        "area_additivity": (
                            "annotation_areas_may_overlap;use_physical_clearance_for_C"
                        ),
                    },
                )
                for row in rows
            ),
            *(
                (
                    inventory.by_id[i].geometry,
                    {
                        "action": "actual_ignition_source_roof",
                        "building_id": i,
                        "source_families": sources[i],
                    },
                )
                for i in sources
            ),
            *(
                (
                    geometry,
                    {
                        "action": "actual_prescribed_ignition_footprint",
                        "source_id": i,
                        "prescribed_geometry_area_m2": geometry.area,
                    },
                )
                for i, geometry in ignition_footprints.items()
            ),
        ):
            if not geometry.is_empty:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(transform(project.transform, geometry)),
                        "properties": {"plan_id": identifier, **properties},
                    }
                )
        write_json(
            export_path(stem, ".geojson"), {"type": "FeatureCollection", "features": features}
        )
        receipt = {
            "schema_version": "heritage-area-action-map-1",
            "plan_id": identifier,
            "status_label": status_label,
            "map_title": title,
            "label_mode": label_mode,
            "common_comparison_view_extents": common_views,
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "search_sha256": None
            if search_path is None
            else hashlib.sha256(search_bytes).hexdigest(),
            "inventory_sha256": inventory.sha256,
            "source_crs": grid.crs,
            "geojson_crs": "EPSG:4326",
            "roof_action_rows": rows,
            "demolished_ids": sorted(selected),
            **ledger,
            "cleared_physical_cell_ids": cells,
            "vegetation_ground_footprint_area_m2": ground_area,
            "vegetation_covered_fuel_area_m2": sum(r["covered_fuel_area_m2"] for r in grass_rows),
            "vegetation_action_rows": grass_rows,
            "actual_source_building_ids": sorted(sources),
            "actual_source_families_by_building_id": sources,
            "cleared_action_geometry_union_area_m2": cost,
            "new_CA_checks": 0,
            "map_does_not_add_a_protection_or_optimality_certificate": True,
        }
        for suffix, table, empty_columns in (
            (".roof-ids.csv", rows, ["building_id", "action"]),
            (".grass-ids.csv", grass_rows, ["physical_cell_id", "construction_footprint_area_m2"]),
        ):
            with export_path(stem, suffix).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=list(table[0]) if table else empty_columns
                )
                writer.writeheader()
                writer.writerows(table)
        write_json(export_path(stem, ".json"), receipt)
        outputs.append(
            {
                "plan_id": identifier,
                "area_m2": cost,
                "png": str(export_path(stem, ".png")),
                "pdf": str(export_path(stem, ".pdf")),
                "geojson": str(export_path(stem, ".geojson")),
                "receipt": str(export_path(stem, ".json")),
            }
        )
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--search", type=Path)
    parser.add_argument("--plan-id")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Light.ttc")
    )
    parser.add_argument("--status-label", default="候选位置；保护效果以对应实验为准")
    parser.add_argument("--label-mode", choices=("selected", "core-sources"), default="selected")
    parser.add_argument("--title")
    parser.add_argument("--common-views", action="store_true")
    args = parser.parse_args()
    print(
        render(
            args.config,
            args.output,
            search_path=args.search,
            plan_id=args.plan_id,
            font_path=args.font,
            status_label=args.status_label,
            label_mode=args.label_mode,
            title=args.title,
            common_views=args.common_views,
        )
    )


if __name__ == "__main__":
    main()
