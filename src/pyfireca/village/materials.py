"""Strict material-catalog loading for village simulations."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_ROOT = {"schema_version", "library_id", "materials"}
_REQUIRED_MATERIAL = {"id", "label", "role", "burnable", "evidence", "source_id", "parameters"}
_PARAMETERS = {
    "fuel_load_kg_m2",
    "heat_of_combustion_J_kg",
    "mass_loss_flux_kg_m2_s",
    "active_area_ratio",
    "emission_fraction",
    "absorptivity",
    "ignition_dose_J_m2",
    "exposure_decay_s",
}
_POSITIVE = {
    "fuel_load_kg_m2",
    "heat_of_combustion_J_kg",
    "mass_loss_flux_kg_m2_s",
    "active_area_ratio",
    "ignition_dose_J_m2",
    "exposure_decay_s",
}
_FRACTIONS = {"active_area_ratio", "emission_fraction", "absorptivity"}


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _exact_keys(value: dict[str, Any], required: set[str], name: str) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise ValueError(f"{name} is missing required keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class VillageMaterial:
    id: str
    label: str
    role: str
    burnable: bool
    evidence: str
    source_id: str
    parameters: dict[str, float]


@dataclass(frozen=True, slots=True)
class VillageMaterialCatalog:
    library_id: str
    materials: tuple[VillageMaterial, ...]

    @property
    def by_id(self) -> dict[str, VillageMaterial]:
        return {material.id: material for material in self.materials}


def parse_material_catalog(data: Any, *, mode: str = "synthetic_demo") -> VillageMaterialCatalog:
    """Parse and validate a material-catalog data structure."""

    root = _mapping(data, "material catalog")
    _exact_keys(root, _REQUIRED_ROOT, "material catalog")
    if root["schema_version"] != "material-catalog-1":
        raise ValueError("unsupported material catalog schema_version")
    if not isinstance(root["library_id"], str) or not root["library_id"].strip():
        raise ValueError("library_id must be a non-empty string")
    if mode not in {"synthetic_demo", "research"}:
        raise ValueError("mode must be 'synthetic_demo' or 'research'")
    records = root["materials"]
    if not isinstance(records, list) or not records:
        raise ValueError("materials must be a non-empty list")
    materials: list[VillageMaterial] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(records):
        record = _mapping(raw, f"materials[{index}]")
        _exact_keys(record, _REQUIRED_MATERIAL, f"materials[{index}]")
        material_id = record["id"]
        if not isinstance(material_id, str) or not material_id.strip():
            raise ValueError(f"materials[{index}].id must be a non-empty string")
        if material_id == "unknown":
            raise ValueError("'unknown' is reserved for missing-data semantics")
        if material_id in identifiers:
            raise ValueError(f"duplicate material id: {material_id}")
        identifiers.add(material_id)
        for field in ("label", "evidence", "source_id"):
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError(f"material {material_id!r} field {field!r} must be non-empty")
        if record["role"] not in {"building_fuel", "surface_fuel", "inert"}:
            raise ValueError(f"material {material_id!r} has unsupported role")
        if not isinstance(record["burnable"], bool):
            raise TypeError(f"material {material_id!r} burnable must be boolean")
        if record["role"] == "inert" and record["burnable"]:
            raise ValueError(f"inert material {material_id!r} cannot be burnable")
        parameters = _mapping(record["parameters"], f"material {material_id!r} parameters")
        unknown = parameters.keys() - _PARAMETERS
        if unknown:
            raise ValueError(f"material {material_id!r} has unknown parameters: {sorted(unknown)}")
        expected = _PARAMETERS if record["burnable"] else set()
        if parameters.keys() != expected:
            missing = expected - parameters.keys()
            extra = parameters.keys() - expected
            raise ValueError(
                f"material {material_id!r} parameter mismatch; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        parsed: dict[str, float] = {}
        for name, raw_value in parameters.items():
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise TypeError(f"material {material_id!r} parameter {name!r} must be numeric")
            value = float(raw_value)
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"material {material_id!r} parameter {name!r} is invalid")
            if name in _POSITIVE and value <= 0.0:
                raise ValueError(f"material {material_id!r} parameter {name!r} must be positive")
            if name in _FRACTIONS and value > 1.0:
                raise ValueError(f"material {material_id!r} parameter {name!r} must not exceed 1")
            parsed[name] = value
        if mode == "research" and record["burnable"] and record["evidence"] == "synthetic":
            raise ValueError(f"research mode rejects synthetic parameters for {material_id!r}")
        materials.append(
            VillageMaterial(
                id=material_id,
                label=record["label"],
                role=record["role"],
                burnable=record["burnable"],
                evidence=record["evidence"],
                source_id=record["source_id"],
                parameters=parsed,
            )
        )
    return VillageMaterialCatalog(library_id=root["library_id"], materials=tuple(materials))


def load_material_catalog(
    path: str | Path, *, mode: str = "synthetic_demo"
) -> VillageMaterialCatalog:
    """Load a UTF-8 YAML material catalog without accepting custom tags."""

    with Path(path).open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return parse_material_catalog(data, mode=mode)
