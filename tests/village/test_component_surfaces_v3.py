"""Independent geometry identities and end-to-end receiver orientation effects."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.cli import main
from pyfireca.village.component_radiation_v3 import (
    _diffuse_view_factor,
    load_internal_radiation_parameters,
)
from pyfireca.village.component_surfaces_v3 import (
    DEFAULT_RECEIVER_SURFACE,
    ReceiverSurface,
    ReceiverSurfaceConfiguration,
    load_receiver_surface_configuration,
)
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.workflow_v3 import write_v3_case_bundle

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("surface_fixture", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def configs():
    return (
        load_component_configuration(DIRECTORY / "components.synthetic.yml"),
        load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml"),
        load_receiver_surface_configuration(DIRECTORY / "receiver-surfaces.synthetic.yml"),
    )


def test_vertical_parallel_squares_match_analytic_factor_and_reciprocity():
    a = ReceiverSurface("vertical", 0, 0, 0, 1, False).quadrature(1, 1, 12)
    b = ReceiverSurface("vertical", 180, 1, 0, 1, False).quadrature(1, 1, 12)
    expected = 0.19982489569838746  # independent equal-square analytic reference
    assert _diffuse_view_factor(a, b, target_two_sided=False) == pytest.approx(expected, abs=1e-10)
    assert _diffuse_view_factor(b, a, target_two_sided=False) == pytest.approx(expected, abs=1e-10)
    back = ReceiverSurface("vertical", 0, 1, 0, 1, False).quadrature(1, 1, 12)
    assert _diffuse_view_factor(a, back, target_two_sided=False) == 0
    assert _diffuse_view_factor(a, back) == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_rectangle_area_center_normals_and_dimensions(orientation):
    surface = ReceiverSurface(orientation, 37, 0.2, -0.3, 4, True)
    center, _, _, normal, width, height = surface.geometry(0.5, 2)
    points, normals, areas = surface.quadrature(0.5, 2, 6)
    assert areas.sum() == pytest.approx(0.5)
    assert width / height == pytest.approx(4)
    assert width * height == pytest.approx(0.5)
    assert np.average(points, axis=0, weights=areas) == pytest.approx(center)
    assert np.allclose(normals, normal)
    assert np.linalg.norm(normal) == pytest.approx(1)


@pytest.mark.parametrize(
    "fields",
    [
        {"orientation": "slanted"},
        {"azimuth_deg": 360},
        {"azimuth_deg": True},
        {"offset_x_m": float("nan")},
        {"aspect_ratio": 0},
        {"two_sided": 1},
    ],
)
def test_invalid_surface_parameters_fail(fields):
    with pytest.raises((ValueError, TypeError)):
        ReceiverSurface(**fields)


def test_continuous_overlap_checks_edges_when_center_is_clear():
    # Vertical center at z=2 is above a 1.2 m flame, but its lower edge is not.
    wall = ReceiverSurface("vertical", 180, 0.1, 0, 1, False)
    assert wall.intersects_cylinder(4, 2, 0.2, 0, 1.2)
    assert not replace(wall, offset_x_m=0.3).intersects_cylinder(4, 2, 0.2, 0, 1.2)
    # A horizontal corner reaches the cylinder even though its center does not.
    patch = ReceiverSurface("horizontal", 45, 0.8, 0, 1, True)
    assert patch.intersects_cylinder(1, 0.5, 0.2, 0, 1)
    assert not patch.intersects_cylinder(1, 1.5, 0.2, 0, 1)


def test_bounds_check_full_rectangle_not_just_center():
    with pytest.raises(ValueError, match="outside"):
        ReceiverSurface(offset_x_m=0.6).validate_bounds(1, 2, 2)
    with pytest.raises(ValueError, match="below"):
        ReceiverSurface("vertical").validate_bounds(1, 0.2, 2)


def test_surface_schema_round_trip_and_unknown_component_is_not_ignored():
    components, radiation, surfaces = configs()
    assert ReceiverSurfaceConfiguration.from_mapping(surfaces.to_mapping()) == surfaces
    with pytest.raises(ValueError, match="exact schema"):
        ReceiverSurfaceConfiguration.from_mapping({**surfaces.to_mapping(), "extra": 1})
    broken = replace(surfaces, surfaces={"building": {"misspelled": ReceiverSurface()}})
    # Building absent: invalid configuration is still rejected.
    with pytest.raises(ValueError, match="unknown receiver"):
        resolve_components(
            audit.make_request(("grass",)),
            components,
            internal_radiation=radiation,
            surfaces=broken,
        )
    with pytest.raises(ValueError, match="require internal radiation"):
        resolve_components(audit.make_request(("building",)), components, surfaces=surfaces)


def test_default_surface_preserves_existing_graph_and_solver_arrays():
    components, radiation, _ = configs()
    request = audit.make_request(("building",))
    default = ReceiverSurfaceConfiguration(
        "synthetic", "test:unchanged", {"building": {"upper_combustible": DEFAULT_RECEIVER_SURFACE}}
    )
    old = resolve_components(request, components, internal_radiation=radiation)
    new = resolve_components(request, components, internal_radiation=radiation, surfaces=default)
    assert np.array_equal(
        old.internal_radiation_graph.edges.weight, new.internal_radiation_graph.edges.weight
    )
    a, b = run_component_simulation(old), run_component_simulation(new)
    for key in a.final_state.__dataclass_fields__:
        assert np.array_equal(
            getattr(a.final_state, key), getattr(b.final_state, key), equal_nan=True
        )


def test_front_back_rotation_fine_quadrature_and_source_budget():
    components, radiation, surfaces = configs()
    request = audit.make_request(("building",))
    wall = surfaces.surfaces["building"]["upper_combustible"]

    def graph(surface, parameters=radiation):
        cfg = replace(surfaces, surfaces={"building": {"upper_combustible": surface}})
        return resolve_components(
            request, components, internal_radiation=parameters, surfaces=cfg
        ).internal_radiation_graph

    front = graph(wall)
    back = graph(replace(wall, azimuth_deg=0))
    both = graph(replace(wall, azimuth_deg=0, two_sided=True))
    rotated = graph(replace(wall, azimuth_deg=90, offset_x_m=0, offset_y_m=-0.75))
    fine = graph(
        wall,
        replace(radiation, quadrature_order=12, max_quadrature_order=24, relative_tolerance=0.001),
    )
    assert front.edges.weight[0] > 0
    assert back.edges.weight[0] == 0
    assert both.edges.weight[0] == pytest.approx(front.edges.weight[0], abs=1e-12)
    assert rotated.edges.weight[0] == pytest.approx(front.edges.weight[0], abs=1e-12)
    assert front.edges.weight[0] == pytest.approx(fine.edges.weight[0], rel=0.01, abs=1e-5)
    assert np.max(np.bincount(front.edges.source, weights=front.edges.weight)) <= 1


def test_overlap_and_bounds_fail_in_real_component_resolution():
    components, radiation, surfaces = configs()
    request = audit.make_request(("building",))
    wall = surfaces.surfaces["building"]["upper_combustible"]
    for surface, message in (
        (replace(wall, offset_x_m=0.1), "GEOMETRY_OVERLAP"),
        (replace(wall, offset_y_m=0.2), "outside"),
    ):
        cfg = replace(surfaces, surfaces={"building": {"upper_combustible": surface}})
        with pytest.raises(ValueError, match=message):
            resolve_components(request, components, internal_radiation=radiation, surfaces=cfg)


def test_cli_saves_resolved_geometry_and_replays_exact_weights(tmp_path):
    case, output = tmp_path / "case", tmp_path / "out"
    write_v3_case_bundle(audit.make_request(("building",)), case)
    assert (
        main(
            [
                "village",
                "run-components-v3",
                str(case),
                "--output",
                str(output),
                "--components-config",
                str(DIRECTORY / "components.synthetic.yml"),
                "--internal-radiation-config",
                str(DIRECTORY / "internal-radiation.synthetic.yml"),
                "--receiver-surfaces-config",
                str(DIRECTORY / "receiver-surfaces.synthetic.yml"),
                "--radiation-only",
            ]
        )
        == 0
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["receiver_geometry_affects"] == "same_cell_radiation_only"
    assert manifest["receiver_surfaces"]["evidence"] == "synthetic"
    assert (output / "receiver-surfaces.csv").is_file()
    components, radiation, _ = configs()
    resolved = resolve_components(
        audit.make_request(("building",)),
        components,
        internal_radiation=radiation,
        surfaces=load_receiver_surface_configuration(output / "receiver-surfaces.resolved.yml"),
    )
    graph = np.load(output / "internal_radiation.graph.npz")
    assert np.array_equal(graph["weight"], resolved.internal_radiation_graph.edges.weight)
    write_component_run(resolved, run_component_simulation(resolved), tmp_path / "repeat")
