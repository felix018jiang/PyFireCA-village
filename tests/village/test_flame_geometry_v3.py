"""Independent branch, geometry, time and resource checks for W02-a."""

import csv
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_geometry_v3 import (
    evaluate_flame_axes,
    inspect_component_flames,
    instantaneous_prescribed_hrr,
)
from pyfireca.village.wind_v3 import parse_wind

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("flame_axis_fixture", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def quantity(value, unit):
    return dict(value=value, unit=unit, evidence="synthetic", source="synthetic:test")


def wind(speed=2, direction=270):
    return parse_wind(
        dict(
            mode="uniform",
            direction_reference="grid_north",
            reference_height_m=quantity(10, "m"),
            speed_m_s=quantity(speed, "m/s"),
            from_deg=quantity(direction, "deg"),
        ),
        end_time_s=100,
    )


def arguments(**updates):
    result = dict(
        time_s=5,
        wind=wind(),
        wind_reference_height_m=10,
        wind_speed_factor=1,
        air_density_kg_m3=1,
        gravity_m_s2=1,
        mass_rate_kg_s=[1],
        footprint_area_m2=[1],
        characteristic_diameter_m=[1],
        axis_length_m=[4],
        base_xyz_m=[[10, 20, 30]],
    )
    return {**result, **updates}


def test_curve_boundaries_unknown_future_and_actual_burnout():
    def q(t, burnout=np.nan):
        return instantaneous_prescribed_hrr(t, [2], [burnout], [120], [3], [4], [6])[0]

    assert [q(t) for t in (0, 2, 3.5, 5, 9, 12, 15, 20)] == [0, 0, 30, 120, 120, 60, 0, 0]
    assert q(7, 7) == 0
    assert q(6, 7) == 120
    assert instantaneous_prescribed_hrr(5, [np.nan], [np.nan], [120], [3], [4], [6])[0] == 0
    assert instantaneous_prescribed_hrr(5, [2], [np.nan], [0], [0], [0], [0])[0] == 0
    with pytest.raises(ValueError, match="burnout"):
        q(5, 1)


def test_instantaneous_curve_integral_matches_prescribed_energy():
    times = np.linspace(0, 13, 2601)
    q = [
        instantaneous_prescribed_hrr(float(t), [0], [np.nan], [120], [3], [4], [6])[0]
        for t in times
    ]
    assert np.trapezoid(q, times) == pytest.approx(120 * (3 / 3 + 4 + 6 / 2), rel=1e-6)


@pytest.mark.parametrize("speed,cosine", [(0, 1), (0.5, 1), (1, 1), (4, 0.5)])
def test_aga_branches_and_axis_length(speed, cosine):
    frame = evaluate_flame_axes(**arguments(wind=wind(speed)))
    assert frame.axis_unit[0, 2] == pytest.approx(cosine)
    assert np.linalg.norm(frame.axis_unit[0]) == pytest.approx(1)
    assert np.linalg.norm(frame.tip_xyz_m[0] - [10, 20, 30]) == pytest.approx(4)
    np.testing.assert_allclose(frame.centroid_xyz_m[0], ([10, 20, 30] + frame.tip_xyz_m[0]) / 2)
    assert frame.dimensionless_wind[0] == pytest.approx(speed)


@pytest.mark.parametrize(
    "direction,xy", [(0, [0, -1]), (90, [-1, 0]), (180, [0, 1]), (270, [1, 0])]
)
def test_meteorological_direction_points_downwind(direction, xy):
    frame = evaluate_flame_axes(**arguments(wind=wind(4, direction)))
    np.testing.assert_allclose(frame.axis_unit[0, :2], np.sqrt(0.75) * np.array(xy), atol=1e-15)
    assert frame.tip_xyz_m[0, 2] == pytest.approx(32)


def test_mass_flux_area_and_density_have_correct_dimensions():
    frame = evaluate_flame_axes(
        **arguments(
            mass_rate_kg_s=[2],
            footprint_area_m2=[4],
            characteristic_diameter_m=[8],
            air_density_kg_m3=2,
        )
    )
    assert frame.mass_flux_kg_m2_s[0] == 0.5
    assert frame.dimensionless_wind[0] == pytest.approx(2 / (0.5 * 8 / 2) ** (1 / 3))


def test_inactive_has_no_ghost_flame_and_outputs_are_readonly():
    args = arguments(mass_rate_kg_s=[0], axis_length_m=[0])
    frame = evaluate_flame_axes(**args)
    assert not frame.active[0]
    assert frame.mass_flux_kg_m2_s[0] == 0
    assert np.isnan(frame.tip_xyz_m).all()
    assert np.isnan(frame.axis_unit).all()
    assert args["base_xyz_m"] == [[10, 20, 30]]
    with pytest.raises(ValueError):
        frame.tip_xyz_m[0] = 0


@pytest.mark.parametrize(
    "updates",
    [
        {"wind_reference_height_m": 2},
        {"footprint_area_m2": [0]},
        {"mass_rate_kg_s": [-1]},
        {"axis_length_m": [0]},
        {"mass_rate_kg_s": [True]},
        {"mass_rate_kg_s": [[1]]},
        {"base_xyz_m": [[0, np.nan, 0]]},
        {"characteristic_diameter_m": [np.inf]},
        {"wind_speed_factor": 0},
        {"mass_rate_kg_s": [1e308], "footprint_area_m2": [1e-308]},
        {"mass_rate_kg_s": [1e-308], "footprint_area_m2": [1e308]},
        {"time_s": 101},
    ],
)
def test_invalid_inputs_fail(updates):
    with pytest.raises(ValueError):
        evaluate_flame_axes(**arguments(**updates))


def test_legacy_wind_cannot_silently_become_calm():
    legacy = parse_wind({"mode": "legacy_edges"}, end_time_s=100)
    with pytest.raises(ValueError):
        evaluate_flame_axes(**arguments(wind=legacy))


@pytest.fixture
def saved(tmp_path):
    request = audit.make_request(
        ("grass", "tree_equivalent", "building", "bare_or_road"), end_time_s=100
    )
    request = replace(request, ground_elevation_m=np.array([[100.0, 101.0, 102.0, 103.0]]))
    resolved = resolve_components(
        request, load_component_configuration(DIRECTORY / "components.synthetic.yml")
    )
    result = run_component_simulation(resolved)
    return write_component_run(resolved, result, tmp_path / "saved")


def inspect(saved, output, **kwargs):
    return inspect_component_flames(
        saved,
        DIRECTORY / "w01-wind.synthetic.yml",
        DIRECTORY / "w02a-flame-geometry.synthetic.yml",
        output,
        end_time_s=60,
        **kwargs,
    )


def test_saved_run_reconstruction_dem_no_thermal_mutation_and_reproducibility(saved, tmp_path):
    before = {p: p.read_bytes() for p in saved.rglob("*") if p.is_file()}
    first = inspect(saved, tmp_path / "a", sample_interval_s=7)
    second = inspect(saved, tmp_path / "b", sample_interval_s=7)
    assert first == second
    assert first["time_s"] == [0, 7, 14, 21, 28, 35, 42, 49, 56, 60]
    assert first["wind_coupled_heat_transport"] is False
    assert first["thermal_solver_executed"] is False
    assert first["ground_mode"] == "saved_dem"
    assert first["record_count"] == 60
    assert before == {p: p.read_bytes() for p in saved.rglob("*") if p.is_file()}
    assert (tmp_path / "a/flame_axes.csv").read_bytes() == (
        tmp_path / "b/flame_axes.csv"
    ).read_bytes()
    with (tmp_path / "a/flame_axes.csv").open() as stream:
        records = list(csv.DictReader(stream))
    assert all(r["tip_z_m"] == "" for r in records if r["active"] == "False")
    burning = [r for r in records if r["node_id"] == "0" and r["active"] == "True"]
    assert burning
    assert all(float(r["tip_z_m"]) > 100 for r in burning)


@pytest.mark.parametrize(
    "kwargs", [{"max_records": 10}, {"sample_interval_s": 0}, {"sample_interval_s": 1e-308}]
)
def test_resource_or_interval_rejects_before_output(saved, tmp_path, kwargs):
    output = tmp_path / "bad"
    with pytest.raises(ValueError):
        inspect(saved, output, **kwargs)
    assert not output.exists()


def test_bad_saved_node_count_fails_before_output(saved, tmp_path):
    path = saved / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["node_count"] -= 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="node count"):
        inspect(saved, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_cli_writes_diagnostic_and_rejects_overwrite(saved, tmp_path, capsys):
    from pyfireca.cli import main

    args = [
        "village",
        "inspect-component-flames-v3",
        str(saved),
        "--wind",
        str(DIRECTORY / "w01-wind.synthetic.yml"),
        "--geometry",
        str(DIRECTORY / "w02a-flame-geometry.synthetic.yml"),
        "--end-time-s",
        "60",
        "--output",
        str(tmp_path / "cli"),
    ]
    assert main(args) == 0
    assert "heat transport unchanged" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "cli/manifest.json").read_text())
    assert manifest["record_count"] == 42
    assert main(args) != 0


def test_saved_energy_tampering_rejects_before_output(saved, tmp_path):
    path = saved / "components.csv"
    with path.open() as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    rows[0]["peak_hrr_W"] = str(float(rows[0]["peak_hrr_W"]) * 2)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="energy budget"):
        inspect(saved, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_missing_footprint_and_duplicate_yaml_reject(saved, tmp_path):
    import yaml

    config = yaml.safe_load((DIRECTORY / "w02a-flame-geometry.synthetic.yml").read_text())
    del config["footprints"]["whole"]
    geometry = tmp_path / "geometry.yml"
    geometry.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="footprint"):
        inspect_component_flames(
            saved, DIRECTORY / "w01-wind.synthetic.yml", geometry, tmp_path / "bad", end_time_s=60
        )
    geometry.write_text("schema_version: a\nschema_version: b\n")
    with pytest.raises(ValueError, match="duplicate"):
        inspect_component_flames(
            saved, DIRECTORY / "w01-wind.synthetic.yml", geometry, tmp_path / "bad", end_time_s=60
        )
    assert not (tmp_path / "bad").exists()
