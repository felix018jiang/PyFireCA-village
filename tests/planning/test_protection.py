"""Do not confuse a pre-ignited source with failure of secondary protection."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import VillageEvaluationEngine
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPlan
from pyfireca.planning.protection import assess_protection, cohort_from_scenario, paired_interaction
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle

spec = importlib.util.spec_from_file_location(
    "protection_fixture",
    Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def example():
    q = fixture.make_request(("building", "grass", "building"), end_time_s=4)
    grid = VillageGridSpec(7, 9, 2, 0, 14)

    def pad(a, value=0):
        return np.pad(a, ((3, 3), (3, 3)), constant_values=value)

    edges = q.exposure_edges
    ids = np.arange(63).reshape(grid.shape)[3:4, 3:6].ravel()
    q = replace(
        q,
        grid=grid,
        domain_mask=np.ones(grid.shape, bool),
        known_mask=np.ones(grid.shape, bool),
        burnable_mask=pad(q.burnable_mask),
        source_mask=pad(q.source_mask),
        target_mask=pad(q.target_mask),
        piloted_ignition_mask=pad(q.piloted_ignition_mask),
        material_index=pad(q.material_index),
        landcover_index=pad(q.landcover_index),
        building_index=pad(np.array([[10, -1, 20]]), -1),
        parameters=V3CellPhysicsArrays(
            **{name: pad(getattr(q.parameters, name)) for name in q.parameters.__dataclass_fields__}
        ),
        exposure_edges=ExposureEdges(ids[edges.source], ids[edges.target], edges.weight, 63),
        scenario=replace(q.scenario, ignitions=(VillageIgnitionEvent(0, 3, 3),)),
    )
    b = V3CaseBundle(q, {}, {}, {})
    types = {10: "building", 20: "fengyuqiao"}
    e = VillageEvaluationEngine(
        b,
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
        structure_types=types,
        code_version="test",
    )
    return b, e, types


def test_internal_source_is_excluded_but_bridge_remains_protected():
    b, e, types = example()
    s = EvaluationScenario("inside", (Ignition(b.request.grid.center(3, 3)),), 4)
    cohort = cohort_from_scenario(b, s, source_family="internal", structure_types=types)
    result = e.evaluate(s, ClearingPlan("empty"))
    assessed = assess_protection(result, cohort)
    assert result.metrics["ignited_structure_count"] == 1
    assert cohort.protected_ids == (20,)
    assert assessed["all_protected_unignited"]
    assert assessed["ignited_bridge_count"] == 0
    # A bridge ignition is never hidden by removing the source house.
    records = result.metrics["building_first_ignitions"] + [
        {"building_index": 20, "source_label": "fengyuqiao", "time_s": 2.0}
    ]
    failed = assess_protection(
        replace(result, metrics={**result.metrics, "building_first_ignitions": records}), cohort
    )
    assert not failed["all_protected_unignited"]
    assert failed["ignited_bridge_count"] == 1


def test_external_keeps_all_buildings_and_rejects_source_mislabel():
    b, e, types = example()
    s = EvaluationScenario("outside", (Ignition(b.request.grid.center(3, 4)),), 4)
    cohort = cohort_from_scenario(b, s, source_family="external", structure_types=types)
    assert cohort.protected_ids == (10, 20)
    result = e.evaluate(s, ClearingPlan("empty"))
    assert assess_protection(result, cohort)["protected_structure_count"] == 2
    with pytest.raises(ValueError, match="external scenario"):
        cohort_from_scenario(
            b,
            replace(s, ignitions=(Ignition(b.request.grid.center(3, 3)),)),
            source_family="external",
            structure_types=types,
        )


def test_cannot_drop_a_receiver_or_exclude_a_late_scheduled_source():
    b, _e, types = example()
    s = EvaluationScenario("inside", (Ignition(b.request.grid.center(3, 3)),), 4)
    with pytest.raises(ValueError, match="every and only"):
        cohort_from_scenario(b, s, source_family="internal", structure_types={10: "building"})
    with pytest.raises(ValueError, match="time-zero"):
        cohort_from_scenario(
            b,
            replace(s, ignitions=(Ignition(b.request.grid.center(3, 3), 1),)),
            source_family="internal",
            structure_types=types,
        )


def test_truncated_boundary_and_wrong_scenario_cannot_pass():
    b, e, types = example()
    s = EvaluationScenario("inside", (Ignition(b.request.grid.center(3, 3)),), 4)
    cohort = cohort_from_scenario(b, s, source_family="internal", structure_types=types)
    result = e.evaluate(s, ClearingPlan("empty"))
    with pytest.raises(ValueError, match="full-horizon"):
        assess_protection(
            replace(
                result,
                diagnostics={
                    **result.diagnostics,
                    "simulated_until_s": 1,
                    "reached_requested_horizon": False,
                },
            ),
            cohort,
        )
    with pytest.raises(ValueError, match="boundary"):
        assess_protection(
            replace(result, metrics={**result.metrics, "burning_reached_domain_edge": True}), cohort
        )
    with pytest.raises(ValueError, match="frozen protection"):
        assess_protection(replace(result, scenario_id="other"), cohort)


def test_interaction_preserves_fixed_measures_and_cohort():
    def row(loss, measures):
        return {
            "scenario_id": "same",
            "cohort_sha256": "fixed",
            "complete": True,
            "loss": loss,
            "measure_ids": measures,
        }

    b, o, i, j = (
        row(10, ()),
        row(7, ("outer_A",)),
        row(8, ("inner_A",)),
        row(2, ("outer_A", "inner_A")),
    )
    x = paired_interaction(baseline=b, outer=o, inner=i, joint=j)
    assert x["interaction_reduction"] == 3
    with pytest.raises(ValueError, match="exact two fixed"):
        paired_interaction(baseline=b, outer=o, inner=i, joint=row(2, ("outer_B", "inner_A")))
    with pytest.raises(ValueError, match="same scenario"):
        paired_interaction(baseline=b, outer=o, inner=i, joint={**j, "cohort_sha256": "changed"})
