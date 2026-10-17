"""Multiple object identities must not multiply shared physical roof fuel."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import VillageEvaluationEngine, request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPlan
from pyfireca.planning.observations import build_object_observations
from pyfireca.planning.protection import assess_protection, cohort_from_scenario
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle

spec = importlib.util.spec_from_file_location(
    "observation_fixture",
    Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def outline(key, coords):
    return {
        "building_id": key,
        "properties": {"source_label": "building"},
        "polygons": [{"exterior": coords, "holes": []}],
    }


def example():
    q = fixture.make_request(("building", "grass", "building"), end_time_s=4)
    q = replace(q, building_index=np.array([[0, -1, 2]]))
    records = [
        outline("parent", [(0, 0), (2, 0), (2, 2), (0, 2)]),
        outline("nested", [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]),
        outline("receiver", [(4, 0), (6, 0), (6, 2), (4, 2)]),
    ]
    return q, records, {0: "parent", 1: "nested", 2: "receiver"}


def test_nested_identity_observed_without_mutating_any_fuel():
    q, records, labels = example()
    before = request_fingerprint(q)
    observer = build_object_observations(q, records, labels)
    assert observer.ids == (0, 1, 2)
    assert observer.summary()["shared_observation_cells"] == 1
    assert observer.summary()["unrepresented_labels"] == []
    assert observer.objects_at_cell(0) == (0, 1)
    times = np.array([[0.0, np.nan, np.nan]])
    assert [r["building_index"] for r in observer.first_ignitions(times)] == [0, 1]
    assert request_fingerprint(q) == before
    with pytest.raises(ValueError):
        observer.objects[0].cell_ids[0] = 2


def test_geometry_and_grid_binding_are_part_of_identity():
    q, records, labels = example()
    a = build_object_observations(q, records, labels)
    records[1] = outline("nested", [(0.3, 0.3), (0.8, 0.3), (0.8, 0.8), (0.3, 0.8)])
    b = build_object_observations(q, records, labels)
    assert a.sha256 != b.sha256
    with pytest.raises(ValueError, match="building grid"):
        a.validate_binding(replace(q, building_index=np.array([[2, -1, 0]])))


def test_missing_building_geometry_cannot_be_silently_ignored():
    q, records, labels = example()
    records[1] = outline("nested", [(2.2, 0.2), (3, 0.2), (3, 1), (2.2, 1)])
    with pytest.raises(ValueError, match="not fully represented"):
        build_object_observations(q, records, labels)


def test_observed_evaluation_changes_counts_but_preserves_heat_and_fuel():
    q, records, labels = example()
    bundle = V3CaseBundle(q, {}, {}, {})
    transport = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)
    observer = build_object_observations(q, records, labels)
    legacy = VillageEvaluationEngine(
        bundle, transport, structure_types={0: "building", 2: "building"}
    )
    engine = VillageEvaluationEngine(
        bundle, transport, structure_types=observer.types, object_observer=observer
    )
    s = EvaluationScenario("same", (Ignition(q.grid.center(0, 0)),), 4)
    a, b = legacy.evaluate(s, ClearingPlan("empty")), engine.evaluate(s, ClearingPlan("empty"))
    assert a.metrics["ignited_structure_count"] == 1
    assert b.metrics["ignited_structure_count"] == 2
    assert a.metrics["consumed_fuel_kg"] == b.metrics["consumed_fuel_kg"]
    assert a.diagnostics["energy_budget"] == b.diagnostics["energy_budget"]
    assert a.metrics["ignited_building_area_m2"] == b.metrics["ignited_building_area_m2"]
    assert a.evaluation_sha256 != b.evaluation_sha256
    with pytest.raises(ValueError, match="ambiguous"):
        cohort_from_scenario(
            bundle,
            s,
            source_family="internal",
            structure_types=observer.types,
            object_observer=observer,
        )


def test_unambiguous_internal_source_keeps_nested_receiver_identities():
    q, records, labels = example()
    bundle = V3CaseBundle(q, {}, {}, {})
    observer = build_object_observations(q, records, labels)
    s = EvaluationScenario("inside", (Ignition(q.grid.center(0, 2)),), 4)
    cohort = cohort_from_scenario(
        bundle,
        s,
        source_family="internal",
        structure_types=observer.types,
        object_observer=observer,
    )
    assert cohort.source_building_ids == (2,)
    assert cohort.protected_ids == (0, 1)
    e = VillageEvaluationEngine(
        bundle,
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
        structure_types=observer.types,
        object_observer=observer,
    )
    r = e.evaluate(s, ClearingPlan("empty"))
    # The tiny 1-row fixture intentionally reaches its boundary; this verifies
    # that a mismatched observer is rejected before any protection assessment.
    with pytest.raises(ValueError, match="observation geometry"):
        assess_protection(r, replace(cohort, observation_sha256="other"))


def test_crop_early_stop_tracks_observed_targets_only():
    q, records, _labels = example()
    observer = build_object_observations(q, [records[2]], {2: "receiver"})
    e = VillageEvaluationEngine(
        V3CaseBundle(q, {}, {}, {}),
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
        structure_types=observer.types,
        object_observer=observer,
    )
    s = EvaluationScenario("cropped", (Ignition(q.grid.center(0, 0)),), 4)
    result = e.evaluate(s, ClearingPlan("empty"), stop_at_first_structure_ignition=True)
    assert result.metrics["ignited_structure_count"] == 0
    assert result.diagnostics["reached_requested_horizon"]
