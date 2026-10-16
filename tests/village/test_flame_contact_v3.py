"""Mechanism, finite energy, geometry, clock and serialization verification."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.flame_contact_v3 import (
    FlameContactOperator,
    FlameContactParameters,
    load_flame_contact_parameters,
)
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

ROOT = Path(__file__).parents[2]
DIRECTORY = ROOT / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("contact_audit", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def contact():
    return load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")


def operator(request, contact):
    return FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        contact,
        request.ground_elevation_m,
        request.material_index,
    )


def test_newton_transfer_uses_covered_area_and_existing_nonradiative_budget(contact):
    request = audit.make_request(("grass", "grass"))
    physics = request.parameters
    hrr = np.zeros(request.grid.shape)
    hrr[0, 0] = physics.peak_hrr_W[0, 0]
    state = np.full(request.grid.shape, 300.0)
    susceptible = np.asarray([[False, True]])
    transfer = operator(request, contact).transfer(hrr, state, susceptible)
    expected = contact.contact_fraction * 4 * 50 * (1100 - 300)
    assert transfer.target_power_W[0, 1] == pytest.approx(expected)
    assert transfer.target_contact_fraction[0, 1] == pytest.approx(0.5)
    assert transfer.transferred_W <= transfer.available_nonradiative_W
    assert transfer.energy_limited_sources == 0
    # Absorptivity is irrelevant to direct hot-gas convection.
    changed = replace(request, parameters=replace(physics, absorptivity=np.zeros((1, 2))))
    assert np.array_equal(
        operator(changed, contact).transfer(hrr, state, susceptible).target_power_W,
        transfer.target_power_W,
    )


def test_source_limit_scales_both_heat_and_contact_area(contact):
    request = audit.make_request(("grass", "grass", "grass"))
    hrr = np.zeros((1, 3))
    hrr[0, 1] = request.parameters.peak_hrr_W[0, 1]
    extreme = replace(contact, heat_transfer_coefficient_W_m2_K=1e6)
    transfer = operator(request, extreme).transfer(
        hrr, np.full((1, 3), 300), np.asarray([[True, False, True]])
    )
    assert transfer.energy_limited_sources == 1
    assert transfer.transferred_W == pytest.approx(transfer.available_nonradiative_W)
    assert np.all(transfer.target_contact_fraction < contact.contact_fraction)
    assert transfer.target_power_W[0, 0] == pytest.approx(transfer.target_power_W[0, 2])


def test_zero_hrr_and_pure_radiation_supply_no_convective_energy(contact):
    request = audit.make_request(("grass", "grass"))
    temperatures = np.full((1, 2), 300.0)
    zero = operator(request, contact).transfer(
        np.zeros((1, 2)), temperatures, request.burnable_mask
    )
    assert zero.transferred_W == 0
    assert not np.any(zero.target_contact_fraction)
    request = replace(
        request, parameters=replace(request.parameters, radiative_fraction=np.ones((1, 2)))
    )
    pure = operator(request, contact).transfer(
        request.parameters.peak_hrr_W, temperatures, request.burnable_mask
    )
    assert pure.transferred_W == 0
    assert not np.any(pure.target_contact_fraction)


def test_height_and_inert_gap_block_contact_and_terrain_is_used(contact):
    road = audit.make_request(("grass", "bare_or_road", "grass"))
    assert np.all(operator(road, contact).neighbors == -1)
    high = audit.make_request(("grass", "building"))
    assert operator(high, contact).neighbors[0, 3] == -1
    low = audit.make_request(("grass", "building"), low_building_receiver=True)
    assert operator(low, contact).neighbors[0, 3] == 1
    uphill = replace(low, ground_elevation_m=np.asarray([[0.0, 1.0]]))
    assert operator(uphill, contact).neighbors[0, 3] == -1


def test_low_building_contact_is_independent_of_radiation_and_tree_height(contact):
    request = audit.make_request(("grass", "building", "tree_equivalent"))
    original = request.parameters.receiver_height_m.copy()
    building_id = int(request.material_index[0, 1])
    low = replace(contact, receiver_height_overrides_m=((building_id, 0.2),))
    graph = operator(request, low)
    assert graph.neighbors[0, 3] == 1
    assert np.array_equal(request.parameters.receiver_height_m, original)
    tree = audit.make_request(("grass", "tree_equivalent"))
    assert operator(tree, low).neighbors[0, 3] == -1
    uphill = replace(request, ground_elevation_m=np.array([[0.0, 1.0, 0.0]]))
    assert operator(uphill, low).neighbors[0, 3] == -1
    with pytest.raises(ValueError, match="matching material grid"):
        FlameContactOperator.build(request.grid, request.burnable_mask, request.parameters, low)


@pytest.mark.parametrize(
    "overrides",
    [((5, -1),), ((5, float("nan")),), ((5, True),), ((True, 0.2),), ((5, 0.2), (5, 0.3)), ((5,),)],
)
def test_invalid_receiver_overrides_rejected(contact, overrides):
    with pytest.raises((ValueError, TypeError)):
        replace(contact, receiver_height_overrides_m=overrides)


def test_contact_override_bundle_roundtrip_and_energy_budget(tmp_path, contact):
    low = replace(contact, receiver_height_overrides_m=((5, 0.2),))
    assert FlameContactParameters.from_mapping(json.loads(json.dumps(low.to_mapping()))) == low
    request = replace(audit.make_request(("shrub", "building")), flame_contact=low)
    write_v3_case_bundle(request, tmp_path / "case")
    loaded = load_v3_case_bundle(tmp_path / "case").request
    assert loaded.flame_contact == low
    result = run_v3_simulation(loaded)
    assert np.isfinite(result.final_state.ignition_time_s[0, 1])
    budget = result.energy_budget
    assert budget.non_radiative_released_J == pytest.approx(
        budget.convective_transferred_J + budget.nonradiative_untransferred_J
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("gas_temperature_K", float("nan")),
        ("contact_fraction", 1.1),
        ("contact_fraction", 0),
        ("heat_transfer_coefficient_W_m2_K", -1),
        ("max_cell_size_m", True),
        ("evidence", "validated"),
        ("provenance", ""),
    ],
)
def test_invalid_effective_parameters_rejected(contact, name, value):
    with pytest.raises((TypeError, ValueError)):
        replace(contact, **{name: value})


def test_schema_strictness_resolution_limit_and_hrr_validation(contact):
    mapping = contact.to_mapping()
    assert FlameContactParameters.from_mapping(mapping) == contact
    with pytest.raises(ValueError):
        FlameContactParameters.from_mapping({**mapping, "typo": 1})
    request = audit.make_request(("grass", "grass"))
    with pytest.raises(ValueError, match="maximum cell size"):
        replace(request, flame_contact=replace(contact, max_cell_size_m=1))
    with pytest.raises(ValueError, match="configured peak"):
        operator(request, contact).transfer(
            request.parameters.peak_hrr_W * 2, np.full((1, 2), 300.0), request.burnable_mask
        )


def test_radiation_peak_cannot_reach_vegetation_threshold_in_this_original_fixture():
    rows = audit.peak_radiation_audit()
    plants = [r for r in rows if r["profile_id"] != "synthetic_building"]
    assert len(plants) == 4
    assert all(r["equilibrium_upper_bound_K"] < r["ignition_temperature_K"] for r in plants)


def test_contact_enables_grass_chain_with_original_thresholds_and_keeps_shared_clock(contact):
    request = audit.make_request(("grass",) * 3, end_time_s=600)
    radiation = run_v3_simulation(request, retain_snapshots=False)
    results = [
        run_v3_simulation(
            replace(
                request, flame_contact=contact, scenario=replace(request.scenario, max_dt_s=dt)
            ),
            retain_snapshots=False,
        )
        for dt in (0.5, 0.25)
    ]
    assert np.count_nonzero(np.isfinite(radiation.final_state.ignition_time_s)) == 1
    assert not np.any(radiation.final_state.received_convection_J)
    for result in results:
        times = result.final_state.ignition_time_s.ravel()
        assert np.all(np.isfinite(times))
        assert 0 == times[0] < times[1] < times[2]
        budget = result.energy_budget
        assert budget.non_radiative_released_J == pytest.approx(
            budget.convective_transferred_J + budget.nonradiative_untransferred_J
        )
        assert np.sum(result.final_state.received_convection_J) == pytest.approx(
            budget.convective_transferred_J
        )
        assert budget.released_J == pytest.approx(np.sum(result.final_state.released_energy_J))
        assert budget.released_J == pytest.approx(result.consumed_fuel_kg * 18e6)
    assert (
        np.max(
            np.abs(results[0].final_state.ignition_time_s - results[1].final_state.ignition_time_s)
        )
        < 5
    )


def test_bundle_crop_and_cli_override_preserve_explicit_contact_and_outputs(tmp_path, contact):
    request = audit.make_request(("grass",) * 3, end_time_s=10)
    enabled = replace(request, flame_contact=contact)
    write_v3_case_bundle(enabled, tmp_path / "enabled")
    assert load_v3_case_bundle(tmp_path / "enabled").request.flame_contact == contact
    from pyfireca.village.preflight_v3 import preflight_v3_case

    report = preflight_v3_case(tmp_path / "enabled")
    assert report["flame_contact"] == contact.to_mapping()
    assert report["transport"]["contact_operator_storage_bytes_estimate"] == 3 * 48
    assert (
        "effective_flame_contact_requires_independent_calibration"
        in report["research_gate_reasons"]
    )
    crop_v3_case_bundle(
        tmp_path / "enabled", tmp_path / "crop", row_start=0, row_stop=1, col_start=0, col_stop=2
    )
    assert load_v3_case_bundle(tmp_path / "crop").request.flame_contact == contact
    write_v3_case_bundle(request, tmp_path / "old")
    assert load_v3_case_bundle(tmp_path / "old").request.flame_contact is None
    result, output = run_v3_case_bundle(
        tmp_path / "old",
        tmp_path / "run",
        flame_contact_config=DIRECTORY / "flame-contact.synthetic.yml",
    )
    run = json.loads((output / "run.json").read_text())
    assert run["flame_contact"]["schema_version"] == "village-flame-contact-1"
    assert run["flame_contact"]["evidence"] == "synthetic"
    assert (output / "outputs/received_convection_final_J.npy").is_file()
    assert result.energy_budget.convective_transferred_J > 0


def test_integer_ambient_temperature_cannot_truncate_incremental_heating(contact):
    request = audit.make_request(("tree_equivalent",) * 2, end_time_s=600)
    integer = replace(request, flame_contact=contact)
    floating = replace(integer, scenario=replace(integer.scenario, ambient_temperature_K=300.0))
    first = run_v3_simulation(integer, retain_snapshots=False)
    second = run_v3_simulation(floating, retain_snapshots=False)
    assert first.final_state.surface_temperature_K.dtype == np.float64
    assert np.array_equal(
        first.final_state.ignition_time_s, second.final_state.ignition_time_s, equal_nan=True
    )
    assert np.array_equal(
        first.final_state.surface_temperature_K, second.final_state.surface_temperature_K
    )


def test_competing_hot_sources_do_not_contact_more_than_total_receiver_area(contact):
    request = audit.make_request(("grass",) * 3)
    contact = replace(contact, contact_fraction=0.75)
    hrr = request.parameters.peak_hrr_W.copy()
    hrr[0, 1] = 0
    transfer = operator(request, contact).transfer(
        hrr, np.full((1, 3), 300), np.asarray([[False, True, False]])
    )
    assert transfer.target_contact_fraction[0, 1] == pytest.approx(1)
    assert transfer.target_power_W[0, 1] == pytest.approx(4 * 50 * 800)
    assert transfer.transferred_W <= transfer.available_nonradiative_W


def test_v2_integer_temperature_initialization_matches_float_case():
    from pyfireca.village.solver_v2 import run_v2_simulation
    from pyfireca.village.workflow_v2 import load_v2_file_case

    request = load_v2_file_case(ROOT / "examples/village/v2_two_cells/case.synthetic.yml").request
    first = run_v2_simulation(
        replace(
            request,
            scenario=replace(
                request.scenario, ambient_temperature_K=int(request.scenario.ambient_temperature_K)
            ),
        )
    )
    second = run_v2_simulation(request)
    assert first.final_state.temperature_K.dtype == np.float64
    assert np.array_equal(first.final_state.temperature_K, second.final_state.temperature_K)
    assert np.array_equal(
        first.final_state.ignition_time_s, second.final_state.ignition_time_s, equal_nan=True
    )


@pytest.mark.parametrize("extent", [-1, float("nan"), float("inf"), True, "0.15"])
def test_invalid_receiver_band_extent_rejected(contact, extent):
    with pytest.raises((ValueError, TypeError)):
        replace(contact, receiver_vertical_half_extent_m=extent)


def test_finite_receiver_overlap_preserves_flat_and_attenuates_partial_contact(contact):
    request = audit.make_request(("grass", "grass"))
    band = replace(contact, receiver_vertical_half_extent_m=0.15)
    assert operator(request, band).geometry_fraction[0, 3] == pytest.approx(1)
    downhill = replace(request, ground_elevation_m=np.array([[0.0, -0.2]]))
    assert operator(downhill, contact).neighbors[0, 3] == -1
    geometry = operator(downhill, band)
    assert geometry.neighbors[0, 3] == 1
    assert geometry.geometry_fraction[0, 3] == pytest.approx(1 / 3)
    hrr = np.array([[request.parameters.peak_hrr_W[0, 0], 0]])
    susceptible = np.array([[False, True]])
    transferred = geometry.transfer(hrr, np.full((1, 2), 300.0), susceptible)
    flat = operator(request, band).transfer(hrr, np.full((1, 2), 300.0), susceptible)
    assert transferred.transferred_W == pytest.approx(flat.transferred_W / 3)
    assert transferred.transferred_W <= transferred.available_nonradiative_W
    far_downhill = replace(request, ground_elevation_m=np.array([[0.0, -0.4]]))
    assert operator(far_downhill, band).neighbors[0, 3] == -1
    uphill = replace(request, ground_elevation_m=np.array([[0.0, 0.1]]))
    assert operator(uphill, band).geometry_fraction[0, 3] == pytest.approx(5 / 6)
    high_tree = audit.make_request(("grass", "tree_equivalent"))
    assert operator(high_tree, band).neighbors[0, 3] == -1
    assert np.all(
        operator(audit.make_request(("grass", "bare_or_road", "grass")), band).neighbors == -1
    )


def test_finite_receiver_band_roundtrip_preserves_budget_and_legacy_defaults(tmp_path, contact):
    assert "receiver_vertical_half_extent_m" not in contact.to_mapping()
    band = replace(contact, receiver_vertical_half_extent_m=0.15)
    assert type(contact).from_mapping(json.loads(json.dumps(band.to_mapping()))) == band
    request = replace(
        audit.make_request(("grass", "grass")),
        flame_contact=band,
        ground_elevation_m=np.array([[0.0, -0.2]]),
    )
    write_v3_case_bundle(request, tmp_path / "case")
    restored = load_v3_case_bundle(tmp_path / "case").request
    assert restored.flame_contact == band
    result = run_v3_simulation(restored)
    assert np.isfinite(result.final_state.ignition_time_s[0, 1])
    budget = result.energy_budget
    assert budget.non_radiative_released_J == pytest.approx(
        budget.convective_transferred_J + budget.nonradiative_untransferred_J
    )


def test_subresolution_receiver_band_falls_back_to_point_without_nan(contact):
    request = replace(
        audit.make_request(("grass", "grass")), ground_elevation_m=np.array([[500.0, 500.0]])
    )
    band = replace(contact, receiver_vertical_half_extent_m=1e-300)
    with np.errstate(all="raise"):
        finite = operator(request, band)
    np.testing.assert_array_equal(finite.neighbors, operator(request, contact).neighbors)
    np.testing.assert_array_equal(
        finite.geometry_fraction, operator(request, contact).geometry_fraction
    )


def test_continuous_slope_face_contact_reaches_all_four_directions_without_extra_energy(contact):
    request = audit.make_request(("tree_equivalent",) * 9)
    physics = replace(
        request.parameters,
        **{
            name: getattr(request.parameters, name).reshape(3, 3)
            for name in request.parameters.__dataclass_fields__
        },
    )
    grid = VillageGridSpec(3, 3, 2, 0, 6)
    mask = np.ones(grid.shape, dtype=bool)
    ground = np.array([[-3.17] * 3, [0.0] * 3, [3.17] * 3]) + 270
    legacy = FlameContactOperator.build(grid, mask, physics, contact, ground)
    face = replace(contact, terrain_reference="shared_face")
    graph = FlameContactOperator.build(grid, mask, physics, face, ground)
    assert legacy.neighbors[4].tolist() == [-1, -1, 3, 5]
    assert graph.neighbors[4].tolist() == [1, 7, 3, 5]
    rotated = FlameContactOperator.build(grid, mask, physics, face, np.rot90(ground))
    np.testing.assert_array_equal(graph.geometry_fraction, rotated.geometry_fraction)
    shifted = FlameContactOperator.build(grid, mask, physics, face, ground + 10000)
    np.testing.assert_array_equal(graph.geometry_fraction, shifted.geometry_fraction)
    hrr = np.zeros(grid.shape)
    hrr[1, 1] = physics.peak_hrr_W[1, 1]
    susceptible = mask.copy()
    susceptible[1, 1] = False
    transfer = graph.transfer(hrr, np.full(grid.shape, 300.0), susceptible)
    values = transfer.target_power_W[[0, 2, 1, 1], [1, 1, 0, 2]]
    np.testing.assert_allclose(values, values[0], rtol=0, atol=0)
    assert values[0] > 0
    assert transfer.transferred_W <= transfer.available_nonradiative_W


def test_face_contact_can_ignite_building_from_trees_on_slope_with_original_fuels(contact):
    request = replace(
        audit.make_request(("tree_equivalent", "building"), end_time_s=1200),
        flame_contact=contact,
        ground_elevation_m=np.array([[270.0, 273.17]]),
    )
    assert operator(request, contact).neighbors[0, 3] == -1
    face = replace(contact, terrain_reference="shared_face")
    corrected = replace(request, flame_contact=face)
    assert operator(corrected, face).neighbors[0, 3] == 1
    result = run_v3_simulation(corrected, retain_snapshots=False)
    assert 0 < result.final_state.ignition_time_s[0, 1] < request.scenario.end_time_s
    assert result.final_state.received_convection_J[0, 1] > 0
    check_final(corrected, result)
    # Shared terrain does not bypass roads or enable short grass to reach a crown.
    road = audit.make_request(("tree_equivalent", "bare_or_road", "building"))
    assert np.all(operator(road, face).neighbors == -1)
    high = audit.make_request(("grass", "tree_equivalent"))
    assert operator(high, face).neighbors[0, 3] == -1


def test_face_contact_roundtrip_and_legacy_default(tmp_path, contact):
    assert "terrain_reference" not in contact.to_mapping()
    assert (
        FlameContactParameters.from_mapping(contact.to_mapping()).terrain_reference == "cell_center"
    )
    face = replace(contact, terrain_reference="shared_face")
    request = replace(audit.make_request(("grass", "grass")), flame_contact=face)
    write_v3_case_bundle(request, tmp_path / "case")
    assert load_v3_case_bundle(tmp_path / "case").request.flame_contact == face
    with pytest.raises(ValueError, match="terrain reference"):
        replace(contact, terrain_reference="ignore_slope")
