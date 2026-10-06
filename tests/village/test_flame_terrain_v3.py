"""Continuous containment, support bounds and step-ground penetration tests."""

import numpy as np
import pytest

from pyfireca.village.flame_terrain_v3 import (
    box_intersects_oriented_cylinder,
    check_flame_terrain,
    cylinder_world_bounds,
    terrain_penetrates_oriented_cylinder,
)
from pyfireca.village.oriented_flame_surfaces_v3 import (
    oriented_segmented_cylinder,
    rotation_from_axis,
)


def source(**updates):
    return {
        **dict(radius_m=0.4, axis_length_m=2, base_xyz_m=[0, 0, 0], rotation=np.eye(3)),
        **updates,
    }


def test_containment_in_both_directions_and_clear_separation():
    assert box_intersects_oriented_cylinder(
        **source(), box_low_xyz_m=[-5, -5, -5], box_high_xyz_m=[5, 5, 5]
    )
    assert box_intersects_oriented_cylinder(
        **source(), box_low_xyz_m=[-0.1, -0.1, 0.5], box_high_xyz_m=[0.1, 0.1, 1.5]
    )
    assert not box_intersects_oriented_cylinder(
        **source(), box_low_xyz_m=[1, 1, 3], box_high_xyz_m=[2, 2, 4]
    )


@pytest.mark.parametrize(
    "low,high,expected",
    [
        ([0.4, -1, 0.5], [1, 1, 1.5], True),
        ([0.400001, -1, 0.5], [1, 1, 1.5], False),
        ([-1, -1, 2], [1, 1, 3], True),
        ([-1, -1, 2.000001], [1, 1, 3], False),
        ([-1, -1, -1], [1, 1, 0], True),
        ([-1, -1, -1], [1, 1, -0.000001], False),
    ],
)
def test_closed_volume_tangency(low, high, expected):
    assert (
        box_intersects_oriented_cylinder(**source(), box_low_xyz_m=low, box_high_xyz_m=high)
        is expected
    )


def test_aabb_overlap_does_not_mean_cylinder_overlap():
    low, high = cylinder_world_bounds(**source())
    box_low, box_high = [0.35, 0.35, 0.5], [0.45, 0.45, 1.5]
    assert np.all(np.asarray(box_low) <= high) and np.all(np.asarray(box_high) >= low)
    assert not box_intersects_oriented_cylinder(
        **source(), box_low_xyz_m=box_low, box_high_xyz_m=box_high
    )


@pytest.mark.parametrize("degrees", [0, 15, 45, 80, 90, 135, 180])
def test_exact_cylinder_support_bounds(degrees):
    rotation = rotation_from_axis([np.sin(np.radians(degrees)), 0, np.cos(np.radians(degrees))])
    base = np.array([10.0, 20.0, 30.0])
    low, high = cylinder_world_bounds(**source(rotation=rotation, base_xyz_m=base))
    for i in range(3):
        radial = rotation[i, :2]
        radial_direction = (
            radial / np.linalg.norm(radial) if np.linalg.norm(radial) else np.array([1, 0])
        )
        points = []
        for z in (0, 2):
            for sign in (-1, 1):
                points.append(base + rotation @ np.array([*(sign * 0.4 * radial_direction), z]))
        assert low[i] == pytest.approx(min(p[i] for p in points), abs=1e-12)
        assert high[i] == pytest.approx(max(p[i] for p in points), abs=1e-12)
    assert not low.flags.writeable and not high.flags.writeable


def test_flat_ground_allows_upright_flame_but_rejects_buried_tilted_lower_cap():
    cell = (-2, 2, -2, 2, 0, False)
    assert not terrain_penetrates_oriented_cylinder(**source(), terrain_cell=cell)
    rotation = rotation_from_axis([np.sqrt(0.5), 0, np.sqrt(0.5)])
    assert terrain_penetrates_oriented_cylinder(**source(rotation=rotation), terrain_cell=cell)
    # An explicitly raised *test* cylinder is clear. The module never applies
    # this lift automatically to a physical source.
    assert not terrain_penetrates_oriented_cylinder(
        **source(rotation=rotation, base_xyz_m=[0, 0, 0.4]), terrain_cell=cell
    )


def test_entire_flame_below_terrain_top_without_any_face_crossing():
    cell = (-5, 5, -5, 5, 10, False)
    assert terrain_penetrates_oriented_cylinder(**source(), terrain_cell=cell)


def test_above_ground_excluded_cell_is_opaque_at_all_heights():
    ordinary = (-2, 2, -2, 2, 0, False)
    excluded = (*ordinary[:5], True)
    args = source(base_xyz_m=[0, 0, 1000])
    assert not terrain_penetrates_oriented_cylinder(**args, terrain_cell=ordinary)
    assert terrain_penetrates_oriented_cylinder(**args, terrain_cell=excluded)


def test_higher_step_clipped_flame_and_horizontal_footprint_separation():
    rotation = rotation_from_axis([np.sqrt(0.5), 0, np.sqrt(0.5)])
    args = source(rotation=rotation, base_xyz_m=[0, 0, 0.4])
    assert terrain_penetrates_oriented_cylinder(**args, terrain_cell=(0.8, 2, -1, 1, 1.5, False))
    assert not terrain_penetrates_oriented_cylinder(**args, terrain_cell=(3, 4, -1, 1, 1.5, False))


def test_penetration_tolerance_is_explicit_and_resting_contact_allowed():
    cell = (-2, 2, -2, 2, 0, False)
    assert not terrain_penetrates_oriented_cylinder(
        **source(base_xyz_m=[0, 0, -1e-10]), terrain_cell=cell
    )
    assert terrain_penetrates_oriented_cylinder(
        **source(base_xyz_m=[0, 0, -1e-6]), terrain_cell=cell
    )
    assert not terrain_penetrates_oriented_cylinder(
        **source(base_xyz_m=[0, 0, -1e-6]), terrain_cell=cell, penetration_tolerance_m=1e-5
    )


def test_large_projected_translation_does_not_change_step_gate():
    rotation = rotation_from_axis([np.sqrt(0.5), 0, np.sqrt(0.5)])
    args = source(rotation=rotation, base_xyz_m=[0, 0, 0.4])
    cell = (0.8, 2, -1, 1, 1.5, False)
    assert terrain_penetrates_oriented_cylinder(**args, terrain_cell=cell)
    delta = np.array([371708.0, 2874584.0, 750.0])
    moved_cell = (
        cell[0] + delta[0],
        cell[1] + delta[0],
        cell[2] + delta[1],
        cell[3] + delta[1],
        cell[4] + delta[2],
        False,
    )
    assert terrain_penetrates_oriented_cylinder(
        **source(rotation=rotation, base_xyz_m=np.asarray(args["base_xyz_m"]) + delta),
        terrain_cell=moved_cell,
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"radius_m": 0},
        {"radius_m": True},
        {"axis_length_m": np.inf},
        {"rotation": np.diag([1, 1, -1])},
        {"base_xyz_m": [0, np.nan, 0]},
        {"base_xyz_m": [1e308, 0, 0]},
    ],
)
def test_bad_cylinder_rejected(updates):
    with pytest.raises(ValueError):
        cylinder_world_bounds(**source(**updates))


@pytest.mark.parametrize(
    "cell,extra",
    [
        ((0, 0, -1, 1, 0, False), {}),
        ((0, 1, -1, 1, np.nan, False), {}),
        ((0, 1, -1, 1, 0, 1), {}),
        ((0, 1, -1, 1, 0, False), {"penetration_tolerance_m": 0}),
        ((0, 1, -1, 1, 0, False), {"penetration_tolerance_m": 1}),
    ],
)
def test_bad_terrain_rejected(cell, extra):
    with pytest.raises(ValueError):
        terrain_penetrates_oriented_cylinder(**source(), terrain_cell=cell, **extra)


def test_volume_gate_before_quadrature_and_resource_limits(monkeypatch):
    import pyfireca.village.oriented_flame_surfaces_v3 as module

    def fail(*args, **kwargs):
        pytest.fail("quadrature must not be generated after invalid terrain")

    monkeypatch.setattr(module, "segmented_cylinder", fail)
    geometry = (
        np.array([4.0, 0.0, 1.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 0.0]),
        2,
        2,
    )
    with pytest.raises(ValueError, match="FLAME_TERRAIN_PENETRATION"):
        oriented_segmented_cylinder(
            **source(), target_geometry=geometry, terrain_cells=[(-5, 5, -5, 5, 10, False)]
        )
    with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
        check_flame_terrain(**source(), terrain_cells=[(-2, 2, -2, 2, 0, False)] * 2, max_cells=1)
    with pytest.raises(ValueError):
        check_flame_terrain(**source(), terrain_cells=[], penetration_tolerance_m=0)
