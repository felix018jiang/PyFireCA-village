"""Same-source joint comparisons bind vegetation geometry, clocks and cache IDs."""

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.roof_joint import RoofJointPlan
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.strategy_io import file_sha256
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "joint_comparison_runner_under_test", ROOT / "scripts/run_heritage_joint_comparison.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(runner, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "joint_comparison_physical_fixture", Path(__file__).with_name("test_roof_joint.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _, cells = fixture.joint_case.__wrapped__()
    monkeypatch.setattr(runner, "load_study", lambda _: (bundle, transport, inventory))
    monkeypatch.setattr(
        runner,
        "source_paths",
        lambda: [
            ROOT / "scripts/run_heritage_joint_comparison.py",
            ROOT / "scripts/run_heritage_fixed_comparison.py",
        ],
    )
    protocol = freeze_source_protocol(
        inventory, seed=20261111, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    source_stratum = protocol["members"]["S"]["stratum"]
    draw = next(d for d in protocol["draws"] if d["stratum"] == source_stratum)
    protocol["draws"] = [{**draw, "priority_ids": ["S"], "baseline_source_id": "S"}]
    protocol["protocol_sha256"] = content_hash(
        {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    )
    (tmp_path / "source.json").write_text(json.dumps(protocol))
    (tmp_path / "case").mkdir()
    (tmp_path / "case/synthetic-reference.txt").write_text("synthetic loader injected by fixture")
    (tmp_path / "structures.json").write_text(json.dumps({"buildings": inventory.structures}))
    (tmp_path / "study.json").write_text(
        json.dumps({"case_dir": "case", "structures_path": "structures.json"})
    )
    config = {
        "schema_version": "heritage-joint-comparison-1",
        "study_path": "study.json",
        "source_fires": [
            {"fire_id": "fixed", "protocol_path": "source.json", "draw_id": draw["draw_id"]}
        ],
        "roof_scales_m": [1.0],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 0.5,
        "horizon_s": 20.0,
        "checkpoints_s": [10.0, 20.0],
        "experimental_test_protocol": True,
        "new_independent_fire_draw_count": 0,
        "refinement_ids": ["M", "U"],
        "plans": [
            {"plan_id": "baseline", "demolished_ids": [], "cleared_physical_cell_ids": []},
            {"plan_id": "joint", "demolished_ids": ["M"], "cleared_physical_cell_ids": [cells[1]]},
        ],
        "max_nodes": 10000,
        "max_radiation_edges": 500000,
        "max_new_checks": 20,
        "max_wall_time_s": 60.0,
        "minimum_free_disk_GB": 0.000001,
    }
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(config))
    return path, config, bundle, transport, inventory, cells


def make(runner, inputs, changes=None):
    path, value, *_ = inputs
    value = value | ({} if changes is None else changes)
    path.write_text(json.dumps(value))
    oracle, _ = runner.make_oracle(path, value, "fixed-synthetic-source-code")
    return oracle


def run_live_snapshot(runner, monkeypatch, path, output):
    """Exercise live management without exec; numerical inputs remain synthetic."""
    monkeypatch.setattr(
        runner, "__file__", str(output / "source-snapshot/scripts/run_heritage_joint_comparison.py")
    )
    return runner.resume(path, output)


def test_production_protocol_rejects_unmarked_short_or_single_support(inputs, runner):
    _, value, *_ = inputs
    runner.validate_config(value)
    production = value | {
        "experimental_test_protocol": False,
        "horizon_s": 3600.0,
        "checkpoints_s": [1200.0, 2400.0, 3600.0],
        "roof_scales_m": [1.0, 0.5],
    }
    runner.validate_config(production)
    with pytest.raises(ValueError):
        runner.validate_config(value | {"experimental_test_protocol": False})
    with pytest.raises(ValueError):
        runner.validate_config(production | {"new_independent_fire_draw_count": -1})


def test_same_roof_set_with_distinct_vegetation_is_distinct_but_duplicate_joint_action_is_not(
    inputs, runner
):
    _, value, _, _, _, (full, sparse) = inputs
    variants = [
        {"plan_id": "one", "demolished_ids": ["M"], "cleared_physical_cell_ids": [full]},
        {"plan_id": "two", "demolished_ids": ["M"], "cleared_physical_cell_ids": [sparse]},
    ]
    runner.validate_config(value | {"plans": variants})
    with pytest.raises(ValueError):
        runner.validate_config(value | {"plans": [variants[0], variants[0] | {"plan_id": "alias"}]})


@pytest.mark.parametrize("bad", [True, -1, 2.5])
def test_config_rejects_noninteger_parent_cell_actions(inputs, runner, bad):
    _, value, *_ = inputs
    bad_plan = {"plan_id": "bad", "demolished_ids": [], "cleared_physical_cell_ids": [bad]}
    with pytest.raises(ValueError):
        runner.validate_config(value | {"plans": [bad_plan]})


def test_same_source_budget_common_layout_and_full_cell_action_area(inputs, runner):
    _, _, _, _, _, (_, sparse) = inputs
    oracle = make(runner, inputs)
    scenario = next(iter(oracle.scenarios))
    engine = oracle.engine_for_scenario(scenario)
    _, baseline, base_layout, _ = oracle.evaluate("baseline", scenario)
    check, changed, layout, _ = oracle.evaluate("joint", scenario)
    assert check.status in {"passed", "failed"}
    assert check.evidence["source_id"] == "S"
    assert not check.evidence["source_replaced"]
    assert check.evidence["source_budget"]["covered_area_m2"] == pytest.approx(25)
    assert baseline.metrics["actual_source_ids"] == changed.metrics["actual_source_ids"] == ["S"]
    assert engine is oracle.engine_for_scenario(scenario)
    assert base_layout.geometry == layout.geometry
    assert changed.intervention["cleared_physical_cell_ids"] == [sparse]
    assert changed.intervention["vegetation_parent_cell_footprint_area_m2"] == 25
    assert changed.intervention["cleared_vegetation_covered_area_m2"] == pytest.approx(10)
    assert changed.intervention["cleared_action_geometry_union_area_m2"] == pytest.approx(50)
    assert engine.radiation_builder.keywords["frozen_integrated_node_mask"].dtype == np.bool_


def test_noop_joint_runner_preserves_original_thermal_engine(inputs, runner):
    oracle = make(runner, inputs)
    scenario = next(iter(oracle.scenarios))
    engine = oracle.engine_for_scenario(scenario)
    _, _, _, joint = oracle.evaluate("baseline", scenario)
    original = RoofHeritageEvaluationEngine(
        engine.bundle,
        engine.transport,
        engine.inventory,
        source_policy="sampled_retained_sources",
        prepared_layout=engine.layout,
        contact_builder=engine.contact_builder,
        radiation_builder=engine.radiation_builder,
    )
    _, _, plain = original.simulate(oracle.scenario_for(scenario), BuildingDemolitionPlan("noop"))
    for field in plain.final_state.__dataclass_fields__:
        left, right = getattr(plain.final_state, field), getattr(joint.final_state, field)
        if isinstance(left, np.ndarray):
            np.testing.assert_array_equal(left, right)
        else:
            assert left == right


def test_static_wind_reaches_joint_roof_physics_and_separates_results(inputs, runner):
    _, _, bundle, *_ = inputs
    legacy = make(runner, inputs)
    wind = {"wind_speed_m_s": 0.0, "wind_from_deg": 0.0, "flame_tilt_coefficient_s_m": 0.2}
    calm = make(runner, inputs, {"static_wind": wind})
    west = make(runner, inputs, {"static_wind": dict(wind, wind_speed_m_s=2.0, wind_from_deg=270)})
    east = make(runner, inputs, {"static_wind": dict(wind, wind_speed_m_s=2.0, wind_from_deg=90)})
    assert "static_wind" not in legacy.problem
    assert west.problem["static_wind"]["direction_reference"] == "meteorological_from_grid_north"
    assert east.problem["static_wind"]["evidence"] == "synthetic_stress_test_not_calibrated"
    assert len({case.problem_sha256 for case in (legacy, calm, west, east)}) == 4
    for case in (calm, west, east):
        for field in bundle.request.parameters.__dataclass_fields__:
            np.testing.assert_array_equal(
                getattr(case.bundle.request.parameters, field),
                getattr(bundle.request.parameters, field),
            )
        assert case.bundle.request.firebrands is None
    scenario = next(iter(legacy.scenarios))
    observations = [case.evaluate("joint", scenario) for case in (legacy, calm, west, east)]
    checks, records, _, results = zip(*observations, strict=True)
    assert all(c.evidence["source_id"] == "S" for c in checks)
    for field in ("cleared_physical_cell_ids", "cleared_action_geometry_union_area_m2"):
        assert all(r.intervention[field] == records[0].intervention[field] for r in records)
    assert len({r.evaluation_sha256 for r in records}) == 4
    for field in results[0].final_state.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(results[0].final_state, field), getattr(results[1].final_state, field)
        )
    assert not np.array_equal(
        results[2].final_state.absorbed_radiation_J, results[3].final_state.absorbed_radiation_J
    )


def test_static_wind_does_not_overwrite_an_incompatible_parent(inputs, runner, monkeypatch):
    _, _, bundle, transport, inventory, _ = inputs
    edges = bundle.request.exposure_edges
    opaque = replace(edges, weight=0.5 * edges.weight)
    bundle = replace(bundle, request=replace(bundle.request, exposure_edges=opaque))
    monkeypatch.setattr(runner, "load_study", lambda _: (bundle, transport, inventory))
    wind = {"wind_speed_m_s": 2, "wind_from_deg": 270, "flame_tilt_coefficient_s_m": 0.2}
    with pytest.raises(ValueError, match="verified transparent parent graph"):
        make(runner, inputs, {"static_wind": wind})


@pytest.mark.parametrize(
    "wind", [None, {}, {"wind_speed_m_s": 2, "wind_from_deg": 0, "flame_tilt_coefficient_s_m": 0}]
)
def test_invalid_static_wind_rejected(inputs, runner, wind):
    with pytest.raises(ValueError):
        runner.validate_config(inputs[1] | {"static_wind": wind})


@pytest.mark.parametrize("identifier", ["M", "G", "S"])
def test_roof_core_and_source_parent_cells_cannot_be_relabelled_vegetation(
    inputs, runner, identifier
):
    _, _, bundle, _, inventory, _ = inputs
    oracle = make(runner, inputs)
    geometry = inventory.by_id[identifier].geometry.representative_point()
    cell = bundle.request.grid.cell_id(*bundle.request.grid.locate(geometry.x, geometry.y))
    plan = RoofJointPlan("illegal", ground_evidence="synthetic", cleared_physical_cell_ids=(cell,))
    with pytest.raises(ValueError):
        oracle.evaluate(plan, next(iter(oracle.scenarios)))


def test_prepare_resume_and_no_new_ca_on_second_resume_bind_grass_ids(
    inputs, runner, monkeypatch, tmp_path
):
    path, _, *_ = inputs
    output = tmp_path / "run"
    runner.prepare(path, output)
    first = run_live_snapshot(runner, monkeypatch, path, output)
    assert first["complete"]
    assert first["completed_checks"] == first["valid_completed_checks"] == 2
    manifests = sorted((output / "evaluations").glob("*/complete.json"))
    assert len(manifests) == 2
    rows = [json.loads(p.read_text()) for p in manifests]
    assert {tuple(row["cleared_physical_cell_ids"]) for row in rows} == {(), (inputs[-1][1],)}
    for manifest in manifests:
        row = json.loads(manifest.read_text())
        saved_check = json.loads((manifest.parent / "check.json").read_text())
        assert saved_check["cleared_physical_cell_ids"] == row["cleared_physical_cell_ids"]
    before = json.loads((output / "execution-budget.json").read_text())
    second = run_live_snapshot(runner, monkeypatch, path, output)
    after = json.loads((output / "execution-budget.json").read_text())
    assert second["complete"] and second["completed_checks"] == 2
    assert before["started_checks"] == after["started_checks"]


def test_explicit_execution_subset_preserves_full_completion_question(
    inputs, runner, monkeypatch, tmp_path
):
    path, value, *_ = inputs
    plan, scenario = next(runner.expected_checks(value))
    value = value | {
        "execution_case_selection": [{"plan_id": plan.plan_id, "scenario_id": scenario}]
    }
    path.write_text(json.dumps(value))
    assert len(tuple(runner.expected_checks(value))) == 2
    assert len(tuple(runner.execution_checks(value))) == 1
    output = tmp_path / "subset"
    runner.prepare(path, output)
    first = run_live_snapshot(runner, monkeypatch, path, output)
    assert not first["complete"]
    assert first["planned_checks"] == 2
    assert first["completed_checks"] == 1
    assert len(list((output / "evaluations").glob("*/complete.json"))) == 1
    before = json.loads((output / "execution-budget.json").read_text())
    second = run_live_snapshot(runner, monkeypatch, path, output)
    after = json.loads((output / "execution-budget.json").read_text())
    assert not second["complete"] and second["completed_checks"] == 1
    assert before["started_checks"] == after["started_checks"] == 1


@pytest.mark.parametrize(
    "selection",
    [
        [],
        "all",
        [{"plan_id": "baseline", "scenario_id": "not-declared"}],
        [{"plan_id": "not-declared", "scenario_id": "fixed-h1"}],
        [{"plan_id": "baseline", "scenario_id": "fixed-h1", "extra": 1}],
        [{"plan_id": "baseline", "scenario_id": "fixed-h1"}] * 2,
    ],
)
def test_execution_subset_rejects_missing_ambiguous_or_undeclared_calls(inputs, runner, selection):
    _, value, *_ = inputs
    with pytest.raises(ValueError):
        runner.validate_config(value | {"execution_case_selection": selection})


def test_cache_identity_includes_grass_cells_and_hashes_every_required_artifact(
    inputs, runner, monkeypatch, tmp_path
):
    path, _, *_ = inputs
    output = tmp_path / "cache-run"
    runner.prepare(path, output)
    run_live_snapshot(runner, monkeypatch, path, output)
    manifest_path = next((output / "evaluations").glob("*/complete.json"))
    manifest = json.loads(manifest_path.read_text())
    args = {
        "plan_id": manifest["plan_id"],
        "scenario_id": manifest["scenario_id"],
        "demolished_ids": manifest["demolished_ids"],
        "cleared_physical_cell_ids": manifest["cleared_physical_cell_ids"],
    }
    runner.read_complete(manifest_path.parent, manifest["problem_sha256"], **args)
    wrong = args | {"cleared_physical_cell_ids": [987654]}
    with pytest.raises(ValueError):
        runner.read_complete(manifest_path.parent, manifest["problem_sha256"], **wrong)
    (manifest_path.parent / "fire-loss.json").write_text("changed")
    with pytest.raises(ValueError):
        runner.read_complete(manifest_path.parent, manifest["problem_sha256"], **args)


def test_cumulative_check_budget_cannot_reset_on_resume(inputs, runner, monkeypatch, tmp_path):
    path, value, *_ = inputs
    path.write_text(json.dumps(value | {"max_new_checks": 1}))
    output = tmp_path / "bounded-run"
    runner.prepare(path, output)
    first = run_live_snapshot(runner, monkeypatch, path, output)
    assert not first["complete"] and first["completed_checks"] == 1
    second = run_live_snapshot(runner, monkeypatch, path, output)
    assert not second["complete"] and second["completed_checks"] == 1
    usage = json.loads((output / "execution-budget.json").read_text())
    assert usage["started_checks"] == 1


@pytest.mark.parametrize("status", ["unknown", "passed", "failed"])
def test_unknown_or_short_observation_never_counts_as_valid_complete(
    inputs, runner, monkeypatch, tmp_path, status
):
    path, value, *_ = inputs
    path.write_text(json.dumps(value | {"plans": [value["plans"][0]]}))
    output = tmp_path / f"short-{status}"
    runner.prepare(path, output)
    run_live_snapshot(runner, monkeypatch, path, output)
    manifest_path = next((output / "evaluations").glob("*/complete.json"))
    manifest = json.loads(manifest_path.read_text())
    check_path = manifest_path.parent / "check.json"
    check = json.loads(check_path.read_text())
    check["status"] = status
    check["evidence"]["reached_horizon"] = False
    check["evidence"]["simulated_until_s"] = 10.0
    check_path.write_text(json.dumps(check))
    manifest["files_sha256"]["check.json"] = file_sha256(check_path)
    manifest_path.write_text(json.dumps(manifest))
    report = runner.summarize(output)
    assert not report["complete"]
    assert report["valid_completed_checks"] == 0


@pytest.mark.parametrize("policy", ["fixed", "retained_sources"])
def test_native_cli_executes_the_frozen_snapshot_and_reuses_completed_checks(
    inputs, tmp_path, policy
):
    path, config, bundle, transport, inventory, _ = inputs
    if policy == "retained_sources":
        path, config, _ = retained_setup(inputs)
    expected_checks = len(config["plans"])
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
    output = tmp_path / "native-run"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_comparison.py"),
        "--config",
        str(path),
        "--output",
        str(output),
    ]
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    previous_checks = None
    for mode in ("--prepare-only", "--resume", "--resume"):
        completed = subprocess.run(
            [*common, mode], env=environment, capture_output=True, text=True, timeout=60
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        budget = json.loads((output / "execution-budget.json").read_text())
        assert budget["active"] is None
        if mode == "--resume":
            summary = json.loads((output / "summary.json").read_text())
            assert summary["complete"] and summary["valid_completed_checks"] == expected_checks
            if previous_checks is not None:
                assert budget["started_checks"] == previous_checks
            previous_checks = budget["started_checks"]
    assert previous_checks == expected_checks


def retained_setup(inputs, *, pool=("M", "U", "S")):
    path, value, *_ = inputs
    source_path = path.parent / "source.json"
    protocol = json.loads(source_path.read_text())
    draw = protocol["draws"][0]
    protocol["strata"] = {"all_retained_sources": list(pool)}
    protocol["members"] = {
        identifier: {**protocol["members"][identifier], "stratum": "all_retained_sources"}
        for identifier in pool
    }
    protocol["draws"] = [
        {
            **draw,
            "stratum": "all_retained_sources",
            "priority_ids": list(pool),
            "baseline_source_id": pool[0],
        }
    ]
    protocol["protocol_sha256"] = content_hash(
        {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    )
    source_path.write_text(json.dumps(protocol))
    value = value | {
        "source_policy": "retained_sources",
        "plans": [
            {"plan_id": "baseline", "demolished_ids": [], "cleared_physical_cell_ids": []},
            {"plan_id": "remove-M", "demolished_ids": ["M"], "cleared_physical_cell_ids": []},
            {"plan_id": "restore-M", "demolished_ids": ["U"], "cleared_physical_cell_ids": []},
            {
                "plan_id": "remove-M-U",
                "demolished_ids": ["M", "U"],
                "cleared_physical_cell_ids": [],
            },
        ],
    }
    path.write_text(json.dumps(value))
    return path, value, protocol


def test_retained_prefix_fires_one_source_per_arm_and_restoration_reinstates_original_source(
    inputs, runner
):
    path, value, _ = retained_setup(inputs)
    oracle, _ = runner.make_oracle(path, value, "retained-code")
    scenario = next(iter(oracle.scenarios))
    engine = oracle.engine_for_scenario(scenario)
    assert list(oracle.fires["fixed"]["footprints"]) == ["M", "U", "S"]
    expected = {"baseline": "M", "remove-M": "U", "restore-M": "M", "remove-M-U": "S"}
    checks, first_geometry = [], None
    for plan, source in expected.items():
        check, record, layout, result = oracle.evaluate(plan, scenario)
        assert check.evidence["source_id"] == source
        assert check.evidence["baseline_source_id"] == "M"
        assert check.evidence["source_replaced"] == (source != "M")
        assert check.evidence["source_budget"]["covered_area_m2"] == pytest.approx(25)
        assert record.metrics["actual_source_ids"] == [source]
        direct = [event.col for event in layout.node_request.scenario.ignitions]
        assert {owner for node in direct for owner in layout.owners[node]} == {source}
        assert result.final_state.time_s == 20
        assert oracle.engine_for_scenario(scenario) is engine
        if first_geometry is None:
            first_geometry = layout.geometry
        assert layout.geometry == first_geometry
        checks.append({"scenario_id": scenario, "plan_id": plan, "evidence": check.evidence})
    assert runner.verify_pairing(checks)
    attribution = runner.source_attribution(checks)
    assert attribution["baseline_source_checks"] == 2
    assert attribution["replacement_source_checks"] == 2
    original = next(
        g
        for g in attribution["same_actual_source_comparison_groups"]
        if g["actual_source_id"] == "M"
    )
    assert original["plan_ids"] == ["baseline", "restore-M"]


def test_fixed_mode_still_rejects_deleting_declared_source(inputs, runner):
    path, value, _ = retained_setup(inputs)
    value["source_policy"] = "fixed"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="cannot remove a source"):
        runner.make_oracle(path, value, "fixed-code")


@pytest.mark.parametrize("change", ["inventory", "priority", "stratum"])
def test_retained_protocol_validates_inventory_and_complete_priority(inputs, runner, change):
    path, value, protocol = retained_setup(inputs)
    if change == "inventory":
        protocol["inventory_sha256"] = "other-inventory"
    elif change == "priority":
        protocol["draws"][0]["priority_ids"] = ["M", "U"]
    else:
        protocol["draws"][0]["stratum"] = "unknown-stratum"
    protocol["protocol_sha256"] = content_hash(
        {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    )
    (path.parent / "source.json").write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match=r"inventory|all_retained_sources"):
        runner.make_oracle(path, value, "retained-code")


def test_no_surviving_source_is_unknown_without_running_no_fire_simulation(
    inputs, runner, monkeypatch, tmp_path
):
    path, value, _ = retained_setup(inputs, pool=("M", "U"))
    oracle, _ = runner.make_oracle(path, value, "retained-code")
    scenario = next(iter(oracle.scenarios))
    check, record, layout, result = oracle.evaluate("remove-M-U", scenario)
    assert check.status == "unknown"
    assert check.evidence["new_CA_checks"] == 0
    assert record is layout is result is None
    output = tmp_path / "retained-unknown"
    runner.prepare(path, output)
    summary = run_live_snapshot(runner, monkeypatch, path, output)
    assert not summary["complete"]
    assert summary["completed_checks"] == summary["valid_completed_checks"] == 3
    assert summary["unknown_source_checks"] == 1
    assert summary["recorded_checks"] == 4
    budget = json.loads((output / "execution-budget.json").read_text())
    assert budget["started_checks"] == 3
    again = run_live_snapshot(runner, monkeypatch, path, output)
    assert not again["complete"] and again["unknown_source_checks"] == 1
    assert json.loads((output / "execution-budget.json").read_text())["started_checks"] == 3


def test_explicit_cap_allows_forty_retained_checks_but_default_stays_twenty(inputs, runner):
    _, value, *_ = inputs
    production = value | {
        "source_policy": "retained_sources",
        "experimental_test_protocol": False,
        "horizon_s": 3600,
        "checkpoints_s": [3600],
        "roof_scales_m": [1, 0.5],
        "max_new_checks": 40,
        "max_total_checks": 40,
        "source_fires": [value["source_fires"][0] | {"fire_id": f"family-{i}"} for i in range(4)],
    }
    production["plans"] = [
        {"plan_id": f"plan-{i}", "demolished_ids": [], "cleared_physical_cell_ids": [i]}
        for i in range(5)
    ]
    runner.validate_config(production)
    del production["max_total_checks"]
    with pytest.raises(ValueError, match="max_total_checks"):
        runner.validate_config(production)


def test_retained_pairing_checks_same_source_budgets_but_separates_different_sources(
    inputs, runner
):
    path, value, _ = retained_setup(inputs)
    oracle, _ = runner.make_oracle(path, value, "retained-code")
    scenario = next(iter(oracle.scenarios))
    rows = []
    for plan in ("baseline", "remove-M", "restore-M"):
        check, _, _, _ = oracle.evaluate(plan, scenario)
        rows.append({"scenario_id": scenario, "plan_id": plan, "evidence": check.evidence})
    # A different actual source can legitimately have a different fuel budget.
    alternate = json.loads(json.dumps(rows))
    alternate[1]["evidence"]["source_budget"]["initial_dry_fuel_mass_kg"] *= 2
    assert runner.verify_pairing(alternate)
    # Restoring M must reproduce the same M-source budget as the baseline.
    bad_same_source = json.loads(json.dumps(rows))
    bad_same_source[2]["evidence"]["source_budget"]["initial_dry_fuel_mass_kg"] *= 2
    with pytest.raises(ValueError, match="source budget"):
        runner.verify_pairing(bad_same_source)
    bad_layout = json.loads(json.dumps(rows))
    bad_layout[1]["evidence"]["frozen_integrated_node_mask_sha256"] = "changed"
    with pytest.raises(ValueError, match="common layout"):
        runner.verify_pairing(bad_layout)
