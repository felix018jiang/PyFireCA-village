"""Strict V02 fuel-profile contracts for the village vegetation model."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from pyfireca.village.vegetation_prepare import LANDCOVER_IDS

PARAMETER_UNITS = MappingProxyType(
    {
        "fuel_load_kg_m2": "kg/m^2",
        "live_dry_mass_fraction": "1",
        "dead_dry_mass_fraction": "1",
        "live_moisture_content_dry_basis": "kg/kg_dry",
        "dead_moisture_content_dry_basis": "kg/kg_dry",
        "heat_of_combustion_J_kg_dry": "J/kg_dry",
        "pyrolysable_dry_mass_fraction": "1",
        "ignition_temperature_K": "K",
        "peak_hrrpua_W_m2": "W/m^2_active",
        "radiative_fraction": "1",
        "absorptivity": "1",
        "growth_time_s": "s",
        "steady_time_s": "s",
        "decay_time_s": "s",
        "effective_heated_dry_mass_kg_m2": "kg/m^2_covered_ground",
        "specific_heat_J_kg_K": "J/(kg*K)",
        "emissivity": "1",
        "ambient_heat_transfer_coefficient_W_m2_K": "W/(m^2*K)",
        "backing_conductance_W_m2_K": "W/(m^2*K)",
        "receiver_area_ratio": "m^2_receiver/m^2_covered_ground",
        "active_surface_area_ratio": "m^2_active/m^2_covered_ground",
        "source_height_m": "m",
        "receiver_height_m": "m",
    }
)

FRACTION_PARAMETERS = {
    "live_dry_mass_fraction",
    "dead_dry_mass_fraction",
    "pyrolysable_dry_mass_fraction",
    "radiative_fraction",
    "absorptivity",
    "emissivity",
}
STRICTLY_POSITIVE_PARAMETERS = {
    "fuel_load_kg_m2",
    "heat_of_combustion_J_kg_dry",
    "pyrolysable_dry_mass_fraction",
    "ignition_temperature_K",
    "peak_hrrpua_W_m2",
    "growth_time_s",
    "decay_time_s",
    "effective_heated_dry_mass_kg_m2",
    "specific_heat_J_kg_K",
    "receiver_area_ratio",
    "active_surface_area_ratio",
}


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


@dataclass(frozen=True, slots=True)
class EvidencedValue:
    value: float
    unit: str
    provenance: str


@dataclass(frozen=True, slots=True)
class ProfileBudget:
    available_energy_J_m2_covered: float
    curve_energy_J_m2_covered: float
    margin_J_m2_covered: float
    utilization_fraction: float


@dataclass(frozen=True, slots=True)
class VegetationFuelProfile:
    id: str
    label: str
    landcover_id: str
    burnable: bool
    evidence: str
    parameters: dict[str, EvidencedValue]
    budget: ProfileBudget | None

    def value(self, name: str) -> float:
        return self.parameters[name].value

    @property
    def weighted_moisture_content_dry_basis(self) -> float:
        if not self.burnable:
            return 0.0
        return self.value("live_dry_mass_fraction") * self.value(
            "live_moisture_content_dry_basis"
        ) + self.value("dead_dry_mass_fraction") * self.value("dead_moisture_content_dry_basis")


@dataclass(frozen=True, slots=True)
class VegetationProfileLibrary:
    path: Path
    library_id: str
    evidence: str
    production_ready: bool
    profiles: tuple[VegetationFuelProfile, ...]

    @property
    def by_id(self) -> dict[str, VegetationFuelProfile]:
        return {profile.id: profile for profile in self.profiles}

    @property
    def by_landcover(self) -> dict[str, VegetationFuelProfile]:
        return {profile.landcover_id: profile for profile in self.profiles}


def _evidenced_parameter(raw_value: Any, name: str) -> EvidencedValue:
    raw = _mapping(raw_value, name)
    _exact_keys(raw, {"value", "unit", "provenance"}, name)
    value = raw["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name}.value must be numeric")
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{name}.value must be finite and non-negative")
    parameter_name = name.rsplit(".", 1)[-1]
    expected_unit = PARAMETER_UNITS[parameter_name]
    if raw["unit"] != expected_unit:
        raise ValueError(f"{name}.unit must be {expected_unit!r}")
    if not isinstance(raw["provenance"], str) or not raw["provenance"].strip():
        raise ValueError(f"{name}.provenance must be a non-empty string")
    if parameter_name in STRICTLY_POSITIVE_PARAMETERS and numeric <= 0.0:
        raise ValueError(f"{name}.value must be positive")
    if parameter_name in FRACTION_PARAMETERS and numeric > 1.0:
        raise ValueError(f"{name}.value must lie in [0, 1]")
    return EvidencedValue(numeric, expected_unit, raw["provenance"])


def _budget(profile_id: str, parameters: dict[str, EvidencedValue]) -> ProfileBudget:
    def value(name: str) -> float:
        return parameters[name].value

    available = (
        value("fuel_load_kg_m2")
        * value("pyrolysable_dry_mass_fraction")
        * value("heat_of_combustion_J_kg_dry")
    )
    curve = (
        value("peak_hrrpua_W_m2")
        * value("active_surface_area_ratio")
        * (value("growth_time_s") / 3.0 + value("steady_time_s") + value("decay_time_s") / 2.0)
    )
    tolerance = max(1e-6, available * 1e-12)
    if curve > available + tolerance:
        raise ValueError(
            f"energy budget exceeded for profile {profile_id!r}: burn curve requires "
            f"{curve:.6g} J/m^2 but fuel supplies {available:.6g} J/m^2"
        )
    if abs(curve - available) <= tolerance:
        curve = available
    return ProfileBudget(
        available_energy_J_m2_covered=available,
        curve_energy_J_m2_covered=curve,
        margin_J_m2_covered=available - curve,
        utilization_fraction=curve / available,
    )


def load_vegetation_profiles(path: str | Path) -> VegetationProfileLibrary:
    """Load V02 profiles without substituting any missing physical value."""

    source = Path(path).resolve()
    with source.open(encoding="utf-8") as stream:
        root = _mapping(yaml.safe_load(stream), "vegetation-profile-library")
    _exact_keys(
        root,
        {"schema_version", "library_id", "evidence", "production_ready", "profiles"},
        "vegetation-profile-library",
    )
    if root["schema_version"] != "village-vegetation-profiles-1":
        raise ValueError("unsupported vegetation profile schema")
    for name in ("library_id", "evidence"):
        if not isinstance(root[name], str) or not root[name].strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not isinstance(root["production_ready"], bool):
        raise TypeError("production_ready must be boolean")
    raw_profiles = root["profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("profiles must be a non-empty list")

    profiles: list[VegetationFuelProfile] = []
    identifiers: set[str] = set()
    landcovers: set[str] = set()
    has_synthetic_value = False
    for index, raw_value in enumerate(raw_profiles):
        name = f"profiles[{index}]"
        raw = _mapping(raw_value, name)
        _exact_keys(
            raw,
            {"id", "label", "landcover_id", "burnable", "evidence", "parameters"},
            name,
        )
        identifier = raw["id"]
        landcover_id = raw["landcover_id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("profile IDs must be non-empty and unique")
        if landcover_id not in LANDCOVER_IDS or landcover_id in landcovers:
            raise ValueError("profile landcover_id must be valid and unique")
        if not isinstance(raw["label"], str) or not raw["label"]:
            raise ValueError(f"profile {identifier!r}.label must be non-empty")
        if not isinstance(raw["burnable"], bool):
            raise TypeError(f"profile {identifier!r}.burnable must be boolean")
        if not isinstance(raw["evidence"], str) or not raw["evidence"]:
            raise ValueError(f"profile {identifier!r}.evidence must be non-empty")
        raw_parameters = _mapping(raw["parameters"], f"profile {identifier!r}.parameters")
        expected = set(PARAMETER_UNITS) if raw["burnable"] else set()
        _exact_keys(raw_parameters, expected, f"profile {identifier!r}.parameters")
        parameters = {
            parameter_name: _evidenced_parameter(
                raw_parameters[parameter_name],
                f"profile {identifier!r}.{parameter_name}",
            )
            for parameter_name in PARAMETER_UNITS
            if raw["burnable"]
        }
        if raw["burnable"]:
            live = parameters["live_dry_mass_fraction"].value
            dead = parameters["dead_dry_mass_fraction"].value
            if abs(live + dead - 1.0) > 1e-12:
                raise ValueError(
                    f"profile {identifier!r} live/dead dry-mass fractions must sum to one"
                )
            budget = _budget(identifier, parameters)
            has_synthetic_value |= any(
                parameter.provenance.startswith("synthetic:") for parameter in parameters.values()
            )
        else:
            budget = None
        profiles.append(
            VegetationFuelProfile(
                id=identifier,
                label=raw["label"],
                landcover_id=landcover_id,
                burnable=raw["burnable"],
                evidence=raw["evidence"],
                parameters=MappingProxyType(parameters),
                budget=budget,
            )
        )
        identifiers.add(identifier)
        landcovers.add(landcover_id)
    missing_landcovers = set(LANDCOVER_IDS) - landcovers
    if missing_landcovers:
        raise ValueError(f"profile library is missing landcovers: {sorted(missing_landcovers)}")
    if root["production_ready"] and has_synthetic_value:
        raise ValueError(
            "a library containing synthetic parameter values cannot be production ready"
        )
    return VegetationProfileLibrary(
        path=source,
        library_id=root["library_id"],
        evidence=root["evidence"],
        production_ready=root["production_ready"],
        profiles=tuple(profiles),
    )


__all__ = [
    "PARAMETER_UNITS",
    "EvidencedValue",
    "ProfileBudget",
    "VegetationFuelProfile",
    "VegetationProfileLibrary",
    "load_vegetation_profiles",
]
