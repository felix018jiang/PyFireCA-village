"""Complementary native cases require identical actions, physics and criteria."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def composer():
    sys.path.insert(0, str(ROOT / "scripts"))
    path = ROOT / "scripts/compose_heritage_joint_cases.py"
    spec = importlib.util.spec_from_file_location("case_compositor_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observations(count=5, area=100):
    return {
        "A_burned_out_m2": area,
        "A_affected_m2": area,
        "A_active_m2": 0,
        "fire_depleted_pyrolysable_fuel_kg": area * 2,
        "N_burned_out": {"lower": count - 1, "upper": count},
        "N_affected": {"lower": count - 1, "upper": count},
        "unrepresented_original_ids": [],
    }


def fragment(
    completed,
    *,
    mode="finite_domain_closed",
    name="roof-seed",
    problem="nativeA",
    grass=(),
    all_scenarios=None,
):
    scenarios = all_scenarios or [f"family{i}-h{scale}" for i in range(4) for scale in (1, 0.5)]
    observed = {scenario: observations() for scenario in completed}
    row = {
        "plan_id": name,
        "problem_sha256": problem,
        "demolished_ids": ["R"],
        "cleared_physical_cell_ids": list(grass),
        "cleared_area_m2": 25 + 25 * len(grass),
        "scenario_observations": observed,
        "status": "unknown",
        "objective_values": None,
        "worst_case": None,
    }
    native, conditional, bindings = [], [], []
    for scenario in scenarios:
        done = scenario in completed
        native.append(
            {
                "plan_id": name,
                "scenario_id": scenario,
                "status": ("passed" if mode == "strict" else "unknown")
                if done
                else "not_evaluated",
                "terminal_complete": done and mode == "strict",
                "invalid_reasons": ["fire_reached_physical_domain_edge"]
                if done and mode != "strict"
                else [],
            }
        )
        if done:
            conditional.append(
                {
                    "plan_id": name,
                    "scenario_id": scenario,
                    "finite_domain_closed": True,
                    "conditional_status": "passed",
                }
            )
            bindings.append(
                {
                    "plan_id": name,
                    "scenario_id": scenario,
                    "manifest_path": "immutable/complete.json",
                    "manifest_sha256": "manifest",
                    "native_behavior_fingerprint": {
                        "physical_request_sha256": "request",
                        "state_and_peak_fingerprints": {
                            "actual_state_arrays_sha256": "state",
                            "historical_valid_temperature_maximum_sha256": "peak",
                        },
                        "actual_stop_time_s": 43140.0,
                        "actual_step_index": 86280,
                        "actual_source": {"id": "S", "area": 25},
                    },
                }
            )
    report = {
        "temporal_mode": mode,
        "run_root": "immutable-" + problem,
        "native_comparison_problem_sha256": problem,
        "analysis_problem_sha256": "analysis-" + problem,
        "native_checks": native,
        "finite_domain_checks": conditional,
        "archive": {
            "comparison_protocol": {"scenario_ids": scenarios},
            "unrepresented_original_ids": [] if completed else None,
            "objective_names": ["C", "Nupper", "A"],
            "fully_evaluated_feasible_plans": [],
            "unresolved_plans": [row],
            "failed_plans": [],
        },
    }
    audit = {
        "physical_plan_contracts": {
            name: {
                "demolished_ids": ["R"],
                "cleared_physical_cell_ids": list(grass),
                "ground_policy": "cleared_inert",
                "overlap_policy": "retain_shared_roof",
            }
        },
        "artifact_bindings": bindings,
    }
    return report, audit, {"scenario_ids": scenarios}


@pytest.mark.parametrize("mode", ["strict", "finite_domain_closed"])
def test_eight_cases_composed_without_relabelling_original_native_results(composer, mode):
    first, a, receipt = fragment(["family0-h1", "family0-h0.5"], mode=mode)
    rest = [
        s
        for s in receipt["scenario_ids"]
        if s not in first["archive"]["unresolved_plans"][0]["scenario_observations"]
    ]
    second, b, _ = fragment(rest, mode=mode, name="same-physical-actions", problem="nativeB")
    before = deepcopy([first, second, a, b])
    output = composer.compose_cases([first, second], [a, b], receipt)
    assert output["complete_feasible_plan_count"] == 1
    row = output["non_dominated_archive"][0]
    assert row["objective_values"] == [25, 5, 100]
    assert len(row["scenario_observations"]) == 8
    assert {p["native_problem_sha256"] for p in row["plan_aliases"]} == {"nativeA", "nativeB"}
    assert all(
        c["native_strict_status"] == ("passed" if mode == "strict" else "unknown")
        for value in row["case_composition_evidence"].values()
        for c in value["original_native_cases"]
        if c["binding"] is not None
    )
    assert [first, second, a, b] == before
    assert not output["global_pareto_frontier_complete"]


def test_one_missing_or_nonclosed_case_remains_unknown(composer):
    first, a, receipt = fragment(["family0-h1", "family0-h0.5"])
    rest = receipt["scenario_ids"][2:-1]
    second, b, _ = fragment(rest, problem="nativeB")
    output = composer.compose_cases([first, second], [a, b], receipt)
    assert output["complete_feasible_plan_count"] == 0
    assert len(output["unresolved_plans"]) == 1
    assert len(output["unresolved_plans"][0]["scenario_observations"]) == 7
    second["finite_domain_checks"][0]["finite_domain_closed"] = False
    with pytest.raises(ValueError, match="independently complete"):
        composer.compose_cases([first, second], [a, b], receipt)


@pytest.mark.parametrize("change", ["request", "state", "peak", "time", "step", "source", "loss"])
def test_duplicate_case_contradictions_cannot_be_chosen_away(composer, change):
    first, a, receipt = fragment(["family0-h1"])
    second, b, _ = fragment(["family0-h1"], problem="nativeB")
    witness = b["artifact_bindings"][0]["native_behavior_fingerprint"]
    if change == "request":
        witness["physical_request_sha256"] = "other"
    elif change == "state":
        witness["state_and_peak_fingerprints"]["actual_state_arrays_sha256"] = "other"
    elif change == "peak":
        witness["state_and_peak_fingerprints"]["historical_valid_temperature_maximum_sha256"] = (
            "other"
        )
    elif change == "time":
        witness["actual_stop_time_s"] = 43141.0
    elif change == "step":
        witness["actual_step_index"] += 1
    elif change == "source":
        witness["actual_source"]["area"] = 26
    else:
        second["archive"]["unresolved_plans"][0]["scenario_observations"]["family0-h1"] = (
            observations(area=99)
        )
    with pytest.raises(ValueError, match="contradictory duplicate"):
        composer.compose_cases([first, second], [a, b], receipt)


def test_equal_duplicates_are_deduplicated_but_different_actions_are_separate(composer):
    first, a, receipt = fragment(["family0-h1"])
    second, b, _ = fragment(["family0-h1"], problem="nativeB")
    output = composer.compose_cases([first, second], [a, b], receipt)
    assert (
        output["unresolved_plans"][0]["case_composition_evidence"]["family0-h1"][
            "duplicate_actual_case_count"
        ]
        == 1
    )
    second, b, _ = fragment(receipt["scenario_ids"][1:], problem="nativeB", grass=(9,))
    output = composer.compose_cases([first, second], [a, b], receipt)
    assert output["declared_unique_physical_action_count"] == 2
    assert output["complete_feasible_plan_count"] == 0


def test_closed_core_failure_and_strict_finite_mixing_stay_hard_failures(composer):
    first, a, receipt = fragment([], mode="strict")
    second, b, _ = fragment(["family0-h1"])
    with pytest.raises(ValueError, match="cannot be pooled"):
        composer.compose_cases([first, second], [a, b], receipt)
    first, a, receipt = fragment(["family0-h1"])
    first["archive"]["unresolved_plans"][0]["scenario_observations"] = {}
    first["finite_domain_checks"] = [
        {
            "plan_id": "roof-seed",
            "scenario_id": "family0-h1",
            "finite_domain_closed": True,
            "conditional_status": "failed",
        }
    ]
    output = composer.compose_cases([first], [a], receipt)
    assert len(output["failed_plans"]) == 1
    assert output["complete_feasible_plan_count"] == 0


def clock_fixture(composer, tmp_path):
    new = (ROOT / composer.SOLVER).read_bytes()
    assert new.count(composer.ADDED_CLOCK_BRANCH) == 1
    old = new.replace(composer.ADDED_CLOCK_BRANCH, b"", 1)
    oldpath, newpath = tmp_path / "old.py", tmp_path / "new.py"
    oldpath.write_bytes(old)
    newpath.write_bytes(new)
    audit = {
        "schema_version": "v3-sample-clock-error-path-extension-audit-1",
        "old_solver_sha256": composer.single.file_sha256(oldpath),
        "new_solver_sha256": composer.single.file_sha256(newpath),
        "exact_added_utf8": composer.ADDED_CLOCK_BRANCH.decode(),
    }
    audit["audit_sha256"] = composer.single.content_hash(audit)
    path = tmp_path / "clock.json"
    path.write_text(json.dumps(audit))
    return path, oldpath, newpath


def test_clock_bridge_accepts_only_the_exact_prior_error_path_addition(composer, tmp_path):
    path, old, new = clock_fixture(composer, tmp_path)
    proof = composer.verify_clock_bridge(path, [old, new])
    assert proof["entire_file_identity_after_removing_exact_added_branch"]
    assert not proof["other_physical_source_differences_permitted"]
    new.write_bytes(new.read_bytes() + b"\n# another change\n")
    value = json.loads(path.read_text())
    value["new_solver_sha256"] = composer.single.file_sha256(new)
    value.pop("audit_sha256")
    value["audit_sha256"] = composer.single.content_hash(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="exactly the four-line"):
        composer.verify_clock_bridge(path, [old, new])


def test_clock_bridge_does_not_silently_allow_other_physical_leaves(composer, tmp_path):
    path, old, new = clock_fixture(composer, tmp_path)
    proof = composer.verify_clock_bridge(path, [old, new])
    shared_path = ROOT / "tests/planning/test_joint_pareto_merge.py"
    spec = importlib.util.spec_from_file_location("clock_equivalence_fixture", shared_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a = module.audit(composer.catalogs)
    b = deepcopy(a)
    a["protocol"]["source_sha256"][composer.SOLVER] = proof["old_solver_sha256"]
    b["protocol"]["source_sha256"][composer.SOLVER] = proof["canonical_extended_solver_sha256"]
    for value in (a, b):
        value["physical_protocol_sha256"] = composer.single.content_hash(value["protocol"])
    with pytest.raises(ValueError, match="not equivalent"):
        composer.canonical_equivalence([a, b], expected_scenario_count=1)
    composer.canonical_equivalence([a, b], expected_scenario_count=1, clock_bridge=proof)
    b["protocol"]["source_sha256"]["src/solver.py"] = "different heat model"
    b["physical_protocol_sha256"] = composer.single.content_hash(b["protocol"])
    with pytest.raises(ValueError, match="not equivalent"):
        composer.canonical_equivalence([a, b], expected_scenario_count=1, clock_bridge=proof)


def test_original_runtime_attestation_requires_versions_problem_and_evidence(composer, tmp_path):
    evidence = tmp_path / "launch.txt"
    evidence.write_text("execution-controller-launch-history")
    runtime = {
        key: "original"
        for key in (
            "python_version",
            "numpy_version",
            "scipy_version",
            "shapely_version",
            "geos_version",
            "machine",
        )
    }
    runtime["python_executable"] = str(Path(sys.executable).resolve())
    value = {
        "schema_version": "heritage-native-runtime-attestation-1",
        "native_problem_sha256s": ["nativeA"],
        "runtime": runtime,
        "attestation_basis": "controller history",
        "evidence_references": [
            {"path": str(evidence), "sha256": composer.single.file_sha256(evidence)}
        ],
        "attested_by": "controller",
    }
    value["receipt_sha256"] = composer.single.content_hash(value)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value))
    report = {"native_comparison_problem_sha256": "nativeA"}
    audit = {"reader_runtime": runtime}
    result = composer.verify_runtime_attestation(path, [report], [audit])
    assert not result["original_archives_embedded_this_runtime_observation"]
    changed = {"reader_runtime": {**runtime, "numpy_version": "different"}}
    with pytest.raises(ValueError, match="attested original runtime"):
        composer.verify_runtime_attestation(path, [report], [changed])
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        composer.verify_runtime_attestation(path, [report], [audit])
