"""Complete cardinality-layer search for simulation-constrained demolition.

The oracle is authoritative. No monotonicity of fire response or graph-path
necessity is assumed. Areas are nominal sums, not cleared-union areas.
"""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass
from math import comb, fsum, isfinite
from time import perf_counter


@dataclass(frozen=True)
class ProtectionCheck:
    status: str
    evidence: dict

    def __post_init__(self):
        if self.status not in {"passed", "failed", "unknown"}:
            raise ValueError("oracle status must be passed, failed or unknown")


def ordered_demolition_sets(
    candidate_areas, count, *, area_limit=None, statistics=None, branch_bound=None
):
    """Yield each k-subset once in nondecreasing nominal area, lazily.

    A prefix's bound is its area plus the cheapest remaining completion.
    Best-first expansion avoids materializing an entire combination layer.
    """
    items = sorted(candidate_areas, key=lambda i: (candidate_areas[i], i))
    n = len(items)
    if not 0 <= count <= n:
        raise ValueError("invalid demolition count")
    heap = []

    def push(prefix, start):
        needed = count - len(prefix)
        if n - start < needed:
            return
        completion = prefix + tuple(range(start, start + needed))
        bound = fsum(candidate_areas[items[i]] for i in completion)
        if branch_bound is not None:
            extra = branch_bound(tuple(items[i] for i in prefix), tuple(items[start:]), needed)
            if extra is None:
                if statistics is not None:
                    statistics["failure_cut_pruned_combinations"] += comb(n - start, needed)
                return
            bound = max(bound, extra)
        if area_limit is None or bound <= area_limit:
            heapq.heappush(heap, (bound, completion, prefix, start))
        elif statistics is not None:
            statistics["area_pruned_prefixes"] += 1
            statistics["area_pruned_combinations"] += comb(n - start, needed)

    push((), 0)
    while heap:
        bound, _completion, prefix, start = heapq.heappop(heap)
        needed = count - len(prefix)
        if branch_bound is not None:
            extra = branch_bound(tuple(items[i] for i in prefix), tuple(items[start:]), needed)
            if extra is None:
                if statistics is not None:
                    statistics["failure_cut_pruned_combinations"] += comb(n - start, needed)
                continue
            bound = max(bound, extra)
            if area_limit is not None and bound > area_limit:
                if statistics is not None:
                    statistics["area_pruned_prefixes"] += 1
                    statistics["area_pruned_combinations"] += comb(n - start, needed)
                continue
        if not needed:
            yield tuple(sorted(items[i] for i in prefix)), bound
        else:
            for index in range(start, n - needed + 1):
                push((*prefix, index), index + 1)


def exact_demolition_search(
    candidate_areas,
    scenario_ids,
    evaluate,
    *,
    problem_sha256,
    initial_ids=None,
    max_new_checks=None,
    cache=None,
    dominated_candidates=None,
    prioritize_failures=False,
    max_wall_time_s=None,
    failure_cuts=None,
    maximum_demolitions=None,
):
    """Minimize (count, nominal area) and report whether proof is complete.

    Cache keys bind the entire caller-supplied frozen problem fingerprint.
    An initial solution is verified, never trusted. Unknown cheaper solutions
    remain proof blockers; callback exceptions propagate for explicit repair.
    """
    areas = dict(candidate_areas)
    started = perf_counter()
    if any(not isinstance(i, str) or not i for i in areas):
        raise ValueError("candidate IDs must be nonempty strings")
    if any(not isfinite(a) or a <= 0 for a in areas.values()):
        raise ValueError("candidate areas must be finite and positive")
    scenarios = tuple(scenario_ids)
    if not scenarios or len(set(scenarios)) != len(scenarios):
        raise ValueError("nonempty unique scenario IDs required")
    if not problem_sha256:
        raise ValueError("frozen problem fingerprint required")
    if max_wall_time_s is not None and (not isfinite(max_wall_time_s) or max_wall_time_s < 0):
        raise ValueError("wall time budget must be finite and nonnegative")
    dominated = {} if dominated_candidates is None else dict(dominated_candidates)
    if not dominated.keys() <= areas.keys() or any(
        proof.get("problem_sha256") != problem_sha256 for proof in dominated.values()
    ):
        raise ValueError("dominance proofs must bind candidate IDs and the frozen problem")
    active_areas = {i: a for i, a in areas.items() if i not in dominated}
    if maximum_demolitions is not None and (
        isinstance(maximum_demolitions, bool)
        or not isinstance(maximum_demolitions, int)
        or not 0 <= maximum_demolitions <= len(areas)
    ):
        raise ValueError("maximum demolitions must lie within the candidate count")
    if max_new_checks is not None and (
        isinstance(max_new_checks, bool)
        or not isinstance(max_new_checks, int)
        or max_new_checks < 0
    ):
        raise ValueError("check budget must be a nonnegative integer")
    if initial_ids is not None:
        initial_ids = tuple(sorted(initial_ids))
        if len(set(initial_ids)) != len(initial_ids) or not set(initial_ids) <= areas.keys():
            raise ValueError("initial solution must contain unique candidate IDs")
    cache = {} if cache is None else cache
    new_checks = cache_hits = 0
    trace, outcomes = [], {}
    incumbent = None
    exhausted = False
    stop_reason = None
    witnesses = []

    def objective(ids):
        return len(ids), fsum(areas[i] for i in ids)

    def check_plan(ids, *, initial=False):
        nonlocal new_checks, cache_hits, exhausted, stop_reason
        checks, unknown = [], False
        status = "passed"
        order = scenarios
        if prioritize_failures and witnesses:
            scores = {s: len(areas) + 1 for s in scenarios}
            for previous, s in witnesses:
                scores[s] = min(scores[s], len(set(ids).symmetric_difference(previous)))
            order = tuple(sorted(scenarios, key=lambda s: (scores[s], scenarios.index(s))))
        for scenario in order:
            key = (problem_sha256, ids, scenario)
            if key in cache:
                check = cache[key]
                cache_hits += 1
            else:
                if max_new_checks is not None and new_checks >= max_new_checks:
                    exhausted = True
                    stop_reason = "check_limit"
                    status = "unknown"
                    break
                if max_wall_time_s is not None and perf_counter() - started >= max_wall_time_s:
                    exhausted = True
                    stop_reason = "wall_time_limit"
                    status = "unknown"
                    break
                check = evaluate(ids, scenario)
                if not isinstance(check, ProtectionCheck):
                    raise TypeError("oracle must return ProtectionCheck")
                cache[key] = check
                new_checks += 1
            checks.append({"scenario_id": scenario, **asdict(check)})
            if check.status == "failed":
                if failure_cuts is not None:
                    failure_cuts.learn(ids, scenario, check)
                status = "failed"
                witnesses.append((frozenset(ids), scenario))
                break
            unknown |= check.status == "unknown"
        if unknown and status == "passed":
            status = "unknown"
        trace.append(
            {
                "demolished_ids": list(ids),
                "objective": list(objective(ids)),
                "status": status,
                "initial_solution_check": initial,
                "checks": checks,
            }
        )
        outcomes[ids] = status
        return status

    if initial_ids is not None:
        status = check_plan(initial_ids, initial=True)
        if status == "failed":
            raise ValueError("declared initial feasible solution fails the frozen problem")
        if status == "passed":
            incumbent = initial_ids

    layers = []
    stop = exhausted
    max_count = (
        len(incumbent)
        if incumbent is not None
        else (len(areas) if maximum_demolitions is None else maximum_demolitions)
    )
    if (
        incumbent is not None
        and maximum_demolitions is not None
        and len(incumbent) > maximum_demolitions
    ):
        raise ValueError("initial feasible solution exceeds the declared demolition bound")
    for count in range(max_count + 1):
        if stop:
            break
        area_limit = (
            objective(incumbent)[1] if incumbent is not None and count == len(incumbent) else None
        )
        layer = {
            "demolished_count": count,
            "total_combinations": comb(len(areas), count),
            "failed": 0,
            "unknown": 0,
            "passed": 0,
        }
        layer.update(
            dominance_pruned_combinations=(
                comb(len(areas), count) - comb(len(active_areas), count)
            ),
            area_pruned_prefixes=0,
            area_pruned_combinations=0,
            failure_cut_pruned_combinations=0,
        )
        if count > len(active_areas):
            layers.append(layer)
            continue
        for ids, _area in ordered_demolition_sets(
            active_areas,
            count,
            area_limit=area_limit,
            statistics=layer,
            branch_bound=None if failure_cuts is None else failure_cuts.bound,
        ):
            status = outcomes.get(ids)
            if status is None:
                status = check_plan(ids)
            layer[status] += 1
            if status == "passed":
                if incumbent is None or objective(ids) < objective(incumbent):
                    incumbent = ids
                stop = True
                break
            if exhausted:
                stop = True
                break
        layers.append(layer)

    blockers = [
        row
        for row in trace
        if row["status"] == "unknown"
        and (incumbent is None or tuple(row["objective"]) < objective(incumbent))
    ]
    proven = incumbent is not None and not blockers and not exhausted
    status = "optimal" if proven else "incomplete" if blockers or exhausted else "infeasible"
    if (
        status == "infeasible"
        and maximum_demolitions is not None
        and maximum_demolitions < len(areas)
    ):
        status = "bounded_infeasible"
    closed_lower = 0
    for layer in layers:
        covered = (
            layer["failed"]
            + layer["failure_cut_pruned_combinations"]
            + layer["dominance_pruned_combinations"]
        )
        if covered == layer["total_combinations"] and not layer["unknown"] and not layer["passed"]:
            closed_lower = layer["demolished_count"] + 1
        else:
            break
    count_proven = proven or (incumbent is not None and closed_lower >= len(incumbent))
    return {
        "schema_version": "heritage-exact-search-1",
        "problem_sha256": problem_sha256,
        "status": status,
        "optimality_proven": proven,
        "minimum_count_proven": count_proven,
        "minimum_count_lower_bound": len(incumbent) if count_proven else closed_lower,
        "maximum_demolitions_searched": max_count,
        "failure_cuts": [] if failure_cuts is None else failure_cuts.to_mapping(),
        "objective_policy": "lexicographic_count_then_sum_nominal_footprint_area_m2",
        "candidate_ids": sorted(areas),
        "effective_candidate_ids": sorted(active_areas),
        "dominance_certificates": dominated,
        "scenario_scheduling": "nearest_previous_failure"
        if prioritize_failures
        else "frozen_order",
        "initial_ids": None if initial_ids is None else list(initial_ids),
        "best_ids": None if incumbent is None else list(incumbent),
        "best_objective": None if incumbent is None else list(objective(incumbent)),
        "new_checks": new_checks,
        "cache_hits": cache_hits,
        "budget_exhausted": exhausted,
        "budget_stop_reason": stop_reason,
        "cardinality_pruned_combinations": (
            sum(comb(len(areas), k) for k in range(len(incumbent) + 1, len(areas) + 1))
            if incumbent is not None
            else 0
        ),
        "layers": layers,
        "proof_blockers": [r["demolished_ids"] for r in blockers],
        "trace": trace,
    }
