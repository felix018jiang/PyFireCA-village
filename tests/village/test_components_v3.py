"""Independent component state, geometric pathways, conserved budgets and replay inputs."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.components_v3 import (
    ComponentConfiguration,
    FuelComponent,
    component_summary,
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location(
    "component_audit_fixture", DIRECTORY / "audit_spread.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def config():
    return load_component_configuration(DIRECTORY / "components.synthetic.yml")


@pytest.fixture
def contact():
    return load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")


def test_live_dead_water_area_and_energy_partitions_close_per_physical_cell(config):
    request = audit.make_request(("grass", "tree_equivalent", "building", "bare_or_road"))
    resolved = resolve_components(request, config)
    assert np.array_equal(resolved.cell_id, [0, 1, 1, 2, 2, 3])
    physics = resolved._node_request.parameters
    assert physics.moisture_content_dry_basis[0, 1] == pytest.approx(0.15)
    assert physics.live_dry_fuel_mass_kg[0, 1] == 0
    assert physics.moisture_content_dry_basis[0, 2] > 0.15
    assert physics.ignition_temperature_K[0, 1] == request.parameters.ignition_temperature_K[0, 1]
    assert physics.ignition_temperature_K[0, 2] == request.parameters.ignition_temperature_K[0, 1]
    assert all(error < 2e-7 for error in resolved.conservation.values())
    # Scaling the total energy scales the peak at unchanged burn durations.
    assert physics.peak_hrr_W[0, 1] == pytest.approx(request.parameters.peak_hrr_W[0, 1] * 0.105)
    assert physics.available_energy_J[0, 1] == pytest.approx(
        request.parameters.available_energy_J[0, 1] * 0.105
    )
    assert np.array_equal(request.parameters.initial_dry_fuel_mass_kg, [[2.4, 32, 72, 0]])


@pytest.mark.parametrize(
    "field,value",
    [
        ("live_fraction", 1.1),
        ("dead_fraction", -0.1),
        ("area_fraction", 0),
        ("footprint_fraction", True),
        ("source_base_height_m", float("nan")),
        ("flame_extent_m", 0),
        ("receiver_height_m", 0),
    ],
)
def test_invalid_component_parameters_are_rejected(config, field, value):
    with pytest.raises((ValueError, TypeError)):
        replace(config.recipes["tree_equivalent"][0], **{field: value})


def test_schema_requires_explicit_budget_geometry_and_ignition_policies(config):
    assert (
        ComponentConfiguration.from_mapping(config.to_mapping()).to_mapping() == config.to_mapping()
    )
    for patch in (
        {"radiation_policy": "new_view_factors"},
        {"direct_ignition_policy": "all"},
        {"schema_version": "old"},
        {"evidence": ""},
    ):
        with pytest.raises(ValueError):
            ComponentConfiguration.from_mapping({**config.to_mapping(), **patch})
    parts = config.recipes["tree_equivalent"]
    with pytest.raises(ValueError, match="sum to one"):
        replace(
            config, recipes={"tree_equivalent": (replace(parts[0], dead_fraction=0.5), parts[1])}
        )
    with pytest.raises(ValueError, match="unique"):
        replace(config, recipes={"tree_equivalent": (parts[0], replace(parts[1], id=parts[0].id))})


def test_zero_mass_recipe_and_resource_limits_fail_before_expansion(config):
    request = audit.make_request(("grass", "building"))
    parts = config.recipes["building"]
    zero = replace(
        config,
        recipes={
            "building": (
                replace(parts[0], live_fraction=1, dead_fraction=0),
                replace(parts[1], live_fraction=0, dead_fraction=1),
            )
        },
    )
    with pytest.raises(ValueError, match="zero resolved dry fuel"):
        resolve_components(request, zero)
    with pytest.raises(ValueError, match="node limit"):
        resolve_components(request, config, max_nodes=2)
    with pytest.raises(ValueError, match="edge limit"):
        resolve_components(request, config, max_radiation_edges=1)


def test_radiation_lift_does_not_duplicate_cell_power(config):
    request = audit.make_request(("grass", "tree_equivalent", "building"))
    resolved = resolve_components(request, config)
    edges = resolved._node_request.exposure_edges
    parent = request.exposure_edges
    for source in range(len(resolved.cell_id)):
        cell = resolved.cell_id[source]
        for target in range(request.domain_mask.size):
            lifted = edges.weight[
                (edges.source == source) & (resolved.cell_id[edges.target] == target)
            ].sum()
            original = parent.weight[(parent.source == cell) & (parent.target == target)].sum()
            assert lifted == pytest.approx(original)
    assert not np.any(resolved.cell_id[edges.source] == resolved.cell_id[edges.target])
    peak = resolved._node_request.parameters.peak_hrr_W.ravel()
    chi = resolved._node_request.parameters.radiative_fraction.ravel()
    delivered = float(np.sum(peak[edges.source] * chi[edges.source] * edges.weight))
    original_delivered = float(
        np.sum(
            request.parameters.peak_hrr_W.ravel()[parent.source]
            * request.parameters.radiative_fraction.ravel()[parent.source]
            * parent.weight
        )
    )
    assert delivered == pytest.approx(original_delivered)


def test_contact_uses_original_terrain_adjacency_and_component_base(config, contact):
    with pytest.raises(ValueError, match="single-state solver"):
        resolve_components(
            audit.make_request(("grass", "building")),
            config,
            contact=replace(contact, receiver_height_overrides_m=((5, 0.2),)),
        )
    request = audit.make_request(("grass", "bare_or_road", "tree_equivalent"))
    resolved = resolve_components(request, config, contact=contact)
    graph = resolved._contact.neighbors
    assert np.all(graph[0] == -1)  # Artificial node storage never bridges the road.
    high = int(np.flatnonzero(resolved.component_id == "upper_fuel")[0])
    low = int(np.flatnonzero(resolved.component_id == "surface_dead")[0])
    assert low not in graph[high]  # High source starts at 2m, cannot bathe ground.
    assert high not in graph[low]
    terrain = replace(
        audit.make_request(("grass", "tree_equivalent")),
        ground_elevation_m=np.asarray([[0.0, 1.0]]),
    )
    assert np.all(resolve_components(terrain, config, contact=contact)._contact.neighbors[0] == -1)


def test_source_energy_cap_is_shared_by_horizontal_and_vertical_targets(config, contact):
    parts = config.recipes["tree_equivalent"]
    joined = replace(
        config,
        recipes={
            "tree_equivalent": (
                replace(parts[0], flame_extent_m=3),
                parts[1],
            )
        },
    )
    resolved = resolve_components(
        audit.make_request(("grass", "tree_equivalent", "grass")),
        joined,
        contact=replace(contact, heat_transfer_coefficient_W_m2_K=1e8),
    )
    physics = resolved._node_request.parameters
    hrr = np.zeros(physics.peak_hrr_W.shape)
    source = int(np.flatnonzero(resolved.component_id == "surface_dead")[0])
    hrr[0, source] = physics.peak_hrr_W[0, source]
    susceptible = np.ones(hrr.shape, dtype=bool)
    susceptible[0, source] = False
    transfer = resolved._contact.transfer(hrr, np.full(hrr.shape, 300.0), susceptible)
    available = hrr[0, source] * (1 - physics.radiative_fraction[0, source])
    assert transfer.transferred_W == pytest.approx(available)
    assert np.count_nonzero(transfer.target_power_W) == 3
    assert transfer.energy_limited_sources == 1
    assert np.max(transfer.target_contact_fraction) <= 1


def test_base_ignition_does_not_ignite_upper_building_or_consume_its_fuel(config, contact):
    resolved = resolve_components(
        audit.make_request(("grass", "building"), end_time_s=1200), config, contact=contact
    )
    result = run_component_simulation(resolved)
    summary = component_summary(resolved, result)
    assert summary["building/combustible_base"]["ignited"] == 1
    assert summary["building/upper_combustible"]["ignited"] == 0
    upper = resolved.component_id == "upper_combustible"
    assert np.array_equal(
        result.final_state.fuel_remaining_kg.ravel()[upper], result.initial_fuel_kg.ravel()[upper]
    )
    building = resolved.cell_id == 1
    consumed = (result.initial_fuel_kg - result.final_state.fuel_remaining_kg).ravel()
    assert consumed[building].sum() == pytest.approx(
        result.initial_fuel_kg.ravel()[building].sum() * 0.05
    )
    assert result.energy_budget.released_J == pytest.approx(
        result.consumed_fuel_kg * 16260000 + (18000000 - 16260000) * 2.28
    )
    assert result.energy_budget.non_radiative_released_J == pytest.approx(
        result.energy_budget.convective_transferred_J
        + result.energy_budget.nonradiative_untransferred_J
    )


def test_direct_ignition_targets_only_lowest_component(config, contact):
    resolved = resolve_components(
        audit.make_request(("tree_equivalent",), end_time_s=0.1), config, contact=contact
    )
    result = run_component_simulation(resolved, retain_snapshots=True)
    assert np.array_equal(np.isfinite(result.final_state.ignition_time_s), [[True, False]])
    assert result.ignition_cause[0, 0] == 1
    assert result.ignition_cause[0, 1] == 0
    assert result.snapshots[0].time_s == 0
    assert result.snapshots[-1].time_s == 0.1


def test_three_layer_path_requires_sequential_heating(config, contact):
    parts = config.recipes["tree_equivalent"]
    ladder = replace(
        config,
        recipes={
            "tree_equivalent": (
                replace(parts[0], dead_fraction=0.2, area_fraction=0.2),
                FuelComponent("ladder_fuel", 0.1, 0.2, 0.2, 1, 0.2, 3, 0.3),
                replace(parts[1], live_fraction=0.9, dead_fraction=0.6, area_fraction=0.6),
            )
        },
    )
    resolved = resolve_components(
        audit.make_request(("grass", "tree_equivalent")), ladder, contact=contact
    )
    result = run_component_simulation(resolved)
    times = result.final_state.ignition_time_s.ravel()
    assert np.all(np.isfinite(times))
    assert 0 == times[0] < times[1] < times[2] < times[3]
    assert np.all(result.ignition_cause.ravel()[1:] == 2)


def test_no_recipe_applied_retains_legacy_solution(config, contact):
    request = audit.make_request(("grass",) * 3, end_time_s=300)
    resolved = resolve_components(request, config, contact=contact)
    components = run_component_simulation(resolved)
    legacy = run_v3_simulation(replace(request, flame_contact=contact), retain_snapshots=False)
    assert np.array_equal(
        components.final_state.ignition_time_s, legacy.final_state.ignition_time_s
    )
    assert np.array_equal(
        components.final_state.fuel_remaining_kg, legacy.final_state.fuel_remaining_kg
    )
    assert components.energy_budget == legacy.energy_budget


def test_wet_controls_and_timestep_classifications_remain_stable(config, contact):
    results = []
    request = audit.make_request(("grass", "tree_equivalent", "tree_equivalent"), end_time_s=600)
    for step in (0.5, 0.25):
        resolved = resolve_components(
            replace(request, scenario=replace(request.scenario, max_dt_s=step)),
            config,
            contact=contact,
        )
        results.append(run_component_simulation(resolved).final_state.ignition_time_s)
    assert np.array_equal(np.isfinite(results[0]), np.isfinite(results[1]))
    valid = np.isfinite(results[0])
    assert np.max(np.abs(results[0][valid] - results[1][valid])) <= 0.5
    wet = resolve_components(
        audit.make_request(("grass", "tree_equivalent"), wet=True, end_time_s=600),
        config,
        contact=contact,
    )
    assert np.isfinite(run_component_simulation(wet).final_state.ignition_time_s).sum() == 1


def test_output_preserves_components_real_grid_and_cli_reproduction(config, contact, tmp_path):
    from pyfireca.cli import main

    request = audit.make_request(("grass", "building"), end_time_s=100)
    resolved = resolve_components(request, config, contact=contact)
    result = run_component_simulation(resolved)
    output = write_component_run(resolved, result, tmp_path / "first")
    with np.load(output / "physical_cells.final.npz", allow_pickle=False) as arrays:
        assert arrays["any_component_ignited"].shape == request.grid.shape
        assert arrays["consumed_fuel_fraction"][0, 1] < 0.05
    with np.load(output / "components.final.npz", allow_pickle=False) as arrays:
        assert len(arrays["cell_id"]) == 3
        assert arrays["component_id"][1] == "combustible_base"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["physical_grid"]["width"] == 2
    assert manifest["resolved_direct_ignitions"][0]["col"] == 0
    base = output / "base-case"
    assert load_v3_case_bundle(base).request.flame_contact == contact
    assert (
        main(
            [
                "village",
                "run-components-v3",
                str(base),
                "--components-config",
                str(output / "components.resolved.yml"),
                "--output",
                str(tmp_path / "second"),
            ]
        )
        == 0
    )
    with np.load(tmp_path / "second/components.final.npz", allow_pickle=False) as arrays:
        assert np.array_equal(
            arrays["ignition_time_s"], result.final_state.ignition_time_s.ravel(), equal_nan=True
        )
    assert load_v3_case_bundle(base).request.grid == request.grid
    with pytest.raises(FileExistsError):
        write_component_run(resolved, result, output)


def test_unknown_cells_stay_excluded_and_unsupported_indices_do_not_select_recipes(config):
    request = audit.make_request(("grass", "tree_equivalent"))
    unknown = replace(
        request,
        known_mask=np.asarray([[True, False]]),
        burnable_mask=np.asarray([[True, False]]),
        source_mask=np.asarray([[True, False]]),
        target_mask=np.asarray([[True, False]]),
        piloted_ignition_mask=np.asarray([[True, False]]),
        exposure_edges=type(request.exposure_edges)(
            np.asarray([], dtype=np.int32), np.asarray([], dtype=np.int32), np.asarray([]), 2
        ),
        unknown_policy="exclude",
    )
    resolved = resolve_components(unknown, config)
    result = run_component_simulation(resolved)
    assert resolved.component_id[1] == "whole"
    assert result.initial_fuel_kg[0, 1] == 0
    assert not np.isfinite(result.final_state.ignition_time_s[0, 1])
    with pytest.raises(ValueError, match="landcover index"):
        resolve_components(replace(request, landcover_index=np.asarray([[1, 6]])), config)


def test_writer_rejects_result_from_other_node_domain(config, tmp_path):
    resolved = resolve_components(audit.make_request(("grass", "building")), config)
    other = run_v3_simulation(audit.make_request(("grass",)), retain_snapshots=False)
    output = tmp_path / "wrong"
    with pytest.raises(ValueError, match="does not match"):
        write_component_run(resolved, other, output)
    assert not output.exists()


def test_component_mode_rejects_single_state_receiver_band(config, contact):
    request = audit.make_request(("grass", "grass"))
    with pytest.raises(ValueError, match="finite receiver bands"):
        resolve_components(
            request, config, contact=replace(contact, receiver_vertical_half_extent_m=0.15)
        )
