from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import yaml
from pyproj import Transformer
from rasterio.transform import from_origin

from pyfireca.village.bootstrap import bootstrap_village_case
from pyfireca.village.prepare import load_prepared_village, prepare_village_case
from pyfireca.village.workflow import validate_village_run


def _write_projected_case(root: Path, *, shifted_materials: bool = False) -> Path:
    root.mkdir()
    crs = "EPSG:32648"
    to_projected = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    center_x, center_y = to_projected.transform(106.0, 26.0)
    x_min = float(round(center_x))
    y_max = float(round(center_y) + 20)

    imagery = np.zeros((3, 4, 4), dtype=np.uint8)
    imagery[0] = 80
    imagery[1] = 110
    imagery[2] = 70
    with rasterio.open(
        root / "village.tif",
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=3,
        dtype="uint8",
        crs=crs,
        transform=from_origin(x_min, y_max, 5.0, 5.0),
    ) as dataset:
        dataset.write(imagery)

    material_transform = from_origin(x_min + (1.0 if shifted_materials else 0.0), y_max, 10.0, 10.0)
    with rasterio.open(
        root / "materials.tif",
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="uint8",
        crs=crs,
        transform=material_transform,
        nodata=255,
    ) as dataset:
        dataset.write(np.array([[1, 2], [1, 2]], dtype=np.uint8), 1)

    (root / "material_codes.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "material-codes-1",
                "codes": {1: "demo_wood", 2: "inert_stone"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    parameters = {
        "fuel_load_kg_m2": 2.0,
        "heat_of_combustion_J_kg": 1_000_000.0,
        "mass_loss_flux_kg_m2_s": 0.1,
        "active_area_ratio": 1.0,
        "emission_fraction": 0.2,
        "absorptivity": 0.8,
        "ignition_dose_J_m2": 1000.0,
        "exposure_decay_s": 10.0,
    }
    (root / "catalog.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "material-catalog-1",
                "library_id": "gis-fixture-v1",
                "materials": [
                    {
                        "id": "demo_wood",
                        "label": "Synthetic wood",
                        "role": "building_fuel",
                        "burnable": True,
                        "evidence": "synthetic",
                        "source_id": "gis-fixture",
                        "parameters": parameters,
                    },
                    {
                        "id": "inert_stone",
                        "label": "Synthetic stone",
                        "role": "inert",
                        "burnable": False,
                        "evidence": "synthetic",
                        "source_id": "gis-fixture",
                        "parameters": {},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    projected_ring = [
        (x_min + 1, y_max - 1),
        (x_min + 9, y_max - 1),
        (x_min + 9, y_max - 9),
        (x_min + 1, y_max - 9),
        (x_min + 1, y_max - 1),
    ]
    wgs84_ring = [list(to_wgs84.transform(x, y)) for x, y in projected_ring]
    (root / "buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"building_id": "B001", "source": "fixture"},
                        "geometry": {"type": "Polygon", "coordinates": [wgs84_ring]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    case = {
        "schema_version": "village-case-1",
        "case_id": "projected-gis-fixture",
        "mode": "synthetic_demo",
        "imagery": {"path": "village.tif", "reference": None},
        "buildings": {"path": "buildings.geojson", "format": "geojson_wgs84"},
        "materials": {
            "source": "materials.tif",
            "format": "geotiff_codes",
            "codes": "material_codes.yml",
            "catalog": "catalog.yml",
        },
        "spatial": {
            "coordinate_mode": "projected",
            "crs": crs,
            "grid": {
                "x_min_m": x_min,
                "y_max_m": y_max,
                "height": 2,
                "width": 2,
                "cell_size_m": 10,
            },
        },
    }
    case_path = root / "case.yml"
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    return case_path


def test_prepare_projected_geotiffs_and_wgs84_buildings(tmp_path: Path) -> None:
    case = _write_projected_case(tmp_path / "source")
    prepared = prepare_village_case(case, tmp_path / "prepared")
    domain = load_prepared_village(prepared)

    assert domain.grid.crs == "EPSG:32648"
    assert np.all(domain.known_mask)
    np.testing.assert_array_equal(domain.burnable_mask, [[True, False], [True, False]])
    assert (prepared / "background.png").is_file()
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["building_count"] == 1
    assert manifest["building_overlap_count"] == 1
    assert len(manifest["sources_sha256"]) == 6


def test_rejects_material_geotiff_that_is_not_grid_aligned(tmp_path: Path) -> None:
    case = _write_projected_case(tmp_path / "source", shifted_materials=True)
    with pytest.raises(ValueError, match="transform does not match"):
        prepare_village_case(case, tmp_path / "prepared")


def test_bootstrap_builds_runnable_placeholder_case_from_geotiff(tmp_path: Path) -> None:
    _write_projected_case(tmp_path / "source")
    case, scenario = bootstrap_village_case(
        tmp_path / "source" / "village.tif",
        tmp_path / "bootstrap",
        cell_size_m=10.0,
        end_time_s=2.0,
        sample_interval_s=1.0,
    )
    prepared = prepare_village_case(case, tmp_path / "prepared")
    request, config = validate_village_run(prepared, scenario)

    assert request.domain.grid.height >= 2
    assert request.domain.grid.width >= 2
    assert config.scenario.end_time_s == 2.0
    assert (
        (tmp_path / "bootstrap" / "README.md")
        .read_text()
        .startswith("# Synthetic village bootstrap")
    )


def test_bootstrap_uses_labelme_buildings_for_material_and_ignition(tmp_path: Path) -> None:
    _write_projected_case(tmp_path / "source")
    labelme = tmp_path / "buildings.json"
    labelme.write_text(
        json.dumps(
            {
                "version": "5.0",
                "flags": {},
                "shapes": [
                    {
                        "label": "building",
                        "points": [[0.5, 0.5], [2.5, 2.5]],
                        "group_id": None,
                        "shape_type": "rectangle",
                        "flags": {},
                    }
                ],
                "imagePath": "village.tif",
                "imageData": None,
                "imageHeight": 4,
                "imageWidth": 4,
            }
        ),
        encoding="utf-8",
    )
    case, scenario = bootstrap_village_case(
        tmp_path / "source" / "village.tif",
        tmp_path / "bootstrap",
        cell_size_m=5.0,
        end_time_s=2.0,
        sample_interval_s=1.0,
        labelme_path=labelme,
    )
    case_data = yaml.safe_load(case.read_text(encoding="utf-8"))
    assert case_data["buildings"]["format"] == "labelme_json"
    prepared = prepare_village_case(case, tmp_path / "prepared")
    request, scenario_config = validate_village_run(prepared, scenario)
    ignition = scenario_config.scenario.ignitions[0]

    assert request.domain.material_index[ignition.row, ignition.col] == 1
    overlap_rows = (prepared / "building_overlap.csv").read_text(encoding="utf-8")
    assert f",{ignition.row},{ignition.col}," in overlap_rows
    buildings = json.loads((prepared / "buildings.json").read_text(encoding="utf-8"))
    assert buildings["buildings"][0]["properties"]["data_status"] == "observed_annotation"
    bootstrap_metadata = json.loads(
        (tmp_path / "bootstrap" / "bootstrap_metadata.json").read_text(encoding="utf-8")
    )
    assert bootstrap_metadata["ignition_building_id"] == "B0001"
    assert bootstrap_metadata["ignition_selection"] == "nearest_building_to_mean_centroid"
