"""Independent clock changes preserve fuel energy and reject unsupported peaks."""

from __future__ import annotations

import csv
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.component_curves_v3 import (
    ComponentBurnCurve,
    ComponentCurveConfiguration,
    load_component_curve_configuration,
)
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.physics_v2 import burn_interval_energy_and_hrr

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("curve_audit_fixture", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def config():
    return load_component_configuration(DIRECTORY / "components.synthetic.yml")


@pytest.fixture
def curves():
    return load_component_curve_configuration(DIRECTORY / "component-curves.synthetic.yml")


def test_independent_clocks_keep_other_parts_and_inputs_unchanged(config, curves):
    request = audit.make_request(("grass", "tree_equivalent", "building"))
    before = resolve_components(request, config)._node_request.parameters
    resolved = resolve_components(request, config, curves=curves)
    after = resolved._node_request.parameters
    assert np.array_equal(after.growth_time_s, [[20, 60, 120, 60, 120]])
    for field in after.__dataclass_fields__:
        if field not in {
            "growth_time_s",
            "steady_time_s",
            "decay_time_s",
            "peak_hrr_W",
            "peak_hrrpua_W_m2_active",
            "configured_curve_energy_J",
        }:
            assert np.array_equal(getattr(after, field), getattr(before, field)), field
    assert after.peak_hrr_W[0, 1] == pytest.approx(before.peak_hrr_W[0, 1] * 2)
    assert after.peak_hrr_W[0, 3] == pytest.approx(before.peak_hrr_W[0, 3] * 2)
    assert np.allclose(after.available_energy_J, after.configured_curve_energy_J)
    assert all(v < 1e-6 for v in resolved.conservation.values())
    assert "peak_hrr_W" not in resolved.conservation
    assert resolved.curve_diagnostics["physical_cell_peak_sum_max_abs_change_W"] == pytest.approx(
        94500
    )
    assert request.parameters.growth_time_s[0, 1] == 120
    default = resolve_components(request, config, curves=None)
    assert np.array_equal(default._node_request.parameters.peak_hrr_W, before.peak_hrr_W)
    assert "peak_hrr_W" in default.conservation


def test_independent_dense_trapezoid_and_full_interval_fuel_closure(config, curves):
    resolved = resolve_components(audit.make_request(("tree_equivalent",)), config, curves=curves)
    p = resolved._node_request.parameters
    g, s, d = (
        getattr(p, name)[0, 0] for name in ("growth_time_s", "steady_time_s", "decay_time_s")
    )
    # Piecewise intervals avoid integrating across corners on an arbitrary grid.
    t1 = np.linspace(0, g, 10001)
    t3 = np.linspace(0, d, 10001)
    integral = p.peak_hrr_W[0, 0] * (
        np.trapezoid((t1 / g) ** 2, t1) + s + np.trapezoid(1 - t3 / d, t3)
    )
    assert integral == pytest.approx(p.available_energy_J[0, 0], rel=1e-8)
    energy, mass, _ = burn_interval_energy_and_hrr(
        np.ones((1, 2), dtype=bool),
        np.zeros((1, 2)),
        p.pyrolysable_dry_fuel_mass_kg,
        p.peak_hrr_W,
        p.growth_time_s,
        p.steady_time_s,
        p.decay_time_s,
        p.heat_of_combustion_J_kg_dry,
        2000,
    )
    assert np.allclose(energy, p.available_energy_J)
    assert np.allclose(mass, p.pyrolysable_dry_fuel_mass_kg)


@pytest.mark.parametrize(
    "key,value",
    [
        ("growth_time_s", 0),
        ("growth_time_s", True),
        ("steady_time_s", -1),
        ("decay_time_s", float("nan")),
        ("decay_time_s", float("inf")),
        ("max_peak_hrrpua_W_m2_active", 0),
        ("max_peak_hrrpua_W_m2_active", "high"),
    ],
)
def test_invalid_curve_fields_fail(curves, key, value):
    with pytest.raises((ValueError, TypeError)):
        replace(curves.curves["tree_equivalent"]["surface_dead"], **{key: value})


def test_short_clock_fails_peak_ceiling_without_changing_input(config, curves):
    request = audit.make_request(("tree_equivalent",))
    fast = ComponentBurnCurve(2, 10, 18, 400000)
    invalid = replace(curves, curves={"tree_equivalent": {"surface_dead": fast}})
    with pytest.raises(ValueError, match="exceeds explicit peak PUA ceiling"):
        resolve_components(request, config, curves=invalid)
    assert request.parameters.peak_hrrpua_W_m2_active[0, 0] == 150000
    # Explicitly permitting the large peak demonstrates the fuel-duration coupling.
    permissive = replace(
        invalid,
        curves={
            "tree_equivalent": {"surface_dead": replace(fast, max_peak_hrrpua_W_m2_active=4e6)}
        },
    )
    p = resolve_components(request, config, curves=permissive)._node_request.parameters
    assert p.peak_hrrpua_W_m2_active[0, 0] > 2e6
    assert p.configured_curve_energy_J[0, 0] == pytest.approx(45.36e6)


def test_schema_unknown_ids_and_unused_landcovers_are_explicit(config, curves):
    assert (
        ComponentCurveConfiguration.from_mapping(curves.to_mapping()).to_mapping()
        == curves.to_mapping()
    )
    for patch in ({"schema_version": "old"}, {"evidence": ""}, {"provenance": " "}, {"curves": {}}):
        with pytest.raises(ValueError):
            ComponentCurveConfiguration.from_mapping({**curves.to_mapping(), **patch})
    bad = curves.to_mapping()
    bad["curves"]["building"]["combustible_base"]["peak_hrr_W"] = 123
    with pytest.raises(ValueError, match="exact timing"):
        ComponentCurveConfiguration.from_mapping(bad)
    typo = replace(
        curves,
        curves={"tree_equivalent": {"typo": curves.curves["tree_equivalent"]["surface_dead"]}},
    )
    with pytest.raises(ValueError, match="unknown curve component"):
        resolve_components(audit.make_request(("grass",)), config, curves=typo)
    resolved = resolve_components(audit.make_request(("grass",)), config, curves=curves)
    assert resolved.curve_diagnostics["tree_equivalent/surface_dead"]["applied_nodes"] == 0
    assert resolved.curve_diagnostics["tree_equivalent/surface_dead"]["peak_ratio_range"] is None


def test_whole_unsplit_clock_and_zero_steady_supported(config):
    curves = ComponentCurveConfiguration(
        "synthetic", "test:whole", {"grass": {"whole": ComponentBurnCurve(10, 0, 80, 1e6)}}
    )
    p = resolve_components(
        audit.make_request(("grass",)), config, curves=curves
    )._node_request.parameters
    assert p.steady_time_s[0, 0] == 0
    assert p.peak_hrr_W[0, 0] * (10 / 3 + 40) == pytest.approx(p.available_energy_J[0, 0])


def test_direct_source_burnout_obeys_its_own_clock_and_fuel(config, curves):
    resolved = resolve_components(
        audit.make_request(("tree_equivalent",), end_time_s=900), config, curves=curves
    )
    result = run_component_simulation(resolved)
    assert result.final_state.burnout_time_s[0, 0] == pytest.approx(430, abs=0.5)
    assert result.final_state.fuel_remaining_kg[0, 0] == pytest.approx(0, abs=1e-10)
    assert result.final_state.released_energy_J[0, 0] == pytest.approx(45.36e6)
    assert np.isnan(result.final_state.ignition_time_s[0, 1])


def test_replay_cli_records_resolved_clocks_and_rejects_unsafe_before_output(
    config, curves, tmp_path
):
    from pyfireca.cli import main

    request = audit.make_request(("grass", "building"), end_time_s=100)
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    resolved = resolve_components(request, config, contact=contact, curves=curves)
    result = run_component_simulation(resolved)
    output = write_component_run(resolved, result, tmp_path / "original")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["component_curves"] == curves.to_mapping()
    rows = list(csv.DictReader((output / "components.csv").open()))
    assert float(rows[1]["growth_time_s"]) == 60
    assert float(rows[1]["peak_hrrpua_W_m2_active"]) == pytest.approx(600000)
    args = [
        "village",
        "run-components-v3",
        str(output / "base-case"),
        "--components-config",
        str(output / "components.resolved.yml"),
        "--component-curves-config",
        str(output / "component-curves.resolved.yml"),
        "--output",
        str(tmp_path / "replay"),
    ]
    assert main(args) == 0
    with np.load(tmp_path / "replay/components.final.npz") as replay:
        for field in ("ignition_time_s", "fuel_remaining_kg", "released_energy_J"):
            assert np.array_equal(
                replay[field], getattr(result.final_state, field).ravel(), equal_nan=True
            )
    invalid = output / "component-curves.resolved.yml"
    invalid.write_text(invalid.read_text().replace("700000", "1"))
    args[-1] = str(tmp_path / "unsafe")
    assert main(args) == 2
    assert not (tmp_path / "unsafe").exists()
