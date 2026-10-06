"""Integrate visible receiver polygons after analytic opaque-shadow clipping.

For each emitting quadrature point, a rectangle's segment shadow is the
intersection of five linear half-planes on the receiver plane. Subtracting
those convex regions before integration avoids sampling a discontinuous
visibility indicator on a fixed receiving grid. Overlapping shadows are
subtracted successively, so blocked area is never counted twice.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
from numpy.polynomial.legendre import leggauss

from pyfireca.village.component_radiation_v3 import _cylinder


def source_height_planes(target_geometry, blockers):
    """Planes where a target corner ray meets a blocker edge as source z varies.

    The side cylinder is subdivided at these heights for each angular node.
    Additional cuts outside a finite blocker edge are harmless subdivisions.
    """
    center, u, v, normal, width, height = target_geometry
    corners = [
        center + a * u * width / 2 + b * v * height / 2
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    planes = []
    if abs(normal[2]) > 1e-14:
        planes.append((normal, float(np.dot(normal, center))))
    for bc, bu, bv, bn, bw, bh in blockers:
        vertices = [
            bc + a * bu * bw / 2 + b * bv * bh / 2 for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
        if abs(bn[2]) > 1e-14:
            planes.append((bn, float(np.dot(bn, bc))))
        for corner in corners:
            for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True):
                n = np.cross(a - corner, b - corner)
                norm = np.linalg.norm(n)
                if norm > 0 and abs(n[2]) > 1e-14 * norm:
                    n = n / norm
                    planes.append((n, float(np.dot(n, corner))))
        for corner in vertices:
            for a, b in zip(corners, corners[1:] + corners[:1], strict=True):
                n = np.cross(a - corner, b - corner)
                norm = np.linalg.norm(n)
                if norm > 0 and abs(n[2]) > 1e-14 * norm:
                    n = n / norm
                    planes.append((n, float(np.dot(n, corner))))
    return planes


def _height_panels(radius, length, order, planes, max_segments):
    result = []
    for angle in (np.arange(4 * order) + 0.5) * (2 * np.pi / (4 * order)):
        x, y = radius * np.cos(angle), radius * np.sin(angle)
        cuts = [0.0, length]
        for normal, distance in planes:
            z = (distance - normal[0] * x - normal[1] * y) / normal[2]
            if 0 < z < length:
                cuts.append(float(z))
        cuts.sort()
        clean = [cuts[0]]
        for cut in cuts[1:]:
            if cut - clean[-1] > 1e-12 * length:
                clean.append(cut)
        if clean[-1] != length:
            clean[-1] = length
        if len(clean) - 1 > max_segments:
            raise ValueError("RESOURCE_LIMIT: flame source height panel limit exceeded")
        result.append((angle, clean))
    return result


def source_point_count(radius, length, order, planes, max_segments):
    panels = _height_panels(radius, length, order, planes, max_segments)
    return order * sum(len(cuts) - 1 for _, cuts in panels) + 8 * order * order


def segmented_cylinder(radius, length, order, planes, max_segments):
    """Same closed emitting cylinder/area, piecewise Gauss integration in side z."""
    nodes, weights = leggauss(order)
    points, normals, areas = [], [], []
    for angle, cuts in _height_panels(radius, length, order, planes, max_segments):
        co, si = np.cos(angle), np.sin(angle)
        for low, high in pairwise(cuts):
            z = low + (nodes + 1) * (high - low) / 2
            points.append(
                np.column_stack((np.full(order, radius * co), np.full(order, radius * si), z))
            )
            normals.append(np.tile([co, si, 0.0], (order, 1)))
            areas.append(weights * (high - low) / 2 * radius * 2 * np.pi / (4 * order))
    cp, cn, ca = _cylinder(radius, 0, length, order)
    caps = 8 * order * order
    points.append(cp[-caps:])
    normals.append(cn[-caps:])
    areas.append(ca[-caps:])
    return np.concatenate(points), np.concatenate(normals), np.concatenate(areas)


def _clip(polygon, plane):
    """Sutherland-Hodgman clipping to a*x+b*y+c >= 0."""
    if len(polygon) == 0:
        return polygon
    values = polygon @ plane[:2] + plane[2]
    result = []
    previous, fp = polygon[-1], values[-1]
    for current, fc in zip(polygon, values, strict=True):
        if (fp >= 0) != (fc >= 0):
            result.append(previous + fp / (fp - fc) * (current - previous))
        if fc >= 0:
            result.append(current)
        previous, fp = current, fc
    if len(result) < 3:
        return np.empty((0, 2))
    result = np.asarray(result)
    # Delete repeated neighboring vertices; no distance regularization is used.
    scale = max(float(np.ptp(result, axis=0).max()), np.finfo(float).tiny)
    keep = np.linalg.norm(result - np.roll(result, 1, axis=0), axis=1) > scale * 1e-13
    result = result[keep]
    return result if len(result) >= 3 else np.empty((0, 2))


def _triangle_count(polygons):
    return sum(max(0, len(p) - 2) for p in polygons)


def _shadow_planes(point, target_geometry, blocker):
    center, u, v = target_geometry[:3]
    bc, bu, bv, bn, bw, bh = blocker
    distance = float(np.dot(bn, bc - point))
    if abs(distance) <= 1e-14:
        return ()  # Endpoint/coplanar contact has zero positive-area shadow.
    sign = np.sign(distance)
    base = center - point
    d = sign * np.array([np.dot(bn, u), np.dot(bn, v), np.dot(bn, base)])
    absolute = abs(distance)
    if np.all(np.abs(d[:2]) <= 1e-14) and abs(d[2] - absolute) <= 1e-12:
        return ()  # The entire receiving plane is the ray endpoint, t=1.
    planes = [d - np.array([0, 0, absolute])]
    for axis, extent in ((bu, bw / 2), (bv, bh / 2)):
        offset = np.dot(axis, point - bc)
        direction = np.array([np.dot(axis, u), np.dot(axis, v), np.dot(axis, base)])
        planes.extend(
            (
                (extent - offset) * d - absolute * direction,
                (extent + offset) * d + absolute * direction,
            )
        )
    return planes


def visible_receiver_polygons(point, source_normal, target_geometry, blockers, max_triangles):
    """Visible convex polygons in receiving (u,v) coordinates and vertex-test count."""
    center, u, v, _, width, height = target_geometry
    polygon = np.array(
        [
            [-width / 2, -height / 2],
            [width / 2, -height / 2],
            [width / 2, height / 2],
            [-width / 2, height / 2],
        ]
    )
    emitting_halfspace = np.array(
        [np.dot(source_normal, u), np.dot(source_normal, v), np.dot(source_normal, center - point)]
    )
    polygon = _clip(polygon, emitting_halfspace)
    pieces = [polygon] if len(polygon) else []
    tests = 4
    for blocker in blockers:
        planes = _shadow_planes(point, target_geometry, blocker)
        if not planes:
            continue
        outside = []
        for piece in pieces:
            remaining = piece
            for plane in planes:
                if np.all(plane[:2] == 0) and plane[2] >= 0:
                    continue  # A universally true constraint has no outside area.
                tests += 2 * len(remaining)
                clear = _clip(remaining, -plane)
                remaining = _clip(remaining, plane)
                if len(clear):
                    outside.append(clear)
                if _triangle_count(outside) + max(0, len(remaining) - 2) > max_triangles:
                    raise ValueError(
                        "RESOURCE_LIMIT: receiver shadow clipping triangle limit exceeded"
                    )
                if len(remaining) == 0:
                    break
        pieces = outside
        if _triangle_count(pieces) > max_triangles:
            raise ValueError("RESOURCE_LIMIT: receiver shadow clipping triangle limit exceeded")
        if not pieces:
            break
    return pieces, tests


def terrain_box_faces(cell, min_z, max_z):
    """Finite top/four sides of the terrain column, sufficient for the ray range."""
    xmin, xmax, ymin, ymax, elevation, excluded = cell
    top = max_z + 1.0 if excluded else elevation
    if top <= min_z:
        return []
    bottom = min_z - 1.0
    xmid, ymid, zmid = (xmin + xmax) / 2, (ymin + ymax) / 2, (bottom + top) / 2
    width, depth, height = xmax - xmin, ymax - ymin, top - bottom
    return [
        (
            np.array([xmid, ymid, top]),
            np.array([1, 0, 0]),
            np.array([0, 1, 0]),
            np.array([0, 0, 1]),
            width,
            depth,
        ),
        (
            np.array([xmin, ymid, zmid]),
            np.array([0, 1, 0]),
            np.array([0, 0, 1]),
            np.array([1, 0, 0]),
            depth,
            height,
        ),
        (
            np.array([xmax, ymid, zmid]),
            np.array([0, 1, 0]),
            np.array([0, 0, 1]),
            np.array([1, 0, 0]),
            depth,
            height,
        ),
        (
            np.array([xmid, ymin, zmid]),
            np.array([1, 0, 0]),
            np.array([0, 0, 1]),
            np.array([0, 1, 0]),
            width,
            height,
        ),
        (
            np.array([xmid, ymax, zmid]),
            np.array([1, 0, 0]),
            np.array([0, 0, 1]),
            np.array([0, 1, 0]),
            width,
            height,
        ),
    ]


def clipped_view_factor(
    source,
    target_geometry,
    blockers,
    *,
    order,
    max_triangles,
    target_two_sided=True,
    extinction_m_inv=0.0,
):
    """Duffy/Gauss triangles on the analytically visible region for each emitter point."""
    sp, sn, sa = source
    center, u, v, normal = target_geometry[:4]
    nodes, weights = leggauss(order)
    a, b = np.meshgrid((nodes + 1) / 2, (nodes + 1) / 2, indexing="ij")
    a, b = a.ravel(), b.ravel()
    base_weights = np.outer(weights, weights).ravel() / 4
    total, evaluations, tests = 0.0, 0, 0
    for point, source_normal, source_area in zip(sp, sn, sa, strict=True):
        face = float(np.dot(normal, point - center))
        if (not target_two_sided and face <= 0) or abs(face) <= 1e-14:
            continue
        polygons, work = visible_receiver_polygons(
            point, source_normal, target_geometry, blockers, max_triangles
        )
        tests += work
        for polygon in polygons:
            first = polygon[0]
            for j in range(1, len(polygon) - 1):
                d1, d2 = polygon[j] - first, polygon[j + 1] - first
                determinant = abs(d1[0] * d2[1] - d1[1] * d2[0])
                uv = first + a[:, None] * d1 + ((1 - a) * b)[:, None] * d2
                points = center + uv[:, :1] * u + uv[:, 1:] * v
                delta = points - point
                distance2 = np.einsum("ij,ij->i", delta, delta)
                if np.any(distance2 <= 0):
                    raise ValueError("radiation surfaces contain coincident points")
                distance = np.sqrt(distance2)
                source_cos = np.maximum(0, delta @ source_normal / distance)
                target_cos = abs(face) / distance
                kernel = source_cos * target_cos / (np.pi * distance2)
                if extinction_m_inv:
                    kernel *= np.exp(-extinction_m_inv * distance)
                total += float(np.sum(kernel * base_weights * (1 - a) * determinant) * source_area)
                evaluations += order * order
    return total / float(sa.sum()), evaluations, tests
