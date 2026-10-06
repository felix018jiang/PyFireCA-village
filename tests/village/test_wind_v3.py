from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pyfireca import cli
from pyfireca.village.wind_v3 import load_wind, parse_wind, wind_from_to_velocity


def quantity(value: float, unit: str) -> dict:
    return {"value": value, "unit": unit, "evidence": "synthetic", "source": "synthetic:test"}


def uniform(speed: float = 2.0, angle: float = 270.0) -> dict:
    return {
        "mode": "uniform",
        "speed_m_s": quantity(speed, "m/s"),
        "from_deg": quantity(angle, "deg"),
        "reference_height_m": quantity(10.0, "m"),
        "direction_reference": "grid_north",
    }


def series(angles: tuple = (270.0, 90.0, 270.0)) -> dict:
    return {
        "mode": "timeseries",
        "direction_reference": "grid_north",
        "reference_height_m": quantity(10.0, "m"),
        "evidence": "synthetic",
        "source": "synthetic:time_series",
        "samples": [
            {"time_s": 10.0 * i, "speed_m_s": 2.0, "from_deg": angle}
            for i, angle in enumerate(angles)
        ],
    }


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (0.0, (0.0, -2.0)),
        (90.0, (-2.0, 0.0)),
        (180.0, (0.0, 2.0)),
        (270.0, (2.0, 0.0)),
    ],
)
def test_cardinal_from_directions_and_uniform_advection(angle, expected) -> None:
    assert wind_from_to_velocity(2.0, angle) == expected
    wind = parse_wind(uniform(angle=angle), end_time_s=30.0)
    assert wind.velocity_at(30.0, x_m=371000.0, y_m=2874500.0, z_m=500.0) == (*expected, 0.0)
    assert wind.horizontal_displacement(0.0, 30.0) == tuple(30 * x for x in expected)
    assert wind.next_change_after(0.0) is None


def test_zero_wind_and_north_crossing_use_vectors() -> None:
    calm = parse_wind(uniform(0.0, 135.0), end_time_s=20.0)
    assert calm.velocity_at(10.0) == (0.0, 0.0, 0.0)
    wind = parse_wind(series((359.0, 1.0)), end_time_s=10.0)
    u, v, w = wind.velocity_at(5.0)
    assert abs(u) < 1e-14
    assert v == pytest.approx(-1.9996953903127825)
    assert w == 0.0
    assert wind.velocity_at(0.0)[:2] == wind_from_to_velocity(2.0, 359.0)
    assert wind.velocity_at(10.0)[:2] == wind_from_to_velocity(2.0, 1.0)


def test_time_knots_partial_interval_and_integral_partition() -> None:
    wind = parse_wind(series(), end_time_s=17.0)
    assert wind.velocity_at(5.0) == (0.0, 0.0, 0.0)
    assert wind.velocity_at(10.0) == (-2.0, 0.0, 0.0)
    assert wind.velocity_at(17.0) == pytest.approx((0.8, 0.0, 0.0))
    assert wind.next_change_after(0.0) == 10.0
    assert wind.next_change_after(10.0) is None
    assert wind.horizontal_displacement(0.0, 10.0) == (0.0, 0.0)
    assert wind.horizontal_displacement(0.0, 17.0) == pytest.approx((-4.2, 0.0))
    a = wind.horizontal_displacement(0.0, 3.25)
    b = wind.horizontal_displacement(3.25, 17.0)
    assert tuple(x + y for x, y in zip(a, b, strict=True)) == pytest.approx((-4.2, 0.0))
    assert wind.horizontal_displacement(17.0, 17.0) == (0.0, 0.0)
    with pytest.raises(ValueError, match="end_s"):
        wind.horizontal_displacement(10.0, 5.0)


@pytest.mark.parametrize("time", [-1, 17.01, True, float("nan"), float("inf")])
def test_query_does_not_extrapolate(time) -> None:
    wind = parse_wind(series(), end_time_s=17.0)
    with pytest.raises(ValueError):
        wind.velocity_at(time)
    with pytest.raises(ValueError):
        wind.next_change_after(time)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("speed_m_s", -1),
        ("speed_m_s", True),
        ("speed_m_s", float("nan")),
        ("speed_m_s", float("inf")),
        ("speed_m_s", "2"),
        ("speed_m_s", 10**400),
        ("from_deg", -1),
        ("from_deg", 360),
        ("reference_height_m", 0),
    ],
)
def test_invalid_quantities_are_rejected(key, value) -> None:
    config = uniform()
    config[key]["value"] = value
    with pytest.raises(ValueError):
        parse_wind(config, end_time_s=20.0)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"direction_reference": "true_north"},
        {"mode": "grid_uv"},
        {"speed_m_s": {"value": 2, "unit": "km/h", "evidence": "synthetic", "source": "test"}},
        {"speed_m_s": {"value": 2, "unit": "m/s", "evidence": "unknown", "source": "test"}},
        {"speed_m_s": {"value": 2, "unit": "m/s", "evidence": "synthetic", "source": " "}},
    ],
)
def test_units_evidence_modes_and_unknown_fields(change) -> None:
    with pytest.raises(ValueError):
        parse_wind({**uniform(), **change}, end_time_s=20.0)


@pytest.mark.parametrize("times", [(0, 0, 20), (0, 20, 10), (1, 10, 20), (0, 5, 9)])
def test_bad_series_coverage_and_order(times) -> None:
    config = series()
    for sample, time in zip(config["samples"], times, strict=True):
        sample["time_s"] = time
    with pytest.raises(ValueError):
        parse_wind(config, end_time_s=10.0)


def test_legacy_is_not_zero_wind_and_input_is_immutable() -> None:
    legacy = parse_wind({"mode": "legacy_edges"}, end_time_s=20.0)
    with pytest.raises(ValueError, match="no recoverable wind"):
        legacy.velocity_at(0.0)
    with pytest.raises(ValueError):
        legacy.horizontal_displacement(0.0, 0.0)
    config = series()
    original = deepcopy(config)
    wind = parse_wind(config, end_time_s=20.0)
    digest = wind.configuration_sha256
    config["samples"][0]["speed_m_s"] = 99
    snapshot = wind.to_dict()
    snapshot["samples"][0]["speed_m_s"] = 12
    assert wind.to_dict() == original
    assert wind.velocity_at(0.0) == (2.0, 0.0, 0.0)
    assert wind.configuration_sha256 == digest
    assert parse_wind(original, end_time_s=20.0).configuration_sha256 == digest


def test_yaml_errors_include_original_path_and_duplicate_key(tmp_path: Path) -> None:
    path = tmp_path / "bad-wind.yml"
    path.write_text("mode: uniform\nmode: legacy_edges\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key") as error:
        load_wind(path, end_time_s=10.0)
    assert str(path) in str(error.value)
    config = series()
    del config["samples"][0]["speed_m_s"]
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=r"wind.samples\[0\]") as error:
        load_wind(path, end_time_s=10.0)
    assert str(path) in str(error.value)


def test_cli_runs_wind_computation_without_claiming_fire_coupling(tmp_path, capsys) -> None:
    import json

    path = tmp_path / "wind.yml"
    output = tmp_path / "report.json"
    path.write_text(yaml.safe_dump(uniform()), encoding="utf-8")
    command = [
        "village",
        "inspect-wind-v3",
        str(path),
        "--end-time-s",
        "20",
        "--output",
        str(output),
    ]
    assert cli.main(command) == 0
    report = json.loads(output.read_text())
    assert report["fire_simulation_executed"] is False
    assert report["samples"][-1]["advection_x_m"] == 40.0
    assert report["wind"]["speed_m_s"]["source"] == "synthetic:test"
    original = output.read_bytes()
    assert cli.main(command) == 2
    assert output.read_bytes() == original
    path.write_text("mode: grid_uv\n", encoding="utf-8")
    assert cli.main(command) == 2
    assert str(path) in capsys.readouterr().err
