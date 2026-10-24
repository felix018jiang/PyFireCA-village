"""Restore one completed selected-position run and verify every saved final field."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit():
    spec = importlib.util.spec_from_file_location(
        "position_restore_runner", ROOT / "scripts/run_heritage_roof_study.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    case = ROOT / (
        "runs/research/heritage-position-controls-20261024-g/"
        "near-B0073-001-h1p0/selected-B0061-B0071-B0076"
    )
    with tempfile.TemporaryDirectory(prefix="heritage-position-restore-") as directory:
        copied = Path(directory) / "case"
        shutil.copytree(case, copied)
        shutil.unpack_archive(copied / "node-storage-not-map.zip", copied)
        result = runner.restore_simulation(copied)
    assert result["all_final_state_fields_equal"] and result["saved_contact_used"]
    output = {
        "schema_version": "heritage-position-control-restoration-1",
        "complete": True,
        "restored_case": str(case.relative_to(ROOT)),
        **result,
    }
    path = ROOT / "docs/results/heritage-position-controls-restoration-20261024.json"
    path.write_text(json.dumps(output, indent=2) + "\n")
    return output


if __name__ == "__main__":
    print(json.dumps(audit()))
