"""Independent heating, conservative geometry partition and single-plan controls."""

import importlib.util
import json
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.heritage import compare_retained_ordinary
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.interventions.clearing import EXTENSIVE, InterventionConflict
from pyfireca.planning.roof_thermal import (
    _area_quadrature,
    resolve_roof_layout,
    roof_area_integrated_radiation_graph,
    roof_radiation_graph,
)
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation, run_v3_simulation
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "roof_synthetic", ROOT / "examples/research/heritage/synthetic_case.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def setup(*, shared=False, contact=False, **kwargs):
    if shared:
        kwargs["geometries"] = {
            "S": box(6.05, 8.2, 6.45, 8.8),
            "M": box(6.5, 8.2, 6.9, 8.8),
            "G": box(7.0, 8.2, 7.4, 8.8),
            "F": box(7.5, 8.2, 7.9, 8.8),
            "U": box(16, 12, 18, 14),
        }
    bundle, transport, inventory, scenario = fixture.make_case(**kwargs)
    if contact:
        bundle = replace(
            bundle,
            request=replace(
                bundle.request,
                flame_contact=FlameContactParameters(
                    1100,
                    20,
                    0.5,
                    5,
                    "synthetic",
                    "technical geometry/contact control",
                    terrain_reference="shared_face",
                ),
            ),
        )
    return RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="technical_probe"
    ), scenario


def removal(*ids):
    return BuildingDemolitionPlan("remove", tuple(ids), ground_evidence="synthetic_cleared_ground")


def test_same_coarse_cell_has_four_independent_physical_states_and_one_direct_source():
    engine, scenario = setup(shared=True)
    layout, _, ledger = engine.prepare(scenario, BuildingDemolitionPlan("baseline"))
    nodes = [int(layout.nodes(i)[0]) for i in ("S", "M", "G", "F")]
    assert len(set(nodes)) == 4
    assert len(set(layout.cell_id[nodes])) == 1
    assert ledger["actual_source_ids"] == ["S"]
    assert [e.col for e in layout.node_request.scenario.ignitions] == nodes[:1]
    short = replace(
        layout.node_request, scenario=replace(layout.node_request.scenario, end_time_s=60.0)
    )
    result = _run_resolved_v3_simulation(short, contact=None, retain_snapshots=False)
    state = result.final_state
    assert state.ignition_time_s[0, nodes[0]] == 0
    assert not np.isfinite(state.ignition_time_s[0, nodes[1:]]).any()
    temperatures = state.surface_temperature_K[0, nodes[1:]]
    # Evaporation can pin temperatures to 373.15 K; an earlier sample is below that plateau.
    assert np.all(temperatures > 300)
    assert len(set(temperatures)) > 1
    assert (state.absorbed_radiation_J[0, nodes[1:]] > 0).all()


def test_same_cell_cores_heat_and_ignite_at_different_times_without_direct_events():
    engine, scenario = setup(shared=True, ignition_temperature_K=350)
    record, layout, result = engine.simulate(scenario, BuildingDemolitionPlan("baseline"))
    outcomes = {o["building_id"]: o for o in record.metrics["core_outcomes"]}
    assert record.metrics["ignited_core_ids"] == ["F", "G"]
    assert 0 < outcomes["G"]["first_ignition_s"] < outcomes["F"]["first_ignition_s"]
    assert all(o["absorbed_radiation_J"] > 0 for o in outcomes.values())
    assert all(o["shared_temperature_components"] == 0 for o in outcomes.values())
    assert len(layout.node_request.scenario.ignitions) == 1
    assert (
        result.ignition_cause[0, layout.nodes("G")[0]]
        != result.ignition_cause[0, layout.nodes("S")[0]]
    )


def test_component_partition_conserves_every_extensive_parent_field():
    engine, _ = setup(shared=True)
    layout, parent = engine.layout, engine.bundle.request
    for name in EXTENSIVE:
        restored = np.bincount(
            layout.cell_id,
            weights=getattr(layout.thermal.parameters, name).ravel(),
            minlength=parent.grid.height * parent.grid.width,
        )
        np.testing.assert_allclose(
            restored, getattr(parent.parameters, name).ravel(), rtol=1e-10, atol=1e-6
        )
    assert sum(layout.geometry[i].area for i in layout.nodes("G")) == pytest.approx(0.24)


def test_one_full_cell_component_reproduces_old_thermal_model_and_windy_graph():
    engine, scenario = setup()
    transport = replace(
        engine.transport, wind_speed_m_s=2, wind_from_deg=45, flame_tilt_coefficient_s_m=0.4
    )
    parent = engine.bundle.request
    graph = build_v3_radiative_transport_edges(
        parent.grid,
        parent.source_mask,
        parent.target_mask,
        parent.parameters.receiver_area_m2,
        parent.parameters.source_height_m,
        parent.parameters.receiver_height_m,
        np.ones(parent.grid.shape),
        transport,
    )
    rebuilt = roof_radiation_graph(engine.layout, transport)
    np.testing.assert_array_equal(rebuilt.source, graph.source)
    np.testing.assert_array_equal(rebuilt.target, graph.target)
    np.testing.assert_allclose(rebuilt.weight, graph.weight, rtol=1e-14)
    _, layout, result = engine.simulate(scenario, BuildingDemolitionPlan("baseline"))
    native = run_v3_simulation(parent, retain_snapshots=False)
    for key in native.final_state.__dataclass_fields__:
        a, b = getattr(native.final_state, key), getattr(result.final_state, key)
        if isinstance(a, np.ndarray):
            np.testing.assert_allclose(a.ravel(), b.ravel(), rtol=1e-10, atol=1e-8)
        else:
            assert a == pytest.approx(b)
    assert layout.node_request.flame_contact is None


def test_roof_contact_allows_continuous_roof_but_never_jumps_positive_gap():
    engine, scenario = setup(shared=True, contact=True)
    _layout, contact, _ = engine.prepare(scenario, BuildingDemolitionPlan("baseline"))
    assert (contact.neighbors >= 0).sum() == 0  # all five polygons are disjoint
    engine2, scenario2 = setup(contact=True)
    layout2, operator, _ = engine2.prepare(scenario2, BuildingDemolitionPlan("baseline"))
    assert int(layout2.nodes("G")[0]) in operator.neighbors[layout2.nodes("M")[0]]


def test_demolition_removes_only_its_patches_and_retained_states_still_receive_radiation():
    engine, scenario = setup(shared=True, ignition_temperature_K=350)
    before = request_fingerprint(engine.bundle.request)
    base, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("baseline"))
    changed, _, ledger = engine.prepare(scenario, removal("M"))
    removed = changed.nodes("M")
    keep = np.ones(changed.node_request.grid.width, bool)
    keep[removed] = False
    for name in changed.node_request.parameters.__dataclass_fields__:
        a, b = (
            getattr(base.node_request.parameters, name),
            getattr(changed.node_request.parameters, name),
        )
        np.testing.assert_array_equal(a[0, keep], b[0, keep])
        assert not b[0, removed].any()
    assert ledger["demolished_footprint_area_m2"] == pytest.approx(0.24)
    assert ledger["removed_dry_fuel_kg"] == pytest.approx(4.32, rel=1e-6)
    assert not changed.node_request.burnable_mask[0, removed].any()
    edges = changed.node_request.exposure_edges
    assert not np.isin(edges.source, removed).any()
    assert not np.isin(edges.target, removed).any()
    assert np.any(
        np.isin(edges.source, changed.nodes("S")) & np.isin(edges.target, changed.nodes("G"))
    )
    baseline = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    treated = engine.evaluate(scenario, removal("M"))
    assert treated.metrics["protection_status"] == "passed"
    assert all(o["absorbed_radiation_J"] > 0 for o in treated.metrics["core_outcomes"])
    assert compare_retained_ordinary(baseline, treated)["cohort_ids"] == ["S", "U"]
    assert request_fingerprint(engine.bundle.request) == before


def test_single_demolition_protection_passes_on_declared_synthetic_scene():
    engine, scenario = setup()
    baseline = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    treated = engine.evaluate(scenario, removal("M"))
    assert baseline.metrics["ignited_core_ids"] == ["F", "G"]
    assert treated.metrics["protection_status"] == "passed"
    assert treated.diagnostics["reached_requested_horizon"]
    assert treated.diagnostics["source_verified"]
    assert (
        max(treated.diagnostics["energy_closure_errors_J"].values())
        <= treated.diagnostics["energy_tolerance_J"]
    )


def test_overlap_fuel_counted_once_and_overlap_demolition_rejected():
    engine, _ = setup()
    inventory = replace(
        engine.inventory,
        buildings=tuple(
            replace(b, geometry=box(4.5, 8, 7, 10)) if b.building_id == "M" else b
            for b in engine.inventory.buildings
        ),
    )
    # Recreate matching raster through the public synthetic fixture.
    geometries = {b.building_id: b.geometry for b in inventory.buildings}
    engine, scenario = setup(geometries=geometries)
    assert set(engine.layout.ambiguous_ids) == {"S", "M"}
    assert any(set(ids) == {"S", "M"} for ids in engine.layout.owners)
    with pytest.raises(InterventionConflict, match="overlapping"):
        engine.prepare(replace(scenario, ignitions=(Ignition((4.25, 9)),)), removal("M"))
    with pytest.raises(ValueError, match="unambiguous"):
        engine.prepare(
            replace(scenario, ignitions=(Ignition((5, 9)),)), BuildingDemolitionPlan("base")
        )


def test_opt_in_demolition_retains_shared_roof_fuel_and_removes_deleted_owner():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(4.5, 8, 7, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    scenario = replace(scenario, ignitions=(Ignition((4.25, 9)),), horizon_s=20)
    before, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("base"))
    plan = BuildingDemolitionPlan(
        "retain-shared", ("M",), ground_evidence="synthetic", overlap_policy="retain_shared_roof"
    )
    after, _, ledger = engine.prepare(scenario, plan)
    shared = [i for i, ids in enumerate(before.owners) if set(ids) == {"S", "M"}]
    assert shared and all(after.owners[i] == ("S",) for i in shared)
    for name in after.thermal.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(after.thermal.parameters, name)[0, shared],
            getattr(before.thermal.parameters, name)[0, shared],
        )
    assert ledger["retained_shared_roof_area_m2"] == pytest.approx(3)
    assert ledger["cleared_roof_union_area_m2"] == pytest.approx(2)
    assert ledger["demolished_footprint_area_m2"] == pytest.approx(5)
    assert not after.thermal.burnable_mask[0, after.nodes("M")].any()
    result = engine.evaluate(scenario, plan)
    assert "M" not in result.metrics["building_first_ignitions"]
    assert result.diagnostics["source_verified"]


def test_removing_all_shared_owners_clears_union_once():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 7.5, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
            "U": box(6.5, 8, 7, 10),
        }
    )
    plan = BuildingDemolitionPlan(
        "both", ("M", "U"), ground_evidence="synthetic", overlap_policy="retain_shared_roof"
    )
    layout, _, ledger = engine.prepare(scenario, plan)
    assert ledger["demolished_footprint_area_m2"] == pytest.approx(4)
    assert ledger["cleared_roof_union_area_m2"] == pytest.approx(3)
    assert ledger["retained_shared_roof_area_m2"] == 0
    assert ledger["removed_dry_fuel_kg"] == pytest.approx(54)
    nodes = np.unique(np.concatenate([layout.nodes("M"), layout.nodes("U")]))
    assert not layout.thermal.burnable_mask[0, nodes].any()


@pytest.mark.parametrize("identifier", ["G", "F", "S"])
def test_core_and_source_deletion_rejected(identifier):
    engine, scenario = setup()
    with pytest.raises(InterventionConflict):
        engine.prepare(scenario, removal(identifier))


def test_pending_eligibility_and_fixed_sources_are_not_inferred_from_technical_probe():
    original, scenario = setup(shared=True, candidate_ids=())
    engine = RoofHeritageEvaluationEngine(
        original.bundle,
        original.transport,
        replace(original.inventory, fixed_source_ids=()),
        source_policy="technical_probe",
    )
    scenario = replace(scenario, ignitions=(Ignition((6.25, 8.5)),))
    record = engine.evaluate(scenario, BuildingDemolitionPlan("base"))
    assert record.intervention["study_source_selection_pending"]
    assert not engine.inventory.fixed_source_ids
    assert not engine.inventory.candidate_ids
    with pytest.raises(InterventionConflict, match="candidate"):
        engine.prepare(scenario, removal("M"))
    with pytest.raises(ValueError, match="pending"):
        RoofHeritageEvaluationEngine(
            engine.bundle,
            engine.transport,
            engine.inventory,
            source_policy="declared_fixed_sources",
        )


def test_core_self_ignition_or_patch_boundary_source_rejected():
    engine, scenario = setup(shared=True)
    for point in ((7.2, 8.5), (7.7, 8.5), (6.05, 8.5)):
        with pytest.raises(ValueError):
            engine.prepare(
                replace(scenario, ignitions=(Ignition(point),)), BuildingDemolitionPlan("b")
            )


def test_graph_limits_and_outgoing_budgets_are_enforced():
    engine, _ = setup(shared=True)
    with pytest.raises(ValueError, match="edge limit"):
        roof_radiation_graph(engine.layout, engine.transport, max_edges=1)
    with pytest.raises(ValueError, match="node limit"):
        resolve_roof_layout(engine.bundle.request, engine.inventory, max_nodes=2)


def test_area_integrated_graph_uses_roof_geometry_instead_of_selected_representative_points():
    engine, _ = setup(shared=True)
    builder = partial(
        roof_area_integrated_radiation_graph,
        integrated_ids=("S", "G", "F"),
        quadrature_spacing_m=0.1,
    )
    expected = builder(engine.layout, engine.transport)
    selected = np.asarray([bool({"S", "G", "F"}.intersection(ids)) for ids in engine.layout.owners])
    shifted = engine.layout.xy_m.copy()
    shifted[selected] += np.asarray((1000.0, -1000.0))
    moved = builder(replace(engine.layout, xy_m=shifted), engine.transport)
    np.testing.assert_array_equal(moved.source, expected.source)
    np.testing.assert_array_equal(moved.target, expected.target)
    np.testing.assert_allclose(moved.weight, expected.weight, rtol=1e-14, atol=1e-15)
    point = roof_radiation_graph(replace(engine.layout, xy_m=shifted), engine.transport)
    assert len(point.source) < len(expected.source)


def test_area_integrated_graph_preserves_unselected_point_edges_and_is_engine_opt_in():
    base, scenario = setup(shared=True)
    builder = partial(
        roof_area_integrated_radiation_graph,
        integrated_ids=("S", "G", "F"),
        quadrature_spacing_m=0.1,
    )
    point = roof_radiation_graph(base.layout, base.transport)
    area = builder(base.layout, base.transport)
    integrated = np.asarray([bool({"S", "G", "F"}.intersection(ids)) for ids in base.layout.owners])

    def unrelated(graph):
        keep = ~(integrated[graph.source] | integrated[graph.target])
        return graph.source[keep], graph.target[keep], graph.weight[keep]

    for actual, expected in zip(unrelated(area), unrelated(point), strict=True):
        np.testing.assert_array_equal(actual, expected)
    engine = RoofHeritageEvaluationEngine(
        base.bundle,
        base.transport,
        base.inventory,
        source_policy="technical_probe",
        radiation_builder=builder,
    )
    record = engine.evaluate(scenario, BuildingDemolitionPlan("baseline"))
    assert record.diagnostics["radiation_method"] == "roof_area_integrated_radiation_graph"
    assert record.intervention["radiation_method"] == "roof_area_integrated_radiation_graph"


def test_area_quadrature_retains_negligible_projection_slivers():
    xy, fraction = _area_quadrature(box(0, 0, 1e-6, 1e-6), 0.25, x0=0, y0=1)
    assert xy.shape == (1, 2)
    np.testing.assert_array_equal(fraction, np.ones(1))


def test_cross_gap_radiation_can_still_ignite_both_cores():
    engine, scenario = setup(radius=8, ignition_temperature_K=350)
    record = engine.evaluate(scenario, removal("M"))
    assert record.metrics["ignited_core_ids"] == ["F", "G"]
    assert record.metrics["protection_status"] == "failed"


def test_no_transport_control_keeps_other_same_cell_objects_ambient_and_unignited():
    engine, scenario = setup(shared=True)
    layout, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("base"))
    # Disable both heat channels explicitly for this negative control.
    request = replace(layout.node_request, exposure_edges=engine.layout.node_inputs.exposure_edges)
    result = _run_resolved_v3_simulation(request, contact=None, retain_snapshots=False)
    for identifier in ("M", "G", "F"):
        nodes = layout.nodes(identifier)
        assert np.all(result.final_state.surface_temperature_K[0, nodes] == 300)
        assert not np.isfinite(result.final_state.ignition_time_s[0, nodes]).any()


def test_core_with_multiple_roof_patches_keeps_independent_states_and_any_patch_fails():
    engine, scenario = setup(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 8, 10),
            "G": box(8, 8, 12, 10),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    nodes = engine.layout.nodes("G")
    assert len(nodes) == 2
    record, _, result = engine.simulate(
        scenario, BuildingDemolitionPlan("b"), stop_at_first_core_ignition=True
    )
    assert record.metrics["core_joint_failure_observed"]
    assert record.metrics["protection_status"] == "failed"
    assert np.isfinite(result.final_state.ignition_time_s[0, nodes]).sum() == 1
    assert not record.diagnostics["reached_requested_horizon"]


def test_saved_arrays_restore_all_states_with_physical_contact_graph(tmp_path):
    script_spec = importlib.util.spec_from_file_location(
        "roof_runner", ROOT / "scripts/run_heritage_roof_study.py"
    )
    runner = importlib.util.module_from_spec(script_spec)
    script_spec.loader.exec_module(runner)
    engine, scenario = setup(contact=True)
    plan = BuildingDemolitionPlan("baseline")
    record, layout, result = engine.simulate(scenario, plan)
    _, contact, _ = engine.prepare(scenario, plan)
    output = tmp_path / "saved"
    runner.save_evaluation(output, record, layout, result, contact)
    assert runner.restore_simulation(output) == {
        "all_final_state_fields_equal": True,
        "saved_contact_used": True,
    }


def test_core_true_overlap_and_stale_geometry_are_rejected():
    with pytest.raises(ValueError, match="core overlaps"):
        setup(
            geometries={
                "S": box(4, 8, 6, 10),
                "M": box(6, 8, 9, 10),
                "G": box(8, 8, 10, 10),
                "F": box(8, 6, 10, 8),
                "U": box(16, 12, 18, 14),
            }
        )
    engine, _ = setup()
    inventory = replace(
        engine.inventory,
        buildings=tuple(
            replace(b, geometry=box(8, 8, 11, 10)) if b.building_id == "G" else b
            for b in engine.inventory.buildings
        ),
    )
    with pytest.raises(ValueError, match="stale"):
        resolve_roof_layout(engine.bundle.request, inventory)


def test_json_study_runner_evaluates_paired_single_plan_and_restores(tmp_path):
    modules = []
    for name, path in (
        ("roof_controls", "examples/research/heritage/roof_controls.py"),
        ("roof_runner", "scripts/run_heritage_roof_study.py"),
    ):
        module_spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        modules.append(module)
    controls, runner = modules
    inputs = controls.create_controls(tmp_path / "inputs")[0]
    output = tmp_path / "output"
    summary = runner.run(inputs["study"], inputs["experiments"], output)
    assert [row["protection_status"] for row in summary["runs"]] == ["failed", "passed"]
    assert [row["demolished_count"] for row in summary["runs"]] == [0, 1]
    assert summary["parent_case_unchanged"]
    restored = runner.restore_simulation(output / "shared-cell/remove-M")
    assert restored["all_final_state_fields_equal"]
    trace = json.loads((output / "shared-cell/baseline/selected-trace.json").read_text())
    assert len(trace["frames"]) > 2
    assert (output / "shared-cell/remove-M/matched-ordinary-comparison.json").is_file()


def test_provisional_candidate_policy_selects_every_noncore_ordinary_building():
    script_spec = importlib.util.spec_from_file_location(
        "candidate_runner", ROOT / "scripts/run_heritage_roof_study.py"
    )
    runner = importlib.util.module_from_spec(script_spec)
    script_spec.loader.exec_module(runner)
    records = [
        {"building_id": "A", "properties": {"source_label": "building"}},
        {"building_id": "G", "properties": {"source_label": "building"}},
        {"building_id": "F", "properties": {"source_label": "fengyuqiao"}},
        {"building_id": "S", "properties": {"source_label": "building"}},
    ]
    config = {
        "candidate_policy": "all_ordinary_except_protected_targets_and_sources",
        "candidate_ids": [],
        "protected_ids": ["G", "F"],
        "targets": [{"building_id": "G"}, {"building_id": "F"}],
        "fixed_source_ids": ["S"],
    }
    assert runner.resolve_candidate_ids(config, records) == ("A",)
