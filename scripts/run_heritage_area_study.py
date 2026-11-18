"""Resumable full-horizon area/loss experiments with explicit numerical controls.

Every job freezes its source, common refinement set, plans and numerics before
solving. This runner never promotes a finite candidate comparison to a global
optimality certificate. Old archived studies and their caches remain unchanged.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import replace
from itertools import pairwise
from math import isfinite
from pathlib import Path

import numpy as np
from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_fixed_comparison import (
    atomic_json,
    load_fires,
    result_curves,
    write_compact_arrays,
)

from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.fire_loss import fire_loss_from_result
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study
from pyfireca.village.state import VillageCellState
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-area-study-1"
REQUIRED_ARTIFACTS = frozenset(
    {
        "check.json",
        "curves.json",
        "evaluation.json",
        "fire-loss.json",
        "final-state.npz",
        "layout.npz",
        "loss-basis.npz",
    }
)


class CompactTrace:
    """Aggregate selected identities at existing output times; no full-field frames."""

    def __init__(self, layout, ids):
        self.nodes = {i: layout.nodes(i) for i in ids}
        self.area = layout.node_inputs.parameters.covered_area_m2.ravel()
        self.rows = []

    def append(self, snapshot):
        for identifier, nodes in self.nodes.items():
            valid = snapshot.temperature_valid_mask.ravel()[nodes]
            states = snapshot.state.ravel()[nodes]
            self.rows.append(
                {
                    "time_s": snapshot.time_s,
                    "building_id": identifier,
                    "maximum_valid_temperature_K": float(
                        snapshot.surface_temperature_K.ravel()[nodes][valid].max()
                    )
                    if valid.any()
                    else None,
                    "interval_mean_hrr_W": float(snapshot.interval_mean_hrr_W.ravel()[nodes].sum()),
                    "burning_area_m2": float(
                        self.area[nodes][states == int(VillageCellState.BURNING)].sum()
                    ),
                    "remaining_fuel_kg": float(snapshot.fuel_remaining_kg.ravel()[nodes].sum()),
                }
            )


def source_paths():
    return [
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *(
            ROOT / "scripts" / name
            for name in (
                "run_heritage_area_study.py",
                "run_heritage_fixed_comparison.py",
                "run_heritage_roof_study.py",
                "preflight_heritage_exact_expansion.py",
            )
        ),
    ]


def validate_config(config):
    if config.get("schema_version") != SCHEMA or not config.get("jobs"):
        raise ValueError("explicit area-study jobs required")
    names = [j["job_id"] for j in config["jobs"]]
    if len(names) != len(set(names)) or any(
        not isinstance(n, str) or not n or Path(n).name != n or n in {".", ".."} for n in names
    ):
        raise ValueError("unique safe job IDs required")
    for job in config["jobs"]:
        if not job.get("plans") or not job.get("source_fires"):
            raise ValueError("every job requires explicit plans and fires")
        ids = [p["plan_id"] for p in job["plans"]]
        if any(not isinstance(i, str) or not i for i in ids):
            raise ValueError("nonempty plan IDs required")
        sets = [tuple(sorted(p["demolished_ids"])) for p in job["plans"]]
        if len(ids) != len(set(ids)) or len(sets) != len(set(sets)):
            raise ValueError("duplicate physical plans must be consolidated")
        if any(len(s) != len(set(s)) for s in sets):
            raise ValueError("duplicate demolition IDs")
        if any(not isinstance(i, str) or not i for s in sets for i in s):
            raise ValueError("nonempty demolition IDs required")
        if any(
            isinstance(job[k], bool) or not isfinite(job[k]) or job[k] <= 0
            for k in ("horizon_s", "max_dt_s")
        ):
            raise ValueError("positive finite horizon and time step required")
        checkpoints = job.get("checkpoints_s", [job["horizon_s"]])
        if (
            not checkpoints
            or any(isinstance(t, bool) or not isfinite(t) or t < 0 for t in checkpoints)
            or any(a >= b for a, b in pairwise(checkpoints))
            or checkpoints[-1] != job["horizon_s"]
        ):
            raise ValueError("increasing finite checkpoints ending at the horizon required")
        fires = [fire["fire_id"] for fire in job["source_fires"]]
        if len(set(fires)) != len(fires) or any(
            not isinstance(i, str) or not i or Path(i).name != i or i in {".", ".."} for i in fires
        ):
            raise ValueError("unique safe fire IDs required")
        scales = job["roof_scales_m"]
        if (
            not scales
            or len(scales) != len(set(scales))
            or any(isinstance(s, bool) or not isfinite(s) or s <= 0 for s in scales)
        ):
            raise ValueError("unique positive finite roof scales required")
    if (
        isinstance(config["max_new_checks"], bool)
        or not isinstance(config["max_new_checks"], int)
        or config["max_new_checks"] < 1
        or any(
            isinstance(config[k], bool) or not isfinite(config[k]) or config[k] <= 0
            for k in ("max_wall_time_s", "minimum_free_disk_GB")
        )
    ):
        raise ValueError("positive finite run budgets and integer check budget required")


def make_oracle(config_path, job, code_hash):
    bundle, transport, inventory = load_study((config_path.parent / job["study_path"]).resolve())
    request = replace(
        bundle.request, scenario=replace(bundle.request.scenario, max_dt_s=float(job["max_dt_s"]))
    )
    if job.get("transport_overrides"):
        transport = replace(transport, **job["transport_overrides"])
        edges = build_v3_radiative_transport_edges(
            request.grid,
            request.source_mask,
            request.target_mask,
            request.parameters.receiver_area_m2,
            request.parameters.source_height_m,
            request.parameters.receiver_height_m,
            np.ones(request.grid.shape),
            transport,
            ground_elevation_m=request.ground_elevation_m,
            max_edge_count=job["max_radiation_edges"],
        )
        request = replace(request, exposure_edges=edges)
    bundle = replace(bundle, request=request)
    interventions = tuple(sorted({i for p in job["plans"] for i in p["demolished_ids"]}))
    if not set(interventions) <= set(inventory.candidate_ids):
        raise ValueError("unqualified intervention candidate")
    fires, references = load_fires(config_path, job, interventions)
    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=interventions,
        refinement_ids=tuple(job.get("refinement_ids", interventions)),
        maximum_demolitions=max(len(p["demolished_ids"]) for p in job["plans"]),
        fires=fires,
        code_version=code_hash,
        horizon_s=job["horizon_s"],
        scales_m=tuple(job["roof_scales_m"]),
        quadrature_spacing_m=job["quadrature_spacing_m"],
        phase_fraction=tuple(job.get("phase_fraction", (0.0, 0.0))),
        cell_size_by_id=job.get("cell_size_by_id"),
        experimental_numerics=True,
        max_nodes=job["max_nodes"],
        max_radiation_edges=job["max_radiation_edges"],
        core_overlap_policy=job.get("core_overlap_policy", "reject"),
        stop_at_first_core_ignition=False,
    )
    if job.get("trace_ids"):
        oracle.snapshot_sink_factory = lambda layout: CompactTrace(layout, job["trace_ids"])
    return oracle, references


def prepare(config_path, output):
    config = read_json(config_path)
    validate_config(config)
    # Capture the list exactly once. Parallel development can add a module while
    # a long geometry preflight is running; it must not change this frozen set.
    paths = tuple(source_paths())
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in paths}
    header = {"config_sha256": file_sha256(config_path), "source_sha256": hashes}
    output.mkdir(parents=True, exist_ok=True)
    if (output / "experiment.json").exists():
        raise ValueError("experiment already prepared; resume its frozen snapshot")
    cache_path = output / "preflight-cache.json"
    if cache_path.exists():
        cache = read_json(cache_path)
        if cache.get("header") != header:
            raise ValueError("partial preparation configuration/source changed")
    else:
        if any(output.iterdir()):
            raise ValueError("nonempty output lacks a compatible preparation cache")
        cache = {"header": header, "geometry": {}}
        atomic_json(cache_path, cache)
    # Freeze before preflight and verify the captured files again afterwards.
    for path in paths:
        target = output / "source-snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if file_sha256(target) != hashes[str(path.relative_to(ROOT))]:
                raise ValueError("partial preparation snapshot changed")
        else:
            shutil.copy2(path, target)
        if file_sha256(target) != hashes[str(path.relative_to(ROOT))]:
            raise ValueError("source changed while freezing")
    references = {}
    jobs = []
    preflight, geometry_cache = [], cache["geometry"]
    for job in config["jobs"]:
        oracle, refs = make_oracle(config_path, job, content_hash(hashes))
        references.update(refs)
        study = (config_path.parent / job["study_path"]).resolve()
        references[str(study)] = file_sha256(study)
        jobs.append(
            {
                "job_id": job["job_id"],
                "problem_sha256": oracle.problem_sha256,
                "problem": oracle.problem,
                "checks": len(job["plans"]) * len(oracle.scenarios),
            }
        )
        for fire_id, fire in oracle.fires.items():
            for scale in oracle.scales_m:
                key = content_hash(
                    [
                        str((config_path.parent / job["study_path"]).resolve()),
                        fire["refined_ids"],
                        [f.to_mapping() for f in fire["footprints"].values()],
                        scale,
                        oracle.phase_fraction,
                        oracle.cell_size_by_id,
                        job.get("transport_overrides", {}),
                        oracle.problem_sha256,
                    ]
                )
                if key not in geometry_cache:
                    layout = refine_roof_layout(
                        oracle.base,
                        oracle.inventory,
                        cell_size_m=scale,
                        refined_ids=fire["refined_ids"],
                        footprints=tuple(fire["footprints"].values()),
                        phase_fraction=oracle.phase_fraction,
                        cell_size_by_id=oracle.cell_size_by_id,
                        max_nodes=oracle.max_nodes,
                    )
                    bounds = edge_bounds(layout, oracle.transport, fire["refined_ids"])
                    geometry_cache[key] = {"nodes": len(layout.owners), **bounds}
                    atomic_json(cache_path, cache)
                    del layout
                row = {
                    "job_id": job["job_id"],
                    "fire_id": fire_id,
                    "scale_m": scale,
                    **geometry_cache[key],
                }
                row["passed"] = (
                    row["nodes"] <= oracle.max_nodes
                    and max(row["raw_point_edges"], row["integrated_edges_upper_bound"])
                    <= oracle.max_radiation_edges
                )
                preflight.append(row)
                atomic_json(
                    output / "preflight.json",
                    {"rows": preflight, "passed": all(r["passed"] for r in preflight)},
                )
                print({"preflight": row}, flush=True)
                if not row["passed"]:
                    write_json(output / "preflight.json", {"rows": preflight, "passed": False})
                    raise ValueError("capacity preflight failed before any CA")
        del oracle
    experiment = {
        "schema_version": SCHEMA,
        "config": config,
        "config_sha256": header["config_sha256"],
        "source_sha256": hashes,
        "references_sha256": references,
        "jobs": jobs,
        "global_optimality_proven": False,
    }
    if any(file_sha256(path) != hashes[str(path.relative_to(ROOT))] for path in paths):
        raise ValueError("captured source changed during preparation")
    if file_sha256(config_path) != header["config_sha256"]:
        raise ValueError("configuration changed during preparation")
    if any(file_sha256(path) != digest for path, digest in references.items()):
        raise ValueError("input changed during preparation")
    write_json(output / "experiment.json", experiment)
    write_json(output / "preflight.json", {"rows": preflight, "passed": True})
    atomic_json(
        output / "execution-budget.json", {"elapsed_s": 0.0, "active": None, "started_checks": 0}
    )
    print(
        {"prepared_jobs": len(jobs), "planned_checks": sum(j["checks"] for j in jobs)}, flush=True
    )


def read_complete(folder, problem_hash, *, plan_id=None, demolished_ids=None, scenario_id=None):
    manifest = read_json(folder / "complete.json")
    if manifest["problem_sha256"] != problem_hash:
        raise ValueError("cached physical problem differs")
    if any(
        expected is not None and manifest.get(key) != expected
        for key, expected in (
            ("plan_id", plan_id),
            ("demolished_ids", None if demolished_ids is None else list(demolished_ids)),
            ("scenario_id", scenario_id),
        )
    ):
        raise ValueError("cached plan/scenario identity differs")
    if not manifest.get("files_sha256", {}).keys() >= REQUIRED_ARTIFACTS:
        raise ValueError("cache lacks a required artifact hash")
    for name, digest in manifest["files_sha256"].items():
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("unsafe cached artifact path")
        if not (folder / name).is_file() or file_sha256(folder / name) != digest:
            raise ValueError("cached artifact changed")
    check = read_json(folder / "check.json")
    if any(check.get(key) != manifest.get(key) for key in ("demolished_ids", "scenario_id")):
        raise ValueError("cached check/manifest identity differs")
    if check.get("status") not in {"passed", "failed", "unknown"}:
        raise ValueError("unsupported cached check status")
    return check


def expected_checks(job):
    for fire in job["source_fires"]:
        for scale in job["roof_scales_m"]:
            scenario = f"{fire['fire_id']}-h{scale:g}"
            for plan in job["plans"]:
                ids = tuple(sorted(plan["demolished_ids"]))
                yield plan, ids, scenario, content_hash([list(ids), scenario])


def observation_status(folder, check, horizon_s):
    curves = read_json(folder / "curves.json")
    record = read_json(folder / "evaluation.json")
    losses = read_json(folder / "fire-loss.json")
    with np.load(folder / "final-state.npz", allow_pickle=False) as state:
        end = float(state["time_s"])
    times = [end, curves.get("final_time_s"), losses.get("saved_final_time_s")]
    if any(t is None or not isfinite(t) for t in times) or not np.allclose(
        times, end, rtol=0, atol=1e-8
    ):
        raise ValueError("saved final-state/curve/loss times disagree")
    checkpoints = curves.get("checkpoints", [])
    reached = end >= horizon_s - 1e-8
    valid = bool(
        check["status"] in {"passed", "failed"}
        and not record.get("diagnostics", {}).get("invalid_reasons", ["missing_diagnostics"])
        and reached
        and check.get("evidence", {}).get("reached_horizon") is True
        and check["evidence"].get("simulated_until_s", -1) >= horizon_s - 1e-8
        and checkpoints
        and checkpoints[-1].get("time_s") == horizon_s
        and all(row.get("valid_observation") is True for row in checkpoints)
    )
    return valid, reached, curves, record


def summarize(output):
    experiment = read_json(output / "experiment.json")
    rows = []
    planned = sum(j["checks"] for j in experiment["jobs"])
    for frozen in experiment["jobs"]:
        job = next(j for j in experiment["config"]["jobs"] if j["job_id"] == frozen["job_id"])
        expected = list(expected_checks(job))
        if len(expected) != frozen["checks"]:
            raise ValueError("frozen planned check count differs from the declared matrix")
        for plan, ids, scenario, key in expected:
            folder = output / frozen["job_id"] / "evaluations" / key
            if not (folder / "complete.json").is_file():
                continue
            check = read_complete(
                folder,
                frozen["problem_sha256"],
                plan_id=plan["plan_id"],
                demolished_ids=ids,
                scenario_id=scenario,
            )
            valid, reached, curves, record = observation_status(folder, check, job["horizon_s"])
            rows.append(
                {
                    "job_id": frozen["job_id"],
                    **check,
                    "plan_id": plan["plan_id"],
                    "valid_full_horizon_observation": valid,
                    "reached_horizon": reached,
                    "endpoint": curves["checkpoints"][-1] if curves.get("checkpoints") else None,
                    "cleared_area_m2": record["intervention"]["cleared_roof_union_area_m2"],
                    "folder": str(folder.relative_to(output)),
                }
            )
    report = {
        "schema_version": "heritage-area-summary-1",
        "planned_checks": planned,
        "completed_checks": len(rows),
        "valid_completed_checks": sum(r["valid_full_horizon_observation"] for r in rows),
        "all_artifacts_persisted": len(rows) == planned,
        "complete": len(rows) == planned and all(r["valid_full_horizon_observation"] for r in rows),
        "all_evaluations_reached_horizon": len(rows) == planned
        and all(r["reached_horizon"] for r in rows),
        "global_optimality_proven": False,
        "checks": rows,
    }
    atomic_json(output / "summary.json", report)
    return report


def resume(config_path, output):
    snapshot = output / "source-snapshot/scripts" / Path(__file__).name
    if Path(__file__).resolve() != snapshot.resolve():
        env = {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")}
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(snapshot),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            env,
        )
    experiment = read_json(output / "experiment.json")
    config = read_json(config_path)
    validate_config(config)
    if config != experiment["config"] or file_sha256(config_path) != experiment["config_sha256"]:
        raise ValueError("frozen configuration changed")
    for path, digest in experiment["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("frozen input changed")
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in source_paths()}
    if hashes != experiment["source_sha256"]:
        raise ValueError("frozen source changed")
    budget = read_json(output / "execution-budget.json")
    if budget["active"] is not None:
        raise ValueError("unclosed previous execution budget; inspect before recovery")
    if not isfinite(budget["elapsed_s"]) or budget["elapsed_s"] < 0:
        raise ValueError("invalid cumulative execution time")
    count = summarize(output)["completed_checks"]
    attempted = budget.get("started_checks", count)
    if isinstance(attempted, bool) or not isinstance(attempted, int) or attempted < 0:
        raise ValueError("invalid cumulative started check count")
    budget["started_checks"] = max(attempted, count)
    started, previous = time.monotonic(), budget["elapsed_s"]
    budget["active"] = {"pid": os.getpid()}

    def save_budget():
        budget["elapsed_s"] = previous + time.monotonic() - started
        atomic_json(output / "execution-budget.json", budget)

    save_budget()
    try:
        if (
            budget["started_checks"] >= config["max_new_checks"]
            or previous >= config["max_wall_time_s"]
        ):
            return summarize(output)
        for job in config["jobs"]:
            oracle, _ = make_oracle(config_path, job, content_hash(hashes))
            frozen = next(j for j in experiment["jobs"] if j["job_id"] == job["job_id"])
            if oracle.problem_sha256 != frozen["problem_sha256"]:
                raise ValueError("physical inputs changed since preparation")

            def persist(ids, scenario, check, record, layout, result, *, job=job, oracle=oracle):
                folder = (
                    output / job["job_id"] / "evaluations" / content_hash([list(ids), scenario])
                )
                pending = folder.with_name(folder.name + ".pending")
                pending.mkdir(parents=True, exist_ok=False)
                losses = fire_loss_from_result(
                    layout,
                    result,
                    oracle.inventory,
                    demolished_ids=ids,
                    source_ids=record.metrics["actual_source_ids"],
                    original_node_owners=oracle._engine.layout.owners,
                )
                check.evidence["loss_metrics"] = {
                    "ignited_roof_area_m2": losses["physical"]["roof"][
                        "cumulative_ignited_covered_area_m2"
                    ],
                    "ignited_total_area_m2": losses["physical"]["all"][
                        "cumulative_ignited_covered_area_m2"
                    ],
                    "ignited_building_count_upper": losses["annotation_counts"][
                        "ignited_annotation_attribution_count"
                    ]["upper"],
                }
                write_json(pending / "fire-loss.json", losses)
                write_json(pending / "evaluation.json", record.to_mapping())
                write_json(
                    pending / "check.json",
                    {
                        "scenario_id": scenario,
                        "demolished_ids": list(ids),
                        "status": check.status,
                        "evidence": check.evidence,
                    },
                )
                write_json(
                    pending / "curves.json",
                    result_curves(
                        record, layout, result, job.get("checkpoints_s", [job["horizon_s"]])
                    ),
                )
                write_compact_arrays(pending, layout, result)
                if oracle.last_snapshot_sink is not None:
                    write_json(pending / "selected-trace.json", oracle.last_snapshot_sink.rows)
                np.savez_compressed(
                    pending / "loss-basis.npz",
                    baseline_covered_area_m2=layout.node_inputs.parameters.covered_area_m2,
                    initial_fuel_kg=result.initial_fuel_kg,
                    post_intervention_covered_area_m2=layout.node_request.parameters.covered_area_m2,
                    landcover_index=layout.node_request.landcover_index,
                )
                plan = next(p for p in job["plans"] if tuple(sorted(p["demolished_ids"])) == ids)
                files = {p.name: file_sha256(p) for p in pending.iterdir() if p.is_file()}
                write_json(
                    pending / "complete.json",
                    {
                        "problem_sha256": oracle.problem_sha256,
                        "plan_id": plan["plan_id"],
                        "scenario_id": scenario,
                        "demolished_ids": list(ids),
                        "files_sha256": files,
                    },
                )
                pending.rename(folder)
                save_budget()
                print(
                    {
                        "completed_job": job["job_id"],
                        "scenario": scenario,
                        "plan": plan["plan_id"],
                        "status": check.status,
                        "solve_s": round(check.evidence["solve_s"], 2),
                    },
                    flush=True,
                )

            oracle.on_evaluation = persist
            for scenario in oracle.scenarios:
                for plan in job["plans"]:
                    ids = tuple(sorted(plan["demolished_ids"]))
                    folder = (
                        output / job["job_id"] / "evaluations" / content_hash([list(ids), scenario])
                    )
                    if folder.exists():
                        read_complete(
                            folder,
                            oracle.problem_sha256,
                            plan_id=plan["plan_id"],
                            demolished_ids=ids,
                            scenario_id=scenario,
                        )
                        continue
                    save_budget()
                    if (
                        budget["started_checks"] >= config["max_new_checks"]
                        or budget["elapsed_s"] >= config["max_wall_time_s"]
                    ):
                        return summarize(output)
                    if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
                        raise RuntimeError("declared minimum free disk reached")
                    print(
                        {
                            "starting_job": job["job_id"],
                            "scenario": scenario,
                            "plan": plan["plan_id"],
                            "completed_checks": count,
                        },
                        flush=True,
                    )
                    # Count the attempt before entering the solver, so a failure
                    # before persistence cannot silently reset the CA budget.
                    budget["started_checks"] += 1
                    save_budget()
                    oracle(ids, scenario)
                    count += 1
                    summarize(output)
            del oracle
    finally:
        save_budget()
        budget["active"] = None
        atomic_json(output / "execution-budget.json", budget)
    return summarize(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    config_path, output = args.config.resolve(), args.output.resolve()
    if args.prepare_only:
        prepare(config_path, output)
    else:
        report = summarize(output) if args.summarize else resume(config_path, output)
        print(
            {k: report[k] for k in ("complete", "completed_checks", "planned_checks")}, flush=True
        )


if __name__ == "__main__":
    main()
