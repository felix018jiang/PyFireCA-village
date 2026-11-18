"""A seed-only wrapper keeps the frozen physical manager and cumulative cache."""

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
from test_joint_master_runner import inputs as inputs
from test_joint_master_runner import runner as runner

from pyfireca.planning.strategy_io import file_sha256
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def schedule():
    spec = importlib.util.spec_from_file_location(
        "joint_seed_schedule_test", ROOT / "scripts/resume_heritage_joint_seed_schedule.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_seed_copy_changes_only_priority_and_preserves_old_order(schedule):
    config = {
        "seed_plans": [{"demolished_ids": ["A", "G3"]}, ["B"]],
        "seed_action_plans": [{"action_ids": ["A", "G3"]}],
        "prefetch_baseline_witnesses": True,
        "max_new_checks": 128,
        "source_area_limit_m2": 1531.6,
    }
    before = deepcopy(config)
    changed = schedule.prioritized_seeds(config, ("C",))
    assert changed["seed_plans"] == [{"demolished_ids": ["C"]}, *before["seed_plans"]]
    assert {k: v for k, v in changed.items() if k != "seed_plans"} == {
        k: v for k, v in before.items() if k != "seed_plans"
    }
    assert config == before


def test_mixed_physical_imports_cannot_enter_frozen_manager(schedule, tmp_path):
    with pytest.raises(ValueError, match="fresh process"):
        schedule.load_frozen_joint_manager(tmp_path)


def test_defer_changes_only_baseline_timing_and_retains_all_constraints(schedule):
    config = {
        "seed_plans": [["A"]],
        "prefetch_baseline_witnesses": True,
        "horizon_s": 172800,
        "max_new_checks": 128,
        "source_area_limit_m2": 1531.6,
    }
    before = deepcopy(config)
    result = schedule.prioritized_seeds(config, ("B",), baseline_prefetch="defer")
    assert result["prefetch_baseline_witnesses"] is False
    assert result["seed_plans"] == [{"demolished_ids": ["B"]}, ["A"]]
    assert {
        k: v for k, v in result.items() if k not in {"seed_plans", "prefetch_baseline_witnesses"}
    } == {k: v for k, v in before.items() if k not in {"seed_plans", "prefetch_baseline_witnesses"}}
    assert config == before


@pytest.mark.parametrize("baseline_policy", ["declared", "defer"])
def test_real_frozen_prepare_wrapper_inspect_resume_and_cache_reuse(
    runner, inputs, tmp_path, baseline_policy
):
    pytest.importorskip("scipy.optimize")
    config_path, config, _ = inputs
    config["roof_scales_m"] = [1.0, 0.5]
    bundle, transport, inventory = runner.load_study(config_path)
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
                "targets": [asdict(t) for t in inventory.targets],
                "fixed_source_ids": list(inventory.fixed_source_ids),
                "candidate_ids": list(inventory.candidate_ids),
                "candidate_evidence": inventory.candidate_evidence,
            }
        )
    )
    config_path.write_text(json.dumps(config))
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "plans": [
                    {
                        "plan_id": "new-first",
                        "demolished_ids": ["M"],
                        "cleared_physical_cell_ids": [],
                    }
                ]
            }
        )
    )
    output = tmp_path / "frozen-run"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    prepare = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_heritage_joint_master.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--prepare-only",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert prepare.returncode == 0, prepare.stdout + prepare.stderr
    experiment = json.loads((output / "experiment.json").read_text())
    config_before, experiment_before = (
        config_path.read_bytes(),
        (output / "experiment.json").read_bytes(),
    )
    budget_before = (output / "execution-budget.json").read_bytes()
    command = [
        sys.executable,
        str(ROOT / "scripts/resume_heritage_joint_seed_schedule.py"),
        "--run",
        str(output),
        "--config",
        str(config_path),
        "--proposal",
        str(proposal),
        "--plan-id",
        "new-first",
        "--baseline-prefetch",
        baseline_policy,
    ]
    inspect = subprocess.run(
        [*command, "--inspect-only"], capture_output=True, text=True, env=env, timeout=60
    )
    assert inspect.returncode == 0, inspect.stdout + inspect.stderr
    observed = json.loads(inspect.stdout)
    assert observed["problem_sha256"] == experiment["problem_sha256"]
    assert observed["proposal_action_ids"] == ["M"]
    assert observed["proposal_construction_C_m2"] == 25
    assert not observed["proposal_is_verified_feasible_upper_bound"]
    assert observed["new_CA_checks_in_inspection"] == 0
    assert observed["baseline_prefetch_declared"] is True
    assert observed["baseline_prefetch_effective"] is (baseline_policy == "declared")
    assert observed["candidate_feasibility_still_requires_all_declared_scenarios"]
    assert (output / "execution-budget.json").read_bytes() == budget_before
    assert not (output / "schedules").exists()
    previous_checks = None
    original_native = None
    for iteration in range(2):
        resumed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        report = json.loads((output / "search.json").read_text())
        assert report["problem_sha256"] == experiment["problem_sha256"]
        assert report["verified_area_upper_bound_m2"] == 25
        seeds = [r["action_ids"] for r in report["trace"] if r["seed_verification"]]
        assert seeds[0] == ["M"]
        assert report["global_minimum_area_proven"]
        assert report["baseline_witness_prefetch_requested"] is (baseline_policy == "declared")
        assert report["baseline_witness_prefetch_all_scenarios_complete"] is (
            baseline_policy == "declared"
        )
        if baseline_policy == "defer":
            assert [r["action_ids"] for r in report["trace"][:2]] == [["M"], ["M"]]
            assert {r["scenario_id"] for r in report["trace"][:2]} == {"one-h1", "one-h0.5"}
            if iteration == 0:
                assert any(
                    r["action_ids"] == [] and not r["seed_verification"] for r in report["trace"]
                )
        positive = []
        for manifest_path in (output / "evaluations").glob("*/complete.json"):
            manifest = json.loads(manifest_path.read_text())
            if manifest["action_ids"] == ["M"]:
                positive.append(
                    (
                        manifest["scenario_id"],
                        json.loads((manifest_path.parent / "check.json").read_text())["status"],
                    )
                )
        assert set(positive) == {("one-h1", "passed"), ("one-h0.5", "passed")}
        checks = json.loads((output / "execution-budget.json").read_text())["started_checks"]
        native = {
            str(p.relative_to(output)): file_sha256(p)
            for p in (output / "evaluations").glob("*/*")
            if p.is_file()
        }
        if previous_checks is not None:
            assert checks == previous_checks
            assert native == original_native
        previous_checks, original_native = checks, native
    receipts = list((output / "schedules").glob("*/receipt.json"))
    assert len(receipts) == 2
    for path in receipts:
        receipt = json.loads(path.read_text())
        assert receipt["problem_sha256"] == experiment["problem_sha256"]
        assert receipt["native_problem_sha256"] == experiment["native_problem_sha256"]
        assert not receipt["native_evidence_injected"]
        assert not receipt["budget_reset_or_limits_changed"]
        assert receipt["baseline_prefetch_policy"] == baseline_policy
        assert receipt["no_scenario_dropped"]
    assert config_path.read_bytes() == config_before
    assert (output / "experiment.json").read_bytes() == experiment_before
    assert all(
        file_sha256(output / "source-snapshot" / p) == digest
        for p, digest in experiment["source_sha256"].items()
    )
