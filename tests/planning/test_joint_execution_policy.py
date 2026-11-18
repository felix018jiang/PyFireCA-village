"""Execution order and counterexample pruning never alter the physical question."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def configured(monkeypatch, tmp_path):
    path = ROOT / "tests/planning/test_joint_terminal_comparison.py"
    spec = importlib.util.spec_from_file_location("execution_policy_terminal_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    shared, runner, inputs = fixture.configured.__wrapped__(monkeypatch, tmp_path)
    config_path, config, *rest = inputs
    config = config | {
        "roof_scales_m": [1.0, 0.5],
        "source_fires": [config["source_fires"][0] | {"fire_id": f"family-{i}"} for i in range(2)],
    }
    config_path.write_text(json.dumps(config))
    return shared, runner, (config_path, config, *rest)


def pairs(values):
    return [(plan.plan_id, scenario) for plan, scenario in values]


def test_default_order_plan_first_priority_and_selection_leave_full_question_unchanged(configured):
    _, runner, (_, config, *_) = configured
    original = deepcopy(config)
    expected = pairs(runner.expected_checks(config))
    assert len(expected) == 8
    assert pairs(runner.execution_checks(config)) == expected
    assert pairs(runner.execution_checks(config | {"execution_case_order": "declared"})) == expected
    plan_first = config | {"execution_case_order": "plans_first"}
    assert [plan for plan, _ in pairs(runner.execution_checks(plan_first))] == ["baseline"] * 4 + [
        "joint"
    ] * 4
    preferred = plan_first | {"execution_priority_plan_ids": ["joint", "baseline"]}
    ordered = pairs(runner.execution_checks(preferred))
    assert [plan for plan, _ in ordered] == ["joint"] * 4 + ["baseline"] * 4
    selected = preferred | {
        "execution_case_selection": [
            {"plan_id": p, "scenario_id": s} for p, s in (ordered[-1], ordered[0])
        ]
    }
    assert pairs(runner.execution_checks(selected)) == [ordered[0], ordered[-1]]
    assert pairs(runner.expected_checks(preferred)) == expected
    assert config == original


@pytest.mark.parametrize(
    "change",
    [
        {"execution_case_order": "unknown"},
        {"execution_case_order": []},
        {"stop_plan_after_protection_failure": "true"},
        {"execution_priority_plan_ids": ["joint", "baseline"]},
        {"execution_case_order": "plans_first", "execution_priority_plan_ids": ["joint"]},
        {"execution_case_order": "plans_first", "execution_priority_plan_ids": ["joint", "joint"]},
        {
            "execution_case_order": "plans_first",
            "execution_priority_plan_ids": ["joint", "missing"],
        },
    ],
)
def test_ambiguous_or_incomplete_execution_policy_is_rejected(configured, change):
    _, runner, (_, config, *_) = configured
    with pytest.raises(ValueError):
        runner.validate_config(config | change)


def test_execution_policy_does_not_change_physical_problem_for_a_fixed_code_version(configured):
    _, runner, (path, config, *_) = configured
    first, _ = runner.make_oracle(path, config, "same-frozen-source-version")
    altered = config | {
        "execution_case_order": "plans_first",
        "execution_priority_plan_ids": ["joint", "baseline"],
        "stop_plan_after_protection_failure": True,
    }
    second, _ = runner.make_oracle(path, altered, "same-frozen-source-version")
    assert first.problem == second.problem
    assert first.problem_sha256 == second.problem_sha256


def test_real_native_failure_prunes_only_failed_plan_and_resume_never_resets_budget(
    configured, monkeypatch, tmp_path
):
    shared, runner, (path, config, *_) = configured
    config = config | {
        "execution_case_order": "plans_first",
        "execution_priority_plan_ids": ["joint", "baseline"],
        "stop_plan_after_protection_failure": True,
    }
    path.write_text(json.dumps(config))
    output = tmp_path / "plan-first-stop"
    runner.prepare(path, output)
    calls = []
    native = runner.JointComparisonOracle.evaluate

    def observe(self, plan, scenario):
        calls.append((plan.plan_id, scenario))
        return native(self, plan, scenario)

    monkeypatch.setattr(runner.JointComparisonOracle, "evaluate", observe)
    first = shared.run_live_snapshot(runner, monkeypatch, path, output)
    assert [p for p, _ in calls] == ["joint"] * 4 + ["baseline"]
    assert not first["complete"]
    assert first["planned_checks"] == 8
    assert first["completed_checks"] == 5
    assert first["execution_control"]["plan_protection_query_status"] == {
        "baseline": "failed",
        "joint": "passed",
    }
    receipt = json.loads((output / "execution-skips.json").read_text())
    assert len(receipt["skipped_cases"]) == 3
    assert receipt["native_case_results_created"] == 0
    assert not receipt["full_scenario_completeness_requirement_changed"]
    for row in receipt["skipped_cases"]:
        assert row["plan_id"] == "baseline"
        witness = row["failure_witness"]
        assert witness["problem_sha256"] == receipt["problem_sha256"]
        assert witness["status"] == "failed" and witness["native_invalid_reasons"] == []
        assert witness["ignited_core_ids"] and witness["strict_terminal_complete"]
        assert runner.file_sha256(output / witness["manifest"]) == witness["manifest_sha256"]
        plan = next(p for p, _ in runner.expected_checks(config) if p.plan_id == row["plan_id"])
        assert not runner.result_folder(output, plan, row["scenario_id"]).exists()
    before = json.loads((output / "execution-budget.json").read_text())
    receipt_before = (output / "execution-skips.json").read_bytes()
    manifests_before = {
        str(p): runner.file_sha256(p) for p in (output / "evaluations").glob("*/complete.json")
    }
    second = shared.run_live_snapshot(runner, monkeypatch, path, output)
    after = json.loads((output / "execution-budget.json").read_text())
    assert len(calls) == 5
    assert before["started_checks"] == after["started_checks"] == 5
    assert after["elapsed_s"] >= before["elapsed_s"]
    assert receipt_before == (output / "execution-skips.json").read_bytes()
    assert second["completed_checks"] == 5 and not second["complete"]
    assert manifests_before == {
        str(p): runner.file_sha256(p) for p in (output / "evaluations").glob("*/complete.json")
    }


def test_real_unclosed_native_unknowns_are_all_evaluated_without_pruning(
    configured, monkeypatch, tmp_path
):
    shared, runner, (path, config, *_) = configured
    config = config | {
        "horizon_s": 0.5,
        "terminal_horizon_cap_s": 0.5,
        "checkpoints_s": [0.5],
        "execution_case_order": "plans_first",
        "stop_plan_after_protection_failure": True,
    }
    path.write_text(json.dumps(config))
    output = tmp_path / "unknown-is-not-failure"
    runner.prepare(path, output)
    summary = shared.run_live_snapshot(runner, monkeypatch, path, output)
    assert summary["completed_checks"] == 8
    assert all(row["status"] == "unknown" for row in summary["checks"])
    assert summary["started_CA_checks"] == 8
    assert not (output / "execution-skips.json").exists()
    assert set(summary["execution_control"]["plan_protection_query_status"].values()) == {"unknown"}


def test_skip_receipt_requires_unchanged_verified_failure_manifest(
    configured, monkeypatch, tmp_path
):
    shared, runner, (path, config, *_) = configured
    config = config | {
        "execution_case_order": "plans_first",
        "stop_plan_after_protection_failure": True,
    }
    path.write_text(json.dumps(config))
    output = tmp_path / "receipt-integrity"
    runner.prepare(path, output)
    shared.run_live_snapshot(runner, monkeypatch, path, output)
    experiment = json.loads((output / "experiment.json").read_text())
    receipt_path = output / "execution-skips.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["skipped_cases"][0]["failure_witness"]["manifest_sha256"] = "0" * 64
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = runner.content_hash(receipt)
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="counterexample is missing or changed"):
        runner.read_execution_skips(output, experiment)


@pytest.fixture(scope="module")
def real_boundary_campaign(tmp_path_factory):
    path = ROOT / "tests/planning/test_joint_pareto_cli.py"
    spec = importlib.util.spec_from_file_location("execution_boundary_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    native = fixture.native_campaign.__wrapped__(tmp_path_factory)
    return fixture, fixture.boundary_campaign.__wrapped__(native, tmp_path_factory)


def test_real_edge_unknowns_never_become_execution_skip_witnesses(real_boundary_campaign, tmp_path):
    _, (_, original) = real_boundary_campaign
    import os
    import subprocess
    import sys

    config = json.loads(original.read_text()) | {
        "execution_case_order": "plans_first",
        "stop_plan_after_protection_failure": True,
    }
    config["study_path"] = str((original.parent / config["study_path"]).resolve())
    config["source_fires"] = [
        row | {"protocol_path": str((original.parent / row["protocol_path"]).resolve())}
        for row in config["source_fires"]
    ]
    path, output = tmp_path / "edge.json", tmp_path / "edge-no-prune"
    path.write_text(json.dumps(config))
    for mode in ("--prepare-only", "--resume"):
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_heritage_joint_comparison.py"),
                "--config",
                str(path),
                "--output",
                str(output),
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert process.returncode == 0, process.stdout + process.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["completed_checks"] == 2
    assert all(
        row["status"] == "unknown"
        and row["evidence"]["invalid_reasons"] == ["fire_reached_physical_domain_edge"]
        for row in summary["checks"]
    )
    assert not (output / "execution-skips.json").exists()
