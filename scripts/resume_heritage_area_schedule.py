"""Continue a frozen area master with an auditable scenario evaluation order.

The original snapshot supplies every physical module, oracle, cache reader,
cut validator and master loop. This wrapper only permutes the same scenario
set and optionally declares larger cumulative computing limits. It never edits
the original configuration, source snapshot, cached evidence or spent budget.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path


def scenario_order(scenarios, policy):
    if policy == "declared":
        return tuple(scenarios)
    if policy != "coarse_first":
        raise ValueError("unknown scenario-order policy")
    # Preserve declared fire order within each support. No source is dropped.
    return tuple(sorted(scenarios, key=lambda name: -float(scenarios[name][1])))


def extended_limits(config, spent, overrides):
    result = dict(config)
    fields = {
        "max_new_checks": "started_checks",
        "max_wall_time_s": "elapsed_s",
        "max_mip_time_s": "mip_elapsed_s",
    }
    for field, cumulative in fields.items():
        value = overrides.get(field)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value < max(config[field], spent[cumulative])
            or (field == "max_new_checks" and not isinstance(value, int))
        ):
            raise ValueError(f"{field} must be a cumulative limit covering original and spent work")
        result[field] = value
    return result


def load_frozen_manager(run):
    if any(name == "pyfireca" or name.startswith("pyfireca.") for name in sys.modules):
        raise ValueError("start a fresh process so only the original physical snapshot is imported")
    snapshot = run / "source-snapshot"
    helpers = {
        "run_heritage_area_study",
        "run_heritage_fixed_comparison",
        "preflight_heritage_exact_expansion",
        "run_heritage_roof_study",
    }
    for name in helpers.intersection(sys.modules):
        path = getattr(sys.modules[name], "__file__", None)
        if path is None or not Path(path).resolve().is_relative_to(snapshot):
            raise ValueError("a preloaded project helper comes from outside the original snapshot")
    script = snapshot / "scripts/run_heritage_area_master.py"
    if not script.is_file():
        raise ValueError("original prepared source snapshot is required")
    sys.path[:0] = [str(snapshot / "src"), str(snapshot / "scripts")]
    spec = importlib.util.spec_from_file_location("frozen_heritage_area_master", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name, imported in tuple(sys.modules.items()):
        if name == "pyfireca" or name.startswith("pyfireca.") or name in helpers:
            path = getattr(imported, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(snapshot):
                raise ValueError("physical or helper module was imported from outside the snapshot")
    return module


def continue_search(run, config_path, *, policy="coarse_first", overrides=None, inspect_only=False):
    run, config_path = Path(run).resolve(), Path(config_path).resolve()
    frozen = load_frozen_manager(run)
    experiment = frozen.read_json(run / "experiment.json")
    if frozen.content_hash(experiment["problem"]) != experiment["problem_sha256"]:
        raise ValueError("original problem metadata fingerprint differs")
    config = frozen.read_json(config_path)
    frozen.validate_config(config)
    frozen.require_mip_solver()
    if (
        config != experiment["config"]
        or frozen.file_sha256(config_path) != experiment["config_sha256"]
    ):
        raise ValueError("original frozen configuration changed")
    hashes = {
        str(path.relative_to(frozen.ROOT)): frozen.file_sha256(path)
        for path in frozen.source_paths()
    }
    if hashes != experiment["source_sha256"] or any(
        frozen.file_sha256(path) != digest
        for path, digest in experiment["references_sha256"].items()
    ):
        raise ValueError("original snapshot or physical input changed")
    if not frozen.read_json(run / "preflight.json")["all_passed"]:
        raise ValueError("original geometry preflight must have passed")
    if list((run / "evaluations").glob("*.pending")):
        raise ValueError("pending physical result requires inspection before continuation")
    oracle, area, _, _ = frozen.make_oracle(config_path, config, frozen.content_hash(hashes))
    if oracle.problem_sha256 != experiment["problem_sha256"]:
        raise ValueError("original physical problem did not reconstruct exactly")
    order = scenario_order(oracle.scenarios, policy)
    if len(order) != len(oracle.scenarios) or set(order) != set(oracle.scenarios):
        raise ValueError("schedule must be an exact permutation of the original scenarios")
    cache, cache_manifests = {}, {}
    for folder in sorted((run / "evaluations").iterdir()):
        if not folder.is_dir():
            continue
        manifest = frozen.read_json(folder / "complete.json")
        ids = frozen.canonical_ids(manifest["demolished_ids"])
        scenario = manifest["scenario_id"]
        if (
            not set(ids) <= set(oracle.candidate_ids)
            or scenario not in oracle.scenarios
            or folder.name != frozen.content_hash([list(ids), scenario])
        ):
            raise ValueError("cached plan/scenario differs from the original problem")
        cache[oracle.problem_sha256, ids, scenario] = frozen.read_complete(
            folder, oracle.problem_sha256, ids=ids, scenario=scenario
        )
        cache_manifests[folder.name] = frozen.file_sha256(folder / "complete.json")
    spent = frozen.read_json(run / "execution-budget.json")
    if spent["started_checks"] < len(cache):
        raise ValueError("started-check ledger is smaller than completed physical evidence")
    effective = extended_limits(config, spent, {} if overrides is None else overrides)
    details = {
        "problem_sha256": oracle.problem_sha256,
        "policy": policy,
        "scenario_evaluation_order": list(order),
        "cached_evaluations": len(cache),
        "reused_native_manifests_sha256": frozen.content_hash(cache_manifests),
        "cumulative_spent_before_start": spent,
        "cumulative_limits": {
            key: effective[key] for key in ("max_new_checks", "max_wall_time_s", "max_mip_time_s")
        },
        "physical_snapshot_matches_original": True,
        "new_CA_checks_in_inspection": 0,
    }
    if inspect_only:
        return details
    # ExecutionBudget refuses any unclosed worker; its counters are inherited.
    budget = frozen.ExecutionBudget(run / "execution-budget.json")
    receipt_dir = run / "schedules" / str(time.time_ns())
    try:
        receipt_dir.mkdir(parents=True, exist_ok=False)
        scheduler = receipt_dir / Path(__file__).name
        source_hash = frozen.file_sha256(Path(__file__))
        shutil.copy2(__file__, scheduler)
        if frozen.file_sha256(scheduler) != source_hash:
            raise ValueError("scheduler changed while freezing its execution receipt")
        previous_search = run / "search.json"
        if previous_search.is_file():
            shutil.copy2(previous_search, receipt_dir / "previous-search.json")
        receipt = {
            "schema_version": "heritage-area-schedule-extension-1",
            **details,
            "scheduler_sha256": source_hash,
            "original_experiment_sha256": frozen.file_sha256(run / "experiment.json"),
            "original_config_sha256": experiment["config_sha256"],
            "physical_execution_source_sha256": hashes,
            "spent_budget_reset": False,
            "original_config_modified": False,
            "cached_evidence_rebound_or_modified": False,
            "previous_search_sha256": frozen.file_sha256(receipt_dir / "previous-search.json")
            if previous_search.is_file()
            else None,
            "extension_scope": "scenario_order_and_explicit_cumulative_computing_limits_only",
        }
        frozen.write_json(receipt_dir / "receipt.json", receipt)
        receipt_hash = frozen.file_sha256(receipt_dir / "receipt.json")
        oracle.on_evaluation = lambda ids, scenario, check, record, layout, result: (
            frozen.persist_result(
                run, oracle, area, config, ids, scenario, check, record, layout, result
            )
        )

        def evaluate(ids, scenario):
            if shutil.disk_usage(run).free < config["minimum_free_disk_GB"] * 1e9:
                return frozen.ProtectionCheck("unknown", {"reason": "minimum_free_disk_reached"})
            return oracle(ids, scenario)

        def save_report(value):
            value.update(
                fixed_source_ids=experiment["fixed_source_ids"],
                protected_targets=[asdict(t) for t in oracle.inventory.targets],
                horizon_s=config["horizon_s"],
                max_dt_s=config["max_dt_s"],
                roof_scales_m=config["roof_scales_m"],
                quadrature_spacing_m=config["quadrature_spacing_m"],
                source_fires={
                    label: list(fire["footprints"]) for label, fire in oracle.fires.items()
                },
                frozen_source_family_count=len(oracle.fires),
                frozen_source_family_baseline_ids={
                    label: fire["protocol"]["draws"][0]["baseline_source_id"]
                    for label, fire in oracle.fires.items()
                },
                source_family_interpretation="reused_design_priorities;not_new_independent_draws",
                execution_schedule_receipt=str(receipt_dir / "receipt.json"),
                execution_schedule_receipt_sha256=receipt_hash,
            )
            frozen.atomic_json(run / "search.json", value)

        report = frozen.run_master_loop(
            effective,
            candidate_ids=oracle.candidate_ids,
            scenario_ids=order,
            area=area,
            problem_sha256=oracle.problem_sha256,
            evaluate=evaluate,
            budget=budget,
            cache=cache,
            fixed_retained_ids=sorted(area.owner_ids - set(oracle.candidate_ids)),
            on_report=save_report,
        )
        frozen.write_json(
            receipt_dir / "completion.json",
            {
                "stop_reason": report["stop_reason"],
                "budget": report["budget"],
                "global_area_lower_bound_m2": report["global_area_lower_bound_m2"],
                "verified_area_upper_bound_m2": report["verified_area_upper_bound_m2"],
            },
        )
        return report
    finally:
        budget.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", choices=("coarse_first", "declared"), default="coarse_first")
    parser.add_argument("--max-cumulative-checks", type=int)
    parser.add_argument("--max-cumulative-wall-s", type=float)
    parser.add_argument("--max-cumulative-mip-s", type=float)
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()
    report = continue_search(
        args.run,
        args.config,
        policy=args.policy,
        inspect_only=args.inspect_only,
        overrides={
            "max_new_checks": args.max_cumulative_checks,
            "max_wall_time_s": args.max_cumulative_wall_s,
            "max_mip_time_s": args.max_cumulative_mip_s,
        },
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    (
                        "problem_sha256",
                        "policy",
                        "scenario_evaluation_order",
                        "cached_evaluations",
                        "cumulative_limits",
                        "new_CA_checks_in_inspection",
                    )
                    if args.inspect_only
                    else (
                        "stop_reason",
                        "global_area_lower_bound_m2",
                        "verified_area_upper_bound_m2",
                        "budget",
                    )
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
