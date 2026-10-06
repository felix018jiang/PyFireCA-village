"""Freeze an existing DEM bundle and short cropped runs without rebuilding edges.

Run from the repository with PYTHONPATH=src. Outputs must use a new directory.
The report checks file identity and compatibility, not physical calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path

import numpy as np

from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): file_hash(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def audit(case_dir: Path, output: Path, row: int, col: int) -> dict:
    if output.exists():
        raise FileExistsError(f"audit output already exists: {output}")
    # Inventory first, then use the production loader (including its DEM hash check).
    before = inventory(case_dir)
    case = load_v3_case_bundle(case_dir)
    request = case.request
    if request.ground_elevation_m is None:
        raise ValueError("W00 terrain audit requires a DEM bundle")
    if not (0 <= row < request.grid.height and 0 <= col < request.grid.width):
        raise ValueError("audit ignition is outside the grid")
    if not request.burnable_mask[row, col]:
        raise ValueError("audit ignition must be burnable")
    output.mkdir(parents=True)
    elevation = request.ground_elevation_m
    outgoing = np.bincount(
        request.exposure_edges.source,
        weights=request.exposure_edges.weight,
        minlength=request.grid.height * request.grid.width,
    )
    if np.max(outgoing) > 1 + 1e-12:
        raise ValueError("source radiation budget exceeds one")
    report = {
        "schema_version": "village-w00-baseline-1",
        "case_dir": str(case_dir.resolve()),
        "input_file_sha256": before,
        "grid": {"height": request.grid.height, "width": request.grid.width},
        "edge_count": len(request.exposure_edges.source),
        "maximum_outgoing_fraction": float(np.max(outgoing)),
        "dem_min_m": float(np.min(elevation)),
        "dem_max_m": float(np.max(elevation)),
        "dem_canonical_sha256": hashlib.sha256(
            np.ascontiguousarray(elevation, dtype="<f8").tobytes()
        ).hexdigest(),
        "builder_accepts_dem": "ground_elevation_m"
        in inspect.signature(build_v3_radiative_transport_edges).parameters,
        "boundary_policy": "cropped internal edges only; not a full-domain equivalent run",
        "runs": [],
    }
    root = Path(__file__).resolve().parents[1]
    report["source_file_sha256"] = {
        str(path.relative_to(root)): file_hash(path)
        for path in sorted((root / "src/pyfireca/village").rglob("*.py"))
    }
    # Three short runs: repeatability, then a changed ignition and duration.
    r0, r1 = max(0, row - 8), min(request.grid.height, row + 9)
    c0, c1 = max(0, col - 8), min(request.grid.width, col + 9)
    local_burnable = request.burnable_mask[r0:r1, c0:c1]
    candidates = [tuple(map(int, p)) for p in np.argwhere(local_burnable)]
    first = (row - r0, col - c0)
    second = next((point for point in candidates if point != first), None)
    if second is None:
        raise ValueError("audit crop requires two burnable cells")
    for name, point, duration in (
        ("repeat-a", first, 30.0),
        ("repeat-b", first, 30.0),
        ("changed-ignition", second, 17.0),
    ):
        bundle = crop_v3_case_bundle(
            case_dir,
            output / name / "case",
            row_start=r0,
            row_stop=r1,
            col_start=c0,
            col_stop=c1,
            end_time_s=duration,
            sample_interval_s=5.0,
            ignition=VillageIgnitionEvent(0.0, *point),
        )
        cropped = load_v3_case_bundle(bundle).request
        np.testing.assert_array_equal(cropped.ground_elevation_m, elevation[r0:r1, c0:c1])
        start = time.perf_counter()
        result, run_dir = run_v3_case_bundle(bundle, output / name / "run", chunk_frames=2)
        if result.final_state.time_s != duration or result.ignition_cause[point] != 1:
            raise AssertionError("selected ignition/duration was not applied")
        report["runs"].append(
            {
                "name": name,
                "local_ignition": list(point),
                "end_time_s": duration,
                "elapsed_s": time.perf_counter() - start,
                "steps": result.final_state.step_index,
                "consumed_fuel_kg": result.consumed_fuel_kg,
                "output_sha256": inventory(run_dir / "outputs"),
            }
        )
    identical = report["runs"][0]["output_sha256"] == report["runs"][1]["output_sha256"]
    unchanged = inventory(case_dir) == before
    report.update(repeated_outputs_identical=identical, source_case_unchanged=unchanged)
    (output / "baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not identical or not unchanged:
        raise AssertionError("W00 repeatability or source preservation failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--row", type=int, default=176)
    parser.add_argument("--col", type=int, default=258)
    args = parser.parse_args()
    report = audit(args.case_dir, args.output_dir, args.row, args.col)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "edge_count",
                    "builder_accepts_dem",
                    "repeated_outputs_identical",
                    "source_case_unchanged",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
