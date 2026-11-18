"""Independent roof-graph checks for prescribed, spatially uniform horizontal wind."""

import importlib.util
from dataclasses import replace
from math import exp, hypot, pi
from pathlib import Path

import pytest
from shapely.geometry import box

from pyfireca.planning.roof_thermal import resolve_roof_layout, roof_area_integrated_radiation_graph


@pytest.fixture
def three_roofs():
    example = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("wind_fixture", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # One 2 m quadrature tile per roof, with the receiving roofs 4 m north and south.
    roofs = {"S": box(12, 8, 14, 10), "G": box(12, 12, 14, 14), "F": box(12, 4, 14, 6)}
    bundle, transport, inventory, _ = module.make_case(radius=4, geometries=roofs, candidate_ids=())
    return resolve_roof_layout(bundle.request, inventory), transport


@pytest.mark.parametrize(("speed", "from_deg", "down_y"), [(0, 0, -1), (2, 0, -1), (2, 180, 1)])
def test_area_weights_match_tilt_on_north_south_axis(three_roofs, speed, from_deg, down_y):
    layout, base = three_roofs
    k = 0.2
    transport = replace(
        base, wind_speed_m_s=speed, wind_from_deg=from_deg, flame_tilt_coefficient_s_m=k
    )
    edges = roof_area_integrated_radiation_graph(
        layout, transport, integrated_ids=("S", "G", "F"), quadrature_spacing_m=2
    )
    source = layout.nodes("S").item()
    physics = layout.thermal.parameters
    # Unit flame axis is proportional to (0, down_y * k * speed, 1).
    half_height = physics.source_height_m.ravel()[source] / 2
    flame_z = half_height / hypot(1, k * speed)
    shift_y = down_y * k * speed * flame_z
    for receiver, offset_y in (("G", 4), ("F", -4)):
        target = layout.nodes(receiver).item()
        delta_z = physics.receiver_height_m.ravel()[target] - flame_z
        distance = hypot(offset_y - shift_y, delta_z)
        expected = (
            physics.receiver_area_m2.ravel()[target]
            * exp(-transport.atmospheric_extinction_m_inv * distance)
            / (4 * pi * (distance**2 + transport.near_field_regularization_m**2))
        )
        actual = edges.weight[(edges.source == source) & (edges.target == target)].item()
        assert actual == pytest.approx(expected, rel=1e-12)


def test_tilt_keeps_the_declared_unshifted_support(three_roofs):
    layout, transport = three_roofs
    # Both receiver centres are 4 m away. Tilting toward the northern receiver
    # puts the radiation centre closer, but cannot create an edge outside R.
    narrow = replace(
        transport,
        exposure_radius_m=3.9,
        wind_speed_m_s=2,
        wind_from_deg=180,
        flame_tilt_coefficient_s_m=0.2,
    )
    edges = roof_area_integrated_radiation_graph(
        layout, narrow, integrated_ids=("S", "G", "F"), quadrature_spacing_m=2
    )
    assert layout.nodes("S").item() not in edges.source
