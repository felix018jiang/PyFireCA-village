"""Reconstruct a zero-heat protective action for eight previously disclosed ordering families."""

import json
import math
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from check_heritage_compartment_first_escape import pair_conditional_doses
from check_heritage_registered_roof_compartment import footprint_budget, verify
from expand_pingyan_fixed_protective_compartments import Experiment, sealed
from export_heritage_joint_loss_actions import physical_features

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs/results"
REG = RES / "heritage-pingyan-eight-known-zero-heat-registration-20261111.json"
STEM = "heritage-pingyan-eight-known-zero-heat-and-design-loss-20261111"


def same_budget(first, second):
    return np.allclose(first, second, atol=1.0e-7, rtol=1.0e-10)


def group_max(pairs):
    return dict(
        capacity_A_upper_m2=max(a for a, _ in pairs),
        capacity_original_N_upper=max(n for _, n in pairs),
    )


def construct():
    reg = sealed(REG)
    for p, d in reg["inputs_sha256"].items():
        verify(file_sha256(ROOT / p) == d, "changed registration input")
    e = Experiment()
    original = tuple(e.families)
    original_caps = [e.cap(g) for g in e.family_sets]
    fixed_four = sealed(RES / "heritage-pingyan-expanded-zero-heat-and-design-loss-20261111.json")[
        "model_action"
    ]
    older = e.parent["model_action"]
    design = read_json(
        ROOT
        / "runs/research/heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
    )
    revealed = read_json(
        ROOT
        / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
        / "experiment.json"
    )
    verify(
        all(
            design["problem"][k] == revealed["problem"][k]
            for k in ("case_sha256", "inventory_sha256", "transport_parameters", "overlap_policy")
        ),
        "geometry or physical transport changed",
    )
    incumbent = next(
        p
        for p in revealed["config"]["plans"]
        if p["plan_id"] == "fixed-core-and-first-escape-compartment"
    )
    heldD = set(incumbent["demolished_ids"])
    heldG = set(incumbent["cleared_physical_cell_ids"])
    joint = sealed(RES / "heritage-total-protection-and-loss-pingyan-20261111.json")
    old8465 = next(
        r for r in joint["optimized_total_construction"] if r["core_energy_ceiling"] == 0.99
    )
    verify(
        heldD == set(old8465["demolished_ids"])
        and heldG == set(old8465["cleared_physical_cell_ids"]),
        "different archived independent policy",
    )
    evidence = []
    design_matches = [0, 0]
    held_matches = 0
    stored = 0
    for prot, owner, fp in original:
        original_budget, _ = footprint_budget(e.L, e.geom, e.tree, owner, fp, e.D0, e.G0)
        for k, plan in enumerate([older, fixed_four]):
            D = set(plan["demolished_ids"])
            G = set(plan["cleared_physical_cell_ids"])
            (r,) = realize_sources(prot, e.inv, demolished_ids=sorted(D))
            b, _ = footprint_budget(e.L, e.geom, e.tree, r["source_id"], r["footprint"], D, G)
            same = (
                r["source_id"] == owner
                and r["footprint"].sha256 == fp.sha256
                and same_budget(b, original_budget)
            )
            design_matches[k] += int(same)
        evidence.append((content_hash(prot), fp.sha256, list(original_budget)))
    verify(design_matches == [4, 4], "earlier four design sources changed")
    for label, frozen in revealed["problem"]["fires"].items():
        prot = frozen["protocol"]
        (new,) = realize_sources(prot, e.inv, demolished_ids=sorted(e.D0))
        (old,) = realize_sources(prot, e.inv, demolished_ids=sorted(heldD))
        roo, fp = new["source_id"], new["footprint"]
        oldroo, oldfp = old["source_id"], old["footprint"]
        states = [
            r
            for r in revealed["source_realizations"]
            if r["plan_id"] == incumbent["plan_id"]
            and r["scenario_id"] in (label + "-h1", label + "-h0.5")
        ]
        verify(
            len(states) == 2
            and all(r["actual_source_id"] == oldroo for r in states)
            and oldfp.sha256 == content_hash(frozen["footprints"][oldroo]),
            "different original independent source",
        )
        B, _ = footprint_budget(e.L, e.geom, e.tree, roo, fp, e.D0, e.G0)
        oldB, _ = footprint_budget(e.L, e.geom, e.tree, oldroo, oldfp, heldD, heldG)
        held_matches += int(roo == oldroo and fp.sha256 == oldfp.sha256 and same_budget(B, oldB))
        if roo in frozen["footprints"]:
            verify(
                content_hash(frozen["footprints"][roo]) == fp.sha256, "stored geometry inconsistent"
            )
            stored += 1
        e.families.append((prot, roo, fp))
        evidence.append((content_hash(prot), fp.sha256, list(B)))
    verify(
        len(e.families) == len({x[0] for x in evidence}) == 8,
        "expected eight distinct registered orderings",
    )
    e.anchors = {s for _, s, _ in e.families}
    pool = np.flatnonzero([bool(set(os) & e.anchors) for os in e.L.owners])
    verify(
        not np.any(e.base[pool] | e.core[pool] | ~e.fuel[pool]),
        "unretained ordinary source support",
    )
    a, b = shapely.STRtree(e.geom[pool]).query(
        e.geom[pool], predicate="dwithin", distance=e.t.exposure_radius_m + 1e-8
    )
    select = a < b
    labels = components(
        np.column_stack((a[select], b[select])).astype(np.int32), np.ones(len(pool), bool)
    )
    groups = []
    baselines = []
    for _, s, fp in e.families:
        B, hit = footprint_budget(e.L, e.geom, e.tree, s, fp, e.D0, e.G0)
        verify(set(hit) <= set(pool), "source budget outside physical ordinary pool")
        selected = np.unique(labels[np.searchsorted(pool, hit)])
        g = frozenset(pool[np.isin(labels, selected)].tolist())
        # Reconstruct the reached set by repeated polygon distance.
        reached = set(hit.tolist())
        remaining = set(pool.tolist()) - reached
        front = set(reached)
        while front and remaining:
            shell = shapely.union_all(e.geom[list(front)])
            candidates = np.array(sorted(remaining))
            front = set(
                candidates[
                    shapely.distance(e.geom[candidates], shell) <= e.t.exposure_radius_m + 1e-8
                ].tolist()
            )
            reached.update(front)
            remaining -= front
        verify(reached == g, "independent initial compartment differs")
        groups.append(g)
        baselines.append(B)
    unique = tuple(dict.fromkeys(groups))
    sol = e.solution(unique)
    verify(
        sol is not None,
        "forced exterior actions incompatible with immutable ordinary source support",
    )
    C, D, G = sol
    M = e.model
    mask = M.mask(D, G)
    verify(e.D0 <= D and e.G0 <= G and not mask[e.core].any(), "zero-heat construction lost")
    live = e.live
    ind_D = e.D0.copy()
    ind_G = e.G0.copy()
    peaks = []
    counts = []
    for g in unique:
        nodes = np.array(sorted(g))
        outside = np.setdiff1d(live, nodes)
        ratios, contacts, n = pair_conditional_doses(
            e.L, e.geom, nodes, outside, [tuple(nodes.tolist())], e.t
        )
        bad = contacts | {i for i, x in ratios.items() if x >= 1 - 1e-10}
        for j in bad:
            if e.L.owners[j]:
                ind_D.update(e.L.owners[j])
            else:
                ind_G.add(int(e.L.cell_id[j]))
        verify(
            not mask[nodes].any() and all(mask[j] for j in contacts), "surviving exterior contact"
        )
        maxdose = max([0.0] + [x for j, x in ratios.items() if not mask[j]])
        verify(maxdose < 1 - 1e-10, "exterior dry energy bound exceeded")
        peaks.append(maxdose)
        counts.append(n)
    verify(ind_D == D and ind_G == G, "independent contact/dose action union differs")
    core = np.flatnonzero(e.core)
    external = np.flatnonzero(e.fuel & ~mask & ~e.core)
    rad = shapely.STRtree(M.support[external]).query(
        M.geo[core], predicate="dwithin", distance=e.t.exposure_radius_m + 1e-8
    )
    touch = shapely.STRtree(M.geo[external]).query(M.geo[core], predicate="dwithin", distance=1e-8)
    verify(rad.shape[1] == touch.shape[1] == 0, "possible external heat on heritage")
    legal = (
        e.L.physical_request.domain_mask
        & e.L.physical_request.known_mask
        & e.L.physical_request.burnable_mask
        & (e.L.physical_request.building_index < 0)
    ).ravel()
    grass = np.isin(e.L.cell_id, sorted(G))
    verify(
        legal[sorted(G)].all() and not np.any(grass & M.roof) and int(grass.sum()) == len(G),
        "illegal ground treatment",
    )
    roof = math.fsum(M.geo[j].area for j in np.flatnonzero(mask & M.roof))
    ground = len(G) * e.L.physical_request.grid.cell_area_m2
    verify(abs(C - (roof + ground)) < 1e-6, "physical union area differs")
    caps = []
    for (protocol, owner, fp), B, g in zip(e.families, baselines, groups, strict=True):
        (realized,) = realize_sources(protocol, e.inv, demolished_ids=sorted(D))
        b, hit = footprint_budget(e.L, e.geom, e.tree, owner, fp, D, G)
        verify(
            realized["source_id"] == owner
            and fp.sha256 == realized["footprint"].sha256
            and np.array_equal(B, b)
            and set(hit) <= g,
            "final physical start or budget changed",
        )
        caps.append(e.cap(g))
    objective = group_max(caps)
    cap_old = group_max(caps[:4])
    cap_held = group_max(caps[4:])
    control = dict(
        plan_id="eight-known-orderings-zero-heat-and-fixed-compartments",
        demolished_ids=sorted(D),
        cleared_physical_cell_ids=sorted(G),
        C_m2=C,
    )
    features, crs = physical_features(Path(e.cfg["study_path"]), control)
    geom = dict(
        type="FeatureCollection",
        metric_crs=crs,
        name="pingyan_eight_known_defensive_clearance",
        construction_m2=C,
        features=features,
    )
    certificate = dict(
        schema_version="eight-disclosed-design-zero-heritage-heat-first-escape-1",
        village="pingyan",
        cohorts="Four original and four previously disclosed orderings, all now design inputs.",
        sources_frozen=8,
        unique_prescribed_physical_footprints=len({r[1] for r in evidence}),
        final_25m2_and_fuel_energy_budgets_unchanged=8,
        source_receipt_aggregate_sha256=content_hash(evidence),
        original_four_matching_9865_on_physical_footprint_and_positive_fuel=design_matches[0],
        original_four_matching_9815_on_physical_footprint_and_positive_fuel=design_matches[1],
        previously_disclosed_four_matching_8465_on_physical_footprint_and_positive_fuel=held_matches,
        previously_disclosed_zero_base_footprints_already_materialized_on_prior_mesh=stored,
        core_zero_baseline_m2=e.parent["base_zero_heat_construction_m2"],
        C_m2=C,
        roof_clearing_union_m2=roof,
        whole_ground_area_m2=ground,
        extra_over_zero_core_m2=C - e.parent["base_zero_heat_construction_m2"],
        protected_heritage_count=len(e.core_ids),
        all_external_core_heat_zero=True,
        all_eight_design_capacities=objective,
        original_four_in_this_eight_design=cap_old,
        prior_four_design_capacity=group_max(original_caps),
        previously_disclosed_four_in_this_eight_design=cap_held,
        previous_A241_49_and_N3_cap_covers_all_eight=(
            objective["capacity_A_upper_m2"] <= 241.49
            and objective["capacity_original_N_upper"] <= 3
        ),
        rule=(
            "Smallest common ceilings covering all fixed compartment capacity upper bounds. "
            "These are analytical capacities."
        ),
        cost_scope=(
            "Minimum construction union for this base, the fixed footprints and "
            "compartments under the sufficient contact/lifetime-energy screen. "
            "Each required atom must clear, including every co-owner on a shared roof."
        ),
        distinct_initial_compartments=len(unique),
        independent_contact_energy_union_equal=True,
        maximum_surviving_external_energy_ratio=max(peaks),
        zero_cross_contact=True,
        new_source_draws=0,
        new_native_fire_simulations=0,
        new_independent_validation_scenarios=0,
        request_condition_and_inputs_sha256=file_sha256(REG),
        eight_case_cap_set_sha256=content_hash(caps),
        model_action=control,
    )
    return certificate, geom


if __name__ == "__main__":
    report, gis = construct()
    p = RES / (STEM + ".geojson")
    with p.open("x") as f:
        json.dump(gis, f, allow_nan=False)
        f.write("\n")
    report["GIS_sha256"] = file_sha256(p)
    report["method_sha256"] = file_sha256(__file__)
    report["receipt_sha256"] = content_hash(report)
    with (RES / (STEM + ".json")).open("x") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print(
        report["C_m2"],
        report["all_eight_design_capacities"],
        report["previously_disclosed_four_matching_8465_on_physical_footprint_and_positive_fuel"],
    )
