"""Engine-independent scenario and result contracts for intervention studies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from pyfireca.planning.contracts import Ignition, require_id, require_number


@dataclass(frozen=True, slots=True)
class EvaluationScenario:
    scenario_id: str
    ignitions: tuple[Ignition, ...]
    horizon_s: float

    def __post_init__(self):
        require_id("scenario_id", self.scenario_id)
        require_number("horizon_s", self.horizon_s, positive=True)
        if not isinstance(self.ignitions, tuple) or not self.ignitions:
            raise ValueError("scenario requires explicit ignitions")
        if any(not isinstance(i, Ignition) or i.time_s > self.horizon_s for i in self.ignitions):
            raise ValueError("ignitions must be typed and within the horizon")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: str
    case_sha256: str
    evaluation_sha256: str
    engine: str
    scenario_id: str
    intervention_id: str
    horizon_s: float
    metrics: dict
    intervention: dict
    diagnostics: dict
    capabilities: dict

    def to_mapping(self):
        return asdict(self)


class EvaluationEngine(Protocol):
    capabilities: dict

    def evaluate(self, scenario: EvaluationScenario, intervention) -> EvaluationResult: ...
