"""Reuse fully simulated, zero-loss screening runs for complete confirmation.

No partial trajectory is promoted. The source evaluation identity is checked
against the same case, model, scenario, geometry and masks. The stop predicate
could not have changed a run that reached its horizon with zero structures
ignited. Preserve the source, record reuse explicitly, and checksum both files.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from run_batuan_building_pairs import dump
from run_batuan_joint_search import Oracle, digest

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.joint import plan_from_mapping


def evaluation_identity(engine, scenario, plan, screening):
    value = hashlib.sha256()
    for part in (
        engine.case_sha256,
        engine.code_version,
        asdict(engine.transport),
        asdict(scenario),
        plan.to_mapping(),
        engine.structure_types,
    ):
        value.update(json.dumps(part, sort_keys=True).encode())
    value.update(engine.zones[plan.zone_id].tobytes())
    if screening:
        value.update(b"stop_at_first_structure_ignition")
    return value.hexdigest()


def complete_from_screen(engine, scenario, plan, record):
    if (
        record.evaluation_sha256 != evaluation_identity(engine, scenario, plan, True)
        or record.case_sha256 != engine.case_sha256
        or record.scenario_id != scenario.scenario_id
        or record.intervention_id != plan.plan_id
        or record.horizon_s != scenario.horizon_s
    ):
        raise ValueError("screening source identity mismatch")
    if (
        not record.diagnostics["reached_requested_horizon"]
        or record.diagnostics["simulated_until_s"] < scenario.horizon_s
        or not record.diagnostics["stop_at_first_structure_ignition"]
        or not record.diagnostics["parent_case_unchanged"]
        or record.metrics["ignited_structure_count"] != 0
    ):
        raise ValueError("only physically complete zero-structure runs can be reused")
    return replace(
        record,
        evaluation_sha256=evaluation_identity(engine, scenario, plan, False),
        diagnostics={
            **record.diagnostics,
            "stop_at_first_structure_ignition": False,
            "physical_run_reused": True,
            "screening_evaluation_sha256": record.evaluation_sha256,
        },
    )


def reuse(output):
    oracle = Oracle(output)
    made = 0
    for path in list(oracle.cache.glob("*.json")):
        raw = json.loads(path.read_text())
        record = EvaluationResult(**raw["record"])
        if (
            not record.diagnostics["reached_requested_horizon"]
            or not record.diagnostics["stop_at_first_structure_ignition"]
            or record.metrics["ignited_structure_count"] != 0
        ):
            continue
        if raw["signature"] != path.stem or raw["record_sha256"] != digest(raw["record"]):
            raise ValueError("source cache checksum mismatch")
        row = oracle.rows[record.scenario_id]
        scenario = EvaluationScenario(record.scenario_id, (Ignition(tuple(row["point_m"])),), 5400)
        engine = oracle.engine(row["environment_id"])
        plan = plan_from_mapping(record.intervention)
        target = complete_from_screen(engine, scenario, plan, record)
        signature = digest(
            {
                "case": engine.case_sha256,
                "code": engine.code_version,
                "plan": plan.to_mapping(),
                "scenario": asdict(scenario),
                "transport": asdict(engine.transport),
                "complete": True,
            }
        )
        destination = oracle.cache / (signature + ".json")
        with destination.with_suffix(".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            if destination.exists():
                continue
            dump(
                destination,
                {
                    "signature": signature,
                    "record": target.to_mapping(),
                    "record_sha256": digest(target.to_mapping()),
                    "reused_from_signature": path.stem,
                    "reused_from_record_sha256": raw["record_sha256"],
                },
            )
            made += 1
    dump(
        Path(output) / "completed-screen-reuse.json",
        {
            "new_complete_cache_records": made,
            "partial_runs_promoted": 0,
            "rule": "identical request; actual horizon complete; zero protected ignition",
        },
    )
    print(json.dumps({"completed_screen_records_reused": made}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    reuse(parser.parse_args().output.resolve())
