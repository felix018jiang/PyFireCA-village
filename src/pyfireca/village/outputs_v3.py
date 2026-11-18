"""Reproducible V3 run and browser-viewer outputs."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca.village.solver_v3 import (
    V3SimulationRequest,
    V3SimulationResult,
    V3Snapshot,
)


def _copy_web_assets(viewer: Path) -> None:
    assets = viewer / "assets"
    assets.mkdir(parents=True)
    resources = files("pyfireca.village.web")
    for name in ("index.html", "styles.css", "app.js", "core-metrics.mjs", "chunk-replay.mjs"):
        source = resources.joinpath(name)
        target = viewer / name if name == "index.html" else assets / name
        target.write_bytes(source.read_bytes())


def _copy_spatial_assets(prepared_dir: Path | None, viewer: Path) -> str | None:
    if prepared_dir is None:
        (viewer / "buildings.json").write_text(
            json.dumps({"schema_version": "village-buildings-1", "buildings": []}),
            encoding="utf-8",
        )
        (viewer / "image_ref.json").write_text(
            json.dumps(
                {
                    "schema_version": "village-image-ref-1",
                    "affine_pixel_to_world": [1, 0, 0, 0, -1, 0],
                }
            ),
            encoding="utf-8",
        )
        return None
    prepared = Path(prepared_dir)
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    image_name = manifest.get("background") or manifest.get("artifacts", {}).get("background")
    if image_name and (prepared / image_name).is_file():
        shutil.copy2(prepared / image_name, viewer / image_name)
    for name in ("buildings.json", "image_ref.json"):
        source = prepared / name
        if source.is_file():
            shutil.copy2(source, viewer / name)
    if not (viewer / "buildings.json").is_file():
        (viewer / "buildings.json").write_text(
            json.dumps({"schema_version": "village-buildings-1", "buildings": []}),
            encoding="utf-8",
        )
    if not (viewer / "image_ref.json").is_file():
        (viewer / "image_ref.json").write_text(
            json.dumps({"affine_pixel_to_world": [1, 0, 0, 0, -1, 0]}), encoding="utf-8"
        )
    return image_name if image_name and (viewer / image_name).is_file() else None


def _write_building_cells(
    request: V3SimulationRequest,
    viewer: Path,
    prepared_dir: Path | None,
    scenario_metadata: dict[str, Any] | None = None,
) -> None:
    buildings: dict[str, list[int]] = {}
    if prepared_dir is not None:
        overlap = Path(prepared_dir) / "building_overlap.csv"
        metadata = scenario_metadata or {}
        crop_rows = metadata.get("crop_rows", [0, request.grid.height])
        crop_cols = metadata.get("crop_cols", [0, request.grid.width])
        row_start, row_stop = (int(crop_rows[0]), int(crop_rows[1]))
        col_start, col_stop = (int(crop_cols[0]), int(crop_cols[1]))
        if overlap.is_file():
            with overlap.open(encoding="utf-8", newline="") as stream:
                for record in csv.DictReader(stream):
                    row = int(record["row"])
                    col = int(record["col"])
                    if not (row_start <= row < row_stop and col_start <= col < col_stop):
                        continue
                    local_row = row - row_start
                    local_col = col - col_start
                    cell_id = local_row * request.grid.width + local_col
                    buildings.setdefault(record["building_id"], []).append(cell_id)
    (viewer / "building_cells.json").write_text(
        json.dumps(
            {"schema_version": "village-building-cells-3", "buildings": buildings},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _labels(values: dict[int, str] | None, prefix: str) -> dict[str, str]:
    if values is None:
        return {}
    return {str(int(index)): str(label) for index, label in values.items()}


def _write_dynamic_chunks(
    viewer: Path,
    name: str,
    snapshots: list[Any],
    *,
    chunk_frames: int,
    dtype: str,
    values_for_snapshot: Any,
    cells: int,
) -> list[dict[str, Any]]:
    """Write bounded frame chunks without stacking the complete replay."""

    chunks_dir = viewer / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    specs: list[dict[str, Any]] = []
    for start in range(0, len(snapshots), chunk_frames):
        stop = min(start + chunk_frames, len(snapshots))
        values = np.stack([values_for_snapshot(snapshot) for snapshot in snapshots[start:stop]])
        array = np.asarray(values, dtype=dtype)
        filename = f"{name}_{start:06d}.bin"
        path = chunks_dir / filename
        payload = np.ascontiguousarray(array).tobytes(order="C")
        path.write_bytes(payload)
        specs.append(
            {
                "path": f"chunks/{filename}",
                "frame_start": start,
                "frame_count": stop - start,
                "cell_count": cells,
                "byte_length": len(payload),
            }
        )
    return specs


class V3StreamingSnapshotSink:
    """Spill sampled V3 frames to bounded chunks and disk-backed arrays."""

    def __init__(
        self,
        staging_dir: str | Path,
        *,
        shape: tuple[int, int],
        initial_fuel: np.ndarray,
        sample_interval_s: float,
        end_time_s: float,
        chunk_frames: int,
    ) -> None:
        if chunk_frames < 1:
            raise ValueError("chunk_frames must be positive")
        if sample_interval_s <= 0.0 or end_time_s < 0.0:
            raise ValueError("streaming snapshot times must be valid")
        self.root = Path(staging_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise FileExistsError(f"snapshot staging directory is not empty: {self.root}")
        (self.root / "chunks").mkdir()
        self.shape = shape
        self.cells = int(np.prod(shape))
        self.initial_fuel = np.asarray(initial_fuel, dtype=np.float64).reshape(-1)
        if self.initial_fuel.size != self.cells:
            raise ValueError("initial_fuel does not match snapshot shape")
        self.chunk_frames = chunk_frames
        self.expected_frame_count = int(np.ceil(end_time_s / sample_interval_s - 1e-12)) + 1
        self.sample_times_s: list[float] = []
        self._buffers: dict[str, list[np.ndarray]] = {
            "state": [],
            "fuel": [],
            "temperature": [],
            "temperature_valid": [],
            "hrr": [],
            "surface_water": [],
        }
        self._chunk_specs: dict[str, list[dict[str, Any]]] = {name: [] for name in self._buffers}
        self._frame_start = 0
        self._frame_count = 0
        self._closed = False
        self._state_samples = np.lib.format.open_memmap(
            self.root / "state_samples.npy",
            mode="w+",
            dtype=np.uint8,
            shape=(self.expected_frame_count, *shape),
        )
        self._fuel_samples = np.lib.format.open_memmap(
            self.root / "fuel_fraction_samples.npy",
            mode="w+",
            dtype=np.float64,
            shape=(self.expected_frame_count, *shape),
        )
        self._temperature_min = np.inf
        self._temperature_max = -np.inf
        self._maximum_hrr = 0.0
        self._maximum_water = 0.0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def closed(self) -> bool:
        return self._closed

    def append(self, snapshot: V3Snapshot) -> None:
        if self._closed:
            raise RuntimeError("cannot append to a closed snapshot sink")
        if self._frame_count >= self.expected_frame_count:
            raise RuntimeError("streaming snapshot count exceeded its time estimate")
        if snapshot.state.shape != self.shape:
            raise ValueError("snapshot shape does not match the streaming sink")
        frame = self._frame_count
        fuel = np.divide(
            snapshot.fuel_remaining_kg.reshape(-1),
            self.initial_fuel,
            out=np.zeros_like(self.initial_fuel),
            where=self.initial_fuel > 0.0,
        ).reshape(self.shape)
        self._state_samples[frame] = snapshot.state
        self._fuel_samples[frame] = fuel
        self.sample_times_s.append(float(snapshot.time_s))
        self._buffers["state"].append(np.asarray(snapshot.state, dtype=np.uint8).copy())
        self._buffers["fuel"].append(np.asarray(fuel, dtype="<f4").copy())
        self._buffers["temperature"].append(
            np.asarray(snapshot.surface_temperature_K, dtype="<f4").copy()
        )
        self._buffers["temperature_valid"].append(
            np.asarray(snapshot.temperature_valid_mask, dtype=np.uint8).copy()
        )
        self._buffers["hrr"].append(np.asarray(snapshot.interval_mean_hrr_W, dtype="<f4").copy())
        self._buffers["surface_water"].append(
            np.asarray(snapshot.heated_water_remaining_kg, dtype="<f4").copy()
        )
        valid_temperature = snapshot.surface_temperature_K[snapshot.temperature_valid_mask]
        if valid_temperature.size:
            self._temperature_min = min(self._temperature_min, float(np.min(valid_temperature)))
            self._temperature_max = max(self._temperature_max, float(np.max(valid_temperature)))
        self._maximum_hrr = max(self._maximum_hrr, float(np.max(snapshot.interval_mean_hrr_W)))
        self._maximum_water = max(
            self._maximum_water, float(np.max(snapshot.heated_water_remaining_kg))
        )
        self._frame_count += 1
        if len(self._buffers["state"]) == self.chunk_frames:
            self._flush_chunk()

    def _flush_chunk(self) -> None:
        if not self._buffers["state"]:
            return
        count = len(self._buffers["state"])
        for name, values in self._buffers.items():
            array = np.ascontiguousarray(np.stack(values))
            filename = f"{name}_{self._frame_start:06d}.bin"
            payload = array.tobytes(order="C")
            (self.root / "chunks" / filename).write_bytes(payload)
            self._chunk_specs[name].append(
                {
                    "path": f"chunks/{filename}",
                    "frame_start": self._frame_start,
                    "frame_count": count,
                    "cell_count": self.cells,
                    "byte_length": len(payload),
                }
            )
            values.clear()
        self._frame_start += count

    def close(self) -> None:
        if self._closed:
            return
        self._flush_chunk()
        self._state_samples.flush()
        self._fuel_samples.flush()
        if self._frame_count != self.expected_frame_count:
            raise RuntimeError(
                "streaming snapshot count does not match the configured sample window: "
                f"{self._frame_count} != {self.expected_frame_count}"
            )
        self._closed = True

    def copy_sample_arrays(self, outputs: Path) -> None:
        if not self._closed:
            raise RuntimeError("close the snapshot sink before copying outputs")
        shutil.copy2(self.root / "state_samples.npy", outputs / "state_samples.npy")
        shutil.copy2(self.root / "fuel_fraction_samples.npy", outputs / "fuel_fraction_samples.npy")

    def copy_chunks(self, viewer: Path) -> None:
        if not self._closed:
            raise RuntimeError("close the snapshot sink before copying chunks")
        shutil.copytree(self.root / "chunks", viewer / "chunks")

    def dynamic_specs(self) -> dict[str, dict[str, Any]]:
        if not self._closed:
            raise RuntimeError("close the snapshot sink before reading specs")
        dtype = {
            "state": "uint8",
            "fuel": "float32-le",
            "temperature": "float32-le",
            "temperature_valid": "uint8",
            "hrr": "float32-le",
            "surface_water": "float32-le",
        }
        specs = {}
        for name, chunks in self._chunk_specs.items():
            specs[name] = {"dtype": dtype[name], "order": "C", "chunks": chunks}
        specs["fuel"]["unit"] = "fraction"
        specs["temperature"]["unit"] = "K"
        specs["hrr"]["unit"] = "W"
        specs["surface_water"]["unit"] = "kg"
        return specs

    @property
    def finite_temperature_min(self) -> float:
        return self._temperature_min

    @property
    def finite_temperature_max(self) -> float:
        return self._temperature_max

    @property
    def maximum_hrr(self) -> float:
        return self._maximum_hrr

    @property
    def maximum_water(self) -> float:
        return self._maximum_water


def write_v3_village_viewer(
    result: V3SimulationResult,
    request: V3SimulationRequest,
    run_dir: str | Path,
    *,
    prepared_dir: str | Path | None = None,
    material_labels: dict[int, str] | None = None,
    landcover_labels: dict[int, str] | None = None,
    scenario_metadata: dict[str, Any] | None = None,
    chunk_frames: int | None = None,
    snapshot_store: V3StreamingSnapshotSink | None = None,
) -> Path:
    """Write a self-contained V3 replay without changing an existing run."""

    if snapshot_store is None and not result.snapshots:
        raise ValueError("v3 viewer output requires sampled simulation snapshots")
    if snapshot_store is not None and snapshot_store.frame_count == 0:
        raise ValueError("v3 viewer output requires sampled simulation snapshots")
    if chunk_frames is not None and chunk_frames < 1:
        raise ValueError("chunk_frames must be positive")
    if snapshot_store is not None and chunk_frames is None:
        raise ValueError("streamed snapshots require chunk_frames")
    viewer = Path(run_dir) / "viewer"
    if viewer.exists():
        raise FileExistsError(f"viewer output already exists: {viewer}")
    viewer.mkdir(parents=True)
    _copy_web_assets(viewer)
    prepared = Path(prepared_dir) if prepared_dir else None
    image_name = _copy_spatial_assets(prepared, viewer)
    metadata = dict(scenario_metadata or {})
    _write_building_cells(request, viewer, prepared, metadata)

    initial = result.initial_fuel_kg
    cells = request.grid.height * request.grid.width
    static_arrays = {
        "initial_fuel": np.asarray(initial, dtype="<f8"),
        "material": np.asarray(request.material_index, dtype="<i4"),
        "landcover": np.asarray(request.landcover_index, dtype="<i4"),
        "coverage": np.asarray(request.parameters.coverage_fraction, dtype="<f4"),
        "initial_moisture": np.asarray(
            np.where(request.burnable_mask, request.parameters.moisture_content_dry_basis, np.nan),
            dtype="<f4",
        ),
        "building": np.asarray(request.building_index, dtype="<i4"),
        "ignition_time": np.asarray(result.final_state.ignition_time_s, dtype="<f8"),
        "burnout_time": np.asarray(result.final_state.burnout_time_s, dtype="<f8"),
        "ignition_cause": np.asarray(result.ignition_cause, dtype=np.uint8),
    }
    if request.material_evidence_index is not None:
        static_arrays["material_evidence"] = request.material_evidence_index
    for name, array in static_arrays.items():
        (viewer / f"{name}.bin").write_bytes(np.ascontiguousarray(array).tobytes(order="C"))

    if chunk_frames is None:
        states = np.stack([snapshot.state for snapshot in result.snapshots]).astype(
            np.uint8, copy=False
        )
        fuels = np.stack(
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
        temperatures = np.stack(
            [snapshot.surface_temperature_K for snapshot in result.snapshots]
        ).astype("<f4", copy=False)
        temperature_valid = np.stack(
            [snapshot.temperature_valid_mask for snapshot in result.snapshots]
        ).astype(np.uint8, copy=False)
        hrr = np.stack([snapshot.interval_mean_hrr_W for snapshot in result.snapshots]).astype(
            "<f4", copy=False
        )
        heated_water = np.stack(
            [snapshot.heated_water_remaining_kg for snapshot in result.snapshots]
        ).astype("<f4", copy=False)
        dynamic_arrays = {
            "state": states,
            "fuel": fuels,
            "temperature": temperatures,
            "temperature_valid": temperature_valid,
            "hrr": hrr,
            "surface_water": heated_water,
        }
        for name, array in dynamic_arrays.items():
            (viewer / f"{name}.bin").write_bytes(np.ascontiguousarray(array).tobytes(order="C"))
        dynamic_specs: dict[str, dict[str, Any]] = {
            "state": {"path": "state.bin", "dtype": "uint8", "order": "C"},
            "fuel": {
                "path": "fuel.bin",
                "dtype": "float32-le",
                "order": "C",
                "unit": "fraction",
            },
            "temperature": {
                "path": "temperature.bin",
                "dtype": "float32-le",
                "order": "C",
                "unit": "K",
            },
            "temperature_valid": {
                "path": "temperature_valid.bin",
                "dtype": "uint8",
                "order": "C",
            },
            "hrr": {
                "path": "hrr.bin",
                "dtype": "float32-le",
                "order": "C",
                "unit": "W",
            },
            "surface_water": {
                "path": "surface_water.bin",
                "dtype": "float32-le",
                "order": "C",
                "unit": "kg",
            },
        }
        finite_temperature = temperatures[temperature_valid.astype(bool)]
        maximum_hrr = float(np.max(hrr)) if hrr.size else 0.0
        maximum_water = float(np.max(heated_water)) if heated_water.size else 0.0
    else:
        if snapshot_store is not None:
            snapshot_store.copy_chunks(viewer)
            dynamic_specs = snapshot_store.dynamic_specs()
            finite_temperature_min = snapshot_store.finite_temperature_min
            finite_temperature_max = snapshot_store.finite_temperature_max
            if np.isfinite(finite_temperature_min) and np.isfinite(finite_temperature_max):
                finite_temperature = np.array(
                    [finite_temperature_min, finite_temperature_max], dtype=np.float64
                )
            else:
                finite_temperature = np.empty(0, dtype=np.float64)
            maximum_hrr = snapshot_store.maximum_hrr
            maximum_water = snapshot_store.maximum_water
        else:
            finite_temperature_min = np.nan
            finite_temperature_max = np.nan
            finite_temperature = np.empty(0)
            maximum_hrr = 0.0
            maximum_water = 0.0
        transforms = {
            "state": ("u1", lambda snapshot: snapshot.state),
            "fuel": (
                "<f4",
                lambda snapshot: np.divide(
                    snapshot.fuel_remaining_kg,
                    initial,
                    out=np.zeros_like(initial),
                    where=initial > 0.0,
                ),
            ),
            "temperature": ("<f4", lambda snapshot: snapshot.surface_temperature_K),
            "temperature_valid": ("u1", lambda snapshot: snapshot.temperature_valid_mask),
            "hrr": ("<f4", lambda snapshot: snapshot.interval_mean_hrr_W),
            "surface_water": ("<f4", lambda snapshot: snapshot.heated_water_remaining_kg),
        }
        if snapshot_store is None:
            dynamic_specs = {}
            for name, (dtype, transform) in transforms.items():
                dynamic_specs[name] = {
                    "dtype": "uint8" if dtype == "u1" else "float32-le",
                    "order": "C",
                    "chunks": _write_dynamic_chunks(
                        viewer,
                        name,
                        result.snapshots,
                        chunk_frames=chunk_frames,
                        dtype=dtype,
                        values_for_snapshot=transform,
                        cells=cells,
                    ),
                }
            finite_temperature_values = []
            maximum_hrr = 0.0
            maximum_water = 0.0
            for snapshot in result.snapshots:
                temperatures_for_snapshot = np.asarray(snapshot.surface_temperature_K)
                valid_for_snapshot = np.asarray(snapshot.temperature_valid_mask, dtype=bool)
                finite_temperature_values.append(temperatures_for_snapshot[valid_for_snapshot])
                maximum_hrr = max(maximum_hrr, float(np.max(snapshot.interval_mean_hrr_W)))
                maximum_water = max(
                    maximum_water, float(np.max(snapshot.heated_water_remaining_kg))
                )
            finite_temperature = (
                np.concatenate(finite_temperature_values)
                if any(value.size for value in finite_temperature_values)
                else np.empty(0)
            )

    grid = request.grid
    (viewer / "grid.json").write_text(
        json.dumps(
            {
                "schema_version": "village-view-grid-3",
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
    if finite_temperature.size == 0:
        finite_temperature = np.array([request.scenario.ambient_temperature_K])
    metadata.setdefault("scenario_id", "v3-run")
    metadata.setdefault("unknown_policy", request.unknown_policy)
    metadata.setdefault(
        "includes_nonbuilding_fuels",
        bool(np.any(request.burnable_mask & (request.building_index < 0))),
    )
    manifest: dict[str, Any] = {
        "schema_version": "village-viewer-3",
        "run_status": "completed",
        "model": "effective_exposure_ca_v3",
        "scenario": metadata,
        "flame_contact": request.flame_contact.to_mapping() if request.flame_contact else None,
        "background": image_name,
        "frame_count": (
            snapshot_store.frame_count if snapshot_store is not None else len(result.snapshots)
        ),
        "sample_times_s": (
            snapshot_store.sample_times_s
            if snapshot_store is not None
            else [snapshot.time_s for snapshot in result.snapshots]
        ),
        "time_display_unit": "min",
        "state": dynamic_specs["state"],
        "fuel": dynamic_specs["fuel"],
        "initial_fuel": {
            "path": "initial_fuel.bin",
            "dtype": "float64-le",
            "order": "C",
            "unit": "kg",
        },
        "temperature": {
            **dynamic_specs["temperature"],
            "minimum": float(
                finite_temperature_min
                if snapshot_store is not None and np.isfinite(finite_temperature_min)
                else np.min(finite_temperature)
            ),
            "maximum": float(
                finite_temperature_max
                if snapshot_store is not None and np.isfinite(finite_temperature_max)
                else np.max(finite_temperature)
            ),
        },
        "temperature_valid": dynamic_specs["temperature_valid"],
        "hrr": {
            **dynamic_specs["hrr"],
            "temporal_meaning": "mean_over_last_solver_step_ending_at_sample",
            "minimum": 0.0,
            "maximum": maximum_hrr,
            "scale": "log1p",
        },
        "surface_water": {
            **dynamic_specs["surface_water"],
            "minimum": 0.0,
            "maximum": maximum_water,
        },
        "material": {
            "path": "material.bin",
            "dtype": "int32-le",
            "order": "C",
            "labels": _labels(material_labels, "material"),
        },
        "landcover": {
            "path": "landcover.bin",
            "dtype": "int32-le",
            "order": "C",
            "labels": _labels(landcover_labels, "landcover"),
        },
        "coverage": {
            "path": "coverage.bin",
            "dtype": "float32-le",
            "order": "C",
            "unit": "fraction",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "initial_moisture": {
            "path": "initial_moisture.bin",
            "dtype": "float32-le",
            "order": "C",
            "unit": "kg_water/kg_dry",
            "basis": "dry_basis_ratio",
            "missing_value": "NaN for nonburnable or excluded cells",
        },
        "building": {"path": "building.bin", "dtype": "int32-le", "order": "C"},
        "ignition_time": {
            "path": "ignition_time.bin",
            "dtype": "float64-le",
            "order": "C",
            "unit": "s",
        },
        "burnout_time": {
            "path": "burnout_time.bin",
            "dtype": "float64-le",
            "order": "C",
            "unit": "s",
        },
        "ignition_cause": {
            "path": "ignition_cause.bin",
            "dtype": "uint8",
            "order": "C",
            "labels": {
                "0": "无",
                "1": "直接火源",
                "2": "热引燃",
                **({"3": "飞火起燃"} if request.firebrands is not None else {}),
            },
        },
        "building_cells": "building_cells.json",
    }
    if chunk_frames is not None:
        manifest["storage"] = {"mode": "frame_chunks", "chunk_frames": chunk_frames}
    if request.ground_elevation_m is not None:
        elevation = np.asarray(request.ground_elevation_m, dtype="<f4")
        (viewer / "elevation.bin").write_bytes(elevation.tobytes(order="C"))
        (viewer / "elevation_valid.bin").write_bytes(
            np.ones(request.grid.shape, dtype=np.uint8).tobytes(order="C")
        )
        manifest["elevation"] = {
            "path": "elevation.bin",
            "dtype": "float32-le",
            "order": "C",
            "unit": "m",
            "minimum": float(np.min(elevation)),
            "maximum": float(np.max(elevation)),
        }
        manifest["elevation_valid"] = {
            "path": "elevation_valid.bin",
            "dtype": "uint8",
            "order": "C",
        }
    if request.material_evidence_index is not None:
        manifest["material_evidence"] = {
            "path": "material_evidence.bin",
            "dtype": "uint8",
            "order": "C",
            "labels": {
                "0": "待核实地表",
                "1": "RGB推测",
                "2": "建筑标注(材质未实测)",
                "3": "人工修正建议(未实测)",
                "255": "域外",
            },
        }
    if request.firebrands is not None:
        manifest["schema_version"] = "village-viewer-4"
        manifest["firebrands"] = {
            "configuration": request.firebrands.to_mapping(),
            "counts": result.firebrands["counts"],
            "events": "../firebrand_events.jsonl",
            "budget": "../firebrand_budget.csv",
        }
    (viewer / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return viewer


def write_v3_run_outputs(
    result: V3SimulationResult,
    request: V3SimulationRequest,
    output_dir: str | Path,
    *,
    prepared_dir: str | Path | None = None,
    material_labels: dict[int, str] | None = None,
    landcover_labels: dict[int, str] | None = None,
    scenario_metadata: dict[str, Any] | None = None,
    chunk_frames: int | None = None,
    snapshot_store: V3StreamingSnapshotSink | None = None,
) -> Path:
    """Write arrays, diagnostics, and the V3 viewer into a new run directory."""

    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    outputs = output / "outputs"
    outputs.mkdir()
    if result.firebrands is not None:
        report = result.firebrands
        (output / "firebrand_events.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report["events"]),
            encoding="utf-8",
        )
        with (output / "firebrand_budget.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(report["budgets"][0]))
            writer.writeheader()
            writer.writerows(report["budgets"])
        (output / "firebrand_summary.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"events", "budgets"}}, indent=2
            ),
            encoding="utf-8",
        )
    if snapshot_store is not None and chunk_frames is None:
        raise ValueError("streamed snapshots require chunk_frames")
    if snapshot_store is not None and not snapshot_store.closed:
        raise RuntimeError("close the snapshot sink before writing run outputs")
    final = result.final_state
    for name, array in {
        "state_final": final.state,
        "surface_temperature_final_K": final.surface_temperature_K,
        "temperature_valid_final": final.temperature_valid_mask,
        "heated_water_remaining_final_kg": final.heated_water_remaining_kg,
        "fuel_remaining_final_kg": final.fuel_remaining_kg,
        "ignition_time_s": final.ignition_time_s,
        "burnout_time_s": final.burnout_time_s,
        "ignition_cause": result.ignition_cause,
        "released_energy_final_J": final.released_energy_J,
        "incident_radiation_final_J": final.incident_radiation_J,
        "absorbed_radiation_final_J": final.absorbed_radiation_J,
        "received_convection_final_J": final.received_convection_J,
    }.items():
        np.save(outputs / f"{name}.npy", array, allow_pickle=False)
    if snapshot_store is None:
        np.save(
            outputs / "sample_times_s.npy",
            np.array([s.time_s for s in result.snapshots]),
            allow_pickle=False,
        )
        np.save(
            outputs / "state_samples.npy",
            np.stack([s.state for s in result.snapshots]),
            allow_pickle=False,
        )
        np.save(
            outputs / "fuel_fraction_samples.npy",
            np.stack(
                [
                    np.divide(
                        s.fuel_remaining_kg,
                        result.initial_fuel_kg,
                        out=np.zeros_like(result.initial_fuel_kg),
                        where=result.initial_fuel_kg > 0.0,
                    )
                    for s in result.snapshots
                ]
            ),
            allow_pickle=False,
        )
    else:
        np.save(
            outputs / "sample_times_s.npy",
            np.asarray(snapshot_store.sample_times_s, dtype=np.float64),
            allow_pickle=False,
        )
        snapshot_store.copy_sample_arrays(outputs)
    frame_count = (
        snapshot_store.frame_count if snapshot_store is not None else len(result.snapshots)
    )
    with (output / "energy_budget.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["quantity", "value_J"])
        for name, value in asdict(result.energy_budget).items():
            if name != "energy_limited_contact_source_steps":
                writer.writerow([name, value])
    groups: dict[str, dict[str, Any]] = {}
    labels = material_labels or {}
    state = final.state
    source_removed = result.initial_fuel_kg - final.fuel_remaining_kg
    locally_consumed = source_removed.copy()
    if request.firebrands is not None:
        for source in request.firebrands.sources:
            selected = (request.material_index == source.material_index) & request.source_mask
            locally_consumed[selected] *= 1 - source.release_mass_fraction
    for index in np.unique(request.material_index):
        if index < 0:
            continue
        mask = request.material_index == index
        label = labels.get(int(index), f"material_{int(index)}")
        ignition_times = final.ignition_time_s[mask]
        ignition_times = ignition_times[np.isfinite(ignition_times)]
        groups[label] = {
            "cell_count": int(np.count_nonzero(mask)),
            "building_cell_count": int(np.count_nonzero(mask & (request.building_index >= 0))),
            "nonbuilding_cell_count": int(np.count_nonzero(mask & (request.building_index < 0))),
            "covered_area_m2": float(np.sum(request.parameters.covered_area_m2[mask])),
            "initial_fuel_kg": float(np.sum(result.initial_fuel_kg[mask])),
            "consumed_fuel_kg": float(np.sum(locally_consumed[mask])),
            "heated_cell_count": int(np.count_nonzero(mask & np.isin(state, [2, 3, 4]))),
            "ignited_cell_count": int(np.count_nonzero(mask & np.isfinite(final.ignition_time_s))),
            "burned_out_cell_count": int(np.count_nonzero(mask & (state == 4))),
            "first_ignition_time_s": float(np.min(ignition_times)) if ignition_times.size else None,
            "ignition_time_p50_s": float(np.median(ignition_times))
            if ignition_times.size
            else None,
        }
    unknown = request.domain_mask & ~request.known_mask
    groups["__unknown__"] = {"cell_count": int(np.count_nonzero(unknown))}
    groups["__outside__"] = {"cell_count": int(np.count_nonzero(~request.domain_mask))}
    (output / "material_outcomes.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "parameters.resolved.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "village-v3-resolved-run-1",
                "model": "effective_exposure_ca_v3",
                "grid": {
                    "height": request.grid.height,
                    "width": request.grid.width,
                    "cell_size_m": request.grid.cell_size_m,
                    "crs": request.grid.crs,
                },
                "scenario": {
                    "end_time_s": request.scenario.end_time_s,
                    "max_dt_s": request.scenario.max_dt_s,
                    "ambient_temperature_K": request.scenario.ambient_temperature_K,
                    "unknown_policy": request.unknown_policy,
                },
                "flame_contact": (
                    request.flame_contact.to_mapping() if request.flame_contact else None
                ),
                "material_labels": labels,
                "landcover_labels": landcover_labels or {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    run_record = dict(scenario_metadata or {})
    if request.firebrands is not None:
        run_record["firebrands"] = request.firebrands.to_mapping()
        run_record["firebrand_counts"] = result.firebrands["counts"]
    run_record.update(
        {
            "schema_version": "village-run-v3-1",
            "status": "completed",
            "model": "effective_exposure_ca_v3",
            "end_time_s": result.final_state.time_s,
            "step_count": result.final_state.step_index,
            "energy_limited_contact_source_steps": (
                result.energy_budget.energy_limited_contact_source_steps
            ),
            "frame_count": frame_count,
            "cell_count": request.grid.height * request.grid.width,
            "burnable_cell_count": int(np.count_nonzero(request.burnable_mask)),
            "unknown_cell_count": int(np.count_nonzero(request.domain_mask & ~request.known_mask)),
            "consumed_fuel_kg": result.consumed_fuel_kg,
            "includes_nonbuilding_fuels": bool(
                np.any(request.burnable_mask & (request.building_index < 0))
            ),
            "elevation_physics_activated": request.ground_elevation_m is not None,
            "flame_contact": (
                request.flame_contact.to_mapping() if request.flame_contact else None
            ),
        }
    )
    (output / "run.json").write_text(
        json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_v3_village_viewer(
        result,
        request,
        output,
        prepared_dir=prepared_dir,
        material_labels=material_labels,
        landcover_labels=landcover_labels,
        scenario_metadata=run_record,
        chunk_frames=chunk_frames,
        snapshot_store=snapshot_store,
    )
    return output


__all__ = [
    "V3StreamingSnapshotSink",
    "write_v3_run_outputs",
    "write_v3_village_viewer",
]
