"""Prioritize a registered proposal using the unchanged frozen joint manager.

Only a private run_master_loop copy receives reordered seeds and, if explicitly
requested, deferred baseline prefetch. Every candidate retains all scenarios.
All physical loading, native validation/persistence, cuts, statuses and budgets
remain those of the original source snapshot. No proposal supplies evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path


def load_frozen_joint_manager(run):
    if any(name == "pyfireca" or name.startswith("pyfireca.") for name in sys.modules):
        raise ValueError("start a fresh process;physical imports must come only from the snapshot")
    helpers = {
        "run_heritage_area_master",
        "run_heritage_area_study",
        "run_heritage_fixed_comparison",
        "run_heritage_roof_study",
        "preflight_heritage_exact_expansion",
    }
    snapshot = run / "source-snapshot"
    if any(name in sys.modules for name in helpers):
        raise ValueError("start a fresh process without preloaded project helpers")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "src"), str(snapshot / "scripts")]
    script = snapshot / "scripts/run_heritage_joint_master.py"
    spec = importlib.util.spec_from_file_location("frozen_joint_seed_schedule_manager", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name, imported in tuple(sys.modules.items()):
        if name == "pyfireca" or name.startswith("pyfireca.") or name in helpers:
            path = getattr(imported, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(snapshot):
                raise ValueError("a physical module or helper escaped the immutable snapshot")
    return module


def prioritized_seeds(loop_config, actions, *, baseline_prefetch="declared"):
    """Preserve constraints and old seed order; optionally defer baseline work."""
    if baseline_prefetch not in {"declared", "defer"}:
        raise ValueError("baseline prefetch policy must be declared or defer")
    original = deepcopy(loop_config)
    target = tuple(actions)
    old = []
    for row in loop_config.get("seed_plans", []):
        ids = row["demolished_ids"] if isinstance(row, dict) else row
        canonical = tuple(sorted(ids))
        if canonical != target:
            old.append(deepcopy(row))
    result = {**loop_config, "seed_plans": [{"demolished_ids": list(actions)}, *old]}
    allowed = {"seed_plans"}
    if baseline_prefetch == "defer":
        result["prefetch_baseline_witnesses"] = False
        allowed.add("prefetch_baseline_witnesses")
    if {k: v for k, v in result.items() if k not in allowed} != {
        k: v for k, v in original.items() if k not in allowed
    } or loop_config != original:
        raise ValueError("seed scheduling changed a nonseed configuration field")
    return result


def inspect_proposal(frozen, run, config_path, proposal_path, plan_id):
    experiment, config = frozen.read_json(run / "experiment.json"), frozen.read_json(config_path)
    frozen.validate_config(config)
    if (
        frozen.content_hash(experiment["problem"]) != experiment["problem_sha256"]
        or config != experiment["config"]
        or frozen.file_sha256(config_path) != experiment["config_sha256"]
    ):
        raise ValueError("original frozen problem/configuration identity changed")
    paths = frozen.source_paths()
    hashes = {str(p.relative_to(frozen.ROOT)): frozen.file_sha256(p) for p in paths}
    if hashes != experiment["source_sha256"] or any(
        frozen.file_sha256(p) != digest for p, digest in experiment["references_sha256"].items()
    ):
        raise ValueError("original frozen source or physical input changed")
    if not frozen.read_json(run / "preflight.json")["passed"]:
        raise ValueError("original joint geometry preflight must have passed")
    if list((run / "evaluations").glob("*.pending")):
        raise ValueError("a pending result needs inspection before a seed continuation")
    oracle, _ = frozen.make_oracle(config_path, config, frozen.content_hash(hashes))
    if (
        oracle.problem_sha256 != experiment["problem_sha256"]
        or oracle.native_problem_sha256 != experiment["native_problem_sha256"]
        or frozen.content_hash(oracle.native_problem)
        != frozen.content_hash(experiment["native_problem"])
        or frozen.content_hash(oracle.boundary_scope_problem)
        != frozen.content_hash(experiment["boundary_scope_problem"])
        or oracle.terminal_validation_scope != experiment["terminal_validation_scope"]
    ):
        raise ValueError("original native/conditional physical problem did not reconstruct")
    oracle.expected_partitions = experiment["scenario_partitions"]
    proposal_hash = frozen.file_sha256(proposal_path)
    catalog = frozen.read_json(proposal_path)
    rows = [row for row in catalog["plans"] if row["plan_id"] == plan_id]
    if len(rows) != 1:
        raise ValueError("exactly one registered proposal plan is required")
    if catalog.get("village_id", config.get("village_id")) != config.get("village_id"):
        raise ValueError("proposal belongs to another village")
    if catalog.get("reference_master_problem_sha256") is not None:
        phase = frozen.read_json(config["reference_phase_master_experiment_path"])
        if catalog["reference_master_problem_sha256"] != phase["problem_sha256"]:
            raise ValueError("proposal refers to another source/mesh preparation")
    proposal = rows[0]
    roofs, ground = proposal["demolished_ids"], proposal["cleared_physical_cell_ids"]
    if (
        roofs != sorted(set(roofs))
        or ground != sorted(set(ground))
        or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in ground)
    ):
        raise ValueError("registered D/G actions must be canonical and unique")
    actions = oracle.logical_universe.canonical_actions([*roofs, *(f"G{i}" for i in ground)])
    physical = oracle.logical_universe.plan(actions)
    if (
        tuple(roofs) != physical.demolished_ids
        or tuple(ground) != physical.cleared_physical_cell_ids
    ):
        raise ValueError("joint action projection differs from the registered proposal")
    source_matrix = {}
    for label, proof in oracle.source_coverages.items():
        source = proof.validate_actions(actions)["source_id"]
        if source is None:
            raise ValueError("the proposal leaves no source in a declared family")
        footprint = oracle.fires[label]["footprints"][source]
        source_matrix[label] = {
            "source_id": source,
            "footprint_sha256": footprint.sha256,
            "footprint_geometry_area_m2": float(footprint.geometry.area),
        }
    area = oracle.logical_universe.area(actions)
    cap = frozen.controller.source_area_cap(config)
    if cap is not None and area > cap:
        raise ValueError("the proposed joint plan exceeds the original source-area cap")
    cache_manifests, statuses = {}, {}
    for folder in sorted((run / "evaluations").glob("*")):
        if not (folder / "complete.json").is_file():
            continue
        manifest = frozen.read_json(folder / "complete.json")
        ids, scenario = tuple(manifest["action_ids"]), manifest["scenario_id"]
        if folder != frozen.folder_for(run, ids, scenario):
            raise ValueError("cached result folder differs from its canonical identity")
        check = frozen.read_complete(folder, oracle, ids, scenario)
        cache_manifests[folder.name] = frozen.file_sha256(folder / "complete.json")
        statuses[check.status] = statuses.get(check.status, 0) + 1
    spent = frozen.read_json(run / "execution-budget.json")
    if spent["started_checks"] < len(cache_manifests):
        raise ValueError("cumulative started slots are smaller than completed receipts")
    details = {
        "problem_sha256": oracle.problem_sha256,
        "native_problem_sha256": oracle.native_problem_sha256,
        "terminal_validation_scope": oracle.terminal_validation_scope,
        "proposal_path": str(proposal_path),
        "proposal_file_sha256": proposal_hash,
        "proposal_plan_id": plan_id,
        "proposal_action_ids": list(actions),
        "demolished_ids": list(physical.demolished_ids),
        "cleared_physical_cell_ids": list(physical.cleared_physical_cell_ids),
        "proposal_construction_C_m2": area,
        "admissible_total_C_cap_m2": cap,
        "proposal_is_verified_feasible_upper_bound": False,
        "registered_action_universe_sha256": oracle.logical_universe.sha256,
        "source_matrix": source_matrix,
        "scenario_order": list(oracle.scenarios),
        "baseline_prefetch_unchanged": config.get("prefetch_baseline_witnesses", False),
        "cached_status_counts": statuses,
        "cached_manifest_sha256": cache_manifests,
        "spent_before": spent,
        "original_experiment_sha256": frozen.file_sha256(run / "experiment.json"),
        "original_config_sha256": experiment["config_sha256"],
        "source_manifest_sha256": frozen.content_hash(hashes),
        "scheduler_source_sha256": frozen.file_sha256(Path(__file__)),
        "new_CA_checks_in_inspection": 0,
    }
    if frozen.file_sha256(proposal_path) != proposal_hash:
        raise ValueError("proposal bytes changed during inspection")
    return details


def continue_search(
    run, config_path, proposal_path, plan_id, *, inspect_only=False, baseline_prefetch="declared"
):
    run, config_path, proposal_path = map(
        lambda p: Path(p).resolve(), (run, config_path, proposal_path)
    )
    frozen = load_frozen_joint_manager(run)
    details = inspect_proposal(frozen, run, config_path, proposal_path, plan_id)
    if baseline_prefetch not in {"declared", "defer"}:
        raise ValueError("baseline prefetch policy must be declared or defer")
    declared = details["baseline_prefetch_unchanged"]
    effective_prefetch = False if baseline_prefetch == "defer" else declared
    details.update(
        baseline_prefetch_policy=baseline_prefetch,
        baseline_prefetch_declared=declared,
        baseline_prefetch_effective=effective_prefetch,
        baseline_prefetch_unchanged=(declared == effective_prefetch),
        no_scenario_dropped=True,
        unobserved_baseline_not_assumed_safe=True,
        candidate_feasibility_still_requires_all_declared_scenarios=True,
    )
    if inspect_only:
        return details
    receipt_dir = run / "schedules" / f"seed-priority-{time.time_ns()}"
    receipt_dir.mkdir(parents=True, exist_ok=False)
    wrapper_path = receipt_dir / Path(__file__).name
    proposal_copy = receipt_dir / "proposal-input.json"
    own_hash = frozen.file_sha256(Path(__file__))
    shutil.copy2(__file__, wrapper_path)
    shutil.copy2(proposal_path, proposal_copy)
    if (
        frozen.file_sha256(wrapper_path) != own_hash
        or frozen.file_sha256(proposal_copy) != details["proposal_file_sha256"]
    ):
        raise ValueError("scheduler or proposal changed while freezing the receipt")
    receipt = {
        "schema_version": "heritage-joint-seed-priority-schedule-1",
        **details,
        "scheduler_sha256": own_hash,
        "frozen_proposal_copy": str(proposal_copy),
        "scope": "private_seed_priority_and_explicit_baseline_prefetch_timing_only",
        "original_config_or_snapshot_modified": False,
        "native_evidence_injected": False,
        "budget_reset_or_limits_changed": False,
        "baseline_or_scenario_order_changed": declared != effective_prefetch,
        "scenario_order_changed": False,
    }
    frozen.atomic_json(receipt_dir / "receipt.json", receipt)
    original_loop = frozen.controller.run_master_loop
    original_loop_config = None

    def hook(loop_config, **kwargs):
        nonlocal original_loop_config
        if original_loop_config is not None:
            raise ValueError("the frozen manager invoked its master loop more than once")
        if (
            kwargs["problem_sha256"] != details["problem_sha256"]
            or list(kwargs["scenario_ids"]) != details["scenario_order"]
        ):
            raise ValueError("frozen master problem or scenario order changed before scheduling")
        if kwargs.get("status_checker") is not frozen.checked_status:
            raise ValueError("the original typed status checker was replaced")
        if frozen.file_sha256(proposal_path) != details["proposal_file_sha256"]:
            raise ValueError("registered proposal changed before the original loop")
        original_loop_config = deepcopy(loop_config)
        effective = prioritized_seeds(
            loop_config, details["proposal_action_ids"], baseline_prefetch=baseline_prefetch
        )
        frozen.atomic_json(
            receipt_dir / "effective-seeds.json",
            {
                "problem_sha256": details["problem_sha256"],
                "original_seed_plans": original_loop_config["seed_plans"],
                "effective_seed_plans": effective["seed_plans"],
                "all_nonseed_fields_unchanged": declared == effective_prefetch,
                "baseline_prefetch_declared": declared,
                "baseline_prefetch_effective": effective_prefetch,
                "all_physical_constraints_scenarios_and_budgets_unchanged": True,
            },
        )
        return original_loop(effective, **kwargs)

    frozen.controller.run_master_loop = hook
    try:
        # This exact original entry point still verifies/reloads every source,
        # input, problem, native cache and cumulative budget before the hook.
        result = frozen.resume(config_path, run)
        frozen.atomic_json(
            receipt_dir / "completion.json",
            {
                "problem_sha256": result["problem_sha256"],
                "stop_reason": result["stop_reason"],
                "spent_after": frozen.read_json(run / "execution-budget.json"),
                "search_sha256": frozen.file_sha256(run / "search.json"),
                "schedule_receipt_sha256": frozen.file_sha256(receipt_dir / "receipt.json"),
                "native_evidence_injected": False,
            },
        )
        return result
    finally:
        frozen.controller.run_master_loop = original_loop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--baseline-prefetch", choices=("declared", "defer"), default="declared")
    args = parser.parse_args()
    result = continue_search(
        args.run,
        args.config,
        args.proposal,
        args.plan_id,
        inspect_only=args.inspect_only,
        baseline_prefetch=args.baseline_prefetch,
    )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
