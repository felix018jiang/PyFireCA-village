"""Restore saved independent-roof runs and verify immutable inputs and core bindings."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(roots, output):
    spec = importlib.util.spec_from_file_location(
        "roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    roots = [Path(p).resolve() for p in roots]
    before = {str(p): sha(p) for root in roots for p in sorted(root.rglob("*")) if p.is_file()}
    rows, protocols = [], []
    for root in roots:
        for protocol_path in sorted(root.rglob("protocol.json")):
            protocol = json.loads(protocol_path.read_text())
            for relative, expected in protocol["source_sha256"].items():
                assert sha(protocol_path.parent / "source-snapshot" / relative) == expected
            protocols.append(str(protocol_path))
        for evaluation in sorted(root.rglob("evaluation.json")):
            record = json.loads(evaluation.read_text())
            layout = json.loads((evaluation.parent / "layout.json").read_text())
            node_sets = []
            for outcome in record["metrics"]["core_outcomes"]:
                identifier = outcome["building_id"]
                parts = [p for p in layout["roof_components"] if identifier in p["building_ids"]]
                assert parts and all(p["building_ids"] == [identifier] for p in parts)
                assert len(parts) == outcome["component_count"]
                assert outcome["shared_temperature_components"] == 0
                assert outcome["first_ignition_s"] is None or outcome["first_ignition_s"] > 0
                node_sets.append({p["node"] for p in parts})
            assert sum(map(len, node_sets)) == len(set().union(*node_sets))
            print(f"Restoring {evaluation.parent}", flush=True)
            recovered = runner.restore_simulation(evaluation.parent)
            rows.append(
                {
                    "evaluation": str(evaluation),
                    "evaluation_sha256": record["evaluation_sha256"],
                    "core_temperature_nodes_disjoint": True,
                    "core_not_directly_ignited": True,
                    **recovered,
                }
            )
    after = {str(p): sha(p) for root in roots for p in sorted(root.rglob("*")) if p.is_file()}
    assert before == after, "restoration modified saved inputs or results"
    assert rows and protocols, "no saved roof evaluations/protocols found"
    value = {
        "schema_version": "heritage-roof-recovery-audit-1",
        "runs": rows,
        "producer_protocols_verified": protocols,
        "saved_files_unchanged": True,
        "saved_file_count": len(before),
        "file_sha256": before,
        "audit_script_sha256": sha(Path(__file__)),
    }
    Path(output).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Verified {len(rows)} complete thermal restorations; {len(before)} files unchanged",
        flush=True,
    )
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    audit(args.root, args.output)
