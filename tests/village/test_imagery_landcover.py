from pathlib import Path
import json

import numpy as np
import pytest

from pyfireca.village.imagery_landcover import classify_rgb_candidates, prepare_rgb_landcover


def test_spectral_candidates_preserve_shadows_and_missing_data():
    rgb = np.array([
        [[40, 90, 140, 150, 30, 10, 80]],
        [[70, 140, 100, 150, 60, 10, 80]],
        [[30, 70, 70, 150, 80, 10, 80]],
    ], dtype=np.uint8)
    valid = np.array([[True, True, True, True, True, True, False]])
    result = classify_rgb_candidates(rgb, valid)
    assert result.tolist() == [[1, 2, 3, 4, 5, 0, 255]]


def test_unknown_low_green_is_not_silently_assigned_hard_surface():
    rgb = np.full((3, 1, 1), 40, dtype=np.uint8)
    assert classify_rgb_candidates(rgb, np.ones((1, 1), dtype=bool))[0, 0] == 0


def test_preparation_keeps_georeferencing_alpha_and_unknown_evidence(tmp_path: Path):
    rasterio = pytest.importorskip("rasterio")
    imagery = tmp_path / "imagery.tif"
    rgba = np.full((4, 4, 4), 10, dtype=np.uint8)
    rgba[3] = 255
    rgba[3, 0, 0] = 0
    transform = rasterio.transform.from_origin(346000, 2870000, 2, 2)
    with rasterio.open(imagery, "w", driver="GTiff", width=4, height=4, count=4,
                       dtype="uint8", crs="EPSG:32649", transform=transform) as source:
        source.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                              rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha)
        source.write(rgba)
    output = prepare_rgb_landcover(imagery, tmp_path / "classified")
    with rasterio.open(output / "landcover.tif") as result:
        assert result.transform == transform
        assert result.crs.to_epsg() == 32649
        values = result.read(1)
        assert values[0, 0] == 255
        assert np.all(values[1:] == 0)
    evidence = np.load(output / "arrays/evidence_index.npy")
    assert evidence[0, 0] == 255
    assert np.all(evidence[1:] == 0)
    assert not np.load(output / "arrays/known_mask.npy").any()
    report = json.loads((output / "classification_report.json").read_text())
    assert report["valid_cell_count"] == 15
    assert report["simulation_ready"] is False
    assert (output / "landcover.u8").stat().st_size == 16


@pytest.mark.parametrize("cell_size", [0, -1, float("nan")])
def test_invalid_grid_size_is_rejected(tmp_path: Path, cell_size: float):
    with pytest.raises(ValueError, match="positive"):
        prepare_rgb_landcover(tmp_path / "missing.tif", tmp_path / "output", cell_size)
