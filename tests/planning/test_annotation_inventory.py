"""Nested annotation identities differ from actual missing roof geometry."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from audit_batuan_annotation_inventory import inventory_audit


def record(key, left, bottom, right, top):
    return {
        "building_id": key,
        "polygons": [
            {
                "exterior": [
                    [left, bottom],
                    [right, bottom],
                    [right, top],
                    [left, top],
                    [left, bottom],
                ],
                "holes": [],
            }
        ],
    }


def test_nested_label_is_not_silently_counted_as_independent_or_double_fuel():
    result = inventory_audit(
        [record("parent", 0, 0, 10, 10), record("child", 2, 2, 4, 4)], {"parent"}
    )
    assert result["annotated_objects"] == 2
    assert result["independent_grid_objects"] == 1
    absent = result["unrepresented_objects"][0]
    assert absent["classification"] == "nested_annotation_requires_semantic_review"
    assert absent["overlapping_objects"][0]["fraction_of_absent"] == 1
    assert result["sum_annotation_area_m2"] == 104
    assert result["union_annotation_area_m2"] == 100
    assert result["automatic_geometry_or_fuel_change"] is False
    assert not result["allow_complete_independent_object_claim"]


def test_isolated_lost_building_is_not_misclassified_as_duplicate():
    result = inventory_audit([record("one", 0, 0, 10, 10), record("lost", 20, 0, 23, 3)], {"one"})
    row = result["unrepresented_objects"][0]
    assert row["classification"] == "isolated_unrepresented_geometry"
    assert not row["overlapping_objects"]
    assert result["union_annotation_area_m2"] == pytest.approx(109)
