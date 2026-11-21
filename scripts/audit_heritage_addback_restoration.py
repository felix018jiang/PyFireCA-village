"""Restore one new B0063 case and verify one actual building addback."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def audit():
    spec = importlib.util.spec_from_file_location(
        "addback_restore_runner", ROOT / "scripts/run_heritage_roof_study.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    case = ROOT / (
        "runs/research/heritage-B0063-confirmation-20261024-e/"
        "near-B0120-001-h1p0/remove-B0061-B0071-B0076"
    )
    reference = ROOT / "runs/research/heritage-triple-validation-20261024-c/near-B0120-002-h1p0"
    addback = ROOT / (
        "runs/research/heritage-ablation-screen-20261024-c/near-B0120-002-h1p0/restore-B0061"
    )
    with tempfile.TemporaryDirectory(prefix="heritage-addback-restore-") as directory:
        directory = Path(directory)
        copied = directory / "B0063"
        shutil.copytree(case, copied)
        shutil.unpack_archive(copied / "node-storage-not-map.zip", copied)
        check = runner.restore_simulation(copied)
        bundles = {}
        for label, source in (
            ("baseline", reference / "baseline"),
            ("triple", reference / "remove-B0061-B0071-B0076"),
            ("addback", addback),
        ):
            destination = directory / label
            destination.mkdir()
            shutil.unpack_archive(source / "node-storage-not-map.zip", destination)
            bundles[label] = runner.load_v3_case_bundle(destination / "node-storage-not-map")
        before, after, original = (bundles[k].request for k in ("triple", "addback", "baseline"))
        restored = after.burnable_mask & ~before.burnable_mask
        assert restored.any()
        for name in before.parameters.__dataclass_fields__:
            b, a, o = (getattr(request.parameters, name) for request in (before, after, original))
            np.testing.assert_array_equal(b[~restored], a[~restored])
            np.testing.assert_array_equal(a[restored], o[restored])
        old_ledger = read(reference / "remove-B0061-B0071-B0076/evaluation.json")["intervention"]
        new_ledger = read(addback / "evaluation.json")["intervention"]
        restored_area = float(after.parameters.covered_area_m2[restored].sum())
        np.testing.assert_allclose(
            restored_area,
            old_ledger["cleared_roof_union_area_m2"] - new_ledger["cleared_roof_union_area_m2"],
            rtol=1e-8,
            atol=1e-5,
        )
    result = {
        "schema_version": "heritage-B0063-addback-restoration-1",
        "complete": True,
        "restored_case": str(case.relative_to(ROOT)),
        **check,
        "addback_case": str(addback.relative_to(ROOT)),
        "all_other_physical_parameters_equal_to_saved_triple": True,
        "all_restored_physical_parameters_equal_to_original_baseline": True,
        "restored_area_m2": restored_area,
        "restored_area_matches_ledger_difference": True,
    }
    output = ROOT / "docs/results/heritage-B0063-ablation-restoration-20261024.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(audit()))
