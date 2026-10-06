"""Physical-grid browser replay of independent component snapshots.

Node arrays remain raw solver output; the browser selects and aggregates nodes
on their original physical cells. A partially burned cell is not burned out.
This first viewer retains snapshots and therefore has an explicit memory gate.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import numpy as np

from pyfireca.village.components_v3 import ResolvedComponents
from pyfireca.village.outputs_v3 import (
    _copy_spatial_assets,
    _write_building_cells,
    write_v3_village_viewer,
)
from pyfireca.village.solver_v3 import V3SimulationResult


def check_component_viewer_memory(resolved: ResolvedComponents, max_memory_mib: int = 256) -> dict:
    if (
        isinstance(max_memory_mib, bool)
        or not isinstance(max_memory_mib, int)
        or max_memory_mib <= 0
    ):
        raise ValueError("component viewer memory limit must be a positive integer MiB")
    request = resolved.physical_request
    if request.sample_interval_s is None:
        raise ValueError("component viewer requires an explicit sample_interval_s")
    ratio = request.scenario.end_time_s / request.sample_interval_s
    if not np.isfinite(ratio):
        raise ValueError("RESOURCE_LIMIT: component viewer sample count is unbounded")
    frames = int(np.ceil(ratio)) + 1
    nodes = len(resolved.cell_id)
    cells = request.grid.height * request.grid.width
    estimate = frames * (72 * nodes + 36 * cells) + 128 * (nodes + cells)
    if estimate > max_memory_mib * 1024**2:
        raise ValueError(
            f"RESOURCE_LIMIT: component replay estimate {estimate} bytes exceeds "
            f"{max_memory_mib} MiB; increase sample interval or reduce the domain"
        )
    return {
        "estimated_bytes": estimate,
        "frame_upper_bound": frames,
        "max_memory_mib": max_memory_mib,
        "streaming_component_replay": False,
    }


def write_component_viewer(
    resolved: ResolvedComponents,
    result: V3SimulationResult,
    run_dir: str | Path,
    *,
    prepared_dir: str | Path | None = None,
    material_labels: dict[int, str] | None = None,
    landcover_labels: dict[int, str] | None = None,
    scenario_metadata: dict | None = None,
    chunk_frames: int = 32,
    max_memory_mib: int = 256,
) -> Path:
    memory = check_component_viewer_memory(resolved, max_memory_mib)
    n = len(resolved.cell_id)
    if not result.snapshots or len(result.snapshots) > memory["frame_upper_bound"]:
        raise ValueError("component viewer requires bounded sampled solver snapshots")
    snapshot_fields = (
        "state",
        "surface_temperature_K",
        "temperature_valid_mask",
        "heated_water_remaining_kg",
        "fuel_remaining_kg",
        "interval_mean_hrr_W",
    )
    if any(
        array.shape != (1, n)
        for array in (
            result.initial_fuel_kg,
            result.ignition_cause,
            result.final_state.ignition_time_s,
            result.final_state.burnout_time_s,
            *(
                getattr(snapshot, field)
                for snapshot in result.snapshots
                for field in snapshot_fields
            ),
        )
    ):
        raise ValueError("component viewer result must match resolved node indexing")
    if not np.array_equal(
        result.initial_fuel_kg,
        np.where(
            resolved._node_request.burnable_mask,
            resolved._node_request.parameters.pyrolysable_dry_fuel_mass_kg,
            0,
        ),
    ):
        raise ValueError("component viewer result must match resolved initial fuel")
    times = np.asarray([snapshot.time_s for snapshot in result.snapshots])
    if (
        not np.all(np.isfinite(times))
        or np.any(np.diff(times) <= 0)
        or times[0] != 0
        or times[-1] != resolved.physical_request.scenario.end_time_s
    ):
        raise ValueError("component viewer requires ordered snapshots spanning the simulation")
    if isinstance(chunk_frames, bool) or not isinstance(chunk_frames, int) or chunk_frames <= 0:
        raise ValueError("component viewer chunk_frames must be a positive integer")
    request = resolved.physical_request
    metadata = {
        **dict(scenario_metadata or {}),
        "end_time_s": request.scenario.end_time_s,
        "sample_interval_s": request.sample_interval_s,
        "fuel_component_experiment": True,
        "requires_independent_calibration": True,
    }
    metadata.setdefault("scenario_id", "component-v3-run")
    metadata.pop("ignition_source", None)
    metadata["ignition_events"] = [
        {"row": event.row, "col": event.col, "time_s": event.time_s}
        for event in request.scenario.ignitions
    ]
    viewer = write_v3_village_viewer(
        result,
        resolved._node_request,
        run_dir,
        material_labels=material_labels,
        landcover_labels=landcover_labels,
        scenario_metadata=metadata,
        chunk_frames=chunk_frames,
    )
    (viewer / "assets/component-view.mjs").write_bytes(
        files("pyfireca.village.web").joinpath("component-view.mjs").read_bytes()
    )
    manifest = json.loads((viewer / "manifest.json").read_text())
    grid = json.loads((viewer / "grid.json").read_text())
    for key in ("height", "width", "cell_size_m", "x_min_m", "y_max_m", "coordinate_mode", "crs"):
        grid[key] = getattr(request.grid, key)
    grid["state_codes"]["5"] = "PARTIALLY_BURNED_DISPLAY_ONLY"
    (viewer / "grid.json").write_text(json.dumps(grid, ensure_ascii=False, indent=2))
    if prepared_dir is not None:
        manifest["background"] = _copy_spatial_assets(Path(prepared_dir), viewer)
    _write_building_cells(request, viewer, Path(prepared_dir) if prepared_dir else None, metadata)
    groups = json.loads((viewer / "building_cells.json").read_text())
    if not groups["buildings"]:
        groups["identity_basis"] = "building_index_not_polygon_id"
        for cell, index in enumerate(request.building_index.ravel()):
            if index >= 0 and request.known_mask.ravel()[cell]:
                groups["buildings"].setdefault(f"index:{index}", []).append(cell)
        (viewer / "building_cells.json").write_text(json.dumps(groups, ensure_ascii=False))
    # Preserve node-level moisture before replacing physical static inputs.
    (viewer / "component_initial_moisture.bin").write_bytes(
        (viewer / "initial_moisture.bin").read_bytes()
    )
    arrays = {
        "material": (request.material_index, "<i4"),
        "landcover": (request.landcover_index, "<i4"),
        "building": (request.building_index, "<i4"),
        "coverage": (request.parameters.coverage_fraction, "<f4"),
        "initial_moisture": (
            np.where(request.burnable_mask, request.parameters.moisture_content_dry_basis, np.nan),
            "<f4",
        ),
        "component_cells": (resolved.cell_id, "<i4"),
        "component_burnable": (resolved._node_request.burnable_mask, "u1"),
        "component_initial_fuel": (result.initial_fuel_kg, "<f8"),
        "component_initial_dry": (
            resolved._node_request.parameters.initial_dry_fuel_mass_kg,
            "<f8",
        ),
    }
    for name, (array, dtype) in arrays.items():
        (viewer / f"{name}.bin").write_bytes(np.ascontiguousarray(array, dtype=dtype).tobytes())
    (viewer / "component_ids.json").write_text(
        json.dumps(resolved.component_id.tolist(), ensure_ascii=False)
    )
    if request.ground_elevation_m is not None:
        elevation = np.asarray(request.ground_elevation_m, dtype="<f4")
        (viewer / "elevation.bin").write_bytes(elevation.tobytes())
        (viewer / "elevation_valid.bin").write_bytes(
            np.asarray(request.known_mask, dtype="u1").tobytes()
        )
        manifest["elevation"] = {
            "path": "elevation.bin",
            "dtype": "float32-le",
            "order": "C",
            "unit": "m",
            "minimum": float(elevation.min()),
            "maximum": float(elevation.max()),
        }
        manifest["elevation_valid"] = {
            "path": "elevation_valid.bin",
            "dtype": "uint8",
            "order": "C",
        }
    manifest.update(
        {
            "schema_version": "village-component-viewer-1",
            "model": "component_effective_exposure_ca_v3",
            "model_label": "燃料部位模拟",
            "flame_contact": resolved._contact.parameters.to_mapping()
            if resolved._contact
            else None,
            "components": {
                "schema_version": "village-view-components-1",
                "node_count": n,
                "cell_id": {"path": "component_cells.bin", "dtype": "int32-le"},
                "burnable": {"path": "component_burnable.bin", "dtype": "uint8"},
                "ids": "component_ids.json",
                "initial_fuel": {
                    "path": "component_initial_fuel.bin",
                    "dtype": "float64-le",
                    "unit": "kg",
                },
                "initial_dry": {
                    "path": "component_initial_dry.bin",
                    "dtype": "float64-le",
                    "unit": "kg",
                },
                "initial_moisture": {
                    "path": "component_initial_moisture.bin",
                    "dtype": "float32-le",
                    "basis": "dry_basis_ratio",
                },
                "snapshot_indexing": "frame_then_component_node",
                "aggregation": (
                    "selected_fuel_sum; valid_unburned_temperature_max; partial_burn_is_not_burnout"
                ),
                "requires_independent_calibration": True,
                "memory_preflight": memory,
                "radiation_geometry": "complete_finite_surfaces"
                if resolved.finite_radiation_graph
                else "inherited_cell_graph_plus_optional_internal",
                "configuration": resolved.configuration.to_mapping(),
            },
        }
    )
    manifest["hrr"]["temporal_meaning"] = "mean_over_last_solver_step_ending_at_sample"
    (viewer / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return viewer
