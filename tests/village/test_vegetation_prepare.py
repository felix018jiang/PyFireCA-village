import json

import numpy as np
import pytest

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.vegetation_prepare import (
    LANDCOVER_IDS,
    building_cell_intersections,
    geometry_to_polygons,
    prepare_vegetation_spatial,
)


def _record(building_id, geometry):
    return {
        "building_id": building_id,
        "polygons": geometry_to_polygons(geometry),
        "properties": {},
    }


def test_geometry_serialization_preserves_holes_and_multipart():
    from shapely.geometry import MultiPolygon, Polygon

    polygon_with_hole = Polygon(
        [(0, 0), (4, 0), (4, 4), (0, 4)],
        holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]],
    )
    geometry = MultiPolygon(
        [polygon_with_hole, Polygon([(5, 0), (6, 0), (6, 1), (5, 1)])]
    )

    serialized = geometry_to_polygons(geometry)

    assert len(serialized) == 2
    assert len(serialized[0]["holes"]) == 1
    assert serialized[0]["holes"][0][0] == [1.0, 1.0]


def test_building_union_coverage_does_not_double_count_overlap():
    from shapely.geometry import Polygon

    grid = VillageGridSpec(1, 1, 2.0, 0.0, 2.0)
    first = Polygon([(0, 0), (1.5, 0), (1.5, 2), (0, 2)])
    second = Polygon([(0.5, 0), (2, 0), (2, 2), (0.5, 2)])

    rows, coverage, _ = building_cell_intersections(
        [_record("B0001", first), _record("B0002", second)], grid
    )

    assert sum(row[3] for row in rows) == pytest.approx(6.0)
    assert coverage[0, 0] == pytest.approx(1.0)


def test_prepare_spatial_reprojects_masks_and_selects_ignition(tmp_path):
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    image_path = tmp_path / "source.tif"
    annotation_path = tmp_path / "buildings.json"
    output = tmp_path / "prepared"
    transform = from_origin(12_213_000.0, 2_997_500.0, 1.0, 1.0)
    rgba = np.zeros((4, 20, 20), dtype=np.uint8)
    rgba[0] = 60
    rgba[1] = 150
    rgba[2] = 50
    rgba[3] = 255
    rgba[3, :5, :5] = 0
    with rasterio.open(
        image_path,
        "w",
        driver="GTiff",
        width=20,
        height=20,
        count=4,
        dtype="uint8",
        crs="EPSG:3857",
        transform=transform,
    ) as dataset:
        dataset.write(rgba)
        dataset.colorinterp = (
            ColorInterp.red,
            ColorInterp.green,
            ColorInterp.blue,
            ColorInterp.alpha,
        )
    annotation_path.write_text(
        json.dumps(
            {
                "imageWidth": 20,
                "imageHeight": 20,
                "shapes": [
                    {
                        "label": "building",
                        "shape_type": "polygon",
                        "points": [[7.2, 7.2], [13.6, 7.2], [13.6, 13.6], [7.2, 13.6]],
                    },
                    {
                        "label": "building",
                        "shape_type": "rectangle",
                        "points": [[0.5, 0.5], [3.5, 3.5]],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    prepare_vegetation_spatial(
        image_path,
        annotation_path,
        output,
        cell_size_m=2.0,
        ignition_building_id="B0001",
    )

    manifest = json.loads((output / "manifest.json").read_text())
    quality = json.loads((output / "quality_report.json").read_text())
    ignition = json.loads((output / "ignition.json").read_text())
    domain = np.load(output / "arrays/domain_mask.npy", allow_pickle=False)
    landcover = np.load(output / "arrays/landcover_index.npy", allow_pickle=False)
    building_coverage = np.load(
        output / "arrays/building_union_coverage_fraction.npy", allow_pickle=False
    )

    assert manifest["grid"]["crs"] == "EPSG:32649"
    assert quality["valid_for_v01_spatial_acceptance"] is True
    assert abs(quality["scale_diagnostics"]["working_scale_error_percent"]) < 0.1
    assert quality["scale_diagnostics"]["coordinate_roundtrip_max_pixel_error"] < 1e-6
    assert abs(quality["scale_diagnostics"]["coverage_area_closure_error_m2"]) < 1e-4
    assert np.any(~domain)
    assert np.all((building_coverage >= 0.0) & (building_coverage <= 1.0))
    assert np.all(building_coverage[~domain] == 0.0)
    assert np.any((building_coverage > 0.0) & (building_coverage < 1.0))
    row, col = ignition["row"], ignition["col"]
    assert domain[row, col]
    assert landcover[row, col] == LANDCOVER_IDS.index("building")
    assert ignition["building_overlap_area_m2"] > 0.0
    with rasterio.open(output / "landcover.tif") as dataset:
        assert dataset.crs.to_epsg() == 32649
        assert dataset.transform.a == pytest.approx(2.0)


def test_prepare_spatial_refuses_to_overwrite(tmp_path):
    output = tmp_path / "exists"
    output.mkdir()
    with pytest.raises(FileExistsError):
        prepare_vegetation_spatial("missing.tif", "missing.json", output)
