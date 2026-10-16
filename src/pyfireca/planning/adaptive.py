"""Seeded, variable-topology corridor search with an on-demand CA oracle.

This is a bounded derivative-free beam/pattern search, not a global optimizer.
Training effectiveness is lexicographic. Area is an optional *last* objective,
never a substitute for a loss of protection, and money is never used.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from math import cos, hypot, radians, sin

from shapely.geometry import LineString, box

from pyfireca.planning.contracts import require_number
from pyfireca.planning.effectiveness import effectiveness_score
from pyfireca.planning.interventions.clearing import (
    ClearingPath,
    ClearingPlan,
    InterventionConflict,
    clearing_footprint,
)


@dataclass(frozen=True, slots=True)
class CorridorSearchConfig:
    seed: int = 20261016
    max_evaluations: int = 64
    batch_size: int = 12
    beam_size: int = 6
    max_paths: int = 3
    max_vertices: int = 5
    width_min_m: float = 2.5
    width_max_m: float = 30.0
    length_min_m: float = 10.0
    length_max_m: float = 240.0
    initial_step_m: float = 20.0
    min_step_m: float = 2.5
    objective: str = "effectiveness_then_area"

    def __post_init__(self):
        for name in ("max_evaluations", "batch_size", "beam_size", "max_paths", "max_vertices"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_vertices < 2:
            raise ValueError("max_vertices must be at least two")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        for name in (
            "width_min_m",
            "width_max_m",
            "length_min_m",
            "length_max_m",
            "initial_step_m",
            "min_step_m",
        ):
            require_number(name, getattr(self, name), positive=True)
        if (
            self.width_min_m > self.width_max_m
            or self.length_min_m > self.length_max_m
            or self.min_step_m > self.initial_step_m
        ):
            raise ValueError("invalid search ranges")
        if self.objective not in ("effectiveness_only", "effectiveness_then_area"):
            raise ValueError("unsupported objective")


class SearchRejected(InterventionConflict):
    """An expected physical/domain conflict, never a hidden solver failure."""


def canonical_plan(zone_id, paths):
    """Remove duplicate/reversed paths and order-independent permutations."""
    normalized = {}
    for path in paths:
        points = tuple((round(x, 6), round(y, 6)) for x, y in path.points_m)
        points = min(points, points[::-1])
        width = round(path.width_m, 6)
        normalized[(points, width)] = ClearingPath(points, width)
    keys = sorted(normalized)
    payload = json.dumps([zone_id, keys], separators=(",", ":"))
    identity = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return ClearingPlan(
        f"{zone_id}-adaptive-{identity}", (), zone_id, tuple(normalized[k] for k in keys)
    )


def paths_from_plan(plan):
    return (*plan.paths, *(ClearingPath((s.start_m, s.end_m), s.width_m) for s in plan.strips))


def rank_key(record, objective):
    score = tuple(record["effectiveness"])
    return (*score, record["cleared_area_m2"]) if objective == "effectiveness_then_area" else score


def pareto_records(records):
    """Nondominated lexicographic protection versus actual cleared ground area."""
    feasible = [r for r in records if r["status"] == "ok"]
    return [
        r
        for r in feasible
        if not any(
            tuple(q["effectiveness"]) <= tuple(r["effectiveness"])
            and q["cleared_area_m2"] <= r["cleared_area_m2"]
            and (
                tuple(q["effectiveness"]) < tuple(r["effectiveness"])
                or q["cleared_area_m2"] < r["cleared_area_m2"]
            )
            for q in feasible
        )
    ]


def _proposal(paths, other, operation, step, rng, config, anchors):
    """Position, angle, width, bends, endpoints, add/remove and recombination."""
    values = list(paths)
    if not values or operation == "restart":
        x, y = rng.choice(anchors)
        angle = rng.uniform(-90, 90)
        length = rng.uniform(config.length_min_m, min(150, config.length_max_m))
        dx, dy = cos(radians(angle)) * length / 2, sin(radians(angle)) * length / 2
        return (
            ClearingPath(
                ((x - dx, y - dy), (x + dx, y + dy)),
                rng.uniform(config.width_min_m, config.width_max_m),
            ),
        )
    index = rng.randrange(len(values))
    path = values[index]
    points, width = list(path.points_m), path.width_m
    sign = rng.choice((-1, 1))
    if operation == "translate":
        dx, dy = rng.choice(((step * sign, 0), (0, step * sign)))
        points = [(x + dx, y + dy) for x, y in points]
    elif operation == "rotate":
        cx, cy = (sum(p[k] for p in points) / len(points) for k in (0, 1))
        theta = radians(sign * max(2.5, min(30, step)))
        points = [
            (
                cx + cos(theta) * (x - cx) - sin(theta) * (y - cy),
                cy + sin(theta) * (x - cx) + cos(theta) * (y - cy),
            )
            for x, y in points
        ]
    elif operation == "width":
        width = min(config.width_max_m, max(config.width_min_m, width + sign * step / 2))
    elif operation == "vertex":
        j = rng.randrange(len(points))
        dx, dy = rng.choice(((step * sign, 0), (0, step * sign)))
        points[j] = (points[j][0] + dx, points[j][1] + dy)
    elif operation == "bend":
        if len(points) >= config.max_vertices:
            j = rng.randrange(1, len(points) - 1)
            points[j] = (points[j][0] + sign * step, points[j][1])
        else:
            j = rng.randrange(len(points) - 1)
            a, b = points[j], points[j + 1]
            length = hypot(b[0] - a[0], b[1] - a[1])
            points.insert(
                j + 1,
                (
                    (a[0] + b[0]) / 2 - (b[1] - a[1]) / length * sign * step,
                    (a[1] + b[1]) / 2 + (b[0] - a[0]) / length * sign * step,
                ),
            )
    elif operation == "add":
        if len(values) >= config.max_paths:
            return _proposal(paths, other, "vertex", step, rng, config, anchors)
        # Both parallel auxiliary bands and independent routes are explored.
        if rng.random() < 0.5:
            values.append(ClearingPath(tuple((x, y + sign * step) for x, y in points), width))
        else:
            values.extend(_proposal((), (), "restart", step, rng, config, anchors))
        return tuple(values)
    elif operation == "remove":
        if len(points) > 2:
            del points[rng.randrange(1, len(points) - 1)]
        else:
            del values[index]
            return tuple(values)
    elif operation == "combine":
        pool = list(values) + list(other)
        rng.shuffle(pool)
        return tuple(pool[: config.max_paths])
    elif operation == "endpoint":
        j, k = rng.choice(((0, 1), (-1, -2)))
        dx, dy = points[j][0] - points[k][0], points[j][1] - points[k][1]
        length = hypot(dx, dy)
        points[j] = (
            points[j][0] + sign * step * dx / length,
            points[j][1] + sign * step * dy / length,
        )
    else:
        raise ValueError("unknown mutation")
    values[index] = ClearingPath(tuple(points), width)
    return tuple(values)


def search_corridors(*, zone_id, bounds, anchors, seeds, evaluate, config=None, progress=None):
    """Run deterministic replayable search. Only the training oracle is visible.

    Cache persistence belongs to the oracle. Geometry rejections are reported;
    unexpected solver errors propagate. A cap limits evaluated unique plans,
    not cleared area. A geometric/attempt cap bounds the search even on plateaus.
    """
    config = config or CorridorSearchConfig()
    if len(bounds) != 4 or not box(*bounds).is_valid or not box(*bounds).area > 0:
        raise ValueError("nonempty metric bounds are required")
    if not anchors:
        raise ValueError("search requires eligible anchor points")
    rng = random.Random(config.seed)
    extent = box(*bounds)
    records, plans, seen, history = [], {}, set(), []
    proposals, invalid, stagnation = 0, 0, 0
    step = config.initial_step_m

    def visit(paths, operator, generation):
        nonlocal proposals, invalid
        proposals += 1
        try:
            plan = canonical_plan(zone_id, paths)
            if len(plan.paths) > config.max_paths:
                raise ValueError("too many paths")
            for path in plan.paths:
                length = LineString(path.points_m).length
                if (
                    len(path.points_m) > config.max_vertices
                    or not config.width_min_m <= path.width_m <= config.width_max_m
                    or not config.length_min_m <= length <= config.length_max_m
                ):
                    raise ValueError("path outside configured geometry bounds")
            if plan.paths and not extent.covers(clearing_footprint(plan)):
                raise ValueError("corridor outside search envelope")
        except ValueError:
            invalid += 1
            return
        if plan.plan_id in seen or len(records) >= config.max_evaluations:
            return
        seen.add(plan.plan_id)
        plans[plan.plan_id] = plan
        record = {
            "plan_id": plan.plan_id,
            "plan": plan.to_mapping(),
            "operator": operator,
            "generation": generation,
            "query_index": len(records) + 1,
        }
        try:
            evaluations = evaluate(plan)
            area = evaluations[0].intervention["cleared_area_m2"]
            if any(abs(e.intervention["cleared_area_m2"] - area) > 1e-6 for e in evaluations):
                raise ValueError("clearance must be identical across training scenarios")
            require_number("cleared area", area)
            record.update(
                status="ok",
                effectiveness=list(effectiveness_score(evaluations)),
                cleared_area_m2=area,
                training_metrics=[e.metrics for e in evaluations],
            )
        except InterventionConflict as exc:
            record.update(status="rejected", reason=str(exc))
        records.append(record)
        if progress:
            progress({"event": "evaluation", **record})

    visit((), "baseline", 0)
    for seed in seeds:
        visit(paths_from_plan(seed), "seed", 0)
    operations = (
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
    generation, op_index = 0, 0
    stop_reason = "evaluation_limit"
    while len(records) < config.max_evaluations:
        feasible = [r for r in records if r["status"] == "ok"]
        if not feasible:
            stop_reason = "no_feasible_start"
            break
        ordered = sorted(feasible, key=lambda r: (rank_key(r, config.objective), r["plan_id"]))
        beam = ordered[: max(1, config.beam_size // 2)]
        for record in sorted(pareto_records(feasible), key=lambda r: r["plan_id"]) + ordered:
            if record not in beam:
                beam.append(record)
            if len(beam) >= config.beam_size:
                break
        before = rank_key(ordered[0], config.objective)
        generation += 1
        count = len(records)
        attempts = 0
        while len(records) < min(count + config.batch_size, config.max_evaluations):
            if attempts >= config.batch_size * 40:
                break
            parent = plans[rng.choice(beam)["plan_id"]]
            other = plans[rng.choice(beam)["plan_id"]]
            operation = operations[op_index % len(operations)]
            op_index += 1
            attempts += 1
            try:
                paths = _proposal(parent.paths, other.paths, operation, step, rng, config, anchors)
            except ValueError:
                invalid += 1
                continue
            visit(paths, operation, generation)
        feasible = [r for r in records if r["status"] == "ok"]
        best = min(feasible, key=lambda r: (rank_key(r, config.objective), r["plan_id"]))
        stagnation = stagnation + 1 if rank_key(best, config.objective) >= before else 0
        history.append(
            {
                "generation": generation,
                "evaluations": len(records),
                "step_m": step,
                "best_plan_id": best["plan_id"],
                "best_effectiveness": best["effectiveness"],
                "best_cleared_area_m2": best["cleared_area_m2"],
            }
        )
        if progress:
            progress({"event": "generation", **history[-1]})
        if len(records) == count:
            stop_reason = "proposal_exhaustion"
            break
        if stagnation >= 2:
            step = max(config.min_step_m, step / 2)
            stagnation = 0
    feasible = [r for r in records if r["status"] == "ok"]
    if not feasible:
        raise ValueError("search produced no valid evaluations")
    best_key = min(rank_key(r, config.objective) for r in feasible)
    ties = sorted(r["plan_id"] for r in feasible if rank_key(r, config.objective) == best_key)
    best_effect = min(tuple(r["effectiveness"]) for r in feasible)
    return {
        "schema": "adaptive-corridor-search-1",
        "zone_id": zone_id,
        "config": asdict(config),
        "bounds_m": bounds,
        "records": records,
        "history": history,
        "selected_ids": ties,
        "representative": ties[0],
        "best_key": best_key,
        "effectiveness_tied_ids": sorted(
            r["plan_id"] for r in feasible if tuple(r["effectiveness"]) == best_effect
        ),
        "pareto_ids": sorted(r["plan_id"] for r in pareto_records(feasible)),
        "stop_reason": stop_reason,
        "proposals": proposals,
        "geometric_rejections": invalid,
        "global_optimality_guaranteed": False,
    }
