"""Audit the actual discretized source support of a frozen confirmation without CA."""

import argparse
from pathlib import Path

import numpy as np
import prepare_heritage_batuan_final_confirmation as frozen
from prepare_heritage_joint_longcap import encoded, immutable
from shapely import STRtree, union_all
from shapely.geometry import shape

from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json

FIELDS = ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")


def check_support(layout, actual, footprint, removed, parent_budget):
    direct = np.flatnonzero(np.asarray(layout.ignition_owner, dtype=object) == actual)
    params = layout.node_inputs.parameters
    budget = [float(getattr(params, name).ravel()[direct].sum()) for name in FIELDS]
    if not len(direct) or any(layout.owners[i] != (actual,) for i in direct) or actual in removed:
        raise ValueError("actual native support must lie on a uniquely owned retained roof")
    for mask in ("domain_mask", "known_mask", "burnable_mask"):
        if not getattr(layout.node_inputs, mask).ravel()[direct].all():
            raise ValueError("native ignition must lie on known, in-domain, burnable support")
    if any(np.any(getattr(params, name).ravel()[direct] <= 0) for name in FIELDS):
        raise ValueError("each actual native source node requires positive physical support")
    union = union_all([layout.geometry[i] for i in direct])
    missing, extra = footprint.difference(union).area, union.difference(footprint).area
    if max(missing, extra) > 1e-8 or abs(budget[0] - 25) > 1e-6:
        raise ValueError("native support differs from the complete frozen physical footprint")
    if not np.allclose(budget, parent_budget, rtol=1e-9, atol=1e-7):
        raise ValueError("native physical support differs from independent base integration")
    return {
        "native_budget": dict(zip(FIELDS, budget, strict=True)),
        "footprint_missing_m2": missing,
        "footprint_extra_m2": extra,
        "native_unique_retained_known_burnable_support": True,
    }


def verify(directory):
    config_path = directory / "confirmation-longcap-config.json"
    config = read_json(config_path)
    bindings = read_json(directory / "original-binding/binding-receipt.json")
    declared = config["additional_frozen_inputs_sha256"]
    if not all(file_sha256(p) == digest for p, digest in declared.items()):
        raise ValueError("a preregistered input changed")
    oracle, _ = frozen.runner.make_oracle(config_path, config, file_sha256(frozen.runner.__file__))
    try:
        frozen.source_guard(oracle, bindings)
        original_error = None
    except ValueError as exc:
        original_error = str(exc)
        if (
            original_error
            != "actual source is ambiguous, unknown, cleared or physically incomplete"
        ):
            raise
    base = oracle.base
    polygons = np.asarray(base.geometry, dtype=object)
    tree = STRtree(polygons)
    (plan,) = oracle.plans.values()
    rows = []
    for index, bound in enumerate(bindings["common_source_and_refinement_requirements"], start=1):
        label = bound["fire_id"]
        fire = oracle.fires[label]
        (expected,) = bound["per_plan"]
        actual = next(
            i for i in fire["protocol"]["draws"][0]["priority_ids"] if i not in plan.demolished_ids
        )
        geometry = shape(bound["actual_source_footprints"][actual]["geometry"])
        if (
            actual != expected["source_id"]
            or fire["footprints"][actual].sha256 != expected["footprint_sha256"]
            or abs(geometry.area - 25) > 1e-8
            or geometry.difference(oracle.inventory.by_id[actual].geometry).area > 1e-8
            or not set(bound["minimum_common_refined_ids"]) <= set(fire["refined_ids"])
        ):
            raise ValueError("binding and realized source differ")
        nodes = tree.query(geometry, predicate="intersects")
        areas = np.asarray([polygons[i].intersection(geometry).area for i in nodes])
        hits = areas > 0
        nodes, areas = nodes[hits], areas[hits]
        allowed = np.asarray([base.owners[i] == (actual,) for i in nodes])
        stray = float(areas[~allowed].sum())
        # Apply the frozen native mesh ambiguity threshold to the sum over the
        # entire footprint, then verify its true assigned nodes at each scale.
        if stray > 1e-8 or abs(areas[allowed].sum() - 25) > 1e-8:
            raise ValueError("material ambiguous or incomplete support on base polygons")
        keep, weights = nodes[allowed], areas[allowed]
        for name in ("domain_mask", "known_mask", "burnable_mask"):
            if not getattr(base.node_inputs, name).ravel()[keep].all():
                raise ValueError("physical parent support invalid")
        fractions = weights / np.asarray([polygons[i].area for i in keep])
        reference = [
            float(np.sum(getattr(base.node_inputs.parameters, name).ravel()[keep] * fractions))
            for name in FIELDS
        ]
        last_budget = None
        for scale in oracle.scales_m:
            layout = refine_roof_layout(
                base,
                oracle.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=oracle.max_nodes,
            )
            result = check_support(layout, actual, geometry, plan.demolished_ids, reference)
            budget = list(result["native_budget"].values())
            if last_budget is not None and not np.allclose(
                last_budget, budget, rtol=1e-9, atol=1e-7
            ):
                raise ValueError("physical budget changed across scales")
            bounds = frozen.runner.edge_bounds(layout, oracle.transport, fire["refined_ids"])
            row = {
                "draw_index": index,
                "scale_m": scale,
                "foreign_base_overlap_m2": stray,
                "nodes": len(layout.owners),
                **bounds,
                **result,
            }
            rows.append(row)
            if (
                len(layout.owners) > oracle.max_nodes
                or max(bounds.values()) > oracle.max_radiation_edges
            ):
                raise ValueError("pre-registered geometric capacity exceeded")
            last_budget = budget
            print("geometry and source support verified", index, scale, flush=True)
    if len(rows) != 8 or oracle._engine is not None:
        raise ValueError("eight graph-free native layouts required")
    return {
        "schema_version": "independent-confirmation-realized-source-support-audit-1",
        "frozen_config_sha256": file_sha256(config_path),
        "verifier_sha256": file_sha256(__file__),
        "original_preflight_error": original_error,
        "source_or_physics_changes": False,
        "redraws": 0,
        "new_CA_checks": 0,
        "capacity_and_source_passed": True,
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    immutable(args.output, encoded(verify(args.confirmation_dir.resolve())))
