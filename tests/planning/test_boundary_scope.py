"""Finite-domain views never erase the native strict boundary guard."""

import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.planning.boundary_scope import (
    EDGE_INVALID,
    audit_finite_domain_observation,
    audit_finite_domain_result,
    finite_domain_observation_is_complete,
    finite_domain_protection_query,
    finite_domain_protection_query_status,
    finite_domain_summary_is_complete,
    make_boundary_scope_problem,
    make_finite_domain_mip_check,
    validate_finite_domain_mip_check,
)
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.pareto import summarize_evaluated_pareto
from pyfireca.planning.terminal_verification import terminal_summary_is_complete

ROOT = Path(__file__).resolve().parents[2]


def setup_case(*, radius=0.1, core_close=False):
    path = ROOT / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("finite_domain_native_case", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    geometries = {
        "S": box(0, 8, 2, 10),
        "M": box(6, 8, 8, 10),
        "G": box(2, 8, 4, 10) if core_close else box(8, 8, 10, 10),
        "F": box(8, 6, 10, 8),
        "U": box(16, 12, 18, 14),
    }
    bundle, transport, inventory, scenario = fixture.make_case(
        radius=radius,
        ignition_temperature_K=350 if core_close else 520,
        geometries=geometries,
        fixed_source_ids=("S",),
        candidate_ids=("M", "U"),
    )
    inventory = replace(inventory, fixed_source_ids=(), candidate_ids=("S", "M", "U"))
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="sampled_retained_sources"
    )
    native_problem = content_hash(
        {
            "case": engine.case_sha256,
            "inventory": inventory.sha256,
            "source_priority": ["S", "M", "U"],
            "scenarios": [scenario.scenario_id],
            "radius": radius,
        }
    )
    return engine, scenario, make_boundary_scope_problem(native_problem, bundle.request)


def observe(engine, scenario, problem, *, plan=None, source="S", cap=1500):
    plan = BuildingDemolitionPlan("baseline") if plan is None else plan
    scenario = replace(
        scenario,
        horizon_s=cap,
        ignitions=(
            Ignition(
                tuple(engine.inventory.by_id[source].geometry.representative_point().coords[0]), 0
            ),
        ),
    )
    record, layout, result = engine.simulate(scenario, plan, stop_at_extinction=True)
    view = audit_finite_domain_result(
        engine,
        scenario,
        plan,
        record,
        layout,
        result,
        contact=engine.last_contact,
        analysis_problem=problem,
        source_evidence={"source_id": source},
    )
    return {
        "engine": engine,
        "scenario": scenario,
        "problem": problem,
        "plan": plan,
        "record": record,
        "layout": layout,
        "result": result,
        "view": view,
    }


@pytest.fixture(scope="module")
def observed_pair():
    engine, scenario, problem = setup_case()
    boundary = observe(engine, scenario, problem)
    strict = observe(
        engine,
        scenario,
        problem,
        plan=BuildingDemolitionPlan("remove-source", ("S",), ground_evidence="synthetic"),
        source="M",
    )
    return boundary, strict


def lineage(item):
    return {
        "native_problem_sha256": item["problem"]["native_problem_sha256"],
        "native_case_sha256": item["record"].case_sha256,
        "all_hashes_verified": True,
        "source_snapshot_sha256": "a" * 64,
        "input_manifest_sha256": "b" * 64,
        "artifact_manifest_sha256": "c" * 64,
    }


def archive(
    item,
    *,
    summary=None,
    check=None,
    state=None,
    peak=None,
    source_evidence=None,
    artifact_lineage=None,
):
    strict = item["view"].strict_verification
    return audit_finite_domain_observation(
        strict.summary if summary is None else summary,
        strict.check if check is None else check,
        item["record"],
        item["result"].final_state if state is None else state,
        item["record"].diagnostics["maximum_valid_surface_temperature_by_node_K"]
        if peak is None
        else peak,
        analysis_problem=item["problem"],
        source_evidence={"source_id": item["view"].summary["source_id"]}
        if source_evidence is None
        else source_evidence,
        artifact_lineage=lineage(item) if artifact_lineage is None else artifact_lineage,
        fire_loss=item["view"].fire_loss,
    )


def test_boundary_closed_view_passes_only_separate_conditional_criterion(observed_pair):
    boundary, _ = observed_pair
    view = boundary["view"]
    strict = view.strict_verification
    assert boundary["record"].diagnostics["invalid_reasons"] == [EDGE_INVALID]
    assert strict.check.status == "unknown" and not terminal_summary_is_complete(strict.summary)
    assert view.check.status == "passed"
    assert view.summary["finite_domain_closed"]
    assert view.summary["native_strict_status"] == "unknown"
    assert view.summary["native_invalid_reasons"] == [EDGE_INVALID]
    assert view.summary["native_strict_summary"] == strict.summary
    assert finite_domain_summary_is_complete(view.summary)
    assert finite_domain_observation_is_complete(
        view.summary,
        boundary["result"].final_state,
        boundary["record"].diagnostics["maximum_valid_surface_temperature_by_node_K"],
    )
    assert view.summary["analysis_problem_sha256"] != view.summary["native_problem_sha256"]


def test_native_strict_complete_point_can_get_same_conditional_problem_view(observed_pair):
    boundary, strict = observed_pair
    assert strict["view"].strict_verification.check.status == "passed"
    assert strict["view"].summary["native_invalid_reasons"] == []
    assert (
        strict["view"].summary["analysis_problem_sha256"]
        == boundary["view"].summary["analysis_problem_sha256"]
    )


def test_archive_view_checks_state_and_peak_even_while_strict_is_unknown(
    observed_pair, monkeypatch
):
    boundary, _ = observed_pair

    def no_fresh(*args, **kwargs):
        raise AssertionError("archive must not reconstruct or rerun native equations")

    monkeypatch.setattr("pyfireca.planning.boundary_scope.terminal.audit_terminal_result", no_fresh)
    view = archive(boundary)
    assert view.check.status == "passed" and view.summary["new_CA_checks"] == 0
    state = boundary["result"].final_state
    changed = state.fuel_remaining_kg.copy()
    changed.ravel()[0] += 1e-6
    with pytest.raises(ValueError, match="state/peak/time"):
        archive(boundary, state=replace(state, fuel_remaining_kg=changed))
    peak = np.asarray(
        boundary["record"].diagnostics["maximum_valid_surface_temperature_by_node_K"]
    ).copy()
    peak[0] += 0.1
    with pytest.raises(ValueError, match="state/peak/time"):
        archive(boundary, peak=peak)


def test_archive_requires_all_three_verified_manifest_layers(observed_pair):
    boundary, _ = observed_pair
    receipt = lineage(boundary)
    receipt["all_hashes_verified"] = False
    with pytest.raises(ValueError, match="caller-verified"):
        archive(boundary, artifact_lineage=receipt)
    receipt = lineage(boundary)
    receipt["source_snapshot_sha256"] = ""
    with pytest.raises(ValueError, match="SHA256"):
        archive(boundary, artifact_lineage=receipt)


def test_unclosed_fire_other_invalid_and_unverified_source_remain_unknown(observed_pair):
    engine, scenario, problem = setup_case()
    partial = observe(engine, scenario, problem, cap=20)
    assert partial["view"].check.status == "unknown"
    assert not finite_domain_summary_is_complete(partial["view"].summary)
    boundary, _ = observed_pair
    for additional, source_verified in (("other_physical_invalid", True), (None, False)):
        diagnostics = deepcopy(boundary["record"].diagnostics)
        if additional:
            diagnostics["invalid_reasons"].append(additional)
        diagnostics["source_verified"] = source_verified
        record = replace(boundary["record"], diagnostics=diagnostics)
        view = audit_finite_domain_result(
            boundary["engine"],
            boundary["scenario"],
            boundary["plan"],
            record,
            boundary["layout"],
            boundary["result"],
            contact=boundary["engine"].last_contact,
            analysis_problem=boundary["problem"],
            source_evidence={"source_id": "S"},
        )
        assert view.check.status == "unknown" and not view.summary["finite_domain_closed"]
    missing = archive(boundary, source_evidence={})
    assert missing.check.status == "unknown"


def test_actual_core_ignition_remains_completed_conditional_failure():
    engine, scenario, problem = setup_case(radius=8, core_close=True)
    item = observe(engine, scenario, problem, cap=8000)
    assert item["view"].summary["finite_domain_closed"]
    assert item["view"].check.status == "failed"
    assert not item["view"].summary["protection_feasible"]
    assert item["view"].summary["ignited_core_ids"]


def test_early_core_failure_query_does_not_fake_complete_fire_losses():
    engine, scenario, problem = setup_case(radius=8, core_close=True)
    scenario = replace(scenario, horizon_s=8000)
    plan = BuildingDemolitionPlan("failed-prefix")
    record, layout, result = engine.simulate(scenario, plan, stop_at_first_core_ignition=True)
    assert record.metrics["ignited_core_ids"]
    view = audit_finite_domain_result(
        engine,
        scenario,
        plan,
        record,
        layout,
        result,
        contact=engine.last_contact,
        analysis_problem=problem,
        source_evidence={"source_id": "S"},
    )
    assert view.check.status == "unknown" and not view.summary["finite_domain_closed"]
    query = finite_domain_protection_query(view.summary)
    assert finite_domain_protection_query_status(query, view.summary) == "failed"
    assert not query["fire_loss_complete"] and not query["future_core_safety_claimed"]
    assert not query["failure_family_cut_provided"]


def test_native_arrays_records_and_invalid_lists_are_never_mutated(observed_pair):
    boundary, _ = observed_pair
    before = content_hash(boundary["record"].to_mapping())
    original = deepcopy(boundary["view"].strict_verification.summary)
    archive(boundary)
    assert content_hash(boundary["record"].to_mapping()) == before
    assert boundary["view"].strict_verification.summary == original
    assert original["invalid_reasons"] == [EDGE_INVALID]


def test_resealed_view_cannot_clear_preserved_invalid_or_invent_source(observed_pair):
    boundary, _ = observed_pair
    for key, value in (
        ("native_invalid_reasons", []),
        ("source_id", "other"),
        ("finite_domain_closed", False),
    ):
        summary = deepcopy(boundary["view"].summary)
        summary[key] = value
        summary["analysis_summary_sha256"] = content_hash(
            {k: v for k, v in summary.items() if k != "analysis_summary_sha256"}
        )
        with pytest.raises(ValueError):
            finite_domain_summary_is_complete(summary)


def pareto_plan(item):
    view = item["view"].summary
    problem = item["problem"]
    protocol = {
        "inventory_sha256": item["engine"].inventory.sha256,
        "physical_protocol_sha256": problem["native_problem_sha256"],
        "native_problem_sha256": problem["native_problem_sha256"],
        "scenario_ids": [item["scenario"].scenario_id],
        "horizon_s": item["scenario"].horizon_s,
        "roof_scales_m": [2.0],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": item["engine"].bundle.request.scenario.max_dt_s,
        "source_policy": "sampled_retained_sources",
        "protected_core_ids": sorted(t.building_id for t in item["engine"].inventory.targets),
        "finite_domain_sha256": problem["finite_domain_sha256"],
        "boundary_contract_sha256": problem["boundary_contract_sha256"],
        "validator_policy_sha256": problem["validator_policy_sha256"],
        "boundary_scope_validator_sha256": problem["validator_sha256"],
    }
    return {
        "plan_id": item["plan"].plan_id,
        "problem_sha256": problem["analysis_problem_sha256"],
        "comparison_protocol": protocol,
        "temporal_mode": "finite_domain_closed",
        "cleared_area_m2": view["metrics"]["C_construction_action_area_m2"],
        "construction_area_basis": "geometric_action_union_m2",
        "demolished_ids": list(item["plan"].demolished_ids),
        "scenario_evaluations": {
            item["scenario"].scenario_id: {
                "problem_sha256": problem["analysis_problem_sha256"],
                "artifact_manifest_verified": True,
                "check": deepcopy(view["native_check"]),
                "finite_domain_summary": view,
                "fire_loss": item["view"].fire_loss,
            }
        },
    }


def test_pareto_conditional_pool_preserves_raw_unknown_and_requires_explicit_mode(observed_pair):
    plans = [pareto_plan(item) for item in observed_pair]
    with pytest.raises(ValueError, match="cannot be mixed"):
        summarize_evaluated_pareto(plans)
    value = summarize_evaluated_pareto(plans, temporal_mode="finite_domain_closed")
    assert value["complete_feasible_plan_count"] == 2
    assert len(value["non_dominated_archive"]) == 1
    assert plans[0]["scenario_evaluations"]["internal-source"]["check"]["status"] == "unknown"
    assert not value["global_pareto_frontier_complete"]
    assert (
        value["outcome_scope"]
        == "fixed_finite_native_domain_without_external_fire_or_heat_feedback"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("finite_domain_sha256", "other-domain"),
        ("max_dt_s", 0.5),
        ("source_policy", "fixed"),
        ("physical_protocol_sha256", "different-materials"),
    ],
)
def test_pareto_refuses_mixed_domain_clock_source_or_native_model(observed_pair, key, value):
    plans = [pareto_plan(item) for item in observed_pair]
    plans[1]["comparison_protocol"][key] = value
    with pytest.raises(ValueError, match="protocols cannot be mixed"):
        summarize_evaluated_pareto(plans, temporal_mode="finite_domain_closed")


def test_mip_receipt_is_new_criterion_and_never_clears_native_invalid(observed_pair):
    boundary, _ = observed_pair
    check = make_finite_domain_mip_check(boundary["view"].summary)
    assert check.status == "passed"
    assert validate_finite_domain_mip_check(check) == "passed"
    assert "invalid_reasons" not in check.evidence
    assert check.evidence["native_invalid_reasons"] == [EDGE_INVALID]
    assert check.evidence["native_check"]["status"] == "unknown"
    check.evidence["loss_metrics"] = {"independent_metric": 4}
    assert validate_finite_domain_mip_check(check) == "passed"
    check.evidence["failure_certificate"] = {
        "schema_version": "heritage-joint-physical-node-removal-cut-1"
    }
    with pytest.raises(ValueError, match="strict strong-cut"):
        validate_finite_domain_mip_check(check)


def test_mip_receipt_cannot_be_reused_under_native_problem_or_fake_empty_invalid(observed_pair):
    boundary, _ = observed_pair
    check = make_finite_domain_mip_check(boundary["view"].summary)
    with pytest.raises(ValueError, match="criterion/native lineage"):
        validate_finite_domain_mip_check(
            check, analysis_problem_sha256=boundary["problem"]["native_problem_sha256"]
        )
    check.evidence["invalid_reasons"] = []
    with pytest.raises(ValueError, match="cannot be cleared"):
        validate_finite_domain_mip_check(check)
