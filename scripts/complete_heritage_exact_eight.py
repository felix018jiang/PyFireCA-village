"""Complete a frozen finite search in two stages, reusing its physical producer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import shutil
import sys
from dataclasses import asdict
from math import fsum
from pathlib import Path
from time import perf_counter


def read(path):
    return json.loads(Path(path).read_text())


def dump(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def plan_status(ids, scenarios, cache):
    rows = [cache.get((tuple(ids), s)) for s in scenarios]
    if any(r is not None and r["status"] == "failed" for r in rows):
        return "failed"
    return "passed" if all(r is not None and r["status"] == "passed" for r in rows) else "unknown"


def certificate(areas, scenarios, cache):
    """Independently classify every subset; bounds depend on a fully checked plan."""
    candidates = sorted(areas)
    plans = [p for k in range(len(candidates) + 1) for p in itertools.combinations(candidates, k)]

    def objective(ids):
        return len(ids), fsum(areas[i] for i in ids)

    safe = [p for p in plans if plan_status(p, scenarios, cache) == "passed"]
    if not safe:
        raise ValueError("verified initial feasible solution is required")
    best = min(safe, key=objective)
    table = []
    for p in plans:
        status = plan_status(p, scenarios, cache)
        if status == "unknown" and len(p) > len(best):
            status = "cardinality_bound"
        elif status == "unknown" and objective(p) >= objective(best):
            status = "area_bound" if objective(p) > objective(best) else "objective_tie"
        table.append({"demolished_ids": list(p), "objective": list(objective(p)), "status": status})
    unknown = [r for r in table if r["status"] == "unknown"]
    return {
        "status": "incomplete" if unknown else "optimal",
        "optimality_proven": not unknown,
        "best_ids": list(best),
        "best_objective": list(objective(best)),
        "unverified_better_plan_count": len(unknown),
        "minimum_count_proven": not any(r["objective"][0] < len(best) for r in unknown),
        "subset_table": table,
    }


def complete_stages(areas, scenarios, cache, pending, evaluate, *, new_keys, budget, on_plan=None):
    """Finish every pending pair; enter triples only after all pairs fail."""
    started, trace, stop_reason = perf_counter(), [], None

    def objective(ids):
        return len(ids), fsum(areas[i] for i in ids)

    for count in (2, 3):
        if count == 3 and any(plan_status(ids, scenarios, cache) != "failed" for ids in pending[2]):
            break
        for ids in sorted(pending[count], key=objective):
            best = certificate(areas, scenarios, cache)["best_objective"]
            if count == 3 and list(objective(ids)) >= best:
                break
            status = plan_status(ids, scenarios, cache)
            if status == "unknown":
                witnesses = [(set(p), s) for (p, s), r in cache.items() if r["status"] == "failed"]
                scores = {
                    s: min(
                        (len(set(ids) ^ p) for p, t in witnesses if t == s), default=len(areas) + 1
                    )
                    for s in scenarios
                }
                order = sorted(scenarios, key=lambda s: (scores[s], scenarios.index(s)))
                for scenario in order:
                    key = (ids, scenario)
                    if key not in cache:
                        used = sum(len(p) == count for p, _ in new_keys)
                        if (
                            len(new_keys) >= budget["max_new_checks"]
                            or used >= budget["stage_limits"][str(count)]
                        ):
                            stop_reason = "check_limit"
                            break
                        if perf_counter() - started >= budget["max_wall_time_s"]:
                            stop_reason = "wall_time_limit"
                            break
                        cache[key] = evaluate(ids, scenario)
                        new_keys.add(key)
                    if cache[key]["status"] == "failed":
                        break
                status = plan_status(ids, scenarios, cache)
            row = {"demolished_ids": list(ids), "stage": count, "status": status}
            trace.append(row)
            if on_plan is not None:
                on_plan(row)
            if status == "unknown":
                stop_reason = stop_reason or "unresolved_physical_evaluation"
                break
        if stop_reason:
            break
    return {
        **certificate(areas, scenarios, cache),
        "stage_trace": trace,
        "budget_stop_reason": stop_reason,
        "new_checks_total": len(new_keys),
        "new_checks_by_stage": {str(k): sum(len(p) == k for p, _ in new_keys) for k in (2, 3)},
        "elapsed_this_invocation_s": perf_counter() - started,
    }


def run(config_path, output, *, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = read(config_path)
    parent = (config_path.parent / config["parent_root"]).resolve()
    frozen_src = parent / "source-snapshot/src"
    sys.path.insert(0, str(frozen_src))
    from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
    from pyfireca.planning.heritage import content_hash

    if (
        Path(sys.modules[RoofDemolitionOracle.__module__].__file__).resolve()
        != (frozen_src / "pyfireca/planning/engines/roof_search.py").resolve()
    ):
        raise ValueError("continuation must import the frozen physical producer")
    experiment = read(parent / "experiment.json")
    for relative, expected in experiment["source_sha256"].items():
        if content_hash((parent / "source-snapshot" / relative).read_text()) != expected:
            raise ValueError("frozen producer snapshot changed")
    original = experiment["config"]
    spec = importlib.util.spec_from_file_location(
        "completion_physical_io", parent / "source-snapshot/scripts/run_heritage_roof_study.py"
    )
    physical_io = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(physical_io)
    bundle, transport, inventory = physical_io.load_study(
        config_path.parent / original["study_path"]
    )
    fires = [
        {"fire_id": label, "protocol": data["protocol"]}
        for label, data in experiment["problem"]["fires"].items()
    ]
    native_saved = output.exists() and any(output.glob("evaluations/*/native"))

    def persist(ids, scenario, check, record, layout, result):
        nonlocal native_saved
        folder = output / "evaluations" / content_hash([list(ids), scenario])
        folder.mkdir(parents=True, exist_ok=True)
        dump(folder / "evaluation.json", record.to_mapping())
        saved = {
            "problem_sha256": oracle.problem_sha256,
            "demolished_ids": list(ids),
            "scenario_id": scenario,
            **asdict(check),
        }
        dump(folder / "check.json", saved)
        if not native_saved:
            physical_io.save_evaluation(
                folder / "native", record, layout, result, oracle._engine.contact_builder(layout)
            )
            native_saved = True
        print(
            json.dumps(
                {
                    "ids": ids,
                    "scenario": scenario,
                    "status": check.status,
                    "source": check.evidence["source_id"],
                    "elapsed_s": round(check.evidence["elapsed_s"], 2),
                }
            ),
            flush=True,
        )
        dump(
            output / "progress.json",
            {
                "latest": saved,
                "completed_new_checks": len(list(output.glob("evaluations/*/check.json"))),
            },
        )

    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=original["candidate_ids"],
        fires=fires,
        code_version=experiment["problem"]["code_version"],
        horizon_s=original["horizon_s"],
        scales_m=original["roof_scales_m"],
        quadrature_spacing_m=original["quadrature_spacing_m"],
        max_nodes=original["max_nodes"],
        max_radiation_edges=original["max_radiation_edges"],
        on_evaluation=persist,
        refinement_ids=original["refinement_ids"],
        maximum_demolitions=original["maximum_demolitions"],
    )
    if (
        oracle.problem_sha256 != experiment["problem_sha256"]
        or oracle.problem_sha256 != config["problem_sha256"]
    ):
        raise ValueError("continuation changed the physical problem")
    parent_paths = [
        parent / name
        for name in ("experiment.json", "inventory.json", "search-result-first-execution.json")
    ]
    parent_paths += sorted(parent.glob("evaluations/*/check.json"))
    parent_paths += sorted(parent.glob("evaluations/*/evaluation.json"))
    receipt = {
        "schema_version": "heritage-exact-completion-receipt-1",
        "config": config,
        "parent_root": str(parent),
        "problem_sha256": oracle.problem_sha256,
        "parent_files_sha256": {str(p.relative_to(parent)): sha(p) for p in parent_paths},
        "driver_sha256": sha(__file__),
    }
    if resume:
        if read(output / "continuation.json") != receipt:
            raise ValueError("continuation receipt, parent data or driver changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(output / "continuation.json", receipt)
        shutil.copyfile(__file__, output / "driver-snapshot.py")
    cache, new_keys = {}, set()
    for root in (parent, output):
        for path in sorted(root.glob("evaluations/*/check.json")):
            row = read(path)
            if row["problem_sha256"] != oracle.problem_sha256:
                raise ValueError("cached check belongs to a different physical problem")
            key = tuple(row["demolished_ids"]), row["scenario_id"]
            if key in cache:
                raise ValueError("continuation unexpectedly repeated a parent evaluation")
            cache[key] = {"status": row["status"], "evidence": row["evidence"]}
            if root == output:
                new_keys.add(key)
    areas = {i: inventory.by_id[i].geometry.area for i in original["candidate_ids"]}
    scenarios = list(oracle.scenarios)
    if plan_status(tuple(sorted(original["initial_ids"])), scenarios, cache) != "passed":
        raise ValueError("original three-building initial solution is not fully verified")
    parent_cache = {key: value for key, value in cache.items() if key not in new_keys}
    unverified = certificate(areas, scenarios, parent_cache)["subset_table"]
    pending = {k: [tuple(sorted(p)) for p in config["pending_plans"][str(k)]] for k in (2, 3)}
    expected = {
        k: {
            tuple(r["demolished_ids"])
            for r in unverified
            if r["status"] == "unknown" and len(r["demolished_ids"]) == k
        }
        for k in (2, 3)
    }
    if any(set(pending[k]) != expected[k] for k in (2, 3)):
        raise ValueError("frozen completion list does not match every remaining better plan")

    def evaluate(ids, scenario):
        if shutil.disk_usage(output).free < original["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("insufficient disk for continuation")
        print(f"Starting {ids}/{scenario}", flush=True)
        return asdict(oracle(ids, scenario))

    before = len(new_keys)
    result = complete_stages(
        areas,
        scenarios,
        cache,
        pending,
        evaluate,
        new_keys=new_keys,
        budget=config["budget"],
        on_plan=lambda row: dump(output / "stage-progress.json", {"latest_plan": row}),
    )
    result.update(
        problem_sha256=oracle.problem_sha256,
        parent_checks=len(parent_cache),
        new_checks_this_invocation=len(new_keys) - before,
    )
    dump(output / "search-result.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "optimality_proven",
                    "best_ids",
                    "new_checks_total",
                    "new_checks_this_invocation",
                )
            }
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, resume=args.resume)
