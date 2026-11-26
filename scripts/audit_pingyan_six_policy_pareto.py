"""Certify finite-set Pareto comparisons from registered strict native witnesses."""

import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from audit_heritage_core_separation import require
from check_heritage_registered_roof_compartment import footprint_budget
from resume_heritage_joint_schedule import verify_original
from run_heritage_pareto_witnesses import (
    CONFIG,
    MINIMUM,
    ORIGIN,
    REGISTRATION,
    SHARD,
    TABLE,
    selected_cases,
)
from summarize_heritage_joint_pareto import summarize

from pyfireca.planning.fire_loss import recover_original_node_owners
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def read_seal(path):
    data = read_json(path)
    require(data.pop("receipt_sha256") == content_hash(data), "changed evidence receipt")
    return data


def verify_native(row, exp, original, inv, unrepresented, polygons, tree):
    p = Path(row["manifest"])
    require(file_sha256(p) == row["manifest_sha256"], "manifest changed")
    saved = read_json(p)
    require(saved["problem_sha256"] == exp["problem_sha256"], "different frozen problem")
    folder = p.parent
    for name, h in saved["files_sha256"].items():
        require(file_sha256(folder / name) == h, "bound native artifact changed")
    scenario = row["scenario_id"]
    plan = next(x for x in exp["config"]["plans"] if x["plan_id"] == row["plan_id"])
    D = set(plan["demolished_ids"])
    G = set(plan["cleared_physical_cell_ids"])
    require(
        set(saved["demolished_ids"]) == D
        and set(saved["cleared_physical_cell_ids"]) == G
        and saved["scenario_id"] == scenario,
        "different intervention",
    )
    raw = read_json(folder / "check.json")["evidence"]
    record = read_json(folder / "evaluation.json")
    fire = exp["problem"]["fires"][raw["fire_id"]]
    (direct,) = realize_sources(fire["protocol"], inv, demolished_ids=sorted(D))
    source = direct["source_id"]
    footprint = direct["footprint"]
    require(
        scenario == raw["fire_id"] + "-h1"
        and raw["scale_m"] == 1
        and footprint.sha256 == raw["source_footprint_sha256"]
        and raw["source_id"] == source
        and record["metrics"]["actual_source_ids"] == [source],
        "different direct event",
    )
    base, _ = footprint_budget(original, polygons, tree, source, footprint, D, G)
    actual = [
        raw["source_budget"][key]
        for key in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
    ]
    require(np.allclose(actual, base, rtol=1e-9, atol=1e-7), "source area, mass or energy differs")
    require(row["status"] in {"passed", "failed", "unknown"}, "unexpected native status")
    with np.load(folder / "final-state.npz") as d:
        end = float(d["time_s"])
        ii = d["ignition_time_s"].ravel()
        bb = d["burnout_time_s"].ravel()
        ign = np.flatnonzero(np.isfinite(ii) & (ii <= end))
        spent = np.flatnonzero(np.isfinite(bb) & (bb <= end))
        require(np.isin(spent, ign).all(), "burnout lacks finite ignition")
    with np.load(folder / "loss-basis.npz") as d:
        area = d["post_intervention_covered_area_m2"].ravel()
        require(np.all(area[ign] > 0), "cleared atom ignited")
        A_fsum = math.fsum(area[spent])
        active = math.fsum(area[np.setdiff1d(ign, spent)])
    with np.load(folder / "layout.npz") as d:
        ids = d["owner_ids"]
        off = d["owner_offsets"]
        owners, _ = recover_original_node_owners(
            SimpleNamespace(
                owners=tuple(tuple(ids[off[i] : off[i + 1]]) for i in ign),
                component_id=d["component_id"][ign],
                cell_id=d["physical_cell_id"][ign],
                xy_m=d["xy_m"][ign],
            ),
            inv,
            demolished_ids=D,
        )
    protected = {b.building_id for b in inv.targets}
    core_ignited = any(protected.intersection(g) for g in owners)
    require(
        core_ignited
        == bool(record["metrics"]["ignited_core_ids"])
        == row["core_ignition_observed"],
        "heritage records/attribution differ",
    )
    affected = [owners[i] for i in np.searchsorted(ign, spent)]
    N = dict(
        lower=len({g[0] for g in affected if len(g) == 1}),
        upper=len(unrepresented.union(*(set(g) for g in affected))),
    )
    loss = read_json(folder / "fire-loss.json")
    ledger = loss["physical_remaining_roof_annotation_counts"]
    require(
        set(ledger["unrepresented_original_ids"]) == unrepresented
        and ledger["burned_out_annotation_attribution_count"] == N,
        "original attribution differs",
    )
    A = loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
    require(abs(A - A_fsum) < 1e-7, "native and independently summed burned areas differ")
    passed = bool(row["status"] == "passed" and row["terminal_complete"] and not core_ignited)
    failed = bool(row["status"] == "failed" and core_ignited and not row["invalid_reasons"])
    require(not passed or active == 0, "active fire in closed case")
    return dict(
        plan=plan["plan_id"],
        manifest_sha256=file_sha256(p),
        native_artifacts_verified=len(saved["files_sha256"]),
        status=row["status"],
        strict_safe_and_closed=passed,
        strict_core_failure_observed=failed,
        source_geometry_area_mass_energy_verified=True,
        N_original=N,
        A_m2=A,
        recomputed_A_fsum_m2=A_fsum,
    )


def audit():
    exp = verify_original(SHARD, CONFIG)
    require(exp == verify_original(ORIGIN, CONFIG), "origins differ")
    reg = read_json(SHARD / REGISTRATION)
    require(
        reg["experiment_sha256"]
        == file_sha256(SHARD / "experiment.json")
        == file_sha256(ORIGIN / "experiment.json")
        and reg["preflight_sha256"]
        == file_sha256(SHARD / "preflight.json")
        == file_sha256(ORIGIN / "preflight.json"),
        "changed physical archive",
    )
    require(
        reg["table_sha256"] == file_sha256(TABLE)
        and reg["candidate_comparison_sha256"] == file_sha256(MINIMUM)
        and reg["runner_sha256"] == file_sha256(ROOT / "scripts/run_heritage_pareto_witnesses.py")
        and reg["native_loader_sha256"]
        == file_sha256(ROOT / "scripts/resume_heritage_joint_schedule.py"),
        "changed allocation or decision archive",
    )
    checks = selected_cases()
    require(
        len(checks) == reg["slots"] == 4
        and list(map(list, checks)) == reg["selected_cases"]
        and reg["new_independent_fires"] == reg["imported_outcomes"] == 0,
        "different witness allocation",
    )
    budget = read_json(SHARD / "execution-budget.json")
    cap = exp["config"]
    require(
        budget["active"] is None
        and budget["started_checks"] == 4
        and reg["original_budget"]["started_checks"] + 4 <= cap["max_new_checks"]
        and reg["original_budget"]["elapsed_s"] + budget["elapsed_s"] <= cap["max_wall_time_s"],
        "allocation incomplete or total budget exceeded",
    )
    table = read_seal(TABLE)
    old = read_seal(MINIMUM)
    for f, sha in old["signed_input_file_hashes"].items():
        require(file_sha256(ROOT / f) == sha, "prior finite set evidence changed")
    costs = {r["policy"]: r["C_m2"] for r in old["observations"]}
    reduced = old["finite_unique_minimum"]
    wide = next(r["policy"] for r in old["observations"] if r["policy"].endswith("-w15.01"))
    narrow = next(r["policy"] for r in old["observations"] if r["policy"].endswith("-w2.5"))
    rejected = next(
        r["policy"]
        for r in old["observations"]
        if r["constraint_verdict"] == "first_heritage_ignition_counterexample"
    )
    require(
        table["subset_dominates_wide_in_C_Nupper_A"]
        and costs[rejected] < costs[narrow] < costs[reduced]
        and len(costs) == 6
        and all(costs[narrow] < costs[x] for x in costs if x not in (rejected, narrow)),
        "unexpected prior classifications",
    )
    threshold = old["constraints"]
    A0 = threshold["maximum_burned_out_covered_area_m2"]
    N0 = threshold["maximum_original_owner_N_upper"]
    narrow_observation = next(row for row in old["observations"] if row["policy"] == narrow)
    require(
        narrow_observation["strict_closed_full_checks"] == 8
        and (
            narrow_observation["max_A_m2"] > A0
            or narrow_observation["worst_original_N"]["upper"] > N0
        ),
        "lower-cost safe candidate might dominate the reduced action",
    )
    with tempfile.TemporaryDirectory(prefix="pingyan-design-witness-audit-") as temp:
        native = summarize(SHARD, CONFIG, Path(temp) / "report.json", temporal_mode="strict")
    cases = [r for r in native["native_checks"] if r.get("manifest")]
    require(
        len(cases) == 4 and {(r["plan_id"], r["scenario_id"]) for r in cases} == set(checks),
        "different native cases",
    )
    base, _, inv = load_study(Path(cap["study_path"]))
    original = resolve_roof_layout(
        base.request,
        inv,
        max_nodes=cap["max_nodes"],
        core_overlap_policy=cap["core_overlap_policy"],
    )
    area = original.node_inputs.parameters.covered_area_m2.ravel()
    unknown = set(inv.by_id) - {
        o for g, x in zip(original.owners, area, strict=True) if x > 0 for o in g
    }
    polygons = np.array(original.geometry, dtype=object)
    tree = shapely.STRtree(polygons)
    outcomes = [verify_native(r, exp, original, inv, unknown, polygons, tree) for r in cases]
    unresolved = []
    disposition = []
    for name in sorted({r["plan"] for r in outcomes}, key=costs.get):
        records = [r for r in outcomes if r["plan"] == name]
        valid = [r for r in records if r["strict_safe_and_closed"]]
        require(costs[name] > costs[reduced], "cost dominance absent")
        lower_A = max((r["A_m2"] for r in valid), default=None)
        lower_N = max((r["N_original"]["upper"] for r in valid), default=None)
        if any(r["strict_core_failure_observed"] for r in records):
            decision = "excluded_by_observed_heritage_ignition"
        elif valid and lower_A >= A0 and lower_N >= N0:
            decision = "cannot_be_nondominated_even_if_other_cases_are_safe"
        else:
            decision = "unresolved"
            unresolved.append(name)
        disposition.append(
            dict(
                plan=name,
                C_m2=costs[name],
                strict_safe_closed_witnesses=len(valid),
                lower_bound_on_maximum_A_m2=lower_A,
                lower_bound_on_maximum_original_N_upper=lower_N,
                decision=decision,
            )
        )
    return dict(
        schema_version="finite-design-enclosure-pareto-witness-1",
        village="pingyan",
        scope="Five enclosures and one reduced action, four original design orderings, two scales.",
        criterion=(
            "Strict heritage protection, construction C, worst burned area A, "
            "and worst upper original-owner count N."
        ),
        handling=(
            "A and N lower bounds on the maxima may come from different valid witnesses. "
            "Missing scenarios remain unobserved."
        ),
        costs_m2=costs,
        lean_benchmark=dict(C_m2=costs[reduced], A_m2=A0, N_upper=N0),
        prior_rejected_by_heritage_prefix=rejected,
        prior_dominated_by_reduced=wide,
        unique_least_C_heritage_safe=narrow,
        definitely_nondominated=[narrow, reduced],
        complete_six_candidate_frontier=not unresolved,
        unresolved_candidates=unresolved,
        intermediate_policy_decisions=disposition,
        verified_witnesses=outcomes,
        native_budget_slots_used=budget["started_checks"],
        native_active_at_audit=budget["active"],
        original_plus_shard_started_slots=reg["original_budget"]["started_checks"]
        + budget["started_checks"],
        original_frozen_slot_limit=cap["max_new_checks"],
        native_full_runs_invented=0,
        CA_checks_by_auditor=0,
        registration_sha256=file_sha256(SHARD / REGISTRATION),
        native_problem_sha256=exp["problem_sha256"],
        budget_file_sha256=file_sha256(SHARD / "execution-budget.json"),
        prior_six_set_certificate_sha256=file_sha256(MINIMUM),
        full_native_comparison_sha256=file_sha256(TABLE),
    )


if __name__ == "__main__":
    r = audit()
    r["method_sha256"] = file_sha256(__file__)
    r["receipt_sha256"] = content_hash(r)
    p = ROOT / "docs/results/heritage-pingyan-six-options-native-pareto-witness-audit-20261111.json"
    with p.open("x") as f:
        json.dump(r, f, indent=2, allow_nan=False)
        f.write("\n")
