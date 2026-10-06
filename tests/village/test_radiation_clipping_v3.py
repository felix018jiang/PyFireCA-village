"""Analytic shadow area, direct-ray references and smooth finite-area integrals."""

import numpy as np
import pytest

from pyfireca.village.component_radiation_v3 import _diffuse_view_factor
from pyfireca.village.component_surfaces_v3 import ReceiverSurface
from pyfireca.village.radiation_clipping_v3 import (
    clipped_view_factor,
    segmented_cylinder,
    source_height_planes,
    source_point_count,
    terrain_box_faces,
    visible_receiver_polygons,
)
from pyfireca.village.radiation_visibility_v3 import visible_between


def area(polygons):
    return sum(
        abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))) / 2
        for p in polygons
    )


def contains(polygons, points):
    result = np.zeros(len(points), dtype=bool)
    for polygon in polygons:
        inside = np.ones(len(points), dtype=bool)
        for a, b in zip(polygon, np.roll(polygon, -1, axis=0), strict=True):
            edge = b - a
            delta = points - a
            inside &= edge[0] * delta[:, 1] - edge[1] * delta[:, 0] >= -1e-12
        result |= inside
    return result


def test_projected_half_shadow_and_overlapping_shadows_do_not_duplicate_area():
    receiver = ReceiverSurface("vertical", 180, 1, 0, 1, False).geometry(1, 1)
    wall = ReceiverSurface("vertical", 0, 0.5, 1, 1, False).geometry(4, 1)
    point, normal = np.array([0, 0, 1]), np.array([1, 0, 0])
    once, _ = visible_receiver_polygons(point, normal, receiver, [wall], 32)
    twice, _ = visible_receiver_polygons(point, normal, receiver, [wall, wall], 32)
    assert area(once) == pytest.approx(0.5)
    assert area(twice) == pytest.approx(0.5)
    source = point[None, :], normal[None, :], np.array([1.0])
    clear, *_ = clipped_view_factor(
        source, receiver, [], order=12, max_triangles=32, target_two_sided=False
    )
    half, *_ = clipped_view_factor(
        source, receiver, [wall], order=12, max_triangles=32, target_two_sided=False
    )
    assert half == pytest.approx(clear / 2, abs=1e-12)


def test_rotated_receiver_shadows_agree_with_independent_segment_intersections():
    receiver = ReceiverSurface("horizontal", 23, 1.5, 1, 2, True).geometry(2, 3)
    walls = [
        ReceiverSurface("vertical", 37, 0.75, 0.1, 1, False).geometry(4, 1.8),
        ReceiverSurface("horizontal", 12, 0.75, 0.3, 1, True).geometry(1.5, 1.7),
    ]
    point, normal = np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])
    polygons, _ = visible_receiver_polygons(point, normal, receiver, walls, 128)
    rng = np.random.default_rng(1024)
    uv = rng.uniform(-0.5, 0.5, (20000, 2)) * np.array(receiver[4:])
    endpoints = receiver[0] + uv[:, :1] * receiver[1] + uv[:, 1:] * receiver[2]
    reference = visible_between(point[None, :], endpoints, walls)[0] & (
        (endpoints - point) @ normal >= 0
    )
    assert np.array_equal(contains(polygons, uv), reference)


def test_triangle_integration_matches_parallel_square_analytic_factor_and_reciprocity():
    a, b = (
        ReceiverSurface("vertical", 0, 0, 0, 1, False),
        ReceiverSurface("vertical", 180, 1, 0, 1, False),
    )
    forward, _, _ = clipped_view_factor(
        a.quadrature(1, 1, 12),
        b.geometry(1, 1),
        [],
        order=12,
        max_triangles=32,
        target_two_sided=False,
    )
    reverse, _, _ = clipped_view_factor(
        b.quadrature(1, 1, 12),
        a.geometry(1, 1),
        [],
        order=12,
        max_triangles=32,
        target_two_sided=False,
    )
    assert forward == pytest.approx(0.19982489569838746, abs=1e-10)
    assert forward == pytest.approx(reverse, abs=1e-12)
    gauss = _diffuse_view_factor(
        a.quadrature(1, 1, 12), b.quadrature(1, 1, 12), target_two_sided=False
    )
    assert forward == pytest.approx(gauss, abs=1e-10)


def test_interior_shadow_hole_has_correct_area_and_bounded_triangulation():
    receiver = ReceiverSurface("vertical", 180, 1, 0, 1, False).geometry(4, 1)
    wall = ReceiverSurface("vertical", 0, 0.5, 0, 1, False).geometry(0.16, 1)
    point, normal = np.array([0, 0, 1]), np.array([1, 0, 0])
    polygons, _ = visible_receiver_polygons(point, normal, receiver, [wall], 32)
    # Perspective magnification is 2, hence shadow area is 4*0.16.
    assert area(polygons) == pytest.approx(4 - 0.64, abs=1e-12)
    with pytest.raises(ValueError, match="triangle limit"):
        visible_receiver_polygons(point, normal, receiver, [wall], 3)


def test_terrain_faces_match_exact_slab_visibility_and_skip_endpoint_planes():
    point, normal = np.array([0.1, 0.5, 1]), np.array([1, 0, 0])
    receiver = ReceiverSurface("vertical", 180, 2.9, 0.5, 1, False).geometry(1, 1)
    cell = (1, 2, 0, 1, 2, False)
    faces = terrain_box_faces(cell, 0.5, 1.5)
    polygons, _ = visible_receiver_polygons(point, normal, receiver, faces, 32)
    assert area(polygons) == 0
    assert not visible_between(point[None, :], receiver[0][None, :], terrain_cells=[cell])[0, 0]
    endpoint = ReceiverSurface("vertical", 180, 2.9, 0.5, 1, False).geometry(4, 1)
    clear, _ = visible_receiver_polygons(point, normal, receiver, [endpoint], 32)
    assert area(clear) == pytest.approx(1)


def test_source_height_panels_preserve_area_normals_and_resource_count():
    receiver = ReceiverSurface("horizontal", 0, 4, 0, 1, True).geometry(4, 0.1)
    wall = ReceiverSurface("vertical", 180, 2, 0, 1, False).geometry(4, 1)
    planes = source_height_planes(receiver, [wall])
    points, normals, weights = segmented_cylinder(0.5, 6, 6, planes, 32)
    assert len(points) == source_point_count(0.5, 6, 6, planes, 32)
    assert len(points) > 12 * 6**2
    assert weights.sum() == pytest.approx(2 * np.pi * 0.5 * (6 + 0.5), abs=1e-12)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1)
    assert np.all((points[:, 2] >= 0) & (points[:, 2] <= 6))
    with pytest.raises(ValueError, match="source height panel"):
        source_point_count(0.5, 6, 6, planes, 1)
