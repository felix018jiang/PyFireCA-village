"""Guard physical feasibility, durable cache identity and cumulative search budgets."""

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.strategy_io import file_sha256
from pyfireca.planning.study_io import load_study
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "area_master_runner_under_test", ROOT / "scripts/run_heritage_area_master.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config(**changes):
    return {
        "schema_version": "heritage-area-master-config-1",
        "study_path": "study.json",
        "source_fires": [{"fire_id": "fire", "protocol_path": "source.json", "draw_id": "one"}],
        "fixed_source_ids": ["S"],
        "refinement_ids": ["A", "B"],
        "seed_plans": [],
        "horizon_s": 3600,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1.0,
        "max_new_checks": 48,
        "max_wall_time_s": 14400.0,
        "max_mip_time_s": 60.0,
        "mip_call_time_limit_s": 10.0,
        "area_gap_absolute_tolerance_m2": 0.0,
        "area_gap_relative_tolerance": 0.0,
        "max_nodes": 1000,
        "max_radiation_edges": 100000,
        "minimum_free_disk_GB": 1.0,
        **changes,
    }


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def budget(runner, tmp_path, *, started_checks=0, elapsed_s=0, mip_elapsed_s=0, clock=None):
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps(
            {
                "elapsed_s": elapsed_s,
                "mip_elapsed_s": mip_elapsed_s,
                "started_checks": started_checks,
                "active": None,
            }
        )
    )
    return runner.ExecutionBudget(path, clock=Clock() if clock is None else clock)


def check(status, **evidence):
    return ProtectionCheck(
        status,
        {
            "reached_horizon": status == "passed",
            "simulated_until_s": 3600 if status == "passed" else 400,
            "invalid_reasons": [],
            "source_replaced": False,
            **evidence,
        },
    )


def area_function():
    # Shared area belongs to AB only, not either individual footprint twice.
    return build_cleared_area_function(
        [("A",), ("B",), ("A", "B"), ("S",)], [5, 3, 2, 4], area_basis="physical_atoms"
    )


def run(runner, value, book, evaluate, **kwargs):
    return runner.run_master_loop(
        value,
        candidate_ids=("A", "B"),
        scenario_ids=("fire-h1", "fire-h0.5"),
        area=area_function(),
        problem_sha256="frozen",
        evaluate=evaluate,
        budget=book,
        fixed_retained_ids=("S",),
        **kwargs,
    )


def test_master_closes_area_bound_only_after_both_supports_pass(runner, tmp_path):
    pytest.importorskip("scipy.optimize")
    calls = []

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return check(
            "passed" if ids == ("B",) else "failed", loss_metrics={"ignited_roof_area_m2": 4}
        )

    book = budget(runner, tmp_path)
    report = run(runner, config(seed_plans=[["A"]]), book, evaluate)
    book.close()
    assert report["best_ids"] == ["B"]
    assert report["global_area_gap_closed"]
    assert report["global_area_lower_bound_m2"] == pytest.approx(3)
    assert report["verified_area_upper_bound_m2"] == pytest.approx(3)
    assert (("A",), "fire-h0.5") not in calls  # A valid first failure short-circuits.
    assert (("B",), "fire-h1") in calls and (("B",), "fire-h0.5") in calls
    assert not report["global_pareto_frontier_complete"]
    assert not report["loss_tie_break_optimality_proven"]
    assert report["budget"]["started_checks"] == 4


def test_unknown_seed_stops_before_master_or_other_scenarios(runner, tmp_path):
    calls = []

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return check("unknown", reason="physical_domain_edge")

    def forbidden_master(*args, **kwargs):
        raise AssertionError("unknown must stop before a master exclusion")

    book = budget(runner, tmp_path)
    report = run(runner, config(seed_plans=[["A"]]), book, evaluate, master_solver=forbidden_master)
    book.close()
    assert len(calls) == 1
    assert report["stop_reason"] == "unknown_physical_check"
    assert report["best_ids"] is None
    assert not report["global_area_gap_closed"]
    assert report["unresolved_plans"][0]["reason"] == "physical_domain_edge"


def test_short_observation_cannot_establish_a_feasible_upper_bound(runner, tmp_path):
    book = budget(runner, tmp_path)
    report = run(
        runner,
        config(seed_plans=[["B"]]),
        book,
        lambda *_: check("passed", reached_horizon=False, simulated_until_s=1200),
    )
    book.close()
    assert report["stop_reason"] == "unknown_physical_check"
    assert report["verified_area_upper_bound_m2"] is None


def test_cached_incomplete_pass_is_normalized_before_feasible_archive(runner, tmp_path):
    cache = {
        ("frozen", ("B",), scenario): check("passed", simulated_until_s=100)
        for scenario in ("fire-h1", "fire-h0.5")
    }
    book = budget(runner, tmp_path, started_checks=2)
    report = run(runner, config(), book, lambda *_: pytest.fail("must use cache"), cache=cache)
    book.close()
    assert report["stop_reason"] == "unknown_physical_check"
    assert report["verified_area_upper_bound_m2"] is None


def test_resume_does_not_reset_started_ca_budget(runner, tmp_path):
    cache = {}
    book = budget(runner, tmp_path, started_checks=1)
    first = run(
        runner,
        config(seed_plans=[["B"]], max_new_checks=2),
        book,
        lambda *_: check("passed"),
        cache=cache,
    )
    book.close()
    assert first["stop_reason"] == "CA_check_limit"
    assert first["verified_area_upper_bound_m2"] is None
    resumed = runner.ExecutionBudget(tmp_path / "budget.json", clock=Clock())
    second = run(
        runner,
        config(seed_plans=[["B"]], max_new_checks=2),
        resumed,
        lambda *_: pytest.fail("cumulative budget forbids a new call"),
        cache=cache,
    )
    resumed.close()
    assert second["stop_reason"] == "CA_check_limit"
    assert second["budget"]["started_checks"] == 2


def test_mip_budget_is_measured_and_persisted_across_resume(runner, tmp_path):
    clock = Clock()
    book = budget(runner, tmp_path, mip_elapsed_s=4, clock=clock)

    def limited_master(*args, **kwargs):
        assert kwargs["time_limit_s"] == 1
        clock.now += 1
        return {
            "remaining_scope_empty": False,
            "remaining_master_lower_bound_m2": 0,
            "next_ids": [],
            "next_area_m2": 0,
        }

    report = run(
        runner,
        config(max_mip_time_s=5),
        book,
        lambda *_: check("failed"),
        master_solver=limited_master,
    )
    book.close()
    assert report["stop_reason"] == "MIP_time_limit"
    assert report["budget"]["mip_elapsed_s"] == 5
    resumed = runner.ExecutionBudget(tmp_path / "budget.json", clock=Clock())
    again = run(
        runner,
        config(max_mip_time_s=5),
        resumed,
        lambda *_: pytest.fail("MIP budget exhausted"),
        master_solver=lambda *_args, **_kwargs: pytest.fail("must not restart MIP budget"),
    )
    resumed.close()
    assert again["stop_reason"] == "MIP_time_limit"


def test_cache_binds_plan_scenario_and_every_required_artifact(runner, tmp_path):
    row = {
        "problem_sha256": "p",
        "demolished_ids": ["B"],
        "scenario_id": "s",
        "status": "passed",
        "evidence": check("passed").evidence,
    }
    for name in runner.REQUIRED:
        (tmp_path / name).write_text(json.dumps(row) if name == "check.json" else "artifact")
    manifest = {k: row[k] for k in ("problem_sha256", "demolished_ids", "scenario_id")}
    manifest["files_sha256"] = {name: file_sha256(tmp_path / name) for name in runner.REQUIRED}
    (tmp_path / "complete.json").write_text(json.dumps(manifest))
    assert runner.read_complete(tmp_path, "p", ids=("B",), scenario="s").status == "passed"
    with pytest.raises(ValueError, match="differs"):
        runner.read_complete(tmp_path, "p", ids=("A",), scenario="s")
    (tmp_path / "fire-loss.json").write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"):
        runner.read_complete(tmp_path, "p")


def test_unclosed_budget_and_replacement_source_cannot_pass(runner, tmp_path):
    book = budget(runner, tmp_path)
    with pytest.raises(ValueError, match="unclosed previous"):
        runner.ExecutionBudget(tmp_path / "budget.json")
    book.close()
    assert runner.checked_status(check("passed", source_replaced=True), 3600).status == "unknown"


def test_config_rejects_implicit_numerics_and_nonfinite_budgets(runner):
    runner.validate_config(config())
    with pytest.raises(ValueError, match="dual-support"):
        runner.validate_config(config(roof_scales_m=[0.5]))
    with pytest.raises(ValueError, match="max_dt_s"):
        runner.validate_config(config(max_dt_s=float("nan")))


def retained_config(**changes):
    return config(
        **(
            {
                "source_policy": "area_bounded_retained_sources",
                "source_area_limit_m2": 3,
                "source_area_tolerance_m2": 0,
                "fixed_source_ids": [],
            }
            | changes
        )
    )


def test_prefetch_collects_every_baseline_witness_before_verifying_seeds(runner, tmp_path):
    calls = []

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return check("passed" if ids == ("B",) else "failed")

    book = budget(runner, tmp_path)
    report = run(
        runner,
        config(seed_plans=[["B"]], prefetch_baseline_witnesses=True),
        book,
        evaluate,
        master_solver=lambda *_args, **_kwargs: {
            "remaining_scope_empty": True,
            "remaining_master_lower_bound_m2": None,
            "next_ids": None,
        },
    )
    book.close()
    assert calls == [((), "fire-h1"), ((), "fire-h0.5"), (("B",), "fire-h1"), (("B",), "fire-h0.5")]
    assert report["baseline_witness_checks_completed"] == 2
    assert report["baseline_witness_prefetch_all_scenarios_complete"]
    assert report["verified_area_upper_bound_m2"] == 3
    assert report["budget"]["started_checks"] == 4


def test_prefetch_unknown_stops_even_after_an_earlier_failure(runner, tmp_path):
    calls = []

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return check("failed" if scenario == "fire-h1" else "unknown", reason="incomplete")

    book = budget(runner, tmp_path)
    report = run(
        runner, config(seed_plans=[["B"]], prefetch_baseline_witnesses=True), book, evaluate
    )
    book.close()
    assert calls == [((), "fire-h1"), ((), "fire-h0.5")]
    assert report["stop_reason"] == "unknown_physical_check"
    assert not report["baseline_witness_prefetch_all_scenarios_complete"]
    assert report["verified_area_upper_bound_m2"] is None


def test_prefetch_obeys_the_same_cumulative_ca_budget(runner, tmp_path):
    calls = []
    book = budget(runner, tmp_path)

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return check("failed")

    report = run(runner, config(max_new_checks=1, prefetch_baseline_witnesses=True), book, evaluate)
    book.close()
    assert len(calls) == 1
    assert report["stop_reason"] == "CA_check_limit"
    assert report["baseline_witness_checks_completed"] == 1
    assert report["budget"]["started_checks"] == 1


def test_infeasible_declared_cap_without_verified_seed_is_not_global_infeasibility(
    runner, tmp_path
):
    pytest.importorskip("scipy.optimize")
    calls = []
    book = budget(runner, tmp_path)

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        assert not ids  # Every nonempty action costs more than this cap.
        return check("failed")

    report = run(runner, retained_config(source_area_limit_m2=2), book, evaluate)
    book.close()
    assert calls == [((), "fire-h1")]
    assert report["stop_reason"] == "no_feasible_plan_within_declared_area_cap"
    assert report["verified_area_upper_bound_m2"] is None
    assert not report["global_area_gap_closed"]
    assert report["full_candidate_space_unresolved"]
    assert report["outside_area_cap_scope"] == "unresolved_outside_declared_cap"


def test_reverified_cap_feasible_seed_can_close_full_action_area_minimum(runner, tmp_path):
    pytest.importorskip("scipy.optimize")
    calls = []
    book = budget(runner, tmp_path)

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        assert area_function()(ids) <= 3
        return check("passed" if ids == ("B",) else "failed", source_replaced=bool(ids))

    report = run(runner, retained_config(seed_plans=[["B"]]), book, evaluate)
    book.close()
    assert report["global_area_gap_closed"]
    assert report["verified_area_upper_bound_m2"] == 3
    assert report["outside_area_cap_scope"] == "area_dominated_by_verified_feasible_upper_bound"
    assert not report["initial_source_area_cap_is_verified_feasible_upper_bound"]
    assert report["verified_feasible_upper_bound_available"]
    assert len([ids for ids, _ in calls if ids == ("B",)]) == 2


def test_out_of_cap_proposal_never_reaches_the_physical_oracle(runner, tmp_path):
    book = budget(runner, tmp_path)

    def bad_proposal(*_args, **kwargs):
        assert kwargs["maximum_cleared_area_m2"] == 2
        return {
            "remaining_scope_empty": False,
            "remaining_master_lower_bound_m2": 0,
            "next_ids": ["A"],
            "next_area_m2": 5,
        }

    report = run(
        runner,
        retained_config(source_area_limit_m2=2),
        book,
        lambda *_: pytest.fail("out-of-cap oracle call"),
        master_solver=bad_proposal,
    )
    book.close()
    assert report["stop_reason"] == "MIP_proposal_outside_declared_area_cap"
    assert report["budget"]["started_checks"] == 0
    assert not report["global_area_gap_closed"]


def test_out_of_cap_cache_cannot_supply_a_verified_upper_bound(runner, tmp_path):
    cache = {("frozen", ("B",), s): check("passed") for s in ("fire-h1", "fire-h0.5")}
    book = budget(runner, tmp_path, started_checks=2)
    with pytest.raises(ValueError, match="cached plan exceeds"):
        run(
            runner,
            retained_config(source_area_limit_m2=2),
            book,
            lambda *_: check("failed"),
            cache=cache,
        )
    book.close()


def retained_fixture(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "retained_runner_fixture", ROOT / "tests/planning/test_roof_source_area_coverage.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, _, inventory, fires = fixture.inputs()
    case_dir = tmp_path / "case"
    write_v3_case_bundle(
        bundle.request,
        case_dir,
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata=bundle.metadata,
    )
    structures = tmp_path / "buildings.json"
    structures.write_text(json.dumps({"buildings": inventory.structures}))
    study = {
        "schema_version": "heritage-p1-study-1",
        "case_dir": str(case_dir),
        "structures_path": str(structures),
        "targets": [asdict(t) for t in inventory.targets],
        "candidate_ids": list(inventory.candidate_ids),
        "fixed_source_ids": [],
        "candidate_evidence": inventory.candidate_evidence,
    }
    study_path = tmp_path / "study.json"
    study_path.write_text(json.dumps(study))
    protocol = fires[0]["protocol"]
    draw = protocol["draws"][0]
    protocol["strata"] = {"all_retained_sources": list(draw["priority_ids"])}
    draw["stratum"] = "all_retained_sources"
    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    protocol_path = tmp_path / "source.json"
    protocol_path.write_text(json.dumps(protocol))
    value = retained_config(
        study_path=str(study_path),
        source_area_limit_m2=4,
        source_fires=[
            {"fire_id": "fire", "protocol_path": str(protocol_path), "draw_id": draw["draw_id"]}
        ],
        refinement_ids=["M"],
        seed_plans=[["S"]],
        enable_physical_group_cuts=True,
    )
    config_path = tmp_path / "retained-config.json"
    config_path.write_text(json.dumps(value))
    return config_path, value, protocol_path


def test_retained_factory_preserves_all_actions_and_real_backup_source_loss(runner, tmp_path):
    path, value, _ = retained_fixture(tmp_path)
    runner.validate_config(value)
    oracle, area, _, fixed = runner.make_oracle(path, value, "retained-test")
    assert oracle.candidate_ids == ("M", "S", "U")
    assert not fixed
    assert list(oracle.fires["fire"]["footprints"]) == ["S", "M"]
    assert oracle.enable_physical_group_cuts
    assert oracle.freeze_integration_membership
    output = tmp_path / "artifacts"
    output.mkdir()
    oracle.on_evaluation = lambda ids, s, c, r, layout, result: runner.persist_result(
        output, oracle, area, value, ids, s, c, r, layout, result
    )
    for scenario in oracle.scenarios:
        raw = oracle(("S",), scenario)
        assert raw.status == "passed"
        assert raw.evidence["source_replaced"] and raw.evidence["source_id"] == "M"
        folder = output / "evaluations" / content_hash([["S"], scenario])
        saved = runner.read_complete(folder, oracle.problem_sha256, ids=("S",), scenario=scenario)
        assert saved.status == "passed"
        assert saved.evidence["reached_horizon"]
        assert saved.evidence["simulated_until_s"] == 3600
        assert saved.evidence["loss_metrics"]["consumed_fuel_kg"] > 0
    with pytest.raises(ValueError, match="every inventory candidate"):
        runner.make_oracle(path, {**value, "fixed_source_ids": ["S"]}, "retained-test")
    with pytest.raises(ValueError, match="every eligible ordinary"):
        runner.make_oracle(path, {**value, "candidate_ids": ["M", "U"]}, "retained-test")
    with pytest.raises(ValueError, match="source-coverage area cap"):
        runner.make_oracle(path, {**value, "source_area_limit_m2": 3}, "retained-test")


def test_retained_loader_rejects_changed_hash_and_incomplete_global_priority(runner, tmp_path):
    path, value, protocol_path = retained_fixture(tmp_path)
    _, _, inventory = load_study(value["study_path"])
    protocol = json.loads(protocol_path.read_text())
    protocol["draws"][0]["priority_ids"] = ["S", "M"]
    protocol_path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="protocol hash"):
        runner.load_retained_fires(path, value, inventory)
    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    protocol_path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="complete all_retained_sources"):
        runner.load_retained_fires(path, value, inventory)


def test_frozen_snapshot_resume_persists_real_full_horizon_and_cumulative_budget(runner, tmp_path):
    pytest.importorskip("scipy.optimize")
    spec = importlib.util.spec_from_file_location(
        "master_synthetic_fixture", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    study_path = fixture.write_example(tmp_path / "synthetic", radius=2)
    _, _, inventory = load_study(study_path)
    protocol = freeze_source_protocol(
        inventory, seed=0, draws_per_stratum=1, area_m2=4, near_distance_m=15
    )
    draw = next(d for d in protocol["draws"] if "S" in d["priority_ids"])
    draw["priority_ids"] = ["S", *[i for i in draw["priority_ids"] if i != "S"]]
    draw["baseline_source_id"] = "S"
    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    (tmp_path / "source.json").write_text(json.dumps(protocol))
    value = config(
        study_path=str(study_path),
        source_fires=[
            {"fire_id": "fire", "protocol_path": "source.json", "draw_id": draw["draw_id"]}
        ],
        seed_plans=[["M"]],
        refinement_ids=["M"],
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="every eligible ordinary"):
        runner.make_oracle(path, {**value, "candidate_ids": ["M"]}, "test-code")
    first_oracle, _, _, _ = runner.make_oracle(path, value, "test-code")
    other_seed, _, _, _ = runner.make_oracle(path, {**value, "seed_plans": [["U"]]}, "test-code")
    smaller_dt, _, _, _ = runner.make_oracle(path, {**value, "max_dt_s": 0.5}, "test-code")
    assert first_oracle.problem_sha256 == other_seed.problem_sha256
    assert first_oracle.problem_sha256 != smaller_dt.problem_sha256
    assert first_oracle.bundle.request.scenario.max_dt_s == 1
    assert smaller_dt.bundle.request.scenario.max_dt_s == 0.5
    assert first_oracle.enable_failure_certificates
    assert first_oracle.stop_at_first_core_ignition
    assert first_oracle.problem["freeze_integration_membership"]
    output = tmp_path / "experiment"
    runner.prepare(path, output)
    command = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_area_master.py"),
        "--config",
        str(path),
        "--output",
        str(output),
        "--resume",
    ]
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stdout + first.stderr
    report = json.loads((output / "search.json").read_text())
    assert report["candidate_ids"] == ["M", "U"]
    assert report["fixed_source_ids"] == ["S"]
    assert report["protected_targets"]
    assert report["verified_area_upper_bound_m2"] is not None
    assert report["global_area_gap_closed"]
    assert not report["global_pareto_frontier_complete"]
    for folder in (output / "evaluations").iterdir():
        saved = runner.read_complete(folder, report["problem_sha256"])
        loss = json.loads((folder / "fire-loss.json").read_text())
        assert loss["fuel"]["available"]
        if saved.status == "passed":
            assert saved.evidence["simulated_until_s"] == 3600
            assert loss["saved_final_time_s"] == 3600
    usage = json.loads((output / "execution-budget.json").read_text())
    assert usage["active"] is None
    count = usage["started_checks"]
    second = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stdout + second.stderr
    usage2 = json.loads((output / "execution-budget.json").read_text())
    assert usage2["started_checks"] == count
    assert usage2["elapsed_s"] >= usage["elapsed_s"]
    assert usage2["mip_elapsed_s"] >= usage["mip_elapsed_s"]
