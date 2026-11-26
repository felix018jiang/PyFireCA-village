"""Finish a frozen pair frontier, then budget the next two cardinality layers.

Only the execution schedule is new. The physical producer, common layouts,
source protocols, failure certificates and previous observations stay frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from math import comb, fsum
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class StageLimit(Exception):
    """An unvisited frontier remains; this is not an infeasibility result."""


def plan_status(ids, scenarios, cache):
    rows = [cache.get((tuple(ids), scenario)) for scenario in scenarios]
    if any(row is not None and row.status == "failed" for row in rows):
        return "failed"
    return (
        "passed" if all(row is not None and row.status == "passed" for row in rows) else "unknown"
    )


def check_plan(ids, scenarios, cache, evaluate):
    """Reuse every old observation; a failure suffices, a pass needs all scales."""
    ids = tuple(sorted(ids))
    status = plan_status(ids, scenarios, cache)
    if status != "unknown":
        return status
    witnesses = [(set(p), s) for (p, s), row in cache.items() if row.status == "failed"]
    scores = {
        s: min((len(set(ids) ^ p) for p, t in witnesses if s == t), default=10**9)
        for s in scenarios
    }
    for scenario in sorted(scenarios, key=lambda s: (scores[s], scenarios.index(s))):
        key = ids, scenario
        if key not in cache:
            row = evaluate(ids, scenario)
            if row.status not in {"passed", "failed"}:
                raise ValueError("physical unknown must be resolved, not cached as a failure")
            cache[key] = row
        if cache[key].status == "failed":
            return "failed"
    return plan_status(ids, scenarios, cache)


def search_layer(areas, count, scenarios, cache, evaluate, cuts, guard, on_plan):
    """Lazily inspect one complete candidate layer, retaining budget blockers."""
    from pyfireca.planning.building_search import ordered_demolition_sets

    statistics = {
        "area_pruned_prefixes": 0,
        "area_pruned_combinations": 0,
        "failure_cut_pruned_combinations": 0,
    }
    rows, pending, reason = [], None, None

    def bound(prefix, remaining, slots):
        guard()
        return cuts.bound(prefix, remaining, slots)

    complete = False
    try:
        for ids, area in ordered_demolition_sets(
            areas, count, statistics=statistics, branch_bound=bound
        ):
            pending = list(ids)
            status = check_plan(ids, scenarios, cache, evaluate)
            row = {"demolished_ids": list(ids), "nominal_area_m2": area, "status": status}
            rows.append(row)
            on_plan(row)
            pending = None
            if status == "passed":
                reason = "feasible_smaller_plan_found"
                break
        else:
            complete = True
    except StageLimit as error:
        reason = str(error)
    covered = (
        sum(row["status"] == "failed" for row in rows)
        + statistics["failure_cut_pruned_combinations"]
    )
    if complete and covered != comb(len(areas), count):
        raise ValueError("closed layer does not account for every candidate combination")
    return {
        "demolished_count": count,
        "total_combinations": comb(len(areas), count),
        "all_combinations_failed_or_proved_failed": complete,
        "stop_reason": reason,
        "first_unfinished_plan": pending,
        "visited_plans": rows,
        **statistics,
    }


def continue_counts(areas, scenarios, cache, pairs, evaluate, cuts, start_stage, guard, on_plan):
    """Pairs are a gate. Four-house exploration cannot certify an open triple layer."""
    start_stage(2)
    pair_rows, stop_reason = [], None
    for ids in sorted(pairs, key=lambda p: (fsum(areas[i] for i in p), p)):
        try:
            status = check_plan(ids, scenarios, cache, evaluate)
        except StageLimit as error:
            status, stop_reason = "unknown", str(error)
        row = {"demolished_ids": list(ids), "status": status}
        pair_rows.append(row)
        on_plan(row)
        if status == "unknown":
            break
    pairs_complete = len(pair_rows) == len(pairs) and all(
        row["status"] in {"passed", "failed"} for row in pair_rows
    )
    layers = []
    if pairs_complete and all(row["status"] == "failed" for row in pair_rows):
        for count in (3, 4):
            if any(
                len(ids) <= count and plan_status(ids, scenarios, cache) == "passed"
                for ids, _ in cache
            ):
                break
            start_stage(count)
            layer = search_layer(areas, count, scenarios, cache, evaluate, cuts, guard, on_plan)
            layers.append(layer)
            if any(row["status"] == "passed" for row in layer["visited_plans"]):
                break
    safe = {ids for ids, _ in cache if plan_status(ids, scenarios, cache) == "passed"}
    if not safe:
        raise ValueError("a fully observed initial feasible plan is required")
    best = min(safe, key=lambda ids: (len(ids), fsum(areas[i] for i in ids)))
    lower = 2  # Parent proof excludes all zero/one-house plans, checked before execution.
    if pairs_complete and all(row["status"] == "failed" for row in pair_rows):
        lower = 3
        for layer in layers:
            if (
                layer["demolished_count"] == lower
                and layer["all_combinations_failed_or_proved_failed"]
            ):
                lower += 1
            else:
                break
    # Pair area optimality is certified only when the entire old frontier is resolved.
    optimal = len(best) == 2 and pairs_complete
    return {
        "objective_policy": "lexicographic_count_then_sum_nominal_footprint_area_m2",
        "status": "optimal" if optimal else "incomplete",
        "optimality_proven": optimal,
        "minimum_count_proven": lower == len(best),
        "minimum_count_lower_bound": lower,
        "best_ids": list(best),
        "best_objective": [len(best), fsum(areas[i] for i in best)],
        "pair_frontier_complete": pairs_complete,
        "pair_frontier": pair_rows,
        "pair_stop_reason": stop_reason,
        "layers": layers,
        "area_proof_scope": "all_pairs_resolved" if optimal else "same_count_area_unresolved",
    }


def prepare(config_path, output):
    config = read(config_path)
    parent = (config_path.parent / config["parent_root"]).resolve()
    experiment = read(parent / "experiment.json")
    frontier = (config_path.parent / config["frontier_path"]).resolve()
    if (
        sha(frontier) != config["frontier_sha256"]
        or sha(parent / "search-result.json") != config["parent_result_sha256"]
        or experiment["problem_sha256"] != config["problem_sha256"]
        or not read(parent / "audit.json")["audit_passed"]
    ):
        raise ValueError("parent evidence or frozen frontier changed")
    files = [parent / name for name in ("experiment.json", "search-result.json", "audit.json")]
    files += sorted(parent.glob("evaluations/*/check.json"))
    files += sorted(parent.glob("evaluations/*/evaluation.json"))
    receipt = {
        "schema_version": "heritage-count-extension-1",
        "config": config,
        "parent_root": str(parent),
        "parent_files_sha256": {str(p.relative_to(parent)): sha(p) for p in files},
        "driver_sha256": sha(__file__),
        "imported_evaluation_folders": sorted(
            p.parent.name for p in parent.glob("evaluations/*/check.json")
        ),
    }
    if output.exists():
        if read(output / "continuation.json") != receipt:
            raise ValueError("continuation changed its driver, budget or parent evidence")
        return receipt
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(parent / "source-snapshot", output / "source-snapshot")
    shutil.copy2(__file__, output / "source-snapshot/scripts" / Path(__file__).name)
    total = len(receipt["imported_evaluation_folders"]) + sum(
        limit["new_CA_checks"] for limit in config["stage_limits"].values()
    )
    child = {**experiment, "config": {**experiment["config"], "max_total_checks": total}}
    # 48 inherited + 136 pair + 32 triple + 32 quadruple checks, no held-out data.
    write(output / "experiment.json", child)
    for name in ("inventory.json", "preflight.json"):
        shutil.copy2(parent / name, output / name)
    for source in files:
        if source.parent.parent.name == "evaluations":
            target = output / source.relative_to(parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    write(output / "continuation.json", receipt)
    return receipt


def run(config_path, output, *, resume=False, prepare_only=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    if output.exists() and not resume:
        raise ValueError("existing continuation requires --resume")
    receipt = prepare(config_path, output)
    if prepare_only:
        print({"prepared": str(output), "new_CA_checks": 0}, flush=True)
        return
    if (output / "execution-complete.json").exists():
        print({"already_complete": True, "new_CA_checks": 0}, flush=True)
        return read(output / "search-result.json")
    if not resume:
        frozen_script = output / "source-snapshot/scripts" / Path(__file__).name
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(frozen_script),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")},
        )
    frozen = output / "source-snapshot"
    sys.path.insert(0, str(frozen / "src"))
    sys.path.insert(0, str(frozen / "scripts"))
    from run_heritage_bounded_search import export_best, save_evaluation

    from pyfireca.planning.building_search import ProtectionCheck
    from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
    from pyfireca.planning.failure_cuts import FailureCutStore
    from pyfireca.planning.heritage import content_hash
    from pyfireca.planning.study_io import load_study

    producer = Path(sys.modules[RoofDemolitionOracle.__module__].__file__).resolve()
    if producer != frozen / "src/pyfireca/planning/engines/roof_search.py":
        raise ValueError("physical producer must come from the unchanged source snapshot")
    experiment, config = read(output / "experiment.json"), receipt["config"]
    original, problem = experiment["config"], experiment["problem"]
    for path, signature in experiment["source_sha256"].items():
        if content_hash((frozen / path).read_text()) != signature:
            raise ValueError("frozen physical source changed")
    for path, signature in experiment["source_references_sha256"].items():
        if sha(path) != signature:
            raise ValueError("frozen source protocol changed")
    bundle, transport, inventory = load_study(config_path.parent / original["study_path"])
    budget_path = output / "stage-budget.json"
    ledger = read(budget_path) if budget_path.exists() else {}
    clock = {"stage": None, "started": None, "base": 0}

    def save_budget():
        if clock["stage"] is not None:
            ledger[clock["stage"]]["elapsed_s"] = (
                clock["base"] + time.monotonic() - clock["started"]
            )
        write(budget_path, ledger)

    def persist(ids, scenario, check, record, layout, result):
        if check.status not in {"passed", "failed"}:
            raise ValueError("invalid physical result requires explicit repair")
        folder = output / "evaluations" / content_hash([list(ids), scenario])
        folder.mkdir(parents=True, exist_ok=True)
        write(folder / "evaluation.json", record.to_mapping())
        write(
            folder / "check.json",
            {
                "problem_sha256": oracle.problem_sha256,
                "demolished_ids": list(ids),
                "scenario_id": scenario,
                "status": check.status,
                "evidence": check.evidence,
            },
        )
        if check.status == "failed" and not (output / "native-witness.json").exists():
            native = folder / "native"
            save_evaluation(native, record, layout, result, oracle._engine.last_contact)
            members = {}
            with zipfile.ZipFile(
                folder / "native.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=3
            ) as archive:
                for path in sorted(native.rglob("*")):
                    if path.is_file():
                        members[str(path.relative_to(native))] = sha(path)
                        archive.write(path, str(path.relative_to(native)))
            write(
                output / "native-witness.json",
                {
                    "folder": str(folder.relative_to(output)),
                    "members_sha256": members,
                    "archive_sha256": sha(folder / "native.zip"),
                },
            )
            shutil.rmtree(native)
        save_budget()
        print(
            {
                "completed_ids": ids,
                "scenario": scenario,
                "status": check.status,
                "source": check.evidence["source_id"],
                "first_ignitions": [
                    (c["building_id"], c["first_ignition_s"])
                    for c in check.evidence["core_outcomes"]
                    if c["ignited"]
                ],
            },
            flush=True,
        )

    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=tuple(original["candidate_ids"]),
        fires=[
            {"fire_id": label, "protocol": data["protocol"]}
            for label, data in problem["fires"].items()
        ],
        code_version=problem["code_version"],
        horizon_s=original["horizon_s"],
        scales_m=tuple(original["roof_scales_m"]),
        quadrature_spacing_m=original["quadrature_spacing_m"],
        max_nodes=original["max_nodes"],
        max_radiation_edges=original["max_radiation_edges"],
        refinement_ids=original["refinement_ids"],
        maximum_demolitions=original["maximum_demolitions"],
        core_overlap_policy=original["core_overlap_policy"],
        stop_at_first_core_ignition=True,
        on_evaluation=persist,
    )
    oracle.enable_failure_certificates = True
    if oracle.problem_sha256 != experiment["problem_sha256"]:
        raise ValueError("continuation changed the physical problem")
    areas = {i: inventory.by_id[i].geometry.area for i in original["candidate_ids"]}
    cuts, cache, new_keys = FailureCutStore(oracle.problem_sha256, areas), {}, set()
    for path in sorted(output.glob("evaluations/*/check.json")):
        row = read(path)
        if row["problem_sha256"] != oracle.problem_sha256:
            raise ValueError("evaluation is not from this frozen physical problem")
        key = tuple(row["demolished_ids"]), row["scenario_id"]
        cache[key] = ProtectionCheck(row["status"], row["evidence"])
        if row["status"] == "failed":
            cuts.learn(*key, cache[key])
        if path.parent.name not in receipt["imported_evaluation_folders"]:
            new_keys.add(key)
    scenarios = tuple(oracle.scenarios)
    initial = tuple(config["initial_ids"])
    if plan_status(initial, scenarios, cache) != "passed":
        raise ValueError("five-house initial solution is not fully verified in the parent")
    frontier = read(config_path.parent / config["frontier_path"])
    if frontier["minimum_count_lower_bound"] != 2 or frontier["candidate_count"] != len(areas):
        raise ValueError("the inherited zero/one-house exclusion certificate is required")
    pairs = [tuple(p) for p in frontier["unresolved_two_building_plans"]]
    if len(set(pairs)) != 17:
        raise ValueError("this continuation requires the exact 17-plan frozen frontier")

    def start_stage(count):
        save_budget()
        stage = str(count)
        ledger.setdefault(stage, {"elapsed_s": 0, "bound_calls": 0})
        # A crash after an evaluation was saved must not erase its spent solve time.
        recorded = fsum(
            cache[k].evidence.get("elapsed_s", 0) for k in new_keys if len(k[0]) == count
        )
        clock.update(
            stage=stage, base=max(recorded, ledger[stage]["elapsed_s"]), started=time.monotonic()
        )
        print(
            {
                "stage": count,
                "budget": config["stage_limits"][stage],
                "new_checks_already_spent": sum(len(ids) == count for ids, _ in new_keys),
            },
            flush=True,
        )

    def guard():
        stage = clock["stage"]
        limit = config["stage_limits"][stage]
        if clock["base"] + time.monotonic() - clock["started"] >= limit["wall_time_s"]:
            raise StageLimit("cumulative_stage_wall_time_limit")
        if ledger[stage]["bound_calls"] >= limit["bound_calls"]:
            raise StageLimit("cumulative_branch_bound_call_limit")
        ledger[stage]["bound_calls"] += 1
        if ledger[stage]["bound_calls"] % 1000 == 0:
            save_budget()

    def evaluate(ids, scenario):
        stage = clock["stage"]
        limit = config["stage_limits"][stage]
        if sum(len(p) == len(ids) for p, _ in new_keys) >= limit["new_CA_checks"]:
            raise StageLimit("cumulative_stage_CA_limit")
        if clock["base"] + time.monotonic() - clock["started"] >= limit["wall_time_s"]:
            raise StageLimit("cumulative_stage_wall_time_limit")
        if shutil.disk_usage(output).free < original["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("less than the frozen 2 GB free disk threshold")
        save_budget()
        print({"starting_ids": ids, "scenario": scenario, "stage": stage}, flush=True)
        check = oracle(ids, scenario)
        new_keys.add((ids, scenario))
        if check.status == "failed":
            cuts.learn(ids, scenario, check)
        return check

    def on_plan(row):
        save_budget()
        write(
            output / "stage-progress.json",
            {
                "stage": clock["stage"],
                "latest_plan": row,
                "new_checks_total": len(new_keys),
                "new_checks_by_stage": {
                    str(k): sum(len(ids) == k for ids, _ in new_keys) for k in (2, 3, 4)
                },
                "physical_problem_sha256": oracle.problem_sha256,
            },
        )

    result = continue_counts(
        areas, scenarios, cache, pairs, evaluate, cuts, start_stage, guard, on_plan
    )
    save_budget()
    result.update(
        schema_version="heritage-count-extension-result-1",
        problem_sha256=oracle.problem_sha256,
        candidate_ids=sorted(areas),
        all_original_candidate_ids_preserved=True,
        initial_ids=list(initial),
        inherited_checks=len(receipt["imported_evaluation_folders"]),
        new_checks_total=len(new_keys),
        physical_CA_checks_total=len(cache),
        new_checks_by_stage={str(k): sum(len(ids) == k for ids, _ in new_keys) for k in (2, 3, 4)},
        bounds={
            "minimum_count_lower": result["minimum_count_lower_bound"],
            "feasible_count_upper": len(result["best_ids"]),
            "incumbent_nominal_area_m2": result["best_objective"][1],
            "same_count_area_lower_bound_m2": cuts.bound((), tuple(areas), len(result["best_ids"])),
        },
        failure_cuts=cuts.to_mapping(),
        stage_budget=ledger,
        unresolved_scope=(
            "unclosed cardinality layers and cheaper same-count plans remain;"
            "no heldout reuse;no exhaustive village enumeration"
        ),
        physical_problem_identical_to_parent=True,
    )
    write(output / "search-result.json", result)
    triple_cache = {
        (oracle.problem_sha256, ids, scenario): check for (ids, scenario), check in cache.items()
    }
    export_best(original, output, bundle, inventory, oracle, result, triple_cache)
    write(
        output / "execution-complete.json",
        {
            "status": "complete_pending_audit_and_documents",
            "new_checks_total": len(new_keys),
            "bounds": result["bounds"],
        },
    )
    print(
        {
            "complete": True,
            "best_ids": result["best_ids"],
            "bounds": result["bounds"],
            "new_checks_total": len(new_keys),
        },
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, resume=args.resume, prepare_only=args.prepare_only)
