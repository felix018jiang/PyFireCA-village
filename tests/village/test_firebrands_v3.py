"""Nonlocal crossing, conservation, deterministic event clocks and provenance."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.firebrands_v3 import (
    FirebrandParameters,
    FirebrandReceiver,
    FirebrandSource,
    draw,
    first_intersection,
)
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)


def request():
    path = Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py"
    spec = importlib.util.spec_from_file_location("brand_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    r = module.make_request(
        ("tree_equivalent", "bare_or_road", "bare_or_road", *("building",) * 7), end_time_s=120
    )
    empty = ExposureEdges(
        np.array([], dtype=np.int32),
        np.array([], dtype=np.int32),
        np.array([], dtype=float),
        r.grid.height * r.grid.width,
    )
    r = replace(
        r,
        exposure_edges=empty,
        sample_interval_s=10,
        scenario=replace(r.scenario, end_time_s=120.0, ambient_temperature_K=300.0),
    )
    source = int(r.material_index[0, 0])
    target = int(r.material_index[0, 3])
    config = FirebrandParameters(
        17,
        3.0,
        270.0,
        1.2,
        10.0,
        (FirebrandSource(source, "flaming", 0.005, 0.0002, 0.002, 1.2, 3.0, 0.0, 1e-7, 100.0),),
        (
            FirebrandReceiver(
                target,
                "flaming",
                "linear_mass_v1",
                0.002,
                1e-9,
                0,
                0,
                0,
                0,
                0,
                10,
                2.0,
                "synthetic exposed timber contact patch",
            ),
        ),
        10000,
        20000000,
        "synthetic",
        "unit test: certain ignition for mechanism isolation",
    )
    return r, config


def test_flat_flight_and_ridge_first_intersection():
    grid = VillageGridSpec(1, 50, 1, 0, 1)
    t, kind, target = first_intersection(grid, None, 0.5, 0.5, 10, 2, 0, 1, 100)
    assert t == 10 and kind == "landed" and target == (0, 20)
    ridge = np.zeros(grid.shape)
    ridge[0, 5] = 20
    t, kind, target = first_intersection(grid, ridge, 0.5, 0.5, 10, 1, 0, 1, 100)
    assert t == 4.5 and target == (0, 5)


def test_exiting_and_lifetime_are_not_clamped_or_recycled():
    grid = VillageGridSpec(1, 10, 1, 0, 1)
    assert first_intersection(grid, None, 0.5, 0.5, 10, -2, 0, 1, 100) == (
        0.25,
        "exited_domain",
        None,
    )
    assert first_intersection(grid, None, 0.5, 0.5, 10, 0, 0, 1, 0.1) == (0.1, "extinguished", None)
    mask = np.ones(grid.shape, dtype=bool)
    mask[0, 2] = False
    assert first_intersection(grid, None, 0.5, 0.5, 10, 1, 0, 1, 100, mask) == (
        1.5,
        "exited_domain",
        None,
    )


def test_real_solver_crosses_inert_gap_and_preserves_mass_energy_and_parent():
    r, c = request()
    before = request_fingerprint(r)
    baseline = run_v3_simulation(r, retain_snapshots=False)
    assert np.isfinite(baseline.final_state.ignition_time_s).sum() == 1
    enabled = replace(r, firebrands=c)
    result = run_v3_simulation(enabled, retain_snapshots=False)
    assert (result.ignition_cause[0, 3:] == 3).any()
    assert not np.isfinite(result.final_state.ignition_time_s[0, 1:3]).any()
    assert result.firebrands["counts"]["ignited"] > 0
    assert result.firebrands["final_budget"]["mass_closure_error_kg"] < 1e-9
    assert result.firebrands["final_budget"]["chemical_closure_error_J"] < 1e-4
    assert result.firebrands["final_budget"]["transferred_mass_kg"] > 0
    check_final(enabled, result)
    assert request_fingerprint(r) == before
    assert result.ignition_cause[0, 0] == 1
    assert np.all(result.final_state.surface_temperature_K[result.ignition_cause == 3] == 300)
    assert not result.final_state.temperature_valid_mask[result.ignition_cause == 3].any()


def test_stable_random_numbers_and_release_clock_across_time_steps():
    r, c = request()
    c = replace(c, sources=(replace(c.sources[0], height_relative_half_range=0.4),))
    left = run_v3_simulation(replace(r, firebrands=c), retain_snapshots=False)
    right = run_v3_simulation(
        replace(r, firebrands=c, scenario=replace(r.scenario, max_dt_s=0.25)),
        retain_snapshots=False,
    )
    a = [e for e in left.firebrands["events"] if e["kind"] == "released"]
    b = [e for e in right.firebrands["events"] if e["kind"] == "released"]
    assert a == b
    assert all(e["time_s"] % 10 == 0 for e in a)
    assert draw(1, "cell:7", "ignite") == draw(1, "cell:7", "ignite")
    assert draw(1, "cell:7", "ignite") != draw(2, "cell:7", "ignite")
    again = run_v3_simulation(replace(r, firebrands=c), retain_snapshots=False)
    assert again.firebrands == left.firebrands


def test_zero_release_has_no_spot_ignitions_and_horizon_keeps_inflight_particles():
    r, c = request()
    zero = replace(c, sources=(replace(c.sources[0], release_mass_fraction=0),))
    result = run_v3_simulation(replace(r, firebrands=zero), retain_snapshots=False)
    assert result.firebrands["counts"]["released"] == 0
    assert not (result.ignition_cause == 3).any()
    high = replace(c, wind_speed_m_s=0, sources=(replace(c.sources[0], release_height_m=1000),))
    result = run_v3_simulation(
        replace(r, firebrands=high, scenario=replace(r.scenario, end_time_s=90.0)),
        retain_snapshots=False,
    )
    assert result.firebrands["final_budget"]["in_flight_mass_kg"] > 0
    assert result.firebrands["pending_events"]
    check_final(replace(r, firebrands=high), result)


def test_resource_exhaustion_raises_instead_of_certifying_a_truncated_process():
    r, c = request()
    with pytest.raises(RuntimeError, match="limit exceeded"):
        run_v3_simulation(
            replace(r, firebrands=replace(c, max_active_particles=1)), retain_snapshots=False
        )


def test_enabled_case_round_trip_and_viewer_make_mechanism_explicit(tmp_path):
    r, c = request()
    r = replace(r, firebrands=c)
    write_v3_case_bundle(r, tmp_path / "case")
    loaded = load_v3_case_bundle(tmp_path / "case")
    assert request_fingerprint(loaded.request) == request_fingerprint(r)
    assert request_fingerprint(replace(r, firebrands=replace(c, seed=18))) != request_fingerprint(r)
    result, out = run_v3_case_bundle(tmp_path / "case", tmp_path / "run", chunk_frames=4)
    assert (
        json.loads((out / "viewer/manifest.json").read_text())["schema_version"]
        == "village-viewer-4"
    )
    assert (
        json.loads((out / "viewer/manifest.json").read_text())["ignition_cause"]["labels"]["3"]
        == "飞火起燃"
    )
    assert (out / "firebrand_events.jsonl").exists() and (out / "firebrand_budget.csv").exists()
    check_final(loaded.request, result)
    manifest = json.loads((tmp_path / "case/manifest.json").read_text())
    manifest["schema_version"] = "village-v3-case-1"
    (tmp_path / "case/manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="silently"):
        load_v3_case_bundle(tmp_path / "case")


@pytest.mark.parametrize(
    "change",
    [
        {"seed": True},
        {"seed": -1},
        {"wind_speed_m_s": -1},
        {"parameter_evidence": "calibrated"},
        {"max_active_particles": 0},
    ],
)
def test_bad_configuration_rejected(change):
    _, c = request()
    with pytest.raises(ValueError):
        replace(c, **change)


def test_glowing_model_requires_its_own_ignition_law_and_delay():
    _, c = request()
    r = c.receivers[0]
    with pytest.raises(ValueError):
        replace(r, particle_state="glowing")
    with pytest.raises(ValueError):
        replace(r, particle_state="glowing", model="logistic_v1", development_delay_s=0)
    valid = replace(
        r, particle_state="glowing", model="logistic_v1", intercept=0, development_delay_s=30
    )
    assert valid.probability(0.001, 0, 3) == 0.5
    with pytest.raises(ValueError):
        valid.probability(0.001, 11, 3)
