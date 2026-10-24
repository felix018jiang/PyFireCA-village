"""Independently rederive bridge trigger evidence from saved cell ignition arrays."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from run_batuan_bridge_mesh import OWNERS, PAIR_IDS, task_request, verify_frozen
from run_batuan_building_pairs import dump, sha
from run_batuan_joint_search import digest

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.observations import build_object_observations


def first_time(times):
    valid = times[np.isfinite(times)]
    return float(valid.min()) if len(valid) else None


def validate_ignition_times(times, shape):
    # The solver stores NaN for cells that never ignite; it is not a time value.
    if times.shape != shape or np.isinf(times).any() or np.any(times < 0):
        raise AssertionError("invalid cell ignition time array")
    if np.any(times[np.isfinite(times)] > 5400 + 1e-8):
        raise AssertionError("ignition outside declared horizon")


def audit(output):
    protocol = verify_frozen(output)
    report = json.loads((output / "report.json").read_text())
    completion = json.loads((output / "completion.json").read_text())
    if completion["report_sha256"] != sha(output / "report.json"):
        raise AssertionError("completed report checksum mismatch")
    tasks = {t["key"]: t for t in protocol["tasks"]}
    if len(report["records"]) != 24 or {r["key"] for r in report["records"]} != set(tasks):
        raise AssertionError("incomplete declared experiment")
    structures = [
        s
        for s in json.loads((output / "buildings.source.json").read_text())["buildings"]
        if s["building_id"] in PAIR_IDS
    ]
    evidence = []
    for row in report["records"]:
        entry = json.loads((output / "records" / f"{row['key']}.json").read_text())
        if entry["record"] != row or entry["record_sha256"] != digest(row):
            raise AssertionError("report/cache evidence mismatch")
        if row["protocol_sha256"] != sha(output / "protocol.json"):
            raise AssertionError("record/protocol mismatch")
        task = tasks[row["key"]]
        if any(row[k] != v for k, v in task.items()):
            raise AssertionError("record differs from declared task")
        _, q = task_request(output, task)
        if request_fingerprint(q) != row["request_sha256"]:
            raise AssertionError("record/request mismatch")
        state = output / row["thermal_state_file"]
        if sha(state) != row["thermal_state_sha256"]:
            raise AssertionError("thermal array checksum mismatch")
        with np.load(state) as saved:
            times = saved["ignition_time_s"]
        validate_ignition_times(times, q.grid.shape)
        if not np.all(times[q.building_index == task["source_owner"]] == 0):
            raise AssertionError("whole owned source roof was not directly ignited")
        guard = np.isfinite(times)
        if guard[[0, -1], :].any() or guard[:, [0, -1]].any():
            raise AssertionError("boundary ignition")
        observer = build_object_observations(
            q, structures, dict(zip(map(str, OWNERS), PAIR_IDS, strict=True))
        )
        obj = next(o for o in observer.objects if o.object_id == row["receiver_owner"])
        own_mask = q.building_index == row["receiver_owner"]
        own, observed = first_time(times[own_mask]), first_time(times.ravel()[obj.cell_ids])
        if (
            own != row["owned_receiver_trigger"]["time_s"]
            or observed != row["annotated_receiver_trigger"]["time_s"]
        ):
            raise AssertionError("trigger time differs from actual thermal array")
        for trigger in [row["owned_receiver_trigger"], row["annotated_receiver_trigger"]]:
            for cell in trigger["cells"]:
                r, c = cell["row"], cell["column"]
                if times[r, c] != trigger["time_s"] or q.building_index[r, c] != cell["fuel_owner"]:
                    raise AssertionError("trigger cell evidence differs")
        area = float(q.parameters.covered_area_m2[own_mask & np.isfinite(times)].sum())
        if abs(area - row["receiver_owned_ignited_area_m2"]) > 1e-8:
            raise AssertionError("receiver burned area differs")
        v = row["validation"]
        if not math.isfinite(row["actual_horizon_s"]) or abs(row["actual_horizon_s"] - 5400) > 1e-8:
            raise AssertionError("incomplete horizon")
        tolerance = v["energy_tolerance_J"]
        if (
            not math.isfinite(tolerance)
            or tolerance <= 0
            or any(
                not math.isfinite(x) or abs(x) > tolerance
                for x in v["energy_closure_errors_J"].values()
            )
        ):
            raise AssertionError("absolute energy closure failed")
        evidence.append(
            {
                "key": row["key"],
                "owned_first_s": own,
                "observed_first_s": observed,
                "thermal_sha256": row["thermal_state_sha256"],
            }
        )
    backup = Path(completion["backup"])
    proof = json.loads((backup / "backup-validation.json").read_text())
    if (
        sha(backup / "bridge-mesh-experiment.tar.gz") != proof["archive_sha256"]
        or not proof["verified"]
    ):
        raise AssertionError("verified experiment archive mismatch")
    result = {
        "protocol_sha256": sha(output / "protocol.json"),
        "report_sha256": sha(output / "report.json"),
        "auditor_sha256": sha(__file__),
        "count": len(evidence),
        "ca_solves": 0,
        "cell_trigger_and_source_evidence_verified": True,
        "absolute_energy_closure_verified": True,
        "all_full_90min": True,
        "boundary_ignition": False,
        "records": evidence,
        "scope": "numerical evidence audit; not field calibration or full-domain replay",
    }
    dump(output / "independent-audit.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.output.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))
