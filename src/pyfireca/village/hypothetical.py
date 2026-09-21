"""Build a reproducible, explicitly hypothetical material map from RGB imagery.

The classifier is intended for integration testing and scenario exploration. RGB
satellite pixels cannot establish construction material, fuel load, or moisture.
"""

from __future__ import annotations

import json
from math import ceil, floor
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.annotations import load_labelme_buildings

MATERIAL_IDS = (
    "assumed_bare_or_road",
    "assumed_sparse_vegetation",
    "assumed_dense_vegetation",
    "assumed_wood_roof",
    "assumed_tile_over_wood",
    "assumed_thatch_roof",
)


def _inert_mapping(construction_id: str) -> dict[str, object]:
    return {
        "construction_id": construction_id,
        "burnable": False,
        "ignition_mode": "piloted",
        "material_parameters": {
            "fuel_load_kg_m2": 0.0,
            "moisture_content_dry_basis": 0.0,
            "heat_of_combustion_J_kg": 0.0,
            "ignition_temperature_K": 0.0,
            "peak_hrrpua_W_m2": 0.0,
            "radiative_fraction": 0.0,
            "absorptivity": 0.0,
            "growth_time_s": 0.0,
            "decay_time_s": 0.0,
        },
        "construction_parameters": {
            "effective_heated_dry_mass_kg_m2": 1.0,
            "specific_heat_J_kg_K": 1000.0,
            "emissivity": 0.9,
            "ambient_heat_transfer_coefficient_W_m2_K": 10.0,
            "backing_conductance_W_m2_K": 2.0,
            "receiver_area_m2": 4.0,
            "active_surface_area_m2": 0.0,
            "pyrolysable_dry_mass_fraction": 0.0,
        },
    }


def _fuel_mapping(
    construction_id: str,
    *,
    fuel_load: float,
    moisture: float,
    heat_of_combustion: float,
    ignition_temperature: float,
    peak_hrrpua: float,
    radiative_fraction: float,
    absorptivity: float,
    growth_time: float,
    decay_time: float,
    heated_mass: float,
    specific_heat: float,
    emissivity: float,
    backing_conductance: float,
    active_area: float,
    pyrolysable_fraction: float,
) -> dict[str, object]:
    return {
        "construction_id": construction_id,
        "burnable": True,
        "ignition_mode": "piloted",
        "material_parameters": {
            "fuel_load_kg_m2": fuel_load,
            "moisture_content_dry_basis": moisture,
            "heat_of_combustion_J_kg": heat_of_combustion,
            "ignition_temperature_K": ignition_temperature,
            "peak_hrrpua_W_m2": peak_hrrpua,
            "radiative_fraction": radiative_fraction,
            "absorptivity": absorptivity,
            "growth_time_s": growth_time,
            "decay_time_s": decay_time,
        },
        "construction_parameters": {
            "effective_heated_dry_mass_kg_m2": heated_mass,
            "specific_heat_J_kg_K": specific_heat,
            "emissivity": emissivity,
            "ambient_heat_transfer_coefficient_W_m2_K": 10.0,
            "backing_conductance_W_m2_K": backing_conductance,
            "receiver_area_m2": 4.0,
            "active_surface_area_m2": active_area,
            "pyrolysable_dry_mass_fraction": pyrolysable_fraction,
        },
    }


def _v1_catalog() -> dict[str, object]:
    labels = (
        "假设裸地或道路(不参与燃烧)",
        "假设稀疏植被(本次不参与燃烧)",
        "假设密集植被(本次不参与燃烧)",
        "假设木质屋面建筑燃料",
        "假设瓦覆木结构建筑燃料",
        "假设茅草或暖色屋面建筑燃料",
    )
    records = []
    for index, (material_id, label) in enumerate(zip(MATERIAL_IDS, labels, strict=True)):
        burnable = index >= 3
        records.append(
            {
                "id": material_id,
                "label": label,
                "role": "building_fuel" if burnable else "inert",
                "burnable": burnable,
                "evidence": "synthetic",
                "source_id": "rgb-quantile-hypothesis-v1",
                "parameters": (
                    {
                        "fuel_load_kg_m2": 1.0,
                        "heat_of_combustion_J_kg": 1_000_000.0,
                        "mass_loss_flux_kg_m2_s": 0.01,
                        "active_area_ratio": 1.0,
                        "emission_fraction": 0.4,
                        "absorptivity": 0.8,
                        "ignition_dose_J_m2": 500.0,
                        "exposure_decay_s": 20.0,
                    }
                    if burnable
                    else {}
                ),
            }
        )
    return {
        "schema_version": "material-catalog-1",
        "library_id": "gaoyou-rgb-hypothetical-v1",
        "materials": records,
    }


def _v2_scenario(ignition_row: int, ignition_col: int) -> dict[str, object]:
    mappings = {
        MATERIAL_IDS[0]: _inert_mapping("hypothetical_bare_or_road"),
        MATERIAL_IDS[1]: _inert_mapping("hypothetical_sparse_vegetation"),
        MATERIAL_IDS[2]: _inert_mapping("hypothetical_dense_vegetation"),
        MATERIAL_IDS[3]: _fuel_mapping(
            "hypothetical_exposed_wood",
            fuel_load=25.0,
            moisture=0.10,
            heat_of_combustion=16_260_000.0,
            ignition_temperature=523.0,
            peak_hrrpua=300_000.0,
            radiative_fraction=0.38,
            absorptivity=0.88,
            growth_time=180.0,
            decay_time=600.0,
            heated_mass=2.5,
            specific_heat=1500.0,
            emissivity=0.85,
            backing_conductance=2.0,
            active_area=4.0,
            pyrolysable_fraction=0.70,
        ),
        MATERIAL_IDS[4]: _fuel_mapping(
            "hypothetical_tile_cover_over_wood",
            fuel_load=18.0,
            moisture=0.10,
            heat_of_combustion=16_260_000.0,
            ignition_temperature=520.0,
            peak_hrrpua=300_000.0,
            radiative_fraction=0.40,
            absorptivity=0.90,
            growth_time=120.0,
            decay_time=300.0,
            heated_mass=2.0,
            specific_heat=1500.0,
            emissivity=0.85,
            backing_conductance=2.0,
            active_area=4.0,
            pyrolysable_fraction=0.60,
        ),
        MATERIAL_IDS[5]: _fuel_mapping(
            "hypothetical_thatch",
            fuel_load=7.0,
            moisture=0.12,
            heat_of_combustion=15_000_000.0,
            ignition_temperature=490.0,
            peak_hrrpua=350_000.0,
            radiative_fraction=0.40,
            absorptivity=0.85,
            growth_time=60.0,
            decay_time=300.0,
            heated_mass=0.8,
            specific_heat=1400.0,
            emissivity=0.90,
            backing_conductance=1.0,
            active_area=5.0,
            pyrolysable_fraction=0.85,
        ),
    }
    return {
        "schema_version": "village-v2-prepared-scenario-1",
        "scenario_id": "gaoyou-rgb-hypothetical-materials-500min",
        "model": "effective_exposure_ca_v2",
        "evidence": "rgb_inspired_hypothetical_materials_not_field_observations",
        "material_mappings": mappings,
        "blockers": {"background_transmissivity": 1.0, "building_transmissivity": 0.2},
        "transport": {
            "calibration": "hypothetical_point_source_building_spread",
            "parameters": {
                "exposure_radius_m": 8.1,
                "effective_flame_height_m": 6.0,
                "effective_receiver_height_m": 2.0,
                "near_field_regularization_m": 0.5,
                "atmospheric_extinction_m_inv": 0.0,
                "wind_speed_m_s": 0.0,
                "wind_from_deg": 0.0,
                "flame_tilt_coefficient_s_m": 0.0,
            },
        },
        "scenario": {
            "ambient_temperature_K": 300.0,
            "max_dt_s": 5.0,
            "end_time_min": 500.0,
            "sample_interval_min": 5.0,
            "ignitions": [
                {
                    "cell": {"row": ignition_row, "col": ignition_col},
                    "time_s": 0.0,
                    "kind": "direct",
                }
            ],
        },
    }


def build_hypothetical_material_case(
    imagery_path: str | Path,
    labelme_path: str | Path,
    output_dir: str | Path,
    *,
    cell_size_m: float = 2.0,
) -> tuple[Path, Path]:
    """Write source files for an RGB-inspired hypothetical multi-material case."""

    if not np.isfinite(cell_size_m) or cell_size_m <= 0.0:
        raise ValueError("cell_size_m must be finite and positive")
    imagery_source = Path(imagery_path).resolve()
    label_source = Path(labelme_path).resolve()
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"hypothetical source directory already exists: {output}")
    if not imagery_source.is_file() or not label_source.is_file():
        raise FileNotFoundError("imagery and LabelMe inputs must both exist")

    try:
        import rasterio
        from PIL import Image
        from rasterio.enums import Resampling
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        from rasterio.warp import reproject
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise ImportError("hypothetical material mapping requires GIS dependencies") from exc

    with rasterio.open(imagery_source) as imagery:
        if imagery.crs is None or not imagery.crs.is_projected or imagery.count < 3:
            raise ValueError("imagery must be a projected RGB GeoTIFF")
        if imagery.dtypes[:3] != ("uint8", "uint8", "uint8"):
            raise TypeError("imagery RGB bands must use uint8")
        x_min = floor(imagery.bounds.left / cell_size_m) * cell_size_m
        y_max = ceil(imagery.bounds.top / cell_size_m) * cell_size_m
        width = ceil((imagery.bounds.right - x_min) / cell_size_m)
        height = ceil((y_max - imagery.bounds.bottom) / cell_size_m)
        transform = from_origin(x_min, y_max, cell_size_m, cell_size_m)
        rgb = np.zeros((3, height, width), dtype=np.float32)
        for band in range(3):
            reproject(
                imagery.read(band + 1),
                rgb[band],
                src_transform=imagery.transform,
                src_crs=imagery.crs,
                dst_transform=transform,
                dst_crs=imagery.crs,
                resampling=Resampling.average,
            )
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

    annotations = load_labelme_buildings(label_source, image_reference)
    geometries = [Polygon(record["polygons"][0]["exterior"]) for record in annotations]
    building_index = rasterize(
        [(geometry, index + 1) for index, geometry in enumerate(geometries)],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        all_touched=True,
        dtype="int32",
    )
    building_mask = building_index > 0
    r, g, b = (rgb[index].astype(np.float64) for index in range(3))
    total = r + g + b + 1.0
    excess_green = (2.0 * g - r - b) / total
    background = ~building_mask
    low_green, high_green = np.quantile(excess_green[background], [0.35, 0.70])
    codes = np.ones((height, width), dtype=np.uint8)
    codes[background & (excess_green >= low_green)] = 2
    codes[background & (excess_green >= high_green)] = 3

    building_rgb = []
    for index in range(1, len(geometries) + 1):
        mask = building_index == index
        if np.any(mask):
            building_rgb.append(
                (index, *(float(np.median(channel[mask])) for channel in (r, g, b)))
            )
    brightness = np.asarray([(red + green + blue) / 3.0 for _, red, green, blue in building_rgb])
    warmth = np.asarray(
        [(red - blue) / (red + green + blue + 1.0) for _, red, green, blue in building_rgb]
    )
    warm_threshold = float(np.quantile(warmth, 0.72))
    bright_threshold = float(np.quantile(brightness, 0.62))
    building_classes: dict[int, int] = {}
    for (index, _red, _green, _blue), light, warm in zip(
        building_rgb, brightness, warmth, strict=True
    ):
        code = 6 if warm >= warm_threshold else (5 if light >= bright_threshold else 4)
        building_classes[index] = code
        codes[building_index == index] = code

    centroid_x = float(np.mean([geometry.centroid.x for geometry in geometries]))
    centroid_y = float(np.mean([geometry.centroid.y for geometry in geometries]))
    central_index = min(
        range(len(geometries)),
        key=lambda index: (
            (geometries[index].centroid.x - centroid_x) ** 2
            + (geometries[index].centroid.y - centroid_y) ** 2
        ),
    )
    central_cells = np.argwhere(building_index == central_index + 1)
    if central_cells.size == 0:
        raise ValueError("central building does not intersect the output grid")
    centroid = geometries[central_index].centroid
    cell_x = x_min + (central_cells[:, 1] + 0.5) * cell_size_m
    cell_y = y_max - (central_cells[:, 0] + 0.5) * cell_size_m
    selected = int(np.argmin((cell_x - centroid.x) ** 2 + (cell_y - centroid.y) ** 2))
    ignition_row, ignition_col = (int(value) for value in central_cells[selected])

    output.mkdir(parents=True)
    try:
        material_path = output / "hypothetical_materials.tif"
        with rasterio.open(
            material_path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
            crs=crs,
            transform=transform,
            compress="deflate",
        ) as dataset:
            dataset.write(codes, 1)
        (output / "material_codes.yml").write_text(
            yaml.safe_dump(
                {"schema_version": "material-codes-1", "codes": dict(enumerate(MATERIAL_IDS, 1))},
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        (output / "material_catalog.yml").write_text(
            yaml.safe_dump(_v1_catalog(), sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        case = {
            "schema_version": "village-case-1",
            "case_id": "gaoyou-rgb-hypothetical-materials",
            "mode": "synthetic_demo",
            "imagery": {"path": str(imagery_source), "reference": None},
            "buildings": {"path": str(label_source), "format": "labelme_json"},
            "materials": {
                "source": material_path.name,
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
        case_path = output / "case.hypothetical.yml"
        case_path.write_text(
            yaml.safe_dump(case, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        scenario_path = output / "scenario.v2.hypothetical.yml"
        scenario_path.write_text(
            yaml.safe_dump(
                _v2_scenario(ignition_row, ignition_col), sort_keys=False, allow_unicode=True
            ),
            encoding="utf-8",
        )
        counts = np.bincount(codes.ravel(), minlength=len(MATERIAL_IDS) + 1)
        building_counts = {
            MATERIAL_IDS[code - 1]: sum(value == code for value in building_classes.values())
            for code in (4, 5, 6)
        }
        report = {
            "schema_version": "hypothetical-material-classification-1",
            "status": "synthetic_hypothesis_not_ground_truth",
            "method": {
                "background": "35th and 70th quantiles of normalized excess green",
                "building": "72nd warmth and 62nd brightness quantiles of per-building median RGB",
                "background_fire_scope": "background classes are non-burning in this run",
            },
            "thresholds": {
                "normalized_excess_green_low": float(low_green),
                "normalized_excess_green_high": float(high_green),
                "building_warmth": warm_threshold,
                "building_brightness": bright_threshold,
            },
            "cell_counts": {
                material_id: int(counts[index]) for index, material_id in enumerate(MATERIAL_IDS, 1)
            },
            "building_counts": building_counts,
            "ignition": {
                "building_id": annotations[central_index]["building_id"],
                "row": ignition_row,
                "col": ignition_col,
                "material_id": MATERIAL_IDS[int(codes[ignition_row, ignition_col]) - 1],
            },
        }
        (output / "classification_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        palette = np.asarray(
            [
                [190, 180, 155],
                [150, 184, 88],
                [38, 105, 45],
                [130, 88, 57],
                [108, 113, 121],
                [205, 155, 78],
            ],
            dtype=np.uint8,
        )
        base = np.moveaxis(np.clip(rgb, 0, 255).astype(np.uint8), 0, -1)
        overlay = palette[codes - 1]
        preview = (0.45 * base + 0.55 * overlay).astype(np.uint8)
        Image.fromarray(preview).save(output / "material_preview.png")
    except Exception:
        import shutil

        shutil.rmtree(output)
        raise
    return case_path, scenario_path


__all__ = ["MATERIAL_IDS", "build_hypothetical_material_case"]
