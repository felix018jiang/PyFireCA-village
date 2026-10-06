"""Time/energy contract checks with independent analytical integrals."""

import numpy as np
import pytest

from pyfireca.village.flame_geometry_v3 import instantaneous_prescribed_hrr
from pyfireca.village.radiation_time_v3 import integrate_dynamic_radiation


def solve(evaluate, **updates):
    return integrate_dynamic_radiation(
        **{
            **dict(
                start_s=0,
                end_s=10,
                node_count=2,
                edge_sources=[0],
                edge_targets=[1],
                evaluate=evaluate,
            ),
            **updates,
        }
    )


def test_integrates_intensity_times_geometry_not_mean_times_end_geometry():
    result = solve(lambda t: ([t * t, 0], [t / 20]))
    assert result.source_energy_J[0] == pytest.approx(1000 / 3)
    assert result.edge_energy_J[0] == pytest.approx(125)  # integral t^3/20
    assert result.unassigned_source_energy_J[0] == pytest.approx(1000 / 3 - 125)
    naive = (1000 / 3) * 0.5
    assert naive > result.edge_energy_J[0] * 1.3
    assert result.quadrature_orders == (6,)
    assert result.callback_evaluations == 9
    assert result.closure_error_J < 1e-10
    with pytest.raises(ValueError):
        result.edge_energy_J[0] = 1


def test_static_fraction_matches_integrated_prescribed_curve():
    def evaluate(t):
        q = instantaneous_prescribed_hrr(t, [0], [13], [120], [3], [4], [6])[0]
        return [q, 0], [0.2]

    result = solve(evaluate, end_s=13, break_times_s=[3, 7])
    energy = 120 * (3 / 3 + 4 + 6 / 2)
    assert result.source_energy_J[0] == pytest.approx(energy)
    assert result.target_energy_J[1] == pytest.approx(0.2 * energy)
    assert result.unassigned_source_energy_J[0] == pytest.approx(0.8 * energy)
    assert result.quadrature_orders == (6, 6, 6)


def test_split_at_known_discontinuity_and_whole_vs_two_steps():
    def callback(t):
        return [10, 0], [0.2 if t < 5 else 0.8]

    whole = solve(callback, break_times_s=[5])
    left = solve(callback, end_s=5)
    right = solve(callback, start_s=5)
    np.testing.assert_allclose(whole.edge_energy_J, left.edge_energy_J + right.edge_energy_J)
    assert whole.edge_energy_J[0] == pytest.approx(50)


def test_multiple_sources_edges_receivers_and_unassigned_budget():
    result = solve(
        lambda t: ([10, 20, 0], [0.25, 0.5, 0.8]),
        node_count=3,
        edge_sources=[0, 0, 1],
        edge_targets=[1, 2, 2],
    )
    np.testing.assert_allclose(result.edge_energy_J, [25, 50, 160])
    np.testing.assert_allclose(result.target_energy_J, [0, 25, 210])
    np.testing.assert_allclose(result.source_energy_J, [100, 200, 0])
    np.testing.assert_allclose(result.unassigned_source_energy_J, [25, 40, 0])


def test_no_edges_preserves_all_source_energy():
    result = solve(
        lambda t: ([10, 0], []),
        edge_sources=np.array([], dtype=int),
        edge_targets=np.array([], dtype=int),
    )
    assert result.edge_energy_J.size == 0
    np.testing.assert_allclose(result.source_energy_J, result.unassigned_source_energy_J)
    assert result.source_energy_J[0] == pytest.approx(100)


def test_zero_source_energy_and_extinguished_interval():
    result = solve(lambda t: ([0, 0], [0.5]))
    assert not result.source_energy_J.any()
    assert not result.edge_energy_J.any()


@pytest.mark.parametrize(
    "power,fractions",
    [
        ([-1, 0], [0.5]),
        ([np.nan, 0], [0.5]),
        ([True, False], [0.5]),
        ([1], [0.5]),
        ([1, 0], [-0.1]),
        ([1, 0], [np.nan]),
        ([1, 0], [True]),
        ([1, 0], [1.001]),
        ([1, 0], []),
    ],
)
def test_bad_callback_values_fail(power, fractions):
    with pytest.raises(ValueError):
        solve(lambda t: (power, fractions))


def test_source_budget_violation_is_not_normalized_away():
    with pytest.raises(ValueError, match="RADIATION_SOURCE_BUDGET"):
        solve(
            lambda t: ([1, 0, 0], [0.6, 0.6]),
            node_count=3,
            edge_sources=[0, 0],
            edge_targets=[1, 2],
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"start_s": 10},
        {"end_s": np.inf},
        {"node_count": True},
        {"edge_sources": [0.0]},
        {"edge_sources": [-1]},
        {"edge_sources": [2]},
        {"edge_targets": [0]},
        {"break_times_s": [0]},
        {"break_times_s": [10]},
        {"break_times_s": [5, 5]},
        {"break_times_s": [6, 5]},
        {"initial_order": 3, "max_order": 3},
        {"relative_tolerance": -1},
        {"relative_tolerance": 0, "absolute_tolerance_J": 0},
    ],
)
def test_invalid_integrator_contract(updates):
    with pytest.raises(ValueError):
        solve(lambda t: ([10, 0], [0.5]), **updates)


def test_resource_rejection_precedes_callback():
    def forbidden(t):
        pytest.fail("callback must not run")

    for updates in (
        {"max_evaluations": 1},
        {"max_nodes": 1},
        {"initial_order": 1000000, "max_order": 2000000},
    ):
        with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
            solve(forbidden, **updates)


def test_duplicate_edges_rejects():
    with pytest.raises(ValueError, match="unique"):
        solve(lambda t: ([10, 0], [0.2, 0.2]), edge_sources=[0, 0], edge_targets=[1, 1])


def test_insufficient_orders_reject_unconverged_geometry():
    with pytest.raises(ValueError, match="TIME_QUADRATURE_NOT_CONVERGED"):
        solve(
            lambda t: ([10, 0], [0.5 if t < 2.345 else 0.9]),
            initial_order=3,
            max_order=6,
            relative_tolerance=1e-12,
            absolute_tolerance_J=1e-12,
            max_subdivisions=0,
        )


def test_time_grid_and_energy_range_fail_clearly():
    with pytest.raises(ValueError, match="resolution"):
        solve(lambda t: ([1, 0], [0.5]), start_s=1e16, end_s=1e16 + 2)
    with pytest.raises(ValueError, match="overflows"):
        solve(lambda t: ([1e308, 0], [0.5]), end_s=100)


def test_local_subdivision_resolves_nonsmooth_fraction_with_global_error_budget():
    location = 2.3
    result = solve(
        lambda t: ([10, 0], [0.1 + 0.1 * np.sqrt(abs(t - location))]),
        break_times_s=[location],
        relative_tolerance=1e-5,
        absolute_tolerance_J=1e-8,
        max_subdivisions=12,
    )
    exact = 10 + 2 / 3 * (location**1.5 + (10 - location) ** 1.5)
    assert result.edge_energy_J[0] == pytest.approx(exact, rel=1e-5)
    assert len(result.time_panels_s) > 2
    assert result.attempted_panels > len(result.time_panels_s)
    assert result.time_panels_s[0][0] == 0
    assert result.time_panels_s[-1][1] == 10
    assert all(
        a[1] == b[0]
        for a, b in zip(result.time_panels_s[:-1], result.time_panels_s[1:], strict=True)
    )
    assert result.estimated_edge_error_J[0] <= 1e-8 + 1e-5 * result.edge_energy_J[0]


def test_adaptive_limits_are_enforced_after_initial_panel():
    with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
        solve(
            lambda t: ([10, 0], [0.5 if t < 2.345 else 0.9]),
            initial_order=3,
            max_order=6,
            relative_tolerance=1e-12,
            absolute_tolerance_J=1e-12,
            max_panels=1,
        )
    with pytest.raises(ValueError):
        solve(lambda t: ([10, 0], [0.5]), max_subdivisions=-1)
