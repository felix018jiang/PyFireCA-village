import copy
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from pyfireca.village.vegetation_config import (
    load_vegetation_case,
    write_vegetation_validation_report,
)
from pyfireca.village.vegetation_materials import load_vegetation_profiles

PROFILE_PATH = Path(__file__).parents[2] / "examples/village/vegetation_v3/profiles.synthetic.yml"
CASE_PATH = Path(__file__).parents[2] / "examples/village/vegetation_v3/gaoyou-v02.synthetic.yml"


def _yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _write_yaml(path, value):
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _prepared_fixture(tmp_path):
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
    quality = {"valid_for_v01_spatial_acceptance": True}
    (prepared / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (prepared / "quality_report.json").write_text(json.dumps(quality), encoding="utf-8")
    np.save(arrays / "domain_mask.npy", np.array([[True, True], [True, False]]))
    np.save(arrays / "known_mask.npy", np.array([[True, True], [False, False]]))
    np.save(arrays / "landcover_index.npy", np.array([[5, 1], [-1, -1]], dtype=np.int16))
    np.save(arrays / "evidence_index.npy", np.array([[2, 1], [0, 0]], dtype=np.uint8))
    np.save(
        arrays / "landcover_coverage_fraction.npy",
        np.array([[0.5, 1.0], [0.0, 0.0]], dtype=np.float32),
    )
    return prepared


def _case_fixture(tmp_path, *, ignition=(1.0, 3.0)):
    case = _yaml(CASE_PATH)
    case["prepared"] = str(_prepared_fixture(tmp_path))
    case["profiles"] = str(PROFILE_PATH)
    case["scenario"]["ignitions"][0]["world_xy_m"] = list(ignition)
    path = tmp_path / "case.yml"
    _write_yaml(path, case)
    return path


def test_synthetic_profile_library_has_independent_balanced_profiles():
    library = load_vegetation_profiles(PROFILE_PATH)

    assert len(library.profiles) == 6
    assert not library.production_ready
    for profile in library.profiles:
        if not profile.burnable:
            continue
        assert profile.value("live_dry_mass_fraction") + profile.value(
            "dead_dry_mass_fraction"
        ) == pytest.approx(1.0)
        assert profile.budget.utilization_fraction == pytest.approx(1.0)
        assert profile.weighted_moisture_content_dry_basis >= 0.0


def test_vt06_rejects_curve_that_exceeds_fuel_energy(tmp_path):
    data = _yaml(PROFILE_PATH)
    grass = next(profile for profile in data["profiles"] if profile["id"] == "synthetic_grass")
    grass["parameters"]["fuel_load_kg_m2"]["value"] = 0.01
    path = tmp_path / "over_budget.yml"
    _write_yaml(path, data)

    with pytest.raises(ValueError, match=r"energy budget exceeded.*synthetic_grass"):
        load_vegetation_profiles(path)


def test_vt13_missing_moisture_names_profile_and_parameter(tmp_path):
    data = _yaml(PROFILE_PATH)
    shrub = next(profile for profile in data["profiles"] if profile["id"] == "synthetic_shrub")
    del shrub["parameters"]["dead_moisture_content_dry_basis"]
    path = tmp_path / "missing.yml"
    _write_yaml(path, data)

    with pytest.raises(
        ValueError,
        match=r"profile 'synthetic_shrub'.parameters is missing keys:.*dead_moisture",
    ):
        load_vegetation_profiles(path)


def test_case_applies_coverage_once_and_writes_preflight(tmp_path):
    case = load_vegetation_case(_case_fixture(tmp_path))

    assert case.ignitions[0].profile_id == "synthetic_building"
    building = next(
        budget for budget in case.spatial_budgets if budget.profile_id == "synthetic_building"
    )
    grass = next(
        budget for budget in case.spatial_budgets if budget.profile_id == "synthetic_grass"
    )
    assert building.covered_area_m2 == pytest.approx(2.0)
    assert building.initial_dry_fuel_kg == pytest.approx(36.0)
    assert grass.covered_area_m2 == pytest.approx(4.0)
    assert grass.initial_dry_fuel_kg == pytest.approx(2.4)

    report_dir = write_vegetation_validation_report(case, tmp_path / "report")
    report = json.loads((report_dir / "budget_report.json").read_text())
    assert report["valid"] is True
    assert report["simulation_executed"] is False
    assert report["unknown_domain_cell_count"] == 1
    with pytest.raises(FileExistsError):
        write_vegetation_validation_report(case, report_dir)


def test_vt13_rejects_direct_ignition_on_unknown_cell(tmp_path):
    case_path = _case_fixture(tmp_path, ignition=(1.0, 1.0))

    with pytest.raises(ValueError, match=r"targets unknown cell \(1, 0\)"):
        load_vegetation_case(case_path)


def test_profile_missing_key_does_not_mutate_source_fixture(tmp_path):
    original = _yaml(PROFILE_PATH)
    changed = copy.deepcopy(original)
    changed["profiles"][1]["parameters"].pop("specific_heat_J_kg_K")
    path = tmp_path / "changed.yml"
    _write_yaml(path, changed)

    with pytest.raises(ValueError, match="specific_heat_J_kg_K"):
        load_vegetation_profiles(path)
    assert _yaml(PROFILE_PATH) == original
