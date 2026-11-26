"""Budgeted whole-candidate search with frozen source snapshots and valid cuts."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import zipfile
from math import comb, fsum
from pathlib import Path
from types import SimpleNamespace

from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_roof_study import save_evaluation

from pyfireca.planning.building_search import ProtectionCheck, exact_demolition_search
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.failure_cuts import FailureCutStore
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import (
    export_strategy,
    file_sha256,
    read_json,
    validate_material_binding,
    write_json,
)
from pyfireca.planning.strategy_map import render_strategy_map
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def run(config_path, output, *, resume=False, prepare_only=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = read_json(config_path)
    if config["schema_version"] != "heritage-bounded-search-config-1":
        raise ValueError("unsupported bounded search config")
    source_paths = [
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *(
            ROOT / "scripts" / p
            for p in (
                "run_heritage_bounded_search.py",
                "run_heritage_roof_study.py",
                "preflight_heritage_exact_expansion.py",
            )
        ),
    ]
    sources = {str(p.relative_to(ROOT)): content_hash(p.read_text()) for p in source_paths}
    bundle, transport, inventory = load_study(config_path.parent / config["study_path"])
    if sorted(config["candidate_ids"]) != sorted(inventory.candidate_ids):
        raise ValueError("bounded village run must include every eligible building")
    fires, source_refs = [], {}
    for item in config["fires"]:
        path = (config_path.parent / item["protocol_path"]).resolve()
        protocol = read_json(path)
        if (
            content_hash({k: v for k, v in protocol.items() if k != "protocol_sha256"})
            != protocol["protocol_sha256"]
        ):
            raise ValueError("source protocol hash mismatch")
        source_refs[str(path)] = file_sha256(path)
        draw = [d for d in protocol["draws"] if d["draw_id"] == item["draw_id"]]
        if len(draw) != 1:
            raise ValueError("one frozen draw required per design fire")
        protocol = {**protocol, "draws": draw}
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        fires.append({"fire_id": item["fire_id"], "protocol": protocol})

    def persist(ids, scenario, check, record, layout, result):
        folder = output / "evaluations" / content_hash([list(ids), scenario])
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "evaluation.json", record.to_mapping())
        write_json(
            folder / "check.json",
            {
                "problem_sha256": oracle.problem_sha256,
                "demolished_ids": ids,
                "scenario_id": scenario,
                "status": check.status,
                "evidence": check.evidence,
            },
        )
        if check.status == "failed" and not (output / "native-witness.json").exists():
            native = folder / "native"
            save_evaluation(native, record, layout, result, oracle._engine.last_contact)
            archive = folder / "native.zip"
            manifest = {}
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3
            ) as z:
                for path in sorted(native.rglob("*")):
                    if path.is_file():
                        relative = str(path.relative_to(native))
                        manifest[relative] = file_sha256(path)
                        z.write(path, relative)
            write_json(
                output / "native-witness.json",
                {
                    "folder": str(folder.relative_to(output)),
                    "members_sha256": manifest,
                    "archive_sha256": file_sha256(archive),
                },
            )
            shutil.rmtree(native)
        print(
            {
                "ids": ids,
                "scenario": scenario,
                "status": check.status,
                "source": check.evidence.get("source_id"),
                "cut_retained": len(
                    check.evidence.get("failure_certificate", {}).get("required_retained_ids", [])
                ),
                "seconds": round(check.evidence.get("elapsed_s", 0), 1),
            },
            flush=True,
        )

    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=tuple(config["candidate_ids"]),
        fires=fires,
        code_version=content_hash(sources),
        horizon_s=config["horizon_s"],
        scales_m=tuple(config["roof_scales_m"]),
        quadrature_spacing_m=config["quadrature_spacing_m"],
        max_nodes=config["max_nodes"],
        max_radiation_edges=config["max_radiation_edges"],
        on_evaluation=persist,
        refinement_ids=config["refinement_ids"],
        maximum_demolitions=config["maximum_demolitions"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
        stop_at_first_core_ignition=config.get("stop_at_first_core_ignition", True),
    )
    oracle.enable_failure_certificates = True
    experiment = {
        "config": config,
        "study": read_json(config_path.parent / config["study_path"]),
        "problem": oracle.problem,
        "problem_sha256": oracle.problem_sha256,
        "source_sha256": sources,
        "source_references_sha256": source_refs,
    }
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "experiment.json", experiment)
        write_json(output / "inventory.json", inventory.to_mapping())
        for path in source_paths:
            destination = output / "source-snapshot" / path.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    elif read_json(output / "experiment.json") != read_json_value(experiment):
        raise ValueError("cannot resume changed physical problem, inputs or producer")
    if prepare_only:
        rows = []
        for label, fire in oracle.fires.items():
            for scale in oracle.scales_m:
                layout = refine_roof_layout(
                    oracle.base,
                    inventory,
                    cell_size_m=scale,
                    refined_ids=fire["refined_ids"],
                    footprints=tuple(fire["footprints"].values()),
                    max_nodes=config["max_nodes"],
                )
                bounds = edge_bounds(layout, transport, fire["refined_ids"])
                row = {
                    "fire_id": label,
                    "scale_m": scale,
                    "nodes": len(layout.owners),
                    **bounds,
                    "passed": max(bounds.values()) <= config["max_radiation_edges"],
                }
                rows.append(row)
                print(row, flush=True)
        write_json(
            output / "preflight.json",
            {
                "rows": rows,
                "all_passed": all(r["passed"] for r in rows),
                "candidate_count": len(inventory.candidate_ids),
                "new_CA_checks": 0,
            },
        )
        if not all(r["passed"] for r in rows):
            raise ValueError("geometry capacity failed; no CA launched")
        return
    if not resume:
        env = {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")}
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(output / "source-snapshot/scripts" / Path(__file__).name),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            env,
        )
    if not read_json(output / "preflight.json")["all_passed"]:
        raise ValueError("a passing frozen geometry preflight is required")
    started = time.monotonic()
    areas = {i: inventory.by_id[i].geometry.area for i in inventory.candidate_ids}
    cuts = FailureCutStore(oracle.problem_sha256, areas)
    cache = {}
    for p in sorted(output.glob("evaluations/*/check.json")):
        row = read_json(p)
        if row["problem_sha256"] != oracle.problem_sha256:
            raise ValueError("cache problem mismatch")
        ids, scenario = tuple(row["demolished_ids"]), row["scenario_id"]
        check = ProtectionCheck(row["status"], row["evidence"])
        cache[oracle.problem_sha256, ids, scenario] = check
        if check.status == "failed":
            cuts.learn(ids, scenario, check)
    previous_count = len(cache)

    def evaluate(ids, scenario):
        if (
            len(cache) >= config["max_total_checks"]
            or time.monotonic() - started >= config["max_wall_time_s"]
        ):
            return ProtectionCheck("unknown", {"reason": "declared_budget_exhausted"})
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("insufficient disk for bounded search")
        print({"starting": list(ids), "scenario": scenario, "completed": len(cache)}, flush=True)
        return oracle(ids, scenario)

    def get(ids, scenario):
        key = (oracle.problem_sha256, ids, scenario)
        if key not in cache:
            check = evaluate(ids, scenario)
            if check.evidence.get("reason") == "declared_budget_exhausted":
                return check
            cache[key] = check
        check = cache[key]
        if check.status == "failed":
            cuts.learn(ids, scenario, check)
        return check

    if config.get("run_mode") == "fixed_confirmation":
        plan_ids = tuple(sorted(config["frozen_plan_ids"]))
        checks = []
        for arm, ids in (("baseline", ()), ("frozen_plan", plan_ids)):
            for scenario in oracle.scenarios:
                check = get(ids, scenario)
                checks.append(
                    {
                        "arm": arm,
                        "scenario_id": scenario,
                        "demolished_ids": list(ids),
                        "status": check.status,
                        "evidence": check.evidence,
                    }
                )
        report = {
            "schema_version": "heritage-bounded-fixed-confirmation-1",
            "physical_problem_sha256": oracle.problem_sha256,
            "frozen_plan_ids": list(plan_ids),
            "parent_design_result_sha256": config["parent_design_result_sha256"],
            "full_horizon_observation": not oracle.stop_at_first_core_ignition,
            "complete": all(c["status"] in {"passed", "failed"} for c in checks),
            "physical_CA_checks_total": len(list(output.glob("evaluations/*/check.json"))),
            "frozen_plan_all_scenarios_passed": all(
                c["status"] == "passed" for c in checks if c["arm"] == "frozen_plan"
            ),
            "checks": checks,
            "heldout_source_audit": config["heldout_source_audit"],
            "no_candidate_reselection": True,
        }
        write_json(output / "fixed-confirmation.json", report)
        print(
            {
                k: report[k]
                for k in (
                    "complete",
                    "physical_CA_checks_total",
                    "frozen_plan_all_scenarios_passed",
                )
            },
            flush=True,
        )
        return report

    best, seeds = None, []
    for seed in config["seed_plans"]:
        ids = tuple(sorted(seed))
        if best is not None and (len(ids), fsum(areas[i] for i in ids)) >= (
            len(best),
            fsum(areas[i] for i in best),
        ):
            seeds.append({"ids": ids, "status": "skipped_objective_bound"})
            continue
        checks = []
        for scenario in oracle.scenarios:
            c = get(ids, scenario)
            checks.append(c.status)
            if c.status != "passed":
                break
        status = (
            "passed"
            if len(checks) == len(oracle.scenarios) and all(s == "passed" for s in checks)
            else checks[-1]
        )
        seeds.append({"ids": ids, "status": status})
        if status == "passed" and (
            best is None
            or (len(ids), fsum(areas[i] for i in ids)) < (len(best), fsum(areas[i] for i in best))
        ):
            best = ids
    # One baseline per design scenario supplies distinct causal failure cuts.
    for scenario in oracle.scenarios:
        get((), scenario)
    for cut in cuts.to_mapping():
        if (
            best is not None
            and set(cut["required_demolished_ids"]) <= set(best)
            and not set(cut["required_retained_ids"]) & set(best)
        ):
            raise ValueError("failure cut contradicts fully verified incumbent")
    result = exact_demolition_search(
        areas,
        tuple(oracle.scenarios),
        get,
        problem_sha256=oracle.problem_sha256,
        initial_ids=best,
        cache=cache,
        max_new_checks=max(0, config["max_total_checks"] - len(cache)),
        max_wall_time_s=max(0, config["max_wall_time_s"] - (time.monotonic() - started)),
        failure_cuts=cuts,
        maximum_demolitions=config["maximum_demolitions"],
        prioritize_failures=True,
    )
    result["physical_CA_checks_total"] = len(list(output.glob("evaluations/*/check.json")))
    result["physical_CA_checks_this_execution"] = (
        result["physical_CA_checks_total"] - previous_count
    )
    result["seed_plan_results"] = seeds
    result["elapsed_s_this_execution"] = time.monotonic() - started
    result["bounds"] = {
        "minimum_count_lower": result["minimum_count_lower_bound"],
        "feasible_count_upper": None if result["best_ids"] is None else len(result["best_ids"]),
        "incumbent_nominal_area_m2": None
        if result["best_ids"] is None
        else result["best_objective"][1],
        "same_count_area_lower_bound_m2": None
        if result["best_ids"] is None
        else cuts.bound((), tuple(areas), len(result["best_ids"])),
    }
    result["unresolved_scope"] = {
        "all_eligible_ids_retained_in_decision_space": True,
        "candidate_count": len(areas),
        "maximum_demolitions": config["maximum_demolitions"],
        "layer_total_combinations": {
            str(k): comb(len(areas), k) for k in range(config["maximum_demolitions"] + 1)
        },
        "interpretation": "unvisited_or_unknown_better_plans_remain;area_LB_is_conservative",
    }
    write_json(output / "search-result.json", result)
    export_best(config, output, bundle, inventory, oracle, result, cache)
    print(
        {
            "status": result["status"],
            "best": result["best_ids"],
            "bounds": result["bounds"],
            "physical_CA_checks_total": result["physical_CA_checks_total"],
        },
        flush=True,
    )
    return result


def read_json_value(value):
    import json

    return json.loads(json.dumps(value))


def export_best(config, output, bundle, inventory, oracle, result, cache):
    if result["best_ids"] is None:
        return
    materials = read_json(Path(config["material_binding_path"]))
    library = (
        Path(config["material_binding_path"]).parent / materials["profile_library"]
    ).resolve()
    material_report = validate_material_binding(bundle, materials, library)
    strategy = {**result, "best_declared_cost": None}
    replay = {
        "strategy_result": strategy,
        "best_ids": result["best_ids"],
        "optimality_proven": result["optimality_proven"],
        "new_physical_CA_checks": result["physical_CA_checks_total"],
    }
    inputs = SimpleNamespace(
        inventory=inventory,
        costs=None,
        paths={"imagery_tiff": Path(config["imagery_path"])},
        bundle=bundle,
        experiment={"problem": oracle.problem},
        manifest={
            "spatial_crs": bundle.request.grid.crs,
            "place_name": config["place_name"],
            "pending_data": config["pending_data"],
        },
        receipt={
            "materials": material_report,
            "input_file_sha256": {"imagery_tiff": file_sha256(config["imagery_path"])},
            "frozen_experiment": {k: oracle.problem[k] for k in ("scales_m", "horizon_s")},
        },
    )
    folder = output / "strategy"
    folder.mkdir(exist_ok=True)
    export_strategy(inputs, replay, folder)
    render_strategy_map(inputs, replay, folder, "/System/Library/Fonts/STHeiti Medium.ttc")
    rows = []
    for scenario in oracle.scenarios:
        check = cache.get((oracle.problem_sha256, tuple(result["best_ids"]), scenario))
        if check is None or check.status != "passed":
            raise ValueError("export requires all incumbent protection checks")
        for core in check.evidence["core_outcomes"]:
            rows.append(
                {
                    "scenario": scenario,
                    "source_id": check.evidence["source_id"],
                    "scale_m": check.evidence["scale_m"],
                    **core,
                }
            )
    write_json(folder / "core-outcomes.json", rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, resume=args.resume, prepare_only=args.prepare_only)
