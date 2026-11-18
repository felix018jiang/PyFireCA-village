"""Verify experimental gate continuity and reuse of the existing source budget."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


experiment = load_module("boundary_experiment", ROOT / "scripts/contact_boundary_experiment.py")
fixture = load_module("spread_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py")


def make_request(labels):
    contact = load_flame_contact_parameters(
        ROOT / "examples/village/vegetation_v3/flame-contact.synthetic.yml"
    )
    return replace(fixture.make_request(labels), flame_contact=contact)


def test_smooth_gate_has_exact_plateaus_and_symmetric_transitions():
    relative = np.array([-0.02, -0.01, 0, 0.01, 0.2, 0.34, 0.35, 0.36, 0.37])
    weights = experiment.smooth_point_weight(relative, 0.35, 0.01)
    np.testing.assert_allclose(weights, [0, 0, 0.5, 1, 1, 1, 0.5, 0, 0], atol=1e-13)


def test_small_geometry_perturbations_obey_analytic_lipschitz_bound():
    relative = np.linspace(-0.02, 0.37, 10001)
    step, width = 1e-4, 0.01
    a = experiment.smooth_point_weight(relative, 0.35, width)
    b = experiment.smooth_point_weight(relative + step, 0.35, width)
    # Smoothstep slope <= 3/(4*width); signed margin is 1-Lipschitz.
    assert np.max(np.abs(a - b)) <= 3 * step / (4 * width) + 1e-12
    assert np.all((a >= 0) & (a <= 1))


@pytest.mark.parametrize("width", [0, -1, float("nan"), float("inf"), True])
def test_invalid_transition_rejected(width):
    with pytest.raises((TypeError, ValueError)):
        experiment.smooth_point_weight(0, 0.35, width)


def test_road_and_high_receiver_are_still_excluded_and_inputs_unchanged():
    request = make_request(("grass", "bare_or_road", "grass"))
    graph = experiment.build_smoothed_contact(request, 0.01)
    assert np.all(graph.neighbors == -1)
    high = make_request(("grass", "tree_equivalent"))
    original = high.parameters.receiver_height_m.copy()
    assert experiment.build_smoothed_contact(high, 0.01).neighbors[0, 3] == -1
    np.testing.assert_array_equal(original, high.parameters.receiver_height_m)
    assert high.flame_contact.receiver_vertical_half_extent_m == 0


def test_transfer_remains_bounded_by_source_energy_and_target_area():
    request = make_request(("grass", "grass", "grass"))
    request = replace(
        request, flame_contact=replace(request.flame_contact, heat_transfer_coefficient_W_m2_K=1e6)
    )
    graph = experiment.build_smoothed_contact(request, 0.01)
    hrr = np.zeros(request.grid.shape)
    hrr[0, 1] = request.parameters.peak_hrr_W[0, 1]
    susceptible = np.array([[True, False, True]])
    result = graph.transfer(hrr, np.full(request.grid.shape, 300.0), susceptible)
    assert result.transferred_W == pytest.approx(result.available_nonradiative_W)
    assert result.energy_limited_sources == 1
    assert np.all((result.target_contact_fraction >= 0) & (result.target_contact_fraction <= 1))
    zero = graph.transfer(np.zeros_like(hrr), np.full(request.grid.shape, 300.0), susceptible)
    assert zero.transferred_W == 0


def test_receiver_band_and_numerical_transition_cannot_be_combined():
    request = make_request(("grass", "grass"))
    request = replace(
        request, flame_contact=replace(request.flame_contact, receiver_vertical_half_extent_m=0.15)
    )
    with pytest.raises(ValueError, match="must not combine"):
        experiment.build_smoothed_contact(request, 0.01)
