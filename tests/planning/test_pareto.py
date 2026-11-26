"""Pure evaluated-set tradeoffs require common protocols and complete losses."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.pareto import summarize_evaluated_pareto
from pyfireca.planning.terminal_verification import verify_terminal_fire


def protocol(**changes):
    return {
        "inventory_sha256": "inventory",
        "physical_protocol_sha256": "physical-protocol",
        "scenario_ids": ["s1", "s2"],
        "horizon_s": 3600,
        "roof_scales_m": [1, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1,
        "source_policy": "area_bounded_retained_sources",
        "protected_core_ids": ["heritage"],
        **changes,
    }


def loss(n=(2, 2), a=100, *, affected_n=None, affected=None, active=0, missing=()):
    affected_n = n if affected_n is None else affected_n
    affected = a if affected is None else affected
    fields = {
        "cumulative_burned_out_covered_area_m2": a,
        "cumulative_ignited_covered_area_m2": affected,
        "active_burning_covered_area_m2": active,
    }
    return {
        "saved_final_time_s": 3600,
        "physical": {
            "all": fields,
            "roof": {k: v * 0.6 for k, v in fields.items()},
            "nonroof": {k: v * 0.4 for k, v in fields.items()},
        },
        "physical_remaining_roof_annotation_counts": {
            "schema_version": "physical-original-owner-fire-counts-1",
            "unrepresented_original_ids": list(missing),
            "burned_out_annotation_attribution_count": dict(
                zip(("lower", "upper"), n, strict=True)
            ),
            "ignited_annotation_attribution_count": dict(
                zip(("lower", "upper"), affected_n, strict=True)
            ),
        },
        "fuel": {"fire_depleted_kg": 10},
    }


def evaluation(**changes):
    return {
        "problem_sha256": "problem",
        "artifact_manifest_verified": True,
        "check": {
            "status": "passed",
            "evidence": {
                "simulated_until_s": 3600,
                "reached_horizon": True,
                "invalid_reasons": [],
                "core_outcomes": [{"building_id": "heritage", "ignited": False}],
            },
        },
        "fire_loss": loss(),
        **changes,
    }


def plan(name, c, n=(2, 2), a=100, **changes):
    return {
        "plan_id": name,
        "problem_sha256": "problem",
        "comparison_protocol": protocol(),
        "temporal_mode": "phase_horizon",
        "cleared_area_m2": c,
        "construction_area_basis": "geometric_action_union_m2",
        "demolished_ids": [name],
        "scenario_evaluations": {s: evaluation(fire_loss=loss(n=n, a=a)) for s in ("s1", "s2")},
        **changes,
    }


def summarize(plans, **kwargs):
    return summarize_evaluated_pareto(plans, terminalclosed=False, **kwargs)


def names(rows):
    return [r["plan_id"] for r in rows]


def test_preserves_construction_fire_loss_tradeoff_and_equal_points():
    plans = [plan("cheap", 10, (5, 5), 100), plan("protect", 20, (2, 2), 50)]
    plans += [plan("dominated", 30, (3, 3), 60), plan("equal", 20, (2, 2), 50)]
    value = summarize(plans, area_anchor_certificates=[{"path": "minimum-area-proof.json"}])
    assert set(names(value["non_dominated_archive"])) == {"cheap", "protect", "equal"}
    assert value["fully_evaluated_feasible_plans"][-1]["upper_bound_dominated_by"] == [
        "equal",
        "protect",
    ]
    assert not value["global_pareto_frontier_complete"]
    assert not value["global_minimum_area_proven_by_this_module"]
    assert value["area_anchor_certificate_references"] == [{"path": "minimum-area-proof.json"}]
    assert value["new_CA_checks"] == 0


def test_worst_case_components_can_come_from_different_scenarios():
    p = plan("one", 4)
    p["scenario_evaluations"]["s1"]["fire_loss"] = loss(n=(4, 5), a=20)
    p["scenario_evaluations"]["s2"]["fire_loss"] = loss(n=(1, 2), a=80)
    row = summarize([p])["non_dominated_archive"][0]
    assert row["objective_values"] == [4, 5, 80]
    assert row["worst_case"]["N_burned_out"] == {"lower": 4, "upper": 5}


def test_interval_dominance_does_not_discard_uncertain_identity_tradeoff():
    value = summarize([plan("wide", 10, (1, 5), 50), plan("narrow", 10, (3, 3), 50)])
    assert names(value["non_dominated_archive"]) == ["narrow"]
    assert set(names(value["robust_non_dominated_archive"])) == {"wide", "narrow"}
    assert value["possible_dominance_free_archive"] == []
    wide = next(r for r in value["fully_evaluated_feasible_plans"] if r["plan_id"] == "wide")
    assert wide["upper_bound_dominated_by"] == ["narrow"]
    assert wide["robustly_dominated_by"] == []
    assert wide["possibly_dominated_by"] == ["narrow"]


def test_missing_unknown_failed_and_short_results_cannot_be_feasible():
    missing = plan("missing", 1)
    missing["scenario_evaluations"].pop("s2")
    unknown = plan("unknown", 2)
    unknown["scenario_evaluations"]["s1"]["check"]["status"] = "unknown"
    short = plan("short", 3)
    short["scenario_evaluations"]["s1"]["check"]["evidence"]["simulated_until_s"] = 1000
    failed = plan("failed", 4)
    failed["scenario_evaluations"]["s1"]["check"]["status"] = "failed"
    passed = plan("passed", 5)
    value = summarize([missing, unknown, short, failed, passed])
    assert names(value["non_dominated_archive"]) == ["passed"]
    assert set(names(value["unresolved_plans"])) == {"missing", "unknown", "short"}
    assert names(value["failed_plans"]) == ["failed"]
    assert value["non_dominated_archive"][0]["unresolved_potential_dominator_ids"] == [
        "missing",
        "short",
        "unknown",
    ]


def test_manifest_and_new_owner_counts_are_mandatory():
    p = plan("one", 1)
    p["scenario_evaluations"]["s1"]["artifact_manifest_verified"] = False
    p["scenario_evaluations"]["s2"]["fire_loss"].pop("physical_remaining_roof_annotation_counts")
    p["scenario_evaluations"]["s2"]["fire_loss"]["annotation_counts"] = {
        "ignited_annotation_attribution_count": {"lower": 0, "upper": 0}
    }
    value = summarize([p])
    assert not value["non_dominated_archive"]
    assert len(value["unresolved_plans"][0]["unresolved_or_failure_reasons"]) == 2


def test_zero_area_shared_label_removal_cannot_gain_from_legacy_count():
    a, b = plan("base", 0, (2, 2), 10), plan("alias", 0, (2, 2), 10)
    for e in b["scenario_evaluations"].values():
        e["fire_loss"]["annotation_counts"] = {
            "ignited_annotation_attribution_count": {"lower": 1, "upper": 1}
        }
    value = summarize([a, b])
    assert set(names(value["non_dominated_archive"])) == {"base", "alias"}
    assert all(p["objective_values"] == [0, 2, 10] for p in value["non_dominated_archive"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("horizon_s", 7200),
        ("roof_scales_m", [1]),
        ("source_policy", "fixed"),
        ("inventory_sha256", "other"),
    ],
)
def test_refuses_mixed_physical_protocols(field, value):
    a, b = plan("a", 1), plan("b", 2)
    b["comparison_protocol"][field] = value
    with pytest.raises(ValueError, match="protocols cannot be mixed"):
        summarize([a, b])


def test_explicit_equivalence_required_across_frozen_problems():
    a, b = plan("a", 1), plan("b", 2)
    b["problem_sha256"] = "other"
    for e in b["scenario_evaluations"].values():
        e["problem_sha256"] = "other"
    with pytest.raises(ValueError, match="explicit physical equivalence"):
        summarize([a, b])
    group = {
        "schema_version": "explicit-physical-protocol-equivalence-1",
        "problem_sha256s": ["problem", "other"],
        "equivalence_group_id": "documented-equivalence",
        "comparison_protocol": protocol(),
        "physical_protocol_sha256": "physical-protocol",
        "evidence_reference": "audited-equivalence.json",
    }
    assert summarize([a, b], equivalence_groups=[group])["explicit_equivalence_group"] == group


def test_unrepresented_scope_is_explicit_and_must_be_identical():
    a, b = plan("a", 1), plan("b", 2)
    for p in (a, b):
        for e in p["scenario_evaluations"].values():
            e["fire_loss"]["physical_remaining_roof_annotation_counts"][
                "unrepresented_original_ids"
            ] = ["missing-owner"]
    value = summarize([a, b])
    assert value["unrepresented_original_ids"] == ["missing-owner"]
    assert not value["all_original_inventory_owners_physically_represented"]
    b["scenario_evaluations"]["s2"]["fire_loss"]["physical_remaining_roof_annotation_counts"][
        "unrepresented_original_ids"
    ] = []
    with pytest.raises(ValueError, match="unrepresented-owner domains"):
        summarize([a, b])


def test_rejects_inconsistent_physical_area_and_invalid_bounds():
    p = plan("a", 1)
    p["scenario_evaluations"]["s1"]["fire_loss"]["physical"]["all"][
        "cumulative_burned_out_covered_area_m2"
    ] = 1
    with pytest.raises(ValueError, match="roof plus nonroof"):
        summarize([p])
    with pytest.raises(ValueError, match="ordered integer"):
        summarize([plan("b", 2, (3, 2))])


def test_phase_rejects_later_observation_and_missing_protected_core():
    p = plan("late", 1)
    e = p["scenario_evaluations"]["s1"]
    e["check"]["evidence"]["simulated_until_s"] = 7200
    e["fire_loss"]["saved_final_time_s"] = 7200
    assert not summarize([p])["non_dominated_archive"]
    p = plan("missing-core", 1)
    p["comparison_protocol"]["protected_core_ids"].append("another-core")
    assert not summarize([p])["non_dominated_archive"]


@pytest.fixture(scope="module")
def terminal_plan():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "pareto_native_case", root / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine

    bundle, transport, inventory, scenario = fixture.make_case(radius=0.1)
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    verified = verify_terminal_fire(
        engine,
        scenario,
        BuildingDemolitionPlan("terminal"),
        horizon_cap_s=1500,
        reference_problem_sha256="problem",
    )
    summary = verified.summary
    assert summary["terminal_complete"]
    return {
        "plan_id": "terminal",
        "problem_sha256": "problem",
        "comparison_protocol": protocol(
            scenario_ids=[scenario.scenario_id],
            horizon_s=1500,
            max_dt_s=bundle.request.scenario.max_dt_s,
        ),
        "temporal_mode": "terminalclosed",
        "cleared_area_m2": 0,
        "construction_area_basis": "geometric_action_union_m2",
        "demolished_ids": [],
        "scenario_evaluations": {
            scenario.scenario_id: {
                "problem_sha256": "problem",
                "artifact_manifest_verified": True,
                "check": {"status": verified.check.status, "evidence": summary},
                "terminal_summary": summary,
                "fire_loss": verified.fire_loss,
            }
        },
    }


def test_terminal_requires_native_extinction_closure_even_before_horizon(terminal_plan):
    value = summarize_evaluated_pareto([terminal_plan])
    row = value["non_dominated_archive"][0]
    assert row["objective_values"][1:] == [1, 4]
    assert row["scenario_observations"][next(iter(terminal_plan["scenario_evaluations"]))][
        "affected_equals_burned_out"
    ]
    summary = next(iter(terminal_plan["scenario_evaluations"].values()))["terminal_summary"]
    assert not summary["reached_requested_horizon"]


def test_terminal_time_cap_and_tampered_loss_cannot_enter_frontier(terminal_plan):
    p = deepcopy(terminal_plan)
    e = next(iter(p["scenario_evaluations"].values()))
    e["terminal_summary"] = None
    assert not summarize_evaluated_pareto([p])["non_dominated_archive"]
    p = deepcopy(terminal_plan)
    p["cleared_area_m2"] = 1
    with pytest.raises(ValueError, match="audited metrics differ"):
        summarize_evaluated_pareto([p])
    p = deepcopy(terminal_plan)
    next(iter(p["scenario_evaluations"].values()))["terminal_summary"][
        "temporal_closure_certificate"
    ]["actual_state_arrays_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint differs"):
        summarize_evaluated_pareto([p])


def test_temporal_modes_cannot_be_mixed(terminal_plan):
    with pytest.raises(ValueError, match="cannot be mixed"):
        summarize([terminal_plan])


def test_terminal_binds_plan_identity_and_ground_cells(terminal_plan):
    p = deepcopy(terminal_plan)
    p["plan_id"] = "other-label"
    with pytest.raises(ValueError, match="scenario, plan or problem"):
        summarize_evaluated_pareto([p])
    p = deepcopy(terminal_plan)
    p["cleared_physical_cell_ids"] = [42]
    with pytest.raises(ValueError, match="scenario, plan or problem"):
        summarize_evaluated_pareto([p])
