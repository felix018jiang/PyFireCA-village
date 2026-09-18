"""Building-annotation adapters for village preparation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


def load_labelme_buildings(
    path: str | Path, image_reference: dict[str, Any]
) -> list[dict[str, Any]]:
    """Convert LabelMe building rectangles/polygons from pixels to world metres."""

    try:
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise ImportError("LabelMe buildings require the 'village' optional dependencies") from exc
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
        raise ValueError("LabelMe annotation must contain a shapes list")
    width = image_reference["width_px"]
    height = image_reference["height_px"]
    if data.get("imageWidth") != width or data.get("imageHeight") != height:
        raise ValueError("LabelMe image dimensions do not match the satellite image")
    affine = image_reference["affine_pixel_to_world"]

    def transform(point: Any) -> tuple[float, float]:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("LabelMe points must be [x, y] pixel pairs")
        pixel_x, pixel_y = (float(value) for value in point)
        if not np.isfinite([pixel_x, pixel_y]).all():
            raise ValueError("LabelMe points must be finite")
        if not 0.0 <= pixel_x <= width or not 0.0 <= pixel_y <= height:
            raise ValueError("LabelMe point lies outside the satellite image")
        a, b, c, d, e, f = (float(value) for value in affine)
        return a * pixel_x + b * pixel_y + c, d * pixel_x + e * pixel_y + f

    prepared: list[dict[str, Any]] = []
    for source_index, shape in enumerate(data["shapes"]):
        if shape.get("label") != "building":
            continue
        shape_type = shape.get("shape_type")
        points = shape.get("points")
        if not isinstance(points, list):
            raise ValueError(f"LabelMe shape {source_index} has no points list")
        if shape_type == "rectangle":
            if len(points) != 2:
                raise ValueError("LabelMe rectangles must contain two corner points")
            (x1, y1), (x2, y2) = points
            pixel_ring = [
                [min(x1, x2), min(y1, y2)],
                [max(x1, x2), min(y1, y2)],
                [max(x1, x2), max(y1, y2)],
                [min(x1, x2), max(y1, y2)],
            ]
        elif shape_type == "polygon":
            if len(points) < 3:
                raise ValueError("LabelMe polygons must contain at least three points")
            pixel_ring = points
        else:
            raise ValueError(
                f"unsupported LabelMe building shape_type {shape_type!r} at index {source_index}"
            )
        geometry = Polygon([transform(point) for point in pixel_ring])
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0.0:
            raise ValueError(f"LabelMe building at index {source_index} has invalid geometry")
        building_id = f"B{len(prepared) + 1:04d}"
        prepared.append(
            {
                "building_id": building_id,
                "polygons": [
                    {
                        "exterior": [list(point) for point in geometry.exterior.coords],
                        "holes": [],
                    }
                ],
                "properties": {
                    "annotation_format": "labelme",
                    "source_shape_index": source_index,
                    "source_shape_type": shape_type,
                    "data_status": "observed_annotation",
                },
            }
        )
    if not prepared:
        raise ValueError("LabelMe annotation contains no shapes labelled 'building'")
    return prepared


def write_prepared_buildings(records: list[dict[str, Any]], output: str | Path) -> None:
    """Write normalized world-coordinate building records."""

    Path(output).write_text(
        json.dumps(
            {
                "schema_version": "prepared-buildings-1",
                "coordinate_space": "world_m",
                "buildings": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


__all__ = ["load_labelme_buildings", "write_prepared_buildings"]
