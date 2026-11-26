"""Batched sample-point evaluation of the existing roof area-radiation kernel."""

from math import atan, cos, pi, radians, sin

import numpy as np
from shapely import STRtree, points

from pyfireca.planning.roof_thermal import _area_quadrature
from pyfireca.village.domain import ExposureEdges


class WindRadiationGeometry:
    """Reuse roof quadrature samples and the target index for one frozen physical layout."""

    def __init__(self, layout, integrated_ids, spacing_m=0.5, *, frozen_integrated_node_mask=None):
        if not np.isfinite(spacing_m) or spacing_m <= 0:
            raise ValueError("quadrature spacing must be positive and finite")
        self.spacing_m = spacing_m
        self.node_count = len(layout.owners)
        request = layout.thermal
        selected = frozenset(integrated_ids)
        if frozen_integrated_node_mask is None:
            available = {key for ids in layout.owners for key in ids}
            if not selected or not selected <= available:
                raise ValueError("area integration requires known selected roof IDs")
            integrated = np.asarray([bool(selected.intersection(ids)) for ids in layout.owners])
        else:
            integrated = np.asarray(frozen_integrated_node_mask)
            if integrated.dtype != np.bool_ or integrated.shape != (self.node_count,):
                raise ValueError("frozen integration mask must be boolean and match physical nodes")
        self.integrated = integrated
        src = request.source_mask.ravel()
        dst = request.target_mask.ravel()
        grid = layout.physical_request.grid
        counts = (src | dst).astype(int)
        samples = {}
        for node in np.flatnonzero(integrated & (src | dst)):
            samples[node] = _area_quadrature(
                layout.geometry[node], spacing_m, x0=grid.x_min_m, y0=grid.y_max_m
            )
            counts[node] = len(samples[node][0])
        end = counts.cumsum()
        self.node = np.repeat(np.arange(self.node_count, dtype=np.int64), counts)
        self.xy = layout.xy_m[self.node].copy()
        self.fraction = np.ones(len(self.node), dtype=float)
        for node, (xy, fractions) in samples.items():
            stop = end[node]
            region = slice(stop - len(fractions), stop)
            self.xy[region] = xy
            self.fraction[region] = fractions
        self.source_samples = np.flatnonzero(src[self.node])
        self.target_samples = np.flatnonzero(dst[self.node])
        self.points = points(self.xy)
        self.targets = STRtree(self.points[self.target_samples])
        physics = request.parameters
        ground = layout.physical_request.ground_elevation_m
        self.z = np.zeros(self.node_count) if ground is None else ground.ravel()[layout.cell_id]
        self.half_height = physics.source_height_m.ravel() / 2
        self.target_z = self.z + physics.receiver_height_m.ravel()
        self.receiver_area = physics.receiver_area_m2.ravel()

    def graph(self, transport, *, max_edges=5_000_000):
        tilt = atan(transport.wind_speed_m_s * transport.flame_tilt_coefficient_s_m)
        angle = radians((transport.wind_from_deg + 180) % 360)
        shift = self.half_height * sin(tilt)
        dx, dy = shift * sin(angle), shift * cos(angle)
        source_z = self.z + self.half_height * cos(tilt)
        radius = transport.exposure_radius_m
        # Dense roof interiors have roughly pi*R^2 / spacing^2 target samples
        # per source sample. Bound work arrays while retaining exact source order.
        density = max(1, pi * radius**2 / self.spacing_m**2)
        batch_size = max(1, min(512, int(200_000 / density)))
        keys, contributions = [], []
        for start in range(0, len(self.source_samples), batch_size):
            block = self.source_samples[start : start + batch_size]
            rows, columns = self.targets.query(
                self.points[block], predicate="dwithin", distance=radius
            )
            source_samples = block[rows]
            target_samples = self.target_samples[columns]
            source, target = self.node[source_samples], self.node[target_samples]
            ex = self.xy[target_samples, 0] - self.xy[source_samples, 0]
            ey = self.xy[target_samples, 1] - self.xy[source_samples, 1]
            keep = (source != target) & (ex**2 + ey**2 <= radius**2)
            if not keep.any():
                continue
            source, target = source[keep], target[keep]
            d2 = (
                (ex[keep] - dx[source]) ** 2
                + (ey[keep] - dy[source]) ** 2
                + (self.target_z[target] - source_z[source]) ** 2
            )
            denom = d2 + transport.near_field_regularization_m**2
            if np.any(denom <= 0):
                raise ValueError("singular roof radiation distance")
            kernel = np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(d2)) / (
                4 * pi * denom
            )
            fractions = self.fraction[source_samples[keep]] * self.fraction[target_samples[keep]]
            encoded, inverse = np.unique(source * self.node_count + target, return_inverse=True)
            keys.append(encoded)
            contributions.append(np.bincount(inverse, weights=kernel * fractions))
        if not keys:
            empty = np.array([], np.int32)
            return ExposureEdges(empty, empty, np.array([], float), self.node_count)
        # A node with multiple source samples may straddle adjacent batches.
        pairs, inverse = np.unique(np.concatenate(keys), return_inverse=True)
        if len(pairs) > max_edges:
            raise ValueError("roof radiation edge limit exceeded")
        source, target = np.divmod(pairs, self.node_count)
        weight = (
            np.bincount(inverse, weights=np.concatenate(contributions)) * self.receiver_area[target]
        )
        keep = (weight > 0) | ~(self.integrated[source] | self.integrated[target])
        return ExposureEdges(source[keep], target[keep], weight[keep], self.node_count)


def wind_radiation_graph(
    layout,
    transport,
    integrated_ids,
    spacing_m=0.5,
    *,
    max_edges=5_000_000,
    frozen_integrated_node_mask=None,
):
    geometry = WindRadiationGeometry(
        layout, integrated_ids, spacing_m, frozen_integrated_node_mask=frozen_integrated_node_mask
    )
    return geometry.graph(transport, max_edges=max_edges)
