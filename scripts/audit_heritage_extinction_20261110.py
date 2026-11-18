"""Rebuild one archived numerical request and certify its saved state; zero CA.

This read-only audit reconstructs geometry and operators but replaces the CA
entry point before invoking the oracle. It preserves the actual archived time;
the last burnout time is never substituted for an observed solver boundary.
"""

from __future__ import annotations

import argparse
import json
import time
from contextlib import suppress
from pathlib import Path

import numpy as np
from run_heritage_area_study import make_oracle

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[1]


class _PreparedWithoutSimulation(Exception):
    pass


def audit(config_path, run_root, job_id, output):
    config = read_json(config_path)
    jobs = [job for job in config["jobs"] if job["job_id"] == job_id]
    if len(jobs) != 1:
        raise ValueError("exactly one declared numerical job required")
    folders = sorted((run_root / job_id / "evaluations").iterdir())
    folders = [folder for folder in folders if (folder / "complete.json").is_file()]
    if len(folders) != 1:
        raise ValueError("this audit requires exactly one complete evaluation in the job")
    folder = folders[0]
    manifest = read_json(folder / "complete.json")
    if not manifest["files_sha256"].keys() >= {
        "evaluation.json",
        "layout.npz",
        "final-state.npz",
    }:
        raise ValueError("archive lacks required original state and layout artifacts")
    for name, digest in manifest["files_sha256"].items():
        if file_sha256(folder / name) != digest:
            raise ValueError("archive checksum changed: " + name)
    captured = {}

    def no_simulation(engine, scenario, plan, **kwargs):
        layout, contact, _ = engine.prepare(scenario, plan)
        captured.update(layout=layout, contact=contact, engine=engine)
        raise _PreparedWithoutSimulation

    started = time.monotonic()
    original = RoofHeritageEvaluationEngine.simulate
    RoofHeritageEvaluationEngine.simulate = no_simulation
    try:
        oracle, _ = make_oracle(config_path, jobs[0], "read-only-extinction-audit")
        with suppress(_PreparedWithoutSimulation):
            oracle(tuple(manifest["demolished_ids"]), manifest["scenario_id"])
    finally:
        RoofHeritageEvaluationEngine.simulate = original
    if not captured:
        raise ValueError("archive could not be reconstructed without simulation")
    layout = captured["layout"]
    record = read_json(folder / "evaluation.json")
    if captured["engine"].case_sha256 != record["case_sha256"]:
        raise ValueError("current parent request differs from the archived parent")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        for key, actual in (
            ("physical_cell_id", layout.cell_id),
            ("component_id", np.asarray(layout.component_id)),
            ("covered_area_m2", layout.node_request.parameters.covered_area_m2.ravel()),
            ("xy_m", layout.xy_m),
            ("area_fraction", layout.area_fraction),
        ):
            if not np.array_equal(saved[key], actual):
                raise ValueError("current node layout differs from the archived " + key)
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        kwargs = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
        kwargs["time_s"] = float(kwargs["time_s"])
        kwargs["step_index"] = int(kwargs["step_index"])
        state = V3State(**kwargs)
    request = layout.node_request
    decision = certify_extinction_v3(
        request,
        state,
        post_apply_events_boundary=True,
        contact=captured["contact"],
    )
    valid = state.temperature_valid_mask
    water = state.heated_water_remaining_kg[valid]
    area = request.parameters.covered_area_m2[valid]
    changed = (water / area) * area != water
    history = []
    for _ in range(10):
        history.append(int(np.count_nonzero((water / area) * area != water)))
        water = (water / area) * area
    report = {
        "schema_version": "heritage-extinction-real-field-audit-1",
        "job_id": job_id,
        "source_manifest_path": str(folder / "complete.json"),
        "source_manifest_sha256": file_sha256(folder / "complete.json"),
        "layout_and_parent_input_equality": True,
        "actual_time_s": state.time_s,
        "physical_node_count": int(state.state.size),
        "new_CA_checks": 0,
        "no_CA_checks": True,
        "water_roundtrip_changing_nodes": int(changed.sum()),
        "water_roundtrip_change_count_over_ten_algebraic_reapplications": history,
        "decision": decision,
        "geometry_and_audit_wall_s": time.monotonic() - started,
        "scope": "existing archived state only; no retrospective earlier stopping claim",
        "script_sha256": file_sha256(Path(__file__)),
    }
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default="h05-dt05")
    parser.add_argument(
        "--run-root", type=Path, default=ROOT / "runs/research/heritage-area-numerics-20261110-v2"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "examples/research/heritage/area-20261110/pingyan-bridge-numerics.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/results/heritage-extinction-real-field-20261110.json",
    )
    args = parser.parse_args()
    report = audit(args.config.resolve(), args.run_root.resolve(), args.job, args.output.resolve())
    print(
        json.dumps(
            {
                "output": str(args.output),
                "closed": report["decision"]["closed"],
                "reasons": report["decision"]["rejection_reasons"],
                "new_CA_checks": 0,
                "actual_time_s": report["actual_time_s"],
            }
        )
    )


if __name__ == "__main__":
    main()
