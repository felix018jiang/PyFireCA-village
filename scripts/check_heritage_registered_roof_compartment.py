"""Protective enclosure of frozen ordinary-roof groups above a verified thermal clearance."""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import (
    components,
    potential_edges,
    proven_source_witnesses,
    summarize,
)

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def verify(test, message):
    if not test:
        raise ValueError(message)


def area_outward(area, number, max_nodes):
    # Match the conservation allowance across every included physical parent, then
    # account for positive summation on any mesh within the registered resource cap.
    enlarged = (area * (1 + 1e-9) + number * 1e-6) / (1 - (max_nodes + 5) * 2.0**-53)
    return math.ceil(math.nextafter(enlarged, math.inf) * 100) / 100


def footprint_budget(layout, polygons, tree, source, footprint, roof, grass):
    geo = footprint.geometry
    verify(abs(geo.area - 25.0) < 1e-7, "direct area is not the prespecified 25 m2")
    hits = tree.query(geo, predicate="intersects")
    areas = shapely.area(shapely.intersection(polygons[hits], geo))
    use = areas > 0
    hits, areas = hits[use], areas[use]
    native = np.array([layout.owners[i] == (source,) for i in hits])
    verify(
        math.fsum(areas[~native]) <= 1e-8 and abs(math.fsum(areas[native]) - 25.0) < 1e-7,
        "ambiguous or incomplete actual footprint",
    )
    hits = hits[native]
    areas = areas[native]
    inputs = layout.node_inputs
    verify(
        all(
            getattr(inputs, k).ravel()[hits].all()
            for k in ["domain_mask", "known_mask", "burnable_mask"]
        ),
        "unknown source support",
    )
    verify(
        source not in roof and all(int(layout.cell_id[i]) not in grass for i in hits),
        "anchor cleared",
    )
    fractions = areas / shapely.area(polygons[hits])
    vals = [
        math.fsum(getattr(inputs.parameters, k).ravel()[hits] * fractions)
        for k in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
    ]
    verify(
        abs(vals[0] - 25) < 1e-6 and min(vals) > 0, "physical source energy, area or mass missing"
    )
    return vals, hits


def compute(village):
    root = Path(__file__).resolve().parents[1]
    res = root / "docs/results"
    runroot = root / "runs/research"
    audits = res / f"heritage-thermal-clearance-{village}-independent-20261111.json"
    energy_file = res / f"heritage-thermal-clearance-{village}-20261111.json"
    energy = read_json(energy_file)
    prior = read_json(audits)
    verify(
        prior.pop("receipt_sha256") == content_hash(prior)
        and prior["source_certificate_file_sha256"] == file_sha256(energy_file),
        "thermal base changed",
    )
    D = set(energy["roof_actions"])
    G = set(energy["ground_parent_actions"])
    campaigns = [
        (
            "design",
            runroot
            / f"heritage-thermal-and-geometric-pareto-{village}-20261111"
            / "experiment.json",
        )
    ]
    if village == "batuan":
        campaigns.append(
            (
                "previously_disclosed_confirmation",
                runroot / "heritage-batuan-independent-D9-G0-20261111" / "experiment.json",
            )
        )
    base = read_json(campaigns[0][1])
    conf = base["config"]
    bundle, transport, inv = load_study(Path(conf["study_path"]))
    parent = bundle.request
    verify(
        request_fingerprint(parent) == base["problem"]["case_sha256"]
        and inv.sha256 == base["problem"]["inventory_sha256"]
        and transport.wind_speed_m_s == 0
        and parent.firebrands is None,
        "parent mismatch",
    )
    families = []
    inputs = {}
    newfp = 0
    for kind, path in campaigns:
        exp = read_json(path)
        inputs[kind] = file_sha256(path)
        verify(
            exp["problem"]["case_sha256"] == base["problem"]["case_sha256"]
            and exp["problem"]["inventory_sha256"] == inv.sha256
            and exp["problem"]["core_overlap_policy"] == base["problem"]["core_overlap_policy"],
            "campaign physical inventories differ",
        )
        for code, digest in exp["source_sha256"].items():
            if code.startswith("src/"):
                verify(file_sha256(root / code) == digest, "reader differs from immutable physics")
        for label, fire in exp["problem"]["fires"].items():
            protocol = fire["protocol"]
            (actual,) = realize_sources(protocol, inv, demolished_ids=sorted(D))
            identifier = actual["source_id"]
            fp = actual["footprint"]
            verify(
                identifier == next(i for i in protocol["draws"][0]["priority_ids"] if i not in D),
                "retention choice changed",
            )
            had = identifier in fire["footprints"]
            if had:
                verify(
                    fp.sha256 == content_hash(fire["footprints"][identifier]),
                    "existing footprint changed",
                )
            if kind == "previously_disclosed_confirmation":
                revealed_path = (
                    runroot
                    / "heritage-batuan-thermal-vs-D9-revealed-sources-20261111/experiment.json"
                )
                revealed = read_json(revealed_path)
                inputs["disclosed_common_grid_native"] = file_sha256(revealed_path)
                same = revealed["problem"]["fires"][label]
                verify(
                    same["protocol"]["draws"][0]["priority_ids"]
                    == protocol["draws"][0]["priority_ids"]
                    and fp.sha256 == content_hash(same["footprints"][identifier]),
                    "disclosed joint source differs",
                )
            newfp += not had
            families.append((kind, protocol, identifier, fp))
    anchors = {t[2] for t in families}
    L = resolve_roof_layout(
        parent, inv, max_nodes=conf["max_nodes"], core_overlap_policy=conf["core_overlap_policy"]
    )
    polys = np.asarray(L.geometry, dtype=object)
    tree = shapely.STRtree(polys)
    active = L.node_inputs.burnable_mask.ravel()
    H = {q.building_id for q in inv.targets}
    core = np.array([bool(H.intersection(ids)) for ids in L.owners])
    is_inside = np.array([bool(anchors.intersection(ids)) for ids in L.owners])
    roof = np.array([bool(ids) for ids in L.owners])
    clear0 = np.array([bool(ids) and set(ids) <= D for ids in L.owners]) | np.isin(
        L.cell_id, list(G)
    )
    verify(
        not np.any(is_inside & (clear0 | core)),
        "fixed-anchor group intersects removed or heritage-only parents",
    )
    exterior = np.flatnonzero(active & ~is_inside & ~clear0)
    support = polys[exterior].copy()
    dirt = ~roof[exterior]
    support[dirt] = shapely.points(L.xy_m[exterior[dirt]])
    radius = transport.exposure_radius_m
    verify(radius > math.sqrt(2) * parent.grid.cell_size_m, "contact radius no longer enclosed")
    in_geom = polys[is_inside]
    pairs = shapely.STRtree(support).query(in_geom, predicate="dwithin", distance=radius + 1e-8)
    near = exterior[np.unique(pairs[1])]
    linked_core = int(np.count_nonzero(core[near]))
    targets = near[~core[near]]
    newD = {i for j in targets for i in L.owners[j]}
    newG = {int(L.cell_id[j]) for j in targets if not L.owners[j]}
    verify(
        not newD.intersection(anchors) and newD <= set(inv.candidate_ids),
        "an obligatory external owner is unremovable",
    )
    D1 = D | newD
    G1 = G | newG
    action = ("thermal-plus-registered-roof-enclosure", D1, G1, True)
    cleared = np.array([bool(ids) and set(ids) <= D1 for ids in L.owners]) | np.isin(
        L.cell_id, list(G1)
    )
    verify(
        not np.any(cleared & is_inside) and np.all(cleared[targets]),
        "inside persistence/outside clearance mismatch",
    )
    admissible = ~(cleared[exterior] | core[exterior])
    residual = shapely.STRtree(support[admissible]).query(
        in_geom, predicate="dwithin", distance=radius + 1e-8
    )
    verify(residual.shape[1] == 0, "exterior radiative support remains")
    # Independent direct contact check works on original polygons, not centroids.
    actual_contact = shapely.STRtree(polys[exterior[admissible]]).query(
        in_geom, predicate="intersects"
    )
    verify(actual_contact.shape[1] == 0, "nonprotected exterior contacts retained")
    rawroof = (
        shapely.union_all([b.geometry for b in inv.buildings if b.building_id in D1])
        .difference(
            shapely.union_all([b.geometry for b in inv.buildings if b.building_id not in D1])
        )
        .area
    )
    physical_C = math.fsum(shapely.area(polys[cleared & roof])) + len(G1) * parent.grid.cell_area_m2
    verify(
        abs(rawroof + len(G1) * parent.grid.cell_area_m2 - physical_C) < 1e-6,
        "original AND building union disagrees",
    )
    fp_cons = []
    for kind, prot, source, fp in families:
        (after,) = realize_sources(prot, inv, demolished_ids=sorted(D1))
        verify(
            after["source_id"] == source and after["footprint"].sha256 == fp.sha256,
            "post-action anchor/footprint changed",
        )
        before, hits = footprint_budget(L, polys, tree, source, fp, D, G)
        after, posthits = footprint_budget(L, polys, tree, source, fp, D1, G1)
        verify(
            before == after and np.array_equal(hits, posthits) and is_inside[hits].all(),
            "direct energy or material changed",
        )
        fp_cons.append((kind, source, hits))
    edges = potential_edges(L, radius)
    labels = components(edges, active & ~(core | cleared))
    physical_area = L.node_inputs.parameters.covered_area_m2.ravel()
    unknown = {b.building_id for b in inv.buildings} - {
        o for j, g in enumerate(L.owners) if physical_area[j] > 0 for o in g
    }
    comps_from_node = np.flatnonzero(is_inside)
    verify(
        not set(labels[comps_from_node]).intersection(labels[exterior[admissible]]),
        "leaking compartment in full potential graph",
    )
    source_bounds = []
    idx = defaultdict(int)
    for kind, s, hits in fp_cons:
        # All possible fragments of this original owner, independent of small direct mask artefacts.
        parts = {labels[j] for j in comps_from_node if s in L.owners[j]}
        verify(set(labels[hits]) <= parts and -1 not in parts, "source compartments invalid")
        nodes = comps_from_node[np.isin(labels[comps_from_node], list(parts))]
        area = area_outward(math.fsum(physical_area[nodes]), len(nodes), conf["max_nodes"])
        owners = unknown.union(*(set(L.owners[j]) for j in nodes))
        idx[kind] += 1
        source_bounds.append(
            {
                "campaign": kind,
                "declared_family_ordinal": idx[kind],
                "single_roof_A_upper_m2": area,
                "single_roof_N_owner_upper": len(owners),
            }
        )
    witnesses = proven_source_witnesses(L, inv)
    summary = summarize(L, inv, edges, action, unknown, anchors, witnesses, int(conf["max_nodes"]))
    verify(abs(summary["whole_action_area_m2"] - physical_C) < 1e-6, "cost verification mismatch")
    return {
        "village": village,
        "thermal_base_certificate_sha256": file_sha256(energy_file),
        "verified_thermal_parent_receipt_sha256": file_sha256(audits),
        "frozen_input_campaign_files_sha256": inputs,
        "all_set_model_source_families": len(families),
        "distinct_registered_roof_count": len(anchors),
        "known_seed_footprints_reconstructed_beyond_prior_action_prefix": newfp,
        "all_priority_choices_footprints_25m2_and_material_budgets_preserved": True,
        "potential_protected_core_transfer_from_group": linked_core > 0,
        "zero_transfer_even_into_protected_core_by_this_construction": linked_core == 0,
        "required_noncore_external_patch_count": len(targets),
        "external_ordinary_or_ground_residual_support_pairs": 0,
        "additional_external_core_ignition_probability_estimated": False,
        "construction_area_m2": physical_C,
        "additional_above_thermal_C_m2": physical_C - energy["construction_area_m2"],
        "additional_roof_action_count": len(D1 - D),
        "additional_ground_action_count": len(G1 - G),
        "model_action": {"demolished_ids": sorted(D1), "cleared_physical_cell_ids": sorted(G1)},
        "registered_source_family_capacities": source_bounds,
        "registered_worst_N_upper": max(y["single_roof_N_owner_upper"] for y in source_bounds),
        "registered_worst_A_upper_m2": max(y["single_roof_A_upper_m2"] for y in source_bounds),
        "all_enclosed_ordinary_roof_group_capacity_m2": area_outward(
            math.fsum(physical_area[comp_from] for comp_from in comps_from_node),
            len(comps_from_node),
            conf["max_nodes"],
        ),
        "arbitrary_single_retained_ordinary_roof_anywhere": summary,
        "other_unrepresented_original_owner_count": len(unknown),
        "source_target_locations_or_risk_ranking_in_output": False,
        "observed_native_A_m2": None,
        "observed_native_N": None,
        "proof_scope": (
            "Minimum extra construction for fixed ordinary roofs and original thermal "
            "plan under the sufficient geometric support separation from all remaining "
            "exterior ordinary fuel. Every obligatory exterior roof atom forces all its "
            "owners, every exterior ground cell its one action. Protected sites are "
            "separately proven nonigniting under the monotone finite-fuel energy "
            "certificate. Upper and lower construction bounds coincide; zero heat into "
            "the core is a separate condition."
        ),
        "evaluation_scope": (
            "Known registered draw families only for the tighter bounds. Previously "
            "reported confirmation families now contribute to design and cannot "
            "independently validate this adaptation. All other retained ordinary roofs "
            "have a separately reported village-wide bound. Finite model, no firebrands "
            "or outside feedback."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("village", choices=["batuan", "pingyan"])
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    r = compute(args.village)
    r["method_sha256"] = file_sha256(__file__)
    r["schema_version"] = "heritage-registered-roof-compartment-1"
    r["new_CA_trajectories"] = 0
    r["receipt_sha256"] = content_hash(r)
    with args.output.open("x") as h:
        json.dump(r, h, indent=2, allow_nan=False)
        h.write("\n")


if __name__ == "__main__":
    main()
