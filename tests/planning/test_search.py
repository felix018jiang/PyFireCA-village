"""Ensure cold search, joint synergy, action-set restrictions and honest gaps."""

import pytest

from pyfireca.planning.objectives import LayoutScore
from pyfireca.planning.optimizers import CandidateSegment, enumerate_layouts
from pyfireca.planning.search import search_layouts


def fixture():
    candidates = tuple(
        CandidateSegment(f"c{i}", (i * 10, 0), (i * 10, 1), (0, 2, 4), "treated") for i in range(3)
    )
    layouts = enumerate_layouts(candidates)
    costs = {layout.plan.plan_id: sum(layout.widths_m) for layout in layouts}
    calls = []

    def oracle(layout):
        calls.append(layout.plan.plan_id)
        loss = (
            0
            if all(w == 4 for w in layout.widths_m)
            else 1
            if all(w >= 2 for w in layout.widths_m)
            else 4
        )
        return LayoutScore(layout.plan.plan_id, costs[layout.plan.plan_id], loss)

    return layouts, costs, oracle, calls


def test_myopic_stalls_but_strong_combo_matches_joint_and_counts_duplicate_queries():
    layouts, costs, oracle, calls = fixture()
    greedy = search_layouts(
        layouts=layouts, cost_by_id=costs, oracle=oracle, budget_units=12, method="myopic_greedy"
    )
    assert greedy.selected.mean_exposure == 4
    assert greedy.selected.cost_units == 0
    assert not greedy.coverage_complete
    calls.clear()
    combo = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=oracle,
        budget_units=12,
        method="combination_lookahead",
    )
    assert combo.selected.mean_exposure == 0
    assert combo.coverage_complete
    assert len(calls) == 27
    assert len(combo.queries) > len(calls)
    assert sum(not q.cache_hit for q in combo.queries) == len(calls)
    assert combo.accepted_path_ids == ("layout-0-0-0", "layout-1-1-1", "layout-2-2-2")


@pytest.mark.parametrize("width,loss,size", [(2, 1, 8), (4, 0, 8)])
def test_fixed_width_optimizes_all_placements_in_its_action_set(width, loss, size):
    layouts, costs, oracle, calls = fixture()
    result = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=oracle,
        budget_units=12,
        method="fixed_width_exhaustive",
        fixed_width_m=width,
    )
    assert result.action_set_size == size
    assert result.selected.mean_exposure == loss
    assert result.coverage_complete and len(calls) == 8


def test_each_new_search_is_cold_and_cannot_reuse_previous_objectives():
    layouts, costs, oracle, calls = fixture()
    for _ in range(2):
        result = search_layouts(
            layouts=layouts,
            cost_by_id=costs,
            oracle=oracle,
            budget_units=12,
            method="joint_exhaustive",
        )
        assert not any(q.cache_hit for q in result.queries)
    assert len(calls) == 54


def test_uniform_width_selector_is_an_actual_cold_search_not_a_hindsight_envelope():
    layouts, costs, oracle, calls = fixture()
    result = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=oracle,
        budget_units=12,
        method="uniform_width_exhaustive",
    )
    assert result.action_set_size == 15
    assert result.coverage_complete and len(calls) == 15
    assert result.selected.mean_exposure == 0
    assert result.selected.plan_id == "layout-2-2-2"


def test_unknown_scores_remain_unknown_and_request_budget_keeps_valid_incumbent():
    layouts, costs, oracle, _calls = fixture()

    def unknown(layout):
        if layout.plan.plan_id == "layout-2-2-2":
            return LayoutScore(
                layout.plan.plan_id, costs[layout.plan.plan_id], None, "resource_limit"
            )
        return oracle(layout)

    result = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=unknown,
        budget_units=12,
        method="joint_exhaustive",
    )
    assert not result.coverage_complete
    assert result.unknown_ids == ("layout-2-2-2",)
    assert result.selected.mean_exposure == 1
    result = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=oracle,
        budget_units=12,
        method="joint_exhaustive",
        max_requests=1,
    )
    assert result.stop_reason == "request_budget"
    assert result.selected.cost_units == 0
    assert len(result.unqueried_ids) == 26


def test_metadata_can_filter_costs_but_never_supply_unqueried_objectives():
    layouts, costs, oracle, calls = fixture()
    result = search_layouts(
        layouts=layouts, cost_by_id=costs, oracle=oracle, budget_units=2, method="joint_exhaustive"
    )
    assert len(calls) == result.action_set_size == 4
    assert all(costs[p] <= 2 for p in calls)
    assert result.coverage_complete
    assert len(result.near_optimal_visited_ids) == 4


def test_cost_or_id_mismatch_and_unknown_method_fail_explicitly():
    layouts, costs, oracle, _calls = fixture()
    with pytest.raises(ValueError, match="unsupported"):
        search_layouts(
            layouts=layouts, cost_by_id=costs, oracle=oracle, budget_units=12, method="fake"
        )

    def wrong_cost(layout):
        return LayoutScore(layout.plan.plan_id, costs[layout.plan.plan_id] + 1, 4)

    with pytest.raises(ValueError, match="cost differs"):
        search_layouts(
            layouts=layouts,
            cost_by_id=costs,
            oracle=wrong_cost,
            budget_units=12,
            method="joint_exhaustive",
        )


def test_three_step_combo_is_not_claimed_to_cover_a_larger_candidate_dimension():
    candidates = tuple(
        CandidateSegment(f"c{i}", (i * 10, 0), (i * 10, 1), (0, 2), "treated") for i in range(4)
    )
    layouts = enumerate_layouts(candidates)
    costs = {layout.plan.plan_id: sum(layout.widths_m) for layout in layouts}

    def oracle(layout):
        return LayoutScore(
            layout.plan.plan_id, costs[layout.plan.plan_id], 0 if all(layout.widths_m) else 4
        )

    result = search_layouts(
        layouts=layouts,
        cost_by_id=costs,
        oracle=oracle,
        budget_units=8,
        method="combination_lookahead",
    )
    assert not result.coverage_complete
    assert result.selected.mean_exposure == 4
    assert "layout-1-1-1-1" in result.unqueried_ids
