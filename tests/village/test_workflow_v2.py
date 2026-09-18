from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from pyfireca.village.state import VillageCellState
from pyfireca.village.workflow_v2 import load_v2_file_case, run_v2_file_case

EXAMPLE = Path("examples/village/v2_two_cells/case.synthetic.yml")
POINT_SOURCE_EXAMPLE = Path("examples/village/v2_two_cells/case.point-source.synthetic.yml")


def test_v2_file_case_resolves_energy_consistent_curve_and_runs(tmp_path: Path) -> None:
    case = load_v2_file_case(EXAMPLE)

    np.testing.assert_allclose(case.request.parameters.steady_time_s, [[8.0, 8.0]])
    result, artifacts = run_v2_file_case(EXAMPLE, tmp_path / "run")

    assert result.final_state.state[0, 0] == int(VillageCellState.BURNED_OUT)
    assert result.final_state.ignition_time_s[0, 1] == pytest.approx(1.0)
    assert result.ignition_cause.tolist() == [[1, 2]]
    record = json.loads((artifacts.directory / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed"
    assert record["evidence"] == "synthetic_numerical_fixture_not_physical_calibration"
    assert (artifacts.outputs / "temperature_samples_K.npy").is_file()
    assert (artifacts.outputs / "cell_summary.csv").is_file()


def test_v2_file_case_rejects_missing_runtime_value(tmp_path: Path) -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["material_profiles"][0]["parameters"]["heat_of_combustion_J_kg"] = None
    broken = tmp_path / "broken.yml"
    broken.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(TypeError, match="must be numeric"):
        load_v2_file_case(broken)


def test_v2_file_case_rejects_unknown_ignition_mode(tmp_path: Path) -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["construction_profiles"][0]["ignition_mode"] = "assumed_autoignition"
    broken = tmp_path / "broken.yml"
    broken.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="only explicit piloted ignition"):
        load_v2_file_case(broken)


def test_v2_file_case_can_generate_point_source_transport(tmp_path: Path) -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["transport"] = {
        "mode": "effective_point_source",
        "calibration": "synthetic_geometry_test",
        "parameters": {
            "exposure_radius_m": 2.0,
            "effective_flame_height_m": 2.0,
            "effective_receiver_height_m": 1.0,
            "near_field_regularization_m": 0.25,
            "atmospheric_extinction_m_inv": 0.0,
            "wind_speed_m_s": 0.0,
            "wind_from_deg": 0.0,
            "flame_tilt_coefficient_s_m": 0.0,
        },
        "blocker_transmissivity_grid": [[1.0, 1.0]],
    }
    generated = tmp_path / "generated.yml"
    generated.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    case = load_v2_file_case(generated)

    assert case.transport_mode == "effective_point_source"
    assert case.transport_calibration == "synthetic_geometry_test"
    assert len(case.request.exposure_edges.source) == 2
    assert np.all(case.request.exposure_edges.weight < 1.0)


def test_point_source_example_thermally_ignites_second_cell(tmp_path: Path) -> None:
    result, artifacts = run_v2_file_case(POINT_SOURCE_EXAMPLE, tmp_path / "point-source-run")

    assert result.final_state.ignition_time_s.tolist() == [[0.0, 2.0]]
    assert result.ignition_cause.tolist() == [[1, 2]]
    record = json.loads((artifacts.directory / "run.json").read_text(encoding="utf-8"))
    assert record["transport_mode"] == "effective_point_source"
    assert record["edge_count"] == 2


def test_v2_file_case_accepts_user_facing_minutes(tmp_path: Path) -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    del data["scenario"]["end_time_s"]
    del data["scenario"]["sample_interval_s"]
    data["scenario"]["end_time_min"] = 0.25
    data["scenario"]["sample_interval_min"] = 1.0 / 60.0
    path = tmp_path / "minutes.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    case = load_v2_file_case(path)

    assert case.request.scenario.end_time_s == pytest.approx(15.0)
    assert case.request.sample_interval_s == pytest.approx(1.0)
