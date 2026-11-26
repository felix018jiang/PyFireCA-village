"""Independent joint master binds long terminal safety and sparse joint cuts."""

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate

from pyfireca.planning.boundary_scope import (
    EDGE_INVALID,
    validate_finite_domain_mip_check,
)
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.strategy_io import file_sha256
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "joint_master_runner_under_test", ROOT / "scripts/run_heritage_joint_master.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(runner, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "joint_master_physical_fixture", Path(__file__).with_name("test_roof_joint.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _, cells = fixture.joint_case.__wrapped__()
    monkeypatch.setattr(runner, "load_study", lambda _: (bundle, transport, inventory))
    protocol = freeze_source_protocol(
        inventory, seed=20261111, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    draw = protocol["draws"][0]
    protocol["draws"] = [
        {
            **draw,
            "stratum": "all_retained_sources",
            "priority_ids": ["S", "M", "U"],
            "baseline_source_id": "S",
        }
    ]
    protocol["strata"] = {"all_retained_sources": ["S", "M", "U"]}
    protocol["protocol_sha256"] = content_hash(
        {k: v for k, v in protocol.items() if k != "protocol_sha256"}
    )
    (tmp_path / "source.json").write_text(json.dumps(protocol))
    (tmp_path / "case").mkdir()
    (tmp_path / "structures.json").write_text(json.dumps({"buildings": inventory.structures}))
    (tmp_path / "study.json").write_text(
        json.dumps(
            {
                "case_dir": "case",
                "structures_path": "structures.json",
            }
        )
    )
    config = {
        "schema_version": "heritage-joint-action-master-config-1",
        "study_path": "study.json",
        "source_policy": "area_bounded_retained_sources",
        "source_fires": [
            {"fire_id": "one", "protocol_path": "source.json", "draw_id": draw["draw_id"]}
        ],
        "refinement_ids": ["M", "U"],
        "roof_scales_m": [1.0],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 0.5,
        "horizon_s": 4000,
        "source_area_limit_m2": 50,
        "source_area_tolerance_m2": 0,
        "seed_action_plans": [{"action_ids": [f"G{cells[1]}", "M"]}],
        "prefetch_baseline_witnesses": True,
        "experimental_test_protocol": True,
        "max_nodes": 5000,
        "max_radiation_edges": 500000,
        "max_new_checks": 32,
        "max_wall_time_s": 120,
        "max_mip_time_s": 30,
        "mip_call_time_limit_s": 5,
        "minimum_free_disk_GB": 0.000001,
    }
    path = tmp_path / "joint-master.json"
    path.write_text(json.dumps(config))
    return path, config, cells


def make(runner, inputs):
    path, config, _ = inputs
    return runner.make_oracle(path, config, "joint-test-code")[0]


def shift_case_to_boundary(runner, inputs, monkeypatch):
    """Move genuine physical arrays and geometry together; no invalid flags injected."""
    bundle, transport, inventory = runner.load_study(inputs[0])
    request = bundle.request
    parameters = replace(
        request.parameters,
        **{
            name: np.roll(getattr(request.parameters, name), -2, axis=1)
            for name in request.parameters.__dataclass_fields__
        },
    )
    fields = {
        name: np.roll(value, -2, axis=1)
        for name in request.__dataclass_fields__
        if isinstance(value := getattr(request, name), np.ndarray)
        and value.shape == request.grid.shape
    }
    edges = build_v3_radiative_transport_edges(
        request.grid,
        fields["source_mask"],
        fields["target_mask"],
        parameters.receiver_area_m2,
        parameters.source_height_m,
        parameters.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
    )
    bundle = replace(
        bundle, request=replace(request, parameters=parameters, exposure_edges=edges, **fields)
    )
    inventory = replace(
        inventory,
        buildings=tuple(
            replace(b, geometry=translate(b.geometry, xoff=-10)) for b in inventory.buildings
        ),
    )
    monkeypatch.setattr(runner, "load_study", lambda _: (bundle, transport, inventory))
    inputs[0].with_name("structures.json").write_text(
        json.dumps({"buildings": inventory.structures})
    )
    protocol_path = inputs[0].with_name("source.json")
    protocol = json.loads(protocol_path.read_text())
    protocol["inventory_sha256"] = inventory.sha256
    protocol["protocol_sha256"] = content_hash(
        {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    )
    protocol_path.write_text(json.dumps(protocol))
    inputs[1]["seed_action_plans"] = [{"action_ids": ["M"]}]


def test_native_failed_prefix_and_strict_closed_pass_remain_distinct(runner, inputs):
    oracle = make(runner, inputs)
    scenario = next(iter(oracle.scenarios))
    failed = oracle((), scenario)
    assert failed.status == "failed"
    assert not failed.evidence["terminal_loss_complete"]
    assert failed.evidence["simulated_until_s"] < oracle.horizon_s
    assert (
        failed.evidence["failure_certificate"]["schema_version"]
        == "heritage-joint-physical-node-removal-cut-1"
    )
    assert runner.checked_status(failed).status == "failed"
    actions = tuple(sorted(inputs[1]["seed_action_plans"][0]["action_ids"]))
    passed = oracle(actions, scenario)
    assert passed.status == "passed"
    assert passed.evidence["terminal_loss_complete"]
    assert not passed.evidence["reached_horizon"]
    assert runner.checked_status(passed).status == "passed"
    assert (
        runner.controller.checked_status(
            passed, oracle.horizon_s, allow_source_replacement=True
        ).status
        == "unknown"
    )


def test_early_or_finite_horizon_pass_without_extinction_is_unknown(runner):
    check = ProtectionCheck("passed", {"invalid_reasons": [], "reached_horizon": True})
    assert runner.checked_status(check).status == "unknown"


def test_finite_mask_criterion_is_separate_and_preserves_real_native_edge(
    runner, inputs, monkeypatch, tmp_path
):
    shift_case_to_boundary(runner, inputs, monkeypatch)
    strict = make(runner, inputs)
    config = inputs[1] | {"terminal_validation_scope": "finite_mask_conditioned"}
    oracle = runner.make_oracle(inputs[0], config, "joint-test-code")[0]
    assert oracle.problem_sha256 != strict.problem_sha256
    assert oracle.native_problem_sha256 == strict.problem_sha256
    scenario = next(iter(oracle.scenarios))
    actions = ("M",)
    strict_check = strict(actions, scenario)
    assert strict_check.status == "unknown"
    assert strict_check.evidence["invalid_reasons"] == [EDGE_INVALID]
    assert strict_check.evidence["terminal_verification"]["temporal_closure_certificate"]
    _, universe = oracle.prepared_geometry(scenario)
    oracle.expected_partitions[scenario] = universe.physical_partition_sha256
    oracle.on_evaluation = lambda ids, sid, *native: runner.persist(
        tmp_path / "conditional", oracle, config, ids, sid, *native
    )
    check = oracle(actions, scenario)
    assert check.status == "passed"
    assert "invalid_reasons" not in check.evidence
    assert check.evidence["native_invalid_reasons"] == [EDGE_INVALID]
    assert check.evidence["native_check"]["status"] == "unknown"
    assert check.evidence["native_protection_query"]["status"] == "unknown"
    assert check.evidence["terminal_loss_complete"]
    assert not check.evidence["native_terminal_loss_complete"]
    assert validate_finite_domain_mip_check(check, analysis_problem_sha256=oracle.problem_sha256)
    folder = runner.folder_for(tmp_path / "conditional", actions, scenario)
    assert runner.read_complete(folder, oracle, actions, scenario) == check
    record = json.loads((folder / "evaluation.json").read_text())
    assert record["diagnostics"]["invalid_reasons"] == [EDGE_INVALID]
    with pytest.raises(ValueError, match="native flags cannot be cleared"):
        runner.checked_status(ProtectionCheck("passed", {**check.evidence, "invalid_reasons": []}))


def test_conditional_edge_core_prefix_is_failed_but_not_a_complete_fire_or_strong_cut(
    runner, inputs, monkeypatch
):
    shift_case_to_boundary(runner, inputs, monkeypatch)
    config = inputs[1] | {"terminal_validation_scope": "finite_mask_conditioned"}
    oracle = runner.make_oracle(inputs[0], config, "edge-prefix")[0]
    check = oracle((), next(iter(oracle.scenarios)))
    assert check.status == "failed"
    assert check.evidence["native_invalid_reasons"] == [EDGE_INVALID]
    assert check.evidence["native_protection_query"]["status"] == "unknown"
    assert not check.evidence["terminal_loss_complete"]
    assert not check.evidence["finite_domain_verification"]["finite_domain_closed"]
    assert "failure_certificate" not in check.evidence
    assert runner.checked_status(check).status == "failed"


def test_conditional_unclosed_cap_is_unknown_and_retains_native_observation(
    runner, inputs, monkeypatch, tmp_path
):
    shift_case_to_boundary(runner, inputs, monkeypatch)
    config = inputs[1] | {
        "terminal_validation_scope": "finite_mask_conditioned",
        "horizon_s": 0.1,
    }
    oracle = runner.make_oracle(inputs[0], config, "short-cap")[0]
    scenario = next(iter(oracle.scenarios))
    _, universe = oracle.prepared_geometry(scenario)
    oracle.expected_partitions[scenario] = universe.physical_partition_sha256
    oracle.on_evaluation = lambda ids, sid, *native: runner.persist(
        tmp_path / "unknown-cap", oracle, config, ids, sid, *native
    )
    check = oracle(("M",), scenario)
    assert check.status == "unknown"
    assert check.evidence["reached_horizon"]
    assert check.evidence["native_invalid_reasons"] == [EDGE_INVALID]
    assert not check.evidence["terminal_loss_complete"]
    assert not check.evidence["finite_domain_verification"]["finite_domain_closed"]
    assert "failure_certificate" not in check.evidence
    folder = runner.folder_for(tmp_path / "unknown-cap", ("M",), scenario)
    assert runner.read_complete(folder, oracle, ("M",), scenario).status == "unknown"


def test_logical_universe_same_across_supports_and_total_cap_includes_grass(runner, inputs):
    path, config, cells = inputs
    config = config | {"roof_scales_m": [1.0, 0.5]}
    oracle = runner.make_oracle(path, config, "two-supports")[0]
    assert list(oracle.scenarios) == ["one-h1", "one-h0.5"]
    physical = []
    for scenario in oracle.scenarios:
        _, universe = oracle.prepared_geometry(scenario)
        assert universe.sha256 == oracle.logical_universe.sha256
        physical.append(universe.physical_partition_sha256)
    assert physical[0] != physical[1]
    assert oracle.logical_universe.area((f"G{cells[1]}", "M")) == 50
    with pytest.raises(ValueError, match="joint plan exceeds"):
        oracle.source_coverages["one"].validate_actions((f"G{cells[0]}", f"G{cells[1]}", "M"))


def test_native_cache_roundtrip_binds_joint_schema_state_and_partition(runner, inputs, tmp_path):
    oracle = make(runner, inputs)
    scenario = next(iter(oracle.scenarios))
    _, universe = oracle.prepared_geometry(scenario)
    oracle.expected_partitions = {scenario: universe.physical_partition_sha256}
    output = tmp_path / "native"
    oracle.on_evaluation = lambda ids, sid, *native: runner.persist(
        output, oracle, inputs[1], ids, sid, *native
    )
    original = oracle((), scenario)
    folder = runner.folder_for(output, (), scenario)
    loaded = runner.read_complete(folder, oracle, (), scenario)
    assert loaded == original
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        arrays = {key: saved[key].copy() for key in saved.files}
    arrays["fuel_remaining_kg"].ravel()[0] += 1
    np.savez_compressed(folder / "final-state.npz", **arrays)
    manifest = json.loads((folder / "complete.json").read_text())
    manifest["files_sha256"]["final-state.npz"] = file_sha256(folder / "final-state.npz")
    (folder / "complete.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="state or historical peak"):
        runner.read_complete(folder, oracle, (), scenario)


def test_pre_native_unknown_is_durable_and_never_feasible(runner, inputs, tmp_path):
    oracle = make(runner, inputs)
    scenario = next(iter(oracle.scenarios))
    check = ProtectionCheck("unknown", {"reason": "no_retained_source", "new_CA_checks": 0})
    runner.persist_unknown(tmp_path, oracle, (), scenario, check)
    loaded = runner.read_complete(runner.folder_for(tmp_path, (), scenario), oracle, (), scenario)
    assert loaded.status == "unknown" and loaded.evidence["new_CA_checks"] == 0


@pytest.mark.parametrize("terminal_scope", ["strict", "finite_mask_conditioned"])
def test_joint_master_adapter_and_controller_close_only_native_terminal_UB(
    runner, inputs, tmp_path, terminal_scope
):
    pytest.importorskip("scipy.optimize")
    inputs[1]["terminal_validation_scope"] = terminal_scope
    oracle = make(runner, inputs)
    for scenario in oracle.scenarios:
        _, universe = oracle.prepared_geometry(scenario)
        oracle.expected_partitions[scenario] = universe.physical_partition_sha256
    budget_path = tmp_path / "budget.json"
    budget_path.write_text(
        json.dumps({"started_checks": 0, "elapsed_s": 0, "mip_elapsed_s": 0, "active": None})
    )
    budget = runner.controller.ExecutionBudget(budget_path)
    calls = []

    def evaluate(ids, scenario):
        calls.append((tuple(ids), scenario))
        return oracle(ids, scenario)

    config = inputs[1] | {
        "seed_plans": [{"demolished_ids": inputs[1]["seed_action_plans"][0]["action_ids"]}]
    }
    try:
        report = runner.controller.run_master_loop(
            config,
            candidate_ids=oracle.candidate_ids,
            scenario_ids=tuple(oracle.scenarios),
            area=oracle.logical_universe.area,
            problem_sha256=oracle.problem_sha256,
            evaluate=evaluate,
            budget=budget,
            master_solver=runner.master_adapter(oracle),
            status_checker=runner.checked_status,
        )
    finally:
        budget.close()
    assert report["verified_area_upper_bound_m2"] is not None
    assert report["global_minimum_area_proven"]
    assert len(calls) == len(set(calls))
    first = runner.publish_report(tmp_path, oracle, report)
    second = runner.publish_report(tmp_path, oracle, report)
    assert first == second
    assert "joint_geometric" in first["scope"]
    assert first["registered_grass_cell_count"] == 2


@pytest.mark.parametrize("terminal_scope", ["strict", "finite_mask_conditioned"])
def test_native_cli_freezes_joint_schema_and_reuses_cumulative_cache(
    runner, inputs, tmp_path, monkeypatch, terminal_scope
):
    pytest.importorskip("scipy.optimize")
    path, config, _ = inputs
    config["terminal_validation_scope"] = terminal_scope
    if terminal_scope == "finite_mask_conditioned":
        shift_case_to_boundary(runner, inputs, monkeypatch)
        config["seed_action_plans"] = [{"action_ids": ["M"]}]
    bundle, transport, inventory = runner.load_study(path)
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
    path.write_text(json.dumps(config))
    output = tmp_path / "native-joint-master"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_master.py"),
        "--config",
        str(path),
        "--output",
        str(output),
    ]
    previous = None
    for mode in ("--prepare-only", "--resume", "--resume"):
        process = subprocess.run(
            [*common, mode],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert process.returncode == 0, process.stdout + process.stderr
        budget = json.loads((output / "execution-budget.json").read_text())
        assert budget["active"] is None
        if mode == "--resume":
            report = json.loads((output / "search.json").read_text())
            assert report["global_minimum_area_proven"]
            assert report["strict_extinction_required_for_feasible"]
            assert report["old_roof_or_phase_certificates_imported"] is False
            if previous is not None:
                assert budget["started_checks"] == previous
            previous = budget["started_checks"]
    assert (output / "source-snapshot/src/pyfireca/planning/joint_action_prototype.py").is_file()
    assert (output / "source-snapshot/src/pyfireca/planning/engines/joint_search.py").is_file()
