"""Bundle, validate, and run the V3 village-fire workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.outputs_v3 import V3StreamingSnapshotSink, write_v3_run_outputs
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import (
    V3Scenario,
    V3SimulationRequest,
    V3SimulationResult,
    run_v3_simulation,
)


@dataclass(frozen=True, slots=True)
class V3CaseBundle:
    request: V3SimulationRequest
    material_labels: dict[int, str]
    landcover_labels: dict[int, str]
    metadata: dict[str, Any]


def _save_array(directory: Path, name: str, value: np.ndarray) -> None:
    np.save(directory / f"{name}.npy", np.asarray(value), allow_pickle=False)


def write_v3_case_bundle(
    request: V3SimulationRequest,
    output_dir: str | Path,
    *,
    material_labels: dict[int, str] | None = None,
    landcover_labels: dict[int, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Persist a fully validated request for independent CLI reproduction."""

    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"v3 case bundle already exists: {output}")
    output.mkdir(parents=True)
    arrays = {
        "domain_mask": request.domain_mask,
        "known_mask": request.known_mask,
        "burnable_mask": request.burnable_mask,
        "source_mask": request.source_mask,
        "target_mask": request.target_mask,
        "piloted_ignition_mask": request.piloted_ignition_mask,
        "material_index": request.material_index,
        "landcover_index": request.landcover_index,
        "building_index": request.building_index,
        "edge_source": request.exposure_edges.source,
        "edge_target": request.exposure_edges.target,
        "edge_weight": request.exposure_edges.weight,
    }
    arrays.update(
        {
            name: getattr(request.parameters, name)
            for name in V3CellPhysicsArrays.__dataclass_fields__
        }
    )
    for name, value in arrays.items():
        _save_array(output, name, value)
    manifest = {
        "schema_version": "village-v3-case-1",
        "grid": {
            "height": request.grid.height,
            "width": request.grid.width,
            "cell_size_m": request.grid.cell_size_m,
            "x_min_m": request.grid.x_min_m,
            "y_max_m": request.grid.y_max_m,
            "coordinate_mode": request.grid.coordinate_mode,
            "crs": request.grid.crs,
        },
        "scenario": {
            "max_dt_s": request.scenario.max_dt_s,
            "end_time_s": request.scenario.end_time_s,
            "ambient_temperature_K": request.scenario.ambient_temperature_K,
            "heating_temperature_delta_K": request.scenario.heating_temperature_delta_K,
            "ignitions": [
                {"time_s": event.time_s, "row": event.row, "col": event.col, "kind": event.kind}
                for event in request.scenario.ignitions
            ],
        },
        "unknown_policy": request.unknown_policy,
        "sample_interval_s": request.sample_interval_s,
        "material_labels": {str(int(key)): value for key, value in (material_labels or {}).items()},
        "landcover_labels": {
            str(int(key)): value for key, value in (landcover_labels or {}).items()
        },
        "metadata": metadata or {},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


def load_v3_case_bundle(path: str | Path) -> V3CaseBundle:
    """Load and validate a V3 case bundle without selecting defaults."""

    directory = Path(path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "village-v3-case-1":
        raise ValueError("unsupported V3 case bundle schema")
    grid_data = manifest["grid"]
    grid = VillageGridSpec(**grid_data)

    def load(name: str) -> np.ndarray:
        return np.load(directory / f"{name}.npy", allow_pickle=False)

    parameter_values = {name: load(name) for name in V3CellPhysicsArrays.__dataclass_fields__}
    scenario_data = manifest["scenario"]
    scenario = V3Scenario(
        max_dt_s=float(scenario_data["max_dt_s"]),
        end_time_s=float(scenario_data["end_time_s"]),
        ambient_temperature_K=float(scenario_data["ambient_temperature_K"]),
        heating_temperature_delta_K=float(scenario_data["heating_temperature_delta_K"]),
        ignitions=tuple(VillageIgnitionEvent(**event) for event in scenario_data["ignitions"]),
    )
    edges = ExposureEdges(
        load("edge_source"),
        load("edge_target"),
        load("edge_weight"),
        grid.height * grid.width,
    )
    request = V3SimulationRequest(
        grid=grid,
        domain_mask=load("domain_mask"),
        known_mask=load("known_mask"),
        burnable_mask=load("burnable_mask"),
        source_mask=load("source_mask"),
        target_mask=load("target_mask"),
        piloted_ignition_mask=load("piloted_ignition_mask"),
        material_index=load("material_index"),
        landcover_index=load("landcover_index"),
        building_index=load("building_index"),
        parameters=V3CellPhysicsArrays(**parameter_values),
        exposure_edges=edges,
        scenario=scenario,
        unknown_policy=manifest.get("unknown_policy", "error"),
        sample_interval_s=manifest.get("sample_interval_s"),
    )
    return V3CaseBundle(
        request=request,
        material_labels={
            int(key): value for key, value in manifest.get("material_labels", {}).items()
        },
        landcover_labels={
            int(key): value for key, value in manifest.get("landcover_labels", {}).items()
        },
        metadata=dict(manifest.get("metadata", {})),
    )


def run_v3_case_bundle(
    case_dir: str | Path,
    output_dir: str | Path,
    *,
    prepared_dir: str | Path | None = None,
    chunk_frames: int | None = None,
) -> tuple[V3SimulationResult, Path]:
    """Run a bundle and write its arrays, diagnostics, and viewer."""

    case = load_v3_case_bundle(case_dir)
    if prepared_dir is None:
        prepared_dir = case.metadata.get("spatial_assets_dir")
    output_path = Path(output_dir)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    if chunk_frames is None:
        result = run_v3_simulation(case.request)
        output = write_v3_run_outputs(
            result,
            case.request,
            output_path,
            prepared_dir=prepared_dir,
            material_labels=case.material_labels,
            landcover_labels=case.landcover_labels,
            scenario_metadata=case.metadata,
            chunk_frames=None,
        )
        return result, output

    if case.request.sample_interval_s is None:
        raise ValueError("chunked V3 output requires sample_interval_s")
    parent = output_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{output_path.name}-snapshots-",
        dir=parent,
    ) as staging:
        snapshot_store = V3StreamingSnapshotSink(
            staging,
            shape=case.request.grid.shape,
            initial_fuel=case.request.parameters.pyrolysable_dry_fuel_mass_kg,
            sample_interval_s=float(case.request.sample_interval_s),
            end_time_s=case.request.scenario.end_time_s,
            chunk_frames=chunk_frames,
        )
        result = run_v3_simulation(
            case.request,
            snapshot_sink=snapshot_store,
            retain_snapshots=False,
        )
        snapshot_store.close()
        output = write_v3_run_outputs(
            result,
            case.request,
            output_path,
            prepared_dir=prepared_dir,
            material_labels=case.material_labels,
            landcover_labels=case.landcover_labels,
            scenario_metadata=case.metadata,
            chunk_frames=chunk_frames,
            snapshot_store=snapshot_store,
        )
    return result, output


def crop_v3_case_bundle(
    case_dir: str | Path,
    output_dir: str | Path,
    *,
    row_start: int,
    row_stop: int,
    col_start: int,
    col_stop: int,
    end_time_s: float | None = None,
    sample_interval_s: float | None = None,
    ignition: VillageIgnitionEvent | None = None,
) -> Path:
    """Create a smaller world-coordinate-preserving V3 case for V08 checks."""

    case = load_v3_case_bundle(case_dir)
    old_grid = case.request.grid
    if not (0 <= row_start < row_stop <= old_grid.height):
        raise ValueError("row crop lies outside the V3 grid")
    if not (0 <= col_start < col_stop <= old_grid.width):
        raise ValueError("column crop lies outside the V3 grid")
    new_height, new_width = row_stop - row_start, col_stop - col_start
    new_grid = replace(
        old_grid,
        height=new_height,
        width=new_width,
        x_min_m=old_grid.x_min_m + col_start * old_grid.cell_size_m,
        y_max_m=old_grid.y_max_m - row_start * old_grid.cell_size_m,
    )

    def crop(value: np.ndarray) -> np.ndarray:
        return np.asarray(value)[row_start:row_stop, col_start:col_stop].copy()

    old_edges = case.request.exposure_edges
    old_source_row, old_source_col = np.divmod(old_edges.source, old_grid.width)
    old_target_row, old_target_col = np.divmod(old_edges.target, old_grid.width)
    keep = (
        (old_source_row >= row_start)
        & (old_source_row < row_stop)
        & (old_source_col >= col_start)
        & (old_source_col < col_stop)
        & (old_target_row >= row_start)
        & (old_target_row < row_stop)
        & (old_target_col >= col_start)
        & (old_target_col < col_stop)
    )
    source = (
        (old_source_row[keep] - row_start) * new_width + old_source_col[keep] - col_start
    ).astype(np.int32)
    target = (
        (old_target_row[keep] - row_start) * new_width + old_target_col[keep] - col_start
    ).astype(np.int32)
    edges = ExposureEdges(source, target, old_edges.weight[keep], new_height * new_width)
    parameters = V3CellPhysicsArrays(
        **{
            name: crop(getattr(case.request.parameters, name))
            for name in V3CellPhysicsArrays.__dataclass_fields__
        }
    )
    events = case.request.scenario.ignitions
    if ignition is not None:
        events = (ignition,)
    else:
        events = tuple(
            replace(event, row=event.row - row_start, col=event.col - col_start)
            for event in events
            if row_start <= event.row < row_stop and col_start <= event.col < col_stop
        )
    if not events:
        raise ValueError("crop does not contain a direct ignition; provide ignition explicitly")
    scenario = replace(
        case.request.scenario,
        end_time_s=end_time_s if end_time_s is not None else case.request.scenario.end_time_s,
        ignitions=events,
    )
    if sample_interval_s is None:
        sample_interval_s = case.request.sample_interval_s
    request = V3SimulationRequest(
        grid=new_grid,
        domain_mask=crop(case.request.domain_mask),
        known_mask=crop(case.request.known_mask),
        burnable_mask=crop(case.request.burnable_mask),
        source_mask=crop(case.request.source_mask),
        target_mask=crop(case.request.target_mask),
        piloted_ignition_mask=crop(case.request.piloted_ignition_mask),
        material_index=crop(case.request.material_index),
        landcover_index=crop(case.request.landcover_index),
        building_index=crop(case.request.building_index),
        parameters=parameters,
        exposure_edges=edges,
        scenario=scenario,
        unknown_policy=case.request.unknown_policy,
        sample_interval_s=sample_interval_s,
    )
    metadata = dict(case.metadata)
    metadata.update(
        {
            "scenario_id": f"{case.metadata.get('scenario_id', 'v3')}-crop",
            "parent_case": str(Path(case_dir).resolve()),
            "crop_rows": [row_start, row_stop],
            "crop_cols": [col_start, col_stop],
            "crop_world_bounds_m": {
                "x_min_m": new_grid.x_min_m,
                "x_max_m": new_grid.x_min_m + new_width * new_grid.cell_size_m,
                "y_min_m": new_grid.y_max_m - new_height * new_grid.cell_size_m,
                "y_max_m": new_grid.y_max_m,
            },
        }
    )
    return write_v3_case_bundle(
        request,
        output_dir,
        material_labels=case.material_labels,
        landcover_labels=case.landcover_labels,
        metadata=metadata,
    )


__all__ = [
    "V3CaseBundle",
    "crop_v3_case_bundle",
    "load_v3_case_bundle",
    "run_v3_case_bundle",
    "write_v3_case_bundle",
]
