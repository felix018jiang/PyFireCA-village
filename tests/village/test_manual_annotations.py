"""Manual footprint imports preserve labels, spatial alignment and repair provenance."""

import hashlib
import json

import pytest
from shapely.geometry import Polygon

from pyfireca.village.annotations import load_labelme_buildings

REFERENCE = dict(width_px=100, height_px=100, affine_pixel_to_world=[2, 0, 1000, 0, -2, 2000])


def write_labels(tmp_path, shapes):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(dict(imageWidth=100, imageHeight=100, shapes=shapes)))
    return path


def test_manual_bridge_is_opt_in_and_keeps_source_label(tmp_path):
    shapes = [
        dict(label=label, shape_type="polygon", points=[[10, 10], [20, 10], [20, 20], [10, 20]])
        for label in ["building", "fengyuqiao"]
    ]
    path = write_labels(tmp_path, shapes)
    assert len(load_labelme_buildings(path, REFERENCE)) == 1
    records = load_labelme_buildings(path, REFERENCE, labels=("building", "fengyuqiao"))
    assert len(records) == 2
    assert records[1]["properties"]["source_label"] == "fengyuqiao"
    assert records[1]["properties"]["data_status"] == "observed_annotation"
    assert records[0]["polygons"][0]["exterior"][0] == [1020, 1980]


def test_self_intersection_requires_explicit_repair_and_preserves_all_parts(tmp_path):
    path = write_labels(
        tmp_path,
        [
            dict(
                label="building",
                shape_type="polygon",
                points=[[10, 10], [20, 20], [10, 20], [20, 10]],
            )
        ],
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="invalid geometry"):
        load_labelme_buildings(path, REFERENCE)
    record = load_labelme_buildings(path, REFERENCE, repair_invalid=True)[0]
    assert len(record["polygons"]) == 2
    assert all(Polygon(part["exterior"]).is_valid for part in record["polygons"])
    assert sum(Polygon(part["exterior"]).area for part in record["polygons"]) == 200
    assert record["properties"]["geometry_repaired"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_manual_image_dimensions_must_match(tmp_path):
    path = write_labels(tmp_path, [])
    with pytest.raises(ValueError, match="dimensions"):
        load_labelme_buildings(path, {**REFERENCE, "width_px": 101})
