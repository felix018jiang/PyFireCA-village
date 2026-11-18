"""Geometry correction, conserved assemblies and honest mesh independence."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate
from shapely.geometry import box

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.spatial import audit_target_mesh, refresh_building_geometry
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.solver_v3 import run_v3_simulation

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "spatial_fixture", ROOT / "examples/research/heritage/synthetic_case.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def rebuild(bundle, transport, inventory, **kwargs):
    return refresh_building_geometry(
        bundle,
        inventory,
        transport,
        ground_material=kwargs.get(
            "ground_material", np.zeros(bundle.request.grid.shape, np.int32)
        ),
        ground_landcover=np.zeros(bundle.request.grid.shape, np.int32),
    )


def moved_inventory(inventory, identifier="U", dx=1.0, dy=0.25):
    return replace(
        inventory,
        buildings=tuple(
            replace(b, geometry=translate(b.geometry, xoff=dx, yoff=dy))
            if b.building_id == identifier
            else b
            for b in inventory.buildings
        ),
    )


def test_no_geometry_change_preserves_request_and_temperature_states():
    bundle, transport, inventory, _scenario = fixture.make_case()
    request, report = rebuild(bundle, transport, inventory)
    assert report["changed_cells"] == 0
    assert request_fingerprint(request) == request_fingerprint(bundle.request)
    a, b = (run_v3_simulation(q, retain_snapshots=False) for q in (request, bundle.request))
    for name in a.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(getattr(a.final_state, name), getattr(b.final_state, name))


def test_moving_a_building_preserves_ids_total_area_mass_and_parent():
    bundle, transport, inventory, _scenario = fixture.make_case()
    before = request_fingerprint(bundle.request)
    updated = moved_inventory(inventory)
    request, report = rebuild(bundle, transport, updated)
    assert report["changed_cells"] > 0
    assert report["exposed_ground_cells"] == 0  # This shift leaves a partial original roof cell.
    assert report["all_declared_buildings_retained"]
    assert report["thermal_simulations_performed"] == 0
    assert report["new_building_area_m2"] == pytest.approx(20)
    assert request.parameters.initial_dry_fuel_mass_kg.sum() == pytest.approx(360)
    assert set(np.unique(request.building_index)) == set(np.unique(bundle.request.building_index))
    number = inventory.by_id["U"].object_id
    assert np.count_nonzero(request.building_index == number) > 1
    unchanged = (request.building_index == bundle.request.building_index) & np.isclose(
        request.parameters.coverage_fraction, bundle.request.parameters.coverage_fraction
    )
    for name in request.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(request.parameters, name)[unchanged],
            getattr(bundle.request.parameters, name)[unchanged],
        )
    assert request_fingerprint(bundle.request) == before


def test_exposed_ground_uses_registered_context_and_its_reference_physics():
    bundle, transport, inventory, _scenario = fixture.make_case()
    request, report = rebuild(bundle, transport, moved_inventory(inventory, dx=4, dy=0))
    old = bundle.request.building_index == inventory.by_id["U"].object_id
    assert report["exposed_ground_cells"] == 1
    assert not request.burnable_mask[old].any()
    assert np.all(request.parameters.covered_area_m2[old] == 4)
    assert "RGB_hypothesis" in report["ground_policy"]
    assert request.parameters.initial_dry_fuel_mass_kg.sum() == pytest.approx(360)


def test_different_registered_ground_is_rejected_instead_of_overwriting_scene():
    bundle, transport, inventory, _scenario = fixture.make_case()
    with pytest.raises(ValueError, match="parent nonbuilding context"):
        rebuild(
            bundle,
            transport,
            inventory,
            ground_material=np.ones(bundle.request.grid.shape, np.int32),
        )


def test_refresh_cannot_silently_replace_an_unsupported_radiation_graph():
    bundle, transport, inventory, _scenario = fixture.make_case()
    altered = replace(
        bundle.request,
        exposure_edges=replace(
            bundle.request.exposure_edges, weight=bundle.request.exposure_edges.weight * 0.9
        ),
    )
    with pytest.raises(ValueError, match="unsupported parent radiation graph"):
        rebuild(replace(bundle, request=altered), transport, inventory)


def test_heterogeneous_assembly_cannot_be_replaced_by_one_default():
    bundle, transport, inventory, _scenario = fixture.make_case()
    values = bundle.request.parameters.specific_heat_J_kg_K.copy()
    values[bundle.request.building_index == inventory.by_id["G"].object_id] *= 2
    request = replace(
        bundle.request, parameters=replace(bundle.request.parameters, specific_heat_J_kg_K=values)
    )
    with pytest.raises(ValueError, match="nonuniform reference assembly"):
        rebuild(replace(bundle, request=request), transport, moved_inventory(inventory))


def test_out_of_grid_outline_cannot_disappear_silently():
    bundle, transport, inventory, _scenario = fixture.make_case()
    with pytest.raises(ValueError, match="outside the grid envelope"):
        rebuild(bundle, transport, moved_inventory(inventory, dx=100))


def mesh_inventory(*, overlap=False):
    _bundle, _transport, inventory, _scenario = fixture.make_case(
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(0.7, 0.2, 0.8, 0.3) if overlap else box(0.1, 0.1, 0.4, 0.4),
            "G": box(0.6, 0.1, 0.9, 0.4),
            "F": box(8, 6, 10, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    return inventory


def test_finer_grid_separates_disjoint_outlines_without_losing_target_area():
    inventory = mesh_inventory()
    coarse, _ = audit_target_mesh(inventory, VillageGridSpec(20, 20, 1, 0, 20), "G")
    fine, arrays = audit_target_mesh(inventory, VillageGridSpec(200, 200, 0.1, 0, 20), "G")
    assert coarse["shared_temperature_cells"] == 1
    assert not coarse["independent_target_geometry"]
    assert fine["shared_temperature_cells"] == 0
    assert fine["positive_gap_cell_face_count"] == 0
    assert fine["independent_target_geometry"]
    assert fine["target_observed_area_m2"] == pytest.approx(0.09)
    assert arrays["target_intersection_area_m2"].sum() == pytest.approx(0.09)


def test_actual_overlapping_outlines_do_not_become_independent_by_refining_grid():
    audit, _ = audit_target_mesh(
        mesh_inventory(overlap=True), VillageGridSpec(200, 200, 0.1, 0, 20), "G"
    )
    assert audit["true_outline_overlaps"]
    assert audit["shared_temperature_cells"] > 0
    assert not audit["independent_target_geometry"]


def test_mesh_requires_whole_target_and_preserves_all_neighbour_observations():
    inventory = mesh_inventory()
    with pytest.raises(ValueError, match="entire target"):
        audit_target_mesh(inventory, VillageGridSpec(2, 2, 0.1, 0, 0.2), "G")
    report, _ = audit_target_mesh(inventory, VillageGridSpec(20, 20, 1, 0, 20), "G")
    assert report["local_represented_object_count"] == len(inventory.buildings)
    assert "no_thermal" in report["scope"]
