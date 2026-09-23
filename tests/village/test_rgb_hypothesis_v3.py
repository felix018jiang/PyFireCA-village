from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pyfireca.village.rgb_hypothesis_v3 import complete_rgb_hypothesis_spatial


def _write_fixture(directory: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    (directory / "arrays").mkdir(parents=True)
    manifest = {
        "schema_version": "prepared-village-vegetation-spatial-1",
        "stage": "V01",
        "material_resolution_status": "pending_v02",
        "grid": {
            "height": 2,
            "width": 2,
            "cell_size_m": 2.0,
            "x_min_m": 0.0,
            "y_max_m": 4.0,
            "coordinate_mode": "projected",
            "crs": "EPSG:32649",
        },
        "landcover_ids": [
            "bare_or_road",
            "grass",
            "shrub",
            "forest_floor",
            "tree_equivalent",
            "building",
        ],
        "evidence_ids": ["unknown", "rgb_hypothesis", "observed_annotation"],
        "sources_sha256": {},
        "artifacts": {},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "classification_report.json").write_text("{}", encoding="utf-8")
    np.save(directory / "arrays/domain_mask.npy", np.asarray([[True, True], [False, True]]))
    np.save(directory / "arrays/known_mask.npy", np.asarray([[True, False], [False, True]]))
    np.save(
        directory / "arrays/landcover_index.npy",
        np.asarray([[1, -1], [-1, 2]], dtype=np.int16),
    )
    np.save(directory / "arrays/evidence_index.npy", np.asarray([[1, 0], [0, 1]], dtype=np.uint8))
    np.save(
        directory / "arrays/landcover_coverage_fraction.npy",
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )
    with rasterio.open(
        directory / "landcover.tif",
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        crs="EPSG:32649",
        transform=from_origin(0.0, 4.0, 2.0, 2.0),
        nodata=-1,
    ) as dataset:
        dataset.write(np.asarray([[1, -1], [-1, 2]], dtype=np.int16), 1)
    with rasterio.open(
        directory / "evidence.tif",
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:32649",
        transform=from_origin(0.0, 4.0, 2.0, 2.0),
        nodata=0,
    ) as dataset:
        dataset.write(np.asarray([[1, 0], [0, 1]], dtype=np.uint8), 1)


def test_rgb_completion_is_explicit_and_does_not_mutate_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_fixture(source)
    output = complete_rgb_hypothesis_spatial(source, tmp_path / "output")

    report = json.loads((output / "hypothetical_completion_report.json").read_text())
    assert report["original_unknown_cell_count"] == 1
    assert report["filled_bare_or_road_cell_count"] == 1
    assert np.array_equal(
        np.load(output / "arrays/landcover_index.npy"),
        np.asarray([[1, 0], [-1, 2]], dtype=np.int16),
    )
    assert np.array_equal(
        np.load(source / "arrays/landcover_index.npy"),
        np.asarray([[1, -1], [-1, 2]], dtype=np.int16),
    )
