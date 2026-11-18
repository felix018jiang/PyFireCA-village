"""Tighten the universal roof-to-ground bound by the original owner of one arbitrary source."""

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from audit_heritage_universal_roof_ground_clearance import ROOT, bounds, core_cover, require
from check_heritage_compartment_first_escape import pair_conditional_doses
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def reachable_groups(layout, inv, retained, roof_actions, radius):
    shapes = np.asarray(layout.geometry, dtype=object)[retained]
    a, b = shapely.STRtree(shapes).query(shapes, predicate="dwithin", distance=radius + 1e-8)
    use = a < b
    c = components(np.column_stack((a[use], b[use])).astype(np.int32), np.ones(len(shapes), bool))
    ordinary = (
        {obj.building_id for obj in inv.buildings if obj.source_label == "building"}
        - roof_actions
        - {obj.building_id for obj in inv.targets}
    )
    owner_parts, parts = defaultdict(set), defaultdict(list)
    for i, tag in zip(retained, c, strict=True):
        parts[tag].append(int(i))
        for owner in ordinary.intersection(layout.owners[i]):
            owner_parts[owner].add(int(tag))
    groups, multiple = {}, 0
    for components_for_owner in owner_parts.values():
        key = tuple(sorted(components_for_owner))
        multiple += len(key) > 1
        if key not in groups:
            groups[key] = tuple(sorted(n for tag in key for n in parts[tag]))
    require(
        set(n for group in groups.values() for n in group) == set(retained),
        "retained emitter unreachable from any admissible roof label",
    )
    return list(groups.values()), len(owner_parts), multiple


def audit(village):
    docs = ROOT / "docs/results"
    base = docs / f"heritage-universal-roof-to-ground-clearance-{village}-20261111.json"
    old = read_json(base)
    require(
        old["receipt_sha256"]
        == content_hash({k: v for k, v in old.items() if k != "receipt_sha256"})
        and old["method_sha256"]
        == file_sha256(
            Path(__file__).with_name("audit_heritage_universal_roof_ground_clearance.py")
        ),
        "original all-roof proof changed",
    )
    sep = read_json(docs / f"heritage-core-separation-{village}-20261111.json")
    reference = read_json(Path(sep["native_reference_run"]) / "experiment.json")
    c = reference["config"]
    t = read_json(docs / f"heritage-thermal-clearance-{village}-20261111.json")
    D = set(t["roof_actions"])
    b, tr, inv = load_study(Path(c["study_path"]))
    p = b.request
    require(
        request_fingerprint(p) == old["physics_case_sha256"]
        and inv.sha256 == sep["inventory_sha256"]
        and p.firebrands is None
        and tr.wind_speed_m_s == 0,
        "physical inventory or transfer changed",
    )
    L = resolve_roof_layout(
        p, inv, max_nodes=c["max_nodes"], core_overlap_policy=c["core_overlap_policy"]
    )
    core = {x.building_id for x in inv.targets}
    active = L.node_inputs.burnable_mask.ravel()
    geom = np.asarray(L.geometry, dtype=object)
    retained = np.array(
        [
            i
            for i, g in enumerate(L.owners)
            if active[i] and g and not set(g) <= D and not core.intersection(g)
        ],
        dtype=int,
    )
    ground = np.array([i for i, g in enumerate(L.owners) if active[i] and not g], dtype=int)
    groups, owner_count, multi = reachable_groups(L, inv, retained, D, tr.exposure_radius_m)
    ratios, contacts, n = pair_conditional_doses(L, geom, retained, ground, groups, tr)
    forced = {
        int(L.cell_id[i])
        for i in contacts | {i for i, value in ratios.items() if value >= 1 - 1e-10}
    }
    G, result, proof = core_cover(L, inv, tr, D, forced)
    peak = max((v for i, v in ratios.items() if L.cell_id[i] not in G), default=0.0)
    cap = old["aggregate_losses"]["two_caps_area_m2_and_owner_count"]
    losses = bounds(L, inv, tr.exposure_radius_m, retained, D, cap, c["max_nodes"])
    require(
        losses == old["aggregate_losses"]
        and peak < 1 - 1e-10
        and len(contacts) == old["contact_ground_count"]
        and result["C_m2"] <= old["C_m2"] + 1e-6
        and len(forced) <= old["mandatory_ground_from_contact_or_dose"],
        "separation/roof bounds or expected monotonic relation failed",
    )
    C = old["original_owner_AND_roof_m2"] + len(G) * p.grid.cell_area_m2
    require(abs(C - result["C_m2"]) < 1e-5, "whole action cost recomputation failed")
    return {
        "schema_version": "single-ordinary-roof-component-thermal-ground-cut-1",
        "village": village,
        "prior_all_roof_bound_sha256": file_sha256(base),
        "method_sha256": file_sha256(__file__),
        "action_sha256": content_hash({"D": sorted(D), "G": sorted(G)}),
        "source_orders_consumed_for_design": 0,
        "CA_runs": 0,
        "retained_roof_owners_in_component_dose": owner_count,
        "multiple_component_owners_counted_across_all_pieces": multi,
        "distinct_possible_emitting_component_unions": len(groups),
        "source_cohort_ground_receiver_doses_independently_recomputed": n,
        "C_m2": result["C_m2"],
        "all_roof_simultaneous_C_m2": old["C_m2"],
        "saved_vs_all_roof_simultaneous_m2": old["C_m2"] - result["C_m2"],
        "saved_vs_geometric_m2": old["geometric_fixed_roof_ground_separation_C_m2"]
        - result["C_m2"],
        "ground_cells": len(G),
        "ground_cost_m2": len(G) * p.grid.cell_area_m2,
        "mandatory_ground_from_contact_or_any_component_dose": len(forced),
        "core_protection_additional_ground_proof": proof,
        "max_ground_component_absorbed_energy_fraction": peak,
        "max_core_absorbed_energy_fraction": result["max_core_absorbed_energy_fraction"],
        "unchanged_roof_only_loss_envelopes": losses,
        "claim_scope": (
            "A single direct ignition on any retained ordinary roof. Before any ground ignition, "
            "with heritage cores protected, only roof components touching that ordinary label can "
            "burn. All components of the same owner enter one combined dose. The maximum ranges "
            "over owners. Bounds apply to the whole finite modeled domain."
        ),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("village", choices=["batuan", "pingyan"])
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    q = audit(args.village)
    q["receipt_sha256"] = content_hash(q)
    immutable(args.output, encoded(q))
    print(q["village"], q["C_m2"], q["saved_vs_all_roof_simultaneous_m2"])
