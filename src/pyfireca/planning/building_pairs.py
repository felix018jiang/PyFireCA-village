"""Outcome-independent geometric pair selection and controlled thermal isolation.

Building outlines are observations; timber assemblies and fuels remain those of
the supplied case. Disabling third structures is a laboratory-style mechanism
control, never a proposed village intervention or a full-neighborhood forecast.
"""

from dataclasses import replace

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from pyfireca.village.domain import ExposureEdges
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges


def select_building_pairs(bundle, structures, *, minimum_cells=4):
    """One closest-to-bin-center pair per gap bin and observed raster fuel class.

    No ignition outcome is inspected. Bins straddle the supplied grid scale;
    missing strata stay missing rather than being filled with favorable fires.
    """
    request, grid = bundle.request, bundle.request.grid
    owners = {value: int(key) for key, value in bundle.metadata["building_labels"].items()}
    polygons = {
        row["building_id"]: unary_union(
            [Polygon(p["exterior"], p["holes"]) for p in row["polygons"]]
        )
        for row in structures
        if row["properties"]["source_label"] == "building"
        and row["building_id"] in owners
        and np.count_nonzero(request.building_index == owners[row["building_id"]]) >= minimum_cells
    }
    cells = {key: np.argwhere(request.building_index == owners[key]) for key in polygons}
    candidates = []
    values = sorted(polygons.items())
    for i, (left, a) in enumerate(values):
        for right, b in values[i + 1 :]:
            gap = a.distance(b)
            if not 2 <= gap < 20:
                continue
            start, end = nearest_points(a, b)
            center = (np.array(start.coords[0]) + np.array(end.coords[0])) / 2
            direction = np.array(end.coords[0]) - np.array(start.coords[0])
            normal = np.array([-direction[1], direction[0]]) / gap
            # A 20 m transverse test band, independent of simulated performance.
            points = (tuple(center - 10 * normal), tuple(center + 10 * normal))
            probe = LineString([start, end]).buffer(grid.cell_size_m / 2)
            ground, vegetation = 0, 0
            aa, bb = cells[left], cells[right]
            low = np.minimum(aa.min(0), bb.min(0)) - 2
            high = np.maximum(aa.max(0), bb.max(0)) + 3
            for row in range(max(0, low[0]), min(grid.height, high[0])):
                for col in range(max(0, low[1]), min(grid.width, high[1])):
                    if (
                        probe.covers(Point(grid.center(row, col)))
                        and request.building_index[row, col] < 0
                    ):
                        ground += 1
                        vegetation += int(request.burnable_mask[row, col])
            fuel_class = "vegetated_gap" if vegetation else "no_mapped_gap_fuel"
            bin_id = "2-5m" if gap < 5 else "5-10m" if gap < 10 else "10-20m"
            target = {"2-5m": 3.5, "5-10m": 7.5, "10-20m": 15}[bin_id]
            raster_gap = float(
                np.sqrt(np.min(np.sum((aa[:, None] - bb[None, :]) ** 2, axis=2))) * grid.cell_size_m
            )
            candidates.append(
                dict(
                    pair_id=f"{left}-{right}",
                    building_ids=[left, right],
                    owner_ids=[owners[left], owners[right]],
                    polygon_gap_m=gap,
                    raster_min_center_distance_m=raster_gap,
                    gap_bin=bin_id,
                    fuel_class=fuel_class,
                    probe_ground_cells=ground,
                    probe_vegetation_cells=vegetation,
                    gap_ground_resolved=ground > 0,
                    band_points_m=[list(p) for p in points],
                    selection_distance=abs(gap - target),
                )
            )
    selected = []
    used = set()
    for gap_bin in ("2-5m", "5-10m", "10-20m"):
        for fuel_class in ("no_mapped_gap_fuel", "vegetated_gap"):
            eligible = sorted(
                (
                    c
                    for c in candidates
                    if c["gap_bin"] == gap_bin and c["fuel_class"] == fuel_class
                ),
                key=lambda c: (
                    bool(set(c["building_ids"]) & used),
                    c["selection_distance"],
                    c["pair_id"],
                ),
            )
            if eligible:
                selected.append(eligible[0])
                used.update(eligible[0]["building_ids"])
    return selected, candidates


def isolate_pair_request(
    bundle, pair, source_owner, transport, *, ground_enabled, dt_s=1.0, source_profile="single_cell"
):
    """Keep two real raster buildings; optionally retain nearby mapped ground.

    A fixed 20 m padding around their bounding rectangle contains all enabled
    fuel. The bundle crop must provide a further 20 m nonburning guard. This is
    controlled fuel support, not truncation of an unreported neighborhood fire.
    """
    request = bundle.request
    pair_mask = np.isin(request.building_index, pair["owner_ids"])
    cells = np.argwhere(pair_mask)
    low, high = cells.min(0), cells.max(0) + 1
    pad = int(np.ceil(20 / request.grid.cell_size_m))
    support = np.zeros(request.grid.shape, dtype=bool)
    support[max(0, low[0] - pad) : high[0] + pad, max(0, low[1] - pad) : high[1] + pad] = True
    active = pair_mask | (ground_enabled & support & (request.building_index < 0))
    burnable = request.burnable_mask & active
    source_cells = np.argwhere(request.building_index == source_owner)
    receiver_cells = np.argwhere(pair_mask & (request.building_index != source_owner))
    # Most exposed source cell is selected from geometry only, not trial outcomes.
    distances = np.min(
        np.sum((source_cells[:, None] - receiver_cells[None, :]) ** 2, axis=2), axis=1
    )
    row, col = source_cells[int(np.argmin(distances))]
    if source_profile not in {"single_cell", "source_roof_cells"}:
        raise ValueError("unknown source ignition profile")
    events = (
        (VillageIgnitionEvent(0, int(row), int(col)),)
        if source_profile == "single_cell"
        else tuple(VillageIgnitionEvent(0, int(r), int(c)) for r, c in source_cells)
    )
    edges = build_v3_radiative_transport_edges(
        request.grid,
        burnable,
        burnable,
        request.parameters.receiver_area_m2,
        request.parameters.source_height_m,
        request.parameters.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
    )
    isolated = replace(
        request,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=burnable,
        piloted_ignition_mask=request.piloted_ignition_mask & burnable,
        exposure_edges=ExposureEdges(edges.source, edges.target, edges.weight, edges.cell_count),
        scenario=replace(
            request.scenario,
            max_dt_s=dt_s,
            end_time_s=5400,
            ignitions=events,
        ),
        sample_interval_s=None,
        firebrands=None,
    )
    return isolated
