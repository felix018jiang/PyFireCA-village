"""Area-first evaluation of an auditable, finite collection of interventions.

Cleared area is a caller-supplied set function. It need not be additive or
monotone: shared fuel can remain until all of its owners are removed. No
cardinality bound, additive area relaxation, or fire-response monotonicity is
used here. Small problems can enumerate their entire powerset; explicit
proposal collections always retain an unresolved full-candidate search scope.
"""

from __future__ import annotations

from dataclasses import asdict
from itertools import combinations
from math import isfinite
from time import perf_counter

from pyfireca.planning.building_search import ProtectionCheck


def _nonnegative(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and nonnegative")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite and nonnegative") from error
    if not isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


def _ids(values, *, name, eligible=None):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a collection of unique nonempty IDs")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError(f"{name} must be a collection of unique nonempty IDs")
    if eligible is not None and not set(values) <= eligible:
        raise ValueError(f"{name} contains ineligible IDs")
    return tuple(sorted(values))


def _limit(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def search_area_candidates(
    candidate_ids,
    scenario_ids,
    evaluate,
    cleared_area,
    *,
    problem_sha256,
    plans=None,
    plan_sources=None,
    loss_metrics=(),
    max_new_checks=None,
    cache=None,
    max_full_subsets=65_536,
    max_wall_time_s=None,
    previous_new_checks=0,
    previous_wall_time_s=0.0,
):
    """Evaluate plans by actual cleared area and retain nondominated outcomes.

    ``evaluate(ids, scenario_id)`` returns ``ProtectionCheck``. Every scenario
    must pass before a plan enters the feasible archive. For each requested
    loss metric, passed checks provide a finite nonnegative value under
    ``check.evidence['loss_metrics'][name]``. A plan's loss vector is the
    componentwise maximum across all declared scenarios (worst case), with
    cleared area as its first objective. Failure can stop feasibility checking
    early; it does not provide a complete loss estimate for a failed plan.

    ``plans=None`` enumerates all subsets only if their count is at most
    ``max_full_subsets``. Explicit plans are a finite proposal study, even if
    every proposal has been evaluated: full-candidate optimality is not claimed.
    Area is computed for each complete plan before sorting, without assuming a
    lower bound from the areas of its members. Equal-area plans are all eligible
    for evaluation, regardless of their building counts.

    Cache keys bind the caller's frozen physical, action, scenario and metric
    problem fingerprint. Cached unknowns remain unknown; remove or replace
    those cache entries explicitly when their underlying computation is repaired.
    Budgets count new oracle calls, including calls returning unknown. Resume
    callers can supply previous usage so limits cannot silently reset. A call
    already started may finish beyond the wall-time limit. Exceptions propagate.
    """
    started = perf_counter()
    candidates = _ids(candidate_ids, name="candidate_ids")
    eligible = set(candidates)
    if isinstance(scenario_ids, (str, bytes)):
        raise ValueError("nonempty unique scenario IDs required")
    scenarios = tuple(scenario_ids)
    if (
        not scenarios
        or any(not isinstance(s, str) or not s for s in scenarios)
        or len(set(scenarios)) != len(scenarios)
    ):
        raise ValueError("nonempty unique scenario IDs required")
    if not isinstance(problem_sha256, str) or not problem_sha256:
        raise ValueError("frozen problem fingerprint required")
    if isinstance(loss_metrics, (str, bytes)):
        raise ValueError("loss metrics must be unique nonempty names")
    metrics = tuple(loss_metrics)
    if any(not isinstance(m, str) or not m for m in metrics) or len(set(metrics)) != len(metrics):
        raise ValueError("loss metrics must be unique nonempty names")
    _limit(max_new_checks, "check budget")
    _limit(max_full_subsets, "full enumeration limit")
    if max_full_subsets is None:
        raise ValueError("full enumeration limit must be a nonnegative integer")
    _limit(previous_new_checks, "previous check usage")
    if previous_new_checks is None:
        raise ValueError("previous check usage must be a nonnegative integer")
    prior_time = _nonnegative(previous_wall_time_s, "previous wall time")
    if max_wall_time_s is not None:
        max_wall_time_s = _nonnegative(max_wall_time_s, "wall time budget")

    full_count = 1 << len(candidates)
    full_scope = plans is None
    if full_scope:
        if full_count > max_full_subsets:
            raise ValueError("full powerset exceeds enumeration limit; supply explicit plans")
        plans = (
            ids for count in range(len(candidates) + 1) for ids in combinations(candidates, count)
        )
    declared = {_ids(ids, name="plan", eligible=eligible) for ids in plans}
    sources = {}
    for ids, source in ({} if plan_sources is None else plan_sources).items():
        ids = _ids(ids, name="plan source", eligible=eligible)
        if ids not in declared or not isinstance(source, str) or not source:
            raise ValueError("plan sources require a declared plan and a nonempty source")
        sources[ids] = source

    # Full-set values are authoritative. In particular, sum(singleton areas)
    # is neither substituted for this value nor used to prune supersets.
    ordered = sorted(
        ((_nonnegative(cleared_area(ids), "cleared area"), ids) for ids in declared),
        key=lambda item: (item[0], item[1]),
    )
    cache = {} if cache is None else cache
    records, feasible, frontier = [], [], []
    new_checks = cache_hits = 0
    stop_reasons = set()

    def budget_reason():
        if max_new_checks is not None and previous_new_checks + new_checks >= max_new_checks:
            return "check_limit"
        if max_wall_time_s is not None and prior_time + perf_counter() - started >= max_wall_time_s:
            return "wall_time_limit"
        return None

    def dominates(left, right):
        return all(a <= b for a, b in zip(left, right, strict=True)) and any(
            a < b for a, b in zip(left, right, strict=True)
        )

    for area, ids in ordered:
        checks = []
        observed_losses = []
        missing = []
        status = "passed"
        saw_unknown = False
        for index, scenario in enumerate(scenarios):
            key = (problem_sha256, ids, scenario)
            if key in cache:
                check = cache[key]
                cache_hits += 1
            else:
                reason = budget_reason()
                if reason is not None:
                    stop_reasons.add(reason)
                    missing.extend(scenarios[index:])
                    status = "unknown" if checks else "not_evaluated"
                    break
                check = evaluate(ids, scenario)
                if not isinstance(check, ProtectionCheck):
                    raise TypeError("oracle must return ProtectionCheck")
                cache[key] = check
                new_checks += 1
            if not isinstance(check, ProtectionCheck):
                raise TypeError("cached oracle result must be ProtectionCheck")
            checks.append({"scenario_id": scenario, **asdict(check)})
            if check.status == "failed":
                status = "failed"
                missing.extend(scenarios[index + 1 :])
                break
            if check.status == "unknown":
                saw_unknown = True
                continue
            losses = check.evidence.get("loss_metrics", {})
            if any(name not in losses for name in metrics):
                raise ValueError("passed oracle check is missing declared loss metrics")
            observed_losses.append(
                tuple(_nonnegative(losses[name], f"loss metric {name}") for name in metrics)
            )
        if saw_unknown and status == "passed":
            status = "unknown"
        row = {
            "demolished_ids": list(ids),
            "demolished_count": len(ids),
            "cleared_area_m2": area,
            "plan_source": sources.get(ids),
            "status": status,
            "checks": checks,
            "not_checked_scenario_ids": missing,
            "objective_values": None,
            "worst_case_losses": None,
        }
        if status == "passed":
            losses = tuple(
                max(values[i] for values in observed_losses) for i in range(len(metrics))
            )
            objectives = (area, *losses)
            row["objective_values"] = list(objectives)
            row["worst_case_losses"] = dict(zip(metrics, losses, strict=True))
            feasible.append(row)
            if not any(dominates(other["objective_values"], objectives) for other in frontier):
                frontier = [
                    other
                    for other in frontier
                    if not dominates(objectives, other["objective_values"])
                ]
                frontier.append(row)
        records.append(row)

    feasible.sort(key=lambda row: (row["objective_values"], row["demolished_ids"]))
    frontier.sort(key=lambda row: (row["objective_values"], row["demolished_ids"]))
    best = feasible[0] if feasible else None
    unresolved = [row for row in records if row["status"] in {"unknown", "not_evaluated"}]
    scope_complete = not unresolved
    blockers = [
        row
        for row in unresolved
        if best is None or row["cleared_area_m2"] < best["cleared_area_m2"]
    ]
    area_proven = best is not None and not blockers
    possible = [row for row in records if row["status"] != "failed"]
    scope_lower = min((row["cleared_area_m2"] for row in possible), default=None)
    global_area_proven = full_scope and area_proven
    status = (
        "complete"
        if scope_complete and best is not None
        else "infeasible_in_scope"
        if scope_complete
        else "incomplete"
    )
    return {
        "schema_version": "heritage-area-candidate-search-1",
        "problem_sha256": problem_sha256,
        "status": status,
        "search_scope": "full_powerset" if full_scope else "declared_plans",
        "candidate_ids": list(candidates),
        "full_candidate_count": len(candidates),
        "full_subset_count": full_count,
        "declared_plan_count": len(records),
        "subsets_outside_scope": 0 if full_scope else full_count - len(records),
        "scenario_ids": list(scenarios),
        "objective_names": ["cleared_area_m2", *metrics],
        "loss_aggregation": "componentwise_maximum_over_all_declared_scenarios",
        "selection_policy": "minimum_area_then_declared_losses_for_ties;pareto_archive_separate",
        "area_policy": (
            "caller_supplied_complete_set_function;no_additivity_or_monotonicity_assumed"
        ),
        "cardinality_pruned_combinations": 0,
        "scope_complete": scope_complete,
        "full_candidate_search_complete": full_scope and scope_complete,
        "minimum_area_proven_within_scope": area_proven,
        "global_minimum_area_proven": global_area_proven,
        "selected_tie_break_objective_proven_within_scope": best is not None
        and not any(row["cleared_area_m2"] <= best["cleared_area_m2"] for row in unresolved),
        "pareto_frontier_complete_within_scope": scope_complete,
        "global_pareto_frontier_complete": full_scope and scope_complete,
        "full_candidate_space_unresolved": not full_scope or not scope_complete,
        "minimum_area_lower_bound_within_scope_m2": scope_lower,
        "global_minimum_area_lower_bound_m2": scope_lower if full_scope else 0.0,
        "best_ids": None if best is None else best["demolished_ids"],
        "best_area_m2": None if best is None else best["cleared_area_m2"],
        "best_objective_values": None if best is None else best["objective_values"],
        "minimum_area_proof_blockers": [row["demolished_ids"] for row in blockers],
        "unresolved_plans": [row["demolished_ids"] for row in unresolved],
        "feasible_plan_count": len(feasible),
        "pareto_frontier": frontier,
        "new_checks": new_checks,
        "previous_new_checks": previous_new_checks,
        "cumulative_new_checks": previous_new_checks + new_checks,
        "cache_hits": cache_hits,
        "budget_exhausted": bool(stop_reasons),
        "budget_stop_reasons": sorted(stop_reasons),
        "cumulative_wall_time_s": prior_time + perf_counter() - started,
        "trace": records,
    }
