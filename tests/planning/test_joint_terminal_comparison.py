"""Terminal protocol composes with frozen joint sources, caches and budgets."""

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.strategy_io import file_sha256
from pyfireca.planning.terminal_verification import terminal_summary_is_complete
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def configured(monkeypatch, tmp_path):
    path = ROOT / "tests/planning/test_joint_comparison.py"
    spec = importlib.util.spec_from_file_location("terminal_comparison_shared_tests", path)
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    runner = shared.runner.__wrapped__(monkeypatch)
    inputs = shared.inputs.__wrapped__(runner, monkeypatch, tmp_path)
    config_path, config, *rest = inputs
    config = config | {
        "horizon_s": 3600.0,
        "checkpoints_s": [1200.0, 2400.0, 3600.0],
        "terminal_fire_verification": True,
        "terminal_horizon_cap_s": 4000.0,
        "max_wall_time_s": 60.0,
    }
    config_path.write_text(json.dumps(config))
    return shared, runner, (config_path, config, *rest)


def test_terminal_opt_in_requires_explicit_cap_and_changes_temporal_problem(configured):
    _, runner, (path, config, *_) = configured
    runner.validate_config(config)
    no_cap = {k: v for k, v in config.items() if k != "terminal_horizon_cap_s"}
    with pytest.raises(ValueError, match="terminal"):
        runner.validate_config(no_cap)
    with pytest.raises(ValueError, match="terminal"):
        runner.validate_config(config | {"terminal_horizon_cap_s": 3599})
    legacy = {k: v for k, v in config.items() if not k.startswith("terminal_")}
    first, _ = runner.make_oracle(path, legacy, "code")
    explicit_false, _ = runner.make_oracle(
        path, legacy | {"terminal_fire_verification": False}, "code"
    )
    terminal, _ = runner.make_oracle(path, config, "code")
    assert first.problem_sha256 == explicit_false.problem_sha256
    assert terminal.problem_sha256 != first.problem_sha256
    assert terminal.problem["evaluation_temporal_protocol"]["terminal_horizon_cap_s"] == 4000
    assert terminal.problem["evaluation_temporal_protocol"]["reference_horizon_s"] == 3600


def test_terminal_pairing_cache_and_closed_checkpoint_basis(configured, monkeypatch, tmp_path):
    shared, runner, (path, config, *_) = configured
    output = tmp_path / "terminal-run"
    runner.prepare(path, output)
    summary = shared.run_live_snapshot(runner, monkeypatch, path, output)
    assert summary["complete"]
    assert summary["valid_completed_checks"] == len(config["plans"])
    assert summary["terminal_fire_verification"]
    assert all(row["valid_terminal_fire_observation"] for row in summary["checks"])
    assert not summary["all_evaluations_reached_horizon"]
    for row in summary["checks"]:
        terminal = row["evidence"]["terminal_verification"]
        assert terminal_summary_is_complete(terminal)
        assert not terminal["reached_requested_horizon"]
        folder = output / row["folder"]
        curves = json.loads((folder / "curves.json").read_text())
        assert [r["time_s"] for r in curves["checkpoints"]] == config["checkpoints_s"]
        assert curves["terminal_endpoint"]["actual_stop_time_s"] == terminal["actual_stop_time_s"]
        future = [r for r in curves["checkpoints"] if r["time_s"] > terminal["actual_stop_time_s"]]
        assert future
        assert all(r["event_metrics_closed_by_extinction_certificate"] for r in future)
        assert all(not r["actual_time_reached"] for r in future)
        assert all(r["future_temperature_fields_not_simulated"] for r in future)
    before = json.loads((output / "execution-budget.json").read_text())["started_checks"]
    again = shared.run_live_snapshot(runner, monkeypatch, path, output)
    assert again["complete"]
    assert json.loads((output / "execution-budget.json").read_text())["started_checks"] == before


def test_terminal_unclosed_cap_is_saved_but_cannot_complete(configured, monkeypatch, tmp_path):
    shared, runner, (path, config, *_) = configured
    config = config | {
        "horizon_s": 20.0,
        "checkpoints_s": [10.0, 20.0],
        "terminal_horizon_cap_s": 20.0,
        "plans": [config["plans"][0]],
    }
    path.write_text(json.dumps(config))
    output = tmp_path / "terminal-cap"
    runner.prepare(path, output)
    summary = shared.run_live_snapshot(runner, monkeypatch, path, output)
    assert summary["completed_checks"] == 1
    assert summary["valid_completed_checks"] == 0
    assert not summary["complete"]
    terminal = summary["checks"][0]["evidence"]["terminal_verification"]
    assert terminal["terminal_status"] == "unknown"
    assert terminal["reached_requested_horizon"]
    assert not terminal["fire_loss_complete"] and not terminal["protection_feasible"]


def test_terminal_cache_certificate_binds_native_state(configured, monkeypatch, tmp_path):
    shared, runner, (path, config, *_) = configured
    path.write_text(json.dumps(config | {"plans": [config["plans"][0]]}))
    output = tmp_path / "terminal-tamper"
    runner.prepare(path, output)
    shared.run_live_snapshot(runner, monkeypatch, path, output)
    manifest_path = next((output / "evaluations").glob("*/complete.json"))
    folder = manifest_path.parent
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        arrays = {key: saved[key].copy() for key in saved.files}
    arrays["fuel_remaining_kg"].ravel()[0] += 1
    np.savez_compressed(folder / "final-state.npz", **arrays)
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"]["final-state.npz"] = file_sha256(folder / "final-state.npz")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=r"state|peak|certificate"):
        runner.summarize(output)


def test_terminal_native_cli_freezes_helper_and_reuses_completed_checks(configured, tmp_path):
    _, _, (path, config, bundle, transport, inventory, _) = configured
    write_v3_case_bundle(
        bundle.request,
        tmp_path / "native-case",
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata={**bundle.metadata, "transport_parameters": asdict(transport)},
    )
    (tmp_path / "study.json").write_text(
        json.dumps(
            {
                "schema_version": "heritage-p1-study-1",
                "case_dir": "native-case",
                "structures_path": "structures.json",
                "targets": [asdict(target) for target in inventory.targets],
                "fixed_source_ids": list(inventory.fixed_source_ids),
                "candidate_ids": list(inventory.candidate_ids),
                "candidate_evidence": inventory.candidate_evidence,
            }
        )
    )
    path.write_text(json.dumps(config))
    output = tmp_path / "native-terminal-run"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_comparison.py"),
        "--config",
        str(path),
        "--output",
        str(output),
    ]
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for flag in ("--prepare-only", "--resume", "--resume"):
        completed = subprocess.run(
            [*common, flag], env=environment, capture_output=True, text=True, timeout=60
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((output / "summary.json").read_text())
    budget = json.loads((output / "execution-budget.json").read_text())
    assert summary["complete"] and summary["valid_completed_checks"] == len(config["plans"])
    assert budget["started_checks"] == len(config["plans"]) and budget["active"] is None
    assert (output / "source-snapshot/src/pyfireca/planning/terminal_verification.py").is_file()
    assert (output / "source-snapshot/src/pyfireca/village/extinction_v3.py").is_file()
