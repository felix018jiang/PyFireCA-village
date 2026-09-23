"""Strict M02 material-resolution workflow for V3 field surveys."""

from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from pyfireca.village.assembly_presets import load_assembly_preset_library
from pyfireca.village.material_cards import load_material_card_library
from pyfireca.village.material_resolution import ResolvedSurveyCase, resolve_material_selections
from pyfireca.village.moisture import load_moisture_initialization
from pyfireca.village.survey import load_field_survey


def load_geometry_areas(path: str | Path) -> dict[str, float]:
    """Load an explicit ``village-geometry-areas-1`` area mapping."""

    source = Path(path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        root = yaml.safe_load(stream)
    if not isinstance(root, dict):
        raise TypeError("geometry-areas must be a mapping")
    if set(root) != {"schema_version", "source", "areas"}:
        raise ValueError("geometry-areas must contain exactly schema_version, source and areas")
    if root["schema_version"] != "village-geometry-areas-1":
        raise ValueError("unsupported geometry-areas schema")
    if not isinstance(root["source"], str) or not root["source"].strip():
        raise ValueError("geometry-areas.source must be a non-empty string")
    raw_areas = root["areas"]
    if not isinstance(raw_areas, dict) or not raw_areas:
        raise ValueError("geometry-areas.areas must be a non-empty mapping")
    areas: dict[str, float] = {}
    for raw_ref, raw_value in raw_areas.items():
        if not isinstance(raw_ref, str) or not raw_ref.strip():
            raise ValueError("geometry reference keys must be non-empty strings")
        if not isinstance(raw_value, dict) or set(raw_value) != {"area_m2"}:
            raise ValueError(f"geometry area {raw_ref!r} must contain only area_m2")
        value = raw_value["area_m2"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"geometry area {raw_ref!r} must be numeric")
        value = float(value)
        if not isfinite(value) or value <= 0.0:
            raise ValueError(f"geometry area {raw_ref!r} must be finite and positive")
        areas[raw_ref.strip()] = value
    return dict(sorted(areas.items()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_survey_v3(
    survey_path: str | Path,
    *,
    material_cards_path: str | Path,
    assemblies_path: str | Path,
    moisture_path: str | Path,
    geometry_areas_path: str | Path,
    mode: str = "demonstration",
    case_id: str = "v3-resolved-survey",
    output: str | Path | None = None,
) -> ResolvedSurveyCase:
    """Resolve survey identities to explicit M02 quantities without building a grid."""

    survey = load_field_survey(survey_path)
    cards = load_material_card_library(material_cards_path)
    assemblies = load_assembly_preset_library(assemblies_path)
    moisture = load_moisture_initialization(moisture_path)
    areas = load_geometry_areas(geometry_areas_path)
    resolved = resolve_material_selections(
        survey,
        cards,
        assemblies,
        moisture,
        geometry_areas_m2=areas,
        mode=mode,
        case_id=case_id,
    )
    if output is not None:
        destination = Path(output)
        if destination.exists():
            raise FileExistsError(f"resolved survey output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = resolved.as_dict()
        payload["input_hashes"] = {
            "survey": _sha256(Path(survey_path).expanduser().resolve()),
            "material_cards": _sha256(Path(material_cards_path).expanduser().resolve()),
            "assemblies": _sha256(Path(assemblies_path).expanduser().resolve()),
            "moisture": _sha256(Path(moisture_path).expanduser().resolve()),
            "geometry_areas": _sha256(Path(geometry_areas_path).expanduser().resolve()),
        }
        payload["resolution_status"] = "ready" if resolved.can_run else "blocked"
        payload["no_cell_arrays_written"] = True
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved


__all__ = ["load_geometry_areas", "resolve_survey_v3"]
