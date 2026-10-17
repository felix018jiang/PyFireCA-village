"""Full-scene confirmation and correctly labelled 90-minute native replays."""

from __future__ import annotations

import argparse
import fcntl
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from run_batuan_adaptive_search import digest, dump
from run_batuan_multiscenario_search import ScenarioOracle, scenario

from pyfireca.planning.adaptive import canonical_plan
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle


def full_batch(study, label, plan_raw, environment_id, selection_sha):
    oracle = ScenarioOracle(study, full=True)
    root = Path(study)
    if digest(json.loads((root / "selection.json").read_text())) != selection_sha:
        raise ValueError("selection changed before full-scene evaluation")
    plan = clearing_plan_from_mapping(plan_raw)
    oracle.preflight(plan)
    rows = [r for r in oracle.rows.values() if r["environment_id"] == environment_id]
    records = [oracle.evaluate(r["scenario_id"], plan, complete=True).to_mapping() for r in rows]
    output = {
        "label": label,
        "environment_id": environment_id,
        "records": records,
        "selection_sha256": selection_sha,
    }
    dump(root / "full-evaluations" / (label + "-" + environment_id + ".json"), output)
    print({"full_batch": label, "environment": environment_id}, flush=True)
    return output


def validate(study, workers):
    study = Path(study)
    selection = json.loads((study / "selection.json").read_text())
    report = (
        json.loads((study / "report.json").read_text())
        if (study / "report.json").exists()
        else None
    )
    sha = digest(selection)
    if report and report["selection_sha256"] != sha:
        raise ValueError("report/selection mismatch")
    protocol = json.loads((study / "protocol.json").read_text())
    envs = sorted({r["environment_id"] for r in protocol["training"] + protocol["held_out"]})
    if selection["protocol_sha256"] != digest(protocol):
        raise ValueError("selection/protocol mismatch")
    result = {"selection_sha256": sha, "scope": "full312x347", "plans": {}}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = [
            pool.submit(full_batch, str(study), label, row["plan"], env, sha)
            for label, row in selection["chosen"].items()
            for env in envs
        ]
        for future in as_completed(tasks):
            future.result()
    for label in selection["chosen"]:
        records = [
            r
            for p in (study / "full-evaluations").glob(label + "-*.json")
            for r in json.loads(p.read_text())["records"]
        ]
        by_id = {r["scenario_id"]: r for r in records}
        expected = {r["scenario_id"] for r in protocol["training"] + protocol["held_out"]}
        if set(by_id) != expected or len(records) != len(expected):
            raise ValueError("full validation scenario coverage mismatch")
        passed = sum(
            r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"] == 0
            and r["diagnostics"]["reached_requested_horizon"]
            for r in records
        )
        edge = any(r["metrics"]["burning_reached_domain_edge"] for r in records)
        match = None
        differences = []
        if report:
            for suite in ("training", "held_out"):
                for crop in report["evaluations"][label][suite]["records"]:
                    full = by_id[crop["scenario_id"]]
                    for key in ("ignited_cells", "ignited_house_count", "ignited_bridge_count"):
                        if full["metrics"][key] != crop["metrics"][key]:
                            differences.append(
                                {
                                    "scenario_id": crop["scenario_id"],
                                    "metric": key,
                                    "crop": crop["metrics"][key],
                                    "full": full["metrics"][key],
                                }
                            )
            match = not differences
        result["plans"][label] = {
            "passed": passed,
            "total": len(records),
            "protected_structures": sorted(
                {r["metrics"]["protected_structure_count"] for r in records}
            ),
            "domain_edge_ignition": edge,
            "crop_full_match": match,
            "differences": differences,
            "all_scenarios_protected": passed == len(records) and not edge,
        }
    dump(study / "full-validation.json", result)
    return result


def replay_task(study, label, raw, sid):
    # A design-only replay prefetch and the completion driver may meet here.
    # Hold one process lock across export so a running bundle is never renamed.
    locks = Path(study) / "replay-locks"
    locks.mkdir(exist_ok=True)
    with (locks / (label + ".lock")).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _replay_task(study, label, raw, sid)


def _replay_task(study, label, raw, sid):
    study = Path(study)
    oracle = ScenarioOracle(study, full=True)
    row = oracle.rows[sid]
    engine = oracle.engine(row["environment_id"])
    plan = clearing_plan_from_mapping(raw)
    record = oracle.evaluate(sid, plan, complete=True)
    output = study / "replays" / label
    marker = output / "replay-complete.json"
    if marker.exists():
        if json.loads(marker.read_text())["evaluation_sha256"] != record.evaluation_sha256:
            raise ValueError("replay identity changed")
    else:
        if output.exists():
            count = 1
            while output.with_name(output.name + f".interrupted-{count}").exists():
                count += 1
            output.rename(output.with_name(output.name + f".interrupted-{count}"))
        request, fraction, treatment = engine.prepare(scenario(row), plan)
        environment = engine.bundle.metadata["protection_environment"]
        metadata = {
            **engine.bundle.metadata,
            "scenario_id": f"batuan-multiscenario-{label}",
            "ignition_source": {
                "row": request.scenario.ignitions[0].row,
                "col": request.scenario.ignitions[0].col,
            },
            "proposed_intervention": treatment,
            "research_objective": "all_design_scenarios_zero_house_and_bridge_then_minimum_area",
            "simulation_scope": "full312x347",
            "input_summary": engine.bundle.metadata["input_summary"]
            + (
                f" 本次为合成压力测试: 网格北来向{environment['wind_from_deg']:g}度, "
                f"等效风速{environment['wind_speed_m_s']:g}m/s, "
                f"点燃温升系数{environment['ignition_rise_factor']:g}, 模拟90分钟。"
                "假设植被清理保留全部建筑; 风只参与静态辐射源倾斜, 不含飞火。"
            ),
        }
        write_v3_case_bundle(
            request,
            output / "case",
            material_labels=engine.bundle.material_labels,
            landcover_labels=engine.bundle.landcover_labels,
            metadata=metadata,
        )
        np.save(output / "cleared_fraction.npy", fraction)
        run_v3_case_bundle(output / "case", output / "run", chunk_frames=4)
    times = np.load(output / "run/outputs/ignition_time_s.npy")
    if int(np.isfinite(times).sum()) != record.metrics["ignited_cells"]:
        raise AssertionError("full replay ignition cells differ from independent evaluation")
    for category, key in (
        ("building", "ignited_house_count"),
        ("fengyuqiao", "ignited_bridge_count"),
    ):
        bids = {
            int(bid)
            for bid in np.unique(engine.bundle.request.building_index[np.isfinite(times)])
            if bid >= 0 and engine.structure_types.get(int(bid), "building") == category
        }
        if len(bids) != record.metrics[key]:
            raise AssertionError("full replay structure count mismatch")
    dump(marker, {"evaluation_sha256": record.evaluation_sha256, "final_metrics_checked": True})
    return {
        "label": label,
        "scenario_id": sid,
        "viewer": f"replays/{label}/run/viewer",
        "evaluation_sha256": record.evaluation_sha256,
        "scope": "full312x347",
    }


def replays(study, workers):
    study = Path(study)
    protocol = json.loads((study / "protocol.json").read_text())
    selection = json.loads((study / "selection.json").read_text())
    if selection["protocol_sha256"] != digest(protocol):
        raise ValueError("selection/protocol mismatch")
    plans = {
        "baseline": canonical_plan("outer_proxy", ()).to_mapping(),
        **{z: v["plan"] for z, v in selection["chosen"].items()},
    }
    sid = protocol["training"][0]["scenario_id"]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = [
            pool.submit(replay_task, str(study), label, plan, sid) for label, plan in plans.items()
        ]
        results = [f.result() for f in tasks]
    dump(study / "replays.json", results)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("study", type=Path)
    p.add_argument("--phase", choices=("full", "replays"), required=True)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    if args.phase == "full":
        validate(args.study, args.workers)
    else:
        replays(args.study, args.workers)
