"""Strict V02 case loader joining V01 space with evidenced fuel profiles."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import yaml

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.vegetation_materials import (
    PARAMETER_UNITS,
    EvidencedValue,
    VegetationProfileLibrary,
    load_vegetation_profiles,
)
from pyfireca.village.vegetation_prepare import LANDCOVER_IDS

CONFIG_PARAMETER_UNITS = MappingProxyType(
    {
        "wind_speed_m_s": "m/s",
        "wind_from_deg": "deg",
        "ambient_temperature_K": "K",
        "max_dt_s": "s",
        "end_time_min": "min",
        "sample_interval_min": "min",
        "exposure_radius_m": "m",
        "near_field_regularization_m": "m",
        "atmospheric_extinction_m_inv": "1/m",
        "flame_tilt_coefficient_s_m": "s/m",
    }
)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _evidenced_value(raw_value: Any, parameter_name: str, location: str) -> EvidencedValue:
    raw = _mapping(raw_value, location)
    _exact_keys(raw, {"value", "unit", "provenance"}, location)
    value = raw["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{location}.value must be numeric")
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{location}.value must be finite and non-negative")
    expected_unit = CONFIG_PARAMETER_UNITS[parameter_name]
    if raw["unit"] != expected_unit:
        raise ValueError(f"{location}.unit must be {expected_unit!r}")
    provenance = _text(raw["provenance"], f"{location}.provenance")
    return EvidencedValue(numeric, expected_unit, provenance)


def _resolved_path(case_path: Path, value: Any, name: str) -> Path:
    text = _text(value, name)
    path = Path(text).expanduser()
    return (case_path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class VegetationIgnition:
    row: int
    col: int
    world_x_m: float
    world_y_m: float
    time_s: float
    kind: str
    provenance: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class SpatialProfileBudget:
    profile_id: str
    landcover_id: str
    cell_count: int
    covered_area_m2: float
    initial_dry_fuel_kg: float
    pyrolysable_dry_fuel_kg: float
    available_energy_J: float
    configured_curve_energy_J: float


@dataclass(frozen=True, slots=True)
class VegetationCase:
    path: Path
    case_id: str
    evidence: str
    prepared_dir: Path
    grid: VillageGridSpec
    profiles: VegetationProfileLibrary
    profile_id_by_cell: np.ndarray
    landcover_index: np.ndarray
    domain_mask: np.ndarray
    known_mask: np.ndarray
    evidence_index: np.ndarray
    burnable_mask: np.ndarray
    coverage_fraction: np.ndarray
    weather: dict[str, EvidencedValue]
    scenario: dict[str, EvidencedValue]
    transport: dict[str, EvidencedValue]
    scope: dict[str, Any]
    resources: dict[str, int]
    ignitions: tuple[VegetationIgnition, ...]
    spatial_budgets: tuple[SpatialProfileBudget, ...]


def _load_array(directory: Path, name: str, shape: tuple[int, int]) -> np.ndarray:
    array = np.load(directory / "arrays" / f"{name}.npy", allow_pickle=False)
    if array.shape != shape:
        raise ValueError(f"prepared array {name!r} shape does not match the grid")
    return array


def load_vegetation_case(path: str | Path) -> VegetationCase:
    """Load a V02 case and fail on missing values, bad units, or invalid ignitions."""

    source = Path(path).resolve()
    with source.open(encoding="utf-8") as stream:
        root = _mapping(yaml.safe_load(stream), "vegetation-case")
    _exact_keys(
        root,
        {
            "schema_version",
            "case_id",
            "model",
            "evidence",
            "prepared",
            "profiles",
            "landcover_profile_map",
            "scope",
            "weather",
            "scenario",
            "transport",
            "resources",
        },
        "vegetation-case",
    )
    if root["schema_version"] != "village-vegetation-case-1":
        raise ValueError("unsupported vegetation case schema")
    if root["model"] != "effective_exposure_ca_v3":
        raise ValueError("vegetation case declares the wrong model")
    case_id = _text(root["case_id"], "case_id")
    evidence = _text(root["evidence"], "evidence")
    prepared = _resolved_path(source, root["prepared"], "prepared")
    profile_path = _resolved_path(source, root["profiles"], "profiles")
    quality = json.loads((prepared / "quality_report.json").read_text(encoding="utf-8"))
    if not quality.get("valid_for_v01_spatial_acceptance", False):
        raise ValueError("prepared spatial package has not passed V01 acceptance")
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "prepared-village-vegetation-spatial-1":
        raise ValueError("unsupported vegetation spatial package schema")
    grid = VillageGridSpec(**manifest["grid"])
    arrays = prepared / "arrays"
    if not arrays.is_dir():
        raise FileNotFoundError(f"prepared arrays directory not found: {arrays}")
    domain = _load_array(prepared, "domain_mask", grid.shape)
    known = _load_array(prepared, "known_mask", grid.shape)
    landcover = _load_array(prepared, "landcover_index", grid.shape)
    evidence_index = _load_array(prepared, "evidence_index", grid.shape)
    coverage = _load_array(prepared, "landcover_coverage_fraction", grid.shape).astype(np.float64)
    if domain.dtype != np.bool_ or known.dtype != np.bool_:
        raise TypeError("prepared domain_mask and known_mask must be boolean")
    if not np.issubdtype(landcover.dtype, np.integer):
        raise TypeError("prepared landcover_index must be an integer array")
    if not np.issubdtype(evidence_index.dtype, np.integer):
        raise TypeError("prepared evidence_index must be an integer array")
    if np.any(known & ~domain) or np.any((landcover >= 0) != known):
        raise ValueError("prepared known/domain/landcover masks are inconsistent")
    if np.any(~np.isfinite(coverage)) or np.any(coverage < 0.0) or np.any(coverage > 1.0):
        raise ValueError("prepared landcover coverage must lie in [0, 1]")
    if np.any(coverage[~known] != 0.0):
        raise ValueError("unknown and outside cells must have zero landcover coverage")

    profiles = load_vegetation_profiles(profile_path)
    raw_map = _mapping(root["landcover_profile_map"], "landcover_profile_map")
    _exact_keys(raw_map, set(LANDCOVER_IDS), "landcover_profile_map")
    by_id = profiles.by_id
    for landcover_id, profile_id in raw_map.items():
        if profile_id not in by_id:
            raise ValueError(
                f"landcover_profile_map.{landcover_id} refers to unknown profile {profile_id!r}"
            )
        if by_id[profile_id].landcover_id != landcover_id:
            raise ValueError(
                f"profile {profile_id!r} cannot be assigned to landcover {landcover_id!r}"
            )
    profile_id_by_cell = np.full(grid.shape, "", dtype=object)
    burnable = np.zeros(grid.shape, dtype=np.bool_)
    for index, landcover_id in enumerate(LANDCOVER_IDS):
        mask = known & (landcover == index)
        profile_id = raw_map[landcover_id]
        profile_id_by_cell[mask] = profile_id
        burnable[mask] = by_id[profile_id].burnable

    scope = _mapping(root["scope"], "scope")
    _exact_keys(
        scope,
        {"vegetation_mode", "radiation", "rothermel_front", "crown_transition", "spotting"},
        "scope",
    )
    if scope["vegetation_mode"] != "equivalent_single_layer":
        raise ValueError("V02 supports only vegetation_mode=equivalent_single_layer")
    for name in ("radiation", "rothermel_front", "crown_transition", "spotting"):
        if not isinstance(scope[name], bool):
            raise TypeError(f"scope.{name} must be boolean")
    if not scope["radiation"]:
        raise ValueError("V02 configuration requires the radiation channel")
    if scope["rothermel_front"] or scope["crown_transition"] or scope["spotting"]:
        raise ValueError("Rothermel front, crown transition, and spotting are outside V02 scope")

    weather_raw = _mapping(root["weather"], "weather")
    _exact_keys(weather_raw, {"wind_speed_m_s", "wind_from_deg"}, "weather")
    weather = {
        name: _evidenced_value(weather_raw[name], name, f"weather.{name}") for name in weather_raw
    }
    if weather["wind_from_deg"].value >= 360.0:
        raise ValueError("weather.wind_from_deg.value must lie in [0, 360)")

    scenario_raw = _mapping(root["scenario"], "scenario")
    _exact_keys(
        scenario_raw,
        {
            "ambient_temperature_K",
            "max_dt_s",
            "end_time_min",
            "sample_interval_min",
            "ignitions",
        },
        "scenario",
    )
    scenario = {
        name: _evidenced_value(scenario_raw[name], name, f"scenario.{name}")
        for name in (
            "ambient_temperature_K",
            "max_dt_s",
            "end_time_min",
            "sample_interval_min",
        )
    }
    for name, value in scenario.items():
        if value.value <= 0.0:
            raise ValueError(f"scenario.{name}.value must be positive")
    if scenario["sample_interval_min"].value > scenario["end_time_min"].value:
        raise ValueError("scenario sample interval cannot exceed end time")
    ignition_rows = scenario_raw["ignitions"]
    if not isinstance(ignition_rows, list) or not ignition_rows:
        raise ValueError("scenario.ignitions must be a non-empty list")
    ignitions: list[VegetationIgnition] = []
    for index, raw_value in enumerate(ignition_rows):
        name = f"scenario.ignitions[{index}]"
        raw = _mapping(raw_value, name)
        _exact_keys(raw, {"world_xy_m", "time_s", "kind", "provenance"}, name)
        coordinates = raw["world_xy_m"]
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ValueError(f"{name}.world_xy_m must be a two-value list")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in coordinates
        ):
            raise TypeError(f"{name}.world_xy_m must contain numeric coordinates")
        x_m, y_m = (float(value) for value in coordinates)
        if not np.isfinite([x_m, y_m]).all():
            raise ValueError(f"{name}.world_xy_m must contain finite coordinates")
        time_s = raw["time_s"]
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)):
            raise TypeError(f"{name}.time_s must be numeric")
        time_s = float(time_s)
        if not isfinite(time_s) or time_s < 0.0:
            raise ValueError(f"{name}.time_s must be finite and non-negative")
        if time_s > scenario["end_time_min"].value * 60.0:
            raise ValueError(f"{name}.time_s exceeds the scenario end time")
        if raw["kind"] != "direct":
            raise ValueError(f"{name}.kind must be 'direct'")
        provenance = _text(raw["provenance"], f"{name}.provenance")
        try:
            row, col = grid.locate(x_m, y_m)
        except IndexError as exc:
            raise ValueError(f"{name} lies outside the prepared grid") from exc
        if not domain[row, col]:
            raise ValueError(f"{name} targets outside/NoData cell ({row}, {col})")
        if not known[row, col]:
            raise ValueError(f"{name} targets unknown cell ({row}, {col})")
        if not burnable[row, col]:
            raise ValueError(
                f"{name} targets non-burnable cell ({row}, {col}) profile "
                f"{profile_id_by_cell[row, col]!r}"
            )
        ignitions.append(
            VegetationIgnition(
                row=row,
                col=col,
                world_x_m=x_m,
                world_y_m=y_m,
                time_s=time_s,
                kind="direct",
                provenance=provenance,
                profile_id=profile_id_by_cell[row, col],
            )
        )

    transport_raw = _mapping(root["transport"], "transport")
    _exact_keys(
        transport_raw,
        set(CONFIG_PARAMETER_UNITS) - set(weather) - set(scenario),
        "transport",
    )
    transport = {
        name: _evidenced_value(transport_raw[name], name, f"transport.{name}")
        for name in transport_raw
    }
    if transport["exposure_radius_m"].value <= 0.0:
        raise ValueError("transport.exposure_radius_m.value must be positive")
    if transport["near_field_regularization_m"].value <= 0.0:
        raise ValueError("transport.near_field_regularization_m.value must be positive")

    resources = _mapping(root["resources"], "resources")
    _exact_keys(resources, {"max_edge_count", "max_memory_mb"}, "resources")
    for name, value in resources.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"resources.{name} must be a positive integer")

    spatial_budgets = []
    for profile in profiles.profiles:
        mask = profile_id_by_cell == profile.id
        covered_area = float(coverage[mask].sum() * grid.cell_area_m2)
        if profile.burnable:
            initial_mass = covered_area * profile.value("fuel_load_kg_m2")
            pyrolysable_mass = initial_mass * profile.value("pyrolysable_dry_mass_fraction")
            available_energy = covered_area * profile.budget.available_energy_J_m2_covered
            curve_energy = covered_area * profile.budget.curve_energy_J_m2_covered
        else:
            initial_mass = pyrolysable_mass = available_energy = curve_energy = 0.0
        spatial_budgets.append(
            SpatialProfileBudget(
                profile_id=profile.id,
                landcover_id=profile.landcover_id,
                cell_count=int(np.count_nonzero(mask)),
                covered_area_m2=covered_area,
                initial_dry_fuel_kg=initial_mass,
                pyrolysable_dry_fuel_kg=pyrolysable_mass,
                available_energy_J=available_energy,
                configured_curve_energy_J=curve_energy,
            )
        )
    return VegetationCase(
        path=source,
        case_id=case_id,
        evidence=evidence,
        prepared_dir=prepared,
        grid=grid,
        profiles=profiles,
        profile_id_by_cell=profile_id_by_cell,
        landcover_index=landcover.astype(np.int16, copy=True),
        domain_mask=domain.copy(),
        known_mask=known.copy(),
        evidence_index=evidence_index.astype(np.int16, copy=True),
        burnable_mask=burnable,
        coverage_fraction=coverage,
        weather=MappingProxyType(weather),
        scenario=MappingProxyType(scenario),
        transport=MappingProxyType(transport),
        scope=MappingProxyType(dict(scope)),
        resources=MappingProxyType(dict(resources)),
        ignitions=tuple(ignitions),
        spatial_budgets=tuple(spatial_budgets),
    )


def write_vegetation_validation_report(case: VegetationCase, output_dir: str | Path) -> Path:
    """Write a reproducible V02 preflight report without starting a simulation."""

    if not isinstance(case, VegetationCase):
        raise TypeError("case must be a VegetationCase")
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"validation output directory already exists: {output}")
    output.mkdir(parents=True)
    try:
        shutil.copy2(case.path, output / "case.snapshot.yml")
        shutil.copy2(case.profiles.path, output / "profiles.snapshot.yml")
        budgets = [
            {
                "profile_id": budget.profile_id,
                "landcover_id": budget.landcover_id,
                "cell_count": budget.cell_count,
                "covered_area_m2": budget.covered_area_m2,
                "initial_dry_fuel_kg": budget.initial_dry_fuel_kg,
                "pyrolysable_dry_fuel_kg": budget.pyrolysable_dry_fuel_kg,
                "available_energy_J": budget.available_energy_J,
                "configured_curve_energy_J": budget.configured_curve_energy_J,
                "energy_margin_J": budget.available_energy_J - budget.configured_curve_energy_J,
            }
            for budget in case.spatial_budgets
        ]
        profile_checks = [
            {
                "profile_id": profile.id,
                "landcover_id": profile.landcover_id,
                "burnable": profile.burnable,
                "weighted_moisture_content_dry_basis": (
                    profile.weighted_moisture_content_dry_basis
                ),
                "budget": (
                    {
                        "available_energy_J_m2_covered": (
                            profile.budget.available_energy_J_m2_covered
                        ),
                        "curve_energy_J_m2_covered": (profile.budget.curve_energy_J_m2_covered),
                        "margin_J_m2_covered": profile.budget.margin_J_m2_covered,
                        "utilization_fraction": profile.budget.utilization_fraction,
                    }
                    if profile.budget is not None
                    else None
                ),
            }
            for profile in case.profiles.profiles
        ]
        report = {
            "schema_version": "village-vegetation-v02-validation-1",
            "valid": True,
            "case_id": case.case_id,
            "model": "effective_exposure_ca_v3",
            "evidence": case.evidence,
            "production_ready": case.profiles.production_ready,
            "simulation_executed": False,
            "grid": {
                "height": case.grid.height,
                "width": case.grid.width,
                "cell_size_m": case.grid.cell_size_m,
                "crs": case.grid.crs,
            },
            "domain_cell_count": int(np.count_nonzero(case.domain_mask)),
            "known_cell_count": int(np.count_nonzero(case.known_mask)),
            "unknown_domain_cell_count": int(np.count_nonzero(case.domain_mask & ~case.known_mask)),
            "burnable_cell_count": int(np.count_nonzero(case.burnable_mask)),
            "parameter_units": dict(PARAMETER_UNITS),
            "profile_checks": profile_checks,
            "spatial_budgets": budgets,
            "ignitions": [
                {
                    "row": ignition.row,
                    "col": ignition.col,
                    "world_xy_m": [ignition.world_x_m, ignition.world_y_m],
                    "time_s": ignition.time_s,
                    "profile_id": ignition.profile_id,
                    "provenance": ignition.provenance,
                }
                for ignition in case.ignitions
            ],
            "blocking_before_simulation": [
                "V03_CELL_PHYSICS_ARRAYS_NOT_IMPLEMENTED",
                "V04_TRANSPORT_NOT_IMPLEMENTED",
                "UNKNOWN_LANDCOVER_CELLS_REQUIRE_POLICY_OR_DATA",
            ],
        }
        (output / "budget_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema_version": "village-vegetation-v02-validation-manifest-1",
            "case_sha256": _sha256(case.path),
            "profiles_sha256": _sha256(case.profiles.path),
            "prepared_manifest_sha256": _sha256(case.prepared_dir / "manifest.json"),
            "prepared_quality_report_sha256": _sha256(case.prepared_dir / "quality_report.json"),
            "artifacts": ["case.snapshot.yml", "profiles.snapshot.yml", "budget_report.json"],
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


__all__ = [
    "CONFIG_PARAMETER_UNITS",
    "SpatialProfileBudget",
    "VegetationCase",
    "VegetationIgnition",
    "load_vegetation_case",
    "write_vegetation_validation_report",
]
