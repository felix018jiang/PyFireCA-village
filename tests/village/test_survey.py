from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pyfireca.village.survey import (
    COMPONENT_IDS,
    MATERIAL_CLASS_IDS,
    load_field_survey,
)

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples/village/material_cards/survey.example.yml"


def _write(path: Path, value: object) -> Path:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _base() -> dict:
    return {
        "schema_version": "village-field-survey-1",
        "survey_id": "test-survey",
        "records": [
            {
                "observation_id": "OBS-1",
                "object_id": "B1",
                "component": "facade",
                "geometry_ref": "buildings/B1",
                "material_class": "wood_unspecified",
            }
        ],
    }


def test_m00_example_requires_only_field_classification_inputs() -> None:
    survey = load_field_survey(EXAMPLE)

    assert survey.survey_id == "gaoyou-field-survey-example"
    assert len(survey.records) == 4
    assert survey.records[0].material_class == "wood_unspecified"
    assert survey.records[1].component == "interior_wall"
    assert survey.records[2].material_class == "concrete_mortar"
    assert survey.records[3].material_class == "unknown"


def test_m00_enumerations_are_stable() -> None:
    survey = load_field_survey(EXAMPLE)

    assert survey.records[0].component in COMPONENT_IDS
    assert all(record.material_class in MATERIAL_CLASS_IDS for record in survey.records)


def test_unknown_material_is_allowed_but_unknown_component_is_rejected(tmp_path: Path) -> None:
    value = _base()
    value["records"][0]["material_class"] = "unknown"
    assert load_field_survey(_write(tmp_path / "unknown.yml", value)).records[0].material_class == (
        "unknown"
    )
    value["records"][0]["component"] = "wall"
    with pytest.raises(ValueError, match="component must be one of"):
        load_field_survey(_write(tmp_path / "bad-component.yml", value))


def test_m00_rejects_moisture_or_condition_fields(tmp_path: Path) -> None:
    value = _base()
    value["records"][0]["condition"] = "dry"

    with pytest.raises(ValueError, match=r"contains unknown keys.*condition"):
        load_field_survey(_write(tmp_path / "dry.yml", value))


def test_m00_rejects_missing_required_geometry_and_duplicate_observations(tmp_path: Path) -> None:
    value = _base()
    del value["records"][0]["geometry_ref"]
    with pytest.raises(ValueError, match=r"missing keys.*geometry_ref"):
        load_field_survey(_write(tmp_path / "missing-geometry.yml", value))

    value = _base()
    value["records"].append(dict(value["records"][0]))
    with pytest.raises(ValueError, match="duplicate observation_id"):
        load_field_survey(_write(tmp_path / "duplicate.yml", value))


def test_m00_optional_quantity_band_is_not_moisture(tmp_path: Path) -> None:
    value = _base()
    value["records"][0]["quantity_band"] = "standard"
    value["records"][0]["notes"] = "厚度待测"

    record = load_field_survey(_write(tmp_path / "quantity.yml", value)).records[0]
    assert record.quantity_band == "standard"
    assert record.notes == "厚度待测"


def test_m00_rejects_empty_records_and_duplicate_survey_fields(tmp_path: Path) -> None:
    value = _base()
    value["records"] = []
    with pytest.raises(ValueError, match="records must be a non-empty list"):
        load_field_survey(_write(tmp_path / "empty.yml", value))

    value = _base()
    value["unexpected"] = True
    with pytest.raises(ValueError, match=r"contains unknown keys.*unexpected"):
        load_field_survey(_write(tmp_path / "extra.yml", value))
