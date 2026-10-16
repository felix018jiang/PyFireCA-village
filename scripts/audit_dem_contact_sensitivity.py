"""Diagnose DEM precision and discontinuous contact gates; never change defaults.

Terrain perturbations affect contact only. Frozen radiation edges deliberately
isolate the contact mechanism; these are not self-consistent terrain scenarios.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def graph(request):
    return FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )


def contact_height(request, delta):
    overrides = dict(request.flame_contact.receiver_height_overrides_m)
    overrides[1] = (
        float(request.parameters.receiver_height_m[request.material_index == 1][0]) + delta
    )
    return replace(
        request,
        flame_contact=replace(
            request.flame_contact,
            receiver_height_overrides_m=tuple(overrides.items()),
        ),
    )


def audit(folder, output):
    output.mkdir(parents=True, exist_ok=False)
    # Resolve the actual case path from the acceptance report, not an assumed name.
    report = json.loads((folder / "report.json").read_text())
    full_case = Path(report["stages"]["full_village"][0]["case"])
    full_bundle = load_v3_case_bundle(full_case)
    full = full_bundle.request
    crop = load_v3_case_bundle(folder / "crops/grass/base").request
    ground = full.ground_elevation_m
    original = graph(full)
    elevation_info = full_bundle.metadata["ground_elevation"]
    dem_path = Path(elevation_info["path"])
    with rasterio.open(dem_path) as dataset:
        raster = dataset.read(1).astype(np.float64)
        dem = {
            "path": str(dem_path),
            "tags": dataset.tags(),
            "dtype": dataset.dtypes[0],
            "resolution_m": list(dataset.res),
            "case_matches_raster": bool(np.array_equal(raster, ground)),
            "sha256": hashlib.sha256(dem_path.read_bytes()).hexdigest(),
        }
    height = full.parameters.receiver_height_m.copy()
    for material, value in full.flame_contact.receiver_height_overrides_m:
        height[full.material_index == material] = value
    margins, grass_margins = [], []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        sr = slice(max(0, -dr), min(full.grid.height, full.grid.height - dr))
        sc = slice(max(0, -dc), min(full.grid.width, full.grid.width - dc))
        tr = slice(max(0, dr), min(full.grid.height, full.grid.height + dr))
        tc = slice(max(0, dc), min(full.grid.width, full.grid.width + dc))
        valid = full.burnable_mask[sr, sc] & full.burnable_mask[tr, tc]
        # Signed distance inside the column; negative means an excluded point.
        relative_z = ground[tr, tc] - ground[sr, sc] + height[tr, tc]
        margin = np.minimum(relative_z, full.parameters.source_height_m[sr, sc] - relative_z)
        margins.append(margin[valid])
        grasses = valid & (full.material_index[sr, sc] == 1) & (full.material_index[tr, tc] == 1)
        grass_margins.append(margin[grasses])
    all_margins, gm = np.concatenate(margins), np.concatenate(grass_margins)
    boundary_counts = [
        {
            "distance_m": eps,
            "all_directed_pairs": int((np.abs(all_margins) <= eps).sum()),
            "grass_directed_pairs": int((np.abs(gm) <= eps).sum()),
            "excluded_pairs": int(((all_margins < 0) & (all_margins >= -eps)).sum()),
        }
        for eps in (1e-5, 1e-4, 1e-3, 0.01, 0.05, 0.1)
    ]
    source = crop.scenario.ignitions[0]
    row, col = source.row, source.col
    sid = row * crop.grid.width + col
    neighbors = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = row + dr, col + dc
        dz = crop.ground_elevation_m[nr, nc] - crop.ground_elevation_m[row, col]
        h = crop.parameters.receiver_height_m[nr, nc]
        neighbors.append(
            {
                "crop_row_col": [nr, nc],
                "terrain_delta_m": float(dz),
                "point_above_base_m": float(dz + h),
                "point_above_top_m": float(dz + h - crop.parameters.source_height_m[row, col]),
            }
        )
    graph_rows = []
    for delta in (-0.001, -0.0001, 0, 0.0001, 0.001):
        op = graph(contact_height(full, delta))
        graph_rows.append(
            {
                "grass_height_delta_m": delta,
                "directed_edges": int((op.neighbors >= 0).sum()),
                "changed_edge_slots": int((op.neighbors != original.neighbors).sum()),
            }
        )
    band_graph_rows = []
    for extent in (0, 0.01, 0.15):
        op = graph(
            replace(
                full,
                flame_contact=replace(
                    full.flame_contact,
                    receiver_vertical_half_extent_m=extent,
                ),
            )
        )
        has_edge = np.any(op.neighbors >= 0, axis=1).reshape(full.grid.shape)
        band_graph_rows.append(
            {
                "half_extent_m": extent,
                "directed_edges": int((op.neighbors >= 0).sum()),
                "grass_cells_without_outgoing_contact": int(
                    (full.burnable_mask & (full.material_index == 1) & ~has_edge).sum()
                ),
            }
        )
    datum = graph(replace(full, ground_elevation_m=ground - 500))
    z = crop.ground_elevation_m
    rng = np.random.default_rng(20261007)
    noise = rng.uniform(-1, 1, size=z.shape)
    experiments = [
        ("baseline", crop),
        ("float64_datum_minus_500m", replace(crop, ground_elevation_m=z - 500)),
    ]
    for delta in (-0.001, -0.0001, -0.00001, 0.00001, 0.0001, 0.001):
        experiments.append((f"grass_receiver_delta_{delta:g}m", contact_height(crop, delta)))
    for amplitude in (0.0001, 0.001, 0.01):
        for sign in (-1, 1):
            experiments.append(
                (
                    f"contact_dem_noise_{sign * amplitude:g}m",
                    replace(crop, ground_elevation_m=z + sign * amplitude * noise),
                )
            )
    for extent in (0.0001, 0.01, 0.15):
        experiments.append(
            (
                f"receiver_band_half_{extent:g}m",
                replace(
                    crop,
                    flame_contact=replace(
                        crop.flame_contact, receiver_vertical_half_extent_m=extent
                    ),
                ),
            )
        )
    band = replace(crop.flame_contact, receiver_vertical_half_extent_m=0.15)
    for amplitude in (0.0001, 0.001, 0.01):
        for sign in (-1, 1):
            experiments.append(
                (
                    f"band_0.15m_contact_dem_noise_{sign * amplitude:g}m",
                    replace(
                        crop, flame_contact=band, ground_elevation_m=z + sign * amplitude * noise
                    ),
                )
            )
    for quantum in (0.01, 0.1):
        experiments.append(
            (
                f"contact_dem_round_{quantum:g}m",
                replace(crop, ground_elevation_m=np.round(z / quantum) * quantum),
            )
        )
    results = []
    for name, request in experiments:
        op = graph(request)
        result = run_v3_simulation(request, retain_snapshots=False)
        results.append(
            {
                "name": name,
                "max_dt_s": request.scenario.max_dt_s,
                "source_edges": int((op.neighbors[sid] >= 0).sum()),
                "source_geometry_weights": op.geometry_fraction[sid].tolist(),
                **check_final(request, result),
            }
        )
        print(
            json.dumps({k: results[-1][k] for k in ("name", "source_edges", "ignited_cells")}),
            flush=True,
        )
    for name, request in (experiments[3], experiments[5], experiments[16]):
        request = replace(request, scenario=replace(request.scenario, max_dt_s=0.25))
        result = run_v3_simulation(request, retain_snapshots=False)
        results.append({"name": name, "max_dt_s": 0.25, **check_final(request, result)})
        print(
            json.dumps({k: results[-1][k] for k in ("name", "max_dt_s", "ignited_cells")}),
            flush=True,
        )
    record = {
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "acceptance_report_sha256": hashlib.sha256(
            (folder / "report.json").read_bytes()
        ).hexdigest(),
        "full_case": str(full_case.resolve()),
        "crop_case": str((folder / "crops/grass/base").resolve()),
        "dem": dem,
        "float32_exact_in_case": bool(
            np.array_equal(ground, ground.astype(np.float32).astype(np.float64))
        ),
        "source_float32_spacing_m": float(np.spacing(np.float32(z[row, col]))),
        "source_ground_m": float(z[row, col]),
        "source_height_m": float(crop.parameters.source_height_m[row, col]),
        "source_receiver_height_m": float(crop.parameters.receiver_height_m[row, col]),
        "neighbor_geometry": neighbors,
        "burnable_directed_pairs": len(all_margins),
        "grass_directed_pairs": len(gm),
        "boundary_counts": boundary_counts,
        "full_graph_height_controls": graph_rows,
        "full_graph_band_controls": band_graph_rows,
        "full_graph_float64_datum_changed_edge_slots": int(
            (datum.neighbors != original.neighbors).sum()
        ),
        "crop_simulations": results,
        "seed": 20261007,
        "duration_s": crop.scenario.end_time_s,
        "radiation_edges_frozen": True,
        "defaults_modified": False,
        "scope": (
            "contact-only numerical diagnosis; noise is hypothetical, "
            "not measured DEM uncertainty; crop omits external sources"
        ),
    }
    (output / "report.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation", type=Path, default=ROOT / "runs/gaoyou-core-validation-20261006"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.validation, args.output)
