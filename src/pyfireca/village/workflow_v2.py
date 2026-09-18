"""Strict file workflow for the effective-exposure village model v2."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca import __version__
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import V2CellPhysicsArrays, energy_consistent_steady_time_s
from pyfireca.village.solver_v2 import (
    V2Scenario,
    V2SimulationRequest,
    V2SimulationResult,
    run_v2_simulation,
)
from pyfireca.village.transport_v2 import (
    V2RadiativeTransportParameters,
    build_v2_radiative_transport_edges,
)

MATERIAL_PARAMETER_NAMES = (
    "fuel_load_kg_m2",
    "moisture_content_dry_basis",
    "heat_of_combustion_J_kg",
    "ignition_temperature_K",
    "peak_hrrpua_W_m2",
    "radiative_fraction",
    "absorptivity",
    "growth_time_s",
    "decay_time_s",
)
CONSTRUCTION_PARAMETER_NAMES = (
    "effective_heated_dry_mass_kg_m2",
    "specific_heat_J_kg_K",
    "emissivity",
    "ambient_heat_transfer_coefficient_W_m2_K",
    "backing_conductance_W_m2_K",
    "receiver_area_m2",
    "active_surface_area_m2",
    "pyrolysable_dry_mass_fraction",
)


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result) or result < 0.0 or (positive and result <= 0.0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_times_s(data: dict[str, Any]) -> tuple[float, float]:
    seconds = {"end_time_s", "sample_interval_s"}
    minutes = {"end_time_min", "sample_interval_min"}
    keys = set(data)
    if seconds <= keys and not (minutes & keys):
        return (
            _number(data["end_time_s"], "scenario.end_time_s", positive=True),
            _number(data["sample_interval_s"], "scenario.sample_interval_s", positive=True),
        )
    if minutes <= keys and not (seconds & keys):
        return (
            60.0 * _number(data["end_time_min"], "scenario.end_time_min", positive=True),
            60.0
            * _number(
                data["sample_interval_min"],
                "scenario.sample_interval_min",
                positive=True,
            ),
        )
    raise ValueError(
        "scenario must define either end_time_s/sample_interval_s or "
        "end_time_min/sample_interval_min"
    )


@dataclass(frozen=True, slots=True)
class V2FileCase:
    """Resolved v2 request plus provenance retained for output records."""

    path: Path
    case_id: str
    evidence: str
    transport_mode: str
    transport_calibration: str
    construction_ids: np.ndarray
    request: V2SimulationRequest


@dataclass(frozen=True, slots=True)
class V2RunArtifacts:
    directory: Path
    outputs: Path


def load_v2_file_case(path: str | Path) -> V2FileCase:
    """Load a complete v2 case without substituting missing material values."""

    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    root = _mapping(data, "village-v2-case")
    _exact_keys(
        root,
        {
            "schema_version",
            "case_id",
            "model",
            "evidence",
            "grid",
            "material_profiles",
            "construction_profiles",
            "cell_construction_grid",
            "transport",
            "scenario",
        },
        "village-v2-case",
    )
    if root["schema_version"] != "village-v2-case-1":
        raise ValueError("unsupported v2 file-case schema")
    if root["model"] != "effective_exposure_ca_v2":
        raise ValueError("v2 file case declares the wrong model")
    for name in ("case_id", "evidence"):
        if not isinstance(root[name], str) or not root[name]:
            raise ValueError(f"{name} must be a non-empty string")

    grid_data = _mapping(root["grid"], "grid")
    _exact_keys(
        grid_data,
        {"height", "width", "cell_size_m", "x_min_m", "y_max_m"},
        "grid",
    )
    grid = VillageGridSpec(
        height=grid_data["height"],
        width=grid_data["width"],
        cell_size_m=_number(grid_data["cell_size_m"], "grid.cell_size_m", positive=True),
        x_min_m=float(grid_data["x_min_m"]),
        y_max_m=float(grid_data["y_max_m"]),
    )

    material_records = root["material_profiles"]
    if not isinstance(material_records, list) or not material_records:
        raise ValueError("material_profiles must be a non-empty list")
    materials: dict[str, tuple[bool, dict[str, float]]] = {}
    for index, raw_value in enumerate(material_records):
        raw = _mapping(raw_value, f"material_profiles[{index}]")
        _exact_keys(raw, {"id", "burnable", "parameters"}, f"material_profiles[{index}]")
        identifier = raw["id"]
        if not isinstance(identifier, str) or not identifier or identifier in materials:
            raise ValueError("v2 material profile IDs must be non-empty and unique")
        if not isinstance(raw["burnable"], bool):
            raise TypeError(f"material {identifier!r} burnable must be boolean")
        parameters = _mapping(raw["parameters"], f"material {identifier!r} parameters")
        _exact_keys(
            parameters, set(MATERIAL_PARAMETER_NAMES), f"material {identifier!r} parameters"
        )
        numeric = {
            name: _number(parameters[name], f"material {identifier!r}.{name}")
            for name in MATERIAL_PARAMETER_NAMES
        }
        for name in ("radiative_fraction", "absorptivity"):
            if numeric[name] > 1.0:
                raise ValueError(f"material {identifier!r}.{name} must lie in [0, 1]")
        materials[identifier] = raw["burnable"], numeric

    construction_records = root["construction_profiles"]
    if not isinstance(construction_records, list) or not construction_records:
        raise ValueError("construction_profiles must be a non-empty list")
    constructions: dict[str, tuple[str, dict[str, float], str]] = {}
    for index, raw_value in enumerate(construction_records):
        raw = _mapping(raw_value, f"construction_profiles[{index}]")
        _exact_keys(
            raw,
            {"id", "material_id", "ignition_mode", "parameters"},
            f"construction_profiles[{index}]",
        )
        identifier = raw["id"]
        material_id = raw["material_id"]
        if not isinstance(identifier, str) or not identifier or identifier in constructions:
            raise ValueError("v2 construction profile IDs must be non-empty and unique")
        if material_id not in materials:
            raise ValueError(f"construction {identifier!r} refers to an unknown material")
        if raw["ignition_mode"] != "piloted":
            raise ValueError("v2 currently supports only explicit piloted ignition profiles")
        parameters = _mapping(raw["parameters"], f"construction {identifier!r} parameters")
        _exact_keys(
            parameters,
            set(CONSTRUCTION_PARAMETER_NAMES),
            f"construction {identifier!r} parameters",
        )
        numeric = {
            name: _number(parameters[name], f"construction {identifier!r}.{name}")
            for name in CONSTRUCTION_PARAMETER_NAMES
        }
        for name in ("emissivity", "pyrolysable_dry_mass_fraction"):
            if numeric[name] > 1.0:
                raise ValueError(f"construction {identifier!r}.{name} must lie in [0, 1]")
        constructions[identifier] = material_id, numeric, raw["ignition_mode"]

    cell_grid = root["cell_construction_grid"]
    if not isinstance(cell_grid, list) or len(cell_grid) != grid.height:
        raise ValueError("cell_construction_grid height does not match grid")
    construction_ids = np.empty(grid.shape, dtype=object)
    for row, values in enumerate(cell_grid):
        if not isinstance(values, list) or len(values) != grid.width:
            raise ValueError("cell_construction_grid width does not match grid")
        for col, identifier in enumerate(values):
            if identifier not in constructions:
                raise ValueError(
                    f"cell ({row}, {col}) refers to unknown construction {identifier!r}"
                )
            construction_ids[row, col] = identifier

    parameter_arrays = {
        name: np.zeros(grid.shape, dtype=np.float64)
        for name in (*MATERIAL_PARAMETER_NAMES, *CONSTRUCTION_PARAMETER_NAMES)
    }
    burnable = np.zeros(grid.shape, dtype=np.bool_)
    pilot = np.zeros(grid.shape, dtype=np.bool_)
    for row in range(grid.height):
        for col in range(grid.width):
            material_id, construction, ignition_mode = constructions[construction_ids[row, col]]
            is_burnable, material = materials[material_id]
            burnable[row, col] = is_burnable
            pilot[row, col] = is_burnable and ignition_mode == "piloted"
            for name, value in material.items():
                parameter_arrays[name][row, col] = value
            for name, value in construction.items():
                parameter_arrays[name][row, col] = value
    steady_time = np.zeros(grid.shape, dtype=np.float64)
    for row, col in zip(*np.nonzero(burnable), strict=True):
        steady_time[row, col] = energy_consistent_steady_time_s(
            fuel_mass_kg=(
                parameter_arrays["fuel_load_kg_m2"][row, col]
                * grid.cell_area_m2
                * parameter_arrays["pyrolysable_dry_mass_fraction"][row, col]
            ),
            heat_of_combustion_J_kg=parameter_arrays["heat_of_combustion_J_kg"][row, col],
            peak_hrr_W=(
                parameter_arrays["peak_hrrpua_W_m2"][row, col]
                * parameter_arrays["active_surface_area_m2"][row, col]
            ),
            growth_time_s=parameter_arrays["growth_time_s"][row, col],
            decay_time_s=parameter_arrays["decay_time_s"][row, col],
        )
    parameters = V2CellPhysicsArrays(
        **parameter_arrays,
        steady_time_s=steady_time,
    )

    transport = _mapping(root["transport"], "transport")
    transport_mode = transport.get("mode")
    if transport_mode == "explicit_edges":
        _exact_keys(transport, {"mode", "calibration", "edges"}, "transport")
    elif transport_mode == "effective_point_source":
        _exact_keys(
            transport,
            {"mode", "calibration", "parameters", "blocker_transmissivity_grid"},
            "transport",
        )
    else:
        raise ValueError("transport.mode must be 'explicit_edges' or 'effective_point_source'")
    if not isinstance(transport["calibration"], str) or not transport["calibration"]:
        raise ValueError("transport.calibration must be a non-empty provenance string")
    if transport_mode == "explicit_edges":
        if not isinstance(transport["edges"], list):
            raise TypeError("transport.edges must be a list")
        edge_source: list[int] = []
        edge_target: list[int] = []
        edge_weight: list[float] = []
        for index, raw_value in enumerate(transport["edges"]):
            raw = _mapping(raw_value, f"transport.edges[{index}]")
            _exact_keys(raw, {"source", "target", "weight"}, f"transport.edges[{index}]")
            source_cell = _mapping(raw["source"], f"transport.edges[{index}].source")
            target_cell = _mapping(raw["target"], f"transport.edges[{index}].target")
            _exact_keys(source_cell, {"row", "col"}, f"transport.edges[{index}].source")
            _exact_keys(target_cell, {"row", "col"}, f"transport.edges[{index}].target")
            edge_source.append(grid.cell_id(source_cell["row"], source_cell["col"]))
            edge_target.append(grid.cell_id(target_cell["row"], target_cell["col"]))
            edge_weight.append(_number(raw["weight"], f"transport.edges[{index}].weight"))
        edges = ExposureEdges(
            source=np.asarray(edge_source, dtype=np.int32),
            target=np.asarray(edge_target, dtype=np.int32),
            weight=np.asarray(edge_weight, dtype=np.float64),
            cell_count=grid.height * grid.width,
        )
    else:
        transport_parameters = _mapping(transport["parameters"], "transport.parameters")
        expected_transport_parameters = {
            "exposure_radius_m",
            "effective_flame_height_m",
            "effective_receiver_height_m",
            "near_field_regularization_m",
            "atmospheric_extinction_m_inv",
            "wind_speed_m_s",
            "wind_from_deg",
            "flame_tilt_coefficient_s_m",
        }
        _exact_keys(
            transport_parameters,
            expected_transport_parameters,
            "transport.parameters",
        )
        blocker_rows = transport["blocker_transmissivity_grid"]
        if not isinstance(blocker_rows, list) or len(blocker_rows) != grid.height:
            raise ValueError("blocker_transmissivity_grid height does not match grid")
        blocker = np.empty(grid.shape, dtype=np.float64)
        for row, values in enumerate(blocker_rows):
            if not isinstance(values, list) or len(values) != grid.width:
                raise ValueError("blocker_transmissivity_grid width does not match grid")
            for col, value in enumerate(values):
                blocker[row, col] = _number(value, f"blocker_transmissivity_grid[{row}][{col}]")
        edges = build_v2_radiative_transport_edges(
            grid,
            burnable,
            burnable,
            parameter_arrays["receiver_area_m2"],
            blocker,
            V2RadiativeTransportParameters(
                **{
                    name: _number(
                        transport_parameters[name],
                        f"transport.parameters.{name}",
                    )
                    for name in expected_transport_parameters
                }
            ),
        )

    scenario_data = _mapping(root["scenario"], "scenario")
    end_time_s, sample_interval_s = _scenario_times_s(scenario_data)
    time_keys = (
        {"end_time_s", "sample_interval_s"}
        if "end_time_s" in scenario_data
        else {"end_time_min", "sample_interval_min"}
    )
    _exact_keys(
        scenario_data,
        {
            "ambient_temperature_K",
            "max_dt_s",
            "ignitions",
        }
        | time_keys,
        "scenario",
    )
    if not isinstance(scenario_data["ignitions"], list):
        raise TypeError("scenario.ignitions must be a list")
    ignitions: list[VillageIgnitionEvent] = []
    for index, raw_value in enumerate(scenario_data["ignitions"]):
        raw = _mapping(raw_value, f"scenario.ignitions[{index}]")
        _exact_keys(raw, {"cell", "time_s", "kind"}, f"scenario.ignitions[{index}]")
        cell = _mapping(raw["cell"], f"scenario.ignitions[{index}].cell")
        _exact_keys(cell, {"row", "col"}, f"scenario.ignitions[{index}].cell")
        ignitions.append(
            VillageIgnitionEvent(
                time_s=_number(raw["time_s"], f"scenario.ignitions[{index}].time_s"),
                row=cell["row"],
                col=cell["col"],
                kind=raw["kind"],
            )
        )
    scenario = V2Scenario(
        ambient_temperature_K=_number(
            scenario_data["ambient_temperature_K"],
            "scenario.ambient_temperature_K",
            positive=True,
        ),
        max_dt_s=_number(scenario_data["max_dt_s"], "scenario.max_dt_s", positive=True),
        end_time_s=end_time_s,
        ignitions=tuple(ignitions),
    )
    request = V2SimulationRequest(
        grid=grid,
        burnable_mask=burnable,
        parameters=parameters,
        exposure_edges=edges,
        piloted_ignition_mask=pilot,
        scenario=scenario,
        sample_interval_s=sample_interval_s,
    )
    return V2FileCase(
        path=source,
        case_id=root["case_id"],
        evidence=root["evidence"],
        transport_mode=transport_mode,
        transport_calibration=transport["calibration"],
        construction_ids=construction_ids,
        request=request,
    )


def write_v2_outputs(
    result: V2SimulationResult,
    request: V2SimulationRequest,
    construction_ids: np.ndarray,
    output: Path,
) -> None:
    """Write common v2 numeric arrays and the per-cell audit table."""

    outputs = output / "outputs"
    outputs.mkdir()
    final = result.final_state
    arrays = {
        "state_final.npy": final.state,
        "temperature_final_K.npy": final.temperature_K,
        "water_mass_final_kg_m2.npy": final.water_mass_kg_m2,
        "fuel_remaining_final_kg.npy": final.fuel_remaining_kg,
        "ignition_time_s.npy": final.ignition_time_s,
        "burnout_time_s.npy": final.burnout_time_s,
        "ignition_cause.npy": result.ignition_cause,
    }
    if result.snapshots:
        arrays.update(
            {
                "sample_times_s.npy": np.array([item.time_s for item in result.snapshots]),
                "state_samples.npy": np.stack([item.state for item in result.snapshots]),
                "temperature_samples_K.npy": np.stack(
                    [item.temperature_K for item in result.snapshots]
                ),
                "water_mass_samples_kg_m2.npy": np.stack(
                    [item.water_mass_kg_m2 for item in result.snapshots]
                ),
                "fuel_remaining_samples_kg.npy": np.stack(
                    [item.fuel_remaining_kg for item in result.snapshots]
                ),
                "hrr_samples_W.npy": np.stack([item.hrr_W for item in result.snapshots]),
            }
        )
    for filename, array in arrays.items():
        np.save(outputs / filename, array, allow_pickle=False)
    with (outputs / "cell_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "row",
                "col",
                "construction_id",
                "burnable",
                "final_state",
                "ignition_cause",
                "ignition_time_s",
                "burnout_time_s",
                "temperature_final_K",
                "water_mass_final_kg_m2",
                "fuel_initial_kg",
                "fuel_remaining_kg",
            ]
        )
        for row in range(request.grid.height):
            for col in range(request.grid.width):
                ignition = final.ignition_time_s[row, col]
                burnout = final.burnout_time_s[row, col]
                writer.writerow(
                    [
                        row,
                        col,
                        construction_ids[row, col],
                        bool(request.burnable_mask[row, col]),
                        int(final.state[row, col]),
                        int(result.ignition_cause[row, col]),
                        "" if not np.isfinite(ignition) else float(ignition),
                        "" if not np.isfinite(burnout) else float(burnout),
                        float(final.temperature_K[row, col]),
                        float(final.water_mass_kg_m2[row, col]),
                        float(result.initial_fuel_kg[row, col]),
                        float(final.fuel_remaining_kg[row, col]),
                    ]
                )


def run_v2_file_case(
    config_path: str | Path, output_dir: str | Path
) -> tuple[V2SimulationResult, V2RunArtifacts]:
    """Validate, execute, and record one reproducible v2 case."""

    case = load_v2_file_case(config_path)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    record: dict[str, Any] = {
        "schema_version": "village-v2-run-1",
        "status": "running",
        "case_id": case.case_id,
        "model": "effective_exposure_ca_v2",
        "evidence": case.evidence,
        "transport_mode": case.transport_mode,
        "transport_calibration": case.transport_calibration,
        "pyfireca_version": __version__,
    }
    (output / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        shutil.copy2(case.path, output / "config.resolved.yml")
        result = run_v2_simulation(case.request)
        write_v2_outputs(result, case.request, case.construction_ids, output)
        record.update(
            {
                "status": "completed",
                "end_time_s": result.final_state.time_s,
                "step_count": result.final_state.step_index,
                "ignited_cell_count": int(
                    np.count_nonzero(np.isfinite(result.final_state.ignition_time_s))
                ),
                "consumed_fuel_kg": result.consumed_fuel_kg,
                "edge_count": len(case.request.exposure_edges.source),
                "config_sha256": _sha256(case.path),
            }
        )
    except Exception as exc:
        record.update({"status": "failed", "error": str(exc)})
        raise
    finally:
        (output / "run.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result, V2RunArtifacts(output, output / "outputs")


__all__ = [
    "CONSTRUCTION_PARAMETER_NAMES",
    "MATERIAL_PARAMETER_NAMES",
    "V2FileCase",
    "V2RunArtifacts",
    "load_v2_file_case",
    "run_v2_file_case",
    "write_v2_outputs",
]
