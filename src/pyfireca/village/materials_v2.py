"""Strict evidence-catalog support for effective_exposure_ca_v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

V2_PARAMETER_NAMES = (
    "fuel_load_kg_m2",
    "moisture_content_dry_basis",
    "heat_of_combustion_J_kg",
    "critical_heat_flux_W_m2",
    "ignition_temperature_K",
    "thermal_inertia_J2_m4_K2_s",
    "peak_hrrpua_W_m2",
    "radiative_fraction",
    "absorptivity",
    "growth_time_s",
    "steady_time_s",
    "decay_time_s",
)
V2_RUNTIME_MATERIAL_PARAMETERS = (
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
V2_CONSTRUCTION_PARAMETERS = (
    "effective_heated_dry_mass_kg_m2",
    "specific_heat_J_kg_K",
    "emissivity",
    "ambient_heat_transfer_coefficient_W_m2_K",
    "backing_conductance_W_m2_K",
    "receiver_area_m2",
    "active_surface_area_m2",
    "pyrolysable_dry_mass_fraction",
    "ignition_mode",
    "transport_kernel_calibration",
)


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class EvidenceMaterialV2:
    id: str
    label: str
    role: str
    burnable: bool
    evidence_level: str
    sources: tuple[str, ...]
    conditions_and_limits: str
    parameters: dict[str, float | None]

    @property
    def missing_runtime_parameters(self) -> tuple[str, ...]:
        if not self.burnable or self.role == "reference_only":
            return ()
        return tuple(
            name for name in V2_RUNTIME_MATERIAL_PARAMETERS if self.parameters[name] is None
        )


@dataclass(frozen=True, slots=True)
class EvidenceCatalogV2:
    production_ready: bool
    catalog_status: str
    materials: tuple[EvidenceMaterialV2, ...]
    source_registry: dict[str, str]
    additional_required_parameters: tuple[str, ...]
    restrictions: tuple[str, ...]

    @property
    def by_id(self) -> dict[str, EvidenceMaterialV2]:
        return {material.id: material for material in self.materials}

    @property
    def runnable_material_ids(self) -> tuple[str, ...]:
        return tuple(
            material.id
            for material in self.materials
            if material.burnable
            and material.role != "reference_only"
            and not material.missing_runtime_parameters
        )


def parse_material_catalog_v2(data: Any) -> EvidenceCatalogV2:
    """Parse the version-2 evidence catalog without filling missing values."""

    if not isinstance(data, dict):
        raise TypeError("material-catalog-2 root must be a mapping")
    _exact_keys(
        data,
        {
            "schema_version",
            "model",
            "production_ready",
            "catalog_status",
            "heat_of_combustion_basis",
            "thermal_inertia_unit",
            "missing_value_policy",
            "materials",
            "source_registry",
            "implementation_requirements",
        },
        "material-catalog-2",
    )
    if data["schema_version"] != "material-catalog-2":
        raise ValueError("unsupported v2 material catalog schema")
    if data["model"] != "effective_exposure_ca_v2":
        raise ValueError("v2 material catalog declares the wrong model")
    if data["heat_of_combustion_basis"] != "dry_mass_loss_effective":
        raise ValueError("v2 requires dry-mass-loss effective heat of combustion")
    if data["thermal_inertia_unit"] != "J^2/(m^4*K^2*s)":
        raise ValueError("unexpected thermal inertia unit")
    if data["missing_value_policy"] != "error_for_required_parameters":
        raise ValueError("v2 catalogs must reject missing required parameters")
    if not isinstance(data["production_ready"], bool):
        raise TypeError("production_ready must be boolean")
    if not isinstance(data["catalog_status"], str) or not data["catalog_status"]:
        raise ValueError("catalog_status must be a non-empty string")
    sources = data["source_registry"]
    if not isinstance(sources, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in sources.items()
    ):
        raise TypeError("source_registry must map source IDs to non-empty URLs")
    requirements = data["implementation_requirements"]
    if not isinstance(requirements, dict):
        raise TypeError("implementation_requirements must be a mapping")
    _exact_keys(
        requirements,
        {
            "surface_area_basis",
            "fuel_area_basis",
            "additional_required_parameters",
            "restrictions",
        },
        "implementation_requirements",
    )
    if requirements["surface_area_basis"] != "exposed_receptor_area":
        raise ValueError("unsupported v2 surface area basis")
    if requirements["fuel_area_basis"] != "ground_projected_area":
        raise ValueError("unsupported v2 fuel area basis")
    additional = tuple(requirements["additional_required_parameters"])
    if additional != V2_CONSTRUCTION_PARAMETERS:
        raise ValueError("v2 construction-parameter contract does not match the implementation")
    restrictions = tuple(requirements["restrictions"])
    if not restrictions or any(not isinstance(item, str) or not item for item in restrictions):
        raise TypeError("v2 restrictions must be non-empty strings")
    records = data["materials"]
    if not isinstance(records, list) or not records:
        raise ValueError("v2 materials must be a non-empty list")
    parsed: list[EvidenceMaterialV2] = []
    identifiers: set[str] = set()
    expected_material_keys = {
        "id",
        "label",
        "role",
        "burnable",
        "evidence_level",
        "sources",
        "conditions_and_limits",
        "parameters",
    }
    valid_roles = {"fuel", "reference_only", "inert_solid", "protective_cover", "fuel_break"}
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise TypeError(f"materials[{index}] must be a mapping")
        _exact_keys(raw, expected_material_keys, f"materials[{index}]")
        material_id = raw["id"]
        if not isinstance(material_id, str) or not material_id or material_id in identifiers:
            raise ValueError("v2 material IDs must be non-empty and unique")
        identifiers.add(material_id)
        if raw["role"] not in valid_roles:
            raise ValueError(f"unsupported v2 material role {raw['role']!r}")
        if not isinstance(raw["burnable"], bool):
            raise TypeError(f"material {material_id!r} burnable must be boolean")
        if raw["role"] in {"inert_solid", "protective_cover", "fuel_break"} and raw["burnable"]:
            raise ValueError(f"non-fuel material {material_id!r} cannot be burnable")
        for field in ("label", "evidence_level", "conditions_and_limits"):
            if not isinstance(raw[field], str) or not raw[field]:
                raise ValueError(f"material {material_id!r} requires non-empty {field}")
        material_sources = raw["sources"]
        if not isinstance(material_sources, list) or any(
            not isinstance(source_id, str) or source_id not in sources
            for source_id in material_sources
        ):
            raise ValueError(f"material {material_id!r} refers to an unknown source")
        parameters = raw["parameters"]
        if not isinstance(parameters, dict):
            raise TypeError(f"material {material_id!r} parameters must be a mapping")
        _exact_keys(parameters, set(V2_PARAMETER_NAMES), f"material {material_id!r} parameters")
        numeric: dict[str, float | None] = {}
        for name, raw_value in parameters.items():
            if raw_value is None:
                numeric[name] = None
                continue
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise TypeError(
                    f"material {material_id!r} parameter {name!r} must be numeric or null"
                )
            value = float(raw_value)
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"material {material_id!r} parameter {name!r} is invalid")
            numeric[name] = value
        for fraction in ("radiative_fraction", "absorptivity"):
            value = numeric[fraction]
            if value is not None and value > 1.0:
                raise ValueError(f"material {material_id!r} {fraction} must lie in [0, 1]")
        parsed.append(
            EvidenceMaterialV2(
                id=material_id,
                label=raw["label"],
                role=raw["role"],
                burnable=raw["burnable"],
                evidence_level=raw["evidence_level"],
                sources=tuple(material_sources),
                conditions_and_limits=raw["conditions_and_limits"],
                parameters=MappingProxyType(numeric),
            )
        )
    return EvidenceCatalogV2(
        production_ready=data["production_ready"],
        catalog_status=data["catalog_status"],
        materials=tuple(parsed),
        source_registry=MappingProxyType(dict(sources)),
        additional_required_parameters=additional,
        restrictions=restrictions,
    )


def load_material_catalog_v2(path: str | Path) -> EvidenceCatalogV2:
    with Path(path).open(encoding="utf-8") as stream:
        return parse_material_catalog_v2(yaml.safe_load(stream))


__all__ = [
    "V2_CONSTRUCTION_PARAMETERS",
    "V2_PARAMETER_NAMES",
    "V2_RUNTIME_MATERIAL_PARAMETERS",
    "EvidenceCatalogV2",
    "EvidenceMaterialV2",
    "load_material_catalog_v2",
    "parse_material_catalog_v2",
]
