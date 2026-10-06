"""Full graph replacement, direction, blockers, resources and physical energy closure."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.cli import main
from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
from pyfireca.village.component_surfaces_v3 import (
    ReceiverSurfaceConfiguration,
    load_receiver_surface_configuration,
)
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
)
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.finite_radiation_v3 import (
    FiniteRadiationConfiguration,
    load_finite_radiation_configuration,
)
from pyfireca.village.workflow_v3 import write_v3_case_bundle

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("finite_fixture", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def configs():
    return (
        load_component_configuration(DIRECTORY / "components.synthetic.yml"),
        load_finite_radiation_configuration(DIRECTORY / "finite-radiation.synthetic.yml"),
        load_receiver_surface_configuration(DIRECTORY / "receiver-surfaces.synthetic.yml"),
    )


def edge(graph, source, target):
    return float(
        graph.edges.weight[(graph.edges.source == source) & (graph.edges.target == target)][0]
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"max_center_distance_m": 0},
        {"max_center_distance_m": True},
        {"receiver_visibility": 1},
        {"terrain_visibility": 0},
        {"max_visibility_tests": 0},
        {"evidence": "assumed"},
        {"provenance": ""},
    ],
)
def test_invalid_finite_graph_configuration(fields):
    _, config, _ = configs()
    with pytest.raises((ValueError, TypeError)):
        replace(config, **fields)


def test_schema_round_trip_and_unknown_keys_reject():
    _, config, _ = configs()
    assert FiniteRadiationConfiguration.from_mapping(config.to_mapping()) == config
    with pytest.raises(ValueError, match="exact schema"):
        FiniteRadiationConfiguration.from_mapping({**config.to_mapping(), "extra": 1})


def test_full_graph_replaces_inherited_weights_and_cannot_mix_old_geometry():
    components, config, surfaces = configs()
    request = audit.make_request(("grass", "building"))
    empty = ExposureEdges(np.array([], dtype=int), np.array([], dtype=int), np.array([]), 2)
    first = resolve_components(request, components, finite_radiation=config, surfaces=surfaces)
    second = resolve_components(
        replace(request, exposure_edges=empty),
        components,
        finite_radiation=config,
        surfaces=surfaces,
    )
    assert np.array_equal(
        first.finite_radiation_graph.edges.weight, second.finite_radiation_graph.edges.weight
    )
    assert first.internal_radiation_graph is None
    assert first.finite_radiation_graph.summary()["intercell_geometry_rebuilt"]
    with pytest.raises(ValueError, match="conflicts"):
        resolve_components(
            request,
            components,
            finite_radiation=config,
            internal_radiation=load_internal_radiation_parameters(
                DIRECTORY / "internal-radiation.synthetic.yml"
            ),
        )


def test_neighbor_wall_front_back_and_terrain_translation():
    components, config, surfaces = configs()
    request = audit.make_request(("grass", "building"))
    first = resolve_components(
        request, components, finite_radiation=config, surfaces=surfaces
    ).finite_radiation_graph
    wall = surfaces.surfaces["building"]["upper_combustible"]
    back = replace(
        surfaces, surfaces={"building": {"upper_combustible": replace(wall, azimuth_deg=0)}}
    )
    second = resolve_components(
        request, components, finite_radiation=config, surfaces=back
    ).finite_radiation_graph
    translated = resolve_components(
        replace(request, ground_elevation_m=np.full(request.grid.shape, 1234.0)),
        components,
        finite_radiation=config,
        surfaces=surfaces,
    ).finite_radiation_graph
    assert edge(first, 0, 2) > 0
    assert edge(second, 0, 2) == 0
    assert np.array_equal(first.edges.weight, translated.edges.weight)


def test_opaque_intermediate_receiver_blocks_neighbor_flux():
    components, config, surfaces = configs()
    parts = components.recipes["building"]
    components = replace(
        components,
        recipes={
            **components.recipes,
            "building": (parts[0], replace(parts[1], receiver_height_m=1.1)),
        },
    )
    wall = replace(surfaces.surfaces["building"]["upper_combustible"], aspect_ratio=3.6 / 2.2**2)
    surfaces = ReceiverSurfaceConfiguration(
        "synthetic", "test:full_wall", {"building": {"upper_combustible": wall}}
    )
    request = audit.make_request(("grass", "building", "building"))
    blocked = resolve_components(
        request, components, finite_radiation=config, surfaces=surfaces
    ).finite_radiation_graph
    clear = resolve_components(
        request,
        components,
        finite_radiation=replace(config, receiver_visibility=False),
        surfaces=surfaces,
    ).finite_radiation_graph
    assert edge(clear, 0, 4) > 0
    assert edge(blocked, 0, 4) == 0


def test_terrain_step_and_unknown_cell_block_without_changing_endpoint_geometry():
    components, config, surfaces = configs()
    request = audit.make_request(("grass", "bare_or_road", "building"))
    hill = replace(request, ground_elevation_m=np.array([[0.0, 3.0, 0.0]]))
    blocked = resolve_components(
        hill, components, finite_radiation=config, surfaces=surfaces
    ).finite_radiation_graph
    clear = resolve_components(
        hill,
        components,
        finite_radiation=replace(config, terrain_visibility=False),
        surfaces=surfaces,
    ).finite_radiation_graph
    assert edge(clear, 0, 3) > 0
    assert edge(blocked, 0, 3) == 0
    known = request.known_mask.copy()
    known[0, 1] = False
    target = request.target_mask.copy()
    target[0, 1] = False
    old_edges = request.exposure_edges
    keep = target.ravel()[old_edges.target]
    filtered_edges = ExposureEdges(
        old_edges.source[keep], old_edges.target[keep], old_edges.weight[keep], 3
    )
    excluded = resolve_components(
        replace(
            request,
            known_mask=known,
            target_mask=target,
            exposure_edges=filtered_edges,
            unknown_policy="exclude",
        ),
        components,
        finite_radiation=config,
        surfaces=surfaces,
    ).finite_radiation_graph
    assert edge(excluded, 0, 3) == 0


def test_cutoff_and_resource_budgets_fail_before_quadrature():
    components, config, surfaces = configs()
    request = audit.make_request(("grass", "building"))
    small = resolve_components(
        request,
        components,
        finite_radiation=replace(config, max_center_distance_m=1),
        surfaces=surfaces,
    ).finite_radiation_graph
    assert np.array_equal(small.edges.source, [1, 2])
    assert np.array_equal(small.edges.target, [2, 1])
    for kwargs in (
        {
            "finite_radiation": replace(
                config, integration=replace(config.integration, max_kernel_evaluations=1)
            )
        },
        {"finite_radiation": replace(config, max_visibility_tests=1)},
        {"finite_radiation": config, "max_radiation_edges": 1},
    ):
        with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
            resolve_components(request, components, surfaces=surfaces, **kwargs)


def test_nonconvergence_is_retained_instead_of_relaxing_tolerances():
    components, config, surfaces = configs()
    coarse = replace(
        config,
        integration=replace(
            config.integration,
            max_quadrature_order=6,
            relative_tolerance=1e-12,
            absolute_tolerance=1e-15,
        ),
    )
    with pytest.raises(ValueError, match="NOT_CONVERGED"):
        resolve_components(
            audit.make_request(("shrub", "building")),
            components,
            finite_radiation=coarse,
            surfaces=surfaces,
        )
    fine = resolve_components(
        audit.make_request(("shrub", "building")),
        components,
        finite_radiation=config,
        surfaces=surfaces,
    )
    assert fine.finite_radiation_graph.summary()["all_integrals_converged"]
    graph = fine.finite_radiation_graph
    assert graph.evaluated_kernel_pairs <= graph.kernel_upper_bound
    assert graph.evaluated_visibility_tests <= graph.visibility_upper_bound


def test_complete_solver_preserves_fuel_and_radiation_budget_at_two_steps():
    components, config, surfaces = configs()
    request = audit.make_request(("grass", "building"))
    results = []
    for dt in (0.5, 0.25):
        resolved = resolve_components(
            replace(request, scenario=replace(request.scenario, max_dt_s=dt)),
            components,
            finite_radiation=config,
            surfaces=surfaces,
        )
        result = run_component_simulation(resolved)
        p = resolved._node_request.parameters
        radiative = float((result.final_state.released_energy_J * p.radiative_fraction).sum())
        budget = result.energy_budget
        assert budget.radiative_intercepted_J + budget.radiative_unintercepted_J == pytest.approx(
            radiative, abs=1e-5
        )
        assert result.consumed_fuel_kg <= request.parameters.pyrolysable_dry_fuel_mass_kg.sum()
        results.append(result.final_state.ignition_time_s)
    assert np.array_equal(np.isfinite(results[0]), np.isfinite(results[1]))


def test_cli_saves_complete_geometry_and_replay_is_exact(tmp_path):
    components, _, surfaces = configs()
    request = audit.make_request(("grass", "building"))
    case = write_v3_case_bundle(request, tmp_path / "case")
    output = tmp_path / "run"
    args = [
        "village",
        "run-components-v3",
        str(case),
        "--output",
        str(output),
        "--components-config",
        str(DIRECTORY / "components.synthetic.yml"),
        "--finite-radiation-config",
        str(DIRECTORY / "finite-radiation.synthetic.yml"),
        "--receiver-surfaces-config",
        str(DIRECTORY / "receiver-surfaces.synthetic.yml"),
        "--radiation-only",
    ]
    assert main(args) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["receiver_geometry_affects"] == "all_component_radiation"
    assert (
        manifest["resolved_radiation_policy"] == "complete_finite_surfaces_with_explicit_visibility"
    )
    assert manifest["finite_radiation"]["evidence"] == "synthetic"
    assert not (output / "internal_radiation.graph.npz").exists()
    stored = np.load(output / "finite_radiation.graph.npz")
    replay = resolve_components(
        request,
        components,
        finite_radiation=load_finite_radiation_configuration(
            output / "finite-radiation.resolved.yml"
        ),
        surfaces=surfaces,
    )
    assert np.array_equal(stored["weight"], replay.finite_radiation_graph.edges.weight)
