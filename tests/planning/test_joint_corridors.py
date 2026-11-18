"""Joint plans must use one physical request and preserve union accounting."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.adaptive import CorridorSearchConfig
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPath, ClearingPlan
from pyfireca.planning.joint import (
    JointVillageEvaluationEngine,
    combined_seed,
    joint_initial_plans,
    optimize_joint_corridors,
    plan_from_mapping,
)
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "joint_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def engine():
    req = fixture.make_request(("grass", "grass", "grass", "building"), end_time_s=120)
    return JointVillageEvaluationEngine(
        V3CaseBundle(req, {}, {}, {}),
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
        outer_mask=np.array([[True, True, False, False]]),
        inner_mask=np.array([[False, False, True, True]]),
        code_version="test",
    )


def test_joint_overlaps_count_once_and_both_domains_share_one_state():
    e = engine()
    sc = EvaluationScenario("fire", (Ignition(e.bundle.request.grid.center(0, 0)),), 120)
    a = ClearingPlan(
        "outer", zone_id="outer_proxy", paths=(ClearingPath(((3.0, 0.0), (3.0, 2.0)), 2),)
    )
    b = ClearingPlan(
        "inner", zone_id="inner_proxy", paths=(ClearingPath(((5.0, 0.0), (5.0, 2.0)), 2),)
    )
    plan = combined_seed((a, b, a))
    req, fraction, summary = e.prepare(sc, plan)
    assert len(plan.paths) == 2
    assert fraction.tolist() == [[0, 1, 1, 0]]
    assert summary["zone_areas_m2"] == {"outer_proxy": 4, "inner_proxy": 4}
    assert summary["cleared_area_m2"] == 8
    assert summary["configuration"] == "both"
    assert (
        req.parameters.initial_dry_fuel_mass_kg[0, 3]
        == e.bundle.request.parameters.initial_dry_fuel_mass_kg[0, 3]
    )
    direct = run_v3_simulation(req, retain_snapshots=False)
    evaluated = e.evaluate(sc, plan)
    assert (
        evaluated.metrics["ignited_cells"] == np.isfinite(direct.final_state.ignition_time_s).sum()
    )
    assert evaluated.intervention["simultaneous_single_run"]
    assert evaluated.diagnostics["parent_case_unchanged"]


def test_partition_changes_identity_even_with_identical_union():
    e = engine()
    changed = JointVillageEvaluationEngine(
        e.bundle,
        e.transport,
        outer_mask=np.array([[True, False, False, False]]),
        inner_mask=np.array([[False, True, True, True]]),
        code_version="test",
    )
    assert e.code_version != changed.code_version
    with pytest.raises(ValueError, match="overlap"):
        JointVillageEvaluationEngine(
            e.bundle, e.transport, outer_mask=e.zones["joint"], inner_mask=e.zones["inner_proxy"]
        )


def test_initial_allocation_sweep_keeps_single_layer_candidates():
    a = ClearingPlan("a", zone_id="outer_proxy", paths=(ClearingPath(((3.0, 0.0), (3.0, 2.0)), 2),))
    b = ClearingPlan("b", zone_id="inner_proxy", paths=(ClearingPath(((5.0, 0.0), (5.0, 2.0)), 2),))
    plans = joint_initial_plans(a, b)
    assert len(plans) == 7
    assert any(not p.paths for p in plans)
    assert sum(len(p.paths) == 1 for p in plans) == 2
    assert {p.zone_id for p in plans} == {"joint"}


def test_scoped_singleton_matches_original_atomic_clearance():
    e = engine()
    sc = EvaluationScenario("fire", (Ignition(e.bundle.request.grid.center(0, 0)),), 120)
    # Requested band crosses both regions. Only its original region is eligible.
    a = ClearingPlan(
        "crossing", zone_id="outer_proxy", paths=(ClearingPath(((4.0, 0.0), (4.0, 2.0)), 4.0),)
    )
    original, fraction, summary = e.prepare(sc, a)
    scoped, combined_fraction, combined_summary = e.prepare(sc, combined_seed((a,)))
    np.testing.assert_array_equal(fraction, combined_fraction)
    assert fraction.tolist() == [[0, 1, 0, 0]]
    assert summary["cleared_area_m2"] == combined_summary["cleared_area_m2"] == 4
    for name in original.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(original.parameters, name), getattr(scoped.parameters, name)
        )
    assert combined_summary["zone_areas_m2"]["inner_proxy"] == 0
    plan = combined_seed((a,))
    assert plan_from_mapping(plan.to_mapping()) == plan
    wrong = {**plan.to_mapping(), "plan_id": "tampered"}
    with pytest.raises(ValueError, match="identity"):
        plan_from_mapping(wrong)
    with pytest.raises(ValueError, match="scope"):
        combined_seed((ClearingPlan("unscoped"),))


def test_conditional_joint_search_respects_shared_budget_and_replays():
    e = engine()
    sc = EvaluationScenario("fire", (Ignition(e.bundle.request.grid.center(0, 0)),), 120)
    a = ClearingPlan(
        "a", zone_id="outer_proxy", paths=(ClearingPath(((3.0, 0.0), (3.0, 2.0)), 1.0),)
    )
    b = ClearingPlan(
        "b", zone_id="inner_proxy", paths=(ClearingPath(((5.0, 0.0), (5.0, 2.0)), 1.0),)
    )
    config = CorridorSearchConfig(
        max_evaluations=12,
        max_paths=2,
        initial_step_m=0.5,
        min_step_m=0.5,
        width_min_m=0.5,
        width_max_m=2,
        length_min_m=0.5,
        length_max_m=2,
        objective="joint_protection_then_area",
    )

    def run():
        evaluated = []

        def oracle(plan):
            evaluated.append(plan.plan_id)
            record = e.evaluate(sc, plan)
            # This one-row fixture has no guard; test search accounting here,
            # while guarded case validation belongs to the study audit.
            return [
                replace(record, metrics={**record.metrics, "burning_reached_domain_edge": False})
            ]

        result = optimize_joint_corridors(
            engine=e, seeds=joint_initial_plans(a, b), evaluate=oracle, config=config
        )
        assert len(evaluated) == len(set(evaluated)) <= 12
        assert len(result["records"]) <= 12
        assert any(r["operator"].startswith("conditional_") for r in result["records"])
        return result["records"]

    assert run() == run()
