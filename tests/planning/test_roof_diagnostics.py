"""Counterfactual paths must preserve sources, geometry and material budgets."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.roof_diagnostics import (
    extract_roof,
    mechanism_request,
    point_samples,
    quadrature_samples,
    uniform_flux,
)
from pyfireca.planning.roof_ignition import (
    fixed_area_ignition,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout, roof_radiation_graph
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "mechanism_synthetic", ROOT / "examples/research/heritage/synthetic_case.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def objects(h=1.0):
    bundle, transport, inventory, _ = fixture.make_case()
    parent = replace(
        bundle.request,
        flame_contact=FlameContactParameters(
            1100, 50, 0.5, 5, "synthetic", "isolated test", terrain_reference="shared_face"
        ),
        ground_elevation_m=np.arange(
            bundle.request.grid.width * bundle.request.grid.height, dtype=float
        ).reshape(bundle.request.grid.shape),
    )
    footprint = fixed_area_ignition(inventory, "S", (5, 9), 1.0)
    layout = refine_roof_layout(
        resolve_roof_layout(parent, inventory),
        inventory,
        cell_size_m=h,
        refined_ids=("S", "G"),
        footprints=(footprint,),
    )
    seeds = np.flatnonzero(np.asarray([i is not None for i in layout.ignition_owner]))
    scenario = replace(
        parent.scenario, ignitions=tuple(VillageIgnitionEvent(0, 0, int(i)) for i in seeds)
    )
    request = layout.node_inputs.bind(scenario)
    contact = refined_contact_operator(layout)
    components = layout.to_mapping()["roof_components"]
    source, target = [
        extract_roof(
            request,
            components,
            identifier,
            contact.neighbors,
            contact.geometry_fraction,
            parent_elevation_m=parent.ground_elevation_m,
        )
        for identifier in ("S", "G")
    ]
    return source, target, scenario, transport, parent.flame_contact, layout


def test_extraction_uses_geographic_parent_elevation_and_conserves_seed():
    source, target, _, _, _, layout = objects()
    assert source.parameters.covered_area_m2.ravel()[source.seed].sum() == pytest.approx(1)
    parts = [
        p for p in layout.to_mapping()["roof_components"] if tuple(p["building_ids"]) == ("S",)
    ]
    assert source.elevation_m.tolist() == [
        layout.physical_request.ground_elevation_m.ravel()[p["physical_cell_id"]] for p in parts
    ]
    assert source.neighbors.max() < source.count
    assert np.all(source.geometry_fraction[source.neighbors < 0] == 0)
    assert target.seed.sum() == 0


def test_crossed_scales_preserve_each_objects_exact_material_arrays():
    source, _, scenario, transport, parameters, _ = objects(1.0)
    _, target, *_ = objects(0.5)
    request, contact = mechanism_request(
        source, target, scenario, transport, parameters, mode="seed_only"
    )
    assert contact is None
    for name in source.parameters.__dataclass_fields__:
        values = getattr(request.parameters, name).ravel()
        np.testing.assert_array_equal(
            values[: source.count], getattr(source.parameters, name).ravel()
        )
        np.testing.assert_array_equal(
            values[source.count :], getattr(target.parameters, name).ravel()
        )
    assert np.array_equal(request.piloted_ignition_mask.ravel()[: source.count], source.seed)
    assert all(event.time_s == 0 for event in request.scenario.ignitions)
    assert len(request.scenario.ignitions) == source.seed.sum()


@pytest.mark.parametrize("mode", ["seed_only", "source_radiation", "source_contact", "source_both"])
def test_switches_exclude_target_feedback_without_altering_source_budget(mode):
    source, target, scenario, transport, parameters, _ = objects()
    request, contact = mechanism_request(source, target, scenario, transport, parameters, mode=mode)
    edges = request.exposure_edges
    assert np.all(edges.source < source.count)
    if mode in ("seed_only", "source_contact"):
        assert np.all(edges.target >= source.count)
    if contact:
        assert np.all(contact.neighbors[source.count :] == -1)
        assert contact.parameters == parameters
    assert request.parameters.available_energy_J[0, : source.count].sum() == pytest.approx(
        source.parameters.available_energy_J.sum()
    )


def test_seed_only_simulation_cannot_acquire_unprescribed_source_fire():
    source, target, scenario, transport, parameters, _ = objects()
    request, contact = mechanism_request(
        source, target, scenario, transport, parameters, mode="seed_only"
    )
    result = _run_resolved_v3_simulation(request, contact=contact, retain_snapshots=False)
    check_final(request, result)
    times = result.final_state.ignition_time_s.ravel()[: source.count]
    assert np.array_equal(np.isfinite(times), source.seed)
    assert result.final_state.received_convection_J.sum() == 0


def test_reconstructed_point_kernel_matches_existing_roof_operator():
    source, target, scenario, transport, parameters, layout = objects()
    request, _ = mechanism_request(
        source, target, scenario, transport, parameters, mode="source_both"
    )
    old = roof_radiation_graph(layout, transport)
    parts = layout.to_mapping()["roof_components"]
    si = [p["node"] for p in parts if tuple(p["building_ids"]) == ("S",)]
    ti = [p["node"] for p in parts if tuple(p["building_ids"]) == ("G",)]
    remap = np.full(len(layout.cell_id), -1, int)
    remap[si + ti] = np.arange(len(si + ti))
    keep = np.isin(old.source, si) & np.isin(old.target, si + ti)
    from pyfireca.village.domain import ExposureEdges

    expected = ExposureEdges(
        remap[old.source[keep]], remap[old.target[keep]], old.weight[keep], request.grid.width
    )
    np.testing.assert_array_equal(request.exposure_edges.source, expected.source)
    np.testing.assert_array_equal(request.exposure_edges.target, expected.target)
    np.testing.assert_allclose(request.exposure_edges.weight, expected.weight, rtol=1e-14)


def test_quadrature_preserves_prescribed_area_and_unit_emission_integral(monkeypatch):
    source, target, _, transport, _, _ = objects()
    for spacing in (0.5, 0.25):
        sample = quadrature_samples(source, spacing, seed_only=True)
        assert sample["area_m2"].sum() == pytest.approx(1, abs=1e-9)
        monkeypatch.setattr(
            "pyfireca.planning.roof_diagnostics.kernel",
            lambda xy, z, tx, tz, height, transport: np.ones((len(xy), len(tx))),
        )
        np.testing.assert_allclose(uniform_flux(sample, point_samples(target), transport), 1)


def test_unknown_mode_fails_explicitly():
    source, target, scenario, transport, parameters, _ = objects()
    with pytest.raises(ValueError, match="unknown mechanism"):
        mechanism_request(source, target, scenario, transport, parameters, mode="fit_results")
