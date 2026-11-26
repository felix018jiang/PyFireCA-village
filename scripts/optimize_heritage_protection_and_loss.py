"""Minimize total physical clearance under both core protection and fire compartment constraints."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_thermal_optimum import material_rows
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import footprint_budget
from optimize_heritage_thermal_clearance import optimize_groups

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def solve_remaining(rows, local_groups, forced, anchors, physical_cost, ceiling):
    """Eliminate fixed actions before optimization, charging all off-site co-owned pieces."""
    optional = {a for group in local_groups for a in group} - forced - anchors
    allowed = optional | forced
    costs = defaultdict(float)
    base_cost = 0.0
    for group, area in physical_cost.items():
        members = set(group)
        if members <= forced:
            base_cost += area
        elif members <= allowed:
            key = tuple(sorted(members - forced))
            costs[key] += area
    positions = {group: i for i, group in enumerate(costs)}
    energy = np.zeros((len(rows), len(costs)))
    fixed_dose = np.zeros(len(rows))
    for i, row in enumerate(rows):
        for group, dose in row.items():
            members = set(group)
            if members <= forced:
                continue
            if not members <= allowed:
                fixed_dose[i] += dose
            else:
                energy[i, positions[tuple(sorted(members - forced))]] += dose
    remaining_capacity = ceiling - fixed_dose
    if np.any(remaining_capacity <= 0):
        raise ValueError("retained roof alone exhausts the receptor capacity")
    if np.all(energy.sum(axis=1) <= remaining_capacity):
        selected, extra = (
            set(),
            {
                "construction_area_m2": 0.0,
                "dual_area_bound_m2": 0.0,
                "solver_status": 0,
                "mip_gap": 0.0,
                "solver_message": "mandatory action set already satisfies the core constraints",
            },
        )
    else:
        selected, extra = optimize_groups(costs, energy, set(), remaining_capacity, 120.0)
    complete = selected | forced
    C = sum(area for g, area in physical_cost.items() if set(g) <= complete)
    if abs(C - base_cost - extra["construction_area_m2"]) > 1e-6 or complete & anchors:
        raise ValueError("post-solve action/cost mismatch")
    realized = max(
        sum(value for group, value in row.items() if not set(group) <= complete) for row in rows
    )
    if realized > ceiling + 1e-10 or realized >= 1:
        raise ValueError("final energy bound admits an unprotected core")
    return complete, {
        "C_m2": C,
        "mandatory_C_m2": base_cost,
        "additional_core_protection_C_m2": C - base_cost,
        "cost_lower_bound_m2": base_cost + extra["dual_area_bound_m2"],
        "max_core_absorbed_energy_fraction": realized,
        "solver_status": extra["solver_status"],
        "solver_message": extra["solver_message"],
        "optional_roof_action_count": sum(x.startswith("B") for x in optional),
        "optional_ground_action_count": sum(x.startswith("G") for x in optional),
    }


def calculate(village, mandatory_path):
    mandatory = read_json(mandatory_path)
    if (
        mandatory["receipt_sha256"]
        != content_hash({k: v for k, v in mandatory.items() if k != "receipt_sha256"})
        or not mandatory["all_original_external_fuel_tested_before_clearance"]
    ):
        raise ValueError("compartment constraints must bind all original external fuel")
    raw = Path(__file__).resolve().parents[1] / "docs/results"
    enclosure_path = raw / f"heritage-first-escape-compartment-{village}-20261111.json"
    enclosure = read_json(enclosure_path)
    if mandatory["village"] != village or enclosure["receipt_sha256"] != content_hash(
        {k: v for k, v in enclosure.items() if k != "receipt_sha256"}
    ):
        raise ValueError("source compartment inputs do not match")
    geo, _, layout, inv, transport, D, G, families, _ = load_bound_inputs(village)
    cells = set(mandatory["first_escape_mandatory_ground_parent_cells"])
    roofs = set(mandatory["first_escape_mandatory_roof_ids"])
    roofs.update(mandatory["source_priority_mandatory_removed_roof_ids"])
    anchors = set(mandatory["must_retain_ordinary_roof_ids"])
    if anchors != {s for _, _, s, _ in families}:
        raise ValueError("source selection changes between geometric and energy programs")
    for _, protocol, source, _ in families:
        priority = protocol["draws"][0]["priority_ids"]
        if not set(priority[: priority.index(source)]) <= roofs:
            raise ValueError("priority-prefix removal is incomplete")
    rows, local, contacts = material_rows(layout.physical_request, layout, transport, inv)

    def label(group):
        return tuple(sorted(f"G{i}" if isinstance(i, int) else i for i in group))

    forced = roofs | {f"G{i}" for i in cells}
    forced.update(a for g in contacts for a in label(g))
    if forced & anchors:
        raise ValueError("source retention conflicts with a forced action")
    all_ground = cells | {g[0] for g in local if isinstance(g[0], int)}
    terrain = layout.physical_request
    eligible = (
        terrain.domain_mask
        & terrain.known_mask
        & terrain.burnable_mask
        & (terrain.building_index < 0)
        & (terrain.parameters.covered_area_m2 > 0)
    ).ravel()
    if not np.all(eligible[list(all_ground)]) or not roofs <= set(inv.candidate_ids):
        raise ValueError("proposed compulsory action is ineligible")
    physics_cost = defaultdict(float)
    for group, shape in zip(layout.owners, layout.geometry, strict=True):
        if group:
            physics_cost[group] += shape.area
    for cell in all_ground:
        physics_cost[(f"G{cell}",)] = terrain.grid.cell_area_m2
    mapped_rows = [{label(g): v for g, v in row.items()} for row in rows]
    local_keys = {label(g) for g in local}
    solutions = []
    geometry = np.asarray(layout.geometry, dtype=object)
    tree = shapely.STRtree(geometry)
    for ceiling in (0.99, 1.0):
        final, report = solve_remaining(
            mapped_rows, local_keys, forced, anchors, physics_cost, ceiling
        )
        finalD = final.intersection(inv.candidate_ids)
        finalG = {i for i in all_ground if f"G{i}" in final}
        if final != finalD | {f"G{i}" for i in finalG}:
            raise ValueError("unrecognized final action")
        for _, prot, s, fp in families:
            (actual,) = realize_sources(prot, inv, demolished_ids=sorted(finalD))
            old, a = footprint_budget(layout, geometry, tree, s, fp, D, G)
            new, b = footprint_budget(layout, geometry, tree, s, fp, finalD, finalG)
            if (
                actual["source_id"] != s
                or actual["footprint"].sha256 != fp.sha256
                or (new != old or not np.array_equal(a, b))
            ):
                raise ValueError("first retained roof or physical footprint budget changed")
        original_shapes = shapely.union_all(
            [b.geometry for b in inv.buildings if b.building_id in finalD]
        )
        surviving = shapely.union_all(
            [b.geometry for b in inv.buildings if b.building_id not in finalD]
        )
        actual_roof = original_shapes.difference(surviving).area
        grass = len(finalG) * terrain.grid.cell_area_m2
        if abs(actual_roof + grass - report["C_m2"]) > 1e-5:
            raise ValueError("raw geometry and computed joint cost disagree")
        solutions.append(
            {
                **report,
                "core_energy_ceiling": ceiling,
                "demolished_ids": sorted(finalD),
                "cleared_physical_cell_ids": sorted(finalG),
                "ground_m2": grass,
                "roof_m2": report["C_m2"] - grass,
            }
        )
    return {
        "village": village,
        "model": (
            "fixed exterior families; external-to-core cumulative energy constraint; "
            "compartment-to-exterior first-ignition constraint"
        ),
        "mandatory_action_file_sha256": file_sha256(mandatory_path),
        "conservative_enclosure_sha256": file_sha256(enclosure_path),
        "fixed_25m2_source_budget_count": len(families),
        "core_protection_scope": "direct ignition on retained exterior fuel; no spotting",
        "registered_family_loss_bounds": geo["registered_source_family_capacities"],
        "worst_registered_A_upper_m2": geo["registered_worst_A_upper_m2"],
        "worst_registered_N_upper": geo["registered_worst_N_upper"],
        "construction_on_fixed_thermal_base_m2": enclosure["construction_area_m2"],
        "optimized_total_construction": solutions,
        "frontier_scope": "fixed anchor roofs, retention priorities and sufficient inequalities",
        "native_CA_counts": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--village", required=True, choices=("batuan", "pingyan"))
    parser.add_argument("--mandatory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    answer = calculate(args.village, args.mandatory)
    answer["method_sha256"] = file_sha256(Path(__file__))
    answer["receipt_sha256"] = content_hash(answer)
    with args.output.open("x") as file:
        json.dump(answer, file, indent=2, allow_nan=False)
        file.write("\n")
