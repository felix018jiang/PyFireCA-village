"""Freeze a longer terminal horizon with identical source, mesh and fire physics.

No geometry preflight or CA is run. The existing 21600 s campaign remains
immutable. This prepares a new protocol from time zero, not a disguised resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def immutable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("immutable preparation file already differs: " + str(path))
        return
    with path.open("xb") as stream:
        stream.write(data)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def prepare(
    parent_config,
    parent_run,
    parent_producer,
    output_config,
    output_producer,
    receipt_path,
    *,
    cap_s=172800.0,
    max_wall_s=172800.0,
    max_checks=40,
):
    paths = [
        Path(p).resolve()
        for p in (
            parent_config,
            parent_run,
            parent_producer,
            output_config,
            output_producer,
            receipt_path,
        )
    ]
    parent_config, parent_run, parent_producer, output_config, output_producer, receipt_path = paths
    old = json.loads(parent_config.read_text())
    experiment = json.loads((parent_run / "experiment.json").read_text())
    producer = json.loads((parent_producer / "producer-manifest.json").read_text())
    if (
        experiment["config"] != old
        or experiment["config_sha256"] != digest(parent_config)
        or old.get("terminal_fire_verification") is not True
        or not isinstance(cap_s, (int, float))
        or isinstance(cap_s, bool)
        or not math.isfinite(cap_s)
        or cap_s <= old["terminal_horizon_cap_s"]
        or not isinstance(max_wall_s, (int, float))
        or isinstance(max_wall_s, bool)
        or not math.isfinite(max_wall_s)
        or max_wall_s <= 0
        or isinstance(max_checks, bool)
        or not isinstance(max_checks, int)
        or not 1 <= max_checks <= old["max_total_checks"]
    ):
        raise ValueError("valid frozen parent and explicit increased cap/budgets required")
    if (
        output_config == parent_config
        or output_config.is_relative_to(parent_run)
        or output_producer == parent_producer
        or output_producer.is_relative_to(parent_producer)
        or receipt_path.is_relative_to(parent_run)
        or receipt_path.is_relative_to(parent_producer)
    ):
        raise ValueError("long-cap protocol must use independent configuration and producer paths")
    if output_config.parent != parent_config.parent:
        for key, value in old.items():
            if key.endswith("_path") and not Path(value).is_absolute():
                raise ValueError(
                    "relative parent paths require the same output configuration directory"
                )
        if any(not Path(f["protocol_path"]).is_absolute() for f in old["source_fires"]):
            raise ValueError("relative fire protocols require the same configuration directory")
    hashes = producer["source_files_sha256"]
    for relative, expected in hashes.items():
        source = parent_producer / relative
        if digest(source) != expected:
            raise ValueError("frozen parent producer changed: " + relative)
        immutable(output_producer / relative, source.read_bytes())
        if digest(output_producer / relative) != expected:
            raise ValueError("new producer byte identity failed")
    frozen = {
        "schema_version": "heritage-longcap-frozen-producer-1",
        "source_files_sha256": hashes,
        "parent_producer_path": str(parent_producer),
        "parent_producer_manifest_sha256": digest(parent_producer / "producer-manifest.json"),
        "all_python_sources_byte_identical": True,
        "new_CA_checks": 0,
    }
    immutable(output_producer / "producer-manifest.json", encoded(frozen))
    new = old | {
        "terminal_horizon_cap_s": float(cap_s),
        "max_wall_time_s": float(max_wall_s),
        "max_new_checks": max_checks,
    }
    immutable(output_config, encoded(new))
    receipt = {
        "schema_version": "heritage-joint-longcap-preparation-1",
        "parent_config_path": str(parent_config),
        "parent_config_sha256": digest(parent_config),
        "parent_run_path": str(parent_run),
        "parent_experiment_sha256": digest(parent_run / "experiment.json"),
        "parent_execution_budget_sha256": digest(parent_run / "execution-budget.json"),
        "old_spent_budget_preserved": json.loads(
            (parent_run / "execution-budget.json").read_text()
        ),
        "new_config_path": str(output_config),
        "new_config_sha256": digest(output_config),
        "new_producer_path": str(output_producer),
        "new_producer_manifest_sha256": digest(output_producer / "producer-manifest.json"),
        "source_file_count": len(hashes),
        "changed_configuration_keys": sorted(k for k in new if new[k] != old[k]),
        "old_terminal_cap_s": old["terminal_horizon_cap_s"],
        "new_terminal_cap_s": float(cap_s),
        "five_arms_and_complete_source_scale_matrix_preserved": new["plans"] == old["plans"]
        and new["source_fires"] == old["source_fires"]
        and new["roof_scales_m"] == old["roof_scales_m"],
        "new_protocol_starts_at_zero": True,
        "old_prefix_or_optimality_transferred": False,
        "cap_unclosed_status": "unknown",
        "new_CA_checks": 0,
    }
    immutable(receipt_path, encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-config", type=Path, required=True)
    parser.add_argument("--parent-run", type=Path, required=True)
    parser.add_argument("--parent-producer", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    parser.add_argument("--output-producer", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--cap-s", type=float, default=172800)
    parser.add_argument("--max-wall-s", type=float, default=172800)
    parser.add_argument("--max-checks", type=int, default=40)
    args = parser.parse_args()
    result = prepare(
        args.parent_config,
        args.parent_run,
        args.parent_producer,
        args.output_config,
        args.output_producer,
        args.receipt,
        cap_s=args.cap_s,
        max_wall_s=args.max_wall_s,
        max_checks=args.max_checks,
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "new_config_path",
                    "new_producer_path",
                    "new_terminal_cap_s",
                    "new_CA_checks",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
