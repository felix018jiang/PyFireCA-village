"""Joint whole-cell vegetation and roof interventions preserve physical inputs."""

import importlib.util
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import scale

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.fire_loss import fire_loss_from_result
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.interventions.clearing import EXTENSIVE, InterventionConflict
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.physics_v3 import V3CellPhysicsArrays, resolve_vegetation_physics
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.vegetation_config import VegetationCase
from pyfireca.village.vegetation_materials import load_vegetation_profiles

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def joint_case():
    """Extend the real synthetic roof fixture onto a 5 m grid with two grass cells."""
    path = ROOT / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("joint_roof_synthetic", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    old_bundle, _, old_inventory, old_scenario = fixture.make_case()
    old = old_bundle.request
    grid = VillageGridSpec(old.grid.height, old.grid.width, 5, 0, old.grid.y_max_m * 2.5)
    inventory = replace(
        old_inventory,
        buildings=tuple(
            replace(b, geometry=scale(b.geometry, xfact=2.5, yfact=2.5, origin=(0, 0)))
            for b in old_inventory.buildings
        ),
    )
    profiles = ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml"
    library = load_vegetation_profiles(profiles)
    roofs = old.building_index >= 0
    grass = np.zeros(grid.shape, bool)
    grass[3, 2] = grass[3, 3] = True
    covers = np.where(roofs, 5, np.where(grass, 1, 0)).astype(np.int16)
    profile_ids = np.where(
        roofs,
        library.by_landcover["building"].id,
        np.where(
            grass,
            library.by_landcover["grass"].id,
            library.by_landcover["bare_or_road"].id,
        ),
    )
    coverage = np.ones(grid.shape)
    coverage[3, 3] = 0.4
    mask = np.ones(grid.shape, bool)
    case = VegetationCase(
        path=profiles,
        case_id="synthetic_joint_5m",
        evidence="synthetic",
        prepared_dir=profiles.parent,
        grid=grid,
        profiles=library,
        profile_id_by_cell=profile_ids,
        landcover_index=covers,
        domain_mask=mask,
        known_mask=mask,
        evidence_index=np.zeros(grid.shape, np.int16),
        burnable_mask=roofs | grass,
        coverage_fraction=coverage,
        weather={},
        scenario={},
        transport={},
        scope={},
        resources={},
        ignitions=(),
        spatial_budgets=(),
    )
    physics = resolve_vegetation_physics(case).arrays
    transport = V3RadiativeTransportParameters(12, 0.5, 0, 0, 0, 0)
    burnable = roofs | grass
    edges = build_v3_radiative_transport_edges(
        grid,
        burnable,
        burnable,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        transport,
    )
    request = replace(
        old,
        grid=grid,
        parameters=physics,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=burnable,
        piloted_ignition_mask=burnable,
        material_index=covers,
        landcover_index=covers,
        exposure_edges=edges,
    )
    bundle = replace(
        old_bundle,
        request=request,
        metadata={**old_bundle.metadata, "transport_parameters": asdict(transport)},
        material_labels={**old_bundle.material_labels, 1: "synthetic_grass"},
        landcover_labels={**old_bundle.landcover_labels, 1: "grass"},
    )
    scenario = replace(
        old_scenario,
        horizon_s=150,
        ignitions=tuple(
            Ignition(tuple(2.5 * p for p in ignition.point_m), ignition.time_s)
            for ignition in old_scenario.ignitions
        ),
    )
    return bundle, transport, inventory, scenario, (grid.cell_id(3, 2), grid.cell_id(3, 3))


def engine_for(case):
    bundle, transport, inventory, _, _ = case
    return RoofJointEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )


def test_joint_clears_only_requested_grass_preserves_roof_parameters_and_parent(joint_case):
    bundle, _, _, scenario, (grass_cell, other_grass) = joint_case
    original_hash = request_fingerprint(bundle.request)
    engine = engine_for(joint_case)
    baseline, _, _ = engine.prepare(scenario, RoofJointPlan("baseline"))
    changed, _, ledger = engine.prepare(
        scenario,
        RoofJointPlan(
            "joint",
            ("M",),
            ground_evidence="synthetic_clearance",
            cleared_physical_cell_ids=(grass_cell,),
        ),
    )
    removed = np.flatnonzero(changed.cell_id == grass_cell)
    untouched_grass = np.flatnonzero(changed.cell_id == other_grass)
    retained_roofs = np.array([bool(ids) and ids != ("M",) for ids in changed.owners])
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        after = getattr(changed.node_request.parameters, name).ravel()
        before = getattr(baseline.node_request.parameters, name).ravel()
        np.testing.assert_array_equal(after[retained_roofs], before[retained_roofs])
        np.testing.assert_array_equal(after[untouched_grass], before[untouched_grass])
        np.testing.assert_array_equal(after[removed], 0 if name in EXTENSIVE else before[removed])
    for mask in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask"):
        assert not getattr(changed.node_request, mask).ravel()[removed].any()
    assert ledger["cleared_vegetation_covered_area_m2"] == 25
    assert ledger["cleared_roof_covered_area_m2"] == 25
    assert ledger["cleared_total_union_area_m2"] == 50
    assert ledger["cleared_action_geometry_union_area_m2"] == 50
    assert ledger["removed_vegetation_dry_fuel_kg"] > 0
    assert ledger["removed_dry_fuel_kg"] == pytest.approx(
        ledger["removed_roof_dry_fuel_kg"] + ledger["removed_vegetation_dry_fuel_kg"]
    )
    assert ledger["actual_source_ids"] == ["S"]
    assert request_fingerprint(bundle.request) == original_hash
    assert changed.geometry == baseline.geometry
    for mask in ("material_index", "landcover_index"):
        np.testing.assert_array_equal(
            getattr(changed.node_request, mask)[0, retained_roofs],
            getattr(baseline.node_request, mask)[0, retained_roofs],
        )


def test_duplicate_parent_cells_and_sparse_grass_charge_covered_area_once(joint_case):
    _, _, _, scenario, (_, sparse) = joint_case
    plan = RoofJointPlan(
        "grass", ground_evidence="synthetic_clearance", cleared_physical_cell_ids=(sparse, sparse)
    )
    assert plan.cleared_physical_cell_ids == (sparse,)
    layout, _, ledger = engine_for(joint_case).prepare(scenario, plan)
    assert ledger["cleared_vegetation_parent_cell_count"] == 1
    assert ledger["vegetation_parent_cell_footprint_area_m2"] == 25
    assert ledger["cleared_vegetation_covered_area_m2"] == pytest.approx(10)
    assert ledger["cleared_total_union_area_m2"] == pytest.approx(10)
    assert ledger["cleared_total_covered_area_m2"] == pytest.approx(10)
    assert ledger["cleared_action_geometry_union_area_m2"] == 25
    proof = ledger["vegetation_parent_cell_eligibility_proof"][0]
    assert proof["roof_owners"] == []
    assert proof["parent_building_index"] < 0
    assert proof["domain"] and proof["known"] and proof["burnable"]
    assert proof["action_ground_footprint_area_m2"] == 25
    assert proof["covered_fuel_area_m2"] == pytest.approx(10)
    assert not proof["covered_fuel_subcell_geometry_resolved"]
    assert (
        layout.node_request.parameters.covered_area_m2[0, np.flatnonzero(layout.cell_id == sparse)]
        == 0
    )


@pytest.mark.parametrize("cell_kind", ["missing", "inert", "roof", "core", "source"])
def test_illegal_vegetation_cells_are_rejected_even_if_building_is_also_demolished(
    joint_case, cell_kind
):
    bundle, _, inventory, scenario, _ = joint_case
    grid = bundle.request.grid
    if cell_kind == "missing":
        cell = grid.width * grid.height
    elif cell_kind == "inert":
        cell = 0
    else:
        identifier = {"roof": "M", "core": "G", "source": "S"}[cell_kind]
        point = inventory.by_id[identifier].geometry.representative_point()
        cell = grid.cell_id(*grid.locate(point.x, point.y))
    plan = RoofJointPlan(
        "illegal", ("M",), ground_evidence="synthetic_clearance", cleared_physical_cell_ids=(cell,)
    )
    with pytest.raises(InterventionConflict):
        engine_for(joint_case).prepare(scenario, plan)


@pytest.mark.parametrize("invalid_ids", [[1], (-1,), (True,), (1.5,)])
def test_cell_identifiers_require_explicit_integer_parent_indices(invalid_ids):
    with pytest.raises(ValueError, match="nonnegative integers"):
        RoofJointPlan("illegal", ground_evidence="synthetic", cleared_physical_cell_ids=invalid_ids)


def test_unknown_vegetation_cannot_be_cleared(joint_case):
    bundle, transport, inventory, scenario, (grass, _) = joint_case
    known = bundle.request.known_mask.copy()
    known.ravel()[grass] = False
    masks = {
        key: getattr(bundle.request, key).copy()
        for key in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask")
    }
    for mask in masks.values():
        mask.ravel()[grass] = False
    physics = bundle.request.parameters
    edges = build_v3_radiative_transport_edges(
        bundle.request.grid,
        masks["source_mask"],
        masks["target_mask"],
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(bundle.request.grid.shape),
        transport,
    )
    changed_bundle = replace(
        bundle,
        request=replace(
            bundle.request,
            known_mask=known,
            unknown_policy="exclude",
            exposure_edges=edges,
            **masks,
        ),
    )
    engine = RoofJointEvaluationEngine(
        changed_bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    with pytest.raises(InterventionConflict, match="known burnable"):
        engine.prepare(
            scenario,
            RoofJointPlan(
                "unknown", ground_evidence="synthetic", cleared_physical_cell_ids=(grass,)
            ),
        )


def test_joint_rebuilds_actual_contact_and_radiation_around_new_inert_grass(joint_case):
    bundle, transport, inventory, scenario, (grass, _) = joint_case
    contact = FlameContactParameters(1100, 25, 0.1, 5, "synthetic", "synthetic_contact_fixture")
    bundle = replace(bundle, request=replace(bundle.request, flame_contact=contact))
    engine = RoofJointEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    baseline, before, _ = engine.prepare(scenario, RoofJointPlan("baseline"))
    layout, after, _ = engine.prepare(
        scenario,
        RoofJointPlan("grass", ground_evidence="synthetic", cleared_physical_cell_ids=(grass,)),
    )
    node = int(np.flatnonzero(layout.cell_id == grass)[0])
    assert np.any(before.neighbors[node] >= 0)
    assert np.all(after.neighbors[node] < 0)
    assert not np.any(after.neighbors == node)
    edges = layout.node_request.exposure_edges
    assert node not in edges.source and node not in edges.target
    assert node in baseline.node_request.exposure_edges.source


def test_noop_joint_matches_original_engine_and_fuel_budget_never_increases(joint_case):
    bundle, transport, inventory, scenario, (grass, _) = joint_case
    original = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    _, original_layout, original_result = original.simulate(
        scenario, BuildingDemolitionPlan("none")
    )
    engine = engine_for(joint_case)
    _, noop_layout, noop_result = engine.simulate(scenario, RoofJointPlan("none"))
    for name in original_result.final_state.__dataclass_fields__:
        np.testing.assert_allclose(
            getattr(original_result.final_state, name),
            getattr(noop_result.final_state, name),
            equal_nan=True,
        )
    plan = RoofJointPlan(
        "joint", ("M",), ground_evidence="synthetic", cleared_physical_cell_ids=(grass,)
    )
    record, joint_layout, result = engine.simulate(scenario, plan)
    assert record.diagnostics["parent_case_unchanged"]
    assert result.final_state.time_s == scenario.horizon_s
    assert result.initial_fuel_kg.sum() <= noop_result.initial_fuel_kg.sum()
    assert (
        joint_layout.node_request.parameters.available_energy_J.sum()
        <= noop_layout.node_request.parameters.available_energy_J.sum()
    )
    losses = fire_loss_from_result(
        joint_layout,
        result,
        inventory,
        demolished_ids=plan.demolished_ids,
        source_ids=record.metrics["actual_source_ids"],
        original_node_owners=engine.layout.owners,
    )
    assert losses["physical"]["roof"]["cleared_covered_area_m2"] == 25
    assert losses["physical"]["nonroof"]["cleared_covered_area_m2"] == 25
    assert losses["physical"]["all"]["cleared_covered_area_m2"] == 50
    assert losses["fuel"]["planned_removed_kg"] == pytest.approx(
        record.intervention["removed_pyrolysable_fuel_kg"]
    )
    assert request_fingerprint(original_layout.physical_request) == request_fingerprint(
        bundle.request
    )
