"""Combine Pingyan's zero-external-heat core with containment of four retained design fires."""

import json
import math
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from check_heritage_compartment_first_escape import load_bound_inputs, pair_conditional_doses
from check_heritage_registered_roof_compartment import area_outward, footprint_budget, verify
from export_heritage_joint_loss_actions import physical_features
from verify_heritage_total_protection_and_loss import NativeSupport, direct_witness

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def audit(root):
    res = root / "docs/results"
    zero_path = res / "heritage-ground-centre-core-separation-20261111.json"
    experimental = (
        root
        / "runs/research/heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
    )
    zero_cert = read_json(zero_path)
    verify(
        zero_cert.pop("audit_sha256") == content_hash(zero_cert),
        "upstream zero-heat certificate changed",
    )
    zero = next(x for x in zero_cert["villages"] if x["village"] == "pingyan")
    exp = read_json(experimental)
    verify(
        exp["problem_sha256"] == content_hash(exp["problem"]), "source or equation registry changed"
    )
    _, _, layout, inv, transport, _, _, old, conf = load_bound_inputs("pingyan")
    original = next(
        p for p in exp["config"]["plans"] if p["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    D0 = set(original["demolished_ids"])
    G0 = set(original["cleared_physical_cell_ids"])
    verify(
        content_hash(dict(demolished_ids=sorted(D0), cleared_physical_cell_ids=sorted(G0)))
        == zero["canonical_actions_sha256"],
        "core-only starting action differs",
    )
    registrar = {content_hash(f["protocol"]): f for f in exp["problem"]["fires"].values()}
    verify(
        len(old) == len(registrar) == 4 and all(x[0] == "design" for x in old),
        "expected four original design families only",
    )
    newly_realized, changed = [], 0
    for kind, protocol, source, previous in old:
        (current,) = realize_sources(protocol, inv, demolished_ids=sorted(D0))
        current_id, fp = current["source_id"], current["footprint"]
        verify(
            fp.sha256 == content_hash(registrar[content_hash(protocol)]["footprints"][current_id]),
            "actual source does not match frozen geometry",
        )
        newly_realized.append((kind, protocol, current_id, fp))
        changed += (current_id, fp.sha256) != (source, previous.sha256)
    native = NativeSupport(layout, transport)
    polys = native.geo
    tree = shapely.STRtree(polys)
    known = layout.node_inputs
    fuel = known.burnable_mask.ravel()
    target_ids = {x.building_id for x in inv.targets}
    core = np.array([bool(target_ids.intersection(g)) for g in layout.owners])
    base = native.mask(D0, G0)
    areas = known.parameters.covered_area_m2.ravel()
    full_sources = {family[2] for family in newly_realized}
    source_group = np.array([bool(full_sources.intersection(g)) for g in layout.owners])
    verify(
        not np.any(source_group & (core | base | ~fuel)) and full_sources <= set(inv.candidate_ids),
        "a fixed ordinary roof is ineligible",
    )
    inside = np.flatnonzero(source_group)
    neighbors = shapely.STRtree(polys[inside]).query(
        polys[inside], predicate="dwithin", distance=transport.exposure_radius_m + 1e-8
    )
    a, b = neighbors
    shortlist = a < b
    labels = components(
        np.column_stack((a[shortlist], b[shortlist])).astype(np.int32),
        np.ones(len(inside), dtype=bool),
    )
    group_labels = np.full(len(polys), -1, dtype=int)
    group_labels[inside] = labels
    sets, parent_budgets = [], []
    for _, _, source, fp in newly_realized:
        budget, hits = footprint_budget(layout, polys, tree, source, fp, D0, G0)
        seed_labels = np.unique(group_labels[hits])
        verify(np.all(seed_labels >= 0), "footprint not supported by included ordinary roofs")
        sets.append(tuple(inside[np.isin(labels, seed_labels)].tolist()))
        parent_budgets.append(budget)
    outside = np.flatnonzero(fuel & ~(base | core | source_group))
    ratios, contacts, checked = pair_conditional_doses(
        layout, polys, inside, outside, sets, transport
    )
    must_clear = contacts | {i for i, frac in ratios.items() if frac >= 1 - 1e-10}
    D = D0 | {o for i in must_clear for o in layout.owners[i]}
    G = G0 | {int(layout.cell_id[i]) for i in must_clear if not layout.owners[i]}
    verify(
        set(inv.candidate_ids) >= D and D.isdisjoint(target_ids | full_sources),
        "obligatory exterior conflicts with a protected/retained owner",
    )
    cleared = native.mask(D, G)
    external = outside[~cleared[outside]]
    verify(
        not np.any(cleared & (core | source_group)) and all(cleared[i] for i in must_clear),
        "mandatory clearing incomplete",
    )
    largest = max([0.0] + [q for i, q in ratios.items() if not cleared[i]])
    independent_max = 0.0
    for group in sorted(set(sets)):
        target, _, dose, contact_nodes, _ = native.pairs(np.asarray(group), external)
        independent_max = max(
            independent_max, float(np.bincount(target, weights=dose, minlength=1).max())
        )
        verify(
            len(contact_nodes) == 0,
            "ordinary exterior contact discovered in independent spatial audit",
        )
    verify(
        largest < 1 - 1e-10
        and math.isclose(largest, independent_max, abs_tol=1e-10, rel_tol=1e-10),
        "independent escape energy failed",
    )
    # Check every surviving emitter against every protected receiver for the combined action.
    all_external = np.flatnonzero(fuel & ~(cleared | core))
    core_i = np.flatnonzero(core)
    rad = shapely.STRtree(native.support[core_i]).query(
        native.support[all_external],
        predicate="dwithin",
        distance=transport.exposure_radius_m + 1e-8,
    )
    contact = shapely.STRtree(polys[core_i]).query(
        polys[all_external], predicate="dwithin", distance=1e-8
    )
    verify(
        not (rad.shape[1] or contact.shape[1]),
        "retained emitter may convey heat to protected buildings",
    )
    independent_budget = list(direct_witness((layout, inv), polys, tree, D, G, newly_realized))
    verify(
        np.allclose(parent_budgets, independent_budget, rtol=1e-10, atol=1e-7),
        "independent final area/fuel source budget differs",
    )
    uncovered = set(inv.by_id) - {o for i, g in enumerate(layout.owners) if areas[i] > 0 for o in g}
    bounds = []
    for group, (_, _, source, fp), baseline in zip(
        sets, newly_realized, parent_budgets, strict=True
    ):
        budget, hits = footprint_budget(layout, polys, tree, source, fp, D, G)
        verify(
            np.array_equal(baseline, budget) and set(hits) <= set(group),
            "source energy changed under final action",
        )
        nodes = np.array(group)
        bounds.append(
            (
                area_outward(math.fsum(areas[nodes]), len(nodes), conf["max_nodes"]),
                len(uncovered.union(*(set(layout.owners[i]) for i in group))),
            )
        )
    roof_union = math.fsum(layout.geometry[i].area for i in np.flatnonzero(cleared & native.roof))
    pasture = len(G) * layout.physical_request.grid.cell_area_m2
    plan = dict(
        plan_id="zero-external-core-heat-and-fixed-design-losses",
        demolished_ids=sorted(D),
        cleared_physical_cell_ids=sorted(G),
        C_m2=roof_union + pasture,
    )
    features, crs = physical_features(Path(conf["study_path"]), plan)
    base_cost = zero["minimum_C_m2_for_mixed_support_rule"]
    result = dict(
        schema_version="pingyan-zero-heat-core-plus-first-escape-fixed-design-1",
        village="pingyan",
        base_zero_heat_construction_m2=base_cost,
        C_m2=plan["C_m2"],
        additional_above_base_m2=plan["C_m2"] - base_cost,
        roof_clearing_union_m2=roof_union,
        whole_ground_clearing_m2=pasture,
        roof_action_count=len(D),
        whole_ground_cells=len(G),
        number_protected_ordinary_or_heritage_core_buildings=len(target_ids),
        actual_source_and_full_25m2_changed_vs_original_shared_thermal_design=changed,
        actual_source_and_full_25m2_unchanged_vs_original_shared_thermal_design=4 - changed,
        actual_source_and_full_25m2_unchanged_from_zero_base_through_final_plan=4,
        fixed_original_design_families=4,
        source_ranking_draws_added=0,
        source_budget_positive_area_mass_energy_verified=4,
        native_CA_trajectories_for_new_plan=0,
        independent_new_source_families_in_design=0,
        exterior_to_core_possible_radiative_pairs=rad.shape[1],
        exterior_to_core_possible_contact_pairs=contact.shape[1],
        core_forced_external_heat_zero_K_rise_from_ambient_for_all_exterior_ignitions=True,
        fixed_design_loss_scope=(
            "Each of four original retained-source priority lists realizes a positive 25 m2 "
            "physical footprint under the frozen zero-core-heat base. It remains the same "
            "under all added actions. Only these four families share the reported "
            "whole-village loss ceiling."
        ),
        core_scope=(
            "All passive, initially ambient heritage receivers, for any direct exterior "
            "ignition, finite original zero-wind and no-firebrands model, no imposed new "
            "core ignition or external heating."
        ),
        maximum_remaining_exterior_lifetime_dose_fraction=largest,
        independently_recomputed_maximum_fraction=independent_max,
        retained_ordinary_positive_contact_edges_from_containment=0,
        source_cohort_recipient_doses_checked=checked,
        worst_registered_burned_area_upper_m2=max(a for a, _ in bounds),
        worst_registered_original_building_owner_count_upper=max(n for _, n in bounds),
        modeled_terminal_losses_measured=False,
        construction_scope=(
            "Feasible placement with minimum added clearance for these fixed containment "
            "sets, zero-heat base and sufficient full-life dose/contact inequalities. Other "
            "compartment selections or full dynamics may admit cheaper placement."
        ),
        model_action=plan,
        receipt_sources_sha256={
            "base_zero_heat": file_sha256(zero_path),
            "native_registered_source_and_equations": file_sha256(experimental),
            "method": file_sha256(__file__),
            **{
                s: file_sha256(Path(__file__).with_name(s))
                for s in (
                    "check_heritage_compartment_first_escape.py",
                    "check_heritage_registered_roof_compartment.py",
                    "verify_heritage_total_protection_and_loss.py",
                    "export_heritage_joint_loss_actions.py",
                )
            },
        },
        geojson_coordinate_check=(
            "projected union area and WGS84 round trip checked independently for both "
            "building union and ground layer"
        ),
    )
    result["receipt_sha256"] = content_hash(result)
    out = res / "heritage-pingyan-zero-heat-and-design-loss-20261111.json"
    with out.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
    gis = dict(
        type="FeatureCollection",
        name="pingyan_zero_heat_heritage_and_fixed_design_loss_clearance",
        metric_crs=crs,
        features=features,
        construction_m2=plan["C_m2"],
        design_burned_area_upper_m2=result["worst_registered_burned_area_upper_m2"],
        design_original_owner_upper=result["worst_registered_original_building_owner_count_upper"],
        certificate_file_sha256=file_sha256(out),
    )
    with out.with_suffix(".geojson").open("x") as f:
        json.dump(gis, f, allow_nan=False)
        f.write("\n")
    print(
        "Completed aggregate containment and GIS",
        plan["C_m2"],
        max(a for a, _ in bounds),
        max(n for _, n in bounds),
        changed,
        flush=True,
    )


if __name__ == "__main__":
    audit(Path(__file__).resolve().parents[1])
