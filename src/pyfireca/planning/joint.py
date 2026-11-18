"""Scoped interventions: retain each domain, union once, simulate once."""

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass, replace

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union

from pyfireca.planning.adaptive import canonical_plan, paths_from_plan, search_corridors
from pyfireca.planning.effectiveness import joint_protection_score
from pyfireca.planning.engines.village import VillageEvaluationEngine
from pyfireca.planning.interventions.clearing import (
    ClearingPlan,
    InterventionConflict,
    _prepare_clearing_footprint,
    clearing_plan_from_mapping,
)

REGIONS = ("outer_proxy", "inner_proxy")


@dataclass(frozen=True, slots=True)
class JointClearingPlan:
    plan_id: str
    components: tuple[ClearingPlan, ...]
    zone_id: str = "joint"

    def __post_init__(self):
        if self.zone_id != "joint" or not isinstance(self.components, tuple):
            raise ValueError("joint components must be an immutable tuple")
        if any(
            not isinstance(p, ClearingPlan) or p.zone_id not in REGIONS for p in self.components
        ):
            raise ValueError("joint components require explicit outer/inner scope")
        if len({p.zone_id for p in self.components}) != len(self.components):
            raise ValueError("one canonical component per region required")

    @property
    def paths(self):
        return tuple(path for p in self.components for path in paths_from_plan(p))

    def to_mapping(self):
        return {
            "schema": "joint-scoped-clearing-1",
            "plan_id": self.plan_id,
            "zone_id": "joint",
            "components": [p.to_mapping() for p in self.components],
        }


def combined_seed(plans):
    grouped = {region: [] for region in REGIONS}
    for p in plans:
        if p.zone_id not in REGIONS:
            raise ValueError("component lacks regional scope")
        grouped[p.zone_id].extend(paths_from_plan(p))
    parts = tuple(canonical_plan(r, grouped[r]) for r in REGIONS if grouped[r])
    identity = hashlib.sha256(
        json.dumps([p.to_mapping() for p in parts], sort_keys=True).encode()
    ).hexdigest()[:20]
    return JointClearingPlan("joint-scoped-" + identity, parts)


def plan_from_mapping(raw):
    if raw.get("schema") == "joint-scoped-clearing-1":
        plan = combined_seed(tuple(clearing_plan_from_mapping(p) for p in raw["components"]))
        if plan.plan_id != raw["plan_id"]:
            raise ValueError("joint plan identity mismatch")
        return plan
    return clearing_plan_from_mapping(raw)


class JointVillageEvaluationEngine(VillageEvaluationEngine):
    """One physical request, retaining original masks for atomic components."""

    def __init__(self, bundle, transport, *, outer_mask, inner_mask, **kwargs):
        masks = [np.asarray(m) for m in (outer_mask, inner_mask)]
        if any(m.dtype != np.bool_ or m.shape != bundle.request.grid.shape for m in masks):
            raise ValueError("joint domains require matching boolean masks")
        if np.any(masks[0] & masks[1]):
            raise ValueError("joint domains must not overlap")
        if not all(m.any() for m in masks):
            raise ValueError("both domains must be nonempty")
        self.domain_identity = hashlib.sha256(masks[0].tobytes() + masks[1].tobytes()).hexdigest()
        version = kwargs.pop("code_version", "unspecified") + ":domains:" + self.domain_identity
        super().__init__(
            bundle,
            transport,
            zones={REGIONS[0]: masks[0], REGIONS[1]: masks[1], "joint": masks[0] | masks[1]},
            code_version=version,
            **kwargs,
        )
        self.capabilities = {**self.capabilities, "joint_outer_inner_clearance": True}

    def prepare(self, scenario, intervention):
        if isinstance(intervention, JointClearingPlan):
            base, _, _ = super().prepare(scenario, ClearingPlan("joint-empty", zone_id="joint"))
            parts = [
                super(JointVillageEvaluationEngine, self).prepare(scenario, p)[2]
                for p in intervention.components
            ]
            footprint = unary_union([shape(p["effective_footprint"]) for p in parts])
            request, fraction, summary = _prepare_clearing_footprint(
                base,
                intervention,
                self.transport,
                footprint,
                allowed_mask=self.zones["joint"],
                ignitions=base.scenario.ignitions,
            )
            summary["scoped_components"] = parts
        else:
            request, fraction, summary = super().prepare(scenario, intervention)
        areas = {
            r: float(fraction[self.zones[r]].sum() * request.grid.cell_area_m2) for r in REGIONS
        }
        if abs(sum(areas.values()) - summary["cleared_area_m2"]) > 1e-7:
            raise AssertionError("joint area attribution does not close")
        summary.update(
            zone_areas_m2=areas,
            configuration=(
                "both"
                if all(a > 1e-8 for a in areas.values())
                else "outer_only"
                if areas[REGIONS[0]] > 1e-8
                else "inner_only"
                if areas[REGIONS[1]] > 1e-8
                else "none"
            ),
            domain_partition_sha256=self.domain_identity,
            simultaneous_single_run=True,
            corridor_evidence="fuel_reduction_not_verified_building_compartment",
        )
        return request, fraction, summary


def joint_initial_plans(outer, inner):
    plans = [combined_seed(()), combined_seed((outer,)), combined_seed((inner,))]
    for a, b in ((1, 1), (0.5, 1), (1, 0.5), (0.5, 0.5)):
        parts = []
        for plan, scale in ((outer, a), (inner, b)):
            parts.append(
                replace(
                    plan,
                    strips=(),
                    paths=tuple(
                        replace(p, width_m=p.width_m * scale) for p in paths_from_plan(plan)
                    ),
                )
            )
        plans.append(combined_seed(parts))
    return list({p.plan_id: p for p in plans}.values())


def optimize_joint_corridors(*, engine, seeds, evaluate, config, progress=None):
    """Bounded allocation sweep and conditional regional geometry search.

    Every unique composite, including rejection, consumes the common budget.
    This is a reproducible heuristic, with no global optimality guarantee.
    """
    if config.objective != "joint_protection_then_area":
        raise ValueError("joint protection required")
    grid = engine.bundle.request.grid
    records = []
    seen = {}
    plans = {}

    def visit(plan, operation):
        if plan.plan_id in seen:
            value = seen[plan.plan_id]
            if isinstance(value, Exception):
                raise value
            return value
        if len(records) >= config.max_evaluations:
            raise InterventionConflict("joint budget exhausted")
        record = {
            "plan_id": plan.plan_id,
            "plan": plan.to_mapping(),
            "operator": operation,
            "generation": 0,
            "query_index": len(records) + 1,
        }
        try:
            if len(plan.paths) > config.max_paths:
                raise InterventionConflict("joint path count exceeded")
            evaluations = evaluate(plan)
            record.update(
                status="ok",
                effectiveness=list(joint_protection_score(evaluations)),
                cleared_area_m2=evaluations[0].intervention["cleared_area_m2"],
                training_metrics=[e.metrics for e in evaluations],
            )
            seen[plan.plan_id] = evaluations
            plans[plan.plan_id] = plan
        except InterventionConflict as error:
            record.update(status="rejected", reason=str(error))
            seen[plan.plan_id] = error
        records.append(record)
        if progress:
            progress({"event": "evaluation", **record})
        if record["status"] != "ok":
            raise seen[plan.plan_id]
        return seen[plan.plan_id]

    for plan in seeds:
        if len(records) >= config.max_evaluations:
            break
        with suppress(InterventionConflict):
            visit(plan, "allocation_seed")
    for turn in range(4):
        if len(records) >= config.max_evaluations:
            break
        valid = [r for r in records if r["status"] == "ok"]
        if not valid:
            break
        best = min(
            valid, key=lambda r: (tuple(r["effectiveness"]), r["cleared_area_m2"], r["plan_id"])
        )
        parent = plans[best["plan_id"]]
        region = REGIONS[turn % 2]
        fixed = tuple(p for p in parent.components if p.zone_id != region)
        regional = [p for p in parent.components if p.zone_id == region]
        if len(parent.paths) >= config.max_paths and not regional:
            continue
        remaining = config.max_evaluations - len(records)
        quota = remaining if turn == 3 else min(remaining, max(3, (remaining + 1) // 2))
        conditional = replace(
            config,
            seed=config.seed + turn,
            max_evaluations=quota,
            max_paths=max(1, config.max_paths - sum(len(paths_from_plan(p)) for p in fixed)),
        )
        mask = engine.zones[region] & engine.bundle.request.burnable_mask
        mask &= engine.bundle.request.building_index < 0
        anchors = tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(mask))

        def evaluate_regional(candidate, fixed=fixed, region=region):
            return visit(combined_seed((*fixed, candidate)), "conditional_" + region)

        search_corridors(
            zone_id=region,
            bounds=(
                grid.x_min_m,
                grid.y_max_m - grid.height * grid.cell_size_m,
                grid.x_min_m + grid.width * grid.cell_size_m,
                grid.y_max_m,
            ),
            anchors=anchors,
            seeds=regional,
            evaluate=evaluate_regional,
            config=conditional,
        )
    return {
        "records": records,
        "objective": config.objective,
        "method": "scoped_conditional_joint_search",
        "budget_limit": config.max_evaluations,
        "unique_requests": len(records),
    }
