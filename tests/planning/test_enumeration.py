"""Verify finite candidate optima, scenario membership and unknown failure semantics."""

import numpy as np
import pytest

pytest.importorskip("shapely", minversion="2")

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.contracts import (
    Evidence,
    Ignition,
    PlanningGrid,
    Scenario,
    Target,
    TreatmentProfile,
)
from pyfireca.planning.objectives import LayoutScore, select_near_optimal, weighted_mean_exposure
from pyfireca.planning.optimizers import CandidateSegment, enumerate_layouts
from pyfireca.planning.runner import PressureScenario, evaluate_layouts
from pyfireca.planning.visibility import VisibilityConfig, VisibilityResourceLimit


def arguments():
    grid = PlanningGrid((10, 10), 10, -50, 50)
    candidates = (CandidateSegment("short", (0, -12), (0, 12), (0, 2, 4), "treated"),)
    targets = (Target("asset", (40, 0), 2),)
    pressures = (
        PressureScenario(Scenario("center", (Ignition((-40, 0)),), targets, 87)),
        PressureScenario(Scenario("upper", (Ignition((-40, 4)),), targets, 87)),
    )
    profile = TreatmentProfile(
        "treated", "M1", Evidence("synthetic", "test", "D0"), 1, ConstantDirectionalSpreadRate(0.25)
    )
    return dict(
        grid=grid,
        domain_mask=np.ones(grid.shape, bool),
        background_rates_m_s=np.ones(grid.shape),
        layouts=enumerate_layouts(candidates),
        pressures=pressures,
        profiles=(profile,),
        config=VisibilityConfig(),
        max_cost_units=48,
    )


def test_enumeration_contains_zero_choices_unique_stable_plans_and_exact_cardinality():
    a = CandidateSegment("a", (0, -12), (0, 0), (0, 2, 4), "treated")
    b = CandidateSegment("b", (0, 0), (0, 12), (0, 2, 4), "treated")
    layouts = enumerate_layouts((a, b))
    assert len(layouts) == 9
    assert layouts == enumerate_layouts((a, b))
    assert layouts[0].plan.treatments == ()
    assert layouts[-1].widths_m == (4, 4)
    assert len({layout.plan.plan_id for layout in layouts}) == 9
    with pytest.raises(ValueError, match="budget"):
        enumerate_layouts((a, b), max_plans=8)
    with pytest.raises(ValueError, match="unique"):
        enumerate_layouts((a, a))


@pytest.mark.parametrize(
    "widths", [(2, 4), (0, 2, 2), (0, -2, 4), (0, True, 4), (0, float("nan"), 4)]
)
def test_width_choices_must_operationalize_no_build_and_distinct_valid_widths(widths):
    with pytest.raises(ValueError):
        CandidateSegment("a", (0, 0), (0, 1), widths, "treated")


def test_pressure_weights_are_explicit_scale_invariant_not_probability_claims():
    assert weighted_mean_exposure((2, 4), (1, 3)) == pytest.approx(3.5)
    assert weighted_mean_exposure((2, 4), (1e308, 1e308)) == pytest.approx(3)
    with pytest.raises(ValueError):
        weighted_mean_exposure((2,), (1, 2))


def test_budget_near_sets_retain_ties_and_resource_failures_make_optimum_unknown():
    scores = (
        LayoutScore("none", 0, 4),
        LayoutScore("a", 48, 1),
        LayoutScore("b", 96, 1.03),
        LayoutScore("unknown", 48, None, "resource_limit"),
        LayoutScore("conflict", 48, None, "physical_conflict"),
    )
    selection = select_near_optimal(scores, 96)
    assert selection.best_known_loss == 1
    assert selection.exact_best_ids == ("a",)
    assert selection.near_optimal_ids == ("a", "b")
    assert selection.unevaluated_ids == ("unknown",)
    assert not selection.candidate_set_complete
    assert select_near_optimal(scores, 0).candidate_set_complete


def test_over_budget_layout_not_solved_and_costs_do_not_change_with_scenario():
    result = evaluate_layouts(**arguments())
    assert result.solver_calls == 4
    assert [o.score.status for o in result.outcomes] == ["ok", "ok", "over_budget"]
    assert result.outcomes[-1].score.mean_exposure is None
    assert result.outcomes[-1].scenarios == ()
    assert result.outcomes[0].score.mean_exposure == 2
    assert result.outcomes[1].score.cost_units == 48


def test_invalid_physics_propagates_and_resource_failure_never_gets_zero_loss(monkeypatch):
    import pyfireca.planning.runner as runner

    def failed(**kwargs):
        raise VisibilityResourceLimit("test node budget")

    monkeypatch.setattr(runner, "evaluate_visibility", failed)
    result = evaluate_layouts(**arguments())
    assert all(
        o.score.status == "resource_limit" and o.score.mean_exposure is None
        for o in result.outcomes[:2]
    )
    assert len(result.outcomes[1].scenarios) == 2

    def invalid(**kwargs):
        raise ValueError("invalid ROS")

    monkeypatch.setattr(runner, "evaluate_visibility", invalid)
    with pytest.raises(ValueError, match="invalid ROS"):
        evaluate_layouts(**arguments())


def test_batch_call_budget_fails_before_any_solve_and_target_membership_cannot_change(monkeypatch):
    import pyfireca.planning.runner as runner

    def must_not_solve(**kwargs):
        pytest.fail("budget/config should have been checked first")

    monkeypatch.setattr(runner, "evaluate_visibility", must_not_solve)
    with pytest.raises(VisibilityResourceLimit, match="before"):
        evaluate_layouts(**arguments(), max_solver_calls=1)
    args = arguments()
    args["pressures"] += (
        PressureScenario(
            Scenario("different", (Ignition((-40, 0)),), (Target("changed", (40, 0)),), 87)
        ),
    )
    with pytest.raises(ValueError, match="fixed targets"):
        evaluate_layouts(**args)


def test_physical_target_conflict_is_distinct_from_resource_failure():
    args = arguments()
    targets = (Target("near", (0, 0)),)
    args["pressures"] = (
        PressureScenario(Scenario("conflict", (Ignition((-40, 0)),), targets, 87)),
    )
    result = evaluate_layouts(**args)
    assert result.outcomes[0].score.status == "ok"
    assert result.outcomes[1].score.status == "physical_conflict"
    assert result.outcomes[1].score.mean_exposure is None


def test_joint_construction_cost_uses_union_not_duplicate_candidate_sum():
    args = arguments()
    candidates = (
        CandidateSegment("a", (0, -12), (0, 12), (0, 2), "treated"),
        CandidateSegment("b", (0, -12), (0, 12), (0, 2), "treated"),
    )
    args["layouts"] = enumerate_layouts(candidates)
    result = evaluate_layouts(**args)
    assert result.outcomes[-1].score.cost_units == 48
    assert result.outcomes[-1].treated_area_m2 == 48
