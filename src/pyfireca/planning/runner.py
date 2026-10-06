"""Small deterministic layout batches with explicit failed/unknown records."""

from dataclasses import dataclass
from time import perf_counter

from pyfireca.planning.contracts import Scenario, require_number
from pyfireca.planning.geometry import prepare_plan
from pyfireca.planning.objectives import LayoutScore, weighted_mean_exposure
from pyfireca.planning.optimizers import CandidateLayout
from pyfireca.planning.visibility import (
    VisibilityConfig,
    VisibilityEvaluation,
    VisibilityPhysicalConflict,
    VisibilityResourceLimit,
    evaluate_visibility,
)


@dataclass(frozen=True, slots=True)
class PressureScenario:
    scenario: Scenario
    weight: float = 1.0

    def __post_init__(self):
        if not isinstance(self.scenario, Scenario):
            raise TypeError("scenario must be Scenario")
        require_number("pressure weight", self.weight, positive=True)


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    scenario_id: str
    status: str
    evaluation: VisibilityEvaluation | None
    error: str | None


@dataclass(frozen=True, slots=True)
class LayoutOutcome:
    layout: CandidateLayout
    score: LayoutScore
    treated_area_m2: float
    scenarios: tuple[ScenarioOutcome, ...]


@dataclass(frozen=True, slots=True)
class BatchResult:
    outcomes: tuple[LayoutOutcome, ...]
    solver_calls: int


def evaluate_layouts(
    *,
    grid,
    domain_mask,
    background_rates_m_s,
    layouts: tuple[CandidateLayout, ...],
    pressures: tuple[PressureScenario, ...],
    profiles: tuple,
    config: VisibilityConfig,
    max_cost_units: float,
    max_solver_calls: int = 600,
    on_evaluation=None,
) -> BatchResult:
    """Evaluate fixed scenarios; do not aggregate a subset after a failed call.

    Unknown input/physics errors propagate. Only typed physical conflicts and
    resource failures become declared statuses. Cost and scenario IDs are
    checked before solving; estimated call budget is a conservative upper bound.
    """
    require_number("max_cost_units", max_cost_units)
    if (
        not isinstance(layouts, tuple)
        or not layouts
        or not all(isinstance(layout, CandidateLayout) for layout in layouts)
    ):
        raise ValueError("layouts must be a nonempty tuple of CandidateLayout")
    if len({layout.plan.plan_id for layout in layouts}) != len(layouts):
        raise ValueError("layout plan IDs must be unique")
    if (
        not isinstance(pressures, tuple)
        or not pressures
        or not all(isinstance(p, PressureScenario) for p in pressures)
    ):
        raise ValueError("pressures must be a nonempty tuple of PressureScenario")
    if len({p.scenario.scenario_id for p in pressures}) != len(pressures):
        raise ValueError("pressure scenario IDs must be unique")
    if (
        isinstance(max_solver_calls, bool)
        or not isinstance(max_solver_calls, int)
        or max_solver_calls < 1
    ):
        raise ValueError("max_solver_calls must be a positive integer")
    # Objective membership cannot change with ignition or layout in this batch.
    targets = pressures[0].scenario.targets
    horizon = pressures[0].scenario.horizon_s
    if any(p.scenario.targets != targets or p.scenario.horizon_s != horizon for p in pressures):
        raise ValueError("pressure scenarios must share fixed targets/weights and horizon")
    prepared = tuple(prepare_plan(grid, layout.plan, profiles) for layout in layouts)
    upper_calls = sum(p.cost_units <= max_cost_units + 1e-8 for p in prepared) * len(pressures)
    if upper_calls > max_solver_calls:
        raise VisibilityResourceLimit("batch solver call budget exceeded before evaluation")
    outcomes, calls = [], 0
    for layout, geometry in zip(layouts, prepared, strict=True):
        if geometry.cost_units > max_cost_units + 1e-8:
            outcomes.append(
                LayoutOutcome(
                    layout,
                    LayoutScore(layout.plan.plan_id, geometry.cost_units, None, "over_budget"),
                    geometry.treated_area_m2,
                    (),
                )
            )
            continue
        results = []
        for pressure in pressures:
            started = perf_counter()
            calls += 1
            try:
                evaluation = evaluate_visibility(
                    grid=grid,
                    domain_mask=domain_mask,
                    background_rates_m_s=background_rates_m_s,
                    plan=layout.plan,
                    scenario=pressure.scenario,
                    profiles=profiles,
                    config=config,
                )
                outcome = ScenarioOutcome(pressure.scenario.scenario_id, "ok", evaluation, None)
            except VisibilityPhysicalConflict as error:
                outcome = ScenarioOutcome(
                    pressure.scenario.scenario_id, "physical_conflict", None, str(error)
                )
            except VisibilityResourceLimit as error:
                outcome = ScenarioOutcome(
                    pressure.scenario.scenario_id, "resource_limit", None, str(error)
                )
            results.append(outcome)
            if on_evaluation is not None:
                on_evaluation(
                    layout.plan.plan_id,
                    pressure.scenario.scenario_id,
                    outcome.status,
                    perf_counter() - started,
                )
        statuses = {r.status for r in results}
        status = (
            "physical_conflict"
            if "physical_conflict" in statuses
            else "resource_limit"
            if "resource_limit" in statuses
            else "ok"
        )
        loss = (
            weighted_mean_exposure(
                tuple(r.evaluation.exposed_weight for r in results),
                tuple(p.weight for p in pressures),
            )
            if status == "ok"
            else None
        )
        outcomes.append(
            LayoutOutcome(
                layout,
                LayoutScore(layout.plan.plan_id, geometry.cost_units, loss, status),
                geometry.treated_area_m2,
                tuple(results),
            )
        )
    return BatchResult(tuple(outcomes), calls)
