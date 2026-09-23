from __future__ import annotations

import json
from pathlib import Path

import yaml

from pyfireca.village.resolution_workflow_v3 import load_geometry_areas, resolve_survey_v3

ROOT = Path(__file__).parents[2]
CARDS = ROOT / "examples/village/material_cards/catalog.yml"
ASSEMBLIES = ROOT / "examples/village/material_cards/assemblies.synthetic.yml"
MOISTURE = ROOT / "examples/village/material_cards/moisture.initial.synthetic.yml"


def _survey(path: Path, material_class: str = "unknown") -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "village-field-survey-1",
                "survey_id": "resolution-test",
                "records": [
                    {
                        "observation_id": "OBS-001",
                        "object_id": "C001",
                        "component": "ground",
                        "geometry_ref": "geom-001",
                        "material_class": material_class,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _areas(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "village-geometry-areas-1",
                "source": "test",
                "areas": {"geom-001": {"area_m2": 4.0}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_geometry_area_loader_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "areas.yml"
    _areas(path)

    assert load_geometry_areas(path) == {"geom-001": 4.0}


def test_resolution_workflow_writes_blocked_auditable_report(tmp_path: Path) -> None:
    survey = tmp_path / "survey.yml"
    areas = tmp_path / "areas.yml"
    output = tmp_path / "resolved.json"
    _survey(survey)
    _areas(areas)

    resolved = resolve_survey_v3(
        survey,
        material_cards_path=CARDS,
        assemblies_path=ASSEMBLIES,
        moisture_path=MOISTURE,
        geometry_areas_path=areas,
        output=output,
    )

    assert resolved.can_run is False
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["resolution_status"] == "blocked"
    assert report["no_cell_arrays_written"] is True
    assert report["issues"]
