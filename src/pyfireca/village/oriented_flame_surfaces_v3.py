"""Rigidly oriented closed flame surfaces for W02 geometry verification.

Source quadrature and continuous rectangle/cylinder intersection are generalized
here. Optional step-terrain volume gates precede quadrature. No dynamic graph
or thermal time integration is implemented. Rectangle shadows are coordinate invariant.
"""

from __future__ import annotations

import numpy as np

from pyfireca.village.radiation_clipping_v3 import (
    segmented_cylinder,
    source_height_planes,
    source_point_count,
)


def _vector(value, name):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.shape != (3,):
        raise ValueError(f"{name} must be a numeric three-vector")
    array = raw.astype(np.float64, copy=True)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be finite")
    return array


def rotation_from_axis(axis_unit):
    """Right-handed orthonormal frame; column 2 is the supplied unit axis.

    A circular uniform cylinder has no physical roll angle. The shortest
    rotation from world up fixes quadrature roll smoothly over the entire
    physical upper hemisphere. Near the antipode a projected east frame avoids
    a singular denominator; wildfire AGA axes never enter that lower branch.
    """
    axis = _vector(axis_unit, "axis_unit")
    if not np.isclose(np.linalg.norm(axis), 1, rtol=0, atol=1e-12):
        raise ValueError("axis_unit must have unit length")
    axis /= np.linalg.norm(axis)
    if axis[2] > -1 + 1e-8:
        x, y = -axis[1], axis[0]
        skew = np.array([[0.0, 0.0, y], [0.0, 0.0, -x], [-y, x, 0.0]])
        rotation = np.eye(3) + skew + skew @ skew / (1 + axis[2])
    else:
        reference = np.array([1.0, 0.0, 0.0])
        first = reference - np.dot(reference, axis) * axis
        first /= np.linalg.norm(first)
        rotation = np.column_stack((first, np.cross(axis, first), axis))
    rotation.setflags(write=False)
    return rotation


def _rotation(value):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.shape != (3, 3):
        raise ValueError("rotation must be a numeric 3x3 matrix")
    matrix = raw.astype(np.float64, copy=True)
    if (
        not np.all(np.isfinite(matrix))
        or not np.allclose(matrix.T @ matrix, np.eye(3), rtol=0, atol=1e-12)
        or not np.isclose(np.linalg.det(matrix), 1, rtol=0, atol=1e-12)
    ):
        raise ValueError("rotation must be a proper orthonormal rotation")
    return matrix


def _local_rectangle(geometry, base, rotation):
    if not isinstance(geometry, (tuple, list)) or len(geometry) != 6:
        raise ValueError("rectangle geometry needs center, u, v, normal, width, height")
    center, u, v, normal = (_vector(value, "rectangle vector") for value in geometry[:4])
    frame = np.column_stack((u, v, normal))
    if not np.allclose(frame.T @ frame, np.eye(3), rtol=0, atol=1e-12):
        raise ValueError("rectangle vectors must be orthonormal")
    sizes = geometry[4:]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or value <= 0
        for value in sizes
    ):
        raise ValueError("rectangle dimensions must be finite and positive")
    with np.errstate(over="ignore", invalid="ignore"):
        local = ((center - base) @ rotation, u @ rotation, v @ rotation, normal @ rotation, *sizes)
    if not all(np.all(np.isfinite(value)) for value in local):
        raise ValueError("local rectangle coordinates overflow")
    return local


def oriented_segmented_cylinder(
    *,
    radius_m,
    axis_length_m,
    base_xyz_m,
    rotation,
    target_geometry,
    blockers=(),
    order=6,
    max_segments=128,
    max_source_points=100_000,
    terrain_cells=(),
    penetration_tolerance_m=1e-9,
    max_terrain_cells=10_000,
):
    """Rotate side/cap quadrature and recompute visibility cuts in source frame.

    Transform receiver and blocker rectangles into the upright local cylinder
    frame *before* deriving critical source-height planes. Transform the final
    points/normals back to world coordinates. Surface area weights are unchanged.
    Resource limits are checked before generating the point arrays.
    """
    for name, value in (("radius_m", radius_m), ("axis_length_m", axis_length_m)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")
    for name, value in (
        ("order", order),
        ("max_segments", max_segments),
        ("max_source_points", max_source_points),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    # Counts scale as order squared even without segmentation. Avoid allocating
    # angular panels/Gauss nodes for an impossible request.
    if 12 * order * order > max_source_points:
        raise ValueError("RESOURCE_LIMIT: flame quadrature minimum exceeds point limit")
    if not isinstance(blockers, (tuple, list)):
        raise ValueError("blockers must be a tuple or list of rectangles")
    base = _vector(base_xyz_m, "base_xyz_m")
    matrix = _rotation(rotation)
    from pyfireca.village.flame_terrain_v3 import check_flame_terrain

    check_flame_terrain(
        terrain_cells=terrain_cells,
        radius_m=radius_m,
        axis_length_m=axis_length_m,
        base_xyz_m=base,
        rotation=matrix,
        penetration_tolerance_m=penetration_tolerance_m,
        max_cells=max_terrain_cells,
    )
    receiver = _local_rectangle(target_geometry, base, matrix)
    local_blockers = [_local_rectangle(value, base, matrix) for value in blockers]
    for geometry in (target_geometry, *blockers):
        if rectangle_intersects_oriented_cylinder(
            radius_m=radius_m,
            axis_length_m=axis_length_m,
            base_xyz_m=base,
            rotation=matrix,
            target_geometry=geometry,
        ):
            raise ValueError("receiver or blocker intersects the oriented flame cylinder")
    planes = source_height_planes(receiver, local_blockers)
    count = source_point_count(radius_m, axis_length_m, order, planes, max_segments)
    if count > max_source_points:
        raise ValueError("RESOURCE_LIMIT: segmented flame exceeds source point limit")
    points, normals, weights = segmented_cylinder(
        radius_m, axis_length_m, order, planes, max_segments
    )
    with np.errstate(over="ignore", invalid="ignore"):
        points = points @ matrix.T + base
        normals = normals @ matrix.T
    if any(np.any(~np.isfinite(array)) for array in (points, normals, weights)):
        raise ValueError("oriented flame quadrature overflows")
    if np.any(weights <= 0):
        raise ValueError("flame surface area weights must be positive")
    for array in (points, normals, weights):
        array.setflags(write=False)
    return points, normals, weights


def _axial_clip_closed(polygon, plane):
    """Clip a closed convex polygon, retaining degenerate contact lines/points."""
    if not len(polygon):
        return polygon
    values = polygon @ plane[:2] + plane[2]
    if np.any(~np.isfinite(values)):
        raise ValueError("intersection clipping overflows")
    result = []
    previous, previous_value = polygon[-1], values[-1]
    for current, value in zip(polygon, values, strict=True):
        if (previous_value >= 0) != (value >= 0):
            # Opposite signed values: weighted interpolation avoids dividing
            # by a subtraction of large opposite values.
            scale = max(abs(previous_value), abs(value))
            fraction = (previous_value / scale) / (previous_value / scale - value / scale)
            result.append((1 - fraction) * previous + fraction * current)
        if value >= 0:
            result.append(current)
        previous, previous_value = current, value
    return np.asarray(result).reshape(-1, 2)


def rectangle_intersects_oriented_cylinder(
    *,
    radius_m,
    axis_length_m,
    base_xyz_m,
    rotation,
    target_geometry,
):
    """Continuous closed-solid test, including axial caps and tangency.

    Clip the rectangle in its (u,v) coordinates to the cylinder's axial slab.
    Minimize squared radial distance over that convex polygon: an interior
    stationary point or one of the exact segment minima. No point sampling.
    A 1e-12 m contact tolerance matches the upright receiver-envelope gate.
    This checks rectangles, not volume terrain columns or cylinder pairs.
    """
    for name, value in (("radius_m", radius_m), ("axis_length_m", axis_length_m)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")
    base = _vector(base_xyz_m, "base_xyz_m")
    matrix = _rotation(rotation)
    center, u, v, _, width, height = _local_rectangle(target_geometry, base, matrix)
    polygon = np.array(
        [
            [-width / 2, -height / 2],
            [width / 2, -height / 2],
            [width / 2, height / 2],
            [-width / 2, height / 2],
        ]
    )
    low = np.array([u[2], v[2], center[2] + 1e-12])
    high = np.array([-u[2], -v[2], axis_length_m - center[2] + 1e-12])
    polygon = _axial_clip_closed(_axial_clip_closed(polygon, low), high)
    if not len(polygon):
        return False
    planar_basis = np.column_stack((u[:2], v[:2]))
    with np.errstate(over="ignore", invalid="ignore"):
        planar_points = polygon @ planar_basis.T + center[:2]
    if not np.all(np.isfinite(planar_points)):
        raise ValueError("intersection coordinates overflow")
    minimum = float(np.min(np.hypot(planar_points[:, 0], planar_points[:, 1])))
    for a, b in zip(planar_points, np.roll(planar_points, -1, axis=0), strict=True):
        delta = b - a
        extent = np.hypot(*delta)
        if not np.isfinite(extent):
            raise ValueError("intersection edge extent overflows")
        if extent > 0:
            direction = delta / extent
            distance = np.clip(-np.dot(a, direction), 0, extent)
            point = a + distance * direction
            minimum = min(minimum, float(np.hypot(*point)))
    # With full rank an interior radial stationary point may be missed by all
    # four edges (e.g. a large horizontal receiver crossing the flame center).
    # With deficient rank the minimizing line reaches the boundary, so exact
    # edge minima already cover it.
    stationary, _, rank, _ = np.linalg.lstsq(planar_basis, -center[:2], rcond=None)
    if rank == 2:
        a, b = stationary
        z = center[2] + a * u[2] + b * v[2]
        if (
            -width / 2 - 1e-12 <= a <= width / 2 + 1e-12
            and -height / 2 - 1e-12 <= b <= height / 2 + 1e-12
            and -1e-12 <= z <= axis_length_m + 1e-12
        ):
            point = center[:2] + planar_basis @ stationary
            minimum = min(minimum, float(np.hypot(*point)))
    return bool(minimum <= radius_m + 1e-12)
