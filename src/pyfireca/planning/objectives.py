"""Transparent pressure-scenario aggregation and budgeted near-optimal sets."""

from dataclasses import dataclass
from math import fsum

from pyfireca.planning.contracts import require_id, require_number


def weighted_mean_exposure(losses: tuple[float, ...], weights: tuple[float, ...]) -> float:
    """Weighted conditional pressure objective, not inferred annual fire risk."""
    if not losses or len(losses) != len(weights):
        raise ValueError("losses and weights must have equal nonzero length")
    for loss in losses:
        require_number("exposure loss", loss)
    for weight in weights:
        require_number("scenario weight", weight, positive=True)
    scale = max(weights)
    normalized = tuple(weight / scale for weight in weights)
    total = fsum(normalized)
    return fsum(loss * (weight / total) for loss, weight in zip(losses, normalized, strict=True))


@dataclass(frozen=True, slots=True)
class LayoutScore:
    plan_id: str
    cost_units: float
    mean_exposure: float | None
    status: str = "ok"

    def __post_init__(self):
        require_id("plan_id", self.plan_id)
        require_number("cost_units", self.cost_units)
        if self.status not in ("ok", "over_budget", "physical_conflict", "resource_limit"):
            raise ValueError("unsupported layout status")
        if self.status == "ok":
            require_number("mean_exposure", self.mean_exposure)
        elif self.mean_exposure is not None:
            raise ValueError("unevaluated or infeasible layouts must not have a numeric loss")


@dataclass(frozen=True, slots=True)
class BudgetSelection:
    budget_units: float
    best_known_loss: float | None
    exact_best_ids: tuple[str, ...]
    near_optimal_ids: tuple[str, ...]
    unevaluated_ids: tuple[str, ...]
    candidate_set_complete: bool


def select_near_optimal(
    scores: tuple[LayoutScore, ...],
    budget_units: float,
    *,
    practical_difference: float = 0.05,
    cost_tolerance: float = 1e-8,
) -> BudgetSelection:
    """Select sets by primary loss only; cost/ID order does not break objective ties.

    Physical conflicts are explicitly excluded. Resources/unknown evaluations
    remain unknown, so the reported candidate optimum can only be best-known.
    """
    require_number("budget_units", budget_units)
    require_number("practical_difference", practical_difference)
    require_number("cost_tolerance", cost_tolerance)
    if len({score.plan_id for score in scores}) != len(scores):
        raise ValueError("score plan IDs must be unique")
    eligible = [s for s in scores if s.cost_units <= budget_units + cost_tolerance]
    unknown = tuple(
        sorted(s.plan_id for s in eligible if s.status in ("resource_limit", "over_budget"))
    )
    evaluated = [s for s in eligible if s.status == "ok"]
    if not evaluated:
        return BudgetSelection(budget_units, None, (), (), unknown, not unknown)
    best = min(s.mean_exposure for s in evaluated)
    ordered = sorted(evaluated, key=lambda s: (s.cost_units, s.plan_id))
    exact = tuple(s.plan_id for s in ordered if abs(s.mean_exposure - best) <= 1e-12)
    near = tuple(
        s.plan_id for s in ordered if s.mean_exposure <= best + practical_difference + 1e-12
    )
    return BudgetSelection(budget_units, best, exact, near, unknown, not unknown)
