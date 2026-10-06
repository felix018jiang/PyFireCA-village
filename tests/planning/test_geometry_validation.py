"""Continuum oracles, adversarial raster controls and direction-bias checks."""

from math import hypot, sqrt

import numpy as np
import pytest

pytest.importorskip("shapely", minversion="2")

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.adapters import centre_labels, evaluate
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
    octile_time_s,
    parallel_band_time_s,
    rectangle_detour_infimum_s,
    rotate_point,
)


def test_rectangle_oracle_matches_corner_formula_and_asymmetric_shorter_side():
    assert rectangle_detour_infimum_s((-40, 0), (40, 0), 2, 12) == pytest.approx(
        2 * sqrt(39**2 + 12**2) + 2
    )
    assert rectangle_detour_infimum_s((-10, 3), (10, 2), 2, 4, 2) == pytest.approx(
        (hypot(9, 1) + 2 + hypot(9, 2)) / 2
    )


@pytest.mark.parametrize("angle", [0, 22.5, 45, 90, -37])
def test_rigid_scene_rotation_preserves_lengths_and_roundtrip(angle):
    source, target = (-40, 0), (40, 0)
    a, b = rotate_point(source, angle), rotate_point(target, angle)
    assert hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(80)
    assert rotate_point(a, -angle) == pytest.approx(source)
    assert rotate_point(b, -angle) == pytest.approx(target)


def test_parallel_band_reference_normal_equal_rate_and_reciprocity():
    assert parallel_band_time_s((-40, 0), (40, 0), 2, 1, 0.25) == pytest.approx(86)
    assert parallel_band_time_s((-40, -3), (40, 9), 2, 2, 2) == pytest.approx(hypot(80, 12) / 2)
    assert parallel_band_time_s((-10, 0), (15, 9), 2, 1, 0.25) == pytest.approx(
        parallel_band_time_s((-15, -9), (10, 0), 2, 1, 0.25)
    )


def test_snell_reference_is_below_independently_enumerated_feasible_crossings():
    oracle = parallel_band_time_s((-10, 0), (10, 9), 2, 1, 0.25)
    feasible = [
        hypot(9, a) + hypot(2, b) / 0.25 + hypot(9, 9 - a - b)
        for a in np.linspace(0, 9, 37)
        for b in np.linspace(0, 9 - a, 37)
    ]
    assert hypot(20, 9) < oracle <= min(feasible) + 1e-10
    assert min(feasible) - oracle < 0.01


@pytest.mark.parametrize(
    "call",
    [
        lambda: rectangle_detour_infimum_s((-10, 10), (10, 0), 2, 4),
        lambda: rectangle_detour_infimum_s((0, 0), (10, 0), 2, 4),
        lambda: parallel_band_time_s((-10, 0), (10, 0), 2, 1, 0),
        lambda: parallel_band_time_s((-10, 0), (10, 0), -2, 1, 1),
    ],
)
def test_references_reject_cases_outside_their_mathematical_support(call):
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("mode", ["M0", "M1"])
def test_centre_control_can_miss_subcell_strip_but_physical_cost_is_unchanged(mode):
    grid = PlanningGrid((20, 20), 8, -80, 80)
    evidence = Evidence("synthetic", "P02 control", "math only")
    profile = TreatmentProfile(
        "treated", mode, evidence, 1, ConstantDirectionalSpreadRate(0.25) if mode == "M1" else None
    )
    plan = Plan("finite", (Treatment("line", (0, -12), (0, 12), 2, "treated"),))
    args = dict(
        grid=grid,
        domain_mask=np.ones(grid.shape, bool),
        background=ConstantDirectionalSpreadRate(1),
        plan=plan,
        scenario=Scenario("s", (Ignition((-40, 0)),), (Target("asset", (40, 0)),), 100),
        profiles=(profile,),
    )
    vector = evaluate(**args)
    raster = evaluate(**args, representation="centre_mask")
    assert vector.target_arrivals_s[0] > raster.target_arrivals_s[0] == 80
    assert vector.treated_area_m2 == raster.treated_area_m2 == pytest.approx(48)
    assert vector.cost_units == raster.cost_units == pytest.approx(48)
    assert np.all(centre_labels(grid, prepare_plan(grid, plan, (profile,))) == -1)


def test_fixed_moore_direction_bias_persists_under_grid_refinement():
    times = []
    for h in (10, 5, 2.5):
        grid = PlanningGrid((round(120 / h), round(120 / h)), h, -40, 80)
        a, b = (0, 0), (40, 20)
        result = evaluate(
            grid=grid,
            domain_mask=np.ones(grid.shape, bool),
            background=ConstantDirectionalSpreadRate(1),
            plan=Plan("empty"),
            scenario=Scenario("same-physical-points", (Ignition(a),), (Target("asset", b),), 100),
        )
        oracle = octile_time_s(grid.centre(*grid.cell_at(a)), grid.centre(*grid.cell_at(b)))
        assert result.target_arrivals_s == pytest.approx((oracle,))
        times.append(result.target_arrivals_s[0])
    assert times == pytest.approx([40 + (sqrt(2) - 1) * 20] * 3)
    assert times[-1] > hypot(40, 20) * 1.07


def test_unknown_representation_fails_instead_of_silently_falling_back():
    with pytest.raises(ValueError, match="representation"):
        evaluate(
            grid=PlanningGrid((1, 2), 10, y_max_m=10),
            domain_mask=np.ones((1, 2), bool),
            background=ConstantDirectionalSpreadRate(1),
            plan=Plan("empty"),
            scenario=Scenario("s", (Ignition((5, 5)),), (Target("t", (15, 5)),), 100),
            representation="unregistered",
        )


def test_source_cell_heterogeneous_background_has_refining_directional_error():
    errors = []
    for h in (8, 4, 2):
        grid = PlanningGrid((1, round(160 / h)), h, -80, h / 2)

        class HalfSpace:
            def __init__(self, grid):
                self.grid = grid

            def spread_rate_m_s(self, row, col, offset):
                return 1 if self.grid.centre(row, col)[0] < 0 else 2

        arrivals = []
        a, b = (-40, 0), (40, 0)
        left, right = grid.centre(*grid.cell_at(a))[0], grid.centre(*grid.cell_at(b))[0]
        reference = -left + right / 2
        for source, target in ((a, b), (b, a)):
            result = evaluate(
                grid=grid,
                domain_mask=np.ones(grid.shape, bool),
                background=HalfSpace(grid),
                plan=Plan("empty"),
                scenario=Scenario("interface", (Ignition(source),), (Target("t", target),), 200),
            )
            arrivals.append(result.target_arrivals_s[0])
        # Reciprocal continuum rates, but a directed source-cell graph gives
        # a half-cell interface error of opposite sign in either direction.
        assert arrivals == pytest.approx((60, 60 - h / 2))
        assert abs(arrivals[0] - reference) == pytest.approx(h / 4)
        assert abs(arrivals[1] - reference) == pytest.approx(h / 4)
        errors.append(abs(arrivals[0] - reference))
    assert errors == pytest.approx((2, 1, 0.5))
