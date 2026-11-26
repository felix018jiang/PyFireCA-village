"""Exact boundary support must distinguish interior tangency from outside fuel."""

import importlib.util
from fractions import Fraction
from pathlib import Path

import pytest
from shapely.geometry import box


@pytest.fixture
def audit(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "thin_events_test", scripts / "audit_heritage_thin_width_events.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ccw(audit, geometry):
    vertices = audit.rational_ring(geometry.exterior)
    if audit.signed_area(vertices) < 0:
        vertices.reverse()
    return vertices


def test_inside_tangent_has_no_outside_area_but_closed_boundary_distance_zero(audit):
    hull = ccw(audit, box(0, 0, 10, 10))
    tangent = box(0, 2, 5, 4)
    assert audit.outside_area_exact(tangent, hull) == 0
    assert audit.boundary_distance_squared_exact(tangent, hull) == 0
    inside = box(1, 2, 3, 4)
    assert audit.outside_area_exact(inside, hull) == 0
    assert audit.boundary_distance_squared_exact(inside, hull) == 1
    crossing = box(-1, 2, 1, 4)
    assert audit.outside_area_exact(crossing, hull) == 2
    assert audit.boundary_distance_squared_exact(crossing, hull) == 0


def test_exact_positive_separation_survives_large_projected_coordinates(audit):
    hull = ccw(audit, box(347000, 2869000, 347010, 2869010))
    exterior = box(347010.125, 2869002, 347012, 2869004)
    distance = audit.boundary_distance_squared_exact(exterior, hull)
    assert distance == Fraction(1, 64)
    result = audit.exact_distance_record(distance)
    low, high = map(Fraction, result["distance_decimal_enclosure_m"])
    assert low <= Fraction(1, 8) <= high


def test_positive_area_tolerance_has_positive_onset_not_any_epsilon(audit):
    transition = audit.numerical_bracket(lambda w: 5 * w > 1e-8, 0.01, tolerance=1e-13)
    assert transition["false_width_m"] <= 2e-9 < transition["true_width_m"]
    assert not audit.segments_touch((0, 0), (1, 0), (2, 0), (3, 0))
