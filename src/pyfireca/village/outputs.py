"""Portable browser-viewer outputs for village simulation results."""

from __future__ import annotations

import csv
import json
import shutil
from importlib.resources import files
from pathlib import Path

import numpy as np

from pyfireca.village.solver import VillageSimulationRequest, VillageSimulationResult
from pyfireca.village.solver_v2 import V2SimulationRequest, V2SimulationResult


def _write_building_cells(prepared: Path, viewer: Path, width: int) -> None:
    building_cells: dict[str, set[int]] = {}
    with (prepared / "building_overlap.csv").open(encoding="utf-8", newline="") as stream:
        for record in csv.DictReader(stream):
            cell_id = int(record["row"]) * width + int(record["col"])
            building_cells.setdefault(record["building_id"], set()).add(cell_id)
    (viewer / "building_cells.json").write_text(
        json.dumps(
            {
                "schema_version": "village-building-cells-1",
                "buildings": {
                    building_id: sorted(cell_ids)
                    for building_id, cell_ids in building_cells.items()
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def write_village_viewer(
    result: VillageSimulationResult,
    request: VillageSimulationRequest,
    prepared_dir: str | Path,
    run_dir: str | Path,
) -> Path:
    """Write a self-contained viewer data directory using raw numeric chunks."""

    if not result.snapshots:
        raise ValueError("viewer output requires sampled simulation snapshots")
    prepared = Path(prepared_dir)
    viewer = Path(run_dir) / "viewer"
    assets = viewer / "assets"
    assets.mkdir(parents=True)
    web_resources = files("pyfireca.village.web")
    for name in ("index.html", "styles.css", "app.js"):
        resource = web_resources.joinpath(name)
        target = viewer / name if name == "index.html" else assets / name
        target.write_bytes(resource.read_bytes())

    prepared_manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    image_name = prepared_manifest["background"]
    shutil.copy2(prepared / image_name, viewer / image_name)
    shutil.copy2(prepared / "buildings.json", viewer / "buildings.json")
    shutil.copy2(prepared / "image_ref.json", viewer / "image_ref.json")
    state = np.ascontiguousarray(
        np.stack([snapshot.state for snapshot in result.snapshots]).astype(np.uint8, copy=False)
    )
    initial = result.initial_fuel_kg
    fuel = np.ascontiguousarray(
        np.stack(
            [
                np.divide(
                    snapshot.fuel_remaining_kg,
                    initial,
                    out=np.zeros_like(initial),
                    where=initial > 0.0,
                )
                for snapshot in result.snapshots
            ]
        ).astype("<f4", copy=False)
    )
    (viewer / "state.bin").write_bytes(state.tobytes(order="C"))
    (viewer / "fuel.bin").write_bytes(fuel.tobytes(order="C"))
    material = np.ascontiguousarray(request.domain.material_index.astype("<i4", copy=False))
    (viewer / "material.bin").write_bytes(material.tobytes(order="C"))
    grid = request.domain.grid
    _write_building_cells(prepared, viewer, grid.width)
    (viewer / "grid.json").write_text(
        json.dumps(
            {
                "schema_version": "village-view-grid-1",
                "height": grid.height,
                "width": grid.width,
                "cell_size_m": grid.cell_size_m,
                "x_min_m": grid.x_min_m,
                "y_max_m": grid.y_max_m,
                "coordinate_mode": grid.coordinate_mode,
                "crs": grid.crs,
                "state_codes": {
                    "0": "INERT",
                    "1": "UNBURNED",
                    "2": "HEATING",
                    "3": "BURNING",
                    "4": "BURNED_OUT",
                    "254": "UNKNOWN",
                    "255": "OUTSIDE",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (viewer / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "village-viewer-1",
                "run_status": "completed",
                "background": image_name,
                "frame_count": len(result.snapshots),
                "sample_times_s": [snapshot.time_s for snapshot in result.snapshots],
                "state": {"path": "state.bin", "dtype": "uint8", "order": "C"},
                "fuel": {"path": "fuel.bin", "dtype": "float32-le", "order": "C"},
                "material": {"path": "material.bin", "dtype": "int32-le", "order": "C"},
                "building_cells": "building_cells.json",
                "model": "effective_exposure_ca_v1",
                "stop_reason": result.stop_reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return viewer


def write_v2_village_viewer(
    result: V2SimulationResult,
    request: V2SimulationRequest,
    material_index: np.ndarray,
    prepared_dir: str | Path,
    run_dir: str | Path,
) -> Path:
    """Write a v2 replay with state, temperature, fuel, and HRR layers."""

    if not result.snapshots:
        raise ValueError("viewer output requires sampled simulation snapshots")
    prepared = Path(prepared_dir)
    viewer = Path(run_dir) / "viewer"
    assets = viewer / "assets"
    assets.mkdir(parents=True)
    web_resources = files("pyfireca.village.web")
    for name in ("index.html", "styles.css", "app.js"):
        resource = web_resources.joinpath(name)
        target = viewer / name if name == "index.html" else assets / name
        target.write_bytes(resource.read_bytes())

    prepared_manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    image_name = prepared_manifest["background"]
    shutil.copy2(prepared / image_name, viewer / image_name)
    shutil.copy2(prepared / "buildings.json", viewer / "buildings.json")
    shutil.copy2(prepared / "image_ref.json", viewer / "image_ref.json")

    state = np.ascontiguousarray(
        np.stack([snapshot.state for snapshot in result.snapshots]).astype(np.uint8, copy=False)
    )
    initial = result.initial_fuel_kg
    fuel = np.ascontiguousarray(
        np.stack(
            [
                np.divide(
                    snapshot.fuel_remaining_kg,
                    initial,
                    out=np.zeros_like(initial),
                    where=initial > 0.0,
                )
                for snapshot in result.snapshots
            ]
        ).astype("<f4", copy=False)
    )
    temperature = np.ascontiguousarray(
        np.stack([snapshot.temperature_K for snapshot in result.snapshots]).astype(
            "<f4", copy=False
        )
    )
    hrr = np.ascontiguousarray(
        np.stack([snapshot.hrr_W for snapshot in result.snapshots]).astype("<f4", copy=False)
    )
    material = np.ascontiguousarray(np.asarray(material_index).astype("<i4", copy=False))
    expected_shape = request.grid.shape
    if material.shape != expected_shape:
        raise ValueError("v2 viewer material index does not match the request grid")
    (viewer / "state.bin").write_bytes(state.tobytes(order="C"))
    (viewer / "fuel.bin").write_bytes(fuel.tobytes(order="C"))
    (viewer / "temperature.bin").write_bytes(temperature.tobytes(order="C"))
    (viewer / "hrr.bin").write_bytes(hrr.tobytes(order="C"))
    (viewer / "material.bin").write_bytes(material.tobytes(order="C"))

    grid = request.grid
    _write_building_cells(prepared, viewer, grid.width)
    (viewer / "grid.json").write_text(
        json.dumps(
            {
                "schema_version": "village-view-grid-1",
                "height": grid.height,
                "width": grid.width,
                "cell_size_m": grid.cell_size_m,
                "x_min_m": grid.x_min_m,
                "y_max_m": grid.y_max_m,
                "coordinate_mode": grid.coordinate_mode,
                "crs": grid.crs,
                "state_codes": {
                    "0": "INERT",
                    "1": "UNBURNED",
                    "2": "HEATING",
                    "3": "BURNING",
                    "4": "BURNED_OUT",
                    "254": "UNKNOWN",
                    "255": "OUTSIDE",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    finite_temperature = temperature[np.isfinite(temperature)]
    maximum_hrr = float(np.max(hrr)) if hrr.size else 0.0
    (viewer / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "village-viewer-2",
                "run_status": "completed",
                "background": image_name,
                "frame_count": len(result.snapshots),
                "sample_times_s": [snapshot.time_s for snapshot in result.snapshots],
                "time_display_unit": "min",
                "state": {"path": "state.bin", "dtype": "uint8", "order": "C"},
                "fuel": {"path": "fuel.bin", "dtype": "float32-le", "order": "C"},
                "temperature": {
                    "path": "temperature.bin",
                    "dtype": "float32-le",
                    "order": "C",
                    "unit": "K",
                    "minimum": float(np.min(finite_temperature)),
                    "maximum": float(np.max(finite_temperature)),
                },
                "hrr": {
                    "path": "hrr.bin",
                    "dtype": "float32-le",
                    "order": "C",
                    "unit": "W",
                    "minimum": 0.0,
                    "maximum": maximum_hrr,
                    "scale": "log1p",
                },
                "material": {"path": "material.bin", "dtype": "int32-le", "order": "C"},
                "building_cells": "building_cells.json",
                "model": "effective_exposure_ca_v2",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return viewer


__all__ = ["write_v2_village_viewer", "write_village_viewer"]
