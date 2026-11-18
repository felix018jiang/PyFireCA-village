"""Choose whole protective clearances with conservative cumulative-energy constraints."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csc_matrix

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def optimize_groups(group_costs, energy, contact_groups, capacity, time_limit):
    """Minimize physical C. A shared patch disappears only if every owner is removed."""
    groups = list(group_costs)
    actions = sorted({action for group in groups for action in group})
    index = {key: i for i, key in enumerate(actions)}
    variables = []
    count = len(actions)
    for group in groups:
        if len(group) == 1:
            variables.append(index[group[0]])
        else:
            variables.append(count)
            count += 1
    objective = np.zeros(count)
    lb = np.zeros(count)
    rows, upper = [], []
    for group, y in zip(groups, variables, strict=True):
        objective[y] = group_costs[group]
        if group in contact_groups:
            lb[y] = 1
        if len(group) > 1:
            for name in group:
                row = np.zeros(count)
                row[y], row[index[name]] = 1, -1
                rows.append(row)
                upper.append(0)
            row = np.zeros(count)
            row[[index[name] for name in group]] = 1
            row[y] = -1
            rows.append(row)
            upper.append(len(group) - 1)
    # Normalize by a positive energy capacity to avoid badly scaled constraints.
    normalized = energy / capacity[:, None]
    for contributions in normalized:
        row = np.zeros(count)
        row[variables] = -contributions
        rows.append(row)
        upper.append(1 - contributions.sum())
    answer = milp(
        objective,
        integrality=np.ones(count),
        bounds=Bounds(lb, np.ones(count)),
        constraints=LinearConstraint(csc_matrix(rows), -np.inf, upper),
        options={"time_limit": time_limit, "mip_rel_gap": 0},
    )
    if answer.x is None:
        raise ValueError("no feasible protective incumbent: " + answer.message)
    selected = {name for name in actions if answer.x[index[name]] > 0.5}
    cleared = np.array([set(group) <= selected for group in groups])
    if not all(set(g) <= selected for g in contact_groups):
        raise ValueError("incumbent retains an external contact group")
    load = energy[:, ~cleared].sum(axis=1) / capacity
    if load.max() > 1 + 1e-9:
        raise ValueError("independent whole-action evaluation rejects the incumbent")
    cost = sum(group_costs[group] for group, flag in zip(groups, cleared, strict=True) if flag)
    if abs(answer.fun - cost) > 1e-5:
        raise ValueError("MIP objective and realized construction area disagree")
    return selected, {
        "construction_area_m2": float(cost),
        "dual_area_bound_m2": float(answer.mip_dual_bound),
        "realized_max_fraction_of_reserved_energy_capacity": float(load.max()),
        "solver_status": int(answer.status),
        "solver_message": str(answer.message),
        "mip_gap": float(answer.mip_gap),
        "binary_variables": count,
    }


def calculate(layout, inventory, transport, separation, *, time_limit=120.0):
    parent = layout.physical_request
    if parent.firebrands is not None or transport.wind_speed_m_s != 0:
        raise ValueError("this implementation requires the frozen zero-wind, no-spotting model")
    if transport.near_field_regularization_m <= 0 or transport.atmospheric_extinction_m_inv < 0:
        raise ValueError("positive regularization and a nonnegative attenuation are required")
    radius = transport.exposure_radius_m
    roofs, cells = set(separation["roof_actions"]), set(separation["ground_parent_actions"])
    protected = {target.building_id for target in inventory.targets}
    core = np.array([bool(protected.intersection(ids)) for ids in layout.owners])
    geom = np.asarray(layout.geometry, dtype=object)
    inputs, physics = layout.node_inputs, layout.node_inputs.parameters
    external = np.flatnonzero(inputs.burnable_mask.ravel() & ~core)
    physical_gap = shapely.distance(geom[external], shapely.union_all(geom[core]))
    # Use the same closed support as the enclosing geometric certificate.
    external = external[physical_gap <= radius]
    groups = defaultdict(float)
    for ids, shape in zip(layout.owners, geom, strict=True):
        if ids and set(ids) <= roofs:
            groups[tuple(sorted(ids))] += shape.area
    for cell in sorted(cells):
        groups[(f"G{cell}",)] = parent.grid.cell_area_m2
    group_list = list(groups)
    index = {g: i for i, g in enumerate(group_list)}
    source_groups = []
    radiative_geometry = []
    for node in external:
        ids = layout.owners[node]
        g = tuple(sorted(ids)) if ids else (f"G{int(layout.cell_id[node])}",)
        if g not in index:
            raise ValueError("the outer geometric certificate omitted an eligible influence group")
        source_groups.append(index[g])
        # A nonroof parent is never spatially refined and radiates from its centre.
        radiative_geometry.append(geom[node] if ids else shapely.Point(layout.xy_m[node]))
    core_nodes = np.flatnonzero(core)
    energy = np.zeros((len(core_nodes), len(groups)))
    capacities = np.zeros(len(core_nodes))
    contact_groups = set()
    rad = np.array(radiative_geometry, dtype=object)
    if np.any(physics.available_energy_J.ravel()[external] <= 0):
        raise ValueError("unexpected zero external fuel energy in the burnable domain")
    z = np.zeros(len(layout.owners))
    if parent.ground_elevation_m is not None:
        z = parent.ground_elevation_m.ravel()[layout.cell_id]
    source_height = z[external] + 0.5 * physics.source_height_m.ravel()[external]
    fuel_energy = np.maximum(
        physics.available_energy_J.ravel()[external],
        physics.pyrolysable_dry_fuel_mass_kg.ravel()[external]
        * physics.heat_of_combustion_J_kg_dry.ravel()[external],
    )
    per_source = fuel_energy * physics.radiative_fraction.ravel()[external]
    ambient = parent.scenario.ambient_temperature_K
    for row, node in enumerate(core_nodes):
        polygon = geom[node]
        close = shapely.distance(geom[external], polygon) <= 1e-8
        contact_groups.update(
            group_list[col] for col, f in zip(source_groups, close, strict=True) if f
        )
        d = shapely.distance(rad, polygon)
        dz = source_height - (z[node] + physics.receiver_height_m.ravel()[node])
        square = d * d + dz * dz
        radius_guard = d <= radius + 1e-8
        kernel = np.where(
            radius_guard,
            np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(square))
            / (4 * np.pi * (square + transport.near_field_regularization_m**2)),
            0.0,
        )
        receptor = physics.receiver_area_m2.ravel()[node]
        per_m2 = physics.covered_area_m2.ravel()[node]
        absorbed = per_source * kernel * receptor / per_m2 * physics.absorptivity.ravel()[node]
        np.add.at(energy[row], source_groups, absorbed)
        capacities[row] = (
            physics.effective_heated_dry_mass_kg.ravel()[node]
            / per_m2
            * physics.specific_heat_J_kg_K.ravel()[node]
            * (physics.ignition_temperature_K.ravel()[node] - ambient)
        )
    if np.any(capacities <= 0) or np.any(energy < 0):
        raise ValueError("nonnegative source and positive receptor capacity required")
    # Retain a 1% physical heat-capacity margin, independently of the MIP feasibility tolerance.
    selected, summary = optimize_groups(
        groups, energy, contact_groups, 0.99 * capacities, time_limit
    )
    selected_roofs = sorted(selected & roofs)
    selected_cells = sorted(c for c in cells if f"G{c}" in selected)
    area = build_cleared_area_function(layout.owners, shapely.area(geom), area_basis="all_owners")
    roof_area = area(selected_roofs)
    ground_area = len(selected_cells) * parent.grid.cell_area_m2
    if abs(roof_area + ground_area - summary["construction_area_m2"]) > 1e-6:
        raise ValueError("actual cleared area differs from the group-cost computation")
    return {
        **summary,
        "roof_actions": selected_roofs,
        "ground_parent_actions": selected_cells,
        "roof_union_m2": roof_area,
        "ground_footprint_m2": ground_area,
        "dry_energy_capacity_reserved_fraction": 0.01,
        "worst_aggregate_fraction_of_dry_ignition_energy": summary[
            "realized_max_fraction_of_reserved_energy_capacity"
        ]
        * 0.99,
        "certificate_scope": (
            "finite, fuel-conserving physical model; exterior-only direct ignitions; "
            "no contact heat from exterior; all external fuel may burn; "
            "energy needed to raise dry protected mass exceeds total possible absorbed radiation"
        ),
        "mip_optimality_scope": "floating-point HiGHS bound for the sufficient energy inequalities",
        "rounded_solver_arithmetic_interval_certificate": False,
        "whole_village_burned_area_m2": None,
        "whole_village_burned_count": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--separation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sep = read_json(args.separation)
    if sep["certificate_sha256"] != content_hash(
        {k: v for k, v in sep.items() if k != "certificate_sha256"}
    ):
        raise ValueError("geometric source certificate changed")
    experiment = read_json(Path(sep["native_reference_run"]) / "experiment.json")
    config = experiment["config"]
    bundle, transport, inventory = load_study(Path(config["study_path"]))
    if (
        request_fingerprint(bundle.request) != sep["native_case_sha256"]
        or inventory.sha256 != sep["inventory_sha256"]
    ):
        raise ValueError("geometry or case differs from the enclosing certificate")
    layout = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config["core_overlap_policy"],
    )
    result = calculate(layout, inventory, transport, sep)
    result.update(
        schema_version="heritage_thermal_energy_clearance_1",
        village=sep["village"],
        geometric_certificate_sha256=sep["certificate_sha256"],
        source_sha256=file_sha256(Path(__file__)),
        native_CA_checks=0,
    )
    result["certificate_sha256"] = content_hash(result)
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
    print(result["village"], result["construction_area_m2"], result["dual_area_bound_m2"])


if __name__ == "__main__":
    main()
