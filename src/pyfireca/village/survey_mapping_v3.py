"""Validate field material mappings against a V3 unknown-area review."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from pyfireca.village.survey import load_field_survey

_GEOMETRY_PATTERN = re.compile(r"^v3-unknown-component/C(?P<component_id>[0-9]+)(?:/.*)?$")
_UNRESOLVED_CLASSES = frozenset({"unknown", "composite_unknown"})


def _read_component_ids(review_dir: Path) -> tuple[int, ...]:
    source = review_dir / "unknown_components.csv"
    if not source.is_file():
        raise FileNotFoundError(f"unknown component report not found: {source}")
    with source.open(encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream)
        component_ids = [int(row["component_id"]) for row in rows]
    if not component_ids or len(component_ids) != len(set(component_ids)):
        raise ValueError("unknown_components.csv must contain unique component IDs")
    return tuple(sorted(component_ids))


def validate_unknown_mapping_v3(
    review_dir: str | Path,
    survey_path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Check that every reviewed unknown component has a non-unknown survey mapping.

    A component may be split into multiple survey records by adding a suffix to
    ``v3-unknown-component/Cxxxx``.  This function only checks spatial reference
    completeness and material identity; it does not resolve cards or build cell
    arrays.
    """

    review = Path(review_dir)
    survey = load_field_survey(survey_path)
    expected_ids = _read_component_ids(review)
    expected = set(expected_ids)
    component_raster_path = review / "unknown_component_id.npy"
    if not component_raster_path.is_file():
        raise FileNotFoundError(f"unknown component raster not found: {component_raster_path}")
    component_raster = np.load(component_raster_path, allow_pickle=False)
    if component_raster.ndim != 2 or not np.issubdtype(component_raster.dtype, np.integer):
        raise ValueError("unknown_component_id.npy must be a two-dimensional integer array")
    raster_ids = {int(value) for value in np.unique(component_raster) if int(value) > 0}
    if raster_ids != expected:
        raise ValueError("unknown component raster IDs do not match unknown_components.csv")
    raster_counts = {
        component_id: int(np.count_nonzero(component_raster == component_id))
        for component_id in expected_ids
    }
    csv_counts: dict[int, int] = {}
    with (review / "unknown_components.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            csv_counts[int(row["component_id"])] = int(row["cell_count"])
    if raster_counts != csv_counts:
        raise ValueError("unknown component raster cell counts do not match CSV")
    mapped: dict[int, list[Any]] = {}
    unresolved: list[dict[str, str]] = []
    invalid_template_refs: list[dict[str, str]] = []
    for record in survey.records:
        match = _GEOMETRY_PATTERN.fullmatch(record.geometry_ref)
        if match is None:
            if record.observation_id.startswith("UNKNOWN-C"):
                invalid_template_refs.append(
                    {
                        "observation_id": record.observation_id,
                        "geometry_ref": record.geometry_ref,
                    }
                )
            continue
        component_id = int(match.group("component_id"))
        if component_id not in expected:
            invalid_template_refs.append(
                {
                    "observation_id": record.observation_id,
                    "geometry_ref": record.geometry_ref,
                }
            )
            continue
        mapped.setdefault(component_id, []).append(record)
        if record.material_class in _UNRESOLVED_CLASSES:
            unresolved.append(
                {
                    "observation_id": record.observation_id,
                    "component_id": str(component_id),
                    "material_class": record.material_class,
                }
            )
    missing = sorted(expected - mapped.keys())
    material_counts = Counter(
        record.material_class
        for records in mapped.values()
        for record in records
        if record.material_class not in _UNRESOLVED_CLASSES
    )
    issues: list[str] = []
    if missing:
        issues.append(f"missing_components={len(missing)}")
    if unresolved:
        issues.append(f"unresolved_material_records={len(unresolved)}")
    if invalid_template_refs:
        issues.append(f"invalid_component_references={len(invalid_template_refs)}")
    report: dict[str, Any] = {
        "schema_version": "village-v3-survey-mapping-validation-1",
        "review_dir": str(review.resolve()),
        "survey_path": str(Path(survey_path).resolve()),
        "survey_id": survey.survey_id,
        "status": "ready_for_resolution" if not issues else "blocked",
        "expected_component_count": len(expected_ids),
        "mapped_component_count": len(mapped),
        "missing_component_ids": missing,
        "unresolved_material_records": unresolved,
        "invalid_component_references": invalid_template_refs,
        "material_counts": dict(sorted(material_counts.items())),
        "spatial_component_raster": {
            "path": component_raster_path.name,
            "shape": list(component_raster.shape),
            "cell_counts_match_csv": True,
        },
        "issues": issues,
        "assumptions": {
            "split_geometry_suffix_allowed": True,
            "composite_unknown_blocks_resolution": True,
            "no_material_parameters_resolved": True,
            "no_case_arrays_modified": True,
        },
    }
    if output is not None:
        destination = Path(output)
        if destination.exists():
            raise FileExistsError(f"survey mapping validation output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


__all__ = ["validate_unknown_mapping_v3"]
