"""Finite-area radiation: analytic factors, geometry, budgets and thermal effects."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.component_radiation_v3 import (
    InternalRadiationParameters,
    _cylinder,
    _diffuse_view_factor,
    _horizontal_patch,
    load_internal_radiation_parameters,
)
from pyfireca.village.components_v3 import (
    FuelComponent,
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location(
    "internal_radiation_fixture", DIRECTORY / "audit_spread.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def parameters():
    return load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")


@pytest.fixture
def config():
    return load_component_configuration(DIRECTORY / "components.synthetic.yml")


def dry_request():
    request = audit.make_request(("tree_equivalent",), max_dt_s=0.25)
    physics = request.parameters
    return replace(
        request,
        parameters=replace(
            physics,
            live_water_mass_kg=physics.live_dry_fuel_mass_kg * 0.01,
            dead_water_mass_kg=physics.dead_dry_fuel_mass_kg * 0.01,
            water_mass_kg=physics.initial_dry_fuel_mass_kg * 0.01,
            moisture_content_dry_basis=np.full(request.grid.shape, 0.01),
        ),
    )


def stress_config(config, height=0.6):
    parts = config.recipes["tree_equivalent"]
    return replace(
        config,
        recipes={
            "tree_equivalent": (
                replace(parts[0], dead_fraction=0.95),
                replace(
                    parts[1],
                    dead_fraction=0.05,
                    source_base_height_m=height,
                    receiver_height_m=height,
                ),
            )
        },
    )


def test_parallel_unit_squares_agree_with_analytic_view_factor_and_reciprocity():
    source = _horizontal_patch(1, 0, 12)
    p, normal, area = _horizontal_patch(1, 1, 12)
    target = p, -normal, area
    # Equal coaxial squares: NISTIR6925 double-area definition, closed form at L/H=1.
    x = 1.0
    expected = (
        2
        / np.pi
        * (
            np.log(np.sqrt((1 + x * x) ** 2 / (1 + 2 * x * x)))
            + 2 * x * np.sqrt(1 + x * x) * np.arctan(x / np.sqrt(1 + x * x))
            - 2 * x * np.arctan(x)
        )
    )
    assert expected == pytest.approx(0.19982489569838746)
    forward = _diffuse_view_factor(source, target, target_two_sided=False)
    reverse = _diffuse_view_factor(target, source, target_two_sided=False)
    assert forward == pytest.approx(expected, abs=1e-10)
    assert forward * source[2].sum() == pytest.approx(reverse * target[2].sum(), abs=1e-12)
    assert _diffuse_view_factor(source, (p, normal, area), target_two_sided=False) == 0
    assert _diffuse_view_factor(source, (p, normal, area)) == pytest.approx(forward)


def test_cylinder_surface_area_normals_and_vertical_symmetry():
    radius, length = 0.5, 0.35
    source = _cylinder(radius, 0, length, 12)
    assert source[2].sum() == pytest.approx(2 * np.pi * radius * (length + radius))
    assert np.allclose(np.linalg.norm(source[1], axis=1), 1)
    above = _diffuse_view_factor(source, _horizontal_patch(3, 2.5, 12))
    below = _diffuse_view_factor(source, _horizontal_patch(3, length - 2.5, 12))
    assert 0 < above < 0.5
    assert above == pytest.approx(below, abs=1e-12)


@pytest.mark.parametrize(
    "patch",
    [
        {"source_radius_fraction_cell": 0},
        {"source_radius_fraction_cell": 0.6},
        {"atmospheric_extinction_m_inv": -1},
        {"quadrature_order": True},
        {"max_quadrature_order": 10},
        {"relative_tolerance": 0},
        {"absolute_tolerance": float("nan")},
        {"max_kernel_evaluations": 0},
    ],
)
def test_invalid_geometry_and_numerical_parameters_are_rejected(parameters, patch):
    with pytest.raises((ValueError, TypeError)):
        replace(parameters, **patch)


def test_configuration_round_trip_is_strict(parameters):
    assert InternalRadiationParameters.from_mapping(parameters.to_mapping()) == parameters
    with pytest.raises(ValueError):
        InternalRadiationParameters.from_mapping({**parameters.to_mapping(), "unknown": 1})
    with pytest.raises(ValueError):
        InternalRadiationParameters.from_mapping(
            {**parameters.to_mapping(), "schema_version": "old"}
        )


def test_internal_graph_matches_finer_quadrature_and_height_distance(config, parameters):
    request = audit.make_request(("tree_equivalent",))
    coarse = resolve_components(
        request, config, internal_radiation=parameters
    ).internal_radiation_graph
    fine_parameters = replace(
        parameters, quadrature_order=12, max_quadrature_order=24, relative_tolerance=0.001
    )
    fine = resolve_components(
        request, config, internal_radiation=fine_parameters
    ).internal_radiation_graph
    assert np.allclose(coarse.edges.weight, fine.edges.weight, rtol=0.01, atol=1e-5)
    assert np.array_equal(coarse.edges.source, [0, 1])
    assert np.array_equal(coarse.edges.target, [1, 0])
    close = resolve_components(
        request, stress_config(config), internal_radiation=parameters
    ).internal_radiation_graph
    assert close.edges.weight[0] > coarse.edges.weight[0]
    terrain = replace(request, ground_elevation_m=np.full(request.grid.shape, 1234.5))
    same = resolve_components(
        terrain, config, internal_radiation=parameters
    ).internal_radiation_graph
    assert np.array_equal(coarse.edges.weight, same.edges.weight)


def test_atmospheric_extinction_decreases_transfer(config, parameters):
    request = audit.make_request(("tree_equivalent",))
    clear = resolve_components(
        request, config, internal_radiation=parameters
    ).internal_radiation_graph
    attenuated = resolve_components(
        request, config, internal_radiation=replace(parameters, atmospheric_extinction_m_inv=0.2)
    ).internal_radiation_graph
    assert np.all(attenuated.edges.weight < clear.edges.weight)
    assert np.all(attenuated.edges.weight > 0)


def test_intersecting_geometry_and_unresolved_third_layer_are_not_regularized(config, parameters):
    request = audit.make_request(("tree_equivalent",))
    with pytest.raises(ValueError, match="GEOMETRY_OVERLAP"):
        resolve_components(
            request, stress_config(config, height=0.3), internal_radiation=parameters
        )
    parts = config.recipes["tree_equivalent"]
    ladder = replace(
        config,
        recipes={
            "tree_equivalent": (
                replace(parts[0], dead_fraction=0.2, area_fraction=0.2),
                FuelComponent("ladder", 0.1, 0.2, 0.2, 1, 0.2, 3, 0.3),
                replace(parts[1], live_fraction=0.9, dead_fraction=0.6, area_fraction=0.6),
            )
        },
    )
    with pytest.raises(ValueError, match="intervening-surface"):
        resolve_components(request, ladder, internal_radiation=parameters)


def test_quadrature_nonconvergence_and_resource_limits_fail_explicitly(config, parameters):
    request = audit.make_request(("tree_equivalent",))
    strict = replace(
        parameters, max_quadrature_order=6, relative_tolerance=1e-12, absolute_tolerance=1e-15
    )
    with pytest.raises(ValueError, match="NOT_CONVERGED"):
        resolve_components(request, config, internal_radiation=strict)
    with pytest.raises(ValueError, match="no quadrature executed"):
        resolve_components(
            request, config, internal_radiation=replace(parameters, max_kernel_evaluations=1)
        )
    # Both internal directions count against the existing graph edge budget.
    with pytest.raises(ValueError, match="edge limit"):
        resolve_components(request, config, internal_radiation=parameters, max_radiation_edges=1)


def test_combined_radiation_cannot_create_extra_source_energy(config, parameters):
    request = audit.make_request(("grass", "building"))
    request = replace(
        request,
        exposure_edges=ExposureEdges(
            np.asarray([1], dtype=np.int32), np.asarray([0], dtype=np.int32), np.asarray([1.0]), 2
        ),
    )
    resolve_components(request, config)  # Original frozen graph has a valid budget.
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        resolve_components(request, config, internal_radiation=parameters)


def test_single_component_graph_is_empty_and_unconfigured_results_stay_unchanged(
    config, parameters
):
    request = audit.make_request(("grass", "grass"), end_time_s=30)
    original = resolve_components(request, config)
    internal = resolve_components(request, config, internal_radiation=parameters)
    assert len(internal.internal_radiation_graph.edges.source) == 0
    first, second = run_component_simulation(original), run_component_simulation(internal)
    assert np.array_equal(
        first.final_state.surface_temperature_K, second.final_state.surface_temperature_K
    )
    assert first.energy_budget == second.energy_budget


def test_internal_radiation_can_preheat_without_ignition(config, parameters):
    request = audit.make_request(("tree_equivalent",), end_time_s=240)
    original = run_component_simulation(resolve_components(request, config))
    resolved = resolve_components(request, config, internal_radiation=parameters)
    result = run_component_simulation(resolved)
    assert (
        result.final_state.surface_temperature_K[0, 1]
        > original.final_state.surface_temperature_K[0, 1]
    )
    assert result.final_state.incident_radiation_J[0, 1] > 0
    assert not np.isfinite(result.final_state.ignition_time_s[0, 1])
    assert np.all(result.final_state.received_convection_J == 0)
    assert np.array_equal(result.initial_fuel_kg, original.initial_fuel_kg)
    assert result.energy_budget.radiative_emitted_J == pytest.approx(
        result.energy_budget.radiative_intercepted_J
        + result.energy_budget.radiative_unintercepted_J
    )


def test_positive_noncontact_stress_case_keeps_threshold_and_time_stability(config, parameters):
    request = dry_request()
    cfg = stress_config(config)
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    resolved = resolve_components(request, cfg, contact=contact, internal_radiation=parameters)
    assert np.all(resolved._contact.neighbors == -1)  # Vertical gap exceeds flame contact.
    results = []
    for dt in (0.5, 0.25):
        req = replace(request, scenario=replace(request.scenario, max_dt_s=dt))
        results.append(
            run_component_simulation(resolve_components(req, cfg, internal_radiation=parameters))
        )
    assert np.isfinite(results[0].final_state.ignition_time_s).sum() == 2
    assert results[0].ignition_cause[0, 1] == 2
    assert results[0].final_state.ignition_time_s[0, 1] > 0
    assert (
        abs(
            results[0].final_state.ignition_time_s[0, 1]
            - results[1].final_state.ignition_time_s[0, 1]
        )
        <= 0.5
    )
    assert resolved._node_request.parameters.ignition_temperature_K[0, 1] == 540
    assert np.all(results[0].final_state.received_convection_J == 0)
    assert (
        np.isfinite(
            run_component_simulation(resolve_components(request, cfg)).final_state.ignition_time_s
        ).sum()
        == 1
    )


def test_saved_configuration_and_graph_reproduce_cli_run(config, parameters, tmp_path):
    from pyfireca.cli import main

    resolved = resolve_components(
        dry_request(), stress_config(config), internal_radiation=parameters
    )
    result = run_component_simulation(resolved)
    first = write_component_run(resolved, result, tmp_path / "first")
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["internal_radiation"] == parameters.to_mapping()
    assert manifest["internal_radiation_diagnostics"]["all_internal_integrals_converged"]
    with np.load(first / "internal_radiation.graph.npz", allow_pickle=False) as graph:
        assert np.array_equal(graph["weight"], resolved.internal_radiation_graph.edges.weight)
        # This one-cell case has no frozen inter-cell edges: the independently
        # reconstructed internal incident energy must match the solver ledger.
        assert np.allclose(
            graph["internal_incident_radiation_J"],
            result.final_state.incident_radiation_J.ravel(),
            rtol=1e-10,
            atol=1e-6,
        )
    second = tmp_path / "second"
    assert (
        main(
            [
                "village",
                "run-components-v3",
                str(first / "base-case"),
                "--components-config",
                str(first / "components.resolved.yml"),
                "--internal-radiation-config",
                str(first / "internal-radiation.resolved.yml"),
                "--radiation-only",
                "--output",
                str(second),
            ]
        )
        == 0
    )
    with np.load(second / "components.final.npz", allow_pickle=False) as arrays:
        assert np.array_equal(
            arrays["ignition_time_s"], result.final_state.ignition_time_s.ravel(), equal_nan=True
        )
