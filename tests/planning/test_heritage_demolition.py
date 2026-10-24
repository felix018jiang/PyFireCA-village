"""Physical demolition and fixed-object protection, including failure cases."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate
from shapely.geometry import box, mapping

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.heritage import HeritageEvaluationEngine, compare_retained_ordinary
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import HeritageTarget, audit_inventory, bind_labelme_targets
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.interventions.clearing import InterventionConflict
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.vegetation_prepare import geometry_to_polygons
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load_module("heritage_synthetic", "examples/research/heritage/synthetic_case.py")


def setup(**kwargs):
    bundle, transport, inventory, scenario = fixture.make_case(**kwargs)
    return HeritageEvaluationEngine(bundle, transport, inventory), scenario


def remove(*ids):
    return BuildingDemolitionPlan("remove", tuple(ids), ground_evidence="synthetic_cleared_ground")


def test_zero_demolition_reproduces_original_thermal_state():
    engine, scenario = setup()
    original = engine.bundle.request
    before = request_fingerprint(original)
    record, request, simulated = engine.simulate(scenario, BuildingDemolitionPlan("baseline"))
    native = run_v3_simulation(original, retain_snapshots=False)
    assert request_fingerprint(request) == before
    for name in native.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(native.final_state, name), getattr(simulated.final_state, name)
        )
    assert record.diagnostics["reached_requested_horizon"]
    assert record.diagnostics["parent_case_unchanged"]
    assert request_fingerprint(original) == before


def test_demolition_rebuilds_physics_and_protects_both_fixed_cores():
    engine, scenario = setup()
    old = engine.bundle.request
    changed, observer, ledger = engine.prepare(scenario, remove("M"))
    middle = old.building_index == engine.inventory.by_id["M"].object_id
    assert not changed.burnable_mask[middle].any()
    assert not changed.source_mask[middle].any()
    assert not changed.target_mask[middle].any()
    assert np.all(changed.building_index[middle] == -1)
    for name in changed.parameters.__dataclass_fields__:
        assert not getattr(changed.parameters, name)[middle].any()
    for identifier in ("G", "F", "S", "U"):
        original_mask = old.building_index == engine.inventory.by_id[identifier].object_id
        for name in changed.parameters.__dataclass_fields__:
            np.testing.assert_array_equal(
                getattr(old.parameters, name)[original_mask],
                getattr(changed.parameters, name)[original_mask],
            )
    assert "M" not in [obj.label for obj in observer.objects]
    assert ledger["demolished_footprint_area_m2"] == 4
    assert ledger["removed_dry_fuel_kg"] == pytest.approx(72)
    assert ledger["affected_household_count"] is None
    baseline = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    treated = engine.evaluate(scenario, remove("M"))
    assert baseline.metrics["ignited_core_ids"] == ["F", "G"]
    assert treated.metrics["protection_status"] == "passed"
    assert treated.metrics["core_count"] == 2
    assert treated.diagnostics["source_verified"]
    assert (
        max(treated.diagnostics["energy_closure_errors_J"].values())
        <= treated.diagnostics["energy_tolerance_J"]
    )


def test_radiation_can_cross_demolished_gap_and_still_ignite_heritage():
    # Explicit susceptible-fuel stress control, not a change fitted to site data.
    engine, scenario = setup(radius=8, ignition_temperature_K=350)
    request, _, _ = engine.prepare(scenario, remove("M"))
    source = np.flatnonzero(request.building_index.ravel() == engine.inventory.by_id["S"].object_id)
    core = np.flatnonzero(request.building_index.ravel() == engine.inventory.by_id["G"].object_id)
    edges = request.exposure_edges
    assert np.any(np.isin(edges.source, source) & np.isin(edges.target, core) & (edges.weight > 0))
    record = engine.evaluate(scenario, remove("M"))
    assert record.metrics["protection_status"] == "failed"
    assert record.metrics["ignited_core_ids"] == ["F", "G"]


def test_core_early_stop_does_not_stop_at_ordinary_source_or_middle_ignition():
    engine, scenario = setup()
    failed = engine.evaluate(
        scenario, BuildingDemolitionPlan("baseline"), stop_at_first_core_ignition=True
    )
    assert failed.metrics["protection_status"] == "failed"
    assert 0 < failed.diagnostics["simulated_until_s"] < scenario.horizon_s
    assert failed.metrics["ignited_secondary_ordinary_ids"] == ["M"]
    passed = engine.evaluate(scenario, remove("M"), stop_at_first_core_ignition=True)
    assert passed.metrics["protection_status"] == "passed"
    assert passed.diagnostics["reached_requested_horizon"]


def test_ordinary_loss_comparison_keeps_identical_surviving_cohort():
    engine, scenario = setup()
    baseline = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    treated = engine.evaluate(scenario, remove("M"))
    comparison = compare_retained_ordinary(baseline, treated)
    assert comparison["cohort_ids"] == ["S", "U"]
    assert comparison["baseline_ignited_ids"] == comparison["treated_ignited_ids"] == ["S"]
    assert comparison["planned_demolished_ids"] == ("M",)
    early = engine.evaluate(
        scenario, BuildingDemolitionPlan("baseline"), stop_at_first_core_ignition=True
    )
    with pytest.raises(ValueError, match="full-horizon"):
        compare_retained_ordinary(early, treated)
    moved = replace(scenario, ignitions=(Ignition((4.1, 8.1)),))
    other = engine.evaluate(moved, remove("M"))
    with pytest.raises(ValueError, match="matched"):
        compare_retained_ordinary(baseline, other)


def test_one_failed_core_is_joint_failure_even_if_the_other_survives():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 8, 10),
            "G": box(16, 8, 18, 10),
            "F": box(4, 6, 6, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    record = engine.evaluate(scenario, remove("M"))
    assert record.metrics["ignited_core_ids"] == ["F"]
    assert record.metrics["core_joint_failure_observed"]
    assert record.metrics["protection_status"] == "failed"


def test_multiple_fixed_sources_are_verified_and_not_counted_as_core_failures():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "S2": box(4, 12, 6, 14),
            "M": box(6, 8, 8, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        },
        fixed_source_ids=("S", "S2"),
    )
    record = engine.evaluate(scenario, remove("M"), stop_at_first_core_ignition=True)
    assert record.metrics["actual_source_ids"] == ["S", "S2"]
    assert record.diagnostics["source_verified"]
    assert record.metrics["protection_status"] == "passed"


def shared_case(*, heterogeneous=False):
    bundle, transport, inventory, scenario = fixture.make_case(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 7, 10),
            "N": box(7, 8, 10, 10),
            "G": box(12, 8, 14, 10),
            "F": box(12, 6, 14, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    if heterogeneous:
        heat = bundle.request.parameters.specific_heat_J_kg_K.copy()
        heat[bundle.request.building_index == inventory.by_id["N"].object_id] *= 2
        bundle = replace(
            bundle,
            request=replace(
                bundle.request,
                parameters=replace(bundle.request.parameters, specific_heat_J_kg_K=heat),
            ),
        )
    return HeritageEvaluationEngine(bundle, transport, inventory), scenario


def test_removing_shared_cell_owner_preserves_neighbours_remaining_fuel():
    engine, scenario = shared_case()
    request, observer, ledger = engine.prepare(scenario, remove("M"))
    row, col = request.grid.locate(6.5, 9)
    assert request.building_index[row, col] == engine.inventory.by_id["N"].object_id
    assert request.parameters.covered_area_m2[row, col] == pytest.approx(2)
    assert request.parameters.initial_dry_fuel_mass_kg[row, col] == pytest.approx(36)
    assert request.burnable_mask[row, col]
    assert ledger["demolished_footprint_area_m2"] == pytest.approx(2)
    assert ledger["removed_dry_fuel_kg"] == pytest.approx(36)
    assert {o.label for o in observer.objects} == {"S", "N", "G", "F", "U"}


def test_shared_cells_cannot_silently_inherit_another_building_assembly():
    engine, scenario = shared_case(heterogeneous=True)
    with pytest.raises(InterventionConflict, match="different assemblies"):
        engine.prepare(scenario, remove("M"))


def test_shared_core_temperature_cannot_certify_protection():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "G": box(8, 8, 9, 10),
            "M": box(9, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    record = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    assert record.metrics["protection_status"] == "invalid"
    assert not record.metrics["protection_goal_met"]
    assert "core_objects_share_temperature_cells" in record.diagnostics["invalid_reasons"]


def test_overlapping_annotation_is_not_an_independently_removable_building():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 8, 10),
            "N": box(7, 8.5, 7.5, 9.5),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    with pytest.raises(InterventionConflict, match="semantic review"):
        engine.prepare(scenario, remove("M"))


@pytest.mark.parametrize("identifier", ["G", "F", "S", "not-in-inventory"])
def test_core_source_and_undeclared_buildings_cannot_be_demolished(identifier):
    engine, scenario = setup()
    with pytest.raises(InterventionConflict, match="declared ordinary"):
        engine.prepare(scenario, remove(identifier))


def test_source_must_be_declared_inside_outline_and_unambiguous():
    engine, scenario = setup()
    for point, error in (((9, 9), "declared"), ((1, 1), "unambiguous")):
        with pytest.raises(ValueError, match=error):
            engine.prepare(replace(scenario, ignitions=(Ignition(point),)), remove("M"))
    with pytest.raises(ValueError, match="time zero"):
        engine.prepare(replace(scenario, ignitions=(Ignition((5, 9), 1),)), remove("M"))
    half, half_scenario = setup(
        geometries={
            "S": box(4, 8, 5, 10),
            "M": box(6, 8, 8, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    with pytest.raises(ValueError, match="outside the actual"):
        half.prepare(replace(half_scenario, ignitions=(Ignition((5.5, 9)),)), remove("M"))


def test_inventory_conflicts_and_missing_identity_are_explicit():
    engine, _ = setup()
    inventory = engine.inventory
    with pytest.raises(ValueError, match="conflict"):
        replace(inventory, candidate_ids=("G",))
    with pytest.raises(ValueError, match="self-ignition"):
        replace(inventory, fixed_source_ids=("G",), candidate_ids=())
    with pytest.raises(ValueError, match="targets and ordinary source"):
        HeritageEvaluationEngine(engine.bundle, engine.transport, replace(inventory, targets=()))
    with pytest.raises(ValueError, match="numeric building identities"):
        wrong = replace(
            inventory,
            buildings=tuple(replace(b, object_id=b.object_id + 10) for b in inventory.buildings),
        )
        other = HeritageEvaluationEngine(engine.bundle, engine.transport, wrong)
        other.prepare(fixture.make_case()[3], BuildingDemolitionPlan("none"))


def test_audit_reports_pending_data_and_keeps_households_unknown():
    engine, _ = setup()
    inventory = replace(
        engine.inventory,
        targets=(HeritageTarget("F", "fengyuqiao", "annotation"),),
        candidate_ids=(),
        fixed_source_ids=(),
    )
    report = audit_inventory(engine.bundle.request, inventory)
    assert report["candidate_ids"] == ()
    assert not report["real_dual_heritage_ready"]
    assert {
        "field": "heritage_identity",
        "role": "gulou",
        "status": "pending",
        "impact": "blocks_real_dual_heritage_claim",
    } in report["missing_data"]
    assert all(f["properties"]["household_count"] is None for f in report["building_features"])
    synthetic = audit_inventory(engine.bundle.request, engine.inventory)
    assert synthetic["ordinary_annotation_count"] == 3
    assert synthetic["physical_building_annotation_count"] == 4
    assert synthetic["dual_target_geometry_ready"]
    assert not synthetic["real_dual_heritage_ready"]
    with pytest.raises(ValueError, match="explicit eligibility"):
        replace(engine.inventory, candidate_evidence="pending_survey_no_candidates_declared")


def test_parent_mutation_and_unsupported_graph_fail_before_evaluation():
    engine, scenario = setup()
    request = engine.bundle.request
    wrong = replace(
        request,
        exposure_edges=replace(request.exposure_edges, weight=request.exposure_edges.weight * 0.9),
    )
    with pytest.raises(ValueError, match="unsupported parent graph"):
        HeritageEvaluationEngine(
            replace(engine.bundle, request=wrong), engine.transport, engine.inventory
        )
    request.parameters.specific_heat_J_kg_K[4, 2] += 1
    with pytest.raises(ValueError, match="parent case changed"):
        engine.prepare(scenario, remove("M"))


def test_boundary_fire_is_invalid_even_if_core_does_not_ignite():
    engine, scenario = setup(
        geometries={
            "S": box(0, 8, 2, 10),
            "M": box(6, 8, 8, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    record = engine.evaluate(scenario, remove("M"))
    assert not record.metrics["core_joint_failure_observed"]
    assert record.metrics["protection_status"] == "invalid"
    assert "fire_reached_domain_edge" in record.diagnostics["invalid_reasons"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"demolished_ids": ("M", "M"), "ground_evidence": "synthetic"},
        {"demolished_ids": ("M",)},
        {"ground_policy": "infer_from_rgb_roof"},
    ],
)
def test_missing_or_ambiguous_action_semantics_rejected(kwargs):
    with pytest.raises(ValueError):
        BuildingDemolitionPlan("bad", **kwargs)


def test_signature_distinguishes_early_stop_and_changed_target_membership():
    engine, scenario = setup()
    a = engine.evaluate(scenario, remove("M"))
    b = engine.evaluate(scenario, remove("M"), stop_at_first_core_ignition=True)
    inventory = replace(engine.inventory, targets=(engine.inventory.targets[1],))
    c = HeritageEvaluationEngine(engine.bundle, engine.transport, inventory).evaluate(
        scenario, remove("M")
    )
    assert len({a.evaluation_sha256, b.evaluation_sha256, c.evaluation_sha256}) == 3


def test_native_exports_reload_and_cli_audit_does_not_run_ca(tmp_path):
    runner = load_module("heritage_runner", "scripts/run_heritage_study.py")
    config = fixture.write_example(tmp_path / "inputs")
    audit = runner.run(config, tmp_path / "audit", audit_only=True)
    assert audit["evaluations"] == 0
    output = tmp_path / "evaluation"
    result = runner.run(config, output)
    assert result["evaluations"] == 2
    summary = json.loads((output / "summary.json").read_text())
    assert not summary["optimization_performed"]
    assert summary["records"][1]["metrics"]["protection_status"] == "passed"
    path = output / "scenario-000/plan-001"
    bundle = load_v3_case_bundle(path / "case")
    assert (
        request_fingerprint(bundle.request)
        == summary["records"][1]["diagnostics"]["physical_request_sha256"]
    )
    labels = bundle.metadata["building_labels"]
    assert "M" not in labels.values()
    assert (path / "final-state.npz").exists()
    protocol = json.loads((output / "protocol.json").read_text())
    assert protocol["mode"] == "explicit_plans_no_search"


@pytest.mark.parametrize("projected", [False, True])
def test_spatial_exports_identify_local_metres_or_transform_to_wgs84(tmp_path, projected):
    runner = load_module("heritage_export", "scripts/run_heritage_study.py")
    grid = (
        VillageGridSpec(9, 13, 2, 346500, 2870018, "projected", "EPSG:32649")
        if projected
        else VillageGridSpec(9, 13, 2, 0, 18)
    )
    geometry = box(grid.x_min_m, grid.y_max_m - 10, grid.x_min_m + 4, grid.y_max_m - 6)
    path = runner.write_features(
        tmp_path / "map",
        grid,
        [{"type": "Feature", "geometry": mapping(geometry), "properties": {"id": "test"}}],
    )
    value = json.loads(path.read_text())
    if projected:
        assert path.suffix == ".geojson"
        for x, y in value["features"][0]["geometry"]["coordinates"][0]:
            assert 109 < x < 110
            assert 25 < y < 27
        assert value["source_grid"]["crs"] == "EPSG:32649"
    else:
        assert path.name.endswith(".local-metric.json")
        assert value["coordinate_units"] == "m"
        assert value["schema_version"] == "local-metric-features-1"


def role_annotation_records():
    _bundle, _transport, inventory, _scenario = fixture.make_case()
    base = [
        {**b.record(), "properties": {**b.record()["properties"], "source_shape_index": n}}
        for n, b in enumerate(inventory.buildings)
    ]
    updated = [
        {
            **row,
            "properties": {
                **row["properties"],
                "source_label": "gulou"
                if row["building_id"] == "G"
                else row["properties"]["source_label"],
            },
        }
        for row in base
    ]
    return base, updated


def test_new_gulou_label_preserves_physical_ids_and_reports_other_changed_geometry():
    base, updated = role_annotation_records()
    for row in updated:
        if row["building_id"] == "G":
            row["building_id"] = "new_annotation_id_must_not_replace_stable_id"
        elif row["building_id"] == "M":
            row["polygons"] = geometry_to_polygons(translate(box(6, 8, 8, 10), xoff=0.1))
    targets, report = bind_labelme_targets(base, updated, identity_evidence="user_updated_labelme")
    assert {(t.building_id, t.role) for t in targets} == {("G", "gulou"), ("F", "fengyuqiao")}
    assert not report["physical_case_matches_updated_annotations"]
    assert report["geometry_changes"][0]["building_id"] == "M"
    assert report["geometry_changes"][0]["symmetric_difference_m2"] > 0


def test_changed_heritage_outline_cannot_be_bound_to_stale_case_geometry():
    base, updated = role_annotation_records()
    for row in updated:
        if row["building_id"] == "G":
            row["polygons"] = geometry_to_polygons(box(8.1, 8, 10.1, 10))
    with pytest.raises(ValueError, match="heritage outline changed"):
        bind_labelme_targets(base, updated, identity_evidence="changed_outline")
