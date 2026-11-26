"""Register or resume an exact case-order permutation of a frozen comparison.

Only the original manager's execution_checks function is wrapped. Native inputs,
problem identity, expected cases, cache readers, resume loop and spent budget
remain original. Active workers are inspected without sending any signal.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


def verify_original(run, config_path):
    experiment = read_json(run / "experiment.json")
    if (
        experiment.get("schema_version") != "heritage-joint-comparison-1"
        or content_hash(experiment["problem"]) != experiment["problem_sha256"]
        or read_json(config_path) != experiment["config"]
        or sha256(config_path) != experiment["config_sha256"]
    ):
        raise ValueError("original frozen comparison/config/problem differs")
    snapshot = run / "source-snapshot"
    for name, digest in experiment["source_sha256"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or sha256(snapshot / path) != digest:
            raise ValueError("original frozen source differs")
    if {str(p.relative_to(snapshot)) for p in snapshot.rglob("*.py")} - set(
        experiment["source_sha256"]
    ):
        raise ValueError("original snapshot contains unbound executable Python files")
    if any(sha256(path) != digest for path, digest in experiment["references_sha256"].items()):
        raise ValueError("original physical input/reference differs")
    if read_json(run / "preflight.json").get("passed") is not True:
        raise ValueError("original geometry preflight has not passed")
    return experiment


def load_manager(run):
    if any(name == "pyfireca" or name.startswith("pyfireca.") for name in sys.modules):
        raise ValueError("start a fresh process with only the original physical snapshot")
    helpers = {
        "run_heritage_area_study",
        "run_heritage_fixed_comparison",
        "run_heritage_roof_study",
        "preflight_heritage_exact_expansion",
    }
    snapshot = run / "source-snapshot"
    for name in helpers.intersection(sys.modules):
        path = getattr(sys.modules[name], "__file__", None)
        if path is None or not Path(path).resolve().is_relative_to(snapshot):
            raise ValueError("preloaded helper is outside the original frozen snapshot")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    path = snapshot / "scripts/run_heritage_joint_comparison.py"
    spec = importlib.util.spec_from_file_location("original_joint_schedule_manager", path)
    manager = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = manager
    spec.loader.exec_module(manager)
    for name, module in tuple(sys.modules.items()):
        if name == "pyfireca" or name.startswith("pyfireca.") or name in helpers:
            path = getattr(module, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(snapshot):
                raise ValueError("physical/helper module imported outside its original snapshot")
    return manager


def plan_first(cases, plan_ids, priority=None):
    cases, plan_ids = tuple(cases), list(plan_ids)
    priority = plan_ids if priority is None else list(priority)
    if (
        len(priority) != len(plan_ids)
        or len(set(priority)) != len(priority)
        or set(priority) != set(plan_ids)
    ):
        raise ValueError("priority requires an exact permutation of every original plan ID")
    keys = [(plan.plan_id, scenario) for plan, scenario in cases]
    if len(set(keys)) != len(keys):
        raise ValueError("original execution case list contains duplicate identities")
    ordered = tuple(
        (plan, scenario) for name in priority for plan, scenario in cases if plan.plan_id == name
    )
    if len(ordered) != len(cases) or {(p.plan_id, s) for p, s in ordered} != set(keys):
        raise ValueError("execution schedule must preserve exactly every original selected case")
    return ordered


def runtime():
    versions = {
        name + "_version": getattr(sys.modules.get(name), "__version__", None)
        for name in ("numpy", "scipy", "shapely")
    }
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        **versions,
        "geos_version": getattr(sys.modules.get("shapely"), "geos_version_string", None),
    }


def inspect_schedule(run, config_path, *, priority=None):
    run, config_path = Path(run).resolve(), Path(config_path).resolve()
    experiment = verify_original(run, config_path)
    manager = load_manager(run)
    config = experiment["config"]
    manager.validate_config(config)
    original = tuple(manager.execution_checks(config))
    plan_ids = [row["plan_id"] for row in config["plans"]]
    priority = plan_ids if priority is None else list(priority)
    ordered = plan_first(original, plan_ids, priority)
    expected = tuple(manager.expected_checks(config))
    manifests, unknown, existing = {}, {}, set()
    for plan, scenario in expected:
        folder = manager.result_folder(run, plan, scenario)
        if (folder / "complete.json").is_file():
            manager.read_complete(
                folder,
                experiment["problem_sha256"],
                plan_id=plan.plan_id,
                scenario_id=scenario,
                demolished_ids=plan.demolished_ids,
                cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
                terminal_required=config.get("terminal_fire_verification", False),
            )
            manifests[str((folder / "complete.json").relative_to(run))] = sha256(
                folder / "complete.json"
            )
            existing.add((plan.plan_id, scenario))
        elif (folder / "unknown-manifest.json").is_file():
            manager.read_unknown_source(folder, experiment["problem_sha256"], plan, scenario)
            unknown[str((folder / "unknown-manifest.json").relative_to(run))] = sha256(
                folder / "unknown-manifest.json"
            )
            existing.add((plan.plan_id, scenario))
    budget_bytes = (run / "execution-budget.json").read_bytes()
    budget = json.loads(budget_bytes)
    if (
        isinstance(budget.get("started_checks"), bool)
        or not isinstance(budget.get("started_checks"), int)
        or budget["started_checks"] < len(manifests)
        or not isinstance(budget.get("elapsed_s"), (int, float))
        or not math.isfinite(budget["elapsed_s"])
        or budget["elapsed_s"] < 0
    ):
        raise ValueError("original cumulative budget is invalid or smaller than completed evidence")
    pending = sorted(str(p.relative_to(run)) for p in (run / "evaluations").glob("*.pending"))
    details = {
        "schema_version": "heritage-joint-plan-first-inspection-1",
        "run_root": str(run),
        "config_path": str(config_path),
        "native_problem_sha256": experiment["problem_sha256"],
        "original_experiment_sha256": sha256(run / "experiment.json"),
        "original_config_sha256": experiment["config_sha256"],
        "original_source_sha256": experiment["source_sha256"],
        "original_references_sha256": experiment["references_sha256"],
        "priority_plan_ids": priority,
        "original_selected_case_order": [
            {"plan_id": p.plan_id, "scenario_id": s} for p, s in original
        ],
        "scheduled_selected_case_order": [
            {"plan_id": p.plan_id, "scenario_id": s} for p, s in ordered
        ],
        "expected_full_case_count": len(expected),
        "selected_case_set_unchanged": True,
        "cached_native_manifests_sha256": manifests,
        "cached_source_unknown_manifests_sha256": unknown,
        "cached_native_case_count": len(manifests),
        "next_uncomputed_cases": [
            {"plan_id": p.plan_id, "scenario_id": s}
            for p, s in ordered
            if (p.plan_id, s) not in existing
        ],
        "cumulative_budget_snapshot": budget,
        "original_budget_snapshot_sha256": hashlib.sha256(budget_bytes).hexdigest(),
        "original_limits": {key: config[key] for key in ("max_new_checks", "max_wall_time_s")},
        "pending_native_artifacts": pending,
        "handoff_ready": budget["active"] is None and not pending,
        "active_worker_stop_attempted": False,
        "external_safe_case_boundary_pause_supported": False,
        "runtime": runtime(),
        "new_CA_checks_in_inspection": 0,
        "original_config_or_source_modified": False,
    }
    verify_original(run, config_path)
    return manager, experiment, details


def register_schedule(run, config, receipt_dir, *, priority=None):
    _, experiment, details = inspect_schedule(run, config, priority=priority)
    receipt_dir = Path(receipt_dir).resolve()
    run = Path(run).resolve()
    if receipt_dir.is_relative_to(run) or any(
        Path(p).resolve().is_relative_to(receipt_dir) for p in experiment["references_sha256"]
    ):
        raise ValueError("schedule receipt must be external to native archive and original inputs")
    receipt_dir.mkdir(parents=True, exist_ok=False)
    wrapper = receipt_dir / Path(__file__).name
    shutil.copy2(__file__, wrapper)
    if sha256(wrapper) != sha256(__file__):
        raise ValueError("wrapper changed while its execution receipt was frozen")
    receipt = {
        "schema_version": "heritage-joint-plan-first-schedule-1",
        **{k: v for k, v in details.items() if k != "schema_version"},
        "wrapper_filename": wrapper.name,
        "wrapper_sha256": sha256(wrapper),
        "registered_utc": datetime.now(UTC).isoformat(),
        "extension_scope": "exact_execution_case_order_permutation_only",
        "native_problem_or_physical_inputs_changed": False,
        "spent_budget_reset": False,
        "native_cache_copied_or_rebound": False,
    }
    receipt["receipt_sha256"] = content_hash(receipt)
    write_json(receipt_dir / "receipt.json", receipt)
    return receipt


def resume_schedule(receipt_path):
    receipt_path = Path(receipt_path).resolve()
    receipt = read_json(receipt_path)
    if (
        receipt.get("schema_version") != "heritage-joint-plan-first-schedule-1"
        or receipt.get("receipt_sha256")
        != content_hash({k: v for k, v in receipt.items() if k != "receipt_sha256"})
        or receipt.get("wrapper_sha256") != sha256(__file__)
        or sha256(receipt_path.parent / receipt["wrapper_filename"]) != receipt["wrapper_sha256"]
    ):
        raise ValueError(
            "frozen schedule wrapper or receipt changed; use the registered wrapper copy"
        )
    manager, _, current = inspect_schedule(
        receipt["run_root"], receipt["config_path"], priority=receipt["priority_plan_ids"]
    )
    for key in (
        "native_problem_sha256",
        "original_experiment_sha256",
        "original_config_sha256",
        "original_source_sha256",
        "original_references_sha256",
        "scheduled_selected_case_order",
        "expected_full_case_count",
        "original_limits",
        "runtime",
    ):
        if current[key] != receipt[key]:
            raise ValueError("registered original comparison/runtime/schedule changed: " + key)
    before, registered = (
        current["cumulative_budget_snapshot"],
        receipt["cumulative_budget_snapshot"],
    )
    if (
        before["started_checks"] < registered["started_checks"]
        or before["elapsed_s"] < registered["elapsed_s"]
    ):
        raise ValueError("original spent budget was reset or decreased")
    if before["active"] is not None:
        raise ValueError("original worker is active; no signal sent and no handoff attempted")
    if current["pending_native_artifacts"]:
        raise ValueError("pending native result needs inspection before any continuation")
    for field in ("cached_native_manifests_sha256", "cached_source_unknown_manifests_sha256"):
        if any(current[field].get(name) != digest for name, digest in receipt[field].items()):
            raise ValueError("previously completed native evidence was changed or removed")
    base_execution = manager.execution_checks

    def ordered(config):
        return iter(
            plan_first(
                base_execution(config),
                [p["plan_id"] for p in config["plans"]],
                receipt["priority_plan_ids"],
            )
        )

    manager.execution_checks = ordered
    started = {
        "schema_version": "heritage-joint-plan-first-invocation-1",
        "schedule_receipt_sha256": receipt["receipt_sha256"],
        "budget_before": before,
        "started_utc": datetime.now(UTC).isoformat(),
        "physical_execution_source": "original_frozen_manager_only",
        "expected_checks_function_changed": False,
        "budget_limits_changed": False,
    }
    invocation = receipt_path.parent / (
        "invocation-" + str(os.getpid()) + "-" + str(__import__("time").time_ns())
    )
    invocation.mkdir(exist_ok=False)
    write_json(invocation / "started.json", started)
    try:
        report = manager.resume(Path(receipt["config_path"]), Path(receipt["run_root"]))
        return report
    finally:
        after = read_json(Path(receipt["run_root"]) / "execution-budget.json")
        write_json(
            invocation / "completion.json",
            {
                "schedule_receipt_sha256": receipt["receipt_sha256"],
                "budget_before": before,
                "budget_after": after,
                "finished_utc": datetime.now(UTC).isoformat(),
                "native_budget_closed": after["active"] is None,
                "spent_budget_reset": False,
            },
        )
        verify_original(Path(receipt["run_root"]), Path(receipt["config_path"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--register-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument("--run", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--priority-plan-ids", nargs="+")
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.resume:
        if args.receipt is None:
            parser.error("--receipt is required")
        report = resume_schedule(args.receipt)
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in (
                        "planned_checks",
                        "completed_checks",
                        "complete",
                        "started_CA_checks",
                    )
                }
            )
        )
        return
    if args.run is None or args.config is None:
        parser.error("--run and --config are required")
    if args.register_only:
        if args.receipt_dir is None:
            parser.error("--receipt-dir is required")
        result = register_schedule(
            args.run, args.config, args.receipt_dir, priority=args.priority_plan_ids
        )
    else:
        _, _, result = inspect_schedule(args.run, args.config, priority=args.priority_plan_ids)
    if args.output:
        protected = {
            args.config.resolve(),
            Path(__file__).resolve(),
            *[Path(path).resolve() for path in result["original_references_sha256"]],
        }
        if (
            args.output.resolve().is_relative_to(args.run.resolve())
            or args.output.resolve() in protected
        ):
            parser.error("inspection output must be external to original archive/config")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(
        json.dumps(
            {
                "native_problem_sha256": result["native_problem_sha256"],
                "cached_native_case_count": result["cached_native_case_count"],
                "handoff_ready": result["handoff_ready"],
                "active_worker_stop_attempted": False,
                "next_uncomputed_cases": result["next_uncomputed_cases"][:8],
            }
        )
    )


if __name__ == "__main__":
    main()
