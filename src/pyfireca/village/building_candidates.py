"""Inspectable roof-colour/shape candidates; no claim of measured building footprints."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def roof_candidate_mask(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Keep neutral/red/blue roof colours while excluding green vegetation and shadow."""
    from PIL import Image, ImageFilter

    r, g, b = rgb.astype(np.float32)
    brightness = (r + g + b) / 3
    saturation = (np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])) / (np.maximum.reduce([r, g, b]) + 1)
    exg = (2 * g - r - b) / (r + g + b + 1)
    neutral = (saturation < .26) & (exg < .09)
    coloured = ((b > 1.3 * r) & (b > 1.1 * g)) | ((r > 1.35 * g) & (r > 1.4 * b))
    mask = valid & (brightness >= 45) & (brightness <= 210) & (neutral | coloured)
    # Close one-pixel holes, then remove isolated narrow lines/roof-colour noise.
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    image = image.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    image = image.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return (np.asarray(image) > 0) & valid


def extract_building_candidates(imagery: Path, output: Path, quality_raster: Path | None = None) -> list[dict]:
    """Polygonize roof candidates, rejecting long road-like or very large shapes."""
    import rasterio
    import cv2
    from rasterio.features import shapes
    from shapely.geometry import shape, mapping, Polygon

    with rasterio.open(imagery) as source:
        valid = source.dataset_mask() > 0
        if quality_raster is not None:
            with rasterio.open(quality_raster) as quality:
                if quality.shape != source.shape or quality.transform != source.transform:
                    raise ValueError("resolution provenance grid differs from imagery")
                valid &= quality.read(1) == 2
        rgb = source.read([1, 2, 3])
        mask = roof_candidate_mask(rgb, valid)
        crs = source.crs.to_string()
        transform = source.transform
        width, height = source.width, source.height
    # Separate attached roofs using distance-transform peaks and image gradients.
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    maxima = (distance >= cv2.dilate(distance, np.ones((13,13), np.uint8)) - 1e-5) & (distance >= 3)
    _, seeds = cv2.connectedComponents(maxima.astype(np.uint8))
    markers = seeds.astype(np.int32) + 1
    markers[mask & (seeds == 0)] = 0
    watershed = cv2.watershed(np.ascontiguousarray(rgb.transpose(1,2,0)), markers)
    regions = np.where(mask & (watershed > 1), watershed, 0).astype(np.int32)
    records = []
    for geometry, value in shapes(regions, mask=regions > 0, transform=transform, connectivity=4):
        polygon = shape(geometry).simplify(.7, preserve_topology=True)
        if not polygon.is_valid or polygon.is_empty or polygon.geom_type != "Polygon":
            continue
        if not 25 <= polygon.area <= 600:
            continue
        xy = np.asarray(polygon.exterior.coords)
        xy = (xy - xy.min(axis=0)).astype(np.float32)
        _, dimensions, _ = cv2.minAreaRect(xy)
        rectangle_area = dimensions[0] * dimensions[1]
        aspect = max(dimensions) / max(min(dimensions), 1e-6)
        if aspect > 2.8 or polygon.area / max(rectangle_area, 1e-6) < .65:
            continue
        # Conservative footprint candidate: retain the polygon rather than its bounding box.
        identifier = f"BT{len(records)+1:04d}"
        records.append(dict(building_id=identifier, polygons=[dict(
            exterior=[list(point) for point in polygon.exterior.coords],
            holes=[[list(point) for point in ring.coords] for ring in polygon.interiors])],
            properties=dict(data_status="rgb_shape_candidate", area_m2=polygon.area,
                            method="roof_colour_watershed_shape_filter", field_verified=False)))
    output.mkdir(parents=True, exist_ok=True)
    (output / "buildings.json").write_text(json.dumps(dict(schema_version="village-buildings-1", crs=crs, buildings=records), ensure_ascii=False), encoding="utf-8")
    geojson = dict(type="FeatureCollection", name="Batuan building candidates", crs=dict(type="name", properties=dict(name=crs)), features=[
        dict(type="Feature", properties={"building_id": record["building_id"], **record["properties"]},
             geometry=mapping(Polygon(record["polygons"][0]["exterior"], record["polygons"][0]["holes"])))
        for record in records])
    (output / "building_candidates.geojson").write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    inverse = ~transform
    labelme = dict(version="5.0", flags={}, imagePath=str(imagery), imageData=None,
                   imageWidth=width, imageHeight=height, shapes=[
                       dict(label="building", shape_type="polygon", points=[list(inverse * tuple(point)) for point in record["polygons"][0]["exterior"][:-1]],
                            group_id=None, flags={"rgb_candidate": True}) for record in records])
    (output / "building_candidates.labelme.json").write_text(json.dumps(labelme, ensure_ascii=False), encoding="utf-8")
    (output / "building_detection_report.json").write_text(json.dumps(dict(
        imagery=str(imagery.resolve()), crs=crs, candidate_count=len(records),
        evidence="rgb_shape_candidate", trained_model=False, field_verified=False,
        rules=dict(area_m2=[25,600], max_aspect=2.8, min_rectangular_fill=.65,
                   brightness=[45,210], max_neutral_saturation=.26),
        limits=["Roads/bare terraces may be false positives", "Dark, shaded, attached or vegetation-covered roofs may be missed",
                "Connected roof groups may be merged; candidates are not a building census", "Lower-resolution fallback regions are excluded"],
    ), ensure_ascii=False, indent=2), encoding="utf-8")
    return records
