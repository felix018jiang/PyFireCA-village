"""Independent ray/plane, aperture-symmetry and terrain segment references."""

import numpy as np
import pytest

from pyfireca.village.component_radiation_v3 import _diffuse_view_factor
from pyfireca.village.component_surfaces_v3 import ReceiverSurface
from pyfireca.village.radiation_visibility_v3 import coplanar_rectangles_overlap, visible_between


def test_finite_wall_blocks_only_rays_crossing_its_interior():
    wall = ReceiverSurface("vertical", 0, 0.5, 0, 1, False).geometry(1, 1)
    source = np.array([[0, 0, 1], [0, 2, 1]])
    target = np.array([[1, 0, 1], [1, 2, 1]])
    visible = visible_between(source, target, [wall])
    assert np.array_equal(visible, [[False, True], [True, True]])
    # Reception may be one-sided; geometric blocking is opaque from either side.
    assert np.array_equal(visible_between(target, source, [wall]), visible.T)
    endpoint = ReceiverSurface("vertical", 0, 1, 0, 1, False).geometry(1, 1)
    assert visible_between(source[:1], target[:1], [endpoint])[0, 0]


def test_partial_aperture_has_independent_half_symmetry_for_a_diffuse_point():
    source = np.array([[0, 0, 1.0]]), np.array([[1.0, 0, 0]]), np.array([1.0])
    target = ReceiverSurface("vertical", 180, 1, 0, 1, False).quadrature(1, 1, 12)
    # Block exactly the y>=0 half of rays; even Gauss order avoids y=0 nodes.
    half_wall = ReceiverSurface("vertical", 0, 0.5, 1, 1, False).geometry(4, 1)
    full_wall = ReceiverSurface("vertical", 0, 0.5, 0, 1, False).geometry(4, 1)
    clear = _diffuse_view_factor(source, target, target_two_sided=False)
    half = _diffuse_view_factor(
        source,
        target,
        target_two_sided=False,
        visibility=lambda a, b: visible_between(a, b, [half_wall]),
    )
    blocked = _diffuse_view_factor(
        source,
        target,
        target_two_sided=False,
        visibility=lambda a, b: visible_between(a, b, [full_wall]),
    )
    assert half == pytest.approx(clear / 2, abs=1e-12)
    assert blocked == 0


def test_step_terrain_clips_whole_ray_and_is_reciprocal():
    source = np.array([[0.1, 0.5, 1], [0.1, 0.5, 3]])
    target = np.array([[2.9, 0.5, 1], [2.9, 0.5, 3]])
    terrain = [(0, 1, 0, 1, 0, False), (1, 2, 0, 1, 2, False), (2, 3, 0, 1, 0, False)]
    visible = visible_between(source, target, terrain_cells=terrain)
    assert np.array_equal(visible, [[False, False], [False, True]])
    assert np.array_equal(visible_between(target, source, terrain_cells=terrain), visible.T)
    # Narrow obstruction is found by exact slab intervals, not samples on a ray.
    assert not visible_between(
        source[:1], np.array([[2.9, 0.5, 10]]), terrain_cells=[(1, 1.001, 0, 1, 4, False)]
    )[0, 0]


def test_excluded_cell_blocks_high_rays_while_clear_ground_does_not():
    a, b = np.array([[0, 0, 100.0]]), np.array([[3, 0, 100.0]])
    clear = [(1, 2, -1, 1, 0, False)]
    excluded = [(1, 2, -1, 1, 0, True)]
    assert visible_between(a, b, terrain_cells=clear)[0, 0]
    assert not visible_between(a, b, terrain_cells=excluded)[0, 0]


def test_ground_endpoint_contact_does_not_block_a_ray_leaving_upwards():
    ground = [(0, 2, -1, 1, 0, False)]
    assert visible_between(np.array([[0.1, 0, 0]]), np.array([[1.9, 0, 1]]), terrain_cells=ground)[
        0, 0
    ]
    assert not visible_between(
        np.array([[0.1, 0, 0]]), np.array([[1.9, 0, 0]]), terrain_cells=ground
    )[0, 0]


def test_coplanar_receiver_overlap_rejects_shared_area_but_allows_contact():
    a = ReceiverSurface().geometry(1, 1)
    same = ReceiverSurface(azimuth_deg=45).geometry(1, 1)
    edge = ReceiverSurface(offset_x_m=1).geometry(1, 1)
    high = ReceiverSurface().geometry(1, 2)
    assert coplanar_rectangles_overlap(a, same)
    assert not coplanar_rectangles_overlap(a, edge)
    assert not coplanar_rectangles_overlap(a, high)


@pytest.mark.parametrize("points", [np.zeros(3), np.ones((2, 2)), np.array([[0, np.nan, 1]])])
def test_invalid_endpoints_are_rejected(points):
    with pytest.raises(ValueError, match="finite Nx3"):
        visible_between(points, np.ones((1, 3)))
