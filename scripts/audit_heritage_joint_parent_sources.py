"""Independently audit exact plans against raw parent cells and source geometry.

No comparison oracle, radiation/contact builder or CA is invoked. Source budgets
are integrated directly over the original physical partition and footprint.
"""

from __future__ import annotations

import argparse
from math import fsum
from pathlib import Path

import numpy as np
import shapely
from prepare_heritage_all_roof_bands import parent_cell_polygons
from prepare_heritage_joint_longcap import encoded, immutable
from shapely.geometry import shape
from shapely.strtree import STRtree

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def audit(config_paths):
    configs = [(Path(p).resolve(), read_json(p)) for p in config_paths]
    studies = {(p.parent / c["study_path"]).resolve() for p, c in configs}
    if len(studies) != 1:
        raise ValueError("one physical study required for a paired readiness audit")
    bundle, _, inventory = load_study(next(iter(studies)))
    parent = bundle.request
    if parent.grid.cell_size_m != 5:
        raise ValueError("original whole 5m parent grid required")
    base = resolve_roof_layout(
        parent,
        inventory,
        max_nodes=configs[0][1]["max_nodes"],
        core_overlap_policy=configs[0][1].get("core_overlap_policy", "reject"),
    )
    polygons = np.asarray(base.geometry, dtype=object)
    cells = parent_cell_polygons(parent.grid)
    roof_cells = {
        int(cell) for cell, owners in zip(base.cell_id, base.owners, strict=True) if owners
    }
    all_roofs = shapely.union_all([b.geometry for b in inventory.buildings])
    tree = STRtree(polygons)
    core = {target.building_id for target in inventory.targets}
    all_rows, plan_rows, references = [], [], {}
    for path, config in configs:
        references[str(path)] = file_sha256(path)
        for source_path, expected in config.get("additional_frozen_inputs_sha256", {}).items():
            if file_sha256(source_path) != expected:
                raise ValueError("a bound input changed")
            references[source_path] = expected
        reference_path = (path.parent / config["reference_master_experiment_path"]).resolve()
        references[str(reference_path)] = file_sha256(reference_path)
        if references[str(reference_path)] != config["reference_master_experiment_sha256"]:
            raise ValueError("frozen physical mesh reference changed")
        reference = read_json(reference_path)
        if content_hash(reference["problem"]) != reference["problem_sha256"]:
            raise ValueError("reference problem fingerprint differs")
        for plan in config["plans"]:
            removed = set(plan["demolished_ids"])
            if removed & core or not removed <= set(inventory.candidate_ids):
                raise ValueError("a roof action removes a core or ineligible owner")
            ground = plan["cleared_physical_cell_ids"]
            if ground != sorted(set(ground)) or any(
                isinstance(cell, bool)
                or not isinstance(cell, int)
                or not 0 <= cell < parent.grid.width * parent.grid.height
                for cell in ground
            ):
                raise ValueError("noncanonical or out-of-domain parent IDs")
            ids = np.asarray(ground, dtype=np.int64)
            if roof_cells.intersection(ground) or any(
                not getattr(parent, name).ravel()[ids].all()
                for name in ("domain_mask", "known_mask", "burnable_mask")
            ):
                raise ValueError("ground action touches a roof, unknown or nonfuel parent")
            if np.any(parent.building_index.ravel()[ids] >= 0) or np.any(
                parent.parameters.covered_area_m2.ravel()[ids] <= 0
            ):
                raise ValueError("ground action lacks positive nonroof fuel")
            ground_geometry = shapely.union_all(cells[ids])
            if (
                ground_geometry.intersection(all_roofs).area > 1e-8
                or abs(ground_geometry.area - len(ids) * 25) > 1e-6
            ):
                raise ValueError("ground action is not a disjoint whole-parent footprint")
            cleared = np.asarray([bool(g) and set(g) <= removed for g in base.owners])
            roof_C = fsum(float(p.area) for p in polygons[cleared])
            plan_rows.append(
                {
                    "config_path": str(path),
                    "plan_id": plan["plan_id"],
                    "D_count": len(removed),
                    "G_count": len(ids),
                    "roof_geometry_union_C_m2": roof_C,
                    "whole_parent_construction_C_m2": len(ids) * 25,
                    "ground_covered_fuel_area_m2": float(
                        parent.parameters.covered_area_m2.ravel()[ids].sum()
                    ),
                    "total_construction_C_m2": roof_C + len(ids) * 25,
                    "raw_parent_and_vector_roof_checks_passed": True,
                }
            )
            for fire_id, fire in reference["problem"]["fires"].items():
                priority = fire["protocol"]["draws"][0]["priority_ids"]
                source = next((i for i in priority if i not in removed), None)
                if source is None or source not in fire["footprints"] or source in core:
                    raise ValueError("no retained ordinary source in the prepared prefix")
                footprint = shape(fire["footprints"][source]["geometry"])
                if (
                    abs(footprint.area - 25) > 1e-8
                    or footprint.difference(inventory.by_id[source].geometry).area > 1e-8
                    or footprint.intersection(ground_geometry).area > 1e-8
                ):
                    raise ValueError("source footprint is not retained in full")
                nodes = tree.query(footprint, predicate="intersects")
                intersect = shapely.area(shapely.intersection(polygons[nodes], footprint))
                nodes, intersect = nodes[intersect > 1e-10], intersect[intersect > 1e-10]
                if not len(nodes) or any(base.owners[n] != (source,) or cleared[n] for n in nodes):
                    raise ValueError("source is not unambiguously retained on every physical patch")
                if abs(fsum(intersect) - footprint.area) > 1e-6:
                    raise ValueError("physical source support does not cover the full footprint")
                fractions = intersect / shapely.area(polygons[nodes])
                budget = {
                    key: fsum(getattr(base.node_inputs.parameters, key).ravel()[nodes] * fractions)
                    for key in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
                }
                if abs(budget["covered_area_m2"] - 25) > 1e-6 or any(
                    v <= 0 for v in budget.values()
                ):
                    raise ValueError("retained source has lost its full physical ignition budget")
                all_rows.append(
                    {
                        "config_path": str(path),
                        "plan_id": plan["plan_id"],
                        "fire_id": fire_id,
                        "first_retained_source_id": source,
                        "source_in_prepared_prefix": True,
                        "source_footprint_sha256": content_hash(fire["footprints"][source]),
                        "source_footprint_geometry_area_m2": footprint.area,
                        "independently_integrated_source_budget": budget,
                        "G_count": len(ids),
                        "all_G_parent_checks_passed": True,
                    }
                )
    if any(file_sha256(p) != digest for p, digest in references.items()):
        raise ValueError("a frozen input changed during the audit")
    return {
        "schema_version": "heritage-joint-independent-parent-source-audit-1",
        "source_matrix": all_rows,
        "plan_parent_cost_matrix": plan_rows,
        "input_sha256": references,
        "all_checks_passed": True,
        "source_covered_budget_absolute_tolerance_m2": 1e-6,
        "source_footprint_geometry_absolute_tolerance_m2": 1e-8,
        "uses_comparison_oracle_or_native_solver": False,
        "radiation_or_contact_graphs_built": 0,
        "new_CA_checks": 0,
        "protection_or_fire_loss_effect_proven": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.config)
    immutable(args.output, encoded(result))
    print(
        {
            "source_rows": len(result["source_matrix"]),
            "plans": len(result["plan_parent_cost_matrix"]),
            "all_checks_passed": True,
            "new_CA_checks": 0,
        }
    )


if __name__ == "__main__":
    main()
