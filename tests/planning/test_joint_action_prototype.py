"""Independent roof+ground cuts must preserve every pruned native CA trajectory."""

import importlib.util
from copy import deepcopy
from dataclasses import replace
from functools import partial
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.area_candidates import ClearedAreaFunction
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import BuildingObject, content_hash
from pyfireca.planning.joint_action_prototype import (
    CUT_SCHEMA,
    JointActionUniverse,
    build_joint_action_universe,
    joint_cold_removal_certificate,
    joint_cut_forbids,
    joint_source_area_coverage,
    solve_joint_action_master,
    validate_joint_cut,
)
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import (
    resolve_roof_layout,
    roof_area_integrated_radiation_graph,
)
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.flame_contact_v3 import FlameContactParameters

ROOT = Path(__file__).resolve().parents[2]


def fixture(
    scale=1.0,
    *,
    cold_cp=None,
    aliases=False,
    warm_grass=False,
    removable_source=False,
    contact=False,
):
    path = Path(__file__).with_name("test_roof_joint.py")
    spec = importlib.util.spec_from_file_location("joint_action_native_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle, transport, inventory, _, grass = module.joint_case.__wrapped__()
    parent, far = bundle.request, bundle.request.grid.width - 1
    fields = {
        name: getattr(parent.parameters, name).copy()
        for name in parent.parameters.__dataclass_fields__
    }
    for value in fields.values():
        value.ravel()[far] = value.ravel()[grass[0]]
    fields["ignition_temperature_K"][:] = 350.0
    if warm_grass:
        fields["ignition_temperature_K"].ravel()[grass[0]] = 2000.0
    if cold_cp is not None:
        fields["specific_heat_J_kg_K"].ravel()[far] = cold_cp
    masks = {
        name: getattr(parent, name).copy()
        for name in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask")
    }
    for value in masks.values():
        value.ravel()[far] = True
    parent = replace(
        parent,
        parameters=replace(parent.parameters, **fields),
        scenario=replace(parent.scenario, max_dt_s=1.0),
        **masks,
    )
    if contact:
        parent = replace(
            parent,
            flame_contact=FlameContactParameters(
                1100, 25, 0.1, 5, "synthetic", "joint_action_native_contact_test"
            ),
        )
    bundle = replace(bundle, request=parent)
    inventory = replace(inventory, candidate_ids=("M",))
    if removable_source:
        inventory = replace(inventory, fixed_source_ids=(), candidate_ids=("S", "M", "U"))
    if aliases:
        inventory = replace(
            inventory,
            buildings=(
                *inventory.buildings,
                BuildingObject("V", 5, "building", inventory.by_id["M"].geometry),
            ),
            candidate_ids=("M", "V"),
        )
    base = resolve_roof_layout(parent, inventory)
    footprint = RoofIgnitionFootprint("S", inventory.by_id["S"].geometry, "synthetic_exact_25m2")
    layout = refine_roof_layout(
        base,
        inventory,
        cell_size_m=scale,
        refined_ids=tuple(inventory.by_id),
        footprints=(footprint,),
    )
    membership = np.asarray([bool(g) for g in layout.owners])
    membership.setflags(write=False)
    engine = RoofJointEvaluationEngine(
        bundle,
        transport,
        inventory,
        source_policy="sampled_retained_sources" if removable_source else "declared_fixed_sources",
        prepared_layout=layout,
        contact_builder=refined_contact_operator,
        radiation_builder=partial(
            roof_area_integrated_radiation_graph,
            integrated_ids=tuple(inventory.by_id),
            quadrature_spacing_m=0.5,
            frozen_integrated_node_mask=membership,
        ),
        max_radiation_edges=500000,
    )
    universe = build_joint_action_universe(
        layout, inventory, grass_cell_ids=(grass[0], far), construction_layout=base
    )
    scenario = EvaluationScenario(f"one-h{scale:g}", footprint_ignitions(layout), 1500)
    return engine, universe, scenario, grass[0], far


def powerset(items):
    items = sorted(items)
    return [ids for n in range(len(items) + 1) for ids in combinations(items, n)]


def run(engine, universe, scenario, actions):
    record, layout, result = engine.simulate(
        scenario, universe.plan(actions), stop_at_first_core_ignition=True
    )
    assert not record.diagnostics["invalid_reasons"]
    cert = joint_cold_removal_certificate(
        engine,
        universe,
        actions,
        record,
        layout,
        result,
        problem_sha256="joint-test",
        source_id="S",
    )
    return record, layout, result, cert


@pytest.mark.parametrize("scale,contact", [(1.0, False), (0.5, False), (1.0, True)])
def test_complete_small_powerset_every_pruned_joint_plan_preserves_native_failure(scale, contact):
    engine, universe, scenario, near, far = fixture(scale, contact=contact)
    baseline_record, baseline_layout, baseline, cert = run(engine, universe, scenario, ())
    assert baseline_record.metrics["protection_status"] == "failed"
    assert cert is not None and cert["schema_version"] == CUT_SCHEMA
    validate_joint_cut(
        cert, universe, problem_sha256="joint-test", scenario_id=scenario.scenario_id, actions=()
    )
    assert [f"G{near}"] in cert["required_clearance_action_groups"]
    assert [f"G{far}"] not in cert["required_clearance_action_groups"]
    assert joint_cut_forbids(cert, (f"G{far}",))
    if contact:
        assert engine.last_contact is not None
        assert baseline.final_state.received_convection_J.sum() > 0
    evaluated = 0
    for actions in powerset(universe.candidate_ids):
        if not actions:
            record, _layout, result = baseline_record, baseline_layout, baseline
        else:
            record, _layout, result, _ = run(engine, universe, scenario, actions)
        evaluated += 1
        if not joint_cut_forbids(cert, actions):
            continue
        assert record.metrics["protection_status"] == "failed"
        assert record.metrics["core_outcomes"] == baseline_record.metrics["core_outcomes"]
        assert result.final_state.time_s == baseline.final_state.time_s
        retained = np.asarray([not (g and set(g) <= set(actions)) for g in universe.node_groups])
        for name in baseline.final_state.__dataclass_fields__:
            actual, expected = (
                getattr(result.final_state, name),
                getattr(baseline.final_state, name),
            )
            if isinstance(actual, np.ndarray):
                np.testing.assert_array_equal(actual.ravel()[retained], expected.ravel()[retained])
    assert evaluated == 2 ** len(universe.candidate_ids)


def test_action_universe_has_same_cost_across_supports_and_grass_charges_whole25m2():
    _, a, _, near, far = fixture(1)
    _, b, _, _, _ = fixture(0.5)
    assert a.sha256 == b.sha256
    assert a.physical_partition_sha256 != b.physical_partition_sha256
    assert a.area((f"G{near}", f"G{far}")) == 50
    assert a.area(("M",)) == pytest.approx(25)
    assert a.grass_scope == "explicit_subset" and a.eligible_grass_cell_count == 3


def test_shared_roof_label_is_not_joint_physical_escape_and_prior_grass_action_is_bound():
    engine, universe, scenario, _near, far = fixture(aliases=True)
    record, _layout, result, cert = run(engine, universe, scenario, (f"G{far}",))
    assert ["M", "V"] in cert["required_clearance_action_groups"]
    assert joint_cut_forbids(cert, (f"G{far}", "M"))
    assert not joint_cut_forbids(cert, ("M",))  # A is not a subset.
    assert not joint_cut_forbids(cert, (f"G{far}", "M", "V"))
    other, _, native, _ = run(engine, universe, scenario, (f"G{far}", "M"))
    assert other.metrics["core_outcomes"] == record.metrics["core_outcomes"]
    assert native.final_state.time_s == result.final_state.time_s


def test_roof_projection_source_cover_and_joint_total_cap_guard_are_distinct():
    engine, universe, _, near, _ = fixture()
    draw = {"priority_ids": ["S"], "baseline_source_id": "S"}
    proof = joint_source_area_coverage(universe, draw, engine.inventory, 25)
    assert proof.validate_actions((f"G{near}",))["joint_construction_area_m2"] == 25
    with pytest.raises(ValueError, match="same total-area"):
        proof.validate_actions(("M", f"G{near}"))


@pytest.mark.parametrize("mutation", ["old_schema", "seal", "partition", "source"])
def test_joint_certificate_rejects_old_roof_capabilities_and_identity_mutations(mutation):
    engine, universe, scenario, _, _ = fixture()
    _, _, _, cert = run(engine, universe, scenario, ())
    cert = deepcopy(cert)
    if mutation == "old_schema":
        cert["schema_version"] = "heritage-physical-node-removal-cut-1"
    elif mutation == "seal":
        cert["capabilities"]["action_types"] = ["whole_building_only"]
    elif mutation == "partition":
        cert["physical_partition_sha256"] = "other"
    else:
        cert["source_id"] = "unknown"
    cert["certificate_sha256"] = content_hash(
        {k: v for k, v in cert.items() if k != "certificate_sha256"}
    )
    with pytest.raises(ValueError):
        validate_joint_cut(
            cert,
            universe,
            problem_sha256="joint-test",
            scenario_id=scenario.scenario_id,
            actions=(),
        )


def test_joint_master_matches_exhaustive_logical_feasible_set_and_keeps_unknown_unknown():
    pytest.importorskip("scipy.optimize")
    engine, universe, scenario, _, far = fixture()
    _, _, _, cert = run(engine, universe, scenario, ())
    check = ProtectionCheck("failed", {"failure_certificate": cert, "source_id": "S"})
    cache = {("joint-test", (), scenario.scenario_id): check}
    answer = solve_joint_action_master(
        universe, (scenario.scenario_id,), problem_sha256="joint-test", cache=cache
    )
    expected = min(
        universe.area(ids)
        for ids in powerset(universe.candidate_ids)
        if not joint_cut_forbids(cert, ids)
    )
    assert answer["next_area_m2"] == pytest.approx(expected)
    assert answer["remaining_master_lower_bound_m2"] == pytest.approx(expected)
    assert answer["next_physical_protection_status"] == "unknown"
    cache["joint-test", (f"G{far}",), scenario.scenario_id] = ProtectionCheck("unknown", {})
    assert solve_joint_action_master(
        universe, (scenario.scenario_id,), problem_sha256="joint-test", cache=cache
    )["next_area_m2"] == pytest.approx(expected)


def test_sparse_exact_nogood_uses_single_shared_count_row():
    pytest.importorskip("scipy.optimize")
    _, universe, scenario, _, _ = fixture()
    excluded = powerset(universe.candidate_ids)[:4]
    cache = {
        ("joint-test", ids, scenario.scenario_id): ProtectionCheck("failed", {}) for ids in excluded
    }
    answer = solve_joint_action_master(
        universe, (scenario.scenario_id,), problem_sha256="joint-test", cache=cache
    )
    expected = min(
        universe.area(ids) for ids in powerset(universe.candidate_ids) if ids not in excluded
    )
    assert answer["next_area_m2"] == pytest.approx(expected)
    assert answer["matrix_nonzero_count"] == len(universe.candidate_ids) + 1 + sum(
        len(ids) + 1 for ids in excluded
    )


def test_warm_unlit_grass_receiver_is_an_escape_even_without_grass_ignition():
    engine, universe, scenario, near, _ = fixture(warm_grass=True)
    _, layout, result, cert = run(engine, universe, scenario, ())
    node = np.flatnonzero(layout.cell_id == near)[0]
    assert not np.isfinite(result.final_state.ignition_time_s.ravel()[node])
    assert result.final_state.surface_temperature_K.ravel()[node] > 300
    assert cert["warm_unignited_receiver_node_count"] > 0
    assert [f"G{near}"] in cert["required_clearance_action_groups"]
    assert not joint_cut_forbids(cert, (f"G{near}",))


def test_cold_global_clock_limiter_cannot_be_silently_removed():
    engine, universe, scenario, _, far = fixture(cold_cp=25)
    _, _, _, cert = run(engine, universe, scenario, ())
    assert cert["cold_node_thermal_limit_s"] < 1
    assert cert["cold_global_timestep_nodes_guarded"]
    assert [f"G{far}"] in cert["required_clearance_action_groups"]
    assert not joint_cut_forbids(cert, (f"G{far}",))


def test_actual_removable_source_has_a_singleton_guard_and_total_cap_source_prefix():
    engine, universe, scenario, _, _ = fixture(removable_source=True)
    _, _, _, cert = run(engine, universe, scenario, ())
    assert ["S"] in cert["required_clearance_action_groups"]
    assert not joint_cut_forbids(cert, ("S",))
    draw = {"priority_ids": ["S", "M", "U"], "baseline_source_id": "S"}
    proof = joint_source_area_coverage(universe, draw, engine.inventory, 25)
    assert proof.roof_coverage.source_ids == ("S", "M")
    assert proof.validate_actions(("S",))["source_id"] == "M"
    backup = replace(
        scenario,
        ignitions=tuple(
            Ignition(tuple(engine.layout.xy_m[node]), 0) for node in engine.layout.nodes("M")
        ),
    )
    record, layout, native = engine.simulate(
        backup, universe.plan(("S",)), stop_at_first_core_ignition=True
    )
    assert record.metrics["actual_source_ids"] == ["M"]
    assert not record.diagnostics["invalid_reasons"]
    changed = joint_cold_removal_certificate(
        engine, universe, ("S",), record, layout, native, problem_sha256="joint-test", source_id="M"
    )
    assert ["M"] in changed["required_clearance_action_groups"]
    full = joint_source_area_coverage(universe, draw, engine.inventory, 100)
    assert full.validate_actions(("S", "M", "U"))["status"] == "no_retained_source_unknown"


def test_two_scenarios_accept_same_action_cost_universe_but_bind_each_physical_partition():
    pytest.importorskip("scipy.optimize")
    contexts = [fixture(h) for h in (1.0, 0.5)]
    cache, parts = {}, {}
    for engine, universe, scenario, _, _ in contexts:
        _, _, _, cert = run(engine, universe, scenario, ())
        parts[scenario.scenario_id] = universe.physical_partition_sha256
        cache["joint-test", (), scenario.scenario_id] = ProtectionCheck(
            "failed", {"failure_certificate": cert}
        )
    universe = contexts[0][1]
    answer = solve_joint_action_master(
        universe,
        tuple(parts),
        problem_sha256="joint-test",
        cache=cache,
        scenario_partition_sha256s=parts,
    )
    assert answer["failure_cut_count"] == 2
    with pytest.raises(ValueError, match="independent physical capability"):
        solve_joint_action_master(universe, tuple(parts), problem_sha256="joint-test", cache=cache)


def test_resource_cap_is_unknown_computation_not_failed_physics():
    pytest.importorskip("scipy.optimize")
    _, universe, scenario, _, _ = fixture()
    cache = {("joint-test", (), scenario.scenario_id): ProtectionCheck("failed", {})}
    with pytest.raises(MemoryError, match="physical status remains unknown"):
        solve_joint_action_master(
            universe,
            (scenario.scenario_id,),
            problem_sha256="joint-test",
            cache=cache,
            max_matrix_nonzeros=1,
        )


def test_formulation_only_sparse_growth_is_linear_in_universe_not_plans_times_universe():
    pytest.importorskip("scipy.optimize")
    cells = tuple(range(1024))
    area = ClearedAreaFunction(
        tuple((frozenset((f"G{i}",)), 25.0) for i in cells),
        "synthetic_disjoint_25m2_formulation_only",
        len(cells),
    )
    universe = JointActionUniverse(
        (),
        cells,
        (),
        (),
        (),
        (),
        "synthetic-no-physical-model",
        "synthetic",
        ClearedAreaFunction((), "no_roof", 0),
        area,
        "explicit_subset",
        len(cells),
    )
    excluded = [(), *((f"G{i}",) for i in range(63))]
    cache = {("logical-mip", ids, "s"): ProtectionCheck("failed", {}) for ids in excluded}
    answer = solve_joint_action_master(
        universe, ("s",), problem_sha256="logical-mip", cache=cache, area_cap_m2=100
    )
    assert answer["next_area_m2"] == 25
    # 1 count equality + 64 sparse no-goods + 1 cost-cap row.
    assert answer["matrix_nonzero_count"] == 1025 + 1 + 63 * 2 + 1024
    assert answer["matrix_nonzero_count"] < len(cells) * len(excluded) / 20


def test_refined_contact_empty_graph_keeps_fixed_parent_h_presence():
    engine, universe, scenario, _, _ = fixture(contact=True)
    layout, _, _ = engine.prepare(scenario, universe.plan(()))
    mask = np.zeros(layout.node_request.grid.shape, bool)
    event = layout.node_request.scenario.ignitions[0]
    mask[event.row, event.col] = True
    request = replace(
        layout.node_request,
        scenario=replace(layout.node_request.scenario, ignitions=(event,)),
        exposure_edges=ExposureEdges(
            np.array([], np.int32), np.array([], np.int32), np.array([], float), len(layout.owners)
        ),
        burnable_mask=mask,
        source_mask=mask,
        target_mask=mask,
        piloted_ignition_mask=mask,
    )
    empty = refined_contact_operator(replace(layout, node_request=request))
    assert empty is not None
    assert empty.neighbors.shape == (len(layout.owners), 0)
    assert empty.parameters.heat_transfer_coefficient_W_m2_K == 25
