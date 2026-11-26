"""Village-neutral minimum-intervention decision layer over a physical oracle."""

from __future__ import annotations

from math import fsum

from pyfireca.planning.building_search import exact_demolition_search
from pyfireca.planning.heritage import content_hash


def plan_heritage_strategy(
    inventory,
    scenario_ids,
    evaluate,
    *,
    physical_problem_sha256,
    max_new_checks,
    candidate_ids=None,
    initial_ids=None,
    declared_cost_by_id=None,
    cost_unit=None,
    cache=None,
    prioritize_failures=True,
    max_wall_time_s=None,
):
    """Protect every declared scenario, then minimize count and additive cost.

    The caller supplies frozen physical/scenario conditions and the authoritative
    protection oracle. Without declared costs, the second objective is nominal
    footprint area; this is never labelled monetary savings. The method contains
    no village, core-role or particular building identifiers.
    """
    candidates = tuple(sorted(inventory.candidate_ids if candidate_ids is None else candidate_ids))
    scenarios = tuple(scenario_ids)
    if len(set(candidates)) != len(candidates) or not set(candidates) <= set(
        inventory.candidate_ids
    ):
        raise ValueError("strategy candidates must be unique eligible inventory buildings")
    if not inventory.targets or not physical_problem_sha256:
        raise ValueError("protected targets and a frozen physical problem are required")
    areas = {i: float(inventory.by_id[i].geometry.area) for i in candidates}
    if declared_cost_by_id is None:
        if cost_unit is not None:
            raise ValueError("a declared cost unit requires explicit building costs")
        weights, secondary, unit = areas, "nominal_footprint_area", "m2"
    else:
        if (
            set(declared_cost_by_id) != set(candidates)
            or not isinstance(cost_unit, str)
            or not cost_unit.strip()
        ):
            raise ValueError("declared costs must cover every candidate and have an explicit unit")
        weights, secondary, unit = dict(declared_cost_by_id), "declared_demolition_cost", cost_unit
    problem = {
        "schema_version": "heritage-strategy-objective-1",
        "physical_problem_sha256": physical_problem_sha256,
        "inventory_sha256": inventory.sha256,
        "candidate_ids": candidates,
        "scenario_ids": scenarios,
        "nominal_areas_m2": areas,
        "primary_objective": "demolished_building_count",
        "secondary_objective": secondary,
        "secondary_unit": unit,
        "secondary_values": weights,
    }
    signature = content_hash(problem)
    result = exact_demolition_search(
        weights,
        scenarios,
        evaluate,
        problem_sha256=signature,
        initial_ids=initial_ids,
        max_new_checks=max_new_checks,
        cache=cache,
        prioritize_failures=prioritize_failures,
        max_wall_time_s=max_wall_time_s,
    )
    result["objective_policy"] = f"lexicographic_count_then_{secondary}"
    for layer in result["layers"]:
        layer["secondary_bound_pruned_prefixes"] = layer.pop("area_pruned_prefixes")
        layer["secondary_bound_pruned_combinations"] = layer.pop("area_pruned_combinations")
    best = result["best_ids"]
    result.update(
        strategy_problem=problem,
        physical_problem_sha256=physical_problem_sha256,
        core_target_ids=sorted(t.building_id for t in inventory.targets),
        secondary_unit=unit,
        best_nominal_area_m2=None if best is None else fsum(areas[i] for i in best),
        best_declared_cost=None
        if best is None or declared_cost_by_id is None
        else fsum(weights[i] for i in best),
        declared_cost_provided=declared_cost_by_id is not None,
    )
    return result
