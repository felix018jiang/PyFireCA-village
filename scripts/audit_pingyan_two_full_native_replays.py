"""Audit two registered same-grid, same-source pairs without publishing individual fires."""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs/results"
RUNS = ROOT / "runs/research"
OLD = RUNS / "heritage-pingyan-independent-three-stage-supplement-20261111"
NEW = RUNS / "heritage-pingyan-eight-design-matched-two-1m-native-20261111"
SNAP = OLD / "source-snapshot"
sys.path[:0] = [str(SNAP / "scripts"), str(SNAP / "src")]
# ruff: noqa: E402 -- both endpoint readers and solver types must use the registered native source.
import numpy as np
import run_heritage_joint_comparison as frozen
from audit_heritage_construction_and_fire_roof_loss import count as count_whole_roofs
from audit_pingyan_two_matched_original_fires import native

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def require(value, msg):
    if not value:
        raise ValueError(msg)


def seal(p):
    d = read_json(p)
    require(d.pop("receipt_sha256") == content_hash(d), "receipt changed")
    return d


def endpoint(path, problem, inv, unknown, ambient):
    folder = path.parent
    m = read_json(path)
    raw = read_json(folder / "check.json")
    ev = raw["evidence"]
    frozen.read_complete(
        folder,
        problem["problem_sha256"],
        plan_id=m["plan_id"],
        scenario_id=m["scenario_id"],
        demolished_ids=m["demolished_ids"],
        cleared_physical_cell_ids=m["cleared_physical_cell_ids"],
        terminal_required=True,
    )
    term = ev["terminal_verification"]
    loss = read_json(folder / "fire-loss.json")
    rec = read_json(folder / "evaluation.json")
    cores = rec["metrics"]["core_outcomes"]
    require(len(cores) == len(inv.targets) == 3, "heritage inventory differs")
    costs = rec["intervention"]["cleared_action_geometry_union_area_m2"]
    result = dict(
        C=costs,
        protected=len(cores),
        ignition=sum(q["ignited"] for q in cores),
        status=raw["status"],
        valid=raw["status"] == "passed"
        and term["terminal_complete"]
        and not term["invalid_reasons"],
        maximum_core_temperature_K=max(r["maximum_valid_surface_temperature_K"] for r in cores),
        maximum_core_temperature_rise_K=max(r["maximum_valid_surface_temperature_K"] for r in cores)
        - ambient,
        any_core_external_heat=any(
            r["absorbed_radiation_J"] > 0 or r["received_convection_J"] > 0 for r in cores
        ),
    )
    if not result["valid"]:
        return result, ev
    ns = loss["physical_remaining_roof_annotation_counts"][
        "burned_out_annotation_attribution_count"
    ]
    A = loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
    summary = dict(
        native_manifest_sha256=file_sha256(path),
        native_problem_sha256=problem["problem_sha256"],
        burned_physical_m2=A,
        burned_buildings_original_owner_bounds=ns,
    )
    measured, low, hi, nfiles = native(
        path, summary, problem, set(m["demolished_ids"]), inv, unknown
    )
    with np.load(folder / "final-state.npz") as state:
        t = float(state["time_s"])
        clock = state["burnout_time_s"].ravel()
        burn = np.flatnonzero(np.isfinite(clock) & (clock <= t))
    with np.load(folder / "layout.npz") as layout:
        roof = np.diff(layout["owner_offsets"]) > 0
    with np.load(folder / "loss-basis.npz") as d:
        coverage = d["post_intervention_covered_area_m2"].ravel()
        roof_A = math.fsum(coverage[burn[roof[burn]]])
        ground_A = math.fsum(coverage[burn[~roof[burn]]])
    require(
        abs(measured - roof_A - ground_A) < 1e-7
        and abs(roof_A - loss["physical"]["roof"]["cumulative_burned_out_covered_area_m2"]) < 1e-7
        and abs(ground_A - loss["physical"]["nonroof"]["cumulative_burned_out_covered_area_m2"])
        < 1e-7,
        "burned roof/ground ledger differs",
    )
    result.update(
        A_m2=measured,
        roof_A_m2=roof_A,
        ground_A_m2=ground_A,
        N_lower=low,
        N_upper=hi,
        artifact_count=nfiles,
        **count_whole_roofs(path),
    )
    return result, ev


def aggregate(rows):
    eligible = all(r["valid"] for r in rows)
    d = dict(
        closed_strict_heritage_safe=sum(r["valid"] for r in rows),
        protected_inventory_per_case=3,
        heritage_ignitions=sum(r["ignition"] for r in rows),
        cases_with_positive_core_heat=sum(r["any_core_external_heat"] for r in rows),
        maximum_core_K=max(r["maximum_core_temperature_K"] for r in rows),
        maximum_core_temperature_rise_K=max(r["maximum_core_temperature_rise_K"] for r in rows),
        construction_C_m2=rows[0]["C"],
    )
    require(
        all(abs(r["C"] - d["construction_C_m2"]) < 1e-6 for r in rows), "inconsistent construction"
    )
    if eligible:
        d.update(
            worst_whole_domain_burned_A_m2=max(r["A_m2"] for r in rows),
            worst_roof_burned_A_m2=max(r["roof_A_m2"] for r in rows),
            worst_ground_burned_A_m2=max(r["ground_A_m2"] for r in rows),
            whole_original_roofs_lost_to_construction=max(r["whole_cleared"] for r in rows),
            worst_additional_whole_original_roofs_lost_to_fire=max(
                r["additional_whole_lost_after_fire"] for r in rows
            ),
            worst_total_whole_original_roofs_lost_including_construction=max(
                r["total_whole_lost"] for r in rows
            ),
            worst_burned_original_N=[
                max(r["N_lower"] for r in rows),
                max(r["N_upper"] for r in rows),
            ],
            native_artifact_hashes_verified=sum(r["artifact_count"] for r in rows),
        )
    return d


def audit():
    old = read_json(OLD / "experiment.json")
    new = read_json(NEW / "experiment.json")
    prep_path = RES / "heritage-pingyan-eight-design-matched-two-1m-preflight-20261111.json"
    prior_path = RES / "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json"
    budget_path = NEW / "execution-budget.json"
    pre = seal(prep_path)
    prior = seal(prior_path)
    require(
        pre["frozen_physics_experiment_sha256"] == file_sha256(OLD / "experiment.json")
        and pre["local_batch_experiment_sha256"] == file_sha256(NEW / "experiment.json")
        and new["problem_sha256"] == content_hash(new["problem"]),
        "physics or action declaration changed",
    )
    budget = read_json(budget_path)
    require(
        budget["active"] is None
        and set(budget["charged"]) == set(new["execution_order"])
        and len(budget["charged"]) == budget["slots"] == 2,
        "native batch not finished",
    )
    committed = {r["original_manifest_sha256"] for r in pre["geometry_rows"]}
    olds = {}
    for p in OLD.glob("**/evaluations/*/complete.json"):
        if file_sha256(p) in committed:
            olds[read_json(p)["scenario_id"]] = p
    news = {read_json(p)["scenario_id"]: p for p in NEW.glob("evaluations/*/complete.json")}
    require(
        set(olds) == set(news) == set(new["execution_order"])
        and len(olds) == 2
        and content_hash(sorted(committed)) == prior["matched_manifest_commitment"],
        "different original matches",
    )
    cfg = old["config"]
    bundle, _, inv = load_study(Path(cfg["study_path"]))
    parent = resolve_roof_layout(
        bundle.request,
        inv,
        max_nodes=cfg["max_nodes"],
        core_overlap_policy=cfg["core_overlap_policy"],
    )
    areas = parent.node_inputs.parameters.covered_area_m2.ravel()
    unknown = set(inv.by_id) - {
        o for g, a in zip(parent.owners, areas, strict=True) if a > 0 for o in g
    }
    ambient = bundle.request.scenario.ambient_temperature_K
    before = []
    after = []
    shared_arrays = 0
    for sc in sorted(olds):
        A, x = endpoint(olds[sc], old, inv, unknown, ambient)
        B, y = endpoint(news[sc], new, inv, unknown, ambient)
        fields = (
            "source_id",
            "source_footprint_sha256",
            "common_layout_sha256",
            "frozen_integrated_node_mask_sha256",
            "scale_m",
        )
        require(
            x["scale_m"] == 1
            and all(x[k] == y[k] for k in fields)
            and all(
                np.isclose(v, y["source_budget"][k], atol=1e-7, rtol=1e-9)
                for k, v in x["source_budget"].items()
            ),
            "different source, physical fuel, layout or integrated mask",
        )
        with (
            np.load(olds[sc].with_name("layout.npz")) as oldL,
            np.load(news[sc].with_name("layout.npz")) as newL,
        ):
            for key in ("physical_cell_id", "xy_m", "component_id", "area_fraction"):
                require(np.array_equal(oldL[key], newL[key]), "mesh atoms changed")
                shared_arrays += 1
        before.append(A)
        after.append(B)
    previous = aggregate(before)
    present = aggregate(after)
    require(
        previous["worst_burned_original_N"] == prior["old_1m_aggregate_worst_original_N_interval"]
        and abs(
            previous["worst_whole_domain_burned_A_m2"] - prior["old_1m_aggregate_max_burned_m2"]
        )
        < 1e-7,
        "prior comparison differs",
    )
    d = dict(
        schema_version="known-design-matched-1m-full-native-pairs-1",
        village="pingyan",
        exact_physical_pairs=2,
        source_area_per_case_m2=25.0,
        all_footprints_initial_masses_and_energies_matched=True,
        all_common_mesh_and_integration_hashes_matched=True,
        direct_immutable_atom_arrays_verified=shared_arrays,
        reference_ambient_K=ambient,
        old=previous,
        new=present,
        corresponding_two_family_theoretical_A_upper_m2=prior["new_matched_analytic_A_upper_m2"],
        corresponding_two_family_theoretical_original_N_upper=prior[
            "new_matched_analytic_original_N_upper"
        ],
        separate_new_native_slots=2,
        total_new_1m_cases=2,
        new_fine_scale_runs=0,
        new_independent_draws=0,
        cohort=(
            "Two same-physical-source families among eight disclosed design inputs, with "
            "one native coarse support per case and policy."
        ),
        areas=(
            "Each reported metric is maximized separately over two cases. A is burned-out "
            "physical coverage. N uses original roof annotation attribution."
        ),
        inputs_sha256={
            str(p.relative_to(ROOT)): file_sha256(p)
            for p in [
                prep_path,
                prior_path,
                budget_path,
                OLD / "experiment.json",
                NEW / "experiment.json",
                NEW / "summary.json",
                ROOT / "scripts/audit_heritage_construction_and_fire_roof_loss.py",
                ROOT / "scripts/audit_pingyan_two_matched_original_fires.py",
            ]
        },
        new_manifest_commitment=content_hash(sorted(file_sha256(p) for p in news.values())),
        old_paired_manifest_commitment=content_hash(sorted(committed)),
    )
    if present["closed_strict_heritage_safe"] == 2:
        d.update(
            cases_within_two_source_theoretical_A_and_N=all(
                r["A_m2"] <= d["corresponding_two_family_theoretical_A_upper_m2"]
                and r["N_upper"] <= d["corresponding_two_family_theoretical_original_N_upper"]
                for r in after
            ),
            observed_difference_in_pair_worst_whole_A_m2=previous["worst_whole_domain_burned_A_m2"]
            - present["worst_whole_domain_burned_A_m2"],
            observed_difference_in_pair_worst_N_upper=previous["worst_burned_original_N"][1]
            - present["worst_burned_original_N"][1],
            observed_difference_in_pair_worst_total_whole_roofs_lost_including_construction=(
                previous["worst_total_whole_original_roofs_lost_including_construction"]
                - present["worst_total_whole_original_roofs_lost_including_construction"]
            ),
        )
    return d


if __name__ == "__main__":
    d = audit()
    d["method_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    out = RES / "heritage-pingyan-two-matched-1m-full-native-pair-audit-20261111.json"
    with out.open("x") as f:
        json.dump(d, f, indent=2, allow_nan=False)
        f.write("\n")
