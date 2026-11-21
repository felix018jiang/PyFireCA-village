import json

import numpy as np
import pytest

from pyfireca.village.building_candidates import extract_building_candidates, roof_candidate_mask


def test_green_and_shadow_are_not_roof_candidates():
    rgb = np.zeros((3, 30, 30), dtype=np.uint8)
    rgb[:, :, :15] = np.array([30, 100, 25])[:, None, None]
    rgb[:, :, 15:] = 10
    assert not roof_candidate_mask(rgb, np.ones((30, 30), dtype=bool)).any()


def test_roof_polygonization_rejects_long_road_and_preserves_evidence(tmp_path):
    pytest.importorskip("cv2")
    rasterio = pytest.importorskip("rasterio")
    rgba = np.zeros((4, 100, 140), dtype=np.uint8)
    rgba[:3] = np.array([30, 100, 25])[:, None, None]
    rgba[3] = 255
    rgba[:3, 10:28, 15:37] = 100  # isolated rectangular roof, 99 m²
    rgba[:3, 70:78, 10:125] = 100  # long road strip
    source = tmp_path / "imagery.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=140,
        height=100,
        count=4,
        dtype="uint8",
        crs="EPSG:32649",
        transform=rasterio.transform.from_origin(346000, 2870000, 0.5, 0.5),
    ) as s:
        s.colorinterp = (
            rasterio.enums.ColorInterp.red,
            rasterio.enums.ColorInterp.green,
            rasterio.enums.ColorInterp.blue,
            rasterio.enums.ColorInterp.alpha,
        )
        s.write(rgba)
    records = extract_building_candidates(source, tmp_path / "output")
    assert len(records) == 1
    assert records[0]["properties"]["field_verified"] is False
    assert records[0]["properties"]["data_status"] == "rgb_shape_candidate"
    report = json.loads((tmp_path / "output/building_detection_report.json").read_text())
    assert report["trained_model"] is False
