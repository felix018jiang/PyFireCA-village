"""Incrementally audit B native factorial observations without running any CA."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from functools import cache
from itertools import combinations, pairwise
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from prepare_heritage_direct_action_ablation import EXPECTED_SOURCES, action_cost
from recount_heritage_area_archives import enrich_saved_loss
from run_heritage_joint_comparison import read_complete

from pyfireca.planning.fire_loss import recover_original_node_owners
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
RUNS = (
    "heritage-thin-hull-batuan-20261111",
    "heritage-direct-action-ablation-batuan-20261111",
    "heritage-joint-longcap-batuan-20261111",
)


def audit_case(folder, experiment, inventory, parent, cost):
    manifest = read_json(folder / "complete.json")
    check = read_complete(
        folder,
        experiment["problem_sha256"],
        plan_id=manifest["plan_id"],
        scenario_id=manifest["scenario_id"],
        demolished_ids=manifest["demolished_ids"],
        cleared_physical_cell_ids=manifest["cleared_physical_cell_ids"],
        terminal_required=True,
    )
    record, loss, reconstruction = enrich_saved_loss(folder, inventory, parent, manifest)
    evidence, diagnostics = check["evidence"], record["diagnostics"]
    fire = evidence["fire_id"]
    source = EXPECTED_SOURCES[fire]
    if (
        evidence["source_id"] != source
        or record["metrics"]["actual_source_ids"] != [source]
        or diagnostics["source_verified"] is not True
        or diagnostics["parent_case_unchanged"] is not True
    ):
        raise ValueError("actual source identity or physical source verification differs")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        offsets, ids = saved["owner_offsets"], saved["owner_ids"]
        layout = SimpleNamespace(
            owners=tuple(tuple(ids[a:b]) for a, b in pairwise(offsets)),
            component_id=saved["component_id"],
            cell_id=saved["physical_cell_id"],
            xy_m=saved["xy_m"],
        )
        owners, _ = recover_original_node_owners(
            layout, inventory, demolished_ids=manifest["demolished_ids"]
        )
    with np.load(folder / "loss-basis.npz", allow_pickle=False) as saved:
        area = saved["baseline_covered_area_m2"].ravel()
        post = saved["post_intervention_covered_area_m2"].ravel()
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        time = float(saved["time_s"])
        ignition = saved["ignition_time_s"].ravel()
        cause = saved["ignition_cause"].ravel()
    source_nodes = np.flatnonzero(ignition == 0)
    if (
        not len(source_nodes)
        or any(owners[n] != (source,) for n in source_nodes)
        or np.any(post[source_nodes] != area[source_nodes])
        or not np.array_equal(cause == 1, ignition == 0)
        or abs(float(area[source_nodes].sum()) - 25) > 1e-6
    ):
        raise ValueError("raw prescribed ignition is missing, cleared or outside its source")
    temperatures = np.asarray(diagnostics["maximum_valid_surface_temperature_by_node_K"]).ravel()
    core = {}
    for target in inventory.targets:
        nodes = np.array([target.building_id in group for group in owners], bool)
        times = ignition[nodes & np.isfinite(ignition) & (ignition <= time)]
        peak = float(temperatures[nodes].max())
        raw = next(
            r for r in record["metrics"]["core_outcomes"] if r["building_id"] == target.building_id
        )
        if (
            not nodes.any()
            or np.any(post[nodes] != area[nodes])
            or bool(len(times)) != raw["ignited"]
            or peak != raw["maximum_valid_surface_temperature_K"]
        ):
            raise ValueError("core ignition, protected roof or historical temperature differs")
        core[target.building_id] = {
            "ignited": bool(len(times)),
            "first_ignition_s": float(times.min()) if len(times) else None,
            "peak_K": peak,
        }
    terminal = evidence["terminal_verification"]
    counts = loss["physical_remaining_roof_annotation_counts"]
    native_loss = read_json(folder / "fire-loss.json")
    if native_loss.get("physical_remaining_roof_annotation_counts", counts) != counts:
        raise ValueError("native original-owner N differs from independent event recount")
    raw_C = record["intervention"]["cleared_action_geometry_union_area_m2"]
    if abs(raw_C - cost["C_construction_action_area_m2"]) > 1e-6:
        raise ValueError("native C differs from independent roof AND geometry plus whole cells")
    closed = terminal["terminal_complete"] and terminal["fire_loss_complete"]
    core_ignited = [identifier for identifier, value in core.items() if value["ignited"]]
    physically_valid = evidence["invalid_reasons"] == diagnostics["invalid_reasons"] == []
    strict_passed = closed and physically_valid and check["status"] == "passed" and not core_ignited
    if closed and loss["physical"]["all"]["active_burning_covered_area_m2"] != 0:
        raise ValueError("strict closure retains physically active fire")
    return {
        "folder": str(folder),
        "manifest_sha256": file_sha256(folder / "complete.json"),
        "artifact_hashes_verified": len(manifest["files_sha256"]),
        "problem_sha256": experiment["problem_sha256"],
        "plan_id": manifest["plan_id"],
        "scenario_id": manifest["scenario_id"],
        "fire_id": fire,
        "scale_m": evidence["scale_m"],
        "actual_source_id": source,
        "raw_source_ignition_area_m2": float(area[source_nodes].sum()),
        "source_budget": evidence["source_budget"],
        "source_footprint_sha256": evidence["source_footprint_sha256"],
        "common_layout_sha256": evidence["common_layout_sha256"],
        "frozen_integrated_node_mask_sha256": evidence["frozen_integrated_node_mask_sha256"],
        "raw_status": check["status"],
        "raw_invalid_reasons": evidence["invalid_reasons"],
        "raw_reached_horizon": evidence["reached_horizon"],
        "terminal_complete": bool(closed),
        "raw_terminal_observation": terminal["terminal_observation"],
        "whole_fire_loss_complete": terminal["fire_loss_complete"],
        "N_A_are_partial_observations_when_not_complete": not closed,
        "strict_passed": bool(strict_passed),
        "core": core,
        "actual_stop_time_s": time,
        "closure_certificate_sha256": (terminal.get("temporal_closure_certificate") or {}).get(
            "certificate_sha256"
        ),
        "C_construction_action_area_m2": cost["C_construction_action_area_m2"],
        "C_native_geometry_area_m2": raw_C,
        "N_burned_out_bounds": counts["burned_out_annotation_attribution_count"],
        "N_ignited_bounds": counts["ignited_annotation_attribution_count"],
        "A_burned_out_m2": loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"],
        "A_ignited_m2": loss["physical"]["all"]["cumulative_ignited_covered_area_m2"],
        "A_nonroof_burned_out_m2": loss["physical"]["nonroof"][
            "cumulative_burned_out_covered_area_m2"
        ],
        "active_burning_area_m2": loss["physical"]["all"]["active_burning_covered_area_m2"],
        "original_owner_reconstruction": reconstruction,
        "native_artifacts_modified": False,
    }


@cache
def frozen_geometry(study, max_nodes, overlap):
    bundle, _, inventory = load_study(Path(study))
    base = resolve_roof_layout(
        bundle.request, inventory, max_nodes=max_nodes, core_overlap_policy=overlap
    )
    return bundle, inventory, base


def compare_complete_to_balanced(runs):
    """Require eight matched same-source cases before a worst-case C/N/A claim."""
    path = ROOT / "docs/results/heritage-balanced-outcomes-audit-20261111.json"
    baseline = read_json(path)["balanced_terminal_audit"]
    reference_plan = "roof-seed-core-source-hull-ring-b0-w15.01"
    summary = next(p for p in baseline["plan_summaries"] if p["plan_id"] == reference_plan)
    original = {
        r["scenario_id"]: r for r in baseline["scenario_rows"] if r["plan_id"] == reference_plan
    }
    native = {}
    for scenario, row in original.items():
        manifest_path = Path(row["manifest_path"])
        if file_sha256(manifest_path) != row["manifest_sha256"]:
            raise ValueError("the completed 4775 reference native manifest changed")
        manifest = read_json(manifest_path)
        for name, digest in manifest["files_sha256"].items():
            if file_sha256(manifest_path.parent / name) != digest:
                raise ValueError("the completed 4775 reference native artifact changed")
        native[scenario] = read_json(manifest_path.parent / "check.json")["evidence"]
    results = []
    for run in runs:
        for candidate in run["plan_summaries"]:
            if not candidate["complete8_strict_passed_and_closed"]:
                continue
            rows = [r for r in run["scenario_rows"] if r["plan_id"] == candidate["plan_id"]]
            for row in rows:
                paired = native[row["scenario_id"]]
                for key in (
                    "source_footprint_sha256",
                    "common_layout_sha256",
                    "frozen_integrated_node_mask_sha256",
                    "source_budget",
                ):
                    if row[key] != paired[key]:
                        raise ValueError("a full comparison changes same-scenario source or mesh")
                if row["actual_source_id"] != paired["source_id"]:
                    raise ValueError("a full comparison uses a different actual source")
            left = [
                candidate["C_m2"],
                candidate["worst_N_burned_out_bounds"]["upper"],
                candidate["worst_A_burned_out_m2"],
            ]
            right = [
                summary["C_m2"],
                summary["worst_N_burned_out_bounds"]["upper"],
                summary["worst_A_burned_out_m2"],
            ]
            results.append(
                {
                    "plan_id": candidate["plan_id"],
                    "reference_plan_id": reference_plan,
                    "all8_same_actual_source_mesh_budget_verified": True,
                    "candidate_worst_C_Nupper_A": left,
                    "reference_worst_C_Nupper_A": right,
                    "candidate_strictly_dominates_reference": all(
                        a <= b for a, b in zip(left, right, strict=True)
                    )
                    and any(a < b for a, b in zip(left, right, strict=True)),
                    "scope": "completed_declared8_scenario_worst_objectives;not_global_optimality",
                }
            )
    return results


def pairwise_complete_actions(runs):
    complete = [
        (run, plan)
        for run in runs
        for plan in run["plan_summaries"]
        if plan["complete8_strict_passed_and_closed"]
    ]
    results = []
    for (left_run, left), (right_run, right) in combinations(complete, 2):
        left_rows = {
            r["scenario_id"]: r
            for r in left_run["scenario_rows"]
            if r["plan_id"] == left["plan_id"]
        }
        right_rows = {
            r["scenario_id"]: r
            for r in right_run["scenario_rows"]
            if r["plan_id"] == right["plan_id"]
        }
        same_losses = True
        for scenario, a in left_rows.items():
            b = right_rows[scenario]
            for key in (
                "actual_source_id",
                "source_budget",
                "source_footprint_sha256",
                "common_layout_sha256",
                "frozen_integrated_node_mask_sha256",
            ):
                if a[key] != b[key]:
                    raise ValueError(
                        "full8 factorial pairing changes actual source, budget or mesh"
                    )
            same_losses &= (
                a["N_burned_out_bounds"] == b["N_burned_out_bounds"]
                and abs(a["A_burned_out_m2"] - b["A_burned_out_m2"]) <= 1e-8
            )
        lv = [
            left["C_m2"],
            left["worst_N_burned_out_bounds"]["upper"],
            left["worst_A_burned_out_m2"],
        ]
        rv = [
            right["C_m2"],
            right["worst_N_burned_out_bounds"]["upper"],
            right["worst_A_burned_out_m2"],
        ]
        results.append(
            {
                "left_plan_id": left["plan_id"],
                "right_plan_id": right["plan_id"],
                "all8_same_actual_source_mesh_budget_verified": True,
                "paired_Nbounds_equal_and_A_equal_within_1e_minus8_m2": bool(same_losses),
                "left_worst_C_Nupper_A": lv,
                "right_worst_C_Nupper_A": rv,
                "left_strictly_dominates_right": all(a <= b for a, b in zip(lv, rv, strict=True))
                and any(a < b for a, b in zip(lv, rv, strict=True)),
                "right_strictly_dominates_left": all(b <= a for a, b in zip(lv, rv, strict=True))
                and any(b < a for a, b in zip(lv, rv, strict=True)),
            }
        )
    return results


def audit(output):
    old = read_json(output) if output.exists() else {}
    cached = {r["folder"]: r for run in old.get("runs", []) for r in run["scenario_rows"]}
    experiments = [
        (
            ROOT / "runs/research" / name,
            read_json(ROOT / "runs/research" / name / "experiment.json"),
        )
        for name in RUNS
    ]
    config = experiments[0][1]["config"]
    bundle, inventory, base = frozen_geometry(
        config["study_path"], config["max_nodes"], config.get("core_overlap_policy", "reject")
    )
    protected = {t.building_id for t in inventory.targets} | set(EXPECTED_SOURCES.values())
    runs, errors, fresh = [], [], []
    for path, experiment in experiments:
        if content_hash(experiment["problem"]) != experiment["problem_sha256"]:
            raise ValueError("frozen experiment problem SHA differs")
        for relative, digest in experiment["source_sha256"].items():
            if file_sha256(path / "source-snapshot" / relative) != digest:
                raise ValueError("frozen source differs: " + relative)
        for reference, digest in experiment["references_sha256"].items():
            if file_sha256(reference) != digest:
                raise ValueError("frozen reference differs: " + reference)
        plans = {p["plan_id"]: p for p in experiment["config"]["plans"]}
        if path.name == RUNS[2]:
            plans = {"roof-seed": plans["roof-seed"]}
        costs = {
            k: action_cost(bundle.request, base, inventory, p, protected) for k, p in plans.items()
        }
        scenarios = {
            f"{fire['fire_id']}-h{scale:g}"
            for fire in experiment["config"]["source_fires"]
            for scale in experiment["config"]["roof_scales_m"]
        }
        rows = []
        for manifest_path in sorted(path.glob("evaluations/*/complete.json")):
            manifest = read_json(manifest_path)
            if manifest["plan_id"] not in plans:
                continue
            folder = manifest_path.parent
            try:
                declared = plans[manifest["plan_id"]]
                if (
                    manifest["problem_sha256"] != experiment["problem_sha256"]
                    or manifest["demolished_ids"] != declared["demolished_ids"]
                    or manifest["cleared_physical_cell_ids"]
                    != declared["cleared_physical_cell_ids"]
                    or manifest["scenario_id"] not in scenarios
                ):
                    raise ValueError("native plan/action/scenario is outside the immutable config")
                prior = cached.get(str(folder))
                if prior is not None:
                    if file_sha256(manifest_path) != prior["manifest_sha256"]:
                        raise ValueError("previously audited immutable native manifest changed")
                    for name, digest in manifest["files_sha256"].items():
                        if file_sha256(folder / name) != digest:
                            raise ValueError("previously audited immutable native artifact changed")
                    row = prior
                    terminal = read_json(folder / "check.json")["evidence"]["terminal_verification"]
                    row.update(
                        raw_terminal_observation=terminal["terminal_observation"],
                        whole_fire_loss_complete=terminal["fire_loss_complete"],
                        N_A_are_partial_observations_when_not_complete=not terminal[
                            "terminal_complete"
                        ],
                    )
                else:
                    row = audit_case(
                        folder, experiment, inventory, bundle.request, costs[manifest["plan_id"]]
                    )
                    fresh.append(row)
                rows.append(row)
            except (KeyError, ValueError, OSError) as error:
                errors.append({"folder": str(folder), "error": str(error)})
        summaries = []
        for plan in plans:
            values = [r for r in rows if r["plan_id"] == plan]
            complete = (
                len(values) == 8
                and {r["scenario_id"] for r in values} == scenarios
                and all(r["strict_passed"] for r in values)
            )
            summaries.append(
                {
                    "plan_id": plan,
                    "native_cases_audited": len(values),
                    "strict_passed_cases": sum(r["strict_passed"] for r in values),
                    "complete8_strict_passed_and_closed": complete,
                    "C_m2": costs[plan]["C_construction_action_area_m2"],
                    "worst_N_burned_out_bounds": {
                        k: max((r["N_burned_out_bounds"][k] for r in values), default=None)
                        for k in ("lower", "upper")
                    },
                    "worst_A_burned_out_m2": max(
                        (r["A_burned_out_m2"] for r in values), default=None
                    ),
                    "worst_metrics_are_partial_until_complete8": not complete,
                }
            )
        runs.append(
            {
                "run": str(path),
                "experiment_sha256": file_sha256(path / "experiment.json"),
                "problem_sha256": experiment["problem_sha256"],
                "source_entries_verified": len(experiment["source_sha256"]),
                "reference_entries_verified": len(experiment["references_sha256"]),
                "budget_raw": read_json(path / "execution-budget.json"),
                "plan_summaries": summaries,
                "scenario_rows": rows,
            }
        )
    result = {
        "schema_version": "heritage-batuan-factorial-incremental-native-audit-1",
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "new_CA_checks": 0,
        "native_artifacts_modified": False,
        "all_four_same_actual_sources_required": EXPECTED_SOURCES,
        "runs": runs,
        "errors": errors,
        "full8_comparisons_to_completed_4775_reference": compare_complete_to_balanced(runs),
        "full8_pairwise_action_comparisons": pairwise_complete_actions(runs),
        "N_scope": "original_physical_owner_attribution_bounds;not_real_destroyed_buildings",
        "partial_observations_do_not_establish_robust_feasibility_or_dominance": True,
    }
    temporary = output.with_suffix(".pending.json")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(output)
    return result, fresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--polls", type=int, default=1)
    parser.add_argument("--interval-s", type=float, default=40)
    args = parser.parse_args()
    if not 1 <= args.polls <= 60 or not 1 <= args.interval_s <= 50:
        parser.error("bounded monitoring requires 1..60 polls and 1..50-second intervals")
    for index in range(args.polls):
        result, fresh = audit(args.output.resolve())
        if fresh or result["errors"]:
            print(
                json.dumps(
                    {
                        "newly_audited": [
                            {
                                k: r[k]
                                for k in (
                                    "plan_id",
                                    "scenario_id",
                                    "raw_status",
                                    "terminal_complete",
                                    "N_burned_out_bounds",
                                    "A_burned_out_m2",
                                    "core",
                                )
                            }
                            for r in fresh
                        ],
                        "errors": result["errors"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if index + 1 < args.polls:
            time.sleep(args.interval_s)


if __name__ == "__main__":
    main()
