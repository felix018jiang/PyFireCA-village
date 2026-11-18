"""Freeze geometry-only roof-free vegetation strips using old baseline design data.

No CA is executed. Only the archived no-intervention drum-side h1 trajectory
guides the early-connection proposal; comparison-arm outcomes are never read.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from shapely import distance
from shapely.geometry import box, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
DESIGN_RADIUS_M = 100.0
MAX_STRIP_CELLS = 4
EARLY_WINDOW_S = 300.0


def load_baseline(root, fire_id):
    experiment = read_json(root / "experiment.json")
    matching = []
    for path in sorted((root / "evaluations").glob("*/complete.json")):
        manifest = read_json(path)
        if manifest["demolished_ids"] == [] and manifest["scenario_id"] == f"{fire_id}-h1":
            matching.append((path.parent, manifest))
    if len(matching) != 1:
        raise ValueError("one archived no-intervention h1 design trajectory required")
    folder, manifest = matching[0]
    if manifest["problem_sha256"] != experiment["problem_sha256"]:
        raise ValueError("archived design trajectory has a different frozen problem")
    for name in ("evaluation.json", "check.json", "layout.npz", "final-state.npz"):
        if file_sha256(folder / name) != manifest["files_sha256"][name]:
            raise ValueError(f"archived design artifact changed: {name}")
    evaluation = read_json(folder / "evaluation.json")
    check = read_json(folder / "check.json")
    if evaluation["diagnostics"]["invalid_reasons"]:
        raise ValueError("invalid baseline cannot guide a design proposal")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        layout = {name: saved[name].copy() for name in saved.files}
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        ignition = saved["ignition_time_s"].ravel().copy()
        final_time = float(saved["time_s"])
    if final_time != 3600.0 or len(ignition) != len(layout["physical_cell_id"]):
        raise ValueError("complete 3600 s baseline and matching layout are required")
    return folder, manifest, evaluation, check, layout, ignition


def cell_geometry(grid, cell):
    row, col = divmod(int(cell), grid.width)
    x, y = grid.center(row, col)
    half = grid.cell_size_m / 2
    return box(x - half, y - half, x + half, y + half)


def straight_strips(cells, grid, length):
    available = set(cells)
    for cell in sorted(available):
        row, col = divmod(cell, grid.width)
        for dr, dc in ((0, 1), (1, 0)):
            end_r, end_c = row + dr * (length - 1), col + dc * (length - 1)
            if end_r >= grid.height or end_c >= grid.width:
                continue
            strip = tuple(cell + step * (dr * grid.width + dc) for step in range(length))
            if set(strip) <= available:
                yield strip


def prepare_village(village):
    config_path = (
        ROOT / f"examples/research/heritage/followup-20261103/{village}-fixed-comparison.json"
    )
    config = read_json(config_path)
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, _, inventory = load_study(study_path)
    parent, grid = bundle.request, bundle.request.grid
    if grid.cell_size_m != 5.0:
        raise ValueError("joint candidates require complete original 5 m parent cells")
    fire = next(f for f in config["source_fires"] if f["fire_id"].startswith("drum-"))
    root = ROOT / f"runs/research/heritage-followup-{village}-20261103"
    folder, manifest, evaluation, check, old_layout, ignition = load_baseline(root, fire["fire_id"])
    parent_sha256 = request_fingerprint(parent)
    if (
        parent_sha256 != evaluation["case_sha256"]
        or tuple(old_layout["physical_grid_shape"]) != grid.shape
    ):
        raise ValueError("current parent inputs differ from the archived design baseline")
    if check["evidence"].get("source_replaced", False):
        raise ValueError("joint proposals require the unchanged declared design source")
    source_id = check["evidence"]["source_id"]
    source = inventory.by_id[source_id].geometry
    core = min(
        inventory.targets,
        key=lambda t: (source.distance(inventory.by_id[t.building_id].geometry), t.building_id),
    )
    core_geometry = inventory.by_id[core.building_id].geometry
    roof_cells = set(
        int(c)
        for c, n in zip(
            old_layout["physical_cell_id"], np.diff(old_layout["owner_offsets"]), strict=True
        )
        if n
    )
    eligible_mask = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    if roof_cells:
        eligible_mask[list(roof_cells)] = False
    eligible = np.flatnonzero(eligible_mask)
    centers = {}
    for cell in eligible:
        row, col = divmod(int(cell), grid.width)
        centers[int(cell)] = tuple(float(v) for v in grid.center(row, col))
    source_xy = np.asarray(source.centroid.coords[0])
    core_xy = np.asarray(core_geometry.centroid.coords[0])
    window = [
        int(cell)
        for cell in eligible
        if min(
            np.linalg.norm(np.asarray(centers[int(cell)]) - source_xy),
            np.linalg.norm(np.asarray(centers[int(cell)]) - core_xy),
        )
        <= DESIGN_RADIUS_M
    ]
    shapes = {cell: cell_geometry(grid, cell) for cell in window}
    cells_array = np.asarray(window, dtype=int)
    polygons = np.asarray([shapes[cell] for cell in window], dtype=object)
    roofs = unary_union([b.geometry for b in inventory.buildings])
    if any(geometry.intersection(roofs).area > 1e-7 for geometry in polygons):
        raise ValueError("a proposed vegetation design cell contains a roof footprint")
    roof_gap = dict(zip(window, distance(polygons, roofs), strict=True))
    core_gap = dict(zip(window, distance(polygons, core_geometry), strict=True))
    source_gap = dict(zip(window, distance(polygons, source), strict=True))
    arrival = np.full(grid.height * grid.width, np.inf)
    no_roof = np.diff(old_layout["owner_offsets"]) == 0
    finite = no_roof & np.isfinite(ignition)
    np.minimum.at(arrival, old_layout["physical_cell_id"][finite], ignition[finite])
    hot = cells_array[np.isfinite(arrival[cells_array])]
    if not len(hot):
        raise ValueError(
            "no observed roof-free vegetation connection in the declared design window"
        )
    earliest = min(hot, key=lambda c: (arrival[c], int(c)))
    first = float(arrival[earliest])
    direction = np.asarray(centers[int(earliest)]) - source_xy
    direction /= max(float(np.linalg.norm(direction)), 1e-12)
    covered = parent.parameters.covered_area_m2.ravel()

    def strip_stats(strip):
        times = arrival[list(strip)]
        xy = np.asarray([centers[c] for c in strip])
        axis = xy[-1] - xy[0]
        axis /= max(float(np.linalg.norm(axis)), 1e-12)
        return {
            "early_vegetation_cell_count": int((times <= first + EARLY_WINDOW_S).sum()),
            "observed_ignited_vegetation_cell_count": int(np.isfinite(times).sum()),
            "first_observed_vegetation_ignition_s": float(times.min())
            if np.isfinite(times).any()
            else None,
            "capped_mean_design_ignition_s": float(np.minimum(times, 3900).mean()),
            "mean_roof_gap_m": float(np.mean([roof_gap[c] for c in strip])),
            "mean_primary_core_gap_m": float(np.mean([core_gap[c] for c in strip])),
            "minimum_primary_core_gap_m": float(min(core_gap[c] for c in strip)),
            "mean_source_gap_m": float(np.mean([source_gap[c] for c in strip])),
            "parallel_alignment_with_first_vegetation_direction": float(
                abs(np.dot(axis, direction))
            ),
            "covered_fuel_area_m2": float(covered[list(strip)].sum()),
        }

    options = []
    length = MAX_STRIP_CELLS
    while length >= 2:
        options = [(strip, strip_stats(strip)) for strip in straight_strips(window, grid, length)]
        if len(options) >= 3:
            break
        length -= 1
    if length < 2:
        raise ValueError("not enough connected roof-free whole-cell strips")
    early, early_stats = min(
        options,
        key=lambda item: (
            -item[1]["early_vegetation_cell_count"],
            -item[1]["observed_ignited_vegetation_cell_count"],
            item[1]["capped_mean_design_ignition_s"],
            -item[1]["parallel_alignment_with_first_vegetation_direction"],
            item[1]["mean_roof_gap_m"],
            item[0],
        ),
    )
    # A dense roof neighborhood may have only one eligible 5 m gap cell.
    # Do not move a nominal four-cell band far away and call it core-adjacent.
    core_window = [cell for cell in window if core_gap[cell] <= 15 and cell not in early]
    core_options = []
    for core_length in range(MAX_STRIP_CELLS, 0, -1):
        core_options = [
            (strip, strip_stats(strip)) for strip in straight_strips(core_window, grid, core_length)
        ]
        if core_options:
            break
    if not core_options:
        raise ValueError("no roof-free known-burnable parent cell within the core design window")
    core_strip, core_stats = min(
        core_options,
        key=lambda item: (item[1]["mean_primary_core_gap_m"], item[1]["mean_roof_gap_m"], item[0]),
    )
    used = set(early) | set(core_strip)
    control, control_stats = min(
        (item for item in options if not set(item[0]) & used),
        key=lambda item: (
            abs(item[1]["covered_fuel_area_m2"] - early_stats["covered_fuel_area_m2"]),
            sum(
                abs(item[1][name] - early_stats[name])
                for name in ("mean_primary_core_gap_m", "mean_source_gap_m", "mean_roof_gap_m")
            ),
            item[0],
        ),
    )
    building_times = evaluation["metrics"]["building_first_ignitions"]
    building_tree = STRtree([b.geometry for b in inventory.buildings])
    initial_roof_ids = next(
        p["demolished_ids"] for p in config["plans"] if p["plan_id"] == "selected"
    )

    def record_strip(label, strip, stats, selection):
        rows = []
        for cell in strip:
            row, col = divmod(cell, grid.width)
            when = float(arrival[cell]) if np.isfinite(arrival[cell]) else None
            nearby = building_tree.query(shapes[cell], predicate="dwithin", distance=15.0)
            relationships = []
            for index in nearby:
                building = inventory.buildings[index]
                roof_time = building_times.get(building.building_id)
                relation = (
                    "no_roof_ignition_observed"
                    if roof_time is None
                    else "vegetation_unignited_in_design"
                    if when is None
                    else "roof_earlier"
                    if roof_time < when
                    else "roof_later"
                    if roof_time > when
                    else "same_time"
                )
                relationships.append(
                    {
                        "building_id": building.building_id,
                        "footprint_gap_m": float(shapes[cell].distance(building.geometry)),
                        "baseline_first_ignition_s": roof_time,
                        "temporal_relation": relation,
                    }
                )
            relationships.sort(key=lambda r: (r["footprint_gap_m"], r["building_id"]))
            material = int(parent.material_index.ravel()[cell])
            cover = int(parent.landcover_index.ravel()[cell])
            rows.append(
                {
                    "physical_cell_id": cell,
                    "row": row,
                    "col": col,
                    "center_xy_m": list(centers[cell]),
                    "construction_footprint_area_m2": grid.cell_area_m2,
                    "covered_fuel_area_m2": float(covered[cell]),
                    "dry_fuel_mass_kg": float(
                        parent.parameters.initial_dry_fuel_mass_kg.ravel()[cell]
                    ),
                    "pyrolysable_fuel_mass_kg": float(
                        parent.parameters.pyrolysable_dry_fuel_mass_kg.ravel()[cell]
                    ),
                    "material_index": material,
                    "material_label": bundle.material_labels.get(material),
                    "landcover_index": cover,
                    "landcover_label": bundle.landcover_labels.get(cover),
                    "baseline_first_ignition_s": when,
                    "primary_core_footprint_gap_m": float(core_gap[cell]),
                    "nearby_roof_temporal_relations_within_15m": relationships,
                    "geometry": mapping(shapes[cell]),
                }
            )
        union = unary_union([shapes[c] for c in strip])
        footprint = len(strip) * grid.cell_area_m2
        if abs(union.area - footprint) > 1e-6 or union.geom_type != "Polygon":
            raise ValueError("strip must be nonoverlapping and face-connected")
        if any(not eligible_mask[c] or c in roof_cells for c in strip):
            raise ValueError("nonroof known-burnable eligibility was violated")
        return {
            "plan_id": f"{village}-{label}",
            "proposal_class": label,
            "cleared_physical_cell_ids": list(strip),
            "suggested_joint_demolished_ids": initial_roof_ids,
            "vegetation_parent_cell_count": len(strip),
            "construction_footprint_area_m2": footprint,
            "covered_fuel_clearing_area_m2": float(covered[list(strip)].sum()),
            "selection_rule": selection,
            "design_trajectory_statistics": stats,
            "physical_checks": {
                "all_known": True,
                "all_burnable": True,
                "all_parent_cells_5m": True,
                "roof_overlap_area_m2": float(union.intersection(roofs).area),
                "face_connected": True,
            },
            "implementation_eligibility_surveyed": False,
            "protection_or_fire_loss_effect_evaluated": False,
            "geometry": mapping(union),
            "cells": rows,
        }

    plans = [
        record_strip(
            "early-vegetation-connection",
            early,
            early_stats,
            (
                "maximum cells in first-vegetation+300s window, then observed vegetation "
                "connection count, mean arrival, source direction alignment, roof gap;"
                "baseline design data only"
            ),
        ),
        record_strip(
            "core-geometric-gap",
            core_strip,
            core_stats,
            (
                "longest available strip up to four cells wholly within 15m of source-side core, "
                "then minimum mean core/roof gap;disjoint from connection strip;"
                "no fire outcome ranking;15m is a design window, not a safety distance"
            ),
        ),
        record_strip(
            "same-area-geometric-control",
            control,
            control_stats,
            (
                "disjoint same-cell-count strip matched first by covered fuel area, then "
                "sum of absolute core/source/roof mean-distance differences;"
                "no ignition-time ranking"
            ),
        ),
    ]
    plans[-1]["area_match_reference_plan_id"] = plans[0]["plan_id"]
    plans[-1]["covered_fuel_area_difference_from_reference_m2"] = (
        plans[-1]["covered_fuel_clearing_area_m2"] - plans[0]["covered_fuel_clearing_area_m2"]
    )
    plans[-1]["geometry_match_absolute_distance_differences_m"] = {
        name: abs(control_stats[name] - early_stats[name])
        for name in ("mean_primary_core_gap_m", "mean_source_gap_m", "mean_roof_gap_m")
    }
    return {
        "village_id": village,
        "inventory_object_count": len(inventory.buildings),
        "design_source_id": source_id,
        "design_fire_id": fire["fire_id"],
        "design_support_m": 1.0,
        "design_horizon_s": 3600.0,
        "design_data_role": "archived_no_intervention_trajectory;not_independent_validation",
        "design_artifact_folder": str(folder.relative_to(ROOT)),
        "design_problem_sha256": manifest["problem_sha256"],
        "design_files_sha256": {
            name: manifest["files_sha256"][name]
            for name in ("evaluation.json", "check.json", "layout.npz", "final-state.npz")
        },
        "study_path": str(study_path.relative_to(ROOT)),
        "study_sha256": file_sha256(study_path),
        "parent_case_sha256": parent_sha256,
        "inventory_sha256": inventory.sha256,
        "primary_core_id": core.building_id,
        "baseline_core_outcomes": evaluation["metrics"]["core_outcomes"],
        "first_vegetation_ignition_s": first,
        "source_to_first_vegetation_direction_unit_xy": direction.tolist(),
        "baseline_nonroof_ignited_area_m2": float(old_layout["covered_area_m2"][finite].sum()),
        "whole_village_eligible_vegetation_parent_cell_count": len(eligible),
        "design_window_radius_m": DESIGN_RADIUS_M,
        "design_window_eligible_cell_count": len(window),
        "minimum_eligible_cell_gap_to_primary_core_m": float(min(core_gap.values())),
        "eligible_cells_within_15m_of_primary_core": int(
            sum(gap <= 15 for gap in core_gap.values())
        ),
        "core_gap_feasibility_note": (
            "closest available whole-cell band;not a continuous core enclosure;"
            "surveyed access_and_cultural_space_eligibility_unknown"
        ),
        "enumerated_straight_strip_count": len(options),
        "selected_strip_length_cells": length,
        "physical_grid": {
            "shape": list(grid.shape),
            "cell_size_m": grid.cell_size_m,
            "x_min_m": grid.x_min_m,
            "y_max_m": grid.y_max_m,
            "cell_id_policy": "row_major_parent_grid;not_thermal_storage_index",
        },
        "causal_interpretation": (
            "nearby roof/vegetation event ordering is a design cue, "
            "not identified causal heat transfer"
        ),
        "scope": (
            "three_local_joint_candidates;no_effectiveness_or_optimality_claim;"
            "field_eligibility_pending"
        ),
        "plans": plans,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/results/heritage-joint-proposals-20261110.json"
    )
    args = parser.parse_args()
    started = time.monotonic()
    villages = []
    for village in ("batuan", "pingyan"):
        row = prepare_village(village)
        villages.append(row)
        print(
            {
                "village": village,
                "strips": [
                    {
                        "class": p["proposal_class"],
                        "cells": p["cleared_physical_cell_ids"],
                        "footprint": p["construction_footprint_area_m2"],
                        "covered": p["covered_fuel_clearing_area_m2"],
                    }
                    for p in row["plans"]
                ],
            },
            flush=True,
        )
    report = {
        "schema_version": "heritage-joint-proposals-1",
        "date": "2026-11-10",
        "new_CA_checks": 0,
        "generator_sha256": file_sha256(Path(__file__)),
        "construction_and_covered_area_are_separate": True,
        "independent_future_validation_required": True,
        "global_optimality_proven": False,
        "elapsed_s": time.monotonic() - started,
        "villages": villages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)


if __name__ == "__main__":
    main()
