"""Cold, audited discrete search baselines; objectives only arrive via oracle."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from pyfireca.planning.contracts import require_number
from pyfireca.planning.objectives import LayoutScore
from pyfireca.planning.optimizers import CandidateLayout


@dataclass(frozen=True, slots=True)
class SearchQuery:
    plan_id: str
    cache_hit: bool
    score: LayoutScore


@dataclass(frozen=True, slots=True)
class SearchResult:
    method: str
    selected: LayoutScore | None
    near_optimal_visited_ids: tuple[str, ...]
    action_set_size: int
    coverage_complete: bool
    unqueried_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    evaluated_ids: tuple[str, ...]
    queries: tuple[SearchQuery, ...]
    accepted_path_ids: tuple[str, ...]
    stop_reason: str


class _RequestLimit(Exception):
    pass


def search_layouts(
    *,
    layouts: tuple[CandidateLayout, ...],
    cost_by_id: dict[str, float],
    oracle,
    budget_units: float,
    method: str,
    fixed_width_m: float | None = None,
    lookahead_segments: int | None = None,
    practical_difference: float = 0.05,
    max_requests: int = 1000,
) -> SearchResult:
    """Query a finite candidate space without pre-reading its loss table.

    Geometry/costs are known, pressure losses are obtained on demand. A new
    per-invocation cache is used; failed scores remain unknown. Exhaustive fixed
    width baselines optimize placement in their own action sets. Forward search
    can change up to ``lookahead_segments`` coordinates at once, including
    skipping a width level; it never accepts a zero immediate/composite gain.
    """
    require_number("budget_units", budget_units)
    require_number("practical_difference", practical_difference)
    if method not in (
        "joint_exhaustive",
        "fixed_width_exhaustive",
        "uniform_width_exhaustive",
        "myopic_greedy",
        "combination_lookahead",
    ):
        raise ValueError("unsupported search method")
    if (
        not isinstance(layouts, tuple)
        or not layouts
        or not all(isinstance(layout, CandidateLayout) for layout in layouts)
    ):
        raise ValueError("layouts must be a nonempty tuple of CandidateLayout")
    ids = {layout.plan.plan_id for layout in layouts}
    if len(ids) != len(layouts) or set(cost_by_id) != ids:
        raise ValueError("unique layout IDs and exact matching cost keys are required")
    for cost in cost_by_id.values():
        require_number("layout cost", cost)
    dimensions = len(layouts[0].widths_m)
    if not dimensions or any(len(layout.widths_m) != dimensions for layout in layouts):
        raise ValueError("layout choices must have the same nonzero dimension")
    for layout in layouts:
        for width in layout.widths_m:
            require_number("layout width", width)
    if len({layout.widths_m for layout in layouts}) != len(layouts):
        raise ValueError("layout width choices must be unique")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise ValueError("max_requests must be a positive integer")
    if lookahead_segments is None:
        lookahead_segments = min(3, dimensions)
    if (
        isinstance(lookahead_segments, bool)
        or not isinstance(lookahead_segments, int)
        or not 1 <= lookahead_segments <= dimensions
    ):
        raise ValueError("lookahead_segments must be within the candidate dimension")
    if method == "fixed_width_exhaustive":
        require_number("fixed_width_m", fixed_width_m, positive=True)
    elif fixed_width_m is not None:
        raise ValueError("fixed width only belongs to the fixed-width method")
    allowed = tuple(
        layout
        for layout in layouts
        if cost_by_id[layout.plan.plan_id] <= budget_units + 1e-8
        and (
            method != "fixed_width_exhaustive"
            or all(w in (0, fixed_width_m) for w in layout.widths_m)
        )
        and (
            method != "uniform_width_exhaustive" or len({w for w in layout.widths_m if w > 0}) <= 1
        )
    )
    empty = [layout for layout in allowed if all(w == 0 for w in layout.widths_m)]
    if len(empty) != 1 or cost_by_id[empty[0].plan.plan_id] > 1e-8:
        raise ValueError("exactly one zero-cost no-treatment layout must be available")
    cache, queries, path = {}, [], []

    def query(layout):
        if len(queries) >= max_requests:
            raise _RequestLimit()
        plan_id = layout.plan.plan_id
        hit = plan_id in cache
        if not hit:
            score = oracle(layout)
            if not isinstance(score, LayoutScore) or score.plan_id != plan_id:
                raise ValueError("oracle must return the queried layout's LayoutScore")
            if abs(score.cost_units - cost_by_id[plan_id]) > 1e-8:
                raise ValueError("oracle cost differs from declared physical construction cost")
            if score.status == "over_budget":
                raise ValueError("oracle must not exclude a declared budget-feasible query")
            cache[plan_id] = score
        queries.append(SearchQuery(plan_id, hit, cache[plan_id]))
        return cache[plan_id]

    stop = "action_set_exhausted"
    try:
        initial = query(empty[0])
        path.append(initial.plan_id)
        if method in ("joint_exhaustive", "fixed_width_exhaustive", "uniform_width_exhaustive"):
            for layout in allowed:
                if layout.plan.plan_id != initial.plan_id:
                    query(layout)
        elif initial.status != "ok":
            stop = "baseline_unresolved"
        else:
            current = empty[0]
            current_score = initial
            depth = 1 if method == "myopic_greedy" else lookahead_segments
            while True:
                candidates = []
                for layout in allowed:
                    changes = sum(
                        a != b for a, b in zip(current.widths_m, layout.widths_m, strict=True)
                    )
                    if not 1 <= changes <= depth or any(
                        b < a for a, b in zip(current.widths_m, layout.widths_m, strict=True)
                    ):
                        continue
                    score = query(layout)
                    if score.status != "ok":
                        continue
                    gain = current_score.mean_exposure - score.mean_exposure
                    if gain <= 1e-12:
                        continue
                    extra_cost = score.cost_units - current_score.cost_units
                    density = gain / extra_cost if extra_cost > 1e-8 else inf
                    candidates.append(
                        (
                            -density,
                            score.mean_exposure,
                            score.cost_units,
                            score.plan_id,
                            layout,
                            score,
                        )
                    )
                if not candidates:
                    stop = "no_positive_marginal_gain"
                    break
                *_, current, current_score = min(candidates, key=lambda c: c[:4])
                path.append(current_score.plan_id)
    except _RequestLimit:
        stop = "request_budget"
    evaluated = sorted(
        (s for s in cache.values() if s.status == "ok"),
        key=lambda s: (s.mean_exposure, s.cost_units, s.plan_id),
    )
    selected = evaluated[0] if evaluated else None
    near = (
        tuple(
            s.plan_id
            for s in sorted(evaluated, key=lambda s: (s.cost_units, s.plan_id))
            if s.mean_exposure <= selected.mean_exposure + practical_difference + 1e-12
        )
        if selected
        else ()
    )
    unqueried = tuple(
        sorted(layout.plan.plan_id for layout in allowed if layout.plan.plan_id not in cache)
    )
    unknown = tuple(sorted(s.plan_id for s in cache.values() if s.status == "resource_limit"))
    return SearchResult(
        method,
        selected,
        near,
        len(allowed),
        not unqueried and not unknown,
        unqueried,
        unknown,
        tuple(sorted(cache)),
        tuple(queries),
        tuple(path),
        stop,
    )
