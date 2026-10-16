"""Research entry points and native-to-geographic intervention exports."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "name",
    [
        "run_batuan_intervention_study.py",
        "check_batuan_intervention_horizon.py",
        "render_batuan_intervention_report.py",
    ],
)
def test_study_entry_points_load(name):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def load_renderer():
    spec = importlib.util.spec_from_file_location(
        "intervention_renderer", ROOT / "scripts/render_batuan_intervention_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_geojson_uses_wgs84_and_preserves_native_geometry():
    renderer = load_renderer()
    native = {
        "type": "Polygon",
        "coordinates": [
            [[347000, 2870000], [347005, 2870000], [347005, 2870005], [347000, 2870000]]
        ],
    }
    result = renderer.geographic_feature(native, {"area_m2": 12.5}, "EPSG:32649")
    longitude, latitude = result["geometry"]["coordinates"][0][0]
    assert 109 < longitude < 110
    assert 25 < latitude < 27
    assert result["projected_geometry"] == native
    assert result["projected_crs"] == "EPSG:32649"
    assert result["properties"]["area_m2"] == 12.5


def test_empty_intervention_has_null_geographic_geometry():
    renderer = load_renderer()
    result = renderer.geographic_feature(
        {"type": "GeometryCollection", "geometries": []}, {}, "EPSG:32649"
    )
    assert result["geometry"] is None
