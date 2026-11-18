"""Checks reject broken physical budgets and corrupted replay, not just happy paths."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).parents[2]


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture():
    core = module(ROOT / "examples/village/vegetation_v3/audit_core.py")
    r = core.make_core_request("grass_chain")
    return r, run_v3_simulation(r)


def test_final_budgets_and_step_comparison():
    r, x = fixture()
    assert check_final(r, x)["burned_out_cells"] == 4
    assert compare_steps(x, x)["engineering_tolerance_pass"]
    fine = run_v3_simulation(replace(r, scenario=replace(r.scenario, max_dt_s=0.25)))
    assert compare_steps(x, fine)["engineering_tolerance_pass"]


@pytest.mark.parametrize(
    "fault",
    ["negative_fuel", "reviving_fuel", "late_heat", "water_gain", "below_ambient", "budget"],
)
def test_invalid_physical_state_is_rejected(fault):
    r, x = fixture()
    state = x.final_state.copy()
    if fault == "negative_fuel":
        state.fuel_remaining_kg[0, 0] = -1
    if fault == "reviving_fuel":
        state.fuel_remaining_kg[0, 0] = 1
    if fault == "late_heat":
        state.interval_mean_hrr_W[0, 0] = 1
    if fault == "water_gain":
        state.heated_water_remaining_kg[0, 0] = 1e6
    if fault == "below_ambient":
        state.temperature_valid_mask[0, 0] = True
        state.surface_temperature_K[0, 0] = 200
    if fault == "budget":
        x = replace(
            x, energy_budget=replace(x.energy_budget, released_J=x.energy_budget.released_J + 1e6)
        )
    with pytest.raises(AssertionError):
        check_final(r, replace(x, final_state=state))


def test_step_comparison_rejects_small_set_outcome_disagreement():
    _r, x = fixture()
    other = x.final_state.copy()
    other.ignition_time_s[0, -1] = np.nan
    assert not compare_steps(x, replace(x, final_state=other))["engineering_tolerance_pass"]


def test_replay_parity_and_corrupted_chunk_rejection(tmp_path):
    audit = module(ROOT / "scripts/validate_gaoyou_core.py")
    core = module(ROOT / "examples/village/vegetation_v3/audit_core.py")
    r = core.make_core_request("grass_chain")
    r = replace(r, scenario=replace(r.scenario, end_time_s=10), sample_interval_s=2)
    case = write_v3_case_bundle(r, tmp_path / "case")
    x, run = run_v3_case_bundle(case, tmp_path / "run", chunk_frames=4)
    assert audit.check_replay(run, r, x)["frames_checked"] == 6
    manifest = json.loads((run / "viewer/manifest.json").read_text())
    path = run / "viewer" / manifest["fuel"]["chunks"][0]["path"]
    fuel = np.fromfile(path, dtype="<f4")
    fuel[0] = -1
    path.write_bytes(fuel.tobytes())
    with pytest.raises(AssertionError, match="fuel fraction"):
        audit.check_replay(run, r, x)
