"""Strict, low-burden field-survey input for village material classification.

This module intentionally stores material identity and spatial references only.
Moisture is a time-dependent scenario state and is therefore not a survey
record field.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "village-field-survey-1"

COMPONENT_IDS = (
    "facade",
    "roof",
    "interior_wall",
    "contents",
    "ground",
    "vegetation",
)

MATERIAL_CLASS_IDS = (
    "wood_unspecified",
    "bamboo_unspecified",
    "thatch_straw",
    "masonry_brick",
    "concrete_mortar",
    "stone",
    "metal_sheet",
    "grass",
    "shrub",
    "tree_equivalent",
    "litter",
    "mixed_contents",
    "composite_unknown",
    "unknown",
)

QUANTITY_BAND_IDS = ("thin", "standard", "thick", "low", "medium", "high", "unknown")

_SURVEY_KEYS = frozenset({"schema_version", "survey_id", "records"})
_RECORD_REQUIRED_KEYS = frozenset(
    {"observation_id", "object_id", "component", "geometry_ref", "material_class"}
)
_RECORD_OPTIONAL_KEYS = frozenset({"quantity_band", "subtype", "notes"})


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


def _required_and_allowed_keys(
    value: dict[str, Any], required: frozenset[str], allowed: frozenset[str], name: str
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - allowed
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _enum(value: Any, name: str, allowed: tuple[str, ...]) -> str:
    text = _required_text(value, name)
    if text not in allowed:
        raise ValueError(f"{name} must be one of {list(allowed)!r}; got {text!r}")
    return text


@dataclass(frozen=True, slots=True)
class FieldSurveyRecord:
    """One observed or explicitly unknown material assignment."""

    observation_id: str
    object_id: str
    component: str
    geometry_ref: str
    material_class: str
    quantity_band: str | None = None
    subtype: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class FieldSurvey:
    """A versioned set of spatially referencable field observations."""

    path: Path
    survey_id: str
    records: tuple[FieldSurveyRecord, ...]


def parse_field_survey(data: Any, *, path: Path | None = None) -> FieldSurvey:
    """Validate an in-memory ``village-field-survey-1`` document.

    The parser deliberately rejects moisture or weather fields such as
    ``condition``, ``dry`` and ``wet``.  Those belong to a separate scenario
    initialization package and must not alter a stable material identity.
    """

    root = _mapping(data, "field-survey")
    _exact_keys(root, _SURVEY_KEYS, "field-survey")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"field-survey.schema_version must be {SCHEMA_VERSION!r}; "
            f"got {root['schema_version']!r}"
        )
    survey_id = _required_text(root["survey_id"], "survey_id")
    raw_records = root["records"]
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("records must be a non-empty list")

    records: list[FieldSurveyRecord] = []
    seen_observations: set[str] = set()
    for index, raw_value in enumerate(raw_records):
        location = f"records[{index}]"
        raw = _mapping(raw_value, location)
        _required_and_allowed_keys(
            raw,
            _RECORD_REQUIRED_KEYS,
            _RECORD_REQUIRED_KEYS | _RECORD_OPTIONAL_KEYS,
            location,
        )
        observation_id = _required_text(raw["observation_id"], f"{location}.observation_id")
        if observation_id in seen_observations:
            raise ValueError(f"duplicate observation_id {observation_id!r}")
        seen_observations.add(observation_id)
        quantity_band = None
        if "quantity_band" in raw:
            quantity_band = _enum(
                raw["quantity_band"], f"{location}.quantity_band", QUANTITY_BAND_IDS
            )
        records.append(
            FieldSurveyRecord(
                observation_id=observation_id,
                object_id=_required_text(raw["object_id"], f"{location}.object_id"),
                component=_enum(raw["component"], f"{location}.component", COMPONENT_IDS),
                geometry_ref=_required_text(raw["geometry_ref"], f"{location}.geometry_ref"),
                material_class=_enum(
                    raw["material_class"],
                    f"{location}.material_class",
                    MATERIAL_CLASS_IDS,
                ),
                quantity_band=quantity_band,
                subtype=_optional_text(raw.get("subtype"), f"{location}.subtype"),
                notes=_optional_text(raw.get("notes"), f"{location}.notes"),
            )
        )
    return FieldSurvey(path=path or Path("<memory>"), survey_id=survey_id, records=tuple(records))


def load_field_survey(path: str | Path) -> FieldSurvey:
    """Load and structurally validate a ``village-field-survey-1`` YAML file."""

    source = Path(path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return parse_field_survey(data, path=source)


__all__ = [
    "COMPONENT_IDS",
    "MATERIAL_CLASS_IDS",
    "QUANTITY_BAND_IDS",
    "SCHEMA_VERSION",
    "FieldSurvey",
    "FieldSurveyRecord",
    "load_field_survey",
    "parse_field_survey",
]
