"""Fixed protection cohorts for external entry and internal secondary spread.

The cohort is fixed from the input scenario, before reading outcomes. Only an
explicitly ignited building at time zero can be excluded in an internal-fire
mechanism study. Every other mapped structure, including bridges, stays protected.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from math import isfinite

import numpy as np

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario


@dataclass(frozen=True, slots=True)
class ProtectionCohort:
    case_sha256: str
    scenario_id: str
    horizon_s: float
    source_family: str
    source_building_ids: tuple[int, ...]
    structure_types: tuple[tuple[int, str], ...]
    observation_sha256: str | None = None

    def __post_init__(self):
        if self.source_family not in {"external", "internal"}:
            raise ValueError("explicit external or internal source family required")
        if not isfinite(self.horizon_s) or self.horizon_s <= 0:
            raise ValueError("positive finite horizon required")
        ids = tuple(i for i, _ in self.structure_types)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ValueError("structure inventory must be unique and sorted")
        if any(kind not in {"building", "fengyuqiao"} for _, kind in self.structure_types):
            raise ValueError("unrecognized protected structure type")
        if self.source_building_ids != tuple(sorted(set(self.source_building_ids))):
            raise ValueError("source building inventory must be unique and sorted")
        if not set(self.source_building_ids) <= set(ids):
            raise ValueError("source building absent from inventory")
        if (self.source_family == "external") != (len(self.source_building_ids) == 0):
            raise ValueError("external sources exclude no buildings; internal sources must exist")
        if len(self.source_building_ids) == len(ids):
            raise ValueError("cohort must retain protected receiver structures")

    @property
    def protected_ids(self):
        return tuple(i for i, _ in self.structure_types if i not in self.source_building_ids)

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def cohort_from_scenario(bundle, scenario, *, source_family, structure_types, object_observer=None):
    """Bind sources and the entire input building inventory before evaluation."""
    if not isinstance(scenario, EvaluationScenario):
        raise TypeError("scenario must be EvaluationScenario")
    request = bundle.request
    inventory = tuple(int(i) for i in np.unique(request.building_index) if i >= 0)
    if object_observer is not None:
        object_observer.validate_binding(request)
        inventory = object_observer.ids
        if object_observer.types != structure_types:
            raise ValueError("observer and cohort types differ")
    if set(structure_types) != set(inventory):
        raise ValueError("explicit types for every and only input structure required")
    sources = set()
    for event in scenario.ignitions:
        r, c = request.grid.locate(*event.point_m)
        if not request.burnable_mask[r, c]:
            raise ValueError("ignition is not on combustible input")
        owner = int(request.building_index[r, c])
        if source_family == "external" and owner >= 0:
            raise ValueError("external scenario cannot start on a building")
        if source_family == "internal":
            if owner < 0 or event.time_s != 0:
                raise ValueError(
                    "internal cohort excludes only explicit time-zero building sources"
                )
            if object_observer is not None:
                observed = object_observer.objects_at_cell(request.grid.cell_id(r, c))
                if len(observed) != 1:
                    raise ValueError("internal source has ambiguous shared annotation observations")
                owner = observed[0]
            sources.add(owner)
    return ProtectionCohort(
        request_fingerprint(request),
        scenario.scenario_id,
        scenario.horizon_s,
        source_family,
        tuple(sorted(sources)),
        tuple((i, structure_types[i]) for i in inventory),
        object_observer.sha256 if object_observer is not None else None,
    )


def assess_protection(result, cohort):
    """Assess full-horizon outputs without changing the physical simulation.

    This is a deterministic scenario outcome, never an inferred fire probability.
    Invalid/truncated or boundary-affected runs cannot become successful trials.
    """
    if not isinstance(result, EvaluationResult) or not isinstance(cohort, ProtectionCohort):
        raise TypeError("typed evaluation and input-bound cohort required")
    if (
        result.case_sha256 != cohort.case_sha256
        or result.scenario_id != cohort.scenario_id
        or result.horizon_s != cohort.horizon_s
    ):
        raise ValueError("result does not match frozen protection cohort")
    d, m = result.diagnostics, result.metrics
    if m.get("object_observation", {}).get("observation_sha256") != cohort.observation_sha256:
        raise ValueError("result observation geometry does not match frozen cohort")
    actual = d.get("simulated_until_s", float("nan"))
    if (
        not d.get("reached_requested_horizon")
        or not isfinite(actual)
        or actual < cohort.horizon_s - 1e-8
        or actual > cohort.horizon_s + 1e-8
    ):
        raise ValueError("full-horizon confirmation required")
    if m.get("burning_reached_domain_edge") is not False:
        raise ValueError("boundary-affected or unaudited run")
    inventory = dict(cohort.structure_types)
    times = {}
    for item in m["building_first_ignitions"]:
        i, t = item["building_index"], item["time_s"]
        if (
            i not in inventory
            or i in times
            or item["source_label"] != inventory[i]
            or not isfinite(t)
            or t < 0
            or t > cohort.horizon_s + 1e-8
        ):
            raise ValueError("invalid object ignition record")
        times[i] = t
    if any(times.get(i) != 0 for i in cohort.source_building_ids):
        raise ValueError("declared internal source was not directly ignited at time zero")
    affected = sorted(set(times) & set(cohort.protected_ids))
    houses = sum(inventory[i] == "building" for i in affected)
    bridges = sum(inventory[i] == "fengyuqiao" for i in affected)
    return {
        "cohort_sha256": cohort.sha256,
        "source_family": cohort.source_family,
        "source_building_ids": list(cohort.source_building_ids),
        "protected_structure_count": len(cohort.protected_ids),
        "ignited_protected_ids": affected,
        "ignited_house_count": houses,
        "ignited_bridge_count": bridges,
        "first_protected_ignition_s": min((times[i] for i in affected), default=None),
        "all_protected_unignited": not affected,
        "actual_horizon_s": actual,
        "interpretation": "declared scenario outcome; not an estimated population probability",
    }


def paired_interaction(*, baseline, outer, inner, joint):
    """Four matched fixed interventions; positive value means superadditive reduction.

    This is not the equal-area comparison: joint uses both fixed components.
    Inputs must already be protected-object losses for the same scenario/cohort.
    """
    rows = [baseline, outer, inner, joint]
    if len({(r["scenario_id"], r["cohort_sha256"]) for r in rows}) != 1:
        raise ValueError("four outcomes must share the same scenario and protection cohort")
    for r in rows:
        if not r.get("complete") or not isinstance(r["loss"], (int, float)):
            raise ValueError("complete numeric protected losses required")
        if not isfinite(r["loss"]) or r["loss"] < 0:
            raise ValueError("losses must be finite and nonnegative")
    measures = [r["measure_ids"] for r in rows]
    if any(not isinstance(m, tuple) or len(m) != len(set(m)) for m in measures):
        raise ValueError("immutable unique measure identities required")
    b, o, i, j = map(set, measures)
    if b or not o or not i or o & i or j != o | i:
        raise ValueError("joint must combine the exact two fixed single-side measures")
    l0, lo, li, lj = [r["loss"] for r in rows]
    return {
        "scenario_id": baseline["scenario_id"],
        "cohort_sha256": baseline["cohort_sha256"],
        "outer_reduction": l0 - lo,
        "inner_reduction": l0 - li,
        "combined_reduction": l0 - lj,
        "interaction_reduction": lo + li - lj - l0,
        "interpretation": "fixed-measure interaction; separate from equal-area optimization",
    }
