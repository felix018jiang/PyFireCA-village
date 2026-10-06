"""Independent hand calculations and adversarial P01 geometry regressions."""

from math import inf, sqrt

import numpy as np
import pytest

pytest.importorskip("shapely", minversion="2")

from pyfireca.arrival import ConstantDirectionalSpreadRate, StaticArrivalTimeSolver
from pyfireca.neighborhood import MooreNeighborhood, VonNeumannNeighborhood
from pyfireca.planning.adapters import GeometrySpreadRate, evaluate, evaluate_pair
from pyfireca.planning.contracts import (
    Evidence,
    Ignition,
    Plan,
    PlanningGrid,
    Scenario,
    Target,
    Treatment,
    TreatmentProfile,
)
from pyfireca.planning.geometry import prepare_plan

EVIDENCE = Evidence("synthetic", "hand-calculated P01 fixture", "constant-rate math only")


def profile(mode="M1", rate=0.25, name="treated", cost=2.0):
    return TreatmentProfile(
        name, mode, EVIDENCE, cost, ConstantDirectionalSpreadRate(rate) if mode == "M1" else None
    )


def line(width=2.0, start=(55.0, 0.0), end=(55.0, 10.0), name="line", pid="treated"):
    return Treatment(name, start, end, width, pid)


def inputs(shape=(1, 11), horizon=105.0):
    grid = PlanningGrid(shape, 10.0, y_max_m=shape[0] * 10.0)
    y = grid.centre(shape[0] // 2, 0)[1]
    return {
        "grid": grid,
        "domain_mask": np.ones(shape, dtype=bool),
        "background": ConstantDirectionalSpreadRate(1.0),
        "scenario": Scenario(
            "fixed", (Ignition((5.0, y)),), (Target("asset", (105.0, y)),), horizon
        ),
    }


@pytest.mark.parametrize("width", [0.02, 2.0, 4.0, 8.0, 20.0])
def test_subcell_treated_crossing_matches_independent_hand_integral(width):
    args = inputs(horizon=200)
    result = evaluate(**args, plan=Plan("strip", (line(width),)), profiles=(profile(),))
    # Total travel is 100 m; w m uses independent ROS=0.25 instead of 1 m/s.
    assert result.target_arrivals_s == pytest.approx((100.0 - width + width / 0.25,))
    assert result.treated_area_m2 == pytest.approx(width * 10)
    assert result.cost_units == pytest.approx(width * 10 * 2)


@pytest.mark.parametrize("width", [0.02, 2.0, 12.0])
def test_ideal_full_strip_cannot_be_crossed_even_when_narrower_than_a_cell(width):
    args = inputs(shape=(9, 11))
    result = evaluate(
        **args,
        plan=Plan("barrier", (line(width, end=(55.0, 90.0)),)),
        profiles=(profile("M0"),),
    )
    assert result.target_arrivals_s == (inf,)
    assert result.exposed_weight == 0
    assert result.unreachable_burnable_cell_count > 0


def test_finite_line_is_bypassed_and_two_touching_half_lines_cut_the_domain():
    args = inputs(shape=(9, 11), horizon=500)
    top = line(start=(55.0, 45.0), end=(55.0, 90.0), name="top")
    bottom = line(end=(55.0, 45.0), name="bottom")
    profiles = (profile("M0"),)
    for treatment in (top, bottom):
        result = evaluate(**args, plan=Plan("half", (treatment,)), profiles=profiles)
        assert 100 < result.target_arrivals_s[0] < inf
        assert result.exposed_weight == 1
    joined = evaluate(**args, plan=Plan("joined", (top, bottom)), profiles=profiles)
    assert joined.target_arrivals_s == (inf,)
    assert joined.treated_area_m2 == pytest.approx(180)


def test_pair_keeps_target_denominator_and_distinguishes_delay_from_unreachability():
    args = inputs()
    original = args["domain_mask"].copy()
    paired = evaluate_pair(**args, plan=Plan("strip", (line(),)), profiles=(profile(),))
    assert paired.baseline.target_arrivals_s == pytest.approx((100,))
    assert paired.intervention.target_arrivals_s == pytest.approx((106,))
    assert paired.exposure_reduction == 1
    assert paired.horizon_capped_target_delay_s == pytest.approx((5,))
    assert paired.intervention.unreachable_burnable_cell_count == 0
    assert paired.intervention.boundary_contact_at_horizon
    np.testing.assert_array_equal(args["domain_mask"], original)
    assert not paired.intervention.arrival_times_s.flags.writeable
    later = evaluate(**inputs(horizon=106), plan=Plan("strip", (line(),)), profiles=(profile(),))
    assert later.exposed_weight == 1


def test_no_intervention_matches_existing_solver_exactly_on_full_domain():
    args = inputs(shape=(9, 11))
    grid = args["grid"]
    ignition = np.full(grid.shape, inf)
    ignition[grid.cell_at(args["scenario"].ignitions[0].point_m)] = 0
    expected = StaticArrivalTimeSolver(
        MooreNeighborhood(), grid.cell_size_m, args["background"]
    ).solve(args["domain_mask"], ignition)
    result = evaluate(**args, plan=Plan("empty"))
    np.testing.assert_array_equal(result.arrival_times_s, expected)


def test_nonburnable_corner_cannot_be_cut_diagonally():
    grid = PlanningGrid((2, 2), 10, y_max_m=20)
    scenario = Scenario("corner", (Ignition((5, 15)),), (Target("opposite", (15, 5)),), 100)
    result = evaluate(
        grid=grid,
        domain_mask=np.array([[True, False], [False, True]]),
        background=ConstantDirectionalSpreadRate(1),
        plan=Plan("empty"),
        scenario=scenario,
    )
    assert result.target_arrivals_s == (inf,)


@pytest.mark.parametrize("mode,expected", [("M0", 0), ("M1", 1)])
def test_closed_barrier_point_tangency_and_m1_zero_length_contact(mode, expected):
    grid = PlanningGrid((2, 2), 10, y_max_m=20)
    # Diagonal (5,15)->(15,5) touches only the top-left corner (10,10)
    # of the strip rectangle [10,12] x [10,20].
    treatment = Treatment("touch", (11, 10), (11, 20), 2, "treated")
    prepared = prepare_plan(grid, Plan("touch", (treatment,)), (profile(mode),))
    adapter = GeometrySpreadRate(
        grid, prepared, ConstantDirectionalSpreadRate(1), np.ones((2, 2), bool)
    )
    assert adapter.spread_rate_m_s(0, 0, (1, 1)) == pytest.approx(expected)


def test_diagonal_m1_length_uses_physical_length_not_cell_count():
    grid = PlanningGrid((2, 2), 10, y_max_m=20)
    treatment = Treatment("diagonal", (10, 0), (10, 20), 2, "treated")
    args = dict(grid=grid, plan=Plan("diagonal", (treatment,)), profiles=(profile(),))
    adapter = GeometrySpreadRate(
        grid, prepare_plan(**args), ConstantDirectionalSpreadRate(1), np.ones((2, 2), bool)
    )
    rate = adapter.spread_rate_m_s(0, 0, (1, 1))
    length = 10 * sqrt(2)
    assert length / rate == pytest.approx(8 * sqrt(2) + 2 * sqrt(2) / 0.25)


def test_overlap_same_profile_is_charged_once_distinct_profiles_are_rejected():
    grid = PlanningGrid((2, 11), 10, y_max_m=20)
    plan = Plan("overlap", (line(name="a"), line(name="b")))
    prepared = prepare_plan(grid, plan, (profile(),))
    assert prepared.treated_area_m2 == pytest.approx(20)
    assert prepared.cost_units == pytest.approx(40)
    mixed = Plan("conflict", (line(name="a"), line(name="b", pid="other")))
    with pytest.raises(ValueError, match="overlap or touch"):
        prepare_plan(grid, mixed, (profile(), profile(name="other")))


def test_treatment_cannot_remove_target_or_relocate_ignition():
    for x in (5.0, 105.0):
        treatment = line(start=(x, 0), end=(x, 10))
        with pytest.raises(ValueError, match="conflicts"):
            evaluate(**inputs(), plan=Plan("conflict", (treatment,)), profiles=(profile(),))


def test_coarse_point_mapping_conflict_is_rejected_not_silently_approximated():
    args = inputs()
    args["scenario"] = Scenario("mapped", (Ignition((5, 5)),), (Target("near", (57, 5)),), 100)
    with pytest.raises(ValueError, match="mapped centre"):
        evaluate(**args, plan=Plan("strip", (line(),)), profiles=(profile(),))


def test_declared_bounds_and_unsupported_modes_fail_explicitly():
    with pytest.raises(ValueError, match="outside declared"):
        prepare_plan(
            PlanningGrid((1, 11), 10, y_max_m=10), Plan("bad", (line(end=(55, 20)),)), (profile(),)
        )
    with pytest.raises(ValueError, match="local_metre"):
        PlanningGrid((2, 2), 10, coordinate_frame="EPSG:32650")
    with pytest.raises(ValueError, match="M0/M1"):
        profile("M2")
    with pytest.raises(ValueError, match="long-range"):
        evaluate(**inputs(), plan=Plan("empty"), neighborhood=MooreNeighborhood(radius=2))


def test_zero_rate_and_invalid_rate_have_different_semantics():
    assert evaluate(
        **inputs(), plan=Plan("zero", (line(),)), profiles=(profile(rate=0),)
    ).target_arrivals_s == (inf,)

    class BadRate:
        def spread_rate_m_s(self, row, col, offset):
            return float("nan")

    bad = TreatmentProfile("treated", "M1", EVIDENCE, 1, BadRate())
    with pytest.raises(ValueError, match="ROS"):
        evaluate(**inputs(), plan=Plan("bad", (line(),)), profiles=(bad,))


def test_slow_band_monotonicity_and_width_response_on_fixed_geometry():
    args = inputs(shape=(9, 11), horizon=500)
    baseline = evaluate(**args, plan=Plan("empty"))
    previous = baseline.arrival_times_s
    for width in (1, 2, 4, 8):
        result = evaluate(
            **args, plan=Plan("strip", (line(width, end=(55, 90)),)), profiles=(profile(),)
        )
        assert np.all(result.arrival_times_s >= previous - 1e-10)
        previous = result.arrival_times_s


def test_delayed_multi_ignitions_are_combined_without_mutating_scenario():
    args = inputs()
    args["scenario"] = Scenario(
        "multi", (Ignition((5, 5)), Ignition((105, 5), 2)), (Target("asset", (105, 5)),), 3
    )
    result = evaluate(**args, plan=Plan("empty"), neighborhood=VonNeumannNeighborhood())
    assert result.target_arrivals_s == (2,)


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf")])
def test_width_contract_rejects_nonphysical_numbers(bad):
    with pytest.raises(ValueError):
        line(width=bad)


def test_m1_can_use_independently_computed_rothermel_profiles_without_multipliers():
    from pyfireca.behavior.fuel_catalog import get_standard_fuel_model
    from pyfireca.behavior.rothermel import RothermelFuelMoisture, RothermelInputs
    from pyfireca.behavior.rothermel_directional import HomogeneousRothermelDirectionalSpreadRate
    from pyfireca.behavior.rothermel_model import RothermelModel

    def provider(code):
        return HomogeneousRothermelDirectionalSpreadRate(
            RothermelModel(),
            RothermelInputs(
                fuel=get_standard_fuel_model(code),
                moisture=RothermelFuelMoisture(0.05, 0.05, 0.05, 1.0, 1.0),
                midflame_wind_speed_m_s=0,
                wind_from_direction_deg=270,
                slope_deg=0,
                aspect_deg=180,
            ),
        )

    background = provider(1)
    treated = provider(2)
    treatment_profile = TreatmentProfile(
        "treated",
        "M1",
        Evidence(
            "model_derived",
            "existing homogeneous FM1/FM2 Rothermel",
            "numerical integration only; not a calibrated treatment prescription",
        ),
        1,
        treated,
    )
    args = inputs(horizon=1e6)
    args["background"] = background
    result = evaluate(**args, plan=Plan("fuel-profile", (line(4),)), profiles=(treatment_profile,))
    expected = 96 / background.spread_rate_m_s(0, 0, (0, 1)) + 4 / treated.spread_rate_m_s(
        0, 0, (0, 1)
    )
    assert result.target_arrivals_s == pytest.approx((expected,))


def test_same_profile_disconnected_regions_and_zero_background_fully_covered_edge():
    grid = PlanningGrid((1, 11), 10, y_max_m=10)
    strips = (
        line(2, start=(30, 0), end=(30, 10), name="left"),
        line(2, start=(70, 0), end=(70, 10), name="right"),
    )
    result = evaluate(**inputs(horizon=200), plan=Plan("two-strips", strips), profiles=(profile(),))
    assert result.target_arrivals_s == pytest.approx((112,))
    full = line(20, start=(15, 0), end=(15, 10))
    adapter = GeometrySpreadRate(
        grid,
        prepare_plan(grid, Plan("full", (full,)), (profile(),)),
        ConstantDirectionalSpreadRate(0),
        np.ones(grid.shape, bool),
    )
    assert adapter.spread_rate_m_s(0, 1, (0, 1)) == pytest.approx(0.25)
