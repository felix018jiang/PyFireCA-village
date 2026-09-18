"""Prepare local imagery, building annotations, and material grids."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca.village.annotations import load_labelme_buildings, write_prepared_buildings
from pyfireca.village.config import load_village_case
from pyfireca.village.domain import CellParameterArrays, PreparedVillageDomain, VillageGridSpec
from pyfireca.village.materials import VillageMaterialCatalog, load_material_catalog


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_material_grid_csv(
    path: Path, grid: VillageGridSpec, catalog: VillageMaterialCatalog
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    by_id = catalog.by_id
    order = {material.id: index for index, material in enumerate(catalog.materials)}
    material_index = np.full(grid.shape, -1, dtype=np.int32)
    known = np.zeros(grid.shape, dtype=np.bool_)
    burnable = np.zeros(grid.shape, dtype=np.bool_)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["row", "col", "material_id"]:
            raise ValueError("material grid header must be exactly row,col,material_id")
        for line, record in enumerate(reader, start=2):
            try:
                row = int(record["row"])
                col = int(record["col"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid row/col at material-grid line {line}") from exc
            grid.cell_id(row, col)
            if known[row, col]:
                raise ValueError(f"duplicate material cell ({row}, {col})")
            material_id = record["material_id"]
            if material_id == "unknown":
                continue
            if material_id not in by_id:
                raise ValueError(f"unknown material id {material_id!r} at cell ({row}, {col})")
            material_index[row, col] = order[material_id]
            known[row, col] = True
            burnable[row, col] = by_id[material_id].burnable
    missing = ~(known | (material_index == -1))
    if np.any(missing):
        raise RuntimeError("internal material-grid completeness error")
    return material_index, known, burnable


def _load_material_geotiff(
    path: Path,
    codes_path: Path,
    grid: VillageGridSpec,
    catalog: VillageMaterialCatalog,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read an exactly aligned integer categorical material GeoTIFF."""

    if grid.coordinate_mode != "projected" or not grid.crs:
        raise ValueError("geotiff_codes materials require a projected grid with a CRS")
    try:
        import rasterio
        from rasterio import Affine
    except ImportError as exc:
        raise ImportError(
            "geotiff_codes materials require the 'gis' optional dependencies"
        ) from exc
    with codes_path.open(encoding="utf-8") as stream:
        raw_codes = yaml.safe_load(stream)
    if not isinstance(raw_codes, dict) or set(raw_codes) != {"schema_version", "codes"}:
        raise ValueError("material code mapping must contain schema_version and codes")
    if raw_codes["schema_version"] != "material-codes-1" or not isinstance(
        raw_codes["codes"], dict
    ):
        raise ValueError("unsupported material code mapping")
    codes: dict[int, str] = {}
    for raw_code, material_id in raw_codes["codes"].items():
        if isinstance(raw_code, bool):
            raise TypeError("material codes must be integers")
        try:
            code = int(raw_code)
        except (TypeError, ValueError) as exc:
            raise TypeError("material codes must be integers") from exc
        if not isinstance(material_id, str):
            raise TypeError("material code values must be material IDs")
        codes[code] = material_id
    expected_transform = Affine(
        grid.cell_size_m,
        0.0,
        grid.x_min_m,
        0.0,
        -grid.cell_size_m,
        grid.y_max_m,
    )
    with rasterio.open(path) as dataset:
        if dataset.count != 1:
            raise ValueError("material GeoTIFF must contain exactly one band")
        if (dataset.height, dataset.width) != grid.shape:
            raise ValueError("material GeoTIFF shape does not match the computation grid")
        if dataset.crs is None or dataset.crs.to_string() != grid.crs:
            raise ValueError("material GeoTIFF CRS does not match the computation grid")
        if not np.allclose(
            tuple(dataset.transform)[:6],
            tuple(expected_transform)[:6],
            rtol=0.0,
            atol=1e-9,
        ):
            raise ValueError("material GeoTIFF transform does not match the computation grid")
        values = dataset.read(1)
        nodata = dataset.nodata
    if not np.issubdtype(values.dtype, np.integer):
        raise TypeError("material GeoTIFF must use an integer dtype")
    order = {material.id: index for index, material in enumerate(catalog.materials)}
    material_index = np.full(grid.shape, -1, dtype=np.int32)
    known = np.ones(grid.shape, dtype=np.bool_)
    if nodata is not None:
        known &= values != nodata
    burnable = np.zeros(grid.shape, dtype=np.bool_)
    for code in np.unique(values[known]):
        numeric_code = int(code)
        if numeric_code not in codes:
            raise ValueError(f"material GeoTIFF contains unmapped code {numeric_code}")
        material_id = codes[numeric_code]
        if material_id not in catalog.by_id:
            raise ValueError(f"material code {numeric_code} refers to unknown ID {material_id!r}")
        mask = known & (values == code)
        material_index[mask] = order[material_id]
        burnable[mask] = catalog.by_id[material_id].burnable
    return material_index, known, burnable


def _parameter_arrays(
    material_index: np.ndarray, known: np.ndarray, catalog: VillageMaterialCatalog
) -> CellParameterArrays:
    fields = tuple(CellParameterArrays.__dataclass_fields__)
    arrays = {name: np.zeros(material_index.shape, dtype=np.float64) for name in fields}
    for index, material in enumerate(catalog.materials):
        mask = known & (material_index == index)
        for name in fields:
            arrays[name][mask] = material.parameters.get(name, 0.0)
    return CellParameterArrays(**arrays)


def _load_image_reference(path: Path, image_size: tuple[int, int]) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "coordinate_mode",
        "width_px",
        "height_px",
        "affine_pixel_to_world",
        "world_unit",
        "north_reference",
    }
    if set(data) != expected:
        raise ValueError("image reference has missing or unknown fields")
    if data["schema_version"] != "image-ref-1" or data["world_unit"] != "m":
        raise ValueError("unsupported image reference schema or unit")
    if (data["width_px"], data["height_px"]) != image_size:
        raise ValueError("image reference dimensions do not match the image")
    affine = data["affine_pixel_to_world"]
    if not isinstance(affine, list) or len(affine) != 6:
        raise ValueError("affine_pixel_to_world must contain six values")
    a, b, _c, d, e, _f = (float(value) for value in affine)
    if not np.isfinite([a, b, d, e]).all() or abs(a * e - b * d) <= 1e-15:
        raise ValueError("image affine must be finite and invertible")
    return data


def _transform_point(point: list[float], affine: list[float]) -> tuple[float, float]:
    if not isinstance(point, list) or len(point) != 2:
        raise ValueError("building coordinates must be [x, y] pairs")
    u, v = (float(value) for value in point)
    a, b, c, d, e, f = (float(value) for value in affine)
    return a * u + b * v + c, d * u + e * v + f


def _prepare_buildings(path: Path, image_reference: dict[str, Any], output: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "buildings-1":
        raise ValueError("unsupported buildings schema_version")
    coordinate_space = data.get("coordinate_space")
    if coordinate_space not in {"image_pixels", "world_m"}:
        raise ValueError("buildings coordinate_space must be image_pixels or world_m")
    raw_buildings = data.get("buildings")
    if not isinstance(raw_buildings, list):
        raise TypeError("buildings must be a list")
    seen: set[str] = set()
    prepared: list[dict[str, Any]] = []
    try:
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise ImportError(
            "village preparation requires the 'village' optional dependencies"
        ) from exc
    for raw in raw_buildings:
        building_id = raw.get("building_id")
        if not isinstance(building_id, str) or not building_id or building_id in seen:
            raise ValueError("building_id values must be non-empty and unique")
        seen.add(building_id)
        polygons: list[dict[str, Any]] = []
        for polygon in raw.get("polygons", []):
            affine = tuple(image_reference["affine_pixel_to_world"])
            transform = (
                (lambda point, matrix=affine: _transform_point(point, list(matrix)))
                if coordinate_space == "image_pixels"
                else (lambda point: tuple(float(value) for value in point))
            )
            exterior = [transform(point) for point in polygon["exterior"]]
            holes = [[transform(point) for point in ring] for ring in polygon.get("holes", [])]
            geometry = Polygon(exterior, holes)
            if geometry.is_empty or not geometry.is_valid or geometry.area <= 0.0:
                raise ValueError(f"building {building_id!r} has invalid polygon geometry")
            polygons.append(
                {
                    "exterior": [list(point) for point in geometry.exterior.coords],
                    "holes": [
                        [list(point) for point in ring.coords] for ring in geometry.interiors
                    ],
                }
            )
        if not polygons:
            raise ValueError(f"building {building_id!r} contains no polygons")
        prepared.append(
            {
                "building_id": building_id,
                "polygons": polygons,
                "properties": raw.get("properties", {}),
            }
        )
    output.write_text(
        json.dumps(
            {
                "schema_version": "prepared-buildings-1",
                "coordinate_space": "world_m",
                "buildings": prepared,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(prepared)


def _prepare_geojson_buildings(path: Path, grid: VillageGridSpec, output: Path) -> int:
    """Convert WGS84 GeoJSON building polygons to the working projected CRS."""

    if grid.coordinate_mode != "projected" or not grid.crs:
        raise ValueError("geojson_wgs84 buildings require a projected computation grid")
    try:
        from pyproj import Transformer
        from shapely.geometry import MultiPolygon, Polygon, shape
        from shapely.ops import transform
    except ImportError as exc:
        raise ImportError("GeoJSON buildings require the 'village' optional dependencies") from exc
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("building GeoJSON must be a FeatureCollection")
    transformer = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    seen: set[str] = set()
    prepared: list[dict[str, Any]] = []
    projected_geometries: list[tuple[str, Any]] = []
    for feature in data["features"]:
        if feature.get("type") != "Feature":
            raise ValueError("building GeoJSON entries must be Features")
        properties = feature.get("properties") or {}
        building_id = properties.get("building_id")
        if not isinstance(building_id, str) or not building_id or building_id in seen:
            raise ValueError("GeoJSON building_id values must be non-empty and unique")
        seen.add(building_id)
        source_geometry = shape(feature.get("geometry"))
        if not isinstance(source_geometry, (Polygon, MultiPolygon)):
            raise ValueError(f"building {building_id!r} must use Polygon or MultiPolygon geometry")
        geometry = transform(transformer.transform, source_geometry)
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0.0:
            raise ValueError(f"building {building_id!r} has invalid projected geometry")
        for other_id, other in projected_geometries:
            if geometry.intersection(other).area > 1e-8:
                raise ValueError(f"BUILDING_OVERLAP: {building_id!r} overlaps {other_id!r}")
        projected_geometries.append((building_id, geometry))
        parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        prepared.append(
            {
                "building_id": building_id,
                "polygons": [
                    {
                        "exterior": [list(point) for point in polygon.exterior.coords],
                        "holes": [
                            [list(point) for point in ring.coords] for ring in polygon.interiors
                        ],
                    }
                    for polygon in parts
                ],
                "properties": {
                    key: value for key, value in properties.items() if key != "building_id"
                },
            }
        )
    output.write_text(
        json.dumps(
            {
                "schema_version": "prepared-buildings-1",
                "coordinate_space": "world_m",
                "buildings": prepared,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(prepared)


def _prepare_labelme_buildings(path: Path, image_reference: dict[str, Any], output: Path) -> int:
    records = load_labelme_buildings(path, image_reference)
    write_prepared_buildings(records, output)
    return len(records)


def _load_imagery(config: Any) -> tuple[dict[str, Any], Any]:
    """Return browser image-reference metadata and an RGB Pillow image."""

    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "village preparation requires the 'village' optional dependencies"
        ) from exc
    if config.image_reference_path is not None:
        with Image.open(config.imagery_path) as image:
            reference = _load_image_reference(config.image_reference_path, image.size)
            return reference, image.convert("RGB")
    if config.grid.coordinate_mode != "projected" or not config.grid.crs:
        raise ValueError("GeoTIFF imagery without a sidecar requires a projected grid")
    try:
        import rasterio
    except ImportError as exc:
        raise ImportError("GeoTIFF imagery requires the 'gis' optional dependencies") from exc
    with rasterio.open(config.imagery_path) as dataset:
        if dataset.crs is None or dataset.crs.to_string() != config.grid.crs:
            raise ValueError("imagery GeoTIFF CRS must match the computation grid CRS")
        if dataset.count < 3:
            raise ValueError("imagery GeoTIFF requires at least three explicit RGB bands")
        rgb = dataset.read((1, 2, 3))
        if rgb.dtype != np.uint8:
            raise TypeError("imagery RGB bands must use uint8; automatic stretching is disabled")
        transform = dataset.transform
        reference = {
            "schema_version": "image-ref-1",
            "coordinate_mode": "projected",
            "width_px": dataset.width,
            "height_px": dataset.height,
            "affine_pixel_to_world": [
                transform.a,
                transform.b,
                transform.c,
                transform.d,
                transform.e,
                transform.f,
            ],
            "world_unit": "m",
            "north_reference": "crs_grid_north",
        }
    return reference, Image.fromarray(np.moveaxis(rgb, 0, -1))


def _write_building_overlap(
    buildings_path: Path, grid: VillageGridSpec, output: Path
) -> tuple[int, int]:
    """Intersect prepared building polygons with the square CA grid."""

    try:
        from shapely.geometry import Polygon, box
        from shapely.ops import unary_union
    except ImportError as exc:
        raise ImportError(
            "village preparation requires the 'village' optional dependencies"
        ) from exc
    data = json.loads(buildings_path.read_text(encoding="utf-8"))
    records: list[tuple[str, int, int, float]] = []
    cell_buildings: dict[int, set[str]] = {}
    tolerance = grid.cell_area_m2 * 1e-9
    for building in data["buildings"]:
        geometries = [
            Polygon(polygon["exterior"], polygon["holes"]) for polygon in building["polygons"]
        ]
        geometry = unary_union(geometries)
        min_x, min_y, max_x, max_y = geometry.bounds
        first_col = max(0, int(np.floor((min_x - grid.x_min_m) / grid.cell_size_m)))
        last_col = min(
            grid.width - 1,
            int(np.ceil((max_x - grid.x_min_m) / grid.cell_size_m)) - 1,
        )
        first_row = max(0, int(np.floor((grid.y_max_m - max_y) / grid.cell_size_m)))
        last_row = min(
            grid.height - 1,
            int(np.ceil((grid.y_max_m - min_y) / grid.cell_size_m)) - 1,
        )
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                x0 = grid.x_min_m + col * grid.cell_size_m
                y1 = grid.y_max_m - row * grid.cell_size_m
                cell = box(x0, y1 - grid.cell_size_m, x0 + grid.cell_size_m, y1)
                area = float(geometry.intersection(cell).area)
                if area <= tolerance:
                    continue
                cell_id = grid.cell_id(row, col)
                cell_buildings.setdefault(cell_id, set()).add(building["building_id"])
                records.append((building["building_id"], row, col, area))
    conflicts = {
        cell_id: sorted(buildings)
        for cell_id, buildings in cell_buildings.items()
        if len(buildings) > 1
    }
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["building_id", "row", "col", "overlap_area_m2"])
        writer.writerows(records)
    return len(records), len(conflicts)


def prepare_village_case(case_path: str | Path, output_dir: str | Path) -> Path:
    """Validate and materialize a version-1 village case."""

    config = load_village_case(case_path)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    catalog = load_material_catalog(config.material_catalog_path, mode=config.mode)
    if config.material_format == "grid_csv":
        material_index, known, burnable = _load_material_grid_csv(
            config.material_grid_path, config.grid, catalog
        )
    else:
        if config.material_codes_path is None:
            raise RuntimeError("geotiff_codes configuration is missing its code mapping")
        material_index, known, burnable = _load_material_geotiff(
            config.material_grid_path,
            config.material_codes_path,
            config.grid,
            catalog,
        )
    domain_mask = np.ones(config.grid.shape, dtype=np.bool_)
    domain = PreparedVillageDomain(
        grid=config.grid,
        material_index=material_index,
        domain_mask=domain_mask,
        known_mask=known,
        burnable_mask=burnable,
        parameters=_parameter_arrays(material_index, known, catalog),
        provenance={"case_id": config.case_id, "mode": config.mode},
    )
    image_reference, browser_image = _load_imagery(config)

    output.mkdir(parents=True)
    try:
        arrays = output / "arrays"
        arrays.mkdir()
        np.save(arrays / "material_index.npy", domain.material_index, allow_pickle=False)
        np.save(arrays / "domain_mask.npy", domain.domain_mask, allow_pickle=False)
        np.save(arrays / "known_mask.npy", domain.known_mask, allow_pickle=False)
        np.save(arrays / "burnable_mask.npy", domain.burnable_mask, allow_pickle=False)
        for name in CellParameterArrays.__dataclass_fields__:
            np.save(arrays / f"{name}.npy", getattr(domain.parameters, name), allow_pickle=False)
        browser_image.save(output / "background.png", format="PNG")
        (output / "image_ref.json").write_text(
            json.dumps(image_reference, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if config.buildings_format == "local_json":
            building_count = _prepare_buildings(
                config.buildings_path, image_reference, output / "buildings.json"
            )
        elif config.buildings_format == "labelme_json":
            building_count = _prepare_labelme_buildings(
                config.buildings_path, image_reference, output / "buildings.json"
            )
        else:
            building_count = _prepare_geojson_buildings(
                config.buildings_path, config.grid, output / "buildings.json"
            )
        building_overlap_count, multi_building_cell_count = _write_building_overlap(
            output / "buildings.json", config.grid, output / "building_overlap.csv"
        )
        shutil.copy2(config.material_catalog_path, output / "material_catalog.yml")
        source_paths = [
            config.path,
            config.imagery_path,
            config.buildings_path,
            config.material_grid_path,
            config.material_catalog_path,
        ]
        if config.image_reference_path is not None:
            source_paths.append(config.image_reference_path)
        if config.material_codes_path is not None:
            source_paths.append(config.material_codes_path)
        sources = {str(path): _sha256(path) for path in source_paths}
        manifest = {
            "schema_version": "prepared-village-1",
            "case_id": config.case_id,
            "mode": config.mode,
            "grid": asdict(config.grid),
            "background": "background.png",
            "building_count": building_count,
            "building_overlap_count": building_overlap_count,
            "multi_building_cell_count": multi_building_cell_count,
            "material_ids": [material.id for material in catalog.materials],
            "known_cell_count": int(np.count_nonzero(known)),
            "unknown_cell_count": int(np.count_nonzero(domain_mask & ~known)),
            "burnable_cell_count": int(np.count_nonzero(burnable)),
            "sources_sha256": sources,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / "quality_report.json").write_text(
            json.dumps(
                {
                    "valid": not np.any(domain_mask & ~known),
                    "errors": [] if np.all(known) else ["UNKNOWN_MATERIAL_CELLS"],
                    "warnings": (
                        [f"MULTI_BUILDING_CELLS:{multi_building_cell_count}"]
                        if multi_building_cell_count
                        else []
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


def load_prepared_village(path: str | Path) -> PreparedVillageDomain:
    """Load a prepared numerical domain without pickle-enabled arrays."""

    directory = Path(path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "prepared-village-1":
        raise ValueError("unsupported prepared village schema")
    grid = VillageGridSpec(**manifest["grid"])
    arrays = directory / "arrays"

    def load(name: str) -> np.ndarray:
        return np.load(arrays / f"{name}.npy", allow_pickle=False)

    parameters = CellParameterArrays(
        **{name: load(name) for name in CellParameterArrays.__dataclass_fields__}
    )
    return PreparedVillageDomain(
        grid=grid,
        material_index=load("material_index"),
        domain_mask=load("domain_mask"),
        known_mask=load("known_mask"),
        burnable_mask=load("burnable_mask"),
        parameters=parameters,
        provenance={"case_id": manifest["case_id"], "mode": manifest["mode"]},
    )
