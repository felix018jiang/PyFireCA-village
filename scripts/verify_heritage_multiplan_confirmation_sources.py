"""Audit retained confirmatory footprints and the native common-mesh resource bounds."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely import STRtree, union_all
from shapely.geometry import shape

FIELDS = ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")


def check_support(layout, owner, geometry, removed, parent_budget):
    indices = np.flatnonzero(np.asarray(layout.ignition_owner, dtype=object) == owner)
    if owner in removed or not len(indices) or any(layout.owners[i] != (owner,) for i in indices):
        raise ValueError("native ignition is not uniquely supported by its retained owner")
    for key in ("domain_mask", "known_mask", "burnable_mask"):
        if not getattr(layout.node_inputs, key).ravel()[indices].all():
            raise ValueError("native source is not on known, in-domain burnable support")
    parameters = layout.node_inputs.parameters
    inputs = [getattr(parameters, key).ravel()[indices] for key in FIELDS]
    sums = [float(arr.sum()) for arr in inputs]
    if any(np.any(arr <= 0) for arr in inputs) or not np.allclose(
        sums, parent_budget, rtol=1e-9, atol=1e-7
    ):
        raise ValueError("native supported area, mass or available energy differs")
    union = union_all([layout.geometry[i] for i in indices])
    missing, extra = geometry.difference(union).area, union.difference(geometry).area
    if max(missing, extra) > 1e-8 or abs(sums[0] - 25) > 1e-6:
        raise ValueError("native footprint differs physically from the 25m2 registered area")
    return {
        "native_budget": dict(zip(FIELDS, sums, strict=True)),
        "footprint_missing_m2": missing,
        "footprint_extra_m2": extra,
    }


def audit(directory, run, *, count_mask_stages=False):
    snapshot = run.resolve() / "source-snapshot"
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import run_heritage_joint_comparison as native

    if Path(native.__file__).resolve() != snapshot / "scripts/run_heritage_joint_comparison.py":
        raise RuntimeError("native frozen reader was not selected")
    cfg = directory / "confirmation-longcap-config.json"
    config = native.read_json(cfg)
    binding = native.read_json(directory / "original-binding/binding-receipt.json")
    if not all(
        native.file_sha256(p) == h for p, h in config["additional_frozen_inputs_sha256"].items()
    ):
        raise ValueError("confirmation dependencies changed")
    oracle, _ = native.make_oracle(cfg, config, native.file_sha256(native.__file__))
    base = oracle.base
    polygons = np.asarray(base.geometry, dtype=object)
    tree = STRtree(polygons)
    rows = []
    for position, bound in enumerate(binding["common_source_and_refinement_requirements"], 1):
        fire = oracle.fires[bound["fire_id"]]
        geometries = {}
        registered = {p["plan_id"]: p for p in bound["per_plan"]}
        for plan_id, p in registered.items():
            plan = oracle.plans[plan_id]
            priority = fire["protocol"]["draws"][0]["priority_ids"]
            actual = next(owner for owner in priority if owner not in plan.demolished_ids)
            if (
                p["source_id"] != actual
                or fire["footprints"][actual].sha256 != p["footprint_sha256"]
            ):
                raise ValueError("bound action-specific source differs from native selection")
            if actual in geometries:
                continue
            footprint = shape(bound["actual_source_footprints"][actual]["geometry"])
            if abs(footprint.area - 25.0) > 1e-8:
                raise ValueError("registered footprint area differs")
            if footprint.difference(oracle.inventory.by_id[actual].geometry).area > 1e-8:
                raise ValueError("registered footprint extends outside its retained owner")
            ii = tree.query(footprint, predicate="intersects")
            areas = np.asarray([polygons[i].intersection(footprint).area for i in ii])
            ii, areas = ii[areas > 0], areas[areas > 0]
            allowed = np.array([base.owners[i] == (actual,) for i in ii])
            if areas[~allowed].sum() > 1e-8 or abs(float(areas[allowed].sum()) - 25) > 1e-8:
                raise ValueError("actual footprint has unresolved original support")
            jj = ii[allowed]
            for key in ("domain_mask", "known_mask", "burnable_mask"):
                if not getattr(base.node_inputs, key).ravel()[jj].all():
                    raise ValueError("original registered source is not on known retained fuel")
            fractions = areas[allowed] / np.array([polygons[j].area for j in jj])
            budget = [
                float(np.sum(getattr(base.node_inputs.parameters, k).ravel()[jj] * fractions))
                for k in FIELDS
            ]
            geometries[actual] = footprint, budget
        for scale in oracle.scales_m:
            layout = native.refine_roof_layout(
                base,
                oracle.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=oracle.max_nodes,
            )
            observed = {}
            for plan_id, p in registered.items():
                actual = p["source_id"]
                geom, budget = geometries[actual]
                proof = check_support(
                    layout, actual, geom, oracle.plans[plan_id].demolished_ids, budget
                )
                observed[plan_id] = {"footprint_sha256": p["footprint_sha256"], **proof}
            by_plan = {}
            if count_mask_stages:
                from heritage_confirmation_stage_capacity import stage_counts

                bounds, by_plan = stage_counts(
                    layout, oracle.transport, fire["refined_ids"], oracle.plans.values()
                )
            else:
                bounds = native.edge_bounds(layout, oracle.transport, fire["refined_ids"])
            passed = (
                max(bounds.values()) <= oracle.max_radiation_edges
                and len(layout.owners) <= oracle.max_nodes
            )
            rows.append(
                {
                    "draw_ordinal": position,
                    "scale_m": scale,
                    "nodes": len(layout.owners),
                    **bounds,
                    "preflight_passed": passed,
                    "physical_support_by_plan": observed,
                    "actual_graph_stage_bound_by_plan": by_plan,
                }
            )
            print("verified source occupancy", position, scale, "capacity", passed, flush=True)
            if not passed and not count_mask_stages:
                return rows, False
    return rows, all(row["preflight_passed"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation-dir", type=Path, required=True)
    parser.add_argument("--native-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplementary-stage-counts", action="store_true")
    args = parser.parse_args()
    rows, ok = audit(
        args.confirmation_dir.resolve(),
        args.native_run.resolve(),
        count_mask_stages=args.supplementary_stage_counts,
    )
    result = {
        "schema_version": "registered-multiarm-realized-footprint-capacity-1",
        "all_eight_common_layouts_with_physical_sources_passed": ok,
        "rows": rows,
        "new_CA_checks": 0,
        "source_redraws": 0,
        "registered_capacity_limits_changed": False,
        "supplemental_diagnostics_for_all_eight_common_layouts": args.supplementary_stage_counts,
        "if_capacity_exceeded": "all unobserved confirmatory outcomes remain unknown",
    }
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
