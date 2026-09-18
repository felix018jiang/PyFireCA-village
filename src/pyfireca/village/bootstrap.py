"""Generate a synthetic village trial case around a real satellite GeoTIFF."""

from __future__ import annotations

import json
from math import ceil, floor
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.annotations import load_labelme_buildings


def bootstrap_village_case(
    imagery_path: str | Path,
    output_dir: str | Path,
    *,
    cell_size_m: float = 10.0,
    end_time_s: float = 180.0,
    sample_interval_s: float = 5.0,
    labelme_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Create synthetic annotations, materials, and a scenario for a real GeoTIFF.

    The generated inputs are deliberately marked synthetic. They support scale and
    workflow tests and must not be interpreted as observations of the village.
    """

    values = (cell_size_m, end_time_s, sample_interval_s)
    if not all(np.isfinite(value) and value > 0.0 for value in values):
        raise ValueError("cell size, end time, and sample interval must be finite and positive")
    source = Path(imagery_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    labelme_source = None if labelme_path is None else Path(labelme_path).resolve()
    if labelme_source is not None and not labelme_source.is_file():
        raise FileNotFoundError(labelme_source)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"bootstrap directory already exists: {output}")
    try:
        import rasterio
        from pyproj import Transformer
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
    except ImportError as exc:
        raise ImportError("village bootstrap requires the 'gis' and 'village' extras") from exc

    with rasterio.open(source) as imagery:
        if imagery.crs is None:
            raise ValueError("satellite GeoTIFF must declare a CRS")
        if not imagery.crs.is_projected:
            raise ValueError(
                "satellite GeoTIFF must use a projected metre CRS; "
                "reproject geographic imagery first"
            )
        if imagery.crs.linear_units.lower() not in {"metre", "meter", "metres", "meters"}:
            raise ValueError("satellite GeoTIFF projected CRS must use metre units")
        if imagery.count < 3 or imagery.dtypes[:3] != ("uint8", "uint8", "uint8"):
            raise ValueError("satellite GeoTIFF must provide at least three uint8 RGB bands")
        bounds = imagery.bounds
        crs = imagery.crs.to_string()
        image_reference = {
            "schema_version": "image-ref-1",
            "coordinate_mode": "projected",
            "width_px": imagery.width,
            "height_px": imagery.height,
            "affine_pixel_to_world": list(tuple(imagery.transform)[:6]),
            "world_unit": "m",
            "north_reference": "crs_grid_north",
        }

    x_min = floor(bounds.left / cell_size_m) * cell_size_m
    y_max = ceil(bounds.top / cell_size_m) * cell_size_m
    width = ceil((bounds.right - x_min) / cell_size_m)
    height = ceil((y_max - bounds.bottom) / cell_size_m)
    if width <= 0 or height <= 0:
        raise ValueError("satellite GeoTIFF has empty spatial bounds")

    material_codes = np.ones((height, width), dtype=np.uint8)
    buildings: list[dict[str, object]] = []
    calculation_transform = from_origin(x_min, y_max, cell_size_m, cell_size_m)
    selected_ignition: tuple[int, int] | None = None
    ignition_building_id: str | None = None
    if labelme_source is not None:
        from shapely.geometry import Polygon, box

        annotation_records = load_labelme_buildings(labelme_source, image_reference)
        geometries = [Polygon(record["polygons"][0]["exterior"]) for record in annotation_records]
        material_codes = rasterize(
            [(geometry, 2) for geometry in geometries],
            out_shape=(height, width),
            transform=calculation_transform,
            fill=1,
            all_touched=True,
            dtype="uint8",
        )
        mean_x = sum(geometry.centroid.x for geometry in geometries) / len(geometries)
        mean_y = sum(geometry.centroid.y for geometry in geometries) / len(geometries)
        central_index = min(
            range(len(geometries)),
            key=lambda index: (
                (geometries[index].centroid.x - mean_x) ** 2
                + (geometries[index].centroid.y - mean_y) ** 2
            ),
        )
        central_geometry = geometries[central_index]
        ignition_building_id = annotation_records[central_index]["building_id"]
        min_x, min_y, max_x, max_y = central_geometry.bounds
        first_col = max(0, floor((min_x - x_min) / cell_size_m))
        last_col = min(width - 1, ceil((max_x - x_min) / cell_size_m) - 1)
        first_row = max(0, floor((y_max - max_y) / cell_size_m))
        last_row = min(height - 1, ceil((y_max - min_y) / cell_size_m) - 1)
        candidates: list[tuple[float, float, int, int]] = []
        centroid = central_geometry.centroid
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                left = x_min + col * cell_size_m
                top = y_max - row * cell_size_m
                cell = box(left, top - cell_size_m, left + cell_size_m, top)
                overlap_area = central_geometry.intersection(cell).area
                if overlap_area <= 0.0:
                    continue
                center_x = left + cell_size_m / 2.0
                center_y = top - cell_size_m / 2.0
                distance_squared = (center_x - centroid.x) ** 2 + (center_y - centroid.y) ** 2
                candidates.append((overlap_area, -distance_squared, row, col))
        if not candidates:
            raise ValueError("central LabelMe building does not intersect the computation grid")
        _area, _distance, ignition_row, ignition_col = max(candidates)
        selected_ignition = ignition_row, ignition_col
    else:
        # Regular synthetic footprints exercise aggregation when no annotation is supplied.
        building_cells = max(1, round(20.0 / cell_size_m))
        gap_cells = max(1, round(30.0 / cell_size_m))
        to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        step = building_cells + gap_cells
        for row in range(gap_cells, max(gap_cells + 1, height - building_cells), step):
            for col in range(gap_cells, max(gap_cells + 1, width - building_cells), step):
                last_row = min(row + building_cells, height)
                last_col = min(col + building_cells, width)
                if last_row <= row or last_col <= col:
                    continue
                material_codes[row:last_row, col:last_col] = 2
                left = x_min + col * cell_size_m
                right = x_min + last_col * cell_size_m
                top = y_max - row * cell_size_m
                bottom = y_max - last_row * cell_size_m
                projected_ring = [
                    (left, top),
                    (right, top),
                    (right, bottom),
                    (left, bottom),
                    (left, top),
                ]
                ring = [list(to_wgs84.transform(x, y)) for x, y in projected_ring]
                buildings.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "building_id": f"SYN_{len(buildings) + 1:05d}",
                            "data_status": "synthetic_placeholder",
                        },
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                    }
                )
        if not buildings:
            row, col = height // 2, width // 2
            material_codes[row, col] = 2
            left, right = x_min + col * cell_size_m, x_min + (col + 1) * cell_size_m
            top, bottom = y_max - row * cell_size_m, y_max - (row + 1) * cell_size_m
            ring = [
                list(to_wgs84.transform(x, y))
                for x, y in [
                    (left, top),
                    (right, top),
                    (right, bottom),
                    (left, bottom),
                    (left, top),
                ]
            ]
            buildings.append(
                {
                    "type": "Feature",
                    "properties": {
                        "building_id": "SYN_00001",
                        "data_status": "synthetic_placeholder",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                }
            )

    output.mkdir(parents=True)
    try:
        with rasterio.open(
            output / "synthetic_materials.tif",
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
            crs=crs,
            transform=calculation_transform,
            compress="deflate",
        ) as materials:
            materials.write(material_codes, 1)
        if labelme_source is None:
            (output / "synthetic_buildings.geojson").write_text(
                json.dumps(
                    {"type": "FeatureCollection", "features": buildings},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        (output / "material_codes.yml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "material-codes-1",
                    "codes": {1: "synthetic_grass", 2: "synthetic_wood"},
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        catalog = {
            "schema_version": "material-catalog-1",
            "library_id": "synthetic-satellite-bootstrap-v1",
            "materials": [
                {
                    "id": "synthetic_grass",
                    "label": "合成地表燃料(占位)",
                    "role": "surface_fuel",
                    "burnable": True,
                    "evidence": "synthetic",
                    "source_id": "bootstrap-placeholder-v1",
                    "parameters": {
                        "fuel_load_kg_m2": 0.5,
                        "heat_of_combustion_J_kg": 500000.0,
                        "mass_loss_flux_kg_m2_s": 0.01,
                        "active_area_ratio": 1.0,
                        "emission_fraction": 0.4,
                        "absorptivity": 0.7,
                        "ignition_dose_J_m2": 300.0,
                        "exposure_decay_s": 12.0,
                    },
                },
                {
                    "id": "synthetic_wood",
                    "label": "合成木质建筑燃料(占位)",
                    "role": "building_fuel",
                    "burnable": True,
                    "evidence": "synthetic",
                    "source_id": "bootstrap-placeholder-v1",
                    "parameters": {
                        "fuel_load_kg_m2": 2.0,
                        "heat_of_combustion_J_kg": 1000000.0,
                        "mass_loss_flux_kg_m2_s": 0.02,
                        "active_area_ratio": 1.0,
                        "emission_fraction": 0.5,
                        "absorptivity": 0.8,
                        "ignition_dose_J_m2": 500.0,
                        "exposure_decay_s": 20.0,
                    },
                },
            ],
        }
        (output / "material_catalog.yml").write_text(
            yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        case = {
            "schema_version": "village-case-1",
            "case_id": f"synthetic-bootstrap-{source.stem}",
            "mode": "synthetic_demo",
            "imagery": {"path": str(source), "reference": None},
            "buildings": {
                "path": (
                    "synthetic_buildings.geojson" if labelme_source is None else str(labelme_source)
                ),
                "format": "geojson_wgs84" if labelme_source is None else "labelme_json",
            },
            "materials": {
                "source": "synthetic_materials.tif",
                "format": "geotiff_codes",
                "codes": "material_codes.yml",
                "catalog": "material_catalog.yml",
            },
            "spatial": {
                "coordinate_mode": "projected",
                "crs": crs,
                "grid": {
                    "x_min_m": x_min,
                    "y_max_m": y_max,
                    "height": height,
                    "width": width,
                    "cell_size_m": cell_size_m,
                },
            },
        }
        case_path = output / "case.synthetic.yml"
        case_path.write_text(
            yaml.safe_dump(case, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        if selected_ignition is None:
            ignition_row, ignition_col = np.argwhere(material_codes == 2)[0]
        else:
            ignition_row, ignition_col = selected_ignition
        scenario = {
            "schema_version": "village-scenario-1",
            "scenario_id": "synthetic-satellite-trial",
            "model": {
                "name": "effective_exposure_ca_v1",
                "exposure_radius_m": 2.5 * cell_size_m,
                "decay_length_m": 2.0 * cell_size_m,
                "wind_beta_s_m": 0.0,
                "coefficients_evidence": "synthetic",
                "coefficients_source_id": "bootstrap-placeholder-v1",
            },
            "weather": {"wind_speed_m_s": 0.0, "wind_from_deg": 0.0},
            "ignitions": [
                {
                    "cell": {"row": int(ignition_row), "col": int(ignition_col)},
                    "time_s": 0.0,
                    "kind": "direct",
                }
            ],
            "numerics": {"max_dt_s": 1.0, "end_time_s": end_time_s},
            "outputs": {"sample_interval_s": sample_interval_s},
            "visualization": {"heating_display_fraction": 0.1},
        }
        scenario_path = output / "scenario.synthetic.yml"
        scenario_path.write_text(
            yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (output / "bootstrap_metadata.json").write_text(
            json.dumps(
                {
                    "schema_version": "village-bootstrap-metadata-1",
                    "ignition_selection": (
                        "nearest_building_to_mean_centroid"
                        if ignition_building_id is not None
                        else "first_synthetic_building_cell"
                    ),
                    "ignition_building_id": ignition_building_id,
                    "ignition_cell": {"row": int(ignition_row), "col": int(ignition_col)},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (output / "README.md").write_text(
            "# Synthetic village bootstrap\n\n"
            + (
                "The satellite GeoTIFF and LabelMe building geometry are observational inputs. "
                if labelme_source is not None
                else "Only the satellite GeoTIFF is observational input. Buildings are synthetic. "
            )
            + "Material classes, fuel parameters, ignition, and weather are synthetic "
            "placeholders. Do not use this case for risk conclusions.\n",
            encoding="utf-8",
        )
    except Exception:
        import shutil

        shutil.rmtree(output)
        raise
    return case_path, scenario_path


__all__ = ["bootstrap_village_case"]
