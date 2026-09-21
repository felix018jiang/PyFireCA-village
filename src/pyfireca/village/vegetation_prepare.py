"""Spatial preparation for the village vegetation model (V01).

This module deliberately stops before assigning combustion parameters.  It
creates a metric analysis grid, records observation provenance, and preserves
unknown cells so that later stages cannot silently treat missing information
as non-combustible land.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca.village.annotations import load_labelme_buildings
from pyfireca.village.domain import VillageGridSpec

LANDCOVER_IDS = (
    "bare_or_road",
    "grass",
    "shrub",
    "forest_floor",
    "tree_equivalent",
    "building",
)
EVIDENCE_IDS = ("unknown", "rgb_hypothesis", "observed_annotation")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_local_metric_crs(source_crs: Any, bounds: Any) -> tuple[Any, dict[str, float]]:
    """Choose the UTM zone containing the source raster centre."""

    from pyproj import CRS, Proj, Transformer

    source = CRS.from_user_input(source_crs)
    centre_x = (float(bounds.left) + float(bounds.right)) / 2.0
    centre_y = (float(bounds.bottom) + float(bounds.top)) / 2.0
    lon, lat = Transformer.from_crs(source, 4326, always_xy=True).transform(centre_x, centre_y)
    zone = math.floor((lon + 180.0) / 6.0) + 1
    zone = min(60, max(1, zone))
    epsg = (32600 if lat >= 0.0 else 32700) + zone
    target = CRS.from_epsg(epsg)
    axis_units = {axis.unit_name.lower() for axis in target.axis_info}
    if not target.is_projected or not axis_units <= {"metre", "meter"}:
        raise ValueError(f"working CRS must be projected in metres: {target}")
    source_factors = Proj(source).get_factors(lon, lat) if source.is_projected else None
    target_factors = Proj(target).get_factors(lon, lat)
    return target, {
        "centre_longitude_deg": float(lon),
        "centre_latitude_deg": float(lat),
        "source_scale_factor": (
            float(source_factors.meridional_scale) if source_factors is not None else 1.0
        ),
        "working_scale_factor": float(target_factors.meridional_scale),
    }


def transform_geometry(geometry: Any, source_crs: Any, target_crs: Any) -> Any:
    """Transform every coordinate in a geometry, including holes and parts."""

    from pyproj import Transformer
    from shapely.ops import transform

    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    result = transform(transformer.transform, geometry)
    if result.is_empty or not result.is_valid:
        raise ValueError("geometry became empty or invalid after reprojection")
    return result


def geometry_to_polygons(geometry: Any) -> list[dict[str, list[list[float]]]]:
    """Serialize Polygon/MultiPolygon without dropping interior rings."""

    if geometry.geom_type == "Polygon":
        parts = [geometry]
    elif geometry.geom_type == "MultiPolygon":
        parts = list(geometry.geoms)
    else:
        raise TypeError(
            f"building geometry must be Polygon or MultiPolygon, got {geometry.geom_type}"
        )
    return [
        {
            "exterior": [[float(x), float(y)] for x, y in part.exterior.coords],
            "holes": [[[float(x), float(y)] for x, y in ring.coords] for ring in part.interiors],
        }
        for part in parts
    ]


def _record_geometry(record: dict[str, Any]) -> Any:
    from shapely.geometry import MultiPolygon, Polygon

    parts = [Polygon(part["exterior"], holes=part.get("holes", [])) for part in record["polygons"]]
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def reproject_building_records(
    records: Iterable[dict[str, Any]], source_crs: Any, target_crs: Any
) -> list[dict[str, Any]]:
    """Reproject normalized building records while retaining topology."""

    transformed = []
    for record in records:
        geometry = transform_geometry(_record_geometry(record), source_crs, target_crs)
        transformed.append(
            {
                "building_id": record["building_id"],
                "polygons": geometry_to_polygons(geometry),
                "properties": dict(record.get("properties", {})),
            }
        )
    return transformed


def building_cell_intersections(
    records: Iterable[dict[str, Any]], grid: VillageGridSpec, tolerance_m2: float = 1e-9
) -> tuple[list[tuple[str, int, int, float]], np.ndarray, dict[str, list[tuple[int, int, float]]]]:
    """Intersect buildings with cells and return union coverage per cell.

    Per-building rows retain attribution.  The raster coverage is calculated
    from a geometric union so overlapping annotations never yield coverage
    above one.
    """

    from shapely.geometry import box
    from shapely.ops import unary_union

    rows: list[tuple[str, int, int, float]] = []
    by_cell: dict[tuple[int, int], list[Any]] = defaultdict(list)
    by_building: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
    for record in records:
        geometry = _record_geometry(record)
        min_x, min_y, max_x, max_y = geometry.bounds
        first_col = max(0, math.floor((min_x - grid.x_min_m) / grid.cell_size_m))
        last_col = min(
            grid.width - 1,
            math.ceil((max_x - grid.x_min_m) / grid.cell_size_m) - 1,
        )
        first_row = max(0, math.floor((grid.y_max_m - max_y) / grid.cell_size_m))
        last_row = min(
            grid.height - 1,
            math.ceil((grid.y_max_m - min_y) / grid.cell_size_m) - 1,
        )
        if first_col > last_col or first_row > last_row:
            continue
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                x0 = grid.x_min_m + col * grid.cell_size_m
                y1 = grid.y_max_m - row * grid.cell_size_m
                cell = box(x0, y1 - grid.cell_size_m, x0 + grid.cell_size_m, y1)
                intersection = geometry.intersection(cell)
                area = float(intersection.area)
                if area <= tolerance_m2:
                    continue
                rows.append((record["building_id"], row, col, area))
                by_building[record["building_id"]].append((row, col, area))
                by_cell[(row, col)].append(intersection)
    coverage = np.zeros(grid.shape, dtype=np.float32)
    for (row, col), geometries in by_cell.items():
        area = float(unary_union(geometries).area)
        coverage[row, col] = min(1.0, area / grid.cell_area_m2)
    rows.sort(key=lambda value: (value[0], value[1], value[2]))
    return rows, coverage, dict(by_building)


def _aligned_grid(bounds: Any, cell_size_m: float, crs: Any) -> tuple[VillageGridSpec, Any]:
    from rasterio.transform import from_origin

    left = math.floor(bounds[0] / cell_size_m) * cell_size_m
    bottom = math.floor(bounds[1] / cell_size_m) * cell_size_m
    right = math.ceil(bounds[2] / cell_size_m) * cell_size_m
    top = math.ceil(bounds[3] / cell_size_m) * cell_size_m
    width = round((right - left) / cell_size_m)
    height = round((top - bottom) / cell_size_m)
    grid = VillageGridSpec(
        height=height,
        width=width,
        cell_size_m=cell_size_m,
        x_min_m=left,
        y_max_m=top,
        coordinate_mode="projected",
        crs=crs.to_string(),
    )
    return grid, from_origin(left, top, cell_size_m, cell_size_m)


def _classify_rgb(
    rgb: np.ndarray, domain: np.ndarray, building_coverage: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Make conservative RGB hypotheses; low-green cells remain unknown."""

    landcover = np.full(domain.shape, -1, dtype=np.int16)
    evidence = np.zeros(domain.shape, dtype=np.uint8)
    coverage = np.zeros(domain.shape, dtype=np.float32)
    building = domain & (building_coverage > 1e-9)
    landcover[building] = LANDCOVER_IDS.index("building")
    evidence[building] = EVIDENCE_IDS.index("observed_annotation")
    coverage[building] = building_coverage[building]

    values = rgb.astype(np.float32)
    total = values.sum(axis=0) + 1.0
    exg = (2.0 * values[1] - values[0] - values[2]) / total
    candidates = domain & ~building
    candidate_values = exg[candidates]
    if candidate_values.size:
        vegetation_threshold = max(0.03, float(np.quantile(candidate_values, 0.45)))
        vegetation = candidates & (exg >= vegetation_threshold)
        vegetation_values = exg[vegetation]
    else:
        vegetation_threshold = 0.03
        vegetation = np.zeros(domain.shape, dtype=np.bool_)
        vegetation_values = np.array([], dtype=np.float32)
    if vegetation_values.size:
        shrub_threshold = float(np.quantile(vegetation_values, 0.65))
        tree_threshold = float(np.quantile(vegetation_values, 0.88))
        grass = vegetation & (exg < shrub_threshold)
        shrub = vegetation & (exg >= shrub_threshold) & (exg < tree_threshold)
        tree = vegetation & (exg >= tree_threshold)
        for mask, name in (
            (grass, "grass"),
            (shrub, "shrub"),
            (tree, "tree_equivalent"),
        ):
            landcover[mask] = LANDCOVER_IDS.index(name)
            evidence[mask] = EVIDENCE_IDS.index("rgb_hypothesis")
            coverage[mask] = 1.0
    else:
        shrub_threshold = vegetation_threshold
        tree_threshold = vegetation_threshold
    rules = {
        "method": "normalized_excess_green_quantiles",
        "formula": "(2G-R-B)/(R+G+B+1)",
        "vegetation_threshold": vegetation_threshold,
        "shrub_quantile_within_vegetation": 0.65,
        "shrub_threshold": shrub_threshold,
        "tree_quantile_within_vegetation": 0.88,
        "tree_threshold": tree_threshold,
        "low_green_policy": "unknown; RGB alone is insufficient to assert bare ground or road",
        "forest_floor_policy": "unresolved; canopy-obscured fuel requires V02 input",
    }
    return landcover, evidence, coverage, rules


def _write_geotiff(path: Path, array: np.ndarray, transform: Any, crs: Any, nodata: Any) -> None:
    import rasterio

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=array.shape[1],
        height=array.shape[0],
        count=1,
        dtype=array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
    ) as dataset:
        dataset.write(array, 1)


def prepare_vegetation_spatial(
    imagery_path: str | Path,
    labelme_path: str | Path,
    output_dir: str | Path,
    *,
    cell_size_m: float = 2.0,
    working_crs: str | None = None,
    ignition_building_id: str = "B0233",
) -> Path:
    """Build the V01 spatial package without running a fire simulation."""

    import rasterio
    from PIL import Image
    from pyproj import CRS, Geod, Proj, Transformer
    from rasterio.warp import Resampling, reproject, transform_bounds

    imagery = Path(imagery_path).resolve()
    annotations = Path(labelme_path).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if not math.isfinite(cell_size_m) or cell_size_m <= 0.0:
        raise ValueError("cell_size_m must be finite and positive")

    with rasterio.open(imagery) as source:
        if source.crs is None:
            raise ValueError("satellite image requires a CRS")
        if source.count < 3:
            raise ValueError("satellite image requires at least three colour bands")
        source_crs = CRS.from_user_input(source.crs)
        selected_crs, scale = choose_local_metric_crs(source.crs, source.bounds)
        target_crs = CRS.from_user_input(working_crs) if working_crs else selected_crs
        if not target_crs.is_projected or any(
            axis.unit_name.lower() not in {"metre", "meter"} for axis in target_crs.axis_info
        ):
            raise ValueError("working CRS must be projected in metres")
        scale["working_scale_factor"] = float(
            Proj(target_crs)
            .get_factors(scale["centre_longitude_deg"], scale["centre_latitude_deg"])
            .meridional_scale
        )
        projected_bounds = transform_bounds(source.crs, target_crs, *source.bounds, densify_pts=21)
        grid, target_transform = _aligned_grid(projected_bounds, cell_size_m, target_crs)
        rgb = np.zeros((3, grid.height, grid.width), dtype=np.uint8)
        for band in range(3):
            reproject(
                source=source.read(band + 1),
                destination=rgb[band],
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=Resampling.bilinear,
                dst_nodata=0,
            )
        source_mask = source.dataset_mask()
        target_mask = np.zeros(grid.shape, dtype=np.uint8)
        reproject(
            source=source_mask,
            destination=target_mask,
            src_transform=source.transform,
            src_crs=source.crs,
            dst_transform=target_transform,
            dst_crs=target_crs,
            resampling=Resampling.nearest,
            src_nodata=0,
            dst_nodata=0,
        )
        source_image_reference = {
            "width_px": source.width,
            "height_px": source.height,
            "affine_pixel_to_world": list(source.transform)[:6],
        }
        source_bounds_values = tuple(float(value) for value in source.bounds)
        source_transform_values = list(source.transform)[:6]
        source_width = source.width
        source_height = source.height

    domain = target_mask > 0
    source_records = load_labelme_buildings(annotations, source_image_reference)
    buildings = reproject_building_records(source_records, source_crs, target_crs)
    overlap_rows, building_coverage, by_building = building_cell_intersections(buildings, grid)
    unmasked_building_coverage = building_coverage.copy()
    building_coverage[~domain] = 0.0
    landcover, evidence, coverage, rules = _classify_rgb(rgb, domain, building_coverage)
    known = domain & (landcover >= 0)
    discarded = np.zeros(grid.shape, dtype=np.float32)
    building_cells = domain & (building_coverage > 1e-9)
    discarded[building_cells] = 1.0 - building_coverage[building_cells]

    candidates = [
        candidate
        for candidate in by_building.get(ignition_building_id, [])
        if domain[candidate[0], candidate[1]]
    ]
    if not candidates:
        raise ValueError(f"ignition building not found on grid: {ignition_building_id}")
    ignition_geometry = _record_geometry(
        next(item for item in buildings if item["building_id"] == ignition_building_id)
    )
    centroid = ignition_geometry.centroid
    ignition_row, ignition_col, ignition_overlap = min(
        candidates,
        key=lambda value: (
            -value[2],
            (grid.center(value[0], value[1])[0] - centroid.x) ** 2
            + (grid.center(value[0], value[1])[1] - centroid.y) ** 2,
            value[0],
            value[1],
        ),
    )
    ignition_x, ignition_y = grid.center(ignition_row, ignition_col)

    centre_lon = scale["centre_longitude_deg"]
    centre_lat = scale["centre_latitude_deg"]
    centre_transformer = Transformer.from_crs(4326, target_crs, always_xy=True)
    c0x, c0y = centre_transformer.transform(centre_lon, centre_lat)
    lon2, lat2, _ = Geod(ellps="WGS84").fwd(centre_lon, centre_lat, 90.0, cell_size_m)
    c1x, c1y = centre_transformer.transform(lon2, lat2)
    projected_distance = math.hypot(c1x - c0x, c1y - c0y)
    source_to_target = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    target_to_source = Transformer.from_crs(target_crs, source_crs, always_xy=True)
    a, b, c, d, e, f = source_transform_values
    determinant = a * e - b * d
    roundtrip_pixel_errors = []
    for pixel_x, pixel_y in (
        (0.0, 0.0),
        (float(source_width), 0.0),
        (float(source_width), float(source_height)),
        (0.0, float(source_height)),
        (float(source_width) / 2.0, float(source_height) / 2.0),
    ):
        source_x = a * pixel_x + b * pixel_y + c
        source_y = d * pixel_x + e * pixel_y + f
        target_x, target_y = source_to_target.transform(source_x, source_y)
        returned_x, returned_y = target_to_source.transform(target_x, target_y)
        delta_x = returned_x - c
        delta_y = returned_y - f
        returned_pixel_x = (e * delta_x - b * delta_y) / determinant
        returned_pixel_y = (-d * delta_x + a * delta_y) / determinant
        roundtrip_pixel_errors.append(
            math.hypot(returned_pixel_x - pixel_x, returned_pixel_y - pixel_y)
        )
    from shapely.ops import transform as shapely_transform
    from shapely.ops import unary_union

    building_union = unary_union([_record_geometry(record) for record in buildings])
    projected_building_area = float(building_union.area)
    to_geographic = Transformer.from_crs(target_crs, 4326, always_xy=True)
    geographic_buildings = shapely_transform(to_geographic.transform, building_union)
    geodesic_building_area = abs(
        float(Geod(ellps="WGS84").geometry_area_perimeter(geographic_buildings)[0])
    )
    rasterized_building_area = float(
        unmasked_building_coverage.sum(dtype=np.float64) * grid.cell_area_m2
    )
    valid_domain_building_area = float(building_coverage.sum(dtype=np.float64) * grid.cell_area_m2)
    scale.update(
        {
            "source_crs": source_crs.to_string(),
            "working_crs": target_crs.to_string(),
            "cell_size_m": cell_size_m,
            "projected_length_for_geodesic_cell_m": projected_distance,
            "cell_length_error_m": projected_distance - cell_size_m,
            "coordinate_roundtrip_max_pixel_error": max(roundtrip_pixel_errors),
            "source_scale_error_percent": (scale["source_scale_factor"] - 1.0) * 100.0,
            "working_scale_error_percent": (scale["working_scale_factor"] - 1.0) * 100.0,
            "building_union_projected_area_m2": projected_building_area,
            "building_union_geodesic_area_m2": geodesic_building_area,
            "building_projected_area_error_percent": (
                (projected_building_area / geodesic_building_area - 1.0) * 100.0
            ),
            "building_union_area_from_cell_coverage_m2": rasterized_building_area,
            "coverage_area_closure_error_m2": rasterized_building_area - projected_building_area,
            "building_area_inside_valid_domain_m2": valid_domain_building_area,
            "building_area_excluded_by_nodata_m2": rasterized_building_area
            - valid_domain_building_area,
        }
    )

    output.mkdir(parents=True)
    try:
        arrays = output / "arrays"
        arrays.mkdir()
        for name, array in (
            ("domain_mask", domain),
            ("known_mask", known),
            ("landcover_index", landcover),
            ("evidence_index", evidence),
            ("landcover_coverage_fraction", coverage),
            ("building_union_coverage_fraction", building_coverage),
            ("discarded_area_fraction", discarded),
        ):
            np.save(arrays / f"{name}.npy", array, allow_pickle=False)
        Image.fromarray(np.moveaxis(rgb, 0, -1), mode="RGB").save(output / "background.png")
        image_reference = {
            "width_px": grid.width,
            "height_px": grid.height,
            "crs": target_crs.to_string(),
            "affine_pixel_to_world": list(target_transform)[:6],
            "source_crs": source_crs.to_string(),
            "source_affine_pixel_to_world": source_transform_values,
            "resampling": {"rgb": "bilinear", "validity_mask": "nearest"},
        }
        (output / "image_ref.json").write_text(
            json.dumps(image_reference, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / "buildings.json").write_text(
            json.dumps(
                {
                    "schema_version": "prepared-buildings-2",
                    "coordinate_space": "projected_metre",
                    "crs": target_crs.to_string(),
                    "buildings": buildings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with (output / "building_overlap.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["building_id", "row", "col", "overlap_area_m2"])
            writer.writerows(overlap_rows)
        (output / "classification_rules.yml").write_text(
            yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        counts = {
            name: int(np.count_nonzero(domain & (landcover == index)))
            for index, name in enumerate(LANDCOVER_IDS)
        }
        classification_report = {
            "schema_version": "village-landcover-classification-report-1",
            "method_status": "hypothesis_pending_field_validation",
            "classes": list(LANDCOVER_IDS),
            "evidence_classes": list(EVIDENCE_IDS),
            "cell_counts": counts,
            "outside_domain_cell_count": int(np.count_nonzero(~domain)),
            "unknown_domain_cell_count": int(np.count_nonzero(domain & ~known)),
            "forest_floor_unresolved": True,
        }
        (output / "classification_report.json").write_text(
            json.dumps(classification_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ignition = {
            "building_id": ignition_building_id,
            "selection_method": "maximum_building_overlap_then_nearest_projected_centroid",
            "centroid_world_m": [float(centroid.x), float(centroid.y)],
            "row": ignition_row,
            "col": ignition_col,
            "cell_id": grid.cell_id(ignition_row, ignition_col),
            "cell_center_world_m": [ignition_x, ignition_y],
            "building_overlap_area_m2": ignition_overlap,
            "building_union_coverage_fraction": float(
                building_coverage[ignition_row, ignition_col]
            ),
        }
        (output / "ignition.json").write_text(
            json.dumps(ignition, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_geotiff(output / "landcover.tif", landcover, target_transform, target_crs, -1)
        _write_geotiff(output / "evidence.tif", evidence, target_transform, target_crs, 0)
        _write_geotiff(
            output / "coverage.tif", coverage.astype(np.float32), target_transform, target_crs, -1.0
        )
        errors = []
        if np.any(domain & ~known):
            errors.append("UNKNOWN_LANDCOVER_CELLS_BLOCK_FINAL_SIMULATION")
        quality = {
            "schema_version": "village-spatial-quality-1",
            "valid_for_v01_spatial_acceptance": True,
            "valid_for_final_simulation": not errors,
            "errors": errors,
            "warnings": [
                "RGB_VEGETATION_CLASSES_ARE_UNVALIDATED_HYPOTHESES",
                "FOREST_FLOOR_NOT_OBSERVABLE_FROM_RGB",
            ],
            "scale_diagnostics": scale,
            "grid": {
                "height": grid.height,
                "width": grid.width,
                "cell_size_m": grid.cell_size_m,
                "cell_area_m2": grid.cell_area_m2,
                "crs": grid.crs,
            },
            "source_bounds": source_bounds_values,
            "domain_cell_count": int(np.count_nonzero(domain)),
            "known_cell_count": int(np.count_nonzero(known)),
            "unknown_domain_cell_count": int(np.count_nonzero(domain & ~known)),
            "building_count": len(buildings),
            "building_overlap_record_count": len(overlap_rows),
            "mixed_building_boundary_cell_count": int(
                np.count_nonzero((building_coverage > 1e-9) & (building_coverage < 1.0 - 1e-9))
            ),
            "maximum_building_union_coverage_fraction": float(building_coverage.max()),
            "discarded_underlying_area_m2": float(discarded.sum() * grid.cell_area_m2),
            "building_priority_rule": (
                "any annotated building overlap assigns the cell to building; the union overlap "
                "is retained as coverage and displaced non-building area is reported"
            ),
            "ignition": ignition,
        }
        (output / "quality_report.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema_version": "prepared-village-vegetation-spatial-1",
            "stage": "V01",
            "material_resolution_status": "pending_v02",
            "grid": {
                "height": grid.height,
                "width": grid.width,
                "cell_size_m": grid.cell_size_m,
                "x_min_m": grid.x_min_m,
                "y_max_m": grid.y_max_m,
                "coordinate_mode": grid.coordinate_mode,
                "crs": grid.crs,
            },
            "landcover_ids": list(LANDCOVER_IDS),
            "evidence_ids": list(EVIDENCE_IDS),
            "sources_sha256": {
                str(imagery): _sha256(imagery),
                str(annotations): _sha256(annotations),
            },
            "artifacts": {
                "background": "background.png",
                "buildings": "buildings.json",
                "building_overlap": "building_overlap.csv",
                "classification_report": "classification_report.json",
                "quality_report": "quality_report.json",
                "ignition": "ignition.json",
            },
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


__all__ = [
    "EVIDENCE_IDS",
    "LANDCOVER_IDS",
    "building_cell_intersections",
    "choose_local_metric_crs",
    "geometry_to_polygons",
    "prepare_vegetation_spatial",
    "reproject_building_records",
    "transform_geometry",
]
