"""Frozen, same-source multi-arm roof comparisons with full-horizon observation.

This is a fixed experiment, not a candidate search. Prepare once, then resume
from the saved source snapshot. Each physical fire/scale shares one geometric
layout across arms. Checkpoint curves are reconstructed from first-ignition and
burnout clocks, not from independently rerun simulations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_roof_study import save_evaluation

from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study
from pyfireca.village.state import VillageCellState

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-fixed-comparison-1"


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    temporary.replace(path)


def normalized(value):
    return json.loads(json.dumps(value))


def validate_config(config):
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported fixed comparison schema")
    horizon = config["horizon_s"]
    if not math.isfinite(horizon) or horizon <= 0:
        raise ValueError("positive finite horizon required")
    checkpoints = config["checkpoints_s"]
    if (
        not checkpoints
        or checkpoints != sorted(set(checkpoints))
        or any(not math.isfinite(t) or t <= 0 or t > horizon for t in checkpoints)
        or checkpoints[-1] != horizon
    ):
        raise ValueError("checkpoints must increase, be positive and end at the horizon")
    if config["roof_scales_m"] != [1.0, 0.5]:
        raise ValueError("current comparison requires both frozen roof scales")
    for key in ("max_nodes", "max_radiation_edges", "max_total_checks"):
        if isinstance(config[key], bool) or not isinstance(config[key], int) or config[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("max_wall_time_s", "minimum_free_disk_GB"):
        if not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f"{key} must be positive and finite")
    plans = config["plans"]
    if not plans or plans[0]["demolished_ids"]:
        raise ValueError("first plan must be the no-demolition baseline")
    names = [p["plan_id"] for p in plans]
    fires = config["source_fires"]
    fire_ids = [f["fire_id"] for f in fires]
    if len(names) != len(set(names)) or not fires or len(fire_ids) != len(set(fire_ids)):
        raise ValueError("unique nonempty plan/fire identities required")
    for name in names + fire_ids:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("safe nonempty plan/fire names required")
    sets = []
    for plan in plans:
        ids = plan["demolished_ids"]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate demolished IDs")
        sets.append(tuple(sorted(ids)))
    if len(sets) != len(set(sets)):
        raise ValueError("duplicate physical arms must be consolidated before simulation")
    return tuple(sorted({i for ids in sets for i in ids}))


def load_fires(config_path, config, intervention_ids):
    fires, references = [], {}
    for item in config["source_fires"]:
        path = (config_path.parent / item["protocol_path"]).resolve()
        protocol = read_json(path)
        expected = content_hash({k: v for k, v in protocol.items() if k != "protocol_sha256"})
        if protocol.get("protocol_sha256") != expected:
            raise ValueError("source protocol hash mismatch")
        draws = [d for d in protocol["draws"] if d["draw_id"] == item["draw_id"]]
        if len(draws) != 1:
            raise ValueError("exactly one frozen draw required per fire")
        draw = draws[0]
        if not draw["priority_ids"] or draw["priority_ids"][0] != draw["baseline_source_id"]:
            raise ValueError("baseline source must be first in frozen priority")
        if draw["baseline_source_id"] in intervention_ids:
            raise ValueError("same-source experiment cannot remove a source in any arm")
        protocol = {**protocol, "draws": draws}
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        fires.append({"fire_id": item["fire_id"], "protocol": protocol})
        references[str(path)] = file_sha256(path)
    return fires, references


def checkpoint_metrics(ignition_times, burnout_times, covered_area, roof_mask, checkpoints):
    """Count physical nodes once, irrespective of shared annotation ownership."""
    ignition = np.asarray(ignition_times).ravel()
    burnout = np.asarray(burnout_times).ravel()
    area = np.asarray(covered_area).ravel()
    roofs = np.asarray(roof_mask, dtype=bool).ravel()
    if not (ignition.shape == burnout.shape == area.shape == roofs.shape):
        raise ValueError("checkpoint arrays must share a physical node layout")
    rows = []
    for checkpoint in checkpoints:
        ignited = np.isfinite(ignition) & (ignition <= checkpoint)
        # V3 uses NaN until burnout has actually occurred. A missing burnout
        # clock is therefore still active after ignition, not already extinct.
        active = ignited & (~np.isfinite(burnout) | (burnout > checkpoint))
        rows.append(
            {
                "time_s": checkpoint,
                "ignited_physical_node_count": int(ignited.sum()),
                "cumulative_ignited_covered_area_m2": float(area[ignited].sum()),
                "cumulative_ignited_roof_covered_area_m2": float(area[ignited & roofs].sum()),
                "active_burning_node_count": int(active.sum()),
                "active_burning_covered_area_m2": float(area[active].sum()),
            }
        )
    return rows


def result_curves(record, layout, result, checkpoints):
    state = result.final_state
    area = layout.node_request.parameters.covered_area_m2.ravel()
    roofs = np.asarray([bool(ids) for ids in layout.owners])
    rows = checkpoint_metrics(state.ignition_time_s, state.burnout_time_s, area, roofs, checkpoints)
    times = record.metrics["building_first_ignitions"]
    ordinary = set(record.metrics["retained_ordinary_ids"])
    sources = set(record.metrics["actual_source_ids"])
    for row in rows:
        checkpoint = row["time_s"]
        row["core_outcomes"] = [
            {
                "building_id": core["building_id"],
                "role": core["role"],
                "ignited": core["first_ignition_s"] is not None
                and core["first_ignition_s"] <= checkpoint,
                "first_ignition_s": core["first_ignition_s"]
                if core["first_ignition_s"] is not None and core["first_ignition_s"] <= checkpoint
                else None,
            }
            for core in record.metrics["core_outcomes"]
        ]
        row["core_joint_failure"] = any(c["ignited"] for c in row["core_outcomes"])
        row["ignited_retained_ordinary_ids"] = sorted(
            i for i in ordinary if i in times and times[i] <= checkpoint
        )
        row["ignited_secondary_ordinary_ids"] = sorted(
            set(row["ignited_retained_ordinary_ids"]) - sources
        )
        row["valid_observation"] = bool(
            not record.diagnostics["invalid_reasons"] and state.time_s >= checkpoint
        )
    return {
        "checkpoints": rows,
        "building_first_ignitions": times,
        "retained_ordinary_ids": sorted(ordinary),
        "ambiguous_ordinary_annotation_ids": record.metrics["ambiguous_ordinary_annotation_ids"],
        "final_active_burning_node_count": int(
            (state.state.ravel() == int(VillageCellState.BURNING)).sum()
        ),
        "final_remaining_fuel_kg": float(state.fuel_remaining_kg.sum()),
        "final_time_s": float(state.time_s),
        "interpretation": "roof-patch ignition; not complete building destruction or annual risk",
        "area_accounting": "sum physical covered area once; never sum overlapping building areas",
    }


def write_compact_arrays(folder, layout, result):
    np.savez_compressed(
        folder / "final-state.npz",
        **{k: getattr(result.final_state, k) for k in result.final_state.__dataclass_fields__},
        ignition_cause=result.ignition_cause,
    )
    offsets = np.zeros(len(layout.owners) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum([len(ids) for ids in layout.owners])
    np.savez_compressed(
        folder / "layout.npz",
        physical_cell_id=layout.cell_id,
        xy_m=layout.xy_m,
        component_id=np.asarray(layout.component_id),
        area_fraction=layout.area_fraction,
        covered_area_m2=layout.node_request.parameters.covered_area_m2.ravel(),
        owner_offsets=offsets,
        owner_ids=np.asarray([i for ids in layout.owners for i in ids], dtype=str),
        physical_grid_shape=np.asarray(layout.physical_request.grid.shape),
    )


def verify_result(folder, problem_hash, ids, scenario):
    manifest = read_json(folder / "complete.json")
    if (
        manifest["problem_sha256"] != problem_hash
        or manifest["demolished_ids"] != list(ids)
        or manifest["scenario_id"] != scenario
    ):
        raise ValueError("cached fixed comparison identity mismatch")
    required = {"evaluation.json", "check.json", "curves.json", "final-state.npz", "layout.npz"}
    if not required <= manifest["files_sha256"].keys():
        raise ValueError("cached result lacks required artifact hashes")
    for relative, digest in manifest["files_sha256"].items():
        path = folder / relative
        if not path.is_file() or file_sha256(path) != digest:
            raise ValueError(f"incomplete or changed cached result: {path}")
    return read_json(folder / "check.json")


def source_paths():
    return [
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *(
            ROOT / "scripts" / name
            for name in (
                "run_heritage_fixed_comparison.py",
                "run_heritage_roof_study.py",
                "preflight_heritage_exact_expansion.py",
            )
        ),
    ]


def build_experiment(config_path):
    config = read_json(config_path)
    interventions = validate_config(config)
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study_path)
    if not set(interventions) <= set(inventory.candidate_ids):
        raise ValueError("all compared actions must be eligible inventory candidates")
    fires, references = load_fires(config_path, config, interventions)
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in source_paths()}
    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=interventions,
        refinement_ids=interventions,
        maximum_demolitions=max(len(p["demolished_ids"]) for p in config["plans"]),
        fires=fires,
        code_version=content_hash(hashes),
        horizon_s=config["horizon_s"],
        scales_m=tuple(config["roof_scales_m"]),
        quadrature_spacing_m=config.get("quadrature_spacing_m", 0.5),
        max_nodes=config["max_nodes"],
        max_radiation_edges=config["max_radiation_edges"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
        stop_at_first_core_ignition=False,
    )
    if any(len(f["footprints"]) != 1 for f in oracle.fires.values()):
        raise ValueError("fixed comparison must have one unchanged source per fire")
    experiment = normalized(
        {
            "schema_version": SCHEMA,
            "config": config,
            "config_sha256": file_sha256(config_path),
            "study": read_json(study_path),
            "study_sha256": file_sha256(study_path),
            "source_references_sha256": references,
            "source_sha256": hashes,
            "problem": oracle.problem,
            "problem_sha256": oracle.problem_sha256,
            "inventory_eligible_candidate_count": len(inventory.candidate_ids),
            "fixed_comparison_intervention_ids": interventions,
            "planned_CA_checks": len(config["plans"]) * len(oracle.scenarios),
            "optimization": False,
            "independent_new_fire_draws": False,
            "wall_budget_policy": "cumulative measured execution wall time; checked between solves",
        }
    )
    return config, oracle, experiment


def prepare(config_path, output):
    config, oracle, experiment = build_experiment(config_path)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "experiment.json", experiment)
    write_json(output / "inventory.json", oracle.inventory.to_mapping())
    for path in source_paths():
        target = output / "source-snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if file_sha256(target) != experiment["source_sha256"][str(path.relative_to(ROOT))]:
            raise ValueError("producer changed during source snapshot")
    inputs = output / "input-snapshot"
    inputs.mkdir()
    write_json(inputs / "config.json", config)
    write_json(inputs / "study.json", experiment["study"])
    for label, fire in oracle.fires.items():
        write_json(inputs / f"{label}.json", fire["protocol"])
    rows = []
    for label, fire in oracle.fires.items():
        for scale in oracle.scales_m:
            layout = refine_roof_layout(
                oracle.base,
                oracle.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=config["max_nodes"],
            )
            bounds = edge_bounds(layout, oracle.transport, fire["refined_ids"])
            row = {
                "fire_id": label,
                "scale_m": scale,
                "source_id": next(iter(fire["footprints"])),
                "nodes": len(layout.owners),
                **bounds,
                "passed": len(layout.owners) <= config["max_nodes"]
                and max(bounds.values()) <= config["max_radiation_edges"],
            }
            rows.append(row)
            print(row, flush=True)
    report = {
        "rows": rows,
        "all_passed": all(row["passed"] for row in rows),
        "new_CA_checks": 0,
        "planned_CA_checks": experiment["planned_CA_checks"],
    }
    write_json(output / "preflight.json", report)
    atomic_json(output / "execution-budget.json", {"elapsed_s": 0.0, "active": None})
    if not report["all_passed"]:
        raise ValueError("geometry capacity preflight failed; no CA launched")
    return report


def export_summary(output, config, oracle):
    checks, curve_rows = [], []
    for scenario, (fire, scale) in oracle.scenarios.items():
        for plan in config["plans"]:
            ids = tuple(sorted(plan["demolished_ids"]))
            folder = output / "evaluations" / content_hash([list(ids), scenario])
            base = {
                "fire_id": fire,
                "scenario_id": scenario,
                "scale_m": scale,
                "plan_id": plan["plan_id"],
                "demolished_ids": list(ids),
            }
            if not folder.exists():
                checks.append({**base, "status": "unknown", "reason": "not_evaluated"})
                continue
            check = verify_result(folder, oracle.problem_sha256, ids, scenario)
            curves = read_json(folder / "curves.json")
            checks.append({**base, **check, "result_folder": str(folder.relative_to(output))})
            for row in curves["checkpoints"]:
                curve_rows.append({**base, **row})
    all_reached = all(c.get("evidence", {}).get("reached_horizon", False) for c in checks)
    report = {
        "schema_version": "heritage-fixed-comparison-summary-1",
        "problem_sha256": oracle.problem_sha256,
        "complete": all_reached and all(c["status"] in {"passed", "failed"} for c in checks),
        "planned_CA_checks": len(checks),
        "completed_CA_checks": sum(c["status"] != "unknown" for c in checks),
        "persisted_CA_checks": sum("result_folder" in c for c in checks),
        "fire_draw_count": len(oracle.fires),
        "new_independent_fire_draw_count": 0,
        "full_horizon_required": True,
        "all_evaluations_reached_horizon": all_reached,
        "full_horizon_observation": all_reached,
        "checks": checks,
        "checkpoint_curves": curve_rows,
        "interpretation": "paired fixed-source pressure replay; not village-wide risk estimation",
    }
    atomic_json(output / "summary.json", report)
    scalar_keys = [
        "fire_id",
        "scenario_id",
        "scale_m",
        "plan_id",
        "time_s",
        "valid_observation",
        "core_joint_failure",
        "ignited_physical_node_count",
        "cumulative_ignited_covered_area_m2",
        "cumulative_ignited_roof_covered_area_m2",
        "active_burning_node_count",
        "active_burning_covered_area_m2",
    ]
    with (output / "checkpoint-curves.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys)
        writer.writeheader()
        writer.writerows({k: row[k] for k in scalar_keys} for row in curve_rows)
    with (output / "core-checkpoints.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "fire_id",
            "scale_m",
            "plan_id",
            "time_s",
            "valid_observation",
            "building_id",
            "role",
            "ignited",
            "first_ignition_s",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in curve_rows:
            for core in row["core_outcomes"]:
                writer.writerow({**{k: row[k] for k in fields[:5]}, **core})
    return report


def resume(config_path, output):
    snapshot_runner = output / "source-snapshot/scripts" / Path(__file__).name
    if Path(__file__).resolve() != snapshot_runner.resolve():
        if not snapshot_runner.is_file():
            raise ValueError("prepare-only must create a frozen source snapshot first")
        env = {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")}
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(snapshot_runner),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            env,
        )
    config, oracle, experiment = build_experiment(config_path)
    if read_json(output / "experiment.json") != experiment:
        raise ValueError("changed physical problem, source, config or producer; resume refused")
    if not read_json(output / "preflight.json")["all_passed"]:
        raise ValueError("passing frozen capacity preflight is required")
    budget_path = output / "execution-budget.json"
    budget = read_json(budget_path)
    if budget["active"] is not None:
        raise ValueError("prior execution has no closing budget receipt; inspect before resuming")
    if list((output / "evaluations").glob("*.pending")):
        raise ValueError("incomplete evaluation artifacts require inspection before resuming")
    started = time.monotonic()
    previous_elapsed = budget["elapsed_s"]
    budget["active"] = {"pid": os.getpid(), "started_utc": datetime.now(UTC).isoformat()}
    atomic_json(budget_path, budget)

    def update_budget():
        budget["elapsed_s"] = previous_elapsed + time.monotonic() - started
        atomic_json(budget_path, budget)

    def persist(ids, scenario, check, record, layout, result):
        if check.evidence["source_replaced"]:
            raise ValueError("fixed-source comparison unexpectedly replaced the source")
        folder = output / "evaluations" / content_hash([list(ids), scenario])
        pending = folder.with_name(folder.name + ".pending")
        pending.mkdir(parents=True, exist_ok=False)
        write_json(pending / "evaluation.json", record.to_mapping())
        row = {"status": check.status, "evidence": check.evidence}
        write_json(pending / "check.json", row)
        write_json(
            pending / "curves.json", result_curves(record, layout, result, config["checkpoints_s"])
        )
        write_compact_arrays(pending, layout, result)
        if not (output / "native-witness.json").exists():
            save_evaluation(pending / "native", record, layout, result, oracle._engine.last_contact)
        files = {
            str(p.relative_to(pending)): file_sha256(p)
            for p in sorted(pending.rglob("*"))
            if p.is_file()
        }
        write_json(
            pending / "complete.json",
            {
                "problem_sha256": oracle.problem_sha256,
                "demolished_ids": list(ids),
                "scenario_id": scenario,
                "files_sha256": files,
            },
        )
        pending.rename(folder)
        if (folder / "native").exists():
            atomic_json(
                output / "native-witness.json",
                {
                    "folder": str((folder / "native").relative_to(output)),
                    "scenario_id": scenario,
                    "demolished_ids": list(ids),
                    "complete_manifest_sha256": file_sha256(folder / "complete.json"),
                },
            )
        update_budget()
        print(
            {
                "completed": scenario,
                "ids": list(ids),
                "status": check.status,
                "solve_s": round(check.evidence["solve_s"], 2),
                "observed_until_s": check.evidence["simulated_until_s"],
            },
            flush=True,
        )

    oracle.on_evaluation = persist
    try:
        for scenario in oracle.scenarios:
            for plan in config["plans"]:
                ids = tuple(sorted(plan["demolished_ids"]))
                folder = output / "evaluations" / content_hash([list(ids), scenario])
                if folder.exists():
                    verify_result(folder, oracle.problem_sha256, ids, scenario)
                    continue
                count = len(list((output / "evaluations").glob("*/complete.json")))
                update_budget()
                if (
                    count >= config["max_total_checks"]
                    or budget["elapsed_s"] >= config["max_wall_time_s"]
                ):
                    return export_summary(output, config, oracle)
                if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
                    raise RuntimeError("insufficient disk space for fixed comparison")
                print(
                    {
                        "starting": scenario,
                        "plan_id": plan["plan_id"],
                        "completed_CA_checks": count,
                    },
                    flush=True,
                )
                oracle(ids, scenario)
                export_summary(output, config, oracle)
        return export_summary(output, config, oracle)
    finally:
        update_budget()
        budget["active"] = None
        atomic_json(budget_path, budget)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config_path, output = args.config.resolve(), args.output.resolve()
    report = prepare(config_path, output) if args.prepare_only else resume(config_path, output)
    print(
        {
            k: v
            for k, v in report.items()
            if k in {"complete", "all_passed", "planned_CA_checks", "completed_CA_checks"}
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
