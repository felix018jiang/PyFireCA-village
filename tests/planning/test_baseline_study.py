"""Cold baselines, objective information isolation and finite reference audit."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely", minversion="2")


def test_cold_baselines_account_for_all_queries_and_do_not_claim_uniform_width_advantage(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/planning/baseline_comparison.py"
    spec = importlib.util.spec_from_file_location("baseline_study", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "study"
    summary = module.run_study(output)
    assert summary["trials"] == 84
    assert summary["solver_calls"] == 1950
    assert summary["oracle_evaluations"] == 650
    assert summary["requests"] == 670
    assert summary["cache_hits"] == 20
    assert all(summary["checks"].values())
    assert summary["max_joint_advantage_over_best_fixed"] == 0
    assert summary["max_myopic_gap"] == pytest.approx(10 / 3)
    assert summary["phase_gate"] == "P04_D0_cold_baselines_complete"
    trials = json.loads((output / "trials.json").read_text())
    queries = json.loads((output / "queries.json").read_text())
    evaluations = json.loads((output / "oracle_evaluations.json").read_text())
    for trial in trials:
        trial_id = trial["trial_id"]
        requests = [q for q in queries if q["trial_id"] == trial_id]
        cold = [e for e in evaluations if e["trial_id"] == trial_id]
        assert not requests[0]["cache_hit"]
        assert requests[0]["plan_id"] == "layout-0-0-0"
        assert len({e["plan_id"] for e in cold}) == len(cold)
        assert {q["plan_id"] for q in requests} == {e["plan_id"] for e in cold}
        assert trial["unique_oracle_evaluations"] == len(cold)
        assert all(len(e["scenarios"]) == 3 for e in cold)
        if trial["method"] == "best_uniform_width":
            assert trial["gap_to_joint_reference"] == 0
            assert trial["gap_interpretation"] == "action-set restriction"
        if trial["method"] == "lookahead_3" and trial["budget_units"] == 192:
            assert trial["action_set_size"] == trial["unique_oracle_evaluations"] == 27
            assert trial["cache_hits"] == 7
        if trial["method"] == "myopic_greedy" and trial["budget_units"] == 96:
            assert trial["selected"]["plan_id"] == "layout-0-0-0"
            assert not trial["coverage_complete"]
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
