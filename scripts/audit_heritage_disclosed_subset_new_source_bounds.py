"""Bound two fixed Pingyan plans on the four already drawn confirmatory families."""

import json
import math
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components, potential_edges
from check_heritage_compartment_first_escape import pair_conditional_doses
from check_heritage_registered_roof_compartment import area_outward, footprint_budget, verify

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def fixed_files(root):
    research = root / "runs/research"
    results = root / "docs/results"
    reg = (
        root / "examples/research/heritage/confirmation-20261110/pingyan-three-fixed-plans-20261111"
    )
    paths = dict(
        registration=reg / "original-binding/binding-receipt.json",
        three_prespecified_plans=reg / "final-candidates.json",
        independent_config=reg / "supplemental-stage-capacity-config.json",
        independent_experiment=research
        / "heritage-pingyan-independent-three-stage-supplement-20261111/experiment.json",
        physical_support_witness=results
        / "heritage-pingyan-independent-three-stage-bounds-20261111.json",
        compact_plan=root
        / "examples/research/heritage/area-20261110"
        / "pingyan-observed-clearance-subset-20261111.json",
        compact_design_experiment=research
        / "heritage-pingyan-observed-clearance-subset-20261111/experiment.json",
        completed_design_comparison=results
        / "heritage-pingyan-three-policies-complete8-table-20261111.json",
        design_merge=results
        / "heritage-pingyan-three-policies-seven-plus-one-strict-20261111.json",
    )
    digests = {k: file_sha256(p) for k, p in paths.items()}
    data = {k: read_json(p) for k, p in paths.items()}
    native, cfg, binding = (
        data["independent_experiment"],
        data["independent_config"],
        data["registration"],
    )
    verify(
        native["problem_sha256"] == content_hash(native["problem"])
        and native["config"] == cfg
        and native["config_sha256"] == digests["independent_config"],
        "native problem or configuration changed",
    )
    verify(
        binding["new_independent_draws"] == 4
        and binding["planned_CA_checks"] == 24
        and binding["final_candidates_sha256"]
        == digests["three_prespecified_plans"]
        == cfg["final_candidates_sha256"],
        "registration changed",
    )
    verify(
        data["three_prespecified_plans"]["plans"] == cfg["plans"] and len(cfg["plans"]) == 3,
        "registered actions changed",
    )
    verify(
        cfg["supplemental_geometry_receipt"]["sha256"] == digests["physical_support_witness"]
        and all(
            file_sha256(path) == digest
            for path, digest in cfg["additional_frozen_inputs_sha256"].items()
        ),
        "input witness changed",
    )
    old = data["compact_design_experiment"]
    verify(
        old["config"] == data["compact_plan"]
        and old["config_sha256"] == digests["compact_plan"]
        and old["problem_sha256"] == content_hash(old["problem"]),
        "fixed compact action changed",
    )
    design = data["completed_design_comparison"]
    verify(
        design["receipt_sha256"]
        == content_hash({k: v for k, v in design.items() if k != "receipt_sha256"})
        and design["merge_file_sha256"] == digests["design_merge"],
        "design audit changed",
    )
    for p in (
        "src/pyfireca/planning/roof_ignition.py",
        "src/pyfireca/planning/random_sources.py",
        "src/pyfireca/planning/roof_thermal.py",
    ):
        verify(
            file_sha256(root / p) == native["source_sha256"][p],
            "native source or mesh algorithm changed",
        )
    return data, digests


def action(layout, plan, protected, inv, expected_C):
    roof, grass = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
    inputs = layout.node_inputs
    base = layout.physical_request
    eligible_ground = (
        base.domain_mask
        & base.known_mask
        & base.burnable_mask
        & (base.building_index < 0)
        & (base.parameters.covered_area_m2 > 0)
    ).ravel()
    verify(
        roof <= set(inv.candidate_ids)
        and protected.isdisjoint(roof)
        and all(eligible_ground[x] for x in grass),
        "invalid action eligibility",
    )
    cleared_roofs = np.array([bool(g) and set(g) <= roof for g in layout.owners])
    cleared_grass = np.isin(layout.cell_id, list(grass))
    verify(
        cleared_grass.sum() == len(grass)
        and all(not layout.owners[i] for i in np.flatnonzero(cleared_grass)),
        "ground action is not a whole legal cell",
    )
    clear = cleared_roofs | cleared_grass
    active = inputs.burnable_mask.ravel() & ~clear
    cost = (
        math.fsum(layout.geometry[i].area for i in np.flatnonzero(cleared_roofs))
        + len(grass) * base.grid.cell_area_m2
    )
    verify(
        math.isclose(expected_C, cost, rel_tol=0, abs_tol=1e-6),
        "union clearance differs from design",
    )
    return dict(
        roof=roof, grass=grass, clear=clear, active=active, C_m2=cost, plan_id=plan["plan_id"]
    )


def envelope(layout, polygons, transfer, active, geometric, hits, transport):
    group = np.zeros(len(active), dtype=bool)
    group[hits] = True
    verify(active[hits].all(), "initial footprint outside surviving material")
    # Start with only physical parents supporting forced ignition. A complete local
    # energy enclosure includes every emitter it admits at its full lifetime energy.
    # The complete geometric component still bounds loss if this search becomes large.
    for _ in range(24):
        nodes = np.flatnonzero(group)
        if len(nodes) > 256:
            break
        outside = np.flatnonzero(active & ~group)
        q, contact, _ = pair_conditional_doses(
            layout, polygons, nodes, outside, [tuple(nodes)], transport
        )
        additions = contact | {i for i, ratio in q.items() if ratio >= 1 - 1e-10}
        if not additions:
            return group, "closed_lifetime_heat_budget"
        group[list(additions)] = True
    if geometric[0] is None:
        geometric[0] = components(transfer, active)
    labels = geometric[0]
    return np.isin(labels, np.unique(labels[hits])), "closed_geometric_component"


def audit(root):
    data, digests = fixed_files(root)
    native, cfg, bind = (
        data["independent_experiment"],
        data["independent_config"],
        data["registration"],
    )
    problem = native["problem"]
    bundle, transport, inv = load_study(Path(cfg["study_path"]))
    parent = bundle.request
    verify(
        request_fingerprint(parent) == problem["case_sha256"]
        and inv.sha256 == problem["inventory_sha256"]
        and parent.firebrands is None
        and transport.wind_speed_m_s == 0,
        "independent physics differs",
    )
    layout = resolve_roof_layout(
        parent, inv, max_nodes=cfg["max_nodes"], core_overlap_policy=cfg["core_overlap_policy"]
    )
    (compact,) = data["compact_plan"]["plans"]
    wide = next(p for p in cfg["plans"] if p["plan_id"].endswith("w15.01"))
    verify(
        compact["plan_id"] not in problem["joint_plans"],
        "compact was already a named registration arm",
    )
    design = {x["plan_id"]: x for x in data["completed_design_comparison"]["tested_plans"]}
    protected = {x.building_id for x in inv.targets}
    plans = [
        action(layout, plan, protected, inv, design[plan["plan_id"]]["C_m2"])
        for plan in (compact, wide)
    ]
    c, w = plans
    verify(
        c["roof"] <= w["roof"]
        and c["grass"] <= w["grass"]
        and not np.any(c["clear"] & ~w["clear"]),
        "compact is not a physical subset of the wide clearance",
    )
    core = np.array([bool(protected.intersection(group)) for group in layout.owners])
    verify(
        core.any() and all(not np.any(p["clear"] & core) for p in plans), "protected roof cleared"
    )
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    unknown = set(inv.by_id) - {o for i, g in enumerate(layout.owners) if area[i] > 0 for o in g}
    polygons = np.asarray(layout.geometry, dtype=object)
    tree = shapely.STRtree(polygons)
    rows = {(r["draw_ordinal"], r["scale_m"]): r for r in data["physical_support_witness"]["rows"]}
    transfer = potential_edges(layout, transport.exposure_radius_m)
    geometry = [[None], [None]]
    records = [[], []]
    matched, separation, physical_support = 0, 0, 0
    bound = {item["fire_id"]: item for item in bind["common_source_and_refinement_requirements"]}
    verify(
        len(cfg["source_fires"]) == len(problem["fires"]) == len(bound) == 4 and len(rows) == 8,
        "incomplete source registry",
    )
    restored = c["active"] & ~w["active"]
    for ordinal, request in enumerate(cfg["source_fires"], 1):
        name = request["fire_id"]
        fire, registered = problem["fires"][name], bound[name]
        sourcepath = request["protocol_path"]
        verify(
            bind["source_protocol_files_sha256"][sourcepath] == file_sha256(sourcepath)
            and fire["protocol"] == read_json(sourcepath),
            "draw changed",
        )
        prepared = []
        for arm, plan in enumerate(plans):
            verify(plan["roof"] <= set(fire["refined_ids"]), "action needs extra refinement")
            (actual,) = realize_sources(fire["protocol"], inv, demolished_ids=sorted(plan["roof"]))
            owner, fp = actual["source_id"], actual["footprint"]
            verify(
                fp.sha256 == content_hash(fire["footprints"][owner]),
                "source missing from existing common mesh",
            )
            witnesses = [
                r["plan_id"]
                for r in registered["per_plan"]
                if r["source_id"] == owner and r["footprint_sha256"] == fp.sha256
            ]
            verify(
                witnesses and (arm != 1 or wide["plan_id"] in witnesses),
                "actual footprint has no registered common-mesh source witness",
            )
            budget, hits = footprint_budget(
                layout, polygons, tree, owner, fp, plan["roof"], plan["grass"]
            )
            for scale in cfg["roof_scales_m"]:
                row = rows[ordinal, scale]
                for witness in witnesses:
                    supported = row["physical_support_by_plan"][witness]
                    native_budget = [
                        supported["native_budget"][k]
                        for k in (
                            "covered_area_m2",
                            "initial_dry_fuel_mass_kg",
                            "available_energy_J",
                        )
                    ]
                    verify(
                        row["nodes"] <= cfg["max_nodes"]
                        and supported["footprint_sha256"] == fp.sha256
                        and max(supported["footprint_extra_m2"], supported["footprint_missing_m2"])
                        <= 1e-8
                        and np.allclose(native_budget, budget, rtol=1e-9, atol=1e-7),
                        "native common-mesh budget differs",
                    )
                physical_support += 1
            group, method = envelope(
                layout, polygons, transfer, plan["active"], geometry[arm], hits, transport
            )
            nodes = np.flatnonzero(group)
            owners = unknown.union(*(set(layout.owners[i]) for i in nodes if layout.owners[i]))
            records[arm].append(
                dict(
                    method=method,
                    core_safe=not np.any(group & core),
                    A=area_outward(math.fsum(area[nodes]), len(nodes), cfg["max_nodes"]),
                    N=len(owners),
                )
            )
            prepared.append((fp.sha256, group))
        if prepared[0][0] == prepared[1][0]:
            matched += 1
            group = prepared[0][1]
            # Strict geometric silence, including subthreshold energy at changed receivers.
            # This is a physical screen. A separate native clock/graph proof would be
            # required to equate complete numerical trajectories and burned-area outcomes.
            u, v = transfer[:, 0], transfer[:, 1]
            if not np.any(group & restored) and not np.any(
                (group[u] & restored[v]) | (restored[u] & group[v])
            ):
                separation += 1
        print("completed one blinded source family", flush=True)
    out = []
    for p, values in zip(plans, records, strict=True):
        out.append(
            dict(
                plan_id=p["plan_id"],
                C_m2=p["C_m2"],
                families_bounded=len(values),
                closed_energy_envelopes=sum(
                    x["method"] == "closed_lifetime_heat_budget" for x in values
                ),
                geometric_component_fallbacks=sum(
                    x["method"] == "closed_geometric_component" for x in values
                ),
                heritage_nonignition_proved_families=sum(bool(x["core_safe"]) for x in values),
                worst_burned_area_upper_m2=max(x["A"] for x in values),
                worst_possible_original_owner_count_upper=max(x["N"] for x in values),
            )
        )
    return dict(
        schema_version="fixed-pingyan-compact-new-source-aggregated-extrapolation-1",
        four_new_families_already_drawn=4,
        reused_scales_m=cfg["roof_scales_m"],
        candidate_added_to_registered_arms=False,
        native_CA_evaluations_for_this_audit=0,
        new_source_draws=0,
        native_compact_confirmation_results_imported=0,
        source_identity_and_full_25m2_footprint_matched_between_both_plans=matched,
        native_topology_common_to_both_plans_for_families=4,
        unchanged_registered_mesh_and_verified_direct_support_plan_family_scale_tuples=physical_support,
        paired_families_with_no_possible_transfer_edge_to_restored_material=separation,
        paired_native_trajectory_equality_certified_families=0,
        independent_compact_dominance_established=False,
        complete_common_geometric_scope=(
            "Parent polygon/radius support bounds all source/target refinements. Contact, "
            "cumulative lifetime dry ignition dose and all retained core parents are "
            "included. Fallback uses the full reachable component, including unknown "
            "original owners in the count. No native trajectory or actual loss inferred."
        ),
        numerical_invariance_scope=(
            "A silent changed-material boundary is only a physical screen. Native graph "
            "rows, integrated mask, control time step, and updates would also have to agree "
            "to transfer an observed loss."
        ),
        results=out,
        input_file_sha256=digests,
        method_sha256=file_sha256(__file__),
        helper_sha256={
            name: file_sha256(root / "scripts" / name)
            for name in [
                "audit_heritage_compartment_bounds.py",
                "check_heritage_compartment_first_escape.py",
                "check_heritage_registered_roof_compartment.py",
                "verify_heritage_multiplan_confirmation_sources.py",
            ]
        },
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    result = audit(root)
    result["receipt_sha256"] = content_hash(result)
    with (
        root / "docs/results/heritage-pingyan-compact-new-source-extrapolation-20261111.json"
    ).open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
