"""Whole-domain aggregate guarantees for a roof-only fire after protective ground clearing."""

import argparse
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from audit_heritage_thermal_optimum import UNIT, material_rows, prove_cover_impossible
from check_heritage_compartment_first_escape import pair_conditional_doses
from check_heritage_registered_roof_compartment import area_outward
from optimize_heritage_protection_and_loss import solve_remaining
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import unambiguous_roof
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def require(valid, message):
    if not valid:
        raise ValueError(message)


def core_cover(layout, inv, transport, D, required_ground):
    parent = layout.physical_request
    rows, local, contacts = material_rows(parent, layout, transport, inv)
    require(
        all(set(g) <= D for g in contacts if isinstance(g[0], str)),
        "fixed D lacks core contact protection",
    )
    forced_ground = required_ground | {g[0] for g in contacts if isinstance(g[0], int)}
    all_ground = forced_ground | {g[0] for g in local if isinstance(g[0], int)}
    mask = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    require(all(mask[n] for n in all_ground), "a required whole ground action is illegal")

    def label(g):
        return tuple(sorted(f"G{v}" if isinstance(v, int) else v for v in g))

    pool = {label(g) for g in local}
    forced = D | {f"G{n}" for n in forced_ground}
    costs = defaultdict(float)
    for g, shape in zip(layout.owners, layout.geometry, strict=True):
        if g:
            costs[g] += shape.area
    for n in all_ground:
        costs[(f"G{n}",)] = parent.grid.cell_area_m2
    table = [{label(g): dose for g, dose in row.items()} for row in rows]
    selected, detail = solve_remaining(table, pool, forced, set(inv.by_id) - D, costs, 0.99)
    G = {n for n in all_ground if f"G{n}" in selected}
    require(
        selected == D | {f"G{n}" for n in G} and forced_ground <= G,
        "fixed roofs/unconditional cuts changed",
    )
    mandatory_core = len(forced_ground - required_ground)
    options = sorted(all_ground - forced_ground)
    k = len(G - forced_ground)

    def down(value):
        return math.floor(max(0.0, value / 0.99 - 1e-10) * UNIT)

    weights = np.array(
        [[down(row.get((n,), 0.0)) for n in options] for row in rows], dtype=np.int64
    )
    fixed = np.array(
        [
            sum(down(d) for g, d in row.items() if isinstance(g[0], str) and not set(g) <= D)
            for row in rows
        ],
        dtype=np.int64,
    )
    counters = Counter()
    proven = prove_cover_impossible(
        weights, weights.sum(axis=1) + fixed - UNIT, k - 1, counters, time.monotonic() + 90
    )
    return (
        G,
        detail,
        {
            "core_contact_additional_whole_cells": mandatory_core,
            "optimal_optional_core_whole_cells": k,
            "integer_preceding_ground_tier_infeasible": bool(proven),
            "integer_proof_counts": dict(counters),
        },
    )


def bounds(layout, inv, R, retained, D, threshold, max_nodes):
    shapes = np.array(layout.geometry, dtype=object)[retained]
    tree = shapely.STRtree(shapes)
    a, b = tree.query(shapes, predicate="dwithin", distance=R + 1e-8)
    take = a < b
    labels = components(
        np.column_stack([a[take], b[take]]).astype(np.int32), np.ones(len(retained), bool)
    )
    active = layout.node_inputs.burnable_mask.ravel()
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    represented = {
        name for i, group in enumerate(layout.owners) if active[i] and area[i] > 0 for name in group
    }
    unknown = set(inv.by_id) - represented
    ordinary = {x.building_id for x in inv.buildings if x.source_label == "building"} - {
        x.building_id for x in inv.targets
    }
    group_parts = defaultdict(list)
    group_owners = defaultdict(set)
    owner_groups = defaultdict(set)
    for i, label in zip(retained, labels, strict=True):
        ids = layout.owners[i]
        group_parts[label].append(float(area[i]))
        group_owners[label].update(ids)
        for owner in ids:
            owner_groups[owner].add(label)
    physical_retained = set(owner_groups) & ordinary
    sources = physical_retained - D
    require(sources == ordinary - D - unknown, "unmapped ordinary source not accounted for")
    qualified = set()
    for owner in ordinary:
        try:
            roof = unambiguous_roof(inv, owner)
        except ValueError:
            continue
        if roof.area >= 25:
            qualified.add(owner)
    source_pool = sources & qualified
    require(
        source_pool and not ((qualified - D) & unknown),
        "eligible retained source absent from physical inventory",
    )
    totals = {g: math.fsum(values) for g, values in group_parts.items()}
    results = {}
    for groups in set(tuple(sorted(owner_groups[i])) for i in sources):
        footprint_area = area_outward(
            math.fsum(totals[g] for g in groups),
            sum(len(group_parts[g]) for g in groups),
            max_nodes,
        )
        count = len(unknown.union(*(group_owners[g] for g in groups)))
        results[groups] = footprint_area, count

    def aggregate(pool):
        v = [results[tuple(sorted(owner_groups[i]))] for i in pool]
        ok = sum(a <= threshold[0] and n <= threshold[1] for a, n in v)
        return {
            "roofs": len(v),
            "meet_both_loss_caps": ok,
            "fraction_meeting_both_caps": ok / len(v),
            "worst_area_upper_m2": max(a for a, n in v),
            "worst_original_owner_upper": max(n for a, n in v),
        }

    return {
        "all_ordinary_inventory": len(ordinary),
        "physically_surviving_ordinary_owner_count": len(physical_retained),
        "physically_surviving_fraction": len(physical_retained) / len(ordinary),
        "eligible_original_geometry_25m2_sources": len(qualified),
        "retained_eligible_fraction": len(source_pool) / len(qualified),
        "unrepresented_original_owner_count_included_in_upper": len(unknown),
        "possible_roof_components": len(totals),
        "two_caps_area_m2_and_owner_count": threshold,
        "all_represented_retained_ordinary_source_labels": aggregate(sources),
        "retained_25m2_eligible_ordinary_sources": aggregate(source_pool),
    }


def calculate(village):
    docs = ROOT / "docs/results"
    thermal_path = docs / f"heritage-thermal-clearance-{village}-20261111.json"
    thermal = read_json(thermal_path)
    base_path = docs / f"heritage-core-separation-{village}-20261111.json"
    baseline = read_json(base_path)
    independent = docs / f"heritage-thermal-clearance-{village}-independent-20261111.json"
    cert = read_json(independent)
    require(
        cert["source_certificate_file_sha256"] == file_sha256(thermal_path)
        and cert["receipt_sha256"]
        == content_hash({k: v for k, v in cert.items() if k != "receipt_sha256"}),
        "thermal baseline differs",
    )
    coarse_path = docs / f"heritage-compartment-loss-envelopes-{village}-v2-20261111.json"
    coarse = read_json(coarse_path)
    require(
        coarse["receipt_sha256"]
        == content_hash({k: v for k, v in coarse.items() if k != "receipt_sha256"}),
        "coarse baseline differs",
    )
    native = read_json(Path(baseline["native_reference_run"]) / "experiment.json")
    cfg = native["config"]
    bundle, transport, inv = load_study(Path(cfg["study_path"]))
    parent = bundle.request
    require(
        request_fingerprint(parent) == baseline["native_case_sha256"]
        and inv.sha256 == baseline["inventory_sha256"]
        and parent.firebrands is None
        and transport.wind_speed_m_s == 0,
        "unsupported or changed model",
    )
    L = resolve_roof_layout(
        parent, inv, max_nodes=cfg["max_nodes"], core_overlap_policy=cfg["core_overlap_policy"]
    )
    D = set(thermal["roof_actions"])
    protected = {t.building_id for t in inv.targets}
    require(set(inv.candidate_ids) >= D and not D & protected, "ineligible roof actions")
    active = L.node_inputs.burnable_mask.ravel()
    polys = np.asarray(L.geometry, dtype=object)
    retained = np.array(
        [
            i
            for i, g in enumerate(L.owners)
            if active[i] and g and not set(g) <= D and not protected.intersection(g)
        ],
        dtype=int,
    )
    ground = np.array([i for i, g in enumerate(L.owners) if active[i] and not g], dtype=int)
    require(len(set(L.cell_id[ground])) == len(ground), "nonroof parent split")
    fractions, contacts, n = pair_conditional_doses(
        L, polys, retained, ground, [tuple(retained)], transport
    )
    high = {i for i, v in fractions.items() if v >= 1 - 1e-10}
    required = {int(L.cell_id[i]) for i in high | contacts}
    G, core, proof = core_cover(L, inv, transport, D, required)
    max_ground = max((v for i, v in fractions.items() if L.cell_id[i] not in G), default=0.0)
    require(
        max_ground < 1 - 1e-10 and all(L.cell_id[i] in G for i in contacts),
        "ground-to-roof separating condition failed",
    )
    removed = shapely.union_all([b.geometry for b in inv.buildings if b.building_id in D])
    survived = shapely.union_all([b.geometry for b in inv.buildings if b.building_id not in D])
    area = removed.difference(survived).area
    require(
        abs(area + len(G) * parent.grid.cell_area_m2 - core["C_m2"]) < 1e-5,
        "cost failed independent original-owner area audit",
    )
    ref = next(x for x in coarse["plans"] if x["plan"] == "thermal-plus-building-ground-separation")
    limits = (420.73, 4) if village == "batuan" else (316.13, 3)
    return {
        "schema_version": "universal-roof-to-ground-single-roof-fire-clearance-1",
        "village": village,
        "physics_case_sha256": baseline["native_case_sha256"],
        "thermal_audit_sha256": file_sha256(independent),
        "coarse_baseline_file_sha256": file_sha256(coarse_path),
        "method_sha256": file_sha256(__file__),
        "action_sha256": content_hash({"D": sorted(D), "G": sorted(G)}),
        "source_orders_read_or_used_for_design": False,
        "new_CA_checks": 0,
        "C_m2": core["C_m2"],
        "original_owner_AND_roof_m2": area,
        "roof_actions": len(D),
        "eligible_whole_ground_actions": len(G),
        "ground_construction_m2": len(G) * parent.grid.cell_area_m2,
        "old_thermal_ground_cells_recovered": len(set(thermal["ground_parent_actions"]) - G),
        "geometric_fixed_roof_ground_separation_C_m2": ref["whole_action_area_m2"],
        "C_difference_from_geometric_m2": core["C_m2"] - ref["whole_action_area_m2"],
        "mandatory_ground_from_contact_or_dose": len(required),
        "contact_ground_count": len(contacts),
        "dose_ground_count": len(high),
        "all_retained_noncore_roof_parent_emitters": len(retained),
        "all_burnable_ground_receivers": len(ground),
        "independently_recomputed_receivers_with_nonzero_dose": n,
        "max_retained_ground_energy_fraction": max_ground,
        "max_core_energy_fraction": core["max_core_absorbed_energy_fraction"],
        "fixed_roofs_minimum_core_addon_proof": proof,
        "aggregate_losses": bounds(
            L, inv, transport.exposure_radius_m, retained, D, limits, cfg["max_nodes"]
        ),
        "scope": (
            "Single exterior ordinary roof direct initialization, zero wind, no brands or outside "
            "feedback. Every retained ground receptor has no contact and subthreshold all-roof "
            "energy before the first ground ignition. Separate core dose and contact inequalities "
            "exclude ignition through a core. N includes all original coowners on reachable roof "
            "components and all unrepresented owners. A includes outward refinement allowance. "
            "Fractions count roofs with deterministic sufficient bounds."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village", choices=["batuan", "pingyan"])
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    q = calculate(a.village)
    q["receipt_sha256"] = content_hash(q)
    immutable(a.output, encoded(q))
    print(
        a.village,
        q["C_m2"],
        q["aggregate_losses"]["retained_25m2_eligible_ordinary_sources"],
        flush=True,
    )
