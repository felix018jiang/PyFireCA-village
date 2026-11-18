"""Exact finite-set zero-ignition feasibility with safe first-failure screening.

A failed scenario is sufficient to disprove feasibility. A pass requires every
scenario. Partial losses are lower bounds used only to guide infeasible search,
never advertised as full-suite mean/worst losses or a protection certificate.
"""

from dataclasses import dataclass

from pyfireca.planning.evaluation import EvaluationResult


def protection_failed(record):
    metrics = record.metrics
    return metrics["ignited_house_count"] + metrics["ignited_bridge_count"] > 0


@dataclass(frozen=True, slots=True)
class ProtectionScreen:
    expected_ids: tuple[str, ...]
    records: tuple[EvaluationResult, ...]

    def __post_init__(self):
        ids = tuple(r.scenario_id for r in self.records)
        if not self.expected_ids or len(set(self.expected_ids)) != len(self.expected_ids):
            raise ValueError("expected scenario IDs must be nonempty and unique")
        if not ids or ids != self.expected_ids[: len(ids)]:
            raise ValueError("screen must be a nonempty prefix of the declared suite")
        if len({r.horizon_s for r in self.records}) != 1:
            raise ValueError("all scenarios must have the same horizon")
        if any(r.metrics["burning_reached_domain_edge"] for r in self.records):
            raise ValueError("domain-edge ignition invalidates protection screening")
        if any(
            not r.diagnostics.get("reached_requested_horizon", True) and not protection_failed(r)
            for r in self.records
        ):
            raise ValueError("a physically truncated zero-loss run cannot certify protection")
        if not self.complete and not any(protection_failed(r) for r in self.records):
            raise ValueError("an incomplete screen cannot certify protection")

    @property
    def complete(self):
        return len(self.records) == len(self.expected_ids)

    @property
    def passed(self):
        return self.complete and not any(protection_failed(r) for r in self.records)

    def to_mapping(self):
        return {
            "expected_scenario_ids": self.expected_ids,
            "evaluated_scenario_ids": tuple(r.scenario_id for r in self.records),
            "complete": self.complete,
            "protection_goal_met": self.passed,
            "losses_are_lower_bounds": not self.complete,
            "stop_reason": "all_scenarios" if self.complete else "first_protection_failure",
            "evaluation_sha256": [r.evaluation_sha256 for r in self.records],
        }


def screen_protection(scenario_ids, evaluate, *, stop_at_failure=True):
    records = []
    for scenario_id in scenario_ids:
        record = evaluate(scenario_id)
        if record.scenario_id != scenario_id:
            raise ValueError("evaluation scenario identity mismatch")
        records.append(record)
        if stop_at_failure and protection_failed(record):
            break
    return ProtectionScreen(tuple(scenario_ids), tuple(records))
