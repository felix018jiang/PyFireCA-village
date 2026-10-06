"""Adversarial checks of continuous-edge research integration and graph paths."""

from math import inf

import numpy as np
import pytest

pytest.importorskip("shapely", minversion="2")

from pyfireca.arrival import ConstantDirectionalSpreadRate
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
from pyfireca.planning.references import (
    parallel_band_time_s,
    rectangle_detour_infimum_s,
    rotate_point,
)
from pyfireca.planning.visibility import IntegratedEdges, VisibilityConfig, evaluate_visibility

EVIDENCE = Evidence("synthetic", "visibility tests", "isotropic D0 only")


def profile(mode="M1", rate=0.25):
    return TreatmentProfile(
        "treated", mode, EVIDENCE, 1, ConstantDirectionalSpreadRate(rate) if mode == "M1" else None
    )


def arguments(h=8, mode="M1", angle=0, source=(-40, 0), target=(40, 0)):
    grid = PlanningGrid((round(160 / h), round(160 / h)), h, -80, 80)
    half = 50 if mode == "M1" else 12
    plan = Plan(
        "strip",
        (
            Treatment(
                "line",
                rotate_point((0, -half), angle),
                rotate_point((0, half), angle),
                2,
                "treated",
            ),
        ),
    )
    return dict(
        grid=grid,
        domain_mask=np.ones(grid.shape, bool),
        background_rates_m_s=np.ones(grid.shape),
        plan=plan,
        scenario=Scenario(
            "physical",
            (Ignition(rotate_point(source, angle)),),
            (Target("asset", rotate_point(target, angle)),),
            87,
        ),
        profiles=(profile(mode),),
    )


@pytest.mark.parametrize("h", [8, 4, 2])
def test_piecewise_background_integral_reciprocal_and_exact_at_physical_points(h):
    grid = PlanningGrid((1, round(160 / h)), h, -80, h / 2)
    rates = np.array([[1 if grid.centre(0, c)[0] < 0 else 2 for c in range(grid.shape[1])]])
    edges = IntegratedEdges(
        grid, np.ones(grid.shape, bool), rates, prepare_plan(grid, Plan("empty"), ())
    )
    assert edges.travel_time_s((-40, 0), (40, 0)) == pytest.approx(60)
    assert edges.travel_time_s((40, 0), (-40, 0)) == pytest.approx(60)
    rates[:] = 9
    assert edges.travel_time_s((-40, 0), (40, 0)) == pytest.approx(60)
    assert not edges.rates.flags.writeable


def test_long_edges_cannot_skip_excluded_cells_or_cut_closed_corners():
    grid = PlanningGrid((2, 4), 10, y_max_m=20)
    domain = np.ones(grid.shape, bool)
    domain[0, 1] = False
    edges = IntegratedEdges(
        grid, domain, np.ones(grid.shape), prepare_plan(grid, Plan("empty"), ())
    )
    assert edges.travel_time_s((5, 15), (35, 15)) == inf
    assert edges.travel_time_s((5, 15), (15, 5)) == inf
    assert edges.travel_time_s((5, 5), (35, 5)) == pytest.approx(30)


def test_two_subcell_treatments_integrated_without_double_counting():
    grid = PlanningGrid((1, 11), 10, y_max_m=10)
    plan = Plan(
        "two",
        (
            Treatment("a", (30, 0), (30, 10), 2, "treated"),
            Treatment("b", (70, 0), (70, 10), 2, "treated"),
            Treatment("duplicate", (30, 0), (30, 10), 2, "treated"),
        ),
    )
    edges = IntegratedEdges(
        grid, np.ones(grid.shape, bool), np.ones(grid.shape), prepare_plan(grid, plan, (profile(),))
    )
    assert edges.travel_time_s((5, 5), (105, 5)) == pytest.approx(112)
    assert edges.travel_time_s((105, 5), (5, 5)) == pytest.approx(112)


@pytest.mark.parametrize("angle", [0, 11.25, 22.5, 33.75, 45, 67.5])
@pytest.mark.parametrize("mode", ["M0", "M1"])
def test_physical_scene_rotation_does_not_restore_lattice_direction_bias(angle, mode):
    result = evaluate_visibility(**arguments(angle=angle, mode=mode))
    reference = 86 if mode == "M1" else rectangle_detour_infimum_s((-40, 0), (40, 0), 2, 12)
    assert reference - 1e-8 <= result.target_arrivals_s[0] <= reference * 1.002
    assert result.exposed_weight == 1
    assert result.tested_edge_count == result.node_count * (result.node_count - 1) // 2


def test_oblique_m1_refraction_verified_against_separate_snell_reference():
    source, target = (-40, -12), (40, 12)
    reference = parallel_band_time_s(source, target, 2, 1, 0.25)
    for spacing in (8, 4, 2):
        result = evaluate_visibility(
            **arguments(source=source, target=target),
            config=VisibilityConfig(boundary_spacing_m=spacing),
        )
        assert reference - 1e-8 <= result.target_arrivals_s[0] <= 1.02 * reference


def test_full_width_m0_remains_unreachable_even_with_direct_long_edge():
    args = arguments(mode="M0")
    args["plan"] = Plan("wall", (Treatment("wall", (0, -80), (0, 80), 0.02, "treated"),))
    result = evaluate_visibility(**args)
    assert result.target_arrivals_s == (inf,)
    assert result.exposed_weight == 0


def test_physical_target_is_not_rejected_because_its_old_mapped_centre_conflicts():
    grid = PlanningGrid((1, 11), 10, y_max_m=10)
    result = evaluate_visibility(
        grid=grid,
        domain_mask=np.ones(grid.shape, bool),
        background_rates_m_s=np.ones(grid.shape),
        plan=Plan("p", (Treatment("t", (55, 0), (55, 10), 2, "treated"),)),
        scenario=Scenario("s", (Ignition((5, 5)),), (Target("t", (57, 5)),), 100),
        profiles=(profile(),),
    )
    assert result.target_arrivals_s == pytest.approx((58,))


def test_budgets_unknown_rates_and_input_conflicts_fail_explicitly():
    with pytest.raises(ValueError, match="budget"):
        evaluate_visibility(**arguments(mode="M0"), config=VisibilityConfig(max_nodes=5))
    with pytest.raises(ValueError, match="budget"):
        evaluate_visibility(**arguments(), config=VisibilityConfig(boundary_spacing_m=1e-9))
    args = arguments()
    args["background_rates_m_s"][0, 0] = float("nan")
    with pytest.raises(ValueError, match="rates"):
        evaluate_visibility(**args)
    args = arguments()
    args["scenario"] = Scenario("s", (Ignition((0, 0)),), (Target("t", (40, 0)),), 100)
    with pytest.raises(ValueError, match="conflicts"):
        evaluate_visibility(**args)

    class Directional:
        def spread_rate_m_s(self, row, col, offset):
            return 1

    args = arguments()
    args["profiles"] = (TreatmentProfile("treated", "M1", EVIDENCE, 1, Directional()),)
    with pytest.raises(ValueError, match="isotropic constant"):
        evaluate_visibility(**args)


def test_delayed_multi_ignitions_and_multiple_target_denominator_preserved():
    args = arguments()
    args["plan"] = Plan("empty")
    args["scenario"] = Scenario(
        "s",
        (Ignition((-40, 0)), Ignition((40, 0), 2)),
        (Target("right", (40, 0), 3), Target("left", (-40, 0), 2)),
        5,
    )
    result = evaluate_visibility(**args)
    assert result.target_arrivals_s == (2, 0)
    assert result.exposed_weight == 5


def test_returned_path_is_feasible_and_sums_to_arrival_including_ignition_time():
    from itertools import pairwise

    args = arguments(mode="M0")
    result = evaluate_visibility(**args)
    edges = IntegratedEdges(
        args["grid"],
        args["domain_mask"],
        args["background_rates_m_s"],
        prepare_plan(args["grid"], args["plan"], args["profiles"]),
    )
    path = result.target_paths_m[0]
    assert path[0] == (-40, 0) and path[-1] == (40, 0)
    assert len(path) >= 4
    assert sum(edges.travel_time_s(a, b) for a, b in pairwise(path)) == pytest.approx(
        result.target_arrivals_s[0]
    )


def test_zero_treated_ros_has_a_finite_end_bypass_without_allowing_crossing():
    args = arguments()
    args["profiles"] = (profile(rate=0),)
    result = evaluate_visibility(**args)
    assert 80 < result.target_arrivals_s[0] < inf
    edges = IntegratedEdges(
        args["grid"],
        args["domain_mask"],
        args["background_rates_m_s"],
        prepare_plan(args["grid"], args["plan"], args["profiles"]),
    )
    assert edges.travel_time_s((-40, 0), (40, 0)) == inf


def test_excluded_raster_island_can_be_bypassed_but_full_wall_cannot():
    grid = PlanningGrid((9, 11), 10, y_max_m=90)
    args = dict(
        grid=grid,
        background_rates_m_s=np.ones(grid.shape),
        plan=Plan("empty"),
        scenario=Scenario("mask", (Ignition((5, 45)),), (Target("t", (105, 45)),), 200),
    )
    island = np.ones(grid.shape, bool)
    island[4, 5] = False
    result = evaluate_visibility(**args, domain_mask=island)
    assert 100 < result.target_arrivals_s[0] < inf
    island[:, 5] = False
    result = evaluate_visibility(**args, domain_mask=island)
    assert result.target_arrivals_s == (inf,)
    assert result.target_paths_m == ((),)


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf")])
def test_invalid_background_rasters_are_never_treated_as_zero_loss(bad):
    args = arguments()
    args["background_rates_m_s"] = np.full(args["grid"].shape, bad)
    with pytest.raises(ValueError, match="rates"):
        evaluate_visibility(**args)


def test_finite_slow_m1_cannot_be_slower_than_available_outside_barrier_detour():
    args = arguments(source=(-40, 16), target=(40, 16))
    args["plan"] = Plan("finite", (Treatment("strip", (0, -24), (0, 24), 4, "treated"),))
    slow = evaluate_visibility(**args)
    ideal = evaluate_visibility(**(args | {"profiles": (profile("M0"),)}))
    outside_infimum = rectangle_detour_infimum_s((-40, 16), (40, 16), 4, 24)
    assert outside_infimum <= slow.target_arrivals_s[0] <= outside_infimum + 0.01
    assert slow.target_arrivals_s[0] <= ideal.target_arrivals_s[0] + 1e-8
    assert slow.exposed_weight == 1
