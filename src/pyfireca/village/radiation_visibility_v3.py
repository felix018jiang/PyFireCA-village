"""Continuous segment visibility for opaque rectangles and stepwise DEM cells.

Terrain is a piecewise-constant height per physical cell, not a reconstructed
surface. Rectangle blockers are two-sided opaque geometry independently of
whether the receiver accepts heating on one or both faces. No reflections or
transmission are traced. All computations use local metric coordinates.
"""

from __future__ import annotations

import numpy as np


def coplanar_rectangles_overlap(a, b) -> bool:
    """Separating-axis test: reject shared positive area, permit edge contact."""
    ca, ua, va, na, wa, ha = a
    cb, ub, vb, nb, wb, hb = b
    delta = cb - ca
    if abs(np.dot(na, nb)) < 1 - 1e-12 or abs(np.dot(delta, na)) > 1e-12:
        return False
    for axis in (ua, va, ub, vb):
        ra = abs(np.dot(ua, axis)) * wa / 2 + abs(np.dot(va, axis)) * ha / 2
        rb = abs(np.dot(ub, axis)) * wb / 2 + abs(np.dot(vb, axis)) * hb / 2
        if abs(np.dot(delta, axis)) >= ra + rb - 1e-12:
            return False
    return True


def visible_between(source_points, target_points, rectangles=(), terrain_cells=()):
    """Return [source_point, target_point] visibility without sampling along rays.

    Rectangle tuple: (center, u, v, normal, width, height).
    Terrain tuple: (xmin, xmax, ymin, ymax, elevation, excluded).
    Excluded cells are opaque through their entire height, conservatively
    preventing a radiation ray from passing through unobserved/excluded space.
    Callers exclude the emitting component and intended target from blockers.
    """
    source_points = np.asarray(source_points, dtype=np.float64)
    target_points = np.asarray(target_points, dtype=np.float64)
    for points in (source_points, target_points):
        if points.ndim != 2 or points.shape[1] != 3 or np.any(~np.isfinite(points)):
            raise ValueError("visibility endpoints must be finite Nx3 point arrays")
    start = source_points[:, None, :]
    delta = target_points[None, :, :] - start
    visible = np.ones(delta.shape[:2], dtype=np.bool_)
    for center, u, v, normal, width, height in rectangles:
        denominator = np.einsum("ijk,k->ij", delta, normal)
        numerator = np.einsum("ijk,k->ij", center - start, normal)
        crossing = np.abs(denominator) > 1e-14
        t = np.divide(numerator, denominator, out=np.zeros_like(denominator), where=crossing)
        # Strict interior intersection avoids blocking the intended endpoint.
        crossing &= (t > 1e-12) & (t < 1 - 1e-12)
        intersection = start + t[:, :, None] * delta - center
        inside = (np.abs(np.einsum("ijk,k->ij", intersection, u)) <= width / 2 + 1e-12) & (
            np.abs(np.einsum("ijk,k->ij", intersection, v)) <= height / 2 + 1e-12
        )
        visible &= ~(crossing & inside)
    for xmin, xmax, ymin, ymax, elevation, excluded in terrain_cells:
        entry = np.zeros(delta.shape[:2])
        leave = np.ones(delta.shape[:2])
        intersects = np.ones(delta.shape[:2], dtype=np.bool_)
        for axis, low, high in ((0, xmin, xmax), (1, ymin, ymax)):
            parallel = np.abs(delta[:, :, axis]) <= 1e-14
            inside = (start[:, :, axis] >= low - 1e-12) & (start[:, :, axis] <= high + 1e-12)
            intersects &= ~parallel | inside
            t1 = np.divide(
                low - start[:, :, axis],
                delta[:, :, axis],
                out=np.full_like(entry, -np.inf),
                where=~parallel,
            )
            t2 = np.divide(
                high - start[:, :, axis],
                delta[:, :, axis],
                out=np.full_like(entry, np.inf),
                where=~parallel,
            )
            axis_entry, axis_leave = np.minimum(t1, t2), np.maximum(t1, t2)
            entry = np.maximum(entry, np.where(parallel, -np.inf, axis_entry))
            leave = np.minimum(leave, np.where(parallel, np.inf, axis_leave))
        intersects &= (leave > entry + 1e-12) & (leave > 1e-12) & (entry < 1 - 1e-12)
        if excluded:
            visible &= ~intersects
        else:
            zentry = start[:, :, 2] + entry * delta[:, :, 2]
            zleave = start[:, :, 2] + leave * delta[:, :, 2]
            penetrates = np.minimum(zentry, zleave) < elevation - 1e-12
            lies_on_or_below = np.maximum(zentry, zleave) <= elevation + 1e-12
            visible &= ~(intersects & (penetrates | lies_on_or_below))
    return visible
