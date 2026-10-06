"""Continuous oriented flame cylinder versus box/step-terrain volume gates.

Faces alone miss a flame wholly contained in an opaque terrain column. A
closed convex solid intersects a box if a box face intersects the solid or a
point of the solid lies inside the box. This module combines both tests, with
an exact cylinder AABB for the broad phase. No sampling or external optimizer.
"""

from __future__ import annotations

import numpy as np

from pyfireca.village.oriented_flame_surfaces_v3 import (
    _rotation,
    _vector,
    rectangle_intersects_oriented_cylinder,
)
from pyfireca.village.wind_v3 import _number


def cylinder_world_bounds(*, radius_m, axis_length_m, base_xyz_m, rotation):
    """Exact support bounds of the closed side and two circular end disks."""
    radius = _number(radius_m, "radius_m", minimum=0)
    length = _number(axis_length_m, "axis_length_m", minimum=0)
    if radius == 0 or length == 0:
        raise ValueError("radius and length must be positive")
    base = _vector(base_xyz_m, "base_xyz_m")
    matrix = _rotation(rotation)
    with np.errstate(over="ignore", invalid="ignore"):
        axial = length * matrix[:, 2]
        radial = radius * np.hypot(matrix[:, 0], matrix[:, 1])
        low = base + np.minimum(0, axial) - radial
        high = base + np.maximum(0, axial) + radial
    if np.any(~np.isfinite(low)) or np.any(~np.isfinite(high)) or np.any(high <= low):
        raise ValueError("cylinder bounds overflow or lose their physical extent")
    low.setflags(write=False)
    high.setflags(write=False)
    return low, high


def _box_faces(low, high):
    # Half-sums avoid overflow of low+high; extent itself must remain finite.
    center, extent = low / 2 + high / 2, high - low
    if np.any(~np.isfinite(extent)):
        raise ValueError("box extent overflows")
    axes = np.eye(3)
    for normal_axis in range(3):
        other = [i for i in range(3) if i != normal_axis]
        u, v = axes[other]
        for side in (low[normal_axis], high[normal_axis]):
            face_center = center.copy()
            face_center[normal_axis] = side
            yield (
                face_center,
                u,
                v,
                axes[normal_axis],
                float(extent[other[0]]),
                float(extent[other[1]]),
            )


def box_intersects_oriented_cylinder(
    *,
    box_low_xyz_m,
    box_high_xyz_m,
    radius_m,
    axis_length_m,
    base_xyz_m,
    rotation,
):
    """Closed-solid intersection, including containment in either direction.

    Side/cap tangency is counted. Ground penetration uses a separate tolerance
    and eroded terrain box below, so a vertical flame may rest on its ground.
    """
    low = _vector(box_low_xyz_m, "box_low_xyz_m")
    high = _vector(box_high_xyz_m, "box_high_xyz_m")
    if np.any(high <= low):
        raise ValueError("box high coordinates must exceed low coordinates")
    base = _vector(base_xyz_m, "base_xyz_m")
    matrix = _rotation(rotation)
    source_low, source_high = cylinder_world_bounds(
        radius_m=radius_m,
        axis_length_m=axis_length_m,
        base_xyz_m=base,
        rotation=matrix,
    )
    if np.any(source_high < low - 1e-12) or np.any(source_low > high + 1e-12):
        return False
    # A base-disk center belongs to the cylinder. This also detects complete
    # containment where none of the six box faces meets its boundary or volume.
    if np.all(base >= low - 1e-12) and np.all(base <= high + 1e-12):
        return True
    return any(
        rectangle_intersects_oriented_cylinder(
            radius_m=radius_m,
            axis_length_m=axis_length_m,
            base_xyz_m=base,
            rotation=matrix,
            target_geometry=face,
        )
        for face in _box_faces(low, high)
    )


def terrain_penetrates_oriented_cylinder(
    *,
    terrain_cell,
    radius_m,
    axis_length_m,
    base_xyz_m,
    rotation,
    penetration_tolerance_m=1e-9,
):
    """Positive penetration into step terrain or a vertically opaque excluded cell.

    Terrain tuple is (xmin,xmax,ymin,ymax,elevation,excluded), as in the
    visibility module. The tolerance erodes all finite terrain boundaries;
    tangency/penetration below this length is unresolved and allowed. It must
    exceed the rectangle contact tolerance and be less than half a cell width.
    No actual solid geometry or radiation area is clipped or lifted here.
    """
    if not isinstance(terrain_cell, (tuple, list)) or len(terrain_cell) != 6:
        raise ValueError("terrain_cell requires six values")
    xmin, xmax, ymin, ymax, elevation = (
        _number(value, "terrain coordinate") for value in terrain_cell[:5]
    )
    excluded = terrain_cell[5]
    if not isinstance(excluded, (bool, np.bool_)):
        raise ValueError("terrain excluded flag must be boolean")
    tolerance = _number(penetration_tolerance_m, "penetration_tolerance_m", minimum=0)
    if tolerance <= 1e-12:
        raise ValueError("penetration tolerance must exceed 1e-12 m contact tolerance")
    if xmax - xmin <= 2 * tolerance or ymax - ymin <= 2 * tolerance:
        raise ValueError("terrain cell extent must exceed twice the penetration tolerance")
    source_low, source_high = cylinder_world_bounds(
        radius_m=radius_m,
        axis_length_m=axis_length_m,
        base_xyz_m=base_xyz_m,
        rotation=rotation,
    )
    # Artificial vertical bounds lie strictly outside the entire cylinder.
    # Excluded space is opaque at every height, not merely below its DEM.
    padding = max(float(radius_m), float(axis_length_m), 4 * tolerance)
    with np.errstate(over="ignore", invalid="ignore"):
        bottom = source_low[2] - padding
        top = source_high[2] + padding if excluded else elevation - tolerance
    if not np.isfinite(bottom) or not np.isfinite(top) or bottom >= source_low[2]:
        raise ValueError("terrain vertical bounding interval exceeds numerical range")
    if top < source_low[2] - 1e-12:
        return False
    if excluded and top <= source_high[2]:
        raise ValueError("excluded-column upper bound loses its padding")
    return box_intersects_oriented_cylinder(
        box_low_xyz_m=[xmin + tolerance, ymin + tolerance, bottom],
        box_high_xyz_m=[xmax - tolerance, ymax - tolerance, top],
        radius_m=radius_m,
        axis_length_m=axis_length_m,
        base_xyz_m=base_xyz_m,
        rotation=rotation,
    )


def check_flame_terrain(
    *,
    terrain_cells,
    radius_m,
    axis_length_m,
    base_xyz_m,
    rotation,
    penetration_tolerance_m=1e-9,
    max_cells=10_000,
):
    """Reject the first intersected terrain volume before surface quadrature."""
    if type(max_cells) is not int or max_cells <= 0:
        raise ValueError("max_cells must be a positive integer")
    if not isinstance(terrain_cells, (tuple, list)):
        raise ValueError("terrain_cells must be a tuple or list")
    if len(terrain_cells) > max_cells:
        raise ValueError("RESOURCE_LIMIT: flame terrain-cell limit exceeded")
    tolerance = _number(penetration_tolerance_m, "penetration_tolerance_m", minimum=0)
    if tolerance <= 1e-12:
        raise ValueError("penetration tolerance must exceed 1e-12 m contact tolerance")
    # Validate the source even for an empty list, so invalid/inactive geometry
    # cannot sneak through an empty-domain diagnostic.
    cylinder_world_bounds(
        radius_m=radius_m, axis_length_m=axis_length_m, base_xyz_m=base_xyz_m, rotation=rotation
    )
    for index, cell in enumerate(terrain_cells):
        if terrain_penetrates_oriented_cylinder(
            terrain_cell=cell,
            radius_m=radius_m,
            axis_length_m=axis_length_m,
            base_xyz_m=base_xyz_m,
            rotation=rotation,
            penetration_tolerance_m=penetration_tolerance_m,
        ):
            raise ValueError(
                f"FLAME_TERRAIN_PENETRATION: terrain cell {index}; no lift or clipping applied"
            )
