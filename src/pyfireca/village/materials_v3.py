"""Parameter-level provenance catalog for village material research.

This evidence inventory does not resolve construction parameters or configure a run.
The v2 numerical solver continues to use explicit scenario parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from pyfireca.village.materials_v2 import V2_PARAMETER_NAMES, V2_RUNTIME_MATERIAL_PARAMETERS

PARAMETER_UNITS = MappingProxyType(
    {
        "fuel_load_kg_m2": "kg/m2",
        "moisture_content_dry_basis": "kg/kg_dry",
        "heat_of_combustion_J_kg": "J/kg_dry_mass_loss",
        "critical_heat_flux_W_m2": "W/m2",
        "ignition_temperature_K": "K",
        "thermal_inertia_J2_m4_K2_s": "J2/(m4*K2*s)",
        "peak_hrrpua_W_m2": "W/m2",
        "radiative_fraction": "1",
        "absorptivity": "1",
        "growth_time_s": "s",
        "steady_time_s": "s",
        "decay_time_s": "s",
    }
)
EVIDENCE_METHODS = frozenset({"measured", "derived", "assumed", "field_survey"})
SOURCE_KINDS = frozenset(
    {
        "peer_reviewed_journal_article",
        "peer_reviewed_conference_paper",
        "government_research_publication",
    }
)
MATERIAL_ROLES = frozenset(
    {"fuel", "reference_only", "inert_solid", "protective_cover", "fuel_break"}
)


def _mapping(value: Any, label: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a mapping")
    missing, unknown = keys - value.keys(), value.keys() - keys
    if missing or unknown:
        raise ValueError(f"{label} keys: missing={sorted(missing)}, unknown={sorted(unknown)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ParameterEvidence:
    value: float
    unit: str
    source_id: str
    locator: str
    method: str
    conditions: str


@dataclass(frozen=True, slots=True)
class MaterialEvidence:
    id: str
    label: str
    role: str
    scope: str
    burnable: bool
    conditions: str
    parameters: dict[str, ParameterEvidence | None]

    @property
    def missing_runtime_parameters(self) -> tuple[str, ...]:
        if not self.burnable or self.scope != "village_candidate":
            return ()
        return tuple(
            name for name in V2_RUNTIME_MATERIAL_PARAMETERS if self.parameters[name] is None
        )


@dataclass(frozen=True, slots=True)
class MaterialCatalogV3:
    catalog_status: str
    production_ready: bool
    sources: dict[str, dict[str, str]]
    materials: tuple[MaterialEvidence, ...]

    @property
    def by_id(self) -> dict[str, MaterialEvidence]:
        return {material.id: material for material in self.materials}

    @property
    def parameter_complete_village_materials(self) -> tuple[str, ...]:
        return tuple(
            material.id
            for material in self.materials
            if material.scope == "village_candidate"
            and material.burnable
            and not material.missing_runtime_parameters
        )

    @property
    def evidenced_parameter_count(self) -> int:
        """Return the number of non-null parameter records with provenance."""
        return sum(
            evidence is not None
            for material in self.materials
            for evidence in material.parameters.values()
        )


def parse_material_catalog_v3(data: Any) -> MaterialCatalogV3:
    root = _mapping(
        data,
        "material-catalog-3",
        {"schema_version", "model", "catalog_status", "production_ready", "sources", "materials"},
    )
    if root["schema_version"] != "material-catalog-3":
        raise ValueError("unsupported material catalog schema")
    if root["model"] != "effective_exposure_ca_v2":
        raise ValueError("material catalog declares the wrong model")
    status = _text(root["catalog_status"], "catalog_status")
    if not isinstance(root["production_ready"], bool):
        raise TypeError("production_ready must be boolean")
    raw_sources = root["sources"]
    if not isinstance(raw_sources, dict):
        raise TypeError("sources must be a mapping")
    sources: dict[str, dict[str, str]] = {}
    for source_id, value in raw_sources.items():
        _text(source_id, "source ID")
        record = _mapping(value, f"source {source_id}", {"title", "citation", "kind", "url"})
        url = _text(record["url"], f"source {source_id} URL")
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"source {source_id} needs an HTTP(S) URL")
        kind = _text(record["kind"], f"source {source_id} kind")
        if kind not in SOURCE_KINDS:
            raise ValueError(f"source {source_id} has unsupported source kind {kind}")
        sources[source_id] = {
            "title": _text(record["title"], f"source {source_id} title"),
            "citation": _text(record["citation"], f"source {source_id} citation"),
            "kind": kind,
            "url": url,
        }
    raw_materials = root["materials"]
    if not isinstance(raw_materials, list) or not raw_materials:
        raise ValueError("materials must be a non-empty list")
    materials: list[MaterialEvidence] = []
    ids: set[str] = set()
    for index, value in enumerate(raw_materials):
        label = f"materials[{index}]"
        material = _mapping(
            value,
            label,
            {"id", "label", "role", "scope", "burnable", "conditions", "parameters"},
        )
        material_id = _text(material["id"], f"{label}.id")
        if material_id in ids:
            raise ValueError(f"duplicate material ID {material_id}")
        ids.add(material_id)
        role = material["role"]
        scope = material["scope"]
        if role not in MATERIAL_ROLES or scope not in {"village_candidate", "reference_only"}:
            raise ValueError(f"{material_id} has invalid role or scope")
        if role == "reference_only" and scope != "reference_only":
            raise ValueError(f"{material_id} reference-only role cannot be a village candidate")
        burnable = material["burnable"]
        if not isinstance(burnable, bool):
            raise TypeError(f"{material_id}.burnable must be boolean")
        if role in {"inert_solid", "protective_cover", "fuel_break"} and burnable:
            raise ValueError(f"{material_id} non-fuel role cannot be burnable")
        raw_parameters = _mapping(
            material["parameters"], f"{material_id}.parameters", set(V2_PARAMETER_NAMES)
        )
        parameters: dict[str, ParameterEvidence | None] = {}
        for name in V2_PARAMETER_NAMES:
            raw = raw_parameters[name]
            if raw is None:
                parameters[name] = None
                continue
            entry = _mapping(
                raw,
                f"{material_id}.{name}",
                {"value", "unit", "source_id", "locator", "method", "conditions"},
            )
            number = entry["value"]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise TypeError(f"{material_id}.{name}.value must be numeric")
            number = float(number)
            if not isfinite(number) or number < 0:
                raise ValueError(f"{material_id}.{name}.value must be finite and non-negative")
            if name in {"radiative_fraction", "absorptivity"} and number > 1:
                raise ValueError(f"{material_id}.{name} fraction must be at most one")
            if entry["unit"] != PARAMETER_UNITS[name]:
                raise ValueError(f"{material_id}.{name} requires unit {PARAMETER_UNITS[name]}")
            source_id = _text(entry["source_id"], f"{material_id}.{name}.source_id")
            if source_id not in sources:
                raise ValueError(f"{material_id}.{name} has unknown source {source_id}")
            method = entry["method"]
            if method not in EVIDENCE_METHODS:
                raise ValueError(f"{material_id}.{name} has unsupported evidence method")
            parameters[name] = ParameterEvidence(
                value=number,
                unit=entry["unit"],
                source_id=source_id,
                locator=_text(entry["locator"], f"{material_id}.{name}.locator"),
                method=method,
                conditions=_text(entry["conditions"], f"{material_id}.{name}.conditions"),
            )
        materials.append(
            MaterialEvidence(
                id=material_id,
                label=_text(material["label"], f"{material_id}.label"),
                role=role,
                scope=scope,
                burnable=burnable,
                conditions=_text(material["conditions"], f"{material_id}.conditions"),
                parameters=MappingProxyType(parameters),
            )
        )
    if root["production_ready"]:
        raise ValueError(
            "material-catalog-3 cannot claim production readiness "
            "without construction and validation"
        )
    return MaterialCatalogV3(
        catalog_status=status,
        production_ready=False,
        sources=MappingProxyType(sources),
        materials=tuple(materials),
    )


def load_material_catalog_v3(path: str | Path) -> MaterialCatalogV3:
    with Path(path).open(encoding="utf-8") as stream:
        return parse_material_catalog_v3(yaml.safe_load(stream))
