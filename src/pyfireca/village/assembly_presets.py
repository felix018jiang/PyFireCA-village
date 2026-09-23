"""Strict construction presets for the M02 material-card resolver.

An assembly preset owns quantities and construction assumptions.  It is kept
separate from a material card so a field label such as ``wood_unspecified``
cannot silently choose a fuel load or a moisture state.  The steady part of a
burn curve is intentionally absent from the YAML and is derived from the
fuel-energy budget by :mod:`pyfireca.village.material_resolution`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from pyfireca.village.survey import COMPONENT_IDS

SCHEMA_VERSION = "village-assembly-presets-1"
PRESET_EVIDENCE_STATUSES = ("scenario_assumed", "evidence_checked", "pending_full_text")
IGNITION_MODES = ("piloted", "thermal")

BASE_PARAMETER_UNITS = MappingProxyType(
    {
        "heat_of_combustion_J_kg_dry": "J/kg_dry",
        "live_dry_mass_fraction": "1",
        "dead_dry_mass_fraction": "1",
        "pyrolysable_dry_mass_fraction": "1",
        "ignition_temperature_K": "K",
        "peak_hrrpua_W_m2": "W/m^2_active",
        "radiative_fraction": "1",
        "absorptivity": "1",
        "growth_time_s": "s",
        "decay_time_s": "s",
        "specific_heat_J_kg_K": "J/(kg*K)",
        "emissivity": "1",
        "ambient_heat_transfer_coefficient_W_m2_K": "W/(m^2*K)",
        "backing_conductance_W_m2_K": "W/(m^2*K)",
        "ignition_mode": "enum",
        "transport_kernel_calibration": "text",
    }
)
BAND_PARAMETER_UNITS = MappingProxyType(
    {
        "fuel_load_kg_m2": "kg/m^2_covered_ground",
        "effective_heated_dry_mass_kg_m2": "kg/m^2_covered_ground",
        "active_surface_area_ratio": "m^2_active/m^2_covered_ground",
        "receiver_area_ratio": "m^2_receiver/m^2_covered_ground",
        "source_height_m": "m",
        "receiver_height_m": "m",
    }
)

_ROOT_KEYS = frozenset(
    {"schema_version", "library_id", "production_ready", "burn_curve_policy", "presets"}
)
_PRESET_KEYS = frozenset(
    {
        "id",
        "version",
        "label",
        "material_card_id",
        "allowed_components",
        "burnable",
        "evidence_status",
        "evidence",
        "base_parameters",
        "quantity_bands",
    }
)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str] | frozenset[str], name: str) -> None:
    missing = set(expected) - value.keys()
    unknown = value.keys() - set(expected)
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _enum(value: Any, name: str, allowed: tuple[str, ...]) -> str:
    text = _text(value, name)
    if text not in allowed:
        raise ValueError(f"{name} must be one of {list(allowed)!r}; got {text!r}")
    return text


def _text_list(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    if not value and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    result = tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicate values")
    return result


@dataclass(frozen=True, slots=True)
class PresetParameter:
    value: float | str
    unit: str
    provenance: str


@dataclass(frozen=True, slots=True)
class QuantityBand:
    id: str
    fuel_load_kg_m2: PresetParameter
    effective_heated_dry_mass_kg_m2: PresetParameter
    active_surface_area_ratio: PresetParameter
    receiver_area_ratio: PresetParameter
    source_height_m: PresetParameter
    receiver_height_m: PresetParameter


@dataclass(frozen=True, slots=True)
class AssemblyPreset:
    id: str
    version: str
    label: str
    material_card_id: str
    allowed_components: tuple[str, ...]
    burnable: bool
    evidence_status: str
    evidence: str
    base_parameters: dict[str, PresetParameter]
    quantity_bands: dict[str, QuantityBand]


@dataclass(frozen=True, slots=True)
class AssemblyPresetLibrary:
    path: Path
    library_id: str
    production_ready: bool
    burn_curve_policy: str
    presets: tuple[AssemblyPreset, ...]

    @property
    def by_id(self) -> dict[str, AssemblyPreset]:
        return {preset.id: preset for preset in self.presets}


def _parameter(
    raw: Any, name: str, units: MappingProxyType, *, parameter_id: str | None = None
) -> PresetParameter:
    value = _mapping(raw, name)
    _exact_keys(value, {"value", "unit", "provenance"}, name)
    number = value["value"]
    parameter_id = parameter_id or name
    expected_unit = units[parameter_id]
    if expected_unit in {"enum", "text"}:
        if not isinstance(number, str) or not number.strip():
            raise ValueError(f"{name}.value must be non-empty text")
        parsed: float | str = number.strip()
    else:
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise TypeError(f"{name}.value must be numeric")
        parsed = float(number)
        if not isfinite(parsed) or parsed < 0.0:
            raise ValueError(f"{name}.value must be finite and non-negative")
        if parameter_id in {"radiative_fraction", "absorptivity", "emissivity"} and parsed > 1.0:
            raise ValueError(f"{name}.value must lie in [0, 1]")
        if parameter_id == "pyrolysable_dry_mass_fraction" and parsed > 1.0:
            raise ValueError(f"{name}.value must lie in [0, 1]")
    if value["unit"] != expected_unit:
        raise ValueError(f"{name}.unit must be {expected_unit!r}")
    return PresetParameter(parsed, expected_unit, _text(value["provenance"], f"{name}.provenance"))


def _parse_band(raw: Any, band_id: str, name: str) -> QuantityBand:
    mapping = _mapping(raw, name)
    _exact_keys(mapping, set(BAND_PARAMETER_UNITS), name)
    values = {
        parameter: _parameter(
            mapping[parameter],
            f"{name}.{parameter}",
            BAND_PARAMETER_UNITS,
            parameter_id=parameter,
        )
        for parameter in BAND_PARAMETER_UNITS
    }
    return QuantityBand(id=band_id, **values)


def parse_assembly_preset_library(data: Any, *, path: Path | None = None) -> AssemblyPresetLibrary:
    root = _mapping(data, "assembly-preset-library")
    _exact_keys(root, _ROOT_KEYS, "assembly-preset-library")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"assembly-preset-library.schema_version must be {SCHEMA_VERSION!r}")
    if root["production_ready"] is not False:
        raise ValueError("assembly preset library must keep production_ready false")
    policy = _text(root["burn_curve_policy"], "burn_curve_policy")
    if policy != "derive_steady_time_from_energy_budget":
        raise ValueError("unsupported burn_curve_policy")
    raw_presets = root["presets"]
    if not isinstance(raw_presets, list) or not raw_presets:
        raise ValueError("presets must be a non-empty list")
    presets: list[AssemblyPreset] = []
    ids: set[str] = set()
    for index, raw_value in enumerate(raw_presets):
        name = f"presets[{index}]"
        raw = _mapping(raw_value, name)
        _exact_keys(raw, _PRESET_KEYS, name)
        preset_id = _text(raw["id"], f"{name}.id")
        if preset_id in ids:
            raise ValueError(f"duplicate preset ID {preset_id!r}")
        ids.add(preset_id)
        components = _text_list(raw["allowed_components"], f"{preset_id}.allowed_components")
        invalid = set(components) - set(COMPONENT_IDS)
        if invalid:
            raise ValueError(
                f"{preset_id}.allowed_components has unsupported values {sorted(invalid)}"
            )
        burnable = raw["burnable"]
        if not isinstance(burnable, bool):
            raise TypeError(f"{preset_id}.burnable must be boolean")
        raw_base = _mapping(raw["base_parameters"], f"{preset_id}.base_parameters")
        if burnable:
            _exact_keys(raw_base, set(BASE_PARAMETER_UNITS), f"{preset_id}.base_parameters")
            base = {
                parameter: _parameter(
                    raw_base[parameter],
                    f"{preset_id}.base_parameters.{parameter}",
                    BASE_PARAMETER_UNITS,
                    parameter_id=parameter,
                )
                for parameter in BASE_PARAMETER_UNITS
            }
            if base["ignition_mode"].value not in IGNITION_MODES:
                raise ValueError(f"{preset_id}.base_parameters.ignition_mode is unsupported")
            live_fraction = float(base["live_dry_mass_fraction"].value)
            dead_fraction = float(base["dead_dry_mass_fraction"].value)
            if abs(live_fraction + dead_fraction - 1.0) > 1e-9:
                raise ValueError(f"{preset_id} live/dead dry-mass fractions must sum to one")
        else:
            if raw_base:
                raise ValueError(f"inert preset {preset_id!r} must have empty base_parameters")
            base = {}
        raw_bands = _mapping(raw["quantity_bands"], f"{preset_id}.quantity_bands")
        if not raw_bands:
            raise ValueError(f"{preset_id}.quantity_bands must not be empty")
        bands = {
            _text(band_id, f"{preset_id}.quantity_bands ID"): _parse_band(
                band_value,
                _text(band_id, f"{preset_id}.quantity_bands ID"),
                f"{preset_id}.quantity_bands.{band_id}",
            )
            for band_id, band_value in raw_bands.items()
        }
        presets.append(
            AssemblyPreset(
                id=preset_id,
                version=_text(raw["version"], f"{preset_id}.version"),
                label=_text(raw["label"], f"{preset_id}.label"),
                material_card_id=_text(raw["material_card_id"], f"{preset_id}.material_card_id"),
                allowed_components=components,
                burnable=burnable,
                evidence_status=_enum(
                    raw["evidence_status"],
                    f"{preset_id}.evidence_status",
                    PRESET_EVIDENCE_STATUSES,
                ),
                evidence=_text(raw["evidence"], f"{preset_id}.evidence"),
                base_parameters=MappingProxyType(base),
                quantity_bands=MappingProxyType(bands),
            )
        )
    return AssemblyPresetLibrary(
        path=path or Path("<memory>"),
        library_id=_text(root["library_id"], "library_id"),
        production_ready=False,
        burn_curve_policy=policy,
        presets=tuple(presets),
    )


def load_assembly_preset_library(path: str | Path) -> AssemblyPresetLibrary:
    source = Path(path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return parse_assembly_preset_library(data, path=source)


__all__ = [
    "BAND_PARAMETER_UNITS",
    "BASE_PARAMETER_UNITS",
    "IGNITION_MODES",
    "PRESET_EVIDENCE_STATUSES",
    "SCHEMA_VERSION",
    "AssemblyPreset",
    "AssemblyPresetLibrary",
    "PresetParameter",
    "QuantityBand",
    "load_assembly_preset_library",
    "parse_assembly_preset_library",
]
