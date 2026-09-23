"""Review unresolved V3 cells without silently assigning a fire material."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image

from pyfireca.village.workflow_v3 import load_v3_case_bundle

_NEIGHBOR_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))
_ADJACENCY_OFFSETS = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component_records(
    unknown: np.ndarray, material: np.ndarray
) -> tuple[list[dict[str, Any]], np.ndarray]:
    height, width = unknown.shape
    visited = np.zeros_like(unknown, dtype=bool)
    component_ids = np.zeros(unknown.shape, dtype=np.int32)
    records: list[dict[str, Any]] = []
    component_id = 0
    for row, col in np.argwhere(unknown):
        row, col = int(row), int(col)
        if visited[row, col]:
            continue
        component_id += 1
        stack = [(row, col)]
        visited[row, col] = True
        cells: list[tuple[int, int]] = []
        adjacent = Counter()
        while stack:
            current_row, current_col = stack.pop()
            cells.append((current_row, current_col))
            component_ids[current_row, current_col] = component_id
            for drow, dcol in _NEIGHBOR_OFFSETS:
                next_row, next_col = current_row + drow, current_col + dcol
                if (
                    0 <= next_row < height
                    and 0 <= next_col < width
                    and unknown[next_row, next_col]
                    and not visited[next_row, next_col]
                ):
                    visited[next_row, next_col] = True
                    stack.append((next_row, next_col))
            for drow, dcol in _ADJACENCY_OFFSETS:
                next_row, next_col = current_row + drow, current_col + dcol
                if 0 <= next_row < height and 0 <= next_col < width:
                    value = int(material[next_row, next_col])
                    if value >= 0:
                        adjacent[value] += 1
        rows = [value[0] for value in cells]
        cols = [value[1] for value in cells]
        records.append(
            {
                "component_id": component_id,
                "cell_count": len(cells),
                "area_m2": len(cells) * 1.0,
                "row_min": min(rows),
                "row_max": max(rows),
                "col_min": min(cols),
                "col_max": max(cols),
                "adjacent_known_material_counts": {
                    str(key): value for key, value in adjacent.most_common()
                },
            }
        )
    return records, component_ids


def review_unknown_v3(
    prepared_dir: str | Path,
    case_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create a spatial review of unknown cells; do not mutate the case bundle."""

    prepared = Path(prepared_dir)
    case = load_v3_case_bundle(case_dir)
    request = case.request
    unknown = np.asarray(request.domain_mask & ~request.known_mask, dtype=bool)
    cell_area = request.grid.cell_area_m2
    component_records, component_ids = _component_records(unknown, request.material_index)
    rgb = None
    background_name = "background.png"
    background = prepared / background_name
    if background.is_file():
        image = np.asarray(Image.open(background).convert("RGB"), dtype=np.float64)
        image = image.reshape((request.grid.height, request.grid.width, 3))
        unknown_rgb = image[unknown]
        excess_green = (2.0 * unknown_rgb[:, 1] - unknown_rgb[:, 0] - unknown_rgb[:, 2]) / (
            np.sum(unknown_rgb, axis=1) + 1.0
        )
        rgb = {
            "mean": [float(value) for value in np.mean(unknown_rgb, axis=0)],
            "p10": [float(value) for value in np.percentile(unknown_rgb, 10, axis=0)],
            "p50": [float(value) for value in np.percentile(unknown_rgb, 50, axis=0)],
            "p90": [float(value) for value in np.percentile(unknown_rgb, 90, axis=0)],
            "excess_green_mean": float(np.mean(excess_green)),
            "excess_green_p10": float(np.percentile(excess_green, 10)),
            "excess_green_p90": float(np.percentile(excess_green, 90)),
        }
    labels = {int(key): value for key, value in case.material_labels.items()}
    for record in component_records:
        record["area_m2"] = record["cell_count"] * cell_area
        record["adjacent_known_materials"] = {
            labels.get(int(key), f"material_{key}"): value
            for key, value in record.pop("adjacent_known_material_counts").items()
        }
    report: dict[str, Any] = {
        "schema_version": "village-v3-unknown-review-1",
        "prepared_dir": str(prepared.resolve()),
        "case_dir": str(Path(case_dir).resolve()),
        "status": "review_required" if np.any(unknown) else "no_unknown_cells",
        "unknown_cell_count": int(np.count_nonzero(unknown)),
        "unknown_area_m2": float(np.count_nonzero(unknown) * cell_area),
        "domain_cell_count": int(np.count_nonzero(request.domain_mask)),
        "cell_size_m": request.grid.cell_size_m,
        "working_crs": request.grid.crs,
        "rgb_summary": rgb,
        "component_count": len(component_records),
        "largest_components": sorted(
            component_records, key=lambda record: record["cell_count"], reverse=True
        )[:20],
        "source_hashes": {
            "prepared_manifest_sha256": _sha256(prepared / "manifest.json"),
            "case_manifest_sha256": _sha256(Path(case_dir) / "manifest.json"),
            "background_sha256": _sha256(background) if background.is_file() else None,
        },
        "policy_options": [
            {
                "id": "strict_error",
                "action": "keep_unknown_and_block_research_run",
                "research_allowed": True,
                "requires_field_review": False,
            },
            {
                "id": "explicit_inert_demo",
                "action": "assign_unknown_to_bare_or_road_in_a_new_demo_bundle_only",
                "research_allowed": False,
                "requires_field_review": True,
            },
            {
                "id": "field_material_mapping",
                "action": "map_each_reviewed_region_to_a_survey_material_class",
                "research_allowed": True,
                "requires_field_review": True,
            },
        ],
        "assumptions": {
            "rgb_statistics_are_descriptive_only": True,
            "component_connectivity": "4-neighbor",
            "adjacency_statistics": "8-neighbor_known_materials",
            "no_case_arrays_modified": True,
        },
    }
    if output_dir is not None:
        output = Path(output_dir)
        if output.exists():
            raise FileExistsError(f"unknown review output already exists: {output}")
        output.mkdir(parents=True)
        template = {
            "schema_version": "village-field-survey-1",
            "survey_id": "gaoyou-unknown-component-template",
            "records": [
                {
                    "observation_id": f"UNKNOWN-C{record['component_id']:04d}",
                    "object_id": f"UNKNOWN-C{record['component_id']:04d}",
                    "component": "ground",
                    "geometry_ref": f"v3-unknown-component/C{record['component_id']:04d}",
                    "material_class": "unknown",
                    "notes": (
                        "Template only: review the boundary and split this region if it "
                        "contains more than one material or component."
                    ),
                }
                for record in component_records
            ],
        }
        template_path = output / "unknown_component_survey.template.yml"
        template_path.write_text(
            yaml.safe_dump(template, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        report["mapping_template"] = template_path.name
        areas_template = {
            "schema_version": "village-geometry-areas-1",
            "source": "unknown_components.csv",
            "areas": {
                f"v3-unknown-component/C{record['component_id']:04d}": {
                    "area_m2": float(record["area_m2"])
                }
                for record in component_records
            },
        }
        areas_path = output / "unknown_geometry_areas.template.yml"
        areas_path.write_text(
            yaml.safe_dump(areas_template, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        report["geometry_areas_template"] = areas_path.name
        component_ids_path = output / "unknown_component_id.npy"
        np.save(component_ids_path, component_ids, allow_pickle=False)
        report["component_id_raster"] = component_ids_path.name
        (output / "unknown_review.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with (output / "unknown_components.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=(
                    "component_id",
                    "cell_count",
                    "area_m2",
                    "row_min",
                    "row_max",
                    "col_min",
                    "col_max",
                    "adjacent_known_materials",
                ),
            )
            writer.writeheader()
            for record in component_records:
                writer.writerow(record)
    return report


__all__ = ["review_unknown_v3"]
