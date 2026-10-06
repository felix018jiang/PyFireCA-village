"""Rigid geometry, analytic shadows and area checks independent of fire spread."""

import numpy as np
import pytest

from pyfireca.village.oriented_flame_surfaces_v3 import (
    oriented_segmented_cylinder,
    rotation_from_axis,
)
from pyfireca.village.radiation_clipping_v3 import (
    clipped_view_factor,
    segmented_cylinder,
    source_height_planes,
)


def rectangle(center=(4, 0, 1), width=2, height=2):
    return (
        np.array(center, dtype=float),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 0.0]),
        width,
        height,
    )


def rotate(geometry, rotation, translation):
    return (
        rotation @ geometry[0] + translation,
        *(rotation @ value for value in geometry[1:4]),
        *geometry[4:],
    )


def source(**updates):
    arguments = dict(
        radius_m=0.4,
        axis_length_m=2,
        base_xyz_m=[0, 0, 0],
        rotation=np.eye(3),
        target_geometry=rectangle(),
        order=6,
    )
    return oriented_segmented_cylinder(**{**arguments, **updates})


def test_zero_tilt_is_exact_old_segmented_geometry():
    target = rectangle()
    expected = segmented_cylinder(0.4, 2, 6, source_height_planes(target, []), 128)
    actual = source()
    for a, b in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(rotation_from_axis([0, 0, 1]), np.eye(3))


@pytest.mark.parametrize("angle", [0, 15, 45, 80, 90, 135, 180])
def test_closed_surface_area_axis_bounds_and_unit_normals(angle):
    axis = np.array([np.sin(np.radians(angle)), 0.0, np.cos(np.radians(angle))])
    rotation = rotation_from_axis(axis)
    points, normals, weights = source(rotation=rotation)
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-12)
    assert weights.sum() == pytest.approx(2 * np.pi * 0.4 * 2 + 2 * np.pi * 0.4**2)
    local = points @ rotation
    assert local[:, 2].min() == pytest.approx(0)
    assert local[:, 2].max() == pytest.approx(2)
    assert np.max(np.hypot(local[:, 0], local[:, 1])) == pytest.approx(0.4)
    with pytest.raises(ValueError):
        weights[0] = 1


def test_full_rigid_rotation_preserves_clipped_transfer_including_blocker():
    target = rectangle()
    blockers = [rectangle(center=(2, 0.4, 1), width=0.6, height=2)]
    upright = source(blockers=blockers)
    old = clipped_view_factor(upright, target, blockers, order=6, max_triangles=128)[0]
    clear = clipped_view_factor(source(), target, [], order=6, max_triangles=128)[0]
    assert 0 < old < clear
    angle = np.radians(47)
    rotation = np.array(
        [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
    )
    translation = np.array([10.0, -5.0, 100.0])
    new_target = rotate(target, rotation, translation)
    new_blockers = [rotate(blocker, rotation, translation) for blocker in blockers]
    tilted = source(
        rotation=rotation, base_xyz_m=translation, target_geometry=new_target, blockers=new_blockers
    )
    for index in (0, 1):
        expected = upright[index] @ rotation.T + (translation if index == 0 else 0)
        np.testing.assert_allclose(tilted[index], expected, atol=1e-12)
    np.testing.assert_allclose(tilted[2], upright[2], atol=1e-12)
    new = clipped_view_factor(tilted, new_target, new_blockers, order=6, max_triangles=128)[0]
    assert new == pytest.approx(old, rel=1e-11, abs=1e-13)


def test_tilt_moves_emitting_surface_and_changes_clear_receiver_transfer():
    target = rectangle(center=(3, 0, 0.7))
    values = []
    for degrees in (0, 45):
        rotation = rotation_from_axis(
            [np.sin(np.radians(degrees)), 0.0, np.cos(np.radians(degrees))]
        )
        emitter = source(rotation=rotation, target_geometry=target, order=12)
        value = clipped_view_factor(emitter, target, [], order=12, max_triangles=128)[0]
        values.append(value)
    assert values[1] > values[0]
    assert all(0 < value < 1 for value in values)


@pytest.mark.parametrize(
    "updates",
    [
        {"rotation": np.diag([1, 1, -1])},
        {"rotation": 2 * np.eye(3)},
        {"rotation": [[True] * 3] * 3},
        {"base_xyz_m": [0, np.nan, 0]},
        {"radius_m": 0},
        {"axis_length_m": np.inf},
        {"order": True},
        {"order": 1000000},
        {"max_source_points": 1},
        {"target_geometry": rectangle(width=0)},
        {"target_geometry": (np.zeros(3), np.ones(3), np.ones(3), np.ones(3), 2, 2)},
    ],
)
def test_invalid_geometry_or_resource_rejects(updates):
    with pytest.raises(ValueError):
        source(**updates)


@pytest.mark.parametrize("axis", [[0, 0, 2], [0, 0, 0], [0, np.nan, 1], [True, False, False]])
def test_invalid_axis_rejects(axis):
    with pytest.raises(ValueError):
        rotation_from_axis(axis)


def test_tilted_clear_surface_quadrature_converges():
    target = rectangle(center=(3, 0, 0.7))
    rotation = rotation_from_axis([np.sqrt(0.5), 0, np.sqrt(0.5)])
    values = []
    for order in (3, 6, 12, 24):
        emitter = source(rotation=rotation, target_geometry=target, order=order)
        values.append(clipped_view_factor(emitter, target, [], order=order, max_triangles=128)[0])
    errors = [abs(value - values[-1]) for value in values[:-1]]
    assert errors[0] > errors[1] > errors[2]
    assert errors[2] < 1e-5 * values[-1]


def test_continuous_intersection_catches_interior_even_when_all_corners_clear():
    from pyfireca.village.oriented_flame_surfaces_v3 import rectangle_intersects_oriented_cylinder

    horizontal = (
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        10,
        10,
    )
    assert rectangle_intersects_oriented_cylinder(
        radius_m=0.4,
        axis_length_m=2,
        base_xyz_m=[0, 0, 0],
        rotation=np.eye(3),
        target_geometry=horizontal,
    )
    vertical = rectangle(center=(0, 0, 1), width=10, height=10)
    assert rectangle_intersects_oriented_cylinder(
        radius_m=0.4,
        axis_length_m=2,
        base_xyz_m=[0, 0, 0],
        rotation=np.eye(3),
        target_geometry=vertical,
    )


@pytest.mark.parametrize(
    "center,expected",
    [
        ((0.4, 0, 1), True),
        ((0.400001, 0, 1), False),
        ((0, 0, 3), True),
        ((0, 0, 3.000001), False),
        ((0, 0, -1), True),
        ((0, 0, -1.000001), False),
        ((4, 0, 1), False),
    ],
)
def test_closed_cylinder_side_and_cap_tangency(center, expected):
    from pyfireca.village.oriented_flame_surfaces_v3 import rectangle_intersects_oriented_cylinder

    assert (
        rectangle_intersects_oriented_cylinder(
            radius_m=0.4,
            axis_length_m=2,
            base_xyz_m=[0, 0, 0],
            rotation=np.eye(3),
            target_geometry=rectangle(center=center),
        )
        is expected
    )


@pytest.mark.parametrize("intersect", [True, False])
def test_intersection_rigid_rotation_translation_invariance(intersect):
    from pyfireca.village.oriented_flame_surfaces_v3 import rectangle_intersects_oriented_cylinder

    target = rectangle(center=(0.2 if intersect else 0.8, 0, 1))
    axis = [np.sqrt(0.5), 0, np.sqrt(0.5)]
    rotation = rotation_from_axis(axis)
    base = np.array([371708.0, 2874584.0, 750.0])
    transformed = rotate(target, rotation, base)
    assert (
        rectangle_intersects_oriented_cylinder(
            radius_m=0.4,
            axis_length_m=2,
            base_xyz_m=base,
            rotation=rotation,
            target_geometry=transformed,
        )
        is intersect
    )


def test_zero_tilt_intersection_agrees_with_existing_supported_rectangles():
    from pyfireca.village.component_surfaces_v3 import ReceiverSurface
    from pyfireca.village.oriented_flame_surfaces_v3 import rectangle_intersects_oriented_cylinder

    for orientation in ("horizontal", "vertical"):
        for x in (-1, 0, 0.2, 1):
            for z in (0.1, 1, 3):
                surface = ReceiverSurface(orientation=orientation, offset_x_m=x)
                target = surface.geometry(1, z)
                expected = surface.intersects_cylinder(1, z, 0.4, 0, 2)
                assert (
                    rectangle_intersects_oriented_cylinder(
                        radius_m=0.4,
                        axis_length_m=2,
                        base_xyz_m=[0, 0, 0],
                        rotation=np.eye(3),
                        target_geometry=target,
                    )
                    == expected
                )


@pytest.mark.parametrize("as_blocker", [False, True])
def test_surface_generation_rejects_intersecting_receiver_or_blocker(as_blocker):
    intersecting = rectangle(center=(0, 0, 1), width=10, height=10)
    arguments = {"blockers": [intersecting]} if as_blocker else {"target_geometry": intersecting}
    with pytest.raises(ValueError, match="intersects"):
        source(**arguments)


def test_intersection_never_misses_sampled_interior_in_random_oblique_rectangles():
    from pyfireca.village.oriented_flame_surfaces_v3 import rectangle_intersects_oriented_cylinder

    rng = np.random.default_rng(20261005)
    a, b = np.meshgrid(np.linspace(-0.5, 0.5, 31), np.linspace(-0.5, 0.5, 31))
    positive_cases = 0
    for _ in range(150):
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        center = rng.uniform([-1, -1, -0.5], [1, 1, 2.5])
        width, height = rng.uniform(0.1, 3, size=2)
        target = (
            center,
            rotation[:, 0],
            rotation[:, 1],
            rotation[:, 2],
            float(width),
            float(height),
        )
        points = (
            center
            + a.ravel()[:, None] * width * rotation[:, 0]
            + b.ravel()[:, None] * height * rotation[:, 1]
        )
        sampled_inside = np.any(
            (np.hypot(points[:, 0], points[:, 1]) < 0.4) & (points[:, 2] > 0) & (points[:, 2] < 2)
        )
        continuous = rectangle_intersects_oriented_cylinder(
            radius_m=0.4,
            axis_length_m=2,
            base_xyz_m=[0, 0, 0],
            rotation=np.eye(3),
            target_geometry=target,
        )
        if sampled_inside:
            positive_cases += 1
            assert continuous
    assert positive_cases >= 30


def test_axis_quadrature_roll_is_continuous_across_former_east_switch():
    rotations = []
    for x in (0.899999, 0.900001):
        rotations.append(rotation_from_axis([x, 0, np.sqrt(1 - x * x)]))
    assert np.linalg.norm(rotations[0] - rotations[1]) < 1e-4
    for matrix in rotations:
        np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-12)
        assert np.linalg.det(matrix) == pytest.approx(1)
