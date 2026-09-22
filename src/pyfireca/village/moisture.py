"""Versioned scenario-level initial moisture states for M02.

Material identity and water state are intentionally separate.  This loader
does not expose ``dry``/``wet`` survey choices and never supplies a missing
water value implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from pyfireca.village.assembly_presets import PresetParameter
from pyfireca.village.survey import MATERIAL_CLASS_IDS

SCHEMA_VERSION = "village-moisture-initialization-1"
MOISTURE_STATUS_IDS = ("scenario_assumed", "field_measured", "literature_reference")

_ROOT_KEYS = frozenset(
    {"schema_version", "package_id", "basis", "production_ready", "missing_policy", "states"}
)
_STATE_KEYS = frozenset({"id", "material_classes", "moisture_content_dry_basis", "status", "notes"})


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
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


@dataclass(frozen=True, slots=True)
class MoistureState:
    id: str
    material_classes: tuple[str, ...]
    moisture_content_dry_basis: PresetParameter
    status: str
    notes: str


@dataclass(frozen=True, slots=True)
class MoistureInitialization:
    path: Path
    package_id: str
    basis: str
    production_ready: bool
    missing_policy: str
    states: tuple[MoistureState, ...]

    @property
    def by_material_class(self) -> dict[str, MoistureState]:
        return {
            material_class: state
            for state in self.states
            for material_class in state.material_classes
        }


def _parameter(value: Any, name: str) -> PresetParameter:
    raw = _mapping(value, name)
    _exact_keys(raw, frozenset({"value", "unit", "provenance"}), name)
    number = raw["value"]
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise TypeError(f"{name}.value must be numeric")
    number = float(number)
    if not isfinite(number) or number < 0.0:
        raise ValueError(f"{name}.value must be finite and non-negative")
    if raw["unit"] != "kg/kg_dry":
        raise ValueError(f"{name}.unit must be 'kg/kg_dry'")
    return PresetParameter(number, "kg/kg_dry", _text(raw["provenance"], f"{name}.provenance"))


def parse_moisture_initialization(data: Any, *, path: Path | None = None) -> MoistureInitialization:
    root = _mapping(data, "moisture-initialization")
    _exact_keys(root, _ROOT_KEYS, "moisture-initialization")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"moisture-initialization.schema_version must be {SCHEMA_VERSION!r}")
    if root["basis"] != "dry_basis_ratio":
        raise ValueError("moisture initialization basis must be dry_basis_ratio")
    if root["production_ready"] is not False:
        raise ValueError("moisture initialization must keep production_ready false")
    if root["missing_policy"] != "error_if_burnable_unassigned":
        raise ValueError("unsupported moisture missing_policy")
    raw_states = root["states"]
    if not isinstance(raw_states, list) or not raw_states:
        raise ValueError("states must be a non-empty list")
    states: list[MoistureState] = []
    ids: set[str] = set()
    classes: set[str] = set()
    for index, raw_value in enumerate(raw_states):
        name = f"states[{index}]"
        raw = _mapping(raw_value, name)
        _exact_keys(raw, _STATE_KEYS, name)
        state_id = _text(raw["id"], f"{name}.id")
        if state_id in ids:
            raise ValueError(f"duplicate moisture state ID {state_id!r}")
        ids.add(state_id)
        raw_classes = raw["material_classes"]
        if not isinstance(raw_classes, list) or not raw_classes:
            raise ValueError(f"{name}.material_classes must be a non-empty list")
        material_classes = tuple(
            _enum(item, f"{name}.material_classes[{class_index}]", MATERIAL_CLASS_IDS)
            for class_index, item in enumerate(raw_classes)
        )
        if len(material_classes) != len(set(material_classes)):
            raise ValueError(f"{name}.material_classes must not contain duplicates")
        overlap = classes.intersection(material_classes)
        if overlap:
            raise ValueError(f"material classes have multiple moisture states: {sorted(overlap)}")
        classes.update(material_classes)
        states.append(
            MoistureState(
                id=state_id,
                material_classes=material_classes,
                moisture_content_dry_basis=_parameter(
                    raw["moisture_content_dry_basis"],
                    f"{name}.moisture_content_dry_basis",
                ),
                status=_enum(raw["status"], f"{name}.status", MOISTURE_STATUS_IDS),
                notes=_text(raw["notes"], f"{name}.notes"),
            )
        )
    return MoistureInitialization(
        path=path or Path("<memory>"),
        package_id=_text(root["package_id"], "package_id"),
        basis="dry_basis_ratio",
        production_ready=False,
        missing_policy="error_if_burnable_unassigned",
        states=tuple(states),
    )


def load_moisture_initialization(path: str | Path) -> MoistureInitialization:
    source = Path(path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return parse_moisture_initialization(data, path=source)


__all__ = [
    "MOISTURE_STATUS_IDS",
    "SCHEMA_VERSION",
    "MoistureInitialization",
    "MoistureState",
    "load_moisture_initialization",
    "parse_moisture_initialization",
]
