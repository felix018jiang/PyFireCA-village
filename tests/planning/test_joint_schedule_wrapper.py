"""A frozen native manager can permute calls without losing cached cases or budget."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/resume_heritage_joint_schedule.py"


@pytest.fixture(scope="module")
def wrapper():
    spec = importlib.util.spec_from_file_location("joint_schedule_wrapper_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_first_is_only_an_exact_permutation(wrapper):
    a, b = SimpleNamespace(plan_id="A"), SimpleNamespace(plan_id="B")
    declared = [(a, "x-h1"), (b, "x-h1"), (a, "x-h05"), (b, "x-h05")]
    ordered = wrapper.plan_first(declared, ["A", "B"], ["B", "A"])
    assert [(p.plan_id, s) for p, s in ordered] == [
        ("B", "x-h1"),
        ("B", "x-h05"),
        ("A", "x-h1"),
        ("A", "x-h05"),
    ]
    for priority in (["A"], ["A", "A"], ["A", "C"]):
        with pytest.raises(ValueError, match="exact permutation"):
            wrapper.plan_first(declared, ["A", "B"], priority)


def test_mixed_physical_imports_require_a_fresh_process(wrapper, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pyfireca.wrapper_mixed_marker", object())
    with pytest.raises(ValueError, match="fresh process"):
        wrapper.load_manager(tmp_path)


def command(*args, script=SCRIPT):
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.fixture(scope="module")
def partial_native(tmp_path_factory):
    fixture_path = ROOT / "tests/planning/test_joint_pareto_cli.py"
    spec = importlib.util.spec_from_file_location("wrapper_native_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    original, config = fixture.native_campaign.__wrapped__(tmp_path_factory)
    run = original.parent / "clean-post-persist-boundary"
    prepare = command(
        "--config",
        config,
        "--output",
        run,
        "--prepare-only",
        script=ROOT / "scripts/run_heritage_joint_comparison.py",
    )
    assert prepare.returncode == 0, prepare.stdout + prepare.stderr
    # The test controller deliberately stops after the first complete persist,
    # before another physical query. Production wrappers never inject a stop.
    code = """
import importlib.util, sys
from pathlib import Path
run, config = map(Path, sys.argv[1:])
snapshot=run/'source-snapshot'
sys.path[:0]=[str(snapshot/'scripts'),str(snapshot/'src')]
spec=importlib.util.spec_from_file_location('original_boundary_manager',snapshot/'scripts/run_heritage_joint_comparison.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
persist=m.persist
def stop_after_persist(*args,**kwargs):
    persist(*args,**kwargs)
    raise KeyboardInterrupt('test-only clean post-persist boundary')
m.persist=stop_after_persist
try:m.resume(config,run)
except KeyboardInterrupt:pass
"""
    first = subprocess.run(
        [sys.executable, "-c", code, str(run), str(config)],
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert first.returncode == 0, first.stdout + first.stderr
    budget = json.loads((run / "execution-budget.json").read_text())
    assert budget["active"] is None and budget["started_checks"] == 1
    assert len(list((run / "evaluations").glob("*/complete.json"))) == 1
    return run, config


def test_external_wrapper_reuses_completed_case_and_keeps_original_native_problem_and_budget(
    partial_native, wrapper, tmp_path
):
    original, config = partial_native
    run = tmp_path / "copied-partial-native"
    shutil.copytree(original, run)
    immutable = {
        str(p.relative_to(run)): wrapper.sha256(p) for p in (run / "source-snapshot").rglob("*.py")
    }
    immutable["experiment.json"] = wrapper.sha256(run / "experiment.json")
    old_manifest = next((run / "evaluations").glob("*/complete.json"))
    old_hash = wrapper.sha256(old_manifest)
    before = json.loads((run / "execution-budget.json").read_text())
    receipt_dir = tmp_path / "external-order-receipt"
    register = command(
        "--run",
        run,
        "--config",
        config,
        "--register-only",
        "--receipt-dir",
        receipt_dir,
        "--priority-plan-ids",
        "joint",
        "baseline",
    )
    assert register.returncode == 0, register.stdout + register.stderr
    receipt = json.loads((receipt_dir / "receipt.json").read_text())
    assert receipt["cached_native_case_count"] == 1
    assert receipt["expected_full_case_count"] == 2
    assert receipt["scheduled_selected_case_order"][0]["plan_id"] == "joint"
    assert receipt["native_problem_or_physical_inputs_changed"] is False
    assert receipt["spent_budget_reset"] is False
    frozen = receipt_dir / SCRIPT.name
    resume = command("--resume", "--receipt", receipt_dir / "receipt.json", script=frozen)
    assert resume.returncode == 0, resume.stdout + resume.stderr
    final = json.loads((run / "summary.json").read_text())
    after = json.loads((run / "execution-budget.json").read_text())
    assert final["complete"] and final["planned_checks"] == final["completed_checks"] == 2
    assert after["started_checks"] == before["started_checks"] + 1 == 2
    assert after["elapsed_s"] >= before["elapsed_s"]
    assert wrapper.sha256(old_manifest) == old_hash
    assert immutable == {name: wrapper.sha256(run / name) for name in immutable}
    again = command("--resume", "--receipt", receipt_dir / "receipt.json", script=frozen)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads((run / "execution-budget.json").read_text())["started_checks"] == 2
    assert len(list(receipt_dir.glob("invocation-*/completion.json"))) == 2


def test_active_worker_is_registered_and_inspected_without_signal_or_budget_mutation(
    partial_native, tmp_path
):
    original, config = partial_native
    run = tmp_path / "active-native"
    shutil.copytree(original, run)
    budget_path = run / "execution-budget.json"
    budget = json.loads(budget_path.read_text())
    budget["active"] = {"pid": 999999}
    budget_path.write_text(json.dumps(budget))
    before = budget_path.read_bytes()
    receipt_dir = tmp_path / "active-order-receipt"
    register = command(
        "--run", run, "--config", config, "--register-only", "--receipt-dir", receipt_dir
    )
    assert register.returncode == 0, register.stdout + register.stderr
    receipt = json.loads((receipt_dir / "receipt.json").read_text())
    assert not receipt["handoff_ready"] and not receipt["active_worker_stop_attempted"]
    failed = command(
        "--resume", "--receipt", receipt_dir / "receipt.json", script=receipt_dir / SCRIPT.name
    )
    assert failed.returncode != 0
    assert "original worker is active" in failed.stderr
    assert budget_path.read_bytes() == before
    assert not list(receipt_dir.glob("invocation-*"))


def test_changed_wrapper_is_rejected_before_native_execution(partial_native, tmp_path):
    run, config = partial_native
    receipt_dir = tmp_path / "tampered-wrapper"
    register = command(
        "--run", run, "--config", config, "--register-only", "--receipt-dir", receipt_dir
    )
    assert register.returncode == 0, register.stdout + register.stderr
    frozen = receipt_dir / SCRIPT.name
    frozen.write_text(frozen.read_text() + "\n# unregistered modification\n")
    failed = command("--resume", "--receipt", receipt_dir / "receipt.json", script=frozen)
    assert failed.returncode != 0
    assert "wrapper or receipt changed" in failed.stderr
