import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from pyfireca.village.physics_v2 import burn_interval_energy_and_hrr
from pyfireca.village.physics_v3 import (
    V3CellPhysicsArrays,
    moisture_energy_demand_J,
    resolve_vegetation_physics,
    write_v3_physics_package,
)
from pyfireca.village.vegetation_config import load_vegetation_case

ROOT = Path(__file__).parents[2]
PROFILE_PATH = ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml"
CASE_PATH = ROOT / "examples/village/vegetation_v3/gaoyou-v02.synthetic.yml"


def _fixture_case(tmp_path):
    prepared = tmp_path / "prepared"
    arrays = prepared / "arrays"
    arrays.mkdir(parents=True)
    manifest = {
        "schema_version": "prepared-village-vegetation-spatial-1",
        "grid": {
            "height": 2,
            "width": 2,
            "cell_size_m": 2.0,
            "x_min_m": 0.0,
            "y_max_m": 4.0,
            "coordinate_mode": "projected",
            "crs": "EPSG:32649",
        },
    }
    (prepared / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (prepared / "quality_report.json").write_text(
        json.dumps({"valid_for_v01_spatial_acceptance": True}), encoding="utf-8"
    )
    np.save(arrays / "domain_mask.npy", np.array([[True, True], [True, False]]))
    np.save(arrays / "known_mask.npy", np.array([[True, True], [False, False]]))
    np.save(arrays / "landcover_index.npy", np.array([[5, 1], [-1, -1]], dtype=np.int16))
    np.save(arrays / "evidence_index.npy", np.array([[2, 1], [0, 0]], dtype=np.uint8))
    np.save(
        arrays / "landcover_coverage_fraction.npy",
        np.array([[0.5, 1.0], [0.0, 0.0]], dtype=np.float32),
    )
    case = yaml.safe_load(CASE_PATH.read_text(encoding="utf-8"))
    case["prepared"] = str(prepared)
    case["profiles"] = str(PROFILE_PATH)
    case["scenario"]["ignitions"][0]["world_xy_m"] = [1.0, 3.0]
    case_path = tmp_path / "case.yml"
    case_path.write_text(yaml.safe_dump(case, allow_unicode=True, sort_keys=False))
    return load_vegetation_case(case_path)


def test_vt03_coverage_is_applied_once_to_mass_and_area(tmp_path):
    physics = resolve_vegetation_physics(_fixture_case(tmp_path))
    arrays = physics.arrays

    assert arrays.covered_area_m2[0, 0] == pytest.approx(2.0)
    assert arrays.initial_dry_fuel_mass_kg[0, 0] == pytest.approx(36.0)
    assert arrays.pyrolysable_dry_fuel_mass_kg[0, 0] == pytest.approx(21.6)
    assert arrays.covered_area_m2[0, 1] == pytest.approx(4.0)
    assert arrays.initial_dry_fuel_mass_kg[0, 1] == pytest.approx(2.4)
    assert np.all(arrays.covered_area_m2 <= 4.0)
    assert np.all(arrays.configured_curve_energy_J <= arrays.available_energy_J + 1e-6)


def test_vt05_wetter_fuel_requires_more_water_energy():
    dry_energy = moisture_energy_demand_J(1.0, 300.0)
    wet_energy = moisture_energy_demand_J(2.0, 300.0)

    assert wet_energy > dry_energy
    assert moisture_energy_demand_J(0.0, 300.0) == 0.0


def test_vt07_single_cell_burn_integral_matches_fuel_energy():
    fuel_mass = 1.25
    heat = 18_000_000.0
    growth, steady, decay = 20.0, 40.0, 80.0
    peak = fuel_mass * heat / (growth / 3.0 + steady + decay / 2.0)
    released, consumed, _ = burn_interval_energy_and_hrr(
        np.array([True]),
        np.array([0.0]),
        np.array([fuel_mass]),
        np.array([peak]),
        np.array([growth]),
        np.array([steady]),
        np.array([decay]),
        np.array([heat]),
        growth + steady + decay,
    )

    assert released[0] == pytest.approx(fuel_mass * heat, rel=1e-12)
    assert consumed[0] == pytest.approx(fuel_mass, rel=1e-12)


def test_v3_package_has_string_provenance_and_no_simulation(tmp_path):
    case = _fixture_case(tmp_path)
    physics = resolve_vegetation_physics(case)
    output = write_v3_physics_package(case, physics, tmp_path / "physics")
    report = json.loads((output / "physics_report.json").read_text())

    assert report["simulation_executed"] is False
    assert report["mass_totals_kg"]["initial_dry_fuel_mass_kg"] == pytest.approx(38.4)
    assert report["energy_totals_J"]["closure_error"] == pytest.approx(0.0, abs=1e-6)
    profile_ids = np.load(output / "arrays/profile_id.npy", allow_pickle=False)
    assert profile_ids.dtype.kind == "U"
    with pytest.raises(FileExistsError):
        write_v3_physics_package(case, physics, output)


def test_v3_validation_rejects_inconsistent_conservation_arrays(tmp_path):
    case = _fixture_case(tmp_path)
    physics = resolve_vegetation_physics(case)
    values = {
        name: getattr(physics.arrays, name).copy()
        for name in physics.arrays.__dataclass_fields__
    }
    values["water_mass_kg"][0, 0] += 1.0
    with pytest.raises(ValueError, match="water_mass_kg"):
        V3CellPhysicsArrays(**values).validated(case.grid.shape, case.grid.cell_area_m2)
