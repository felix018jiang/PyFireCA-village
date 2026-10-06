"""Complete finite optimum and numerical stability, without a field-risk claim."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely", minversion="2")


def test_layout_study_enumerates_all_choices_retains_ties_and_handles_connector_synergy(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/planning/layout_enumeration.py"
    spec = importlib.util.spec_from_file_location("layout_study", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "study"
    summary = module.run_study(output)
    assert summary["layouts_per_mode"] == 27
    assert summary["solver_calls_including_benchmark"] == 504
    assert summary["phase_gate"] == "P03A_D0_candidate_reference_complete"
    assert summary["target_horizon_flip_count"] == 0
    rows = json.loads((output / "selections.json").read_text())
    at96 = [r for r in rows if r["budget_units"] == 96]
    assert all(r["exact_best_ids"] == ["layout-1-1-1"] for r in at96)
    high0 = next(
        r for r in rows if r["budget_units"] == 192 and r["mode"] == "M0" and r["phase"] == "fine"
    )
    high1 = next(
        r for r in rows if r["budget_units"] == 192 and r["mode"] == "M1" and r["phase"] == "fine"
    )
    assert len(high0["near_optimal_ids"]) == 8
    assert high1["near_optimal_ids"] == ["layout-2-2-2"]
    actions = json.loads((output / "action_comparison.json").read_text())
    for mode in ("M0", "M1"):
        gap = next(r for r in actions if r["mode"] == mode and r["action"] == "leave_gap")
        filled = next(r for r in actions if r["mode"] == mode and r["action"] == "fill_gap")
        assert gap["mean_exposure"] == 4
        assert filled["cost_units"] - gap["cost_units"] == 8
        assert filled["mean_exposure"] < gap["mean_exposure"]
    provenance = json.loads((output / "provenance.json").read_text())
    for relative, digest in provenance["source_sha256"].items():
        assert (
            hashlib.sha256((output / "source_snapshot" / relative).read_bytes()).hexdigest()
            == digest
        )
    before = (output / "validation.json").read_bytes()
    with pytest.raises(FileExistsError):
        module.run_study(output)
    assert before == (output / "validation.json").read_bytes()
