"""Scenario screening order preserves the problem and cumulative accounting."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def schedule():
    spec = importlib.util.spec_from_file_location(
        "area_schedule_under_test", ROOT / "scripts/resume_heritage_area_schedule.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schedule_preserves_every_source_and_support(schedule):
    scenarios = {"a-h1": ("a", 1), "a-h05": ("a", 0.5), "b-h1": ("b", 1), "b-h05": ("b", 0.5)}
    assert schedule.scenario_order(scenarios, "coarse_first") == ("a-h1", "b-h1", "a-h05", "b-h05")
    assert schedule.scenario_order(scenarios, "declared") == tuple(scenarios)
    with pytest.raises(ValueError, match="policy"):
        schedule.scenario_order(scenarios, "discard_fine")


def test_limits_are_cumulative_and_never_change_physics(schedule):
    config = {
        "max_new_checks": 64,
        "max_wall_time_s": 14400,
        "max_mip_time_s": 1200,
        "horizon_s": 3600,
        "source_area_limit_m2": 443.84,
    }
    spent = {"started_checks": 64, "elapsed_s": 15000, "mip_elapsed_s": 4}
    result = schedule.extended_limits(
        config, spent, {"max_new_checks": 128, "max_wall_time_s": 28800, "max_mip_time_s": 2400}
    )
    assert result["max_new_checks"] == 128
    assert result["source_area_limit_m2"] == config["source_area_limit_m2"]
    assert result["horizon_s"] == config["horizon_s"]
    assert config["max_new_checks"] == 64
    assert spent["started_checks"] == 64
    with pytest.raises(ValueError, match="cumulative"):
        schedule.extended_limits(config, spent, {"max_wall_time_s": 14401})
    with pytest.raises(ValueError, match="cumulative"):
        schedule.extended_limits(config, spent, {"max_new_checks": True})
    # A completed call may have passed the old wall limit; no override does
    # not refund it or forbid producing an already-exhausted budget report.
    assert schedule.extended_limits(config, spent, {}) == config


def test_mixed_physical_imports_are_rejected(schedule, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pyfireca.mixed_snapshot_marker", object())
    with pytest.raises(ValueError, match="fresh process"):
        schedule.load_frozen_manager(tmp_path)


def test_order_changes_calls_but_not_area_optimum(schedule, monkeypatch, tmp_path):
    pytest.importorskip("scipy.optimize")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import run_heritage_area_master as manager

    from pyfireca.planning.area_candidates import build_cleared_area_function
    from pyfireca.planning.building_search import ProtectionCheck

    scenarios = {"a-h1": ("a", 1), "a-h05": ("a", 0.5), "b-h1": ("b", 1), "b-h05": ("b", 0.5)}
    reports, calls = {}, {}
    for policy in ("declared", "coarse_first"):
        path = tmp_path / (policy + ".json")
        path.write_text(
            json.dumps({"elapsed_s": 0, "mip_elapsed_s": 0, "started_checks": 10, "active": None})
        )
        book = manager.ExecutionBudget(path)
        calls[policy] = []

        def evaluate(ids, scenario, policy=policy):
            calls[policy].append((ids, scenario))
            passed = "B" in ids or ("A" in ids and scenario.startswith("a-"))
            return ProtectionCheck(
                "passed" if passed else "failed",
                {
                    "reached_horizon": passed,
                    "simulated_until_s": 3600 if passed else 400,
                    "invalid_reasons": [],
                    "source_replaced": False,
                },
            )

        reports[policy] = manager.run_master_loop(
            {
                "seed_plans": [["A", "B"]],
                "horizon_s": 3600,
                "max_new_checks": 50,
                "max_wall_time_s": 60,
                "max_mip_time_s": 30,
                "mip_call_time_limit_s": 10,
            },
            candidate_ids=("A", "B"),
            scenario_ids=schedule.scenario_order(scenarios, policy),
            area=build_cleared_area_function([("A",), ("B",)], [2, 5], area_basis="physical"),
            problem_sha256="unchanged_physics",
            evaluate=evaluate,
            budget=book,
        )
        book.close()
        assert reports[policy]["best_ids"] == ["B"]
        assert reports[policy]["global_area_gap_closed"]
        assert reports[policy]["verified_area_upper_bound_m2"] == 5
        assert reports[policy]["global_area_lower_bound_m2"] == 5
        assert reports[policy]["budget"]["started_checks"] == 10 + len(calls[policy])
        assert {s for ids, s in calls[policy] if ids == ("B",)} == set(scenarios)
    assert len(calls["coarse_first"]) < len(calls["declared"])
