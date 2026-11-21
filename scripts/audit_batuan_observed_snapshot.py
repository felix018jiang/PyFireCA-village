"""Audit an immutable snapshot of written design-search cache records, without solving CA."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(output):
    protocol = json.loads((output / "protocol.json").read_text())
    errors = []
    source_checks = {}
    for name, expected in protocol["source_hashes"].items():
        source_checks[name] = {
            "working": sha(ROOT / name) == expected,
            "snapshot": sha(output / "source_snapshot" / name) == expected,
        }
    inputs = {name: sha(ROOT / name) == h for name, h in protocol["input_hashes"].items()}
    if not all(all(row.values()) for row in source_checks.values()) or not all(inputs.values()):
        errors.append("frozen source/input mismatch")
    if sha(output / "buildings.source.json") != protocol["annotation_sha256"]:
        errors.append("annotation snapshot mismatch")
    training = {s["scenario_id"] for s in protocol["training"]}
    # Only search-cache files are read; held-out and internal confirmation results stay closed.
    files = sorted((output / "cache").glob("*.json"))
    rows = []
    counts = Counter()
    for path in files:
        content = path.read_bytes()  # Each cache file is published by atomic rename.
        item = json.loads(content)
        record = item["record"]
        checksum = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        problems = []
        if item["signature"] != path.stem or checksum != item["record_sha256"]:
            problems.append("cache name/checksum")
        if record["scenario_id"] not in training:
            problems.append("non-design scenario in search cache")
        d, m = record["diagnostics"], record["metrics"]
        obs = m["object_observation"]
        if (
            obs["object_count"] != 237
            or obs["unrepresented_labels"]
            or obs["partly_unrepresented"]
            or obs["fuel_duplicated"]
            or obs["subcell_independent_temperatures"]
        ):
            problems.append("crop observation contract")
        tolerance = d["energy_tolerance_J"]
        energy = d["energy_closure_errors_J"]
        if (
            not math.isfinite(tolerance)
            or tolerance <= 0
            or any(not math.isfinite(x) or abs(x) > tolerance for x in energy.values())
        ):
            problems.append("absolute energy closure")
        until = d["simulated_until_s"]
        if record["horizon_s"] != protocol["horizon_s"] or not 0 <= until <= 5400 + 1e-8:
            problems.append("declared/simulated horizon")
        if d["reached_requested_horizon"]:
            category = "full_horizon"
            if abs(until - 5400) > 1e-8:
                problems.append("full horizon flag/time mismatch")
        elif d["stop_at_first_structure_ignition"] and m["ignited_structure_count"] > 0:
            category = "early_failure_lower_bound"
        else:
            category = "unexplained_truncation"
            problems.append(category)
        counts[category] += 1
        counts["boundary_affected"] += bool(m["burning_reached_domain_edge"])
        if problems:
            errors.append({"file": str(path.relative_to(output)), "problems": problems})
        rows.append(
            {
                "file": str(path.relative_to(output)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "scenario_id": record["scenario_id"],
                "plan_id": record["intervention_id"],
                "category": category,
                "simulated_until_s": until,
                "maximum_absolute_energy_closure_J": max(map(abs, energy.values())),
                "energy_tolerance_J": tolerance,
            }
        )
    return {
        "created_at": datetime.now().astimezone().isoformat(),
        "protocol_sha256": sha(output / "protocol.json"),
        "scope": "written design-search cache snapshot; not a completed optimization audit",
        "frozen_source_files": len(source_checks),
        "source_checks": source_checks,
        "input_checks": inputs,
        "cache_records": len(rows),
        "counts": dict(counts),
        "held_or_internal_outcomes_read": False,
        "selection_exists": (output / "selection.json").exists(),
        "completion_exists": (output / "completion.json").exists(),
        "records": rows,
        "errors": errors,
        "passed": not errors,
        "interpretation": (
            "Early failures are loss lower bounds; "
            "237 crop observations do not confirm 333 full-domain objects."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.output.resolve())
    folder = args.output / "independent-audits"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = folder / f"design-snapshot-{stamp}.json"
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ["passed", "cache_records", "counts", "errors"]}))
    print(dest)
    raise SystemExit(0 if result["passed"] else 1)
