from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.survey_mapping_v3 import validate_unknown_mapping_v3


def _review(tmp_path: Path) -> Path:
    review = tmp_path / "review"
    review.mkdir()
    with (review / "unknown_components.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("component_id", "cell_count"))
        writer.writeheader()
        writer.writerows(
            ({"component_id": "1", "cell_count": "3"}, {"component_id": "2", "cell_count": "4"})
        )
    np.save(
        review / "unknown_component_id.npy",
        np.asarray([[1, 1, 1, 2, 2, 2, 2]], dtype=np.int32),
    )
    return review


def _survey(path: Path, records: list[dict[str, str]]) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "village-field-survey-1",
                "survey_id": "mapping-test",
                "records": records,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_mapping_validation_blocks_missing_and_unknown_components(tmp_path: Path) -> None:
    review = _review(tmp_path)
    survey = tmp_path / "survey.yml"
    _survey(
        survey,
        [
            {
                "observation_id": "UNKNOWN-C0001",
                "object_id": "UNKNOWN-C0001",
                "component": "ground",
                "geometry_ref": "v3-unknown-component/C0001",
                "material_class": "unknown",
            }
        ],
    )

    report = validate_unknown_mapping_v3(review, survey)

    assert report["status"] == "blocked"
    assert report["missing_component_ids"] == [2]
    assert report["unresolved_material_records"][0]["material_class"] == "unknown"
    assert report["assumptions"]["no_case_arrays_modified"] is True


def test_mapping_validation_accepts_explicit_split_components(tmp_path: Path) -> None:
    review = _review(tmp_path)
    survey = tmp_path / "survey.yml"
    _survey(
        survey,
        [
            {
                "observation_id": "UNKNOWN-C0001-A",
                "object_id": "UNKNOWN-C0001",
                "component": "vegetation",
                "geometry_ref": "v3-unknown-component/C0001/part-a",
                "material_class": "grass",
            },
            {
                "observation_id": "UNKNOWN-C0001-B",
                "object_id": "UNKNOWN-C0001",
                "component": "vegetation",
                "geometry_ref": "v3-unknown-component/C0001/part-b",
                "material_class": "shrub",
            },
            {
                "observation_id": "UNKNOWN-C0002",
                "object_id": "UNKNOWN-C0002",
                "component": "ground",
                "geometry_ref": "v3-unknown-component/C0002",
                "material_class": "concrete_mortar",
            },
        ],
    )
    output = tmp_path / "mapping-validation.json"

    report = validate_unknown_mapping_v3(review, survey, output=output)

    assert report["status"] == "ready_for_resolution"
    assert report["mapped_component_count"] == 2
    assert report["material_counts"] == {
        "concrete_mortar": 1,
        "grass": 1,
        "shrub": 1,
    }
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ready_for_resolution"
