"""Auditable first/middle/final-frame checks for V3 browser replays."""

from __future__ import annotations

import json
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

_DYNAMIC_SPECS = {
    "state": (np.dtype("u1"), "state"),
    "fuel": (np.dtype("<f4"), "fuel"),
    "temperature": (np.dtype("<f4"), "temperature"),
    "temperature_valid": (np.dtype("u1"), "temperature_valid"),
    "hrr": (np.dtype("<f4"), "hrr"),
    "surface_water": (np.dtype("<f4"), "surface_water"),
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _frame_indices(frame_count: int) -> list[int]:
    if frame_count < 1:
        raise ValueError("viewer manifest must contain at least one frame")
    return sorted({0, frame_count // 2, frame_count - 1})


def _read_static(viewer: Path, spec: dict[str, Any], dtype: np.dtype, cells: int) -> np.ndarray:
    path = viewer / str(spec["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = cells * dtype.itemsize
    actual = path.stat().st_size
    if actual != expected:
        raise ValueError(f"{path.name} has {actual} bytes; expected {expected}")
    return np.fromfile(path, dtype=dtype, count=cells)


def _read_dynamic_frame(
    viewer: Path,
    spec: dict[str, Any],
    dtype: np.dtype,
    frame: int,
    frame_count: int,
    cells: int,
) -> np.ndarray:
    if "chunks" in spec:
        chunks = sorted(spec["chunks"], key=lambda chunk: int(chunk["frame_start"]))
        cursor = 0
        for chunk in chunks:
            start = int(chunk["frame_start"])
            count = int(chunk["frame_count"])
            if start != cursor or count < 1:
                raise ValueError("V3 frame chunks have a gap or overlap")
            path = viewer / str(chunk["path"])
            expected = count * cells * dtype.itemsize
            if path.stat().st_size != expected or int(chunk["byte_length"]) != expected:
                raise ValueError(f"invalid chunk length: {path}")
            cursor += count
            if start <= frame < start + count:
                values = np.memmap(path, mode="r", dtype=dtype, shape=(count, cells), order="C")
                return np.asarray(values[frame - start], dtype=dtype).copy()
        if cursor != frame_count:
            raise ValueError("V3 frame chunks do not cover frame_count")
        raise ValueError(f"frame outside V3 chunks: {frame}")
    path = viewer / str(spec["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = frame_count * cells * dtype.itemsize
    actual = path.stat().st_size
    if actual != expected:
        raise ValueError(f"{path.name} has {actual} bytes; expected {expected}")
    values = np.memmap(path, mode="r", dtype=dtype, shape=(frame_count, cells), order="C")
    return np.asarray(values[frame], dtype=dtype).copy()


def _state_counts(state: np.ndarray) -> dict[str, int]:
    return {
        str(int(code)): int(np.count_nonzero(state == code)) for code in (0, 1, 2, 3, 4, 254, 255)
    }


def _cell_snapshot(
    *,
    frame: int,
    cell_id: int,
    grid: dict[str, Any],
    material: np.ndarray,
    landcover: np.ndarray | None,
    coverage: np.ndarray | None,
    states: np.ndarray,
    fuel: np.ndarray,
    temperature: np.ndarray | None,
    temperature_valid: np.ndarray | None,
    hrr: np.ndarray | None,
    surface_water: np.ndarray | None,
    material_labels: dict[str, str],
    landcover_labels: dict[str, str],
) -> dict[str, Any]:
    row, col = divmod(cell_id, int(grid["width"]))
    record: dict[str, Any] = {
        "frame": frame,
        "cell_id": cell_id,
        "row": row,
        "col": col,
        "state": int(states[cell_id]),
        "material": material_labels.get(str(int(material[cell_id])), int(material[cell_id])),
        "landcover": (
            landcover_labels.get(str(int(landcover[cell_id])), int(landcover[cell_id]))
            if landcover is not None
            else None
        ),
        "coverage_fraction": float(coverage[cell_id]) if coverage is not None else None,
        "fuel_fraction": float(fuel[cell_id]),
    }
    if temperature is not None:
        record["temperature_K"] = (
            float(temperature[cell_id])
            if temperature_valid is None or bool(temperature_valid[cell_id])
            else None
        )
    if hrr is not None:
        record["hrr_W"] = float(hrr[cell_id])
    if surface_water is not None:
        record["surface_water_kg"] = float(surface_water[cell_id])
    return record


def inspect_v3_run(
    run_dir: str | Path,
    *,
    output: str | Path | None = None,
    cell_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Check replay byte lengths and compare representative frames to backend arrays."""

    run = Path(run_dir)
    viewer = run / "viewer"
    manifest = _load_json(viewer / "manifest.json")
    grid = _load_json(viewer / "grid.json")
    if manifest.get("schema_version") != "village-viewer-3":
        raise ValueError("inspect-v3 requires a village-viewer-3 manifest")
    height, width = int(grid["height"]), int(grid["width"])
    cells = height * width
    frame_count = int(manifest["frame_count"])
    times = [float(value) for value in manifest["sample_times_s"]]
    if len(times) != frame_count or any(b <= a for a, b in pairwise(times)):
        raise ValueError("sample times must be strictly monotone and match frame_count")

    material_spec = manifest.get("material")
    if not isinstance(material_spec, dict):
        raise ValueError("viewer manifest has no material specification")
    material = _read_static(viewer, material_spec, np.dtype("<i4"), cells)
    landcover = (
        _read_static(viewer, manifest["landcover"], np.dtype("<i4"), cells)
        if manifest.get("landcover")
        else None
    )
    coverage = (
        _read_static(viewer, manifest["coverage"], np.dtype("<f4"), cells)
        if manifest.get("coverage")
        else None
    )
    material_labels = {
        str(key): str(value) for key, value in material_spec.get("labels", {}).items()
    }
    landcover_labels = {
        str(key): str(value)
        for key, value in manifest.get("landcover", {}).get("labels", {}).items()
    }
    frame_ids = _frame_indices(frame_count)
    snapshots: list[dict[str, Any]] = []
    for frame in frame_ids:
        values: dict[str, np.ndarray] = {}
        for name, (dtype, _) in _DYNAMIC_SPECS.items():
            spec = manifest.get(name)
            if spec is not None:
                values[name] = _read_dynamic_frame(viewer, spec, dtype, frame, frame_count, cells)
        state = values["state"]
        record: dict[str, Any] = {
            "frame": frame,
            "time_s": times[frame],
            "state_counts": _state_counts(state),
            "burning_cells": int(np.count_nonzero(state == 3)),
            "affected_cells": int(np.count_nonzero(np.isin(state, (2, 3, 4)))),
        }
        if values.get("fuel") is not None:
            record["fuel_fraction_sum"] = float(np.sum(values["fuel"]))
        if cell_ids:
            selected: list[dict[str, Any]] = []
            for cell_id in cell_ids:
                if not 0 <= int(cell_id) < cells:
                    raise ValueError(f"cell id outside viewer grid: {cell_id}")
                selected.append(
                    _cell_snapshot(
                        frame=frame,
                        cell_id=int(cell_id),
                        grid=grid,
                        material=material,
                        landcover=landcover,
                        coverage=coverage,
                        states=values["state"],
                        fuel=values["fuel"],
                        temperature=values.get("temperature"),
                        temperature_valid=values.get("temperature_valid"),
                        hrr=values.get("hrr"),
                        surface_water=values.get("surface_water"),
                        material_labels=material_labels,
                        landcover_labels=landcover_labels,
                    )
                )
            record["cells"] = selected
        snapshots.append(record)

    backend_checks: dict[str, Any] = {}
    outputs = run / "outputs"
    state_samples = outputs / "state_samples.npy"
    if state_samples.is_file():
        backend = np.load(state_samples, mmap_mode="r", allow_pickle=False)
        backend_checks["state_samples_match"] = bool(
            backend.shape == (frame_count, height, width)
            and all(
                np.array_equal(
                    backend[frame].reshape(-1),
                    _read_dynamic_frame(
                        viewer, manifest["state"], np.dtype("u1"), frame, frame_count, cells
                    ),
                )
                for frame in frame_ids
            )
        )
    fuel_samples = outputs / "fuel_fraction_samples.npy"
    if fuel_samples.is_file() and manifest.get("fuel"):
        backend = np.load(fuel_samples, mmap_mode="r", allow_pickle=False)
        backend_checks["fuel_samples_match"] = bool(
            backend.shape == (frame_count, height, width)
            and all(
                np.allclose(
                    backend[frame].reshape(-1),
                    _read_dynamic_frame(
                        viewer, manifest["fuel"], np.dtype("<f4"), frame, frame_count, cells
                    ),
                    rtol=1e-6,
                    atol=1e-7,
                )
                for frame in frame_ids
            )
        )
    report: dict[str, Any] = {
        "schema_version": "village-v3-inspection-1",
        "run": str(run.resolve()),
        "scenario_id": manifest.get("scenario", {}).get("scenario_id"),
        "grid": {"height": height, "width": width, "cell_size_m": grid.get("cell_size_m")},
        "frame_count": frame_count,
        "representative_frames": snapshots,
        "backend_checks": backend_checks,
        "visual_acceptance": bool(all(backend_checks.values())) if backend_checks else True,
    }
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


__all__ = ["inspect_v3_run"]
