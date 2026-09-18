"""Run model v2 on a prepared village-v1 spatial domain."""

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
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.outputs import write_v2_village_viewer
from pyfireca.village.physics_v2 import V2CellPhysicsArrays, energy_consistent_steady_time_s
from pyfireca.village.prepare import load_prepared_village
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
from pyfireca.village.workflow_v2 import (
    CONSTRUCTION_PARAMETER_NAMES,
    MATERIAL_PARAMETER_NAMES,
    V2RunArtifacts,
    write_v2_outputs,
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
class PreparedV2Case:
    path: Path
    prepared_dir: Path
    scenario_id: str
    evidence: str
    transport_calibration: str
    material_index: np.ndarray
    construction_ids: np.ndarray
    blocker_transmissivity: np.ndarray
    request: V2SimulationRequest


def _building_mask(prepared_dir: Path, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.bool_)
    with (prepared_dir / "building_overlap.csv").open(encoding="utf-8", newline="") as stream:
        for record in csv.DictReader(stream):
            row, col = int(record["row"]), int(record["col"])
            if not (0 <= row < shape[0] and 0 <= col < shape[1]):
                raise ValueError("prepared building overlap contains an out-of-grid cell")
            mask[row, col] = True
    return mask


def load_prepared_v2_case(prepared_dir: str | Path, scenario_path: str | Path) -> PreparedV2Case:
    """Resolve complete v2 profiles against an existing prepared spatial grid."""

    prepared = Path(prepared_dir)
    source = Path(scenario_path)
    quality = json.loads((prepared / "quality_report.json").read_text(encoding="utf-8"))
    if not quality.get("valid", False):
        raise ValueError(f"prepared case has blocking quality errors: {quality.get('errors', [])}")
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "prepared-village-1":
        raise ValueError("unsupported prepared village schema")
    domain = load_prepared_village(prepared)
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    root = _mapping(data, "prepared-v2-scenario")
    _exact_keys(
        root,
        {
            "schema_version",
            "scenario_id",
            "model",
            "evidence",
            "material_mappings",
            "blockers",
            "transport",
            "scenario",
        },
        "prepared-v2-scenario",
    )
    if root["schema_version"] != "village-v2-prepared-scenario-1":
        raise ValueError("unsupported prepared-v2 scenario schema")
    if root["model"] != "effective_exposure_ca_v2":
        raise ValueError("prepared-v2 scenario declares the wrong model")
    for name in ("scenario_id", "evidence"):
        if not isinstance(root[name], str) or not root[name]:
            raise ValueError(f"{name} must be a non-empty string")

    source_material_ids = tuple(manifest["material_ids"])
    mappings = _mapping(root["material_mappings"], "material_mappings")
    if set(mappings) != set(source_material_ids):
        missing = sorted(set(source_material_ids) - mappings.keys())
        unknown = sorted(mappings.keys() - set(source_material_ids))
        raise ValueError(
            "material_mappings must exactly cover prepared materials; "
            f"missing={missing}, unknown={unknown}"
        )
    shape = domain.grid.shape
    material_arrays = {name: np.zeros(shape, dtype=np.float64) for name in MATERIAL_PARAMETER_NAMES}
    construction_arrays = {
        name: np.zeros(shape, dtype=np.float64) for name in CONSTRUCTION_PARAMETER_NAMES
    }
    burnable = np.zeros(shape, dtype=np.bool_)
    piloted = np.zeros(shape, dtype=np.bool_)
    construction_ids = np.empty(shape, dtype=object)
    for material_index, material_id in enumerate(source_material_ids):
        raw = _mapping(mappings[material_id], f"material_mappings.{material_id}")
        _exact_keys(
            raw,
            {
                "construction_id",
                "burnable",
                "ignition_mode",
                "material_parameters",
                "construction_parameters",
            },
            f"material_mappings.{material_id}",
        )
        if not isinstance(raw["construction_id"], str) or not raw["construction_id"]:
            raise ValueError(f"material mapping {material_id!r} requires construction_id")
        if not isinstance(raw["burnable"], bool):
            raise TypeError(f"material mapping {material_id!r} burnable must be boolean")
        if raw["ignition_mode"] != "piloted":
            raise ValueError("prepared v2 currently supports only piloted ignition")
        material_parameters = _mapping(
            raw["material_parameters"], f"material_mappings.{material_id}.material_parameters"
        )
        construction_parameters = _mapping(
            raw["construction_parameters"],
            f"material_mappings.{material_id}.construction_parameters",
        )
        _exact_keys(
            material_parameters,
            set(MATERIAL_PARAMETER_NAMES),
            f"material_mappings.{material_id}.material_parameters",
        )
        _exact_keys(
            construction_parameters,
            set(CONSTRUCTION_PARAMETER_NAMES),
            f"material_mappings.{material_id}.construction_parameters",
        )
        cell_mask = domain.known_mask & (domain.material_index == material_index)
        burnable[cell_mask] = raw["burnable"]
        piloted[cell_mask] = raw["burnable"]
        construction_ids[cell_mask] = raw["construction_id"]
        for name in MATERIAL_PARAMETER_NAMES:
            material_arrays[name][cell_mask] = _number(
                material_parameters[name], f"material_mappings.{material_id}.{name}"
            )
        for name in CONSTRUCTION_PARAMETER_NAMES:
            construction_arrays[name][cell_mask] = _number(
                construction_parameters[name], f"material_mappings.{material_id}.{name}"
            )
        if construction_parameters["receiver_area_m2"] <= 0.0:
            raise ValueError(f"material mapping {material_id!r} requires positive receiver area")
    if any(value is None for value in construction_ids[domain.known_mask]):
        raise RuntimeError("internal prepared-v2 material resolution failure")
    for name in ("radiative_fraction", "absorptivity"):
        if np.any(material_arrays[name] > 1.0):
            raise ValueError(f"{name} must lie in [0, 1]")
    for name in ("emissivity", "pyrolysable_dry_mass_fraction"):
        if np.any(construction_arrays[name] > 1.0):
            raise ValueError(f"{name} must lie in [0, 1]")
    steady = np.zeros(shape, dtype=np.float64)
    for row, col in zip(*np.nonzero(burnable), strict=True):
        steady[row, col] = energy_consistent_steady_time_s(
            fuel_mass_kg=(
                material_arrays["fuel_load_kg_m2"][row, col]
                * domain.grid.cell_area_m2
                * construction_arrays["pyrolysable_dry_mass_fraction"][row, col]
            ),
            heat_of_combustion_J_kg=material_arrays["heat_of_combustion_J_kg"][row, col],
            peak_hrr_W=(
                material_arrays["peak_hrrpua_W_m2"][row, col]
                * construction_arrays["active_surface_area_m2"][row, col]
            ),
            growth_time_s=material_arrays["growth_time_s"][row, col],
            decay_time_s=material_arrays["decay_time_s"][row, col],
        )
    parameters = V2CellPhysicsArrays(
        **material_arrays,
        **construction_arrays,
        steady_time_s=steady,
    )

    blockers = _mapping(root["blockers"], "blockers")
    _exact_keys(
        blockers,
        {"background_transmissivity", "building_transmissivity"},
        "blockers",
    )
    background_tau = _number(
        blockers["background_transmissivity"], "blockers.background_transmissivity"
    )
    building_tau = _number(blockers["building_transmissivity"], "blockers.building_transmissivity")
    if background_tau > 1.0 or building_tau > 1.0:
        raise ValueError("blocker transmissivity must lie in [0, 1]")
    blocker = np.full(shape, background_tau, dtype=np.float64)
    blocker[_building_mask(prepared, shape)] = building_tau

    transport = _mapping(root["transport"], "transport")
    _exact_keys(transport, {"calibration", "parameters"}, "transport")
    if not isinstance(transport["calibration"], str) or not transport["calibration"]:
        raise ValueError("transport.calibration must be a non-empty string")
    transport_parameters = _mapping(transport["parameters"], "transport.parameters")
    transport_fields = set(V2RadiativeTransportParameters.__dataclass_fields__)
    _exact_keys(transport_parameters, transport_fields, "transport.parameters")
    edges = build_v2_radiative_transport_edges(
        domain.grid,
        burnable,
        burnable,
        construction_arrays["receiver_area_m2"],
        blocker,
        V2RadiativeTransportParameters(
            **{
                name: _number(transport_parameters[name], f"transport.parameters.{name}")
                for name in transport_fields
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
    request = V2SimulationRequest(
        grid=domain.grid,
        burnable_mask=burnable,
        parameters=parameters,
        exposure_edges=edges,
        piloted_ignition_mask=piloted,
        scenario=V2Scenario(
            ambient_temperature_K=_number(
                scenario_data["ambient_temperature_K"],
                "scenario.ambient_temperature_K",
                positive=True,
            ),
            max_dt_s=_number(scenario_data["max_dt_s"], "scenario.max_dt_s", positive=True),
            end_time_s=end_time_s,
            ignitions=tuple(ignitions),
        ),
        sample_interval_s=sample_interval_s,
    )
    return PreparedV2Case(
        path=source,
        prepared_dir=prepared,
        scenario_id=root["scenario_id"],
        evidence=root["evidence"],
        transport_calibration=transport["calibration"],
        material_index=domain.material_index,
        construction_ids=construction_ids,
        blocker_transmissivity=blocker,
        request=request,
    )


def run_prepared_v2_case(
    prepared_dir: str | Path, scenario_path: str | Path, output_dir: str | Path
) -> tuple[V2SimulationResult, V2RunArtifacts]:
    """Validate and run v2 on a prepared village, including browser replay output."""

    case = load_prepared_v2_case(prepared_dir, scenario_path)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    record: dict[str, Any] = {
        "schema_version": "prepared-village-v2-run-1",
        "status": "running",
        "scenario_id": case.scenario_id,
        "model": "effective_exposure_ca_v2",
        "evidence": case.evidence,
        "transport_calibration": case.transport_calibration,
        "pyfireca_version": __version__,
    }
    (output / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        shutil.copy2(case.path, output / "config.resolved.yml")
        result = run_v2_simulation(case.request)
        write_v2_outputs(result, case.request, case.construction_ids, output)
        write_v2_village_viewer(
            result,
            case.request,
            case.material_index,
            case.prepared_dir,
            output,
        )
        np.save(
            output / "outputs" / "blocker_transmissivity.npy",
            case.blocker_transmissivity,
            allow_pickle=False,
        )
        record.update(
            {
                "status": "completed",
                "end_time_s": result.final_state.time_s,
                "end_time_min": result.final_state.time_s / 60.0,
                "step_count": result.final_state.step_index,
                "ignited_cell_count": int(
                    np.count_nonzero(np.isfinite(result.final_state.ignition_time_s))
                ),
                "consumed_fuel_kg": result.consumed_fuel_kg,
                "edge_count": len(case.request.exposure_edges.source),
                "scenario_sha256": _sha256(case.path),
                "prepared_directory": str(case.prepared_dir.resolve()),
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


__all__ = ["PreparedV2Case", "load_prepared_v2_case", "run_prepared_v2_case"]
