"""Independent parent heat-budget verifier and discrete cost lower bound."""

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_core_separation import require
from scipy.optimize import linprog

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

UNIT = 10**8
COEFFICIENT_ROUND_DOWN = 1e-10
CONTACT_TOL = 1e-8


def verify_seal(path):
    data = read_json(path)
    seal = data.pop("certificate_sha256")
    require(content_hash(data) == seal, "invalid candidate or parent seal")
    data["certificate_sha256"] = seal
    return data


def material_rows(parent, layout, transport, inventory):
    """Full parent scan per receiver with rectangular rejection and scalar terms."""
    g = np.asarray(layout.geometry, dtype=object)
    bounds = shapely.bounds(g)
    protected = {x.building_id for x in inventory.targets}
    core = np.array([bool(protected.intersection(ids)) for ids in layout.owners])
    exterior = layout.node_inputs.burnable_mask.ravel() & ~core
    p = layout.node_inputs.parameters
    f = {
        k: getattr(p, k).ravel()
        for k in (
            "pyrolysable_dry_fuel_mass_kg",
            "available_energy_J",
            "heat_of_combustion_J_kg_dry",
            "radiative_fraction",
            "receiver_area_m2",
            "covered_area_m2",
            "absorptivity",
            "effective_heated_dry_mass_kg",
            "specific_heat_J_kg_K",
            "ignition_temperature_K",
            "source_height_m",
            "receiver_height_m",
        )
    }
    z = (
        np.zeros(len(g))
        if parent.ground_elevation_m is None
        else parent.ground_elevation_m.ravel()[layout.cell_id]
    )
    source_joules = (
        np.maximum(
            f["pyrolysable_dry_fuel_mass_kg"] * f["heat_of_combustion_J_kg_dry"],
            f["available_energy_J"],
        )
        * f["radiative_fraction"]
    )
    require(transport.wind_speed_m_s == 0 and parent.firebrands is None, "incompatible transport")
    records = []
    all_groups, contact = set(), set()
    radius = transport.exposure_radius_m
    for j in np.flatnonzero(core):
        minx, miny, maxx, maxy = bounds[j]
        r = radius + CONTACT_TOL
        local = np.flatnonzero(
            exterior
            & (bounds[:, 0] <= maxx + r)
            & (bounds[:, 2] >= minx - r)
            & (bounds[:, 1] <= maxy + r)
            & (bounds[:, 3] >= miny - r)
        )
        capacity_joule = (
            f["effective_heated_dry_mass_kg"][j]
            * f["specific_heat_J_kg_K"][j]
            * (f["ignition_temperature_K"][j] - parent.scenario.ambient_temperature_K)
        )
        require(capacity_joule > 0, "invalid dry thermal mass")
        rows = defaultdict(list)
        for i in local:
            polygon_gap = g[i].distance(g[j])
            if polygon_gap > radius + CONTACT_TOL:
                continue
            group = tuple(layout.owners[i]) or (int(layout.cell_id[i]),)
            all_groups.add(group)
            if polygon_gap <= CONTACT_TOL:
                contact.add(group)
            d = polygon_gap if layout.owners[i] else shapely.Point(layout.xy_m[i]).distance(g[j])
            if d > radius + CONTACT_TOL:
                continue
            h = (z[i] + f["source_height_m"][i] / 2) - (z[j] + f["receiver_height_m"][j])
            length = math.hypot(d, h)
            kernel = math.exp(-transport.atmospheric_extinction_m_inv * length) / (
                4 * math.pi * (d * d + h * h + transport.near_field_regularization_m**2)
            )
            # J per whole target, divided by J needed for that target. A child inherits this ratio.
            dose = (
                source_joules[i]
                * kernel
                * f["receiver_area_m2"][j]
                * f["absorptivity"][j]
                / capacity_joule
            )
            rows[group].append(dose)
        records.append({k: math.fsum(v) for k, v in rows.items()})
    return records, all_groups, contact


def integer_ray_proof(weights, coefficients, need, k):
    """Integer bound for any k choices, for any nonnegative integer row multiplier."""
    if not weights.max(initial=0) > 0:
        return False
    lam = np.rint(weights / weights.max() * 10**6).astype(np.int64)
    chosen = np.flatnonzero(lam)
    if not len(chosen):
        return False
    scores = lam[chosen].astype(object) @ coefficients[chosen].astype(object)
    target = sum(int(lam[i]) * int(need[i]) for i in chosen)
    return sum(sorted(scores, reverse=True)[:k]) < target


def prove_cover_impossible(coeff, need, budget, counters, deadline):
    """Exhaustive branching with exact integer leaves; LP only suggests dual rays or branches."""
    if time.monotonic() > deadline:
        raise TimeoutError("audit branch time limit exceeded")
    counters["branch_nodes"] += 1
    if budget < 0:
        counters["negative_budget"] += 1
        return True
    keep = need > 0
    if not keep.any():
        return False
    w, b = coeff[keep], need[keep]
    relevant = np.any(w > 0, axis=0)
    w = w[:, relevant]
    n = w.shape[1]
    k = min(budget, n)
    # Strongest single row bound with k highest contributions for that row.
    if k == 0 or np.any(np.sort(w, axis=1)[:, -k:].sum(axis=1) < b):
        counters["integer_single_row_exclusions"] += 1
        return True
    if k == n:
        return False
    # Add slack t (max normalized coverage deficit), solve always-feasible LP.
    # A positive dual vector may then prove k choices insufficient using integer arithmetic.
    wt = w.astype(float) / UNIT
    bt = b.astype(float) / UNIT
    m = len(b)
    M = np.vstack((np.column_stack((-wt, -np.ones(m))), np.r_[np.ones(n), 0]))
    opt = linprog(
        np.r_[np.zeros(n), 1],
        A_ub=M,
        b_ub=np.r_[-bt, k],
        bounds=[(0, 1)] * n + [(0, None)],
        method="highs",
    )
    if opt.success:
        if integer_ray_proof(np.maximum(0, -opt.ineqlin.marginals[:m]), w, b, k):
            counters["integer_weighted_exclusions"] += 1
            return True
        x = opt.x[:n]
        # An all-integer feasible vector demonstrates that this relaxed bound cannot exclude.
        if (
            np.max(np.abs(x - np.rint(x))) < 1e-8
            and np.all(w @ np.rint(x).astype(np.int64) >= b)
            and np.rint(x).sum() <= k
        ):
            return False
        # Choose an ambiguous influential variable and verify both possibilities.
        fraction = np.minimum(x, 1 - x)
        j = int(np.argmax(fraction)) if fraction.max() > 1e-8 else int(np.argmax(w.sum(axis=0)))
    else:
        j = int(np.argmax(w.sum(axis=0)))
    remain = np.delete(w, j, axis=1)
    return prove_cover_impossible(
        remain, b - w[:, j], k - 1, counters, deadline
    ) and prove_cover_impossible(remain, b, k, counters, deadline)


def verify(path, geo, seconds):
    result = verify_seal(path)
    parent_cert = verify_seal(geo)
    require(
        result["geometric_certificate_sha256"] == parent_cert["certificate_sha256"],
        "energy certificate refers to different parent",
    )
    native = Path(parent_cert["native_reference_run"])
    ex = read_json(native / "experiment.json")
    conf = ex["config"]
    root = Path(__file__).resolve().parents[1]
    require(
        file_sha256(root / "scripts/optimize_heritage_thermal_clearance.py")
        == result["source_sha256"],
        "registered method changed",
    )
    for code, sha in ex["source_sha256"].items():
        if code.startswith("src/"):
            require(
                file_sha256(root / code) == file_sha256(native / "source-snapshot" / code) == sha,
                "physical snapshot mismatch",
            )
    bundle, t, inv = load_study(Path(conf["study_path"]))
    phys = bundle.request
    require(
        request_fingerprint(phys)
        == ex["problem"]["case_sha256"]
        == parent_cert["native_case_sha256"]
        and inv.sha256 == ex["problem"]["inventory_sha256"] == parent_cert["inventory_sha256"]
        and asdict(t) == ex["problem"]["transport_parameters"],
        "input mismatch",
    )
    L = resolve_roof_layout(
        phys, inv, max_nodes=conf["max_nodes"], core_overlap_policy=conf["core_overlap_policy"]
    )
    rows, local, contacts = material_rows(phys, L, t, inv)
    candidate_roofs = sorted(
        {name for group in local if isinstance(group[0], str) for name in group}
    )
    grass = sorted(group[0] for group in local if isinstance(group[0], int))
    require(
        set(candidate_roofs) == set(parent_cert["roof_actions"])
        and set(grass) == set(parent_cert["ground_parent_actions"]),
        "complete local influence catalogue differs",
    )
    chosen = set(result["roof_actions"])
    ground = set(result["ground_parent_actions"])
    force_roof = {name for group in contacts if isinstance(group[0], str) for name in group}
    force_grass = {group[0] for group in contacts if isinstance(group[0], int)}
    require(
        force_roof <= chosen <= set(candidate_roofs) and force_grass <= ground <= set(grass),
        "uncovered contact or nonlocal action",
    )
    chosen_all = chosen | ground
    ratio = np.array(
        [math.fsum(v for g, v in row.items() if not set(g) <= chosen_all) for row in rows]
    )
    capacity_fraction = 1 - result["dry_energy_capacity_reserved_fraction"]
    require(
        max(ratio) <= capacity_fraction
        and abs(max(ratio) - result["worst_aggregate_fraction_of_dry_ignition_energy"]) < 1e-11,
        "independent per-receptor check failed",
    )
    raw_removed = shapely.union_all([b.geometry for b in inv.buildings if b.building_id in chosen])
    raw_retained = shapely.union_all(
        [b.geometry for b in inv.buildings if b.building_id not in chosen]
    )
    ground_cost = phys.grid.cell_area_m2
    rawcost = raw_removed.difference(raw_retained).area + ground_cost * len(ground)
    require(
        abs(rawcost - result["construction_area_m2"]) <= 1e-6,
        "original polygon owner-AND cost differs",
    )
    roofgroups = defaultdict(list)
    for geom, owners in zip(L.geometry, L.owners, strict=True):
        if owners and set(owners) <= set(candidate_roofs):
            roofgroups[tuple(owners)].append(geom.area)
    costgroup = {g: math.fsum(v) for g, v in roofgroups.items()}
    C = math.fsum(v for g, v in costgroup.items() if set(g) <= chosen) + len(ground) * ground_cost
    require(abs(C - result["construction_area_m2"]) < 1e-6, "group cost mismatch")
    # Relax the audited binary64 coefficients by rounding every nonnegative group down.
    all_g = sorted(local, key=lambda g: (isinstance(g[0], int), g))
    vals = np.array([[row.get(g, 0.0) / capacity_fraction for g in all_g] for row in rows])
    w = np.floor(np.maximum(vals - COEFFICIENT_ROUND_DOWN, 0) * UNIT).astype(np.int64)
    ground_ix = [
        j for j, g in enumerate(all_g) if isinstance(g[0], int) and g[0] not in force_grass
    ]
    grass_w = w[:, ground_ix]
    deadline = time.monotonic() + seconds
    counters = Counter()
    unresolved = 0
    previous = -math.inf
    near_equal = 0
    for mask in range(1 << len(candidate_roofs)):
        select = {a for i, a in enumerate(candidate_roofs) if mask & (1 << i)}
        # A tiny geometric rounding tolerance applies only to equal roof cost tiers.
        rc = math.fsum(v for g, v in costgroup.items() if set(g) <= select)
        kb = min(len(grass), math.floor((C - rc) / ground_cost))
        if kb >= 0 and abs(rc + kb * ground_cost - C) < 1e-7:
            if rc + kb * ground_cost != C:
                near_equal += 1
            kb -= 1
        if kb < 0:
            continue
        previous = max(previous, rc + kb * ground_cost)
        if not force_roof <= select:
            counters["roof_subsets_excluded_by_native_contact"] += 1
            continue
        counters["legal_cheaper_roof_subsets"] += 1
        exterior_roof = [
            j for j, g in enumerate(all_g) if isinstance(g[0], str) and not set(g) <= select
        ]
        need = grass_w.sum(axis=1) + w[:, exterior_roof].sum(axis=1) - UNIT
        if not prove_cover_impossible(grass_w, need, kb - len(force_grass), counters, deadline):
            unresolved += 1
    return {
        "village": result["village"],
        "source_certificate_file_sha256": file_sha256(path),
        "C_m2": C,
        "raw_owner_union_cost_m2": rawcost,
        "ground_count": len(ground),
        "roof_action_count": len(chosen),
        "protected_parent_receptors_recomputed": len(rows),
        "all_receptors_below_99_percent_dry_capacity": True,
        "greatest_recomputed_dose_fraction": float(ratio.max()),
        "native_contact_left_unblocked": 0,
        "total_local_roof_subsets": 1 << len(candidate_roofs),
        "all_local_ground_actions": len(grass),
        "lower_discrete_cost_tier_m2": previous,
        "cost_gap_to_next_lower_tier_m2": C - previous,
        "unresolved_cheaper_roof_subsets": unresolved,
        "near_equal_float_cost_tiers": near_equal,
        "cheaper_discrete_solutions_excluded": unresolved == 0 and near_equal == 0,
        "certificate_counts": dict(counters),
        "native_CA_runs": 0,
        "dose_bound_arithmetic": (
            "Per-group ratios computed independently in binary64, down-adjusted by 1e-10 "
            "and floored in 1e-8 units. Feasibility cuts are integer for that relaxed "
            "table. No rational survey polygon claim."
        ),
        "objective_scope": (
            "Sufficient cumulative-energy inequalities only, under physical owner AND and "
            "whole grass cells."
        ),
    }


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument("--certificate", type=Path, required=True)
    arg.add_argument("--geometric-parent", type=Path, required=True)
    arg.add_argument("--output", type=Path, required=True)
    arg.add_argument("--seconds", type=float, default=300)
    args = arg.parse_args()
    result = verify(args.certificate, args.geometric_parent, args.seconds)
    result["schema_version"] = "independent-thermal-dose-and-discrete-cost-verification-1"
    result["audit_source_sha256"] = file_sha256(__file__)
    result["receipt_sha256"] = content_hash(result)
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
