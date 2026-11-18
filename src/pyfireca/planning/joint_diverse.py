"""Allocation-stratified beam search for scoped inner/outer fuel treatments.

Each allocation stratum retains parents even when a single-side solution leads
the global ranking. Simultaneous width exchange and component recombination
allow substitutions without requiring every intermediate move to improve.
"""

from __future__ import annotations

import random
from dataclasses import replace
from math import isfinite

import numpy as np
from shapely.geometry import LineString, box

from pyfireca.planning.adaptive import (
    _elite_poll,
    _proposal,
    canonical_plan,
    matches_family,
    paths_from_plan,
)
from pyfireca.planning.contracts import require_number
from pyfireca.planning.effectiveness import joint_protection_score
from pyfireca.planning.interventions.clearing import (
    ClearingPlan,
    InterventionConflict,
    clearing_footprint,
)
from pyfireca.planning.joint import REGIONS, combined_seed


def allocation_stratum(areas):
    outer, inner = (areas[r] for r in REGIONS)
    if not all(isfinite(x) for x in (outer, inner)) or min(outer, inner) < -1e-8:
        raise ValueError("negative regional clearance")
    total = outer + inner
    if total <= 1e-8:
        return "none"
    if outer <= 1e-8:
        return "inner_only"
    if inner <= 1e-8:
        return "outer_only"
    return f"mixed_{min(3, int(4 * outer / total))}"


def allocation_beam(records, per_stratum=2):
    groups = {}
    for row in records:
        if row["status"] == "ok":
            groups.setdefault(row["allocation_stratum"], []).append(row)

    def key(r):
        return tuple(r["effectiveness"]), r["cleared_area_m2"], r["plan_id"]

    return [r for group in sorted(groups) for r in sorted(groups[group], key=key)[:per_stratum]]


def optimize_diverse_joint(
    *, engine, seeds, evaluate, config, scenario_ids, progress=None, area_cap_m2=None, per_stratum=2
):
    if config.objective != "joint_protection_then_area":
        raise ValueError("joint protection objective required")
    if not scenario_ids or len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("explicit unique design scenario IDs required")
    if area_cap_m2 is not None:
        require_number("area cap", area_cap_m2)
    if per_stratum < 1:
        raise ValueError("positive beam capacity required")
    rng = random.Random(config.seed)
    grid = engine.bundle.request.grid
    extent = box(
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    anchors = {}
    for r in REGIONS:
        mask = (
            engine.zones[r]
            & engine.bundle.request.burnable_mask
            & (engine.bundle.request.building_index < 0)
        )
        anchors[r] = tuple(grid.center(int(i), int(j)) for i, j in np.argwhere(mask))
    records = []
    plans = {}
    seen = set()
    attempts = 0
    geometry_rejections = 0

    def visit(plan, operation, generation):
        nonlocal attempts, geometry_rejections
        attempts += 1
        if plan.plan_id in seen or len(records) >= config.max_evaluations:
            return
        try:
            if len(plan.paths) > config.max_paths:
                raise ValueError("too many paths")
            proxy = ClearingPlan("geometry", paths=plan.paths)
            if plan.paths and not matches_family(proxy, config.geometry_family):
                raise ValueError("geometry family mismatch")
            for p in plan.paths:
                if (
                    len(p.points_m) > config.max_vertices
                    or not config.width_min_m <= p.width_m <= config.width_max_m
                    or not config.length_min_m
                    <= LineString(p.points_m).length
                    <= config.length_max_m
                ):
                    raise ValueError("geometry outside configured limits")
            if plan.paths and not extent.covers(clearing_footprint(proxy)):
                raise ValueError("geometry outside study envelope")
        except ValueError:
            geometry_rejections += 1
            return
        seen.add(plan.plan_id)
        row = {
            "plan_id": plan.plan_id,
            "plan": plan.to_mapping(),
            "operator": operation,
            "generation": generation,
            "query_index": len(records) + 1,
        }
        try:
            # Source and footprint preflight belong to the supplied oracle.
            results = evaluate(plan)
            ids = tuple(r.scenario_id for r in results)
            if not ids or ids != tuple(scenario_ids[: len(ids)]):
                raise ValueError("oracle must return an ordered design-scenario prefix")
            complete = len(ids) == len(scenario_ids) and all(
                r.diagnostics.get("reached_requested_horizon", False) for r in results
            )
            loss = any(
                r.metrics["ignited_house_count"] + r.metrics["ignited_bridge_count"] > 0
                for r in results
            )
            if not complete and not loss:
                raise ValueError("incomplete zero-loss search evaluation")
            area = results[0].intervention["cleared_area_m2"]
            if any(abs(r.intervention["cleared_area_m2"] - area) > 1e-6 for r in results):
                raise ValueError("clearance changed across scenarios")
            if area_cap_m2 is not None and area > area_cap_m2 + 1e-7:
                raise InterventionConflict("actual footprint exceeds declared area cap")
            areas = results[0].intervention["zone_areas_m2"]
            row.update(
                status="ok",
                effectiveness=list(joint_protection_score(results)),
                cleared_area_m2=area,
                zone_areas_m2=areas,
                allocation_stratum=allocation_stratum(areas),
                complete_design_evaluation=complete,
                protection_goal_met=complete and not loss,
                losses_are_lower_bounds=not complete,
                training_metrics=[r.metrics for r in results],
            )
            plans[plan.plan_id] = plan
        except InterventionConflict as error:
            row.update(status="rejected", reason=str(error))
        records.append(row)
        if progress:
            progress({"event": "evaluation", **row})

    for seed in seeds:
        visit(seed, "allocation_seed", 0)
        if len(records) >= config.max_evaluations:
            break
    operations = (
        "width_exchange",
        "recombine",
        "elite",
        "translate",
        "rotate",
        "width",
        "vertex",
        "bend",
        "add",
        "remove",
        "combine",
        "endpoint",
        "restart",
    )
    generation = 0
    while (
        len(records) < config.max_evaluations
        and attempts < config.max_evaluations * 60
        and generation < config.max_evaluations * 60
    ):
        beam = allocation_beam(records, per_stratum)
        if not beam:
            break
        generation += 1
        # Round-robin strata, rather than always mutating the global incumbent.
        parent = plans[beam[(generation - 1) % len(beam)]["plan_id"]]
        other = plans[rng.choice(beam)["plan_id"]]
        operation = operations[((generation - 1) // len(beam)) % len(operations)]
        step = max(
            config.min_step_m,
            config.initial_step_m / (2 ** min(30, (generation - 1) // max(1, 26 * len(beam)))),
        )
        components = {p.zone_id: p for p in parent.components}
        try:
            if operation == "recombine":
                parts = [p for p in parent.components if p.zone_id == REGIONS[0]]
                parts.extend(p for p in other.components if p.zone_id == REGIONS[1])
            elif operation == "width_exchange" and len(components) == 2:
                sign = (-1, 1)[generation % 2]
                parts = [
                    canonical_plan(
                        r,
                        tuple(
                            replace(
                                p,
                                width_m=min(
                                    config.width_max_m,
                                    max(
                                        config.width_min_m,
                                        p.width_m
                                        + sign * (1 if r == REGIONS[0] else -1) * step / 2,
                                    ),
                                ),
                            )
                            for p in paths_from_plan(components[r])
                        ),
                    )
                    for r in REGIONS
                ]
            else:
                region = REGIONS[generation % 2]
                fixed = tuple(p for p in parent.components if p.zone_id != region)
                remaining = config.max_paths - sum(len(p.paths) for p in fixed)
                if remaining <= 0 or not anchors[region]:
                    continue
                paths = paths_from_plan(components[region]) if region in components else ()
                mate = next(
                    (paths_from_plan(p) for p in other.components if p.zone_id == region), ()
                )
                regional = replace(config, max_paths=remaining)
                if operation == "elite" and paths:
                    polls = [p for p, _op in _elite_poll(paths, step, regional) if p is not None]
                    candidate = rng.choice(polls) if polls else paths
                else:
                    op = operation if operation not in {"width_exchange", "elite"} else "width"
                    candidate = _proposal(paths, mate, op, step, rng, regional, anchors[region])
                parts = (*fixed, canonical_plan(region, candidate))
            candidate_plan = combined_seed(parts)
        except ValueError:
            geometry_rejections += 1
            attempts += 1
            continue
        visit(candidate_plan, operation, generation)
    valid = [r for r in records if r["status"] == "ok" and r["protection_goal_met"]]
    selected = min(valid, key=lambda r: (r["cleared_area_m2"], r["plan_id"])) if valid else None
    return {
        "method": "allocation_stratified_joint_beam",
        "records": records,
        "selected": selected,
        "unique_requests": len(records),
        "budget_limit": config.max_evaluations,
        "proposals": attempts,
        "geometry_rejections": geometry_rejections,
        "retained_strata": sorted(
            {r["allocation_stratum"] for r in allocation_beam(records, per_stratum)}
        ),
        "global_optimality_guaranteed": False,
        "area_cap_m2": area_cap_m2,
    }
