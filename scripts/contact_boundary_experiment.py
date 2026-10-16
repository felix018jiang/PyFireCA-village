"""Experimental numerical gate smoothing, separate from finite receiver area.

Not a calibrated DEM-error law or a production preset. Interior point contacts
retain their area; only a declared transition around the column boundary changes.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from pyfireca.village.flame_contact_v3 import FlameContactOperator


def smooth_point_weight(relative_z_m, source_height_m, transition_m):
    if isinstance(transition_m, bool) or not isinstance(transition_m, (int, float)):
        raise TypeError("transition must be numeric")
    if not np.isfinite(transition_m) or transition_m <= 0:
        raise ValueError("transition must be finite and positive")
    z, height = np.broadcast_arrays(relative_z_m, source_height_m)
    if np.any(~np.isfinite(z)) or np.any(~np.isfinite(height)) or np.any(height < 0):
        raise ValueError("relative geometry must be finite with nonnegative heights")
    margin = np.minimum(z, height - z)
    t = np.clip((margin + transition_m) / (2 * transition_m), 0, 1)
    return t * t * (3 - 2 * t)


def build_smoothed_contact(request, transition_m):
    if request.flame_contact.receiver_vertical_half_extent_m != 0:
        raise ValueError("numerical boundary smoothing must not combine with receiver area bands")
    base = FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )
    # Validate even when the grid contains no neighboring burnable cells.
    smooth_point_weight(0, 1, transition_m)
    height = request.parameters.receiver_height_m.copy()
    for material, value in request.flame_contact.receiver_height_overrides_m:
        height[request.material_index == material] = value
    ground = (
        np.zeros(request.grid.shape)
        if request.ground_elevation_m is None
        else request.ground_elevation_m
    )
    ids = np.arange(request.grid.height * request.grid.width).reshape(request.grid.shape)
    neighbors = np.full_like(base.neighbors, -1)
    fractions = np.zeros_like(base.geometry_fraction)
    for direction, (dr, dc) in enumerate(((-1, 0), (1, 0), (0, -1), (0, 1))):
        sr = slice(max(0, -dr), min(request.grid.height, request.grid.height - dr))
        sc = slice(max(0, -dc), min(request.grid.width, request.grid.width - dc))
        tr = slice(max(0, dr), min(request.grid.height, request.grid.height + dr))
        tc = slice(max(0, dc), min(request.grid.width, request.grid.width + dc))
        relative = ground[tr, tc] - ground[sr, sc] + height[tr, tc]
        weight = smooth_point_weight(
            relative, request.parameters.source_height_m[sr, sc], transition_m
        )
        keep = request.burnable_mask[sr, sc] & request.burnable_mask[tr, tc] & (weight > 0)
        source = ids[sr, sc][keep]
        neighbors[source, direction] = ids[tr, tc][keep]
        fractions[source, direction] = (
            request.parameters.coverage_fraction[sr, sc][keep] * weight[keep]
        )
    neighbors.setflags(write=False)
    fractions.setflags(write=False)
    return replace(base, neighbors=neighbors, geometry_fraction=fractions)
