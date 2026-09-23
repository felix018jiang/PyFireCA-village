"""Create an explicitly hypothetical RGB-completed V01 spatial package."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_raster(path: Path, array: np.ndarray, *, nodata: int) -> None:
    import rasterio

    with rasterio.open(path) as source:
        profile = source.profile.copy()
    profile.update(dtype=array.dtype, count=1, nodata=nodata, compress="deflate")
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(array, 1)


def complete_rgb_hypothesis_spatial(
    prepared_dir: str | Path,
    output_dir: str | Path,
) -> Path:
    """Fill V01 unknown cells with a low-green ``bare_or_road`` hypothesis.

    The source V01 package is copied to a new directory.  Buildings and the
    existing grass/shrub/tree RGB hypotheses are preserved; only domain cells
    with ``known_mask=False`` receive land-cover 0, full coverage, and
    ``rgb_hypothesis`` evidence.  No combustion parameter or simulation array
    is created here.
    """

    source = Path(prepared_dir).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"RGB hypothesis output already exists: {output}")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "prepared-village-vegetation-spatial-1":
        raise ValueError("unsupported V01 spatial package schema")
    arrays = source / "arrays"
    domain = np.load(arrays / "domain_mask.npy", allow_pickle=False)
    known = np.load(arrays / "known_mask.npy", allow_pickle=False)
    landcover = np.load(arrays / "landcover_index.npy", allow_pickle=False).astype(
        np.int16, copy=True
    )
    evidence = np.load(arrays / "evidence_index.npy", allow_pickle=False).astype(
        np.uint8, copy=True
    )
    coverage = np.load(arrays / "landcover_coverage_fraction.npy", allow_pickle=False).astype(
        np.float32, copy=True
    )
    if domain.dtype != np.bool_ or known.dtype != np.bool_:
        raise TypeError("V01 domain_mask and known_mask must be boolean")
    if domain.shape != known.shape or landcover.shape != domain.shape:
        raise ValueError("V01 spatial arrays do not share a shape")
    unknown = domain & ~known
    filled_count = int(np.count_nonzero(unknown))
    if filled_count == 0:
        raise ValueError("V01 package contains no unknown domain cells to complete")

    shutil.copytree(source, output)
    known[unknown] = True
    landcover[unknown] = 0
    evidence[unknown] = 1
    coverage[unknown] = 1.0
    np.save(output / "arrays/known_mask.npy", known, allow_pickle=False)
    np.save(output / "arrays/landcover_index.npy", landcover, allow_pickle=False)
    np.save(output / "arrays/evidence_index.npy", evidence, allow_pickle=False)
    np.save(output / "arrays/landcover_coverage_fraction.npy", coverage, allow_pickle=False)
    _write_raster(output / "landcover.tif", landcover, nodata=-1)
    _write_raster(output / "evidence.tif", evidence, nodata=0)

    report: dict[str, Any] = {
        "schema_version": "village-rgb-hypothesis-completion-1",
        "source_prepared_dir": str(source),
        "source_manifest_sha256": _sha256(manifest_path),
        "output_dir": str(output),
        "status": "hypothetical_only",
        "policy": "domain_unknown_to_bare_or_road",
        "formula": "unknown cells are assigned landcover_index=0; no new RGB threshold is inferred",
        "grid_shape": list(domain.shape),
        "domain_cell_count": int(np.count_nonzero(domain)),
        "original_unknown_cell_count": filled_count,
        "filled_bare_or_road_cell_count": filled_count,
        "outside_nodata_cell_count": int(np.count_nonzero(~domain)),
        "final_known_cell_count": int(np.count_nonzero(known)),
        "final_landcover_counts": {
            str(index): int(np.count_nonzero(domain & (landcover == index))) for index in range(6)
        },
        "evidence": "rgb_hypothesis",
        "assumptions": [
            "low-green unknown pixels are treated as bare_or_road for demonstration only",
            "bare_or_road receives no fuel and is not an ignition source",
            "this does not distinguish road, bare soil, roof, shadow or hidden vegetation",
            "no combustion parameters were modified or created",
            "no existing V01 package was modified",
        ],
    }
    (output / "hypothetical_completion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    classification = json.loads((output / "classification_report.json").read_text(encoding="utf-8"))
    classification["hypothetical_completion"] = report
    (output / "classification_report.hypothetical.json").write_text(
        json.dumps(classification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest["material_resolution_status"] = "hypothetical_rgb_completion_pending_v02"
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


__all__ = ["complete_rgb_hypothesis_spatial"]
