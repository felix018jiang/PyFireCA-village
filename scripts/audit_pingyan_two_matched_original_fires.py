"""Compare two same-physical-source native observations with their new design guarantees."""

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from check_heritage_registered_roof_compartment import footprint_budget, verify
from expand_pingyan_fixed_protective_compartments import Experiment, sealed

from pyfireca.planning.fire_loss import recover_original_node_owners
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.village.extinction_v3 import (
    extinction_certificate_sha256,
    extinction_observation_fingerprints,
)
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs/results"


def native(p, summary, exp, oldD, inv, unknown):
    digest = file_sha256(p)
    m = read_json(p)
    folder = p.parent
    verify(
        digest == summary["native_manifest_sha256"]
        and m["problem_sha256"] == exp["problem_sha256"] == summary["native_problem_sha256"],
        "native evidence lineage differs",
    )
    verify(
        all(file_sha256(folder / f) == h for f, h in m["files_sha256"].items()),
        "original fire artifact changed",
    )
    c = read_json(folder / "check.json")
    ev = c["evidence"]
    v = read_json(folder / "evaluation.json")
    t = ev["terminal_verification"]
    cert = t["temporal_closure_certificate"]
    verify(
        c["status"] == "passed"
        and ev["scale_m"] == 1
        and t["terminal_complete"]
        and not t["invalid_reasons"]
        and not t["core_ignition_observed"]
        and v["diagnostics"]["source_verified"]
        and cert["certificate_sha256"] == extinction_certificate_sha256(cert),
        "original strict observation unverified",
    )
    with np.load(folder / "final-state.npz") as d:
        fields = {k: d[k].copy() for k in V3State.__dataclass_fields__}
    fields["time_s"], fields["step_index"] = float(fields["time_s"]), int(fields["step_index"])
    state = V3State(**fields)
    fingerprints = extinction_observation_fingerprints(
        state, v["diagnostics"]["maximum_valid_surface_temperature_by_node_K"]
    )
    verify(
        all(cert[k] == h for k, h in fingerprints.items())
        and cert["actual_step_index"] == state.step_index
        and cert["actual_stop_time_s"] == state.time_s,
        "native terminal state or peak changed",
    )
    b = state.burnout_time_s.ravel()
    i = state.ignition_time_s.ravel()
    burned = np.flatnonzero(np.isfinite(b) & (b <= state.time_s))
    affected = np.flatnonzero(np.isfinite(i) & (i <= state.time_s))
    verify(np.array_equal(affected, burned), "native terminal fire still active")
    with np.load(folder / "loss-basis.npz") as d:
        after = d["post_intervention_covered_area_m2"].ravel()
        A = math.fsum(after[burned])
        verify(np.all(after[burned] > 0), "cleared node in thermal loss")
    with np.load(folder / "layout.npz") as d:
        ids = d["owner_ids"]
        off = d["owner_offsets"]
        original, _ = recover_original_node_owners(
            SimpleNamespace(
                owners=tuple(tuple(ids[off[j] : off[j + 1]]) for j in burned),
                xy_m=d["xy_m"][burned],
                component_id=d["component_id"][burned],
                cell_id=d["physical_cell_id"][burned],
            ),
            inv,
            demolished_ids=oldD,
        )
    low = len({g[0] for g in original if len(g) == 1})
    high = len(unknown.union(*(set(g) for g in original)))
    fire_loss = read_json(folder / "fire-loss.json")
    ledger = fire_loss["physical_remaining_roof_annotation_counts"]
    A_native = fire_loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
    verify(
        abs(A - A_native) < 1e-7
        and abs(A_native - summary["burned_physical_m2"]) < 1e-7
        and ledger["burned_out_annotation_attribution_count"]
        == summary["burned_buildings_original_owner_bounds"]
        == {"lower": low, "upper": high}
        and set(ledger["unrepresented_original_ids"]) == unknown,
        "independent actual area/owners differ",
    )
    return A_native, low, high, len(m["files_sha256"])


def audit():
    E = Experiment()
    new_path = RES / "heritage-pingyan-eight-known-zero-heat-and-design-loss-20261111.json"
    new = sealed(new_path)
    newplan = new["model_action"]
    newD = set(newplan["demolished_ids"])
    newG = set(newplan["cleared_physical_cell_ids"])
    archive = RES / "heritage-pingyan-independent-three-arm-protection-and-loss-20261111.json"
    orig = read_json(archive)
    held = ROOT / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
    exp = read_json(held / "experiment.json")
    plan = next(
        r
        for r in exp["config"]["plans"]
        if r["plan_id"] == "fixed-core-and-first-escape-compartment"
    )
    oldD = set(plan["demolished_ids"])
    oldG = set(plan["cleared_physical_cell_ids"])
    rows = {
        r["native_manifest_sha256"]: r
        for r in orig["native_protection_and_loss_by_registered_ordinal"].values()
        if r.get("roof_scale_m") == 1
        and r.get("registered_plan") == plan["plan_id"]
        and r.get("strict_status") == "passed"
        and r.get("strict_complete")
    }
    verify(len(rows) == 4, "expected four coarse original cases")
    paths = {}
    for p in held.glob("**/evaluations/*/complete.json"):
        h = file_sha256(p)
        if h in rows:
            ev = read_json(p.with_name("check.json"))["evidence"]
            paths[ev["fire_id"]] = (p, rows[h])
    verify(set(paths) == set(exp["problem"]["fires"]), "missing physical family observations")
    families = E.families.copy()
    matched = {}
    for f, fire in exp["problem"]["fires"].items():
        prot = fire["protocol"]
        (old,) = realize_sources(prot, E.inv, demolished_ids=sorted(oldD))
        (present,) = realize_sources(prot, E.inv, demolished_ids=sorted(newD))
        (base,) = realize_sources(prot, E.inv, demolished_ids=sorted(E.D0))
        oldsource, oldfp = old["source_id"], old["footprint"]
        source, fp = present["source_id"], present["footprint"]
        verify(
            source == base["source_id"] and fp.sha256 == base["footprint"].sha256,
            "new plan shifts frozen event",
        )
        p, summary = paths[f]
        ev = read_json(p.with_name("check.json"))["evidence"]
        verify(
            oldsource == ev["source_id"] and oldfp.sha256 == ev["source_footprint_sha256"],
            "original native event differs from physical protocol",
        )
        original, _ = footprint_budget(E.L, E.geom, E.tree, oldsource, oldfp, oldD, oldG)
        measured = [
            ev["source_budget"][k]
            for k in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        ]
        budget, _ = footprint_budget(E.L, E.geom, E.tree, source, fp, newD, newG)
        verify(np.allclose(measured, original, atol=1e-7, rtol=1e-10), "native source fuel differs")
        if (
            source == oldsource
            and fp.sha256 == oldfp.sha256
            and np.allclose(budget, measured, atol=1e-7, rtol=1e-10)
        ):
            matched[len(families)] = (p, summary)
        families.append((prot, source, fp))
    verify(
        len(matched)
        == new["previously_disclosed_four_matching_8465_on_physical_footprint_and_positive_fuel"]
        == 2,
        "different matched cases",
    )
    sources = {s for _, s, _ in families}
    pool = np.flatnonzero([bool(sources.intersection(g)) for g in E.L.owners])
    caps = []
    observed = []
    seen = 0
    for order, (_, source, fp) in enumerate(families):
        _, hit = footprint_budget(E.L, E.geom, E.tree, source, fp, newD, newG)
        g = set(hit.tolist())
        rest = set(pool.tolist()) - g
        step = g.copy()
        while step and rest:
            seed = shapely.union_all(E.geom[list(step)])
            a = np.array(sorted(rest))
            step = set(
                a[shapely.distance(E.geom[a], seed) <= E.t.exposure_radius_m + 1e-8].tolist()
            )
            g |= step
            rest -= step
        caps.append(E.cap(g))
        if order in matched:
            p, summary = matched[order]
            A, low, hi, count = native(p, summary, exp, oldD, E.inv, E.unknown)
            observed.append((A, low, hi))
            seen += count
    verify(
        content_hash(caps) == new["eight_case_cap_set_sha256"], "published capacity table differs"
    )
    A0 = max(o[0] for o in observed)
    N0 = [max(o[i] for o in observed) for i in (1, 2)]
    U = max(caps[j][0] for j in matched)
    N = max(caps[j][1] for j in matched)
    return dict(
        schema_version="paired-physical-coarse-native-vs-deterministic-limit-1",
        village="pingyan",
        known_ordering_families_screened=4,
        exact_physical_matches=2,
        excluded_replaced_source_cases=2,
        old_construction_C_m2=8465.532897386736,
        new_construction_C_m2=new["C_m2"],
        old_1m_aggregate_max_burned_m2=A0,
        old_1m_aggregate_worst_original_N_interval=N0,
        new_matched_analytic_A_upper_m2=U,
        new_matched_analytic_original_N_upper=N,
        guaranteed_difference_in_pair_worst_A_m2_at_least=math.nextafter(A0 - U, -math.inf),
        guaranteed_difference_in_pair_worst_N_upper_metric_at_least=N0[1] - N,
        guaranteed_difference_in_pair_worst_attributable_original_owner_count_at_least=N0[0] - N,
        common_source_criteria=(
            "Same retained roof and footprint, 25 m2, dry fuel mass and "
            "available energy verified against original and native material."
        ),
        comparison_scope=(
            "Worst over the same two families. Old outcomes are strict 1 m "
            "extinction endpoints. New limits cover both admissible meshes."
        ),
        interpretation=(
            "Matching applies to the initial physical material. The new capacity "
            "uses the eight disclosed design inputs."
        ),
        prospective_new_validation_families=0,
        new_CA_runs=0,
        verified_native_manifests=2,
        verified_native_artifact_hashes=seen,
        input_files_sha256={
            str(p.relative_to(ROOT)): file_sha256(p)
            for p in [new_path, archive, held / "experiment.json"]
        },
        matched_manifest_commitment=content_hash(
            sorted(file_sha256(p) for p, _ in matched.values())
        ),
    )


if __name__ == "__main__":
    d = audit()
    d["method_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    p = RES / "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json"
    with p.open("x") as f:
        json.dump(d, f, indent=2, allow_nan=False)
        f.write("\n")
    print(
        d["old_1m_aggregate_max_burned_m2"],
        d["old_1m_aggregate_worst_original_N_interval"],
        d["new_matched_analytic_A_upper_m2"],
        d["new_matched_analytic_original_N_upper"],
    )
