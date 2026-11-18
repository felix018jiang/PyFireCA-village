"""Rebuild observed pair outlines at fixed metric meshes; preserve the CA kernel.

This is controlled numerical verification, not measured narrow-alley validation.
Ground classes inherit the 5 m map; newly exposed subcell gaps stay explicitly
unobserved/inert. A separate synthetic grass-bridge control tests fuel removal.
"""

from __future__ import annotations

import argparse
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import shapely
from run_batuan_building_pairs import BASE, ROOT, dump, sha
from shapely.geometry import LineString, Polygon
from shapely.ops import nearest_points, unary_union

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.interventions.clearing import EXTENSIVE
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import V3SimulationRequest, run_v3_simulation
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

PARENT = ROOT / "runs/research/batuan-building-pairs-20261017-r3"
PAIRS = ("B0163-B0180", "B0329-B0330", "B0289-B0290")


def bilinear(values, rows, cols):
    """Sample inherited cell-center DEM, with clamped edge coordinates."""
    rows = np.clip(rows, 0, values.shape[0] - 1)
    cols = np.clip(cols, 0, values.shape[1] - 1)
    r0, c0 = rows.astype(int), cols.astype(int)
    r1 = np.minimum(r0 + 1, values.shape[0] - 1)
    c1 = np.minimum(c0 + 1, values.shape[1] - 1)
    dr, dc = rows - r0, cols - c0
    return (
        (1 - dr) * (1 - dc) * values[r0, c0]
        + dr * (1 - dc) * values[r1, c0]
        + (1 - dr) * dc * values[r0, c1]
        + dr * dc * values[r1, c1]
    )


def outline(row):
    return unary_union([Polygon(p["exterior"], p["holes"]) for p in row["polygons"]])


def geometry_grid(grid):
    rr, cc = np.indices(grid.shape)
    left = grid.x_min_m + cc * grid.cell_size_m
    top = grid.y_max_m - rr * grid.cell_size_m
    return shapely.box(left, top - grid.cell_size_m, left + grid.cell_size_m, top)


def clear_local_ground(request):
    """Remove ground fuel completely while preserving all building arrays."""
    keep = request.building_index >= 0
    active = request.burnable_mask & keep
    fields = {
        name: np.where(keep, getattr(request.parameters, name), 0.0)
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    edges = request.exposure_edges
    allowed = active.ravel()[edges.source] & active.ravel()[edges.target]
    return replace(
        request,
        parameters=V3CellPhysicsArrays(**fields),
        burnable_mask=active,
        source_mask=active,
        target_mask=active,
        material_index=np.where(keep, request.material_index, 0),
        landcover_index=np.where(keep, request.landcover_index, 0),
        piloted_ignition_mask=request.piloted_ignition_mask & active,
        exposure_edges=ExposureEdges(
            edges.source[allowed], edges.target[allowed], edges.weight[allowed], edges.cell_count
        ),
    )


def density_fields(request, mask):
    """Require uniform per-area assembly; never fit to a fire outcome."""
    result = {}
    area = request.parameters.covered_area_m2[mask]
    if not len(area) or np.any(area <= 0):
        raise ValueError("reference assembly requires positive covered cells")
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        if name in {"coverage_fraction", "covered_area_m2"}:
            continue
        values = getattr(request.parameters, name)[mask]
        if name in EXTENSIVE:
            values = values / area
        value = float(np.median(values))
        if not np.allclose(values, value, rtol=1e-9, atol=1e-9):
            raise ValueError(f"nonuniform reference assembly: {name}")
        result[name] = value
    return result


def rebuild(bundle, pair, structures, spacing, ground_mode):
    old = bundle.request
    polygons = {p["building_id"]: outline(p) for p in structures}
    a, b = [polygons[k] for k in pair["building_ids"]]
    bounds = unary_union([a, b]).bounds
    # Common physical domain and support independent of mesh. 20 m inert guard.
    xmin = np.floor((bounds[0] - 45) / 5) * 5
    ymax = np.ceil((bounds[3] + 45) / 5) * 5
    xmax = np.ceil((bounds[2] + 45) / 5) * 5
    ymin = np.floor((bounds[1] - 45) / 5) * 5
    grid = VillageGridSpec(
        round((ymax - ymin) / spacing),
        round((xmax - xmin) / spacing),
        spacing,
        xmin,
        ymax,
        "projected",
        old.grid.crs,
    )
    boxes = geometry_grid(grid)
    rr, cc = np.indices(grid.shape)
    xx = xmin + (cc + 0.5) * spacing
    yy = ymax - (rr + 0.5) * spacing
    oldr = np.floor((old.grid.y_max_m - yy) / old.grid.cell_size_m).astype(int)
    oldc = np.floor((xx - old.grid.x_min_m) / old.grid.cell_size_m).astype(int)
    if (
        oldr.min() < 0
        or oldc.min() < 0
        or oldr.max() >= old.grid.height
        or oldc.max() >= old.grid.width
    ):
        raise ValueError("local domain outside input map")
    lookup = (oldr, oldc)
    areas = np.stack([shapely.area(shapely.intersection(boxes, p)) for p in [a, b]])
    maximum = areas.max(axis=0)
    owner = np.full(grid.shape, -1, dtype=np.int32)
    for i, number in enumerate(pair["owner_ids"]):
        owner[(areas.argmax(axis=0) == i) & (maximum > 1e-10)] = number
    # Other protected structures are inert controls, never cleared or proposed removed.
    all_local = unary_union(
        [p for p in polygons.values() if p.intersects(shapely.box(*bounds).buffer(25))]
    )
    protected = shapely.area(shapely.intersection(boxes, all_local)) > 1e-10
    fields = {name: np.zeros(grid.shape) for name in V3CellPhysicsArrays.__dataclass_fields__}
    material = np.zeros(grid.shape, dtype=np.int16)
    landcover = material.copy()

    def assign(mask, area, density):
        fields["covered_area_m2"][mask] = area
        fields["coverage_fraction"][mask] = area / grid.cell_area_m2
        for name, value in density.items():
            fields[name][mask] = value * area if name in EXTENSIVE else value

    totals = []
    for i, number in enumerate(pair["owner_ids"]):
        m = owner == number
        assign(m, areas[i][m], density_fields(old, old.building_index == number))
        material[m] = 5
        landcover[m] = 5
        totals.append(
            {
                "owner": number,
                "vector_area_m2": [a, b][i].area,
                "mesh_area_m2": float(areas[i][m].sum()),
                "peak_hrr_W": float(fields["peak_hrr_W"][m].sum()),
            }
        )
    support = shapely.box(*bounds).buffer(20, cap_style="square", join_style="mitre")
    ground = (~protected) & shapely.covers(support, shapely.points(xx, yy))
    unresolved = ground & (old.building_index[lookup] >= 0)
    if ground_mode == "mapped":
        for code in np.unique(old.material_index[lookup][ground & ~unresolved]):
            reference = (old.material_index == code) & (old.building_index < 0) & old.burnable_mask
            if not reference.any():
                continue
            m = ground & ~unresolved & (old.material_index[lookup] == code)
            assign(m, np.full(m.sum(), grid.cell_area_m2), density_fields(old, reference))
            material[m] = code
            landcover[m] = int(old.landcover_index[reference][0])
    elif ground_mode == "synthetic_grass_bridge":
        # Explicit hypothetical fuel bridge, not satellite-derived alley material.
        start, end = nearest_points(a, b)
        bridge = LineString([start, end]).buffer(3, cap_style="square")
        m = ground & shapely.intersects(boxes, bridge)
        reference = (old.landcover_index == 1) & (old.building_index < 0) & old.burnable_mask
        assign(m, np.full(m.sum(), grid.cell_area_m2), density_fields(old, reference))
        material[m] = 1
        landcover[m] = 1
    elif ground_mode != "none":
        raise ValueError("unsupported ground mode")
    physics = V3CellPhysicsArrays(**fields).validated(grid.shape, grid.cell_area_m2)
    burnable = (physics.pyrolysable_dry_fuel_mass_kg > 0) & (physics.peak_hrr_W > 0)
    z = bilinear(
        old.ground_elevation_m,
        (old.grid.y_max_m - yy) / old.grid.cell_size_m - 0.5,
        (xx - old.grid.x_min_m) / old.grid.cell_size_m - 0.5,
    )
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    edges = build_v3_radiative_transport_edges(
        grid,
        burnable,
        burnable,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        transport,
        ground_elevation_m=z,
    )
    source = np.argwhere(owner == pair["owner_ids"][0])
    request = V3SimulationRequest(
        grid,
        np.ones(grid.shape, bool),
        np.ones(grid.shape, bool),
        burnable,
        burnable,
        burnable,
        burnable,
        material,
        landcover,
        owner,
        physics,
        ExposureEdges(edges.source, edges.target, edges.weight, grid.height * grid.width),
        replace(
            old.scenario,
            end_time_s=5400,
            max_dt_s=1,
            ignitions=tuple(VillageIgnitionEvent(0, int(r), int(c)) for r, c in source),
        ),
        ground_elevation_m=z,
        flame_contact=old.flame_contact,
    )
    face_contacts = 0
    for u, v in [(owner[:-1], owner[1:]), (owner[:, :-1], owner[:, 1:])]:
        face_contacts += int(
            (
                ((u == pair["owner_ids"][0]) & (v == pair["owner_ids"][1]))
                | ((v == pair["owner_ids"][0]) & (u == pair["owner_ids"][1]))
            ).sum()
        )
    audit = {
        "spacing_m": spacing,
        "building_area": totals,
        "mixed_pair_cells": int(((areas > 1e-10).sum(axis=0) > 1).sum()),
        "direct_building_face_pairs": face_contacts,
        "unobserved_gap_ground_cells": int(unresolved.sum()),
        "ground_mode": ground_mode,
        "active_ground_area_m2": float(physics.covered_area_m2[owner < 0].sum()),
        "edge_count": len(edges.source),
        "request_sha256": request_fingerprint(request),
    }
    return request, audit, transport


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    parent = json.loads((PARENT / "protocol.json").read_text())
    structures = json.loads((PARENT / "buildings.source.json").read_text())["buildings"]
    bundle = load_v3_case_bundle(BASE)
    protocol = {
        "schema": "batuan-local-mesh-1",
        "pairs": [p for p in parent["selected"] if p["pair_id"] in PAIRS],
        "spacings_m": [5, 2.5, 1],
        "ground_modes": ["none", "mapped", "synthetic_grass_bridge"],
        "source_profile": "all source-building roof cells at t=0; fixed per-area assembly",
        "horizon_s": 5400,
        "dt_s": 1,
        "extra_controls": {
            "pair": PAIRS[0],
            "one_metre": ["dt_0.5s_both", "dt_1s_radiation_only", "dt_1s_contact_only"],
            "half_metre": "no ground, both directions, dt=1s, both channels",
        },
        "firebrands": False,
        "terrain": "bilinear inherited ~30m FABDEM; finer mesh is not finer terrain evidence",
        "ground_evidence": "5m classes inherited; exposed old building subcells inert/unobserved",
        "regularization": "fixed 5m physical radiation regularizer, not reduced with mesh",
        "isolation": "two active buildings + ground in box +20m; inert domain guard >=20m",
        "clearance": "all active local ground removed; upper-bound control; no opaque wall",
        "scope": "controlled numerical verification, not field-calibrated safe spacing",
        "source_hashes": {
            str(p.relative_to(ROOT)): sha(p)
            for p in [
                *ROOT.joinpath("src/pyfireca/village").glob("*.py"),
                *ROOT.joinpath("src/pyfireca/planning").rglob("*.py"),
                Path(__file__),
            ]
        },
        "input_hashes": {
            str(p): sha(p)
            for p in [*BASE.glob("*"), PARENT / "buildings.source.json", PARENT / "protocol.json"]
            if p.is_file()
        },
    }
    dump(output / "protocol.json", protocol)
    for name in protocol["source_hashes"]:
        dest = output / "source_snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    shutil.copy2(PARENT / "buildings.source.json", output / "buildings.source.json")
    cases = [
        (pair, spacing, mode)
        for pair in protocol["pairs"]
        for spacing in protocol["spacings_m"]
        for mode in protocol["ground_modes"]
    ]
    cases.append((next(p for p in protocol["pairs"] if p["pair_id"] == PAIRS[0]), 0.5, "none"))
    for pair, spacing, mode in cases:
        path = output / "cases" / f"{pair['pair_id']}-{spacing:g}m-{mode}"
        request, audit, transport = rebuild(bundle, pair, structures, spacing, mode)
        write_v3_case_bundle(
            request,
            path,
            metadata={
                "mesh_audit": audit,
                "transport_parameters": {
                    k: getattr(transport, k) for k in transport.__dataclass_fields__
                },
            },
        )
        print(json.dumps({"prepared": str(path), **audit}), flush=True)
    dump(
        output / "mesh-inputs.json",
        {
            str(p.relative_to(output)): sha(p)
            for p in sorted((output / "cases").rglob("*"))
            if p.is_file()
        },
    )


def run_one(task):
    output, pair, spacing, mode, source, cleared, dt, channel = task
    key = (
        f"{pair['pair_id']}-{spacing:g}m-{mode}-from{source}-clear{int(cleared)}-dt{dt:g}-{channel}"
    )
    dest = output / "records" / f"{key}.json"
    digest = sha(output / "protocol.json")
    if dest.exists():
        record = json.loads(dest.read_text())
        if record["protocol_sha256"] != digest:
            raise ValueError("cache protocol mismatch")
        return record
    bundle = load_v3_case_bundle(output / "cases" / f"{pair['pair_id']}-{spacing:g}m-{mode}")
    q = bundle.request
    if cleared:
        # Whole local ground removal is an upper-bound effect control. Buildings untouched.
        q = clear_local_ground(q)
    events = tuple(
        VillageIgnitionEvent(0, int(r), int(c)) for r, c in np.argwhere(q.building_index == source)
    )
    q = replace(q, scenario=replace(q.scenario, max_dt_s=dt, ignitions=events))
    if channel == "radiation_only":
        q = replace(q, flame_contact=None)
    if channel == "contact_only":
        q = replace(
            q,
            exposure_edges=ExposureEdges(
                np.array([], dtype=np.int64),
                np.array([], dtype=np.int64),
                np.array([], dtype=float),
                q.exposure_edges.cell_count,
            ),
        )
    result = run_v3_simulation(q, retain_snapshots=False)
    target = next(i for i in pair["owner_ids"] if i != source)
    mask = q.building_index == target
    t = result.final_state.ignition_time_s[mask]
    t = t[np.isfinite(t)]
    guard = np.isfinite(result.final_state.ignition_time_s)
    record = {
        "key": key,
        "pair_id": pair["pair_id"],
        "spacing_m": spacing,
        "ground_mode": mode,
        "source_owner": source,
        "receiver_owner": target,
        "cleared": cleared,
        "dt_s": dt,
        "channel": channel,
        "protocol_sha256": digest,
        "request_sha256": request_fingerprint(q),
        "first_receiver_ignition_s": float(t.min()) if len(t) else None,
        "ignited_receiver_area_m2": float(
            q.parameters.covered_area_m2[mask][
                np.isfinite(result.final_state.ignition_time_s[mask])
            ].sum()
        ),
        "removed_ground_area_m2": bundle.metadata["mesh_audit"]["active_ground_area_m2"]
        if cleared
        else 0,
        "actual_horizon_s": result.final_state.time_s,
        "guard_ignited": bool(guard[[0, -1], :].any() or guard[:, [0, -1]].any()),
        "receiver_radiation_J": float(result.final_state.absorbed_radiation_J[mask].sum()),
        "receiver_contact_J": float(result.final_state.received_convection_J[mask].sum()),
        "mesh_audit": bundle.metadata["mesh_audit"],
        "validation": check_final(q, result),
    }
    dest.parent.mkdir(exist_ok=True)
    dump(dest, record)
    print(
        json.dumps(
            {k: record[k] for k in ["key", "first_receiver_ignition_s", "actual_horizon_s"]}
        ),
        flush=True,
    )
    return record


def run(output, workers):
    protocol = json.loads((output / "protocol.json").read_text())
    for name, h in {**protocol["source_hashes"], **protocol["input_hashes"]}.items():
        if sha(ROOT / name) != h:
            raise ValueError(f"frozen input changed: {name}")
    mesh_inputs = json.loads((output / "mesh-inputs.json").read_text())
    for name, h in mesh_inputs.items():
        if sha(output / name) != h:
            raise ValueError(f"frozen mesh changed: {name}")
    tasks = [
        (output, p, s, m, source, cleared, 1.0, "both")
        for p in protocol["pairs"]
        for s in protocol["spacings_m"]
        for m in protocol["ground_modes"]
        for source in p["owner_ids"]
        for cleared in ([False] if m == "none" else [False, True])
    ]
    # Prespecified timestep and channel controls on the finest narrowest pair.
    p = next(p for p in protocol["pairs"] if p["pair_id"] == PAIRS[0])
    tasks.extend(
        (output, p, 1, "none", source, False, dt, channel)
        for source in p["owner_ids"]
        for dt, channel in [(0.5, "both"), (1.0, "radiation_only"), (1.0, "contact_only")]
    )
    tasks.extend((output, p, 0.5, "none", source, False, 1.0, "both") for source in p["owner_ids"])
    with ProcessPoolExecutor(workers) as pool:
        records = list(pool.map(run_one, tasks))
    for name, h in {**protocol["source_hashes"], **protocol["input_hashes"]}.items():
        if sha(ROOT / name) != h:
            raise ValueError(f"frozen input changed during study: {name}")
    for name, h in mesh_inputs.items():
        if sha(output / name) != h:
            raise ValueError(f"frozen mesh changed during study: {name}")
    if any(abs(r["actual_horizon_s"] - 5400) > 1e-8 or r["guard_ignited"] for r in records):
        raise AssertionError("incomplete or boundary-affected experiment")
    dump(
        output / "report.json",
        {
            "protocol_sha256": sha(output / "protocol.json"),
            "records": records,
            "all_full_horizon": True,
            "any_guard_ignition": False,
            "count": len(records),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["prepare", "run"], required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    prepare(args.output.resolve()) if args.phase == "prepare" else run(
        args.output.resolve(), args.workers
    )
