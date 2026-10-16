"""RGB-only candidate land cover for imagery review, without combustion parameters."""
# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

LABELS = {
    0: "未辨明地表 / 阴影",
    1: "林木 / 灌丛候选",
    2: "草地 / 农田候选",
    3: "裸土 / 干燥地表候选",
    4: "硬化地表 / 屋顶候选",
    5: "水体候选（可能混有蓝色屋顶）",
    255: "影像范围外",
}
COLORS = {
    0: "#9f6fa8",
    1: "#207549",
    2: "#a9cc52",
    3: "#d5a26d",
    4: "#c3c9d0",
    5: "#459ed4",
    255: "#000000",
}


def classify_rgb_candidates(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Return broad spectral candidates; shadows and ambiguous pixels stay unknown.

    The fixed rules are inspectable heuristics, not a trained semantic model.
    RGB cannot determine roof construction, understory fuel, or fuel moisture.
    """
    if rgb.shape != (3, *valid.shape) or valid.dtype != np.bool_:
        raise ValueError("RGB and validity mask must share a grid")
    r, g, b = rgb.astype(np.float32)
    brightness = (r + g + b) / 3
    exg = (2 * g - r - b) / (r + g + b + 1)
    visible = valid & (brightness >= 25)
    vegetation = visible & (exg >= 0.10) & (g >= 1.05 * r) & (g >= 1.02 * b)
    result = np.full(valid.shape, 0, dtype=np.uint8)
    result[vegetation & (brightness < 75)] = 1
    result[vegetation & (brightness >= 75)] = 2
    nonvegetation = visible & ~vegetation
    soil = nonvegetation & (r >= 1.08 * g) & (r >= 1.15 * b)
    result[soil] = 3
    neutral = nonvegetation & ~soil & (brightness >= 70) & (exg < 0.10)
    result[neutral] = 4
    water = nonvegetation & (brightness < 100) & (b >= 1.20 * r) & (g >= 1.05 * r)
    result[water] = 5
    result[~valid] = 255
    return result


def complete_rgb_candidates(rgb: np.ndarray, classes: np.ndarray, valid: np.ndarray):
    """Infer unclassified valid cells from chromaticity and nearby classified cover.

    Existing classes are fixed seeds. Deep shadows rely more on spatial context;
    brighter pixels use normalised RGB as well. No calibrated accuracy is implied.
    """
    import cv2

    if rgb.shape != (3, *valid.shape) or classes.shape != valid.shape:
        raise ValueError("RGB, classes and validity must share a grid")
    if not np.isin(classes[valid], [0, 1, 2, 3, 4, 5]).all():
        raise ValueError("unsupported valid landcover class")
    result = classes.copy()
    original_unknown = valid & (classes == 0)
    reason = np.zeros(valid.shape, dtype=np.uint8)
    reason[~valid] = 255
    values = rgb.astype(np.float32)
    r, g, b = values
    total = r + g + b
    exg = (2 * g - r - b) / (total + 1)
    green_shadow = (
        original_unknown & (total >= 8) & (exg >= 0.08) & (g >= 1.03 * r) & (g >= 1.02 * b)
    )
    result[green_shadow] = 1
    reason[green_shadow] = 1
    remaining = valid & (result == 0)
    if remaining.any():
        seeds = valid & (result > 0)
        if not seeds.any():
            raise ValueError("No classified neighbours to support ground-cover inference")
        chroma = np.moveaxis(values / np.maximum(total, 1)[None, :, :], 0, -1)
        best_cost = np.full(valid.shape, np.inf, dtype=np.float32)
        chosen = np.zeros(valid.shape, dtype=np.uint8)
        # Fixed seeds prevent inferred regions from recursively reinforcing themselves.
        for code in range(1, 6):
            seed = seeds & (result == code)
            if not seed.any():
                continue
            distance, labels = cv2.distanceTransformWithLabels(
                (~seed).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL
            )
            lookup = np.zeros((int(labels.max()) + 1, 3), dtype=np.float32)
            lookup[labels[seed]] = chroma[seed]
            colour_distance = np.linalg.norm(chroma - lookup[labels], axis=-1)
            support = cv2.boxFilter(
                seed.astype(np.float32), -1, (9, 9), normalize=True, borderType=cv2.BORDER_CONSTANT
            )
            reliability = np.clip(total / 90, 0.05, 1.0)
            cost = (
                distance / 12 + reliability * colour_distance / 0.12 - 0.45 * np.log(support + 0.03)
            )
            # A few blue roofs must not turn whole shadowed hillsides into water.
            if code == 5:
                cost = np.where(
                    (support >= 0.25) | ((b > 1.2 * r) & (g > 1.05 * r) & (total >= 30)),
                    cost,
                    np.inf,
                )
            select = remaining & (cost < best_cost)
            best_cost[select] = cost[select]
            chosen[select] = code
        if (chosen[remaining] == 0).any():
            raise ValueError("No plausible RGB/context class for remaining cells")
        result[remaining] = chosen[remaining]
        reason[remaining] = 2
    result[~valid] = 255
    report = dict(
        method="shadow_chromaticity_and_fixed_neighbour_seed_cost_v1",
        original_unknown_cell_count=int(original_unknown.sum()),
        shadow_green_cell_count=int(green_shadow.sum()),
        contextual_cell_count=int(remaining.sum()),
        completed_class_counts={
            str(code): int((original_unknown & (result == code)).sum()) for code in range(1, 6)
        },
        final_unknown_cell_count=int((valid & (result == 0)).sum()),
        cost=(
            "distance_cells/12 + clip(RGB_sum/90,0.05,1)*chroma_distance/0.12 "
            "- 0.45*log(local_9x9_seed_fraction+0.03)"
        ),
        evidence="inferred_RGB_and_neighbourhood_hypothesis_not_observed_material",
        accuracy_validated=False,
    )
    return result, reason, report


def prepare_rgb_landcover(
    imagery: Path, output: Path, cell_size_m: float = 2.0, *, complete_unknown: bool = False
) -> Path:
    """Classify a metric GeoTIFF on an aligned grid and save reviewable artifacts."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    if not math.isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be positive")
    if output.exists():
        raise FileExistsError(output)
    with rasterio.open(imagery) as source:
        if (
            source.crs is None
            or not source.crs.is_projected
            or source.crs.linear_units.lower() not in {"metre", "meter", "metres", "meters"}
        ):
            raise ValueError("imagery must use a metric projected CRS")
        if source.count < 3 or source.dtypes[:3] != ("uint8",) * 3:
            raise ValueError("imagery requires uint8 RGB")
        left = math.floor(source.bounds.left / cell_size_m) * cell_size_m
        top = math.ceil(source.bounds.top / cell_size_m) * cell_size_m
        width = math.ceil((source.bounds.right - left) / cell_size_m)
        height = math.ceil((top - source.bounds.bottom) / cell_size_m)
        transform = from_origin(left, top, cell_size_m, cell_size_m)
        rgb = np.zeros((3, height, width), dtype=np.uint8)
        for index in range(3):
            reproject(
                rasterio.band(source, index + 1),
                rgb[index],
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=transform,
                dst_crs=source.crs,
                resampling=Resampling.average,
            )
        validity = np.zeros((height, width), dtype=np.uint8)
        reproject(
            source.dataset_mask(),
            validity,
            src_transform=source.transform,
            src_crs=source.crs,
            dst_transform=transform,
            dst_crs=source.crs,
            resampling=Resampling.nearest,
        )
        valid = validity > 0
        classes = classify_rgb_candidates(rgb, valid)
        original_classes = classes.copy()
        completion = None
        inference_reason = np.where(valid, 0, 255).astype(np.uint8)
        if complete_unknown:
            classes, inference_reason, completion = complete_rgb_candidates(rgb, classes, valid)
        evidence = np.where(valid, np.where(classes == 0, 0, 1), 255).astype(np.uint8)
        profile = dict(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="uint8",
            crs=source.crs,
            transform=transform,
            nodata=255,
            compress="deflate",
            tiled=True,
        )
        grid = dict(
            height=height,
            width=width,
            cell_size_m=cell_size_m,
            x_min_m=left,
            y_max_m=top,
            coordinate_mode="projected",
            crs=source.crs.to_string(),
        )
    output.mkdir(parents=True)
    arrays = output / "arrays"
    arrays.mkdir()
    for name, values in (("landcover", classes), ("evidence", evidence)):
        with rasterio.open(output / f"{name}.tif", "w", **profile) as target:
            target.write(values, 1)
        np.save(arrays / f"{name}_index.npy", values, allow_pickle=False)
    np.save(arrays / "domain_mask.npy", valid, allow_pickle=False)
    np.save(arrays / "known_mask.npy", valid & (classes != 0), allow_pickle=False)
    np.save(arrays / "original_landcover_index.npy", original_classes, allow_pickle=False)
    np.save(
        arrays / "inferred_mask.npy",
        valid & (original_classes == 0) & (classes != 0),
        allow_pickle=False,
    )
    np.save(arrays / "inference_reason.npy", inference_reason, allow_pickle=False)
    if complete_unknown:
        with rasterio.open(output / "inference_reason.tif", "w", **profile) as target:
            target.write(inference_reason, 1)
    (output / "landcover.u8").write_bytes(classes.tobytes())
    valid_count = int(valid.sum())
    report = {
        "schema_version": "village-rgb-landcover-review-1",
        "source_imagery": str(imagery.resolve()),
        "source_sha256": hashlib.sha256(imagery.read_bytes()).hexdigest(),
        "grid": grid,
        "labels": LABELS,
        "colors": COLORS,
        "evidence": "rgb_hypothesis",
        "simulation_ready": False,
        "method": "fixed_RGB_spectral_rules_v1"
        + ("_with_context_completion" if complete_unknown else ""),
        "completion": completion,
        "inference_reason_codes": {
            "0": "initial_RGB_class",
            "1": "shadow_green_chromaticity",
            "2": "RGB_and_neighbourhood",
            "255": "outside_imagery",
        },
        "rules": {
            "excess_green": "(2G-R-B)/(R+G+B+1)",
            "vegetation": "brightness>=25, ExG>=0.10, G>=1.05R, G>=1.02B",
            "vegetation_split": "brightness<75: woody candidate; otherwise grass/crop candidate",
            "soil": "nonvegetation, R>=1.08G, R>=1.15B",
            "hard_surface": "nonvegetation, not soil, brightness>=70, ExG<0.10",
            "water": "nonvegetation, brightness<100, B>=1.20R, G>=1.05R",
            "unknown": "RGB/context hypothesis completion"
            if complete_unknown
            else "shadow brightness<25 or no rule match; never assigned inert fuel",
        },
        "valid_cell_count": valid_count,
        "class_counts": {str(code): int((classes == code).sum()) for code in LABELS},
        "class_area_m2": {
            str(code): int((classes == code).sum()) * cell_size_m**2 for code in LABELS
        },
        "limitations": [
            "RGB spectral rules have no measured classification accuracy or calibrated confidence",
            "vegetation brightness does not reliably separate trees, shrubs, grass and crops",
            "roads and roofs are grouped; wood, concrete and tile roof materials are unresolved",
            "blue roofs may resemble water; canopy-obscured ground remains unresolved",
            "map labels, shadows and mixed pixels can cause errors",
            "no fuel load, moisture, combustion properties or terrain inferred",
        ],
    }
    (output / "classification_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "grid.json").write_text(json.dumps(grid, indent=2), encoding="utf-8")
    return output
