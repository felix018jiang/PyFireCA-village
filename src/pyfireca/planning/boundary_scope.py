"""Explicit finite-domain outcome views, preserving native strict invalidity.

A fire may be extinct on the fixed native graph even though its prior contact
with the physical grid edge makes domain sufficiency unknown. This separate
analysis assumes no propagation outside the modeled grid/masks and no unmodeled
external heat feedback, firebrands or new fires. It never proves those physical
assumptions, clears a native invalid flag, or upgrades a native strict record.

Fresh audits use the original native auditor. Archived observations require a
caller-verified immutable source/input/artifact lineage; this module additionally
checks the genuine producer certificate against loaded state and peak arrays.
No radiation graph reconstruction is needed for that archival verification.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from math import isfinite
from pathlib import Path

import numpy as np

from pyfireca.planning import terminal_verification as terminal
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.village import extinction_v3 as extinction

EDGE_INVALID = "fire_reached_physical_domain_edge"
SUMMARY_SCHEMA = "heritage-finite-domain-closure-analysis-1"
PROBLEM_SCHEMA = "heritage-finite-domain-analysis-problem-1"
BOUNDARY_CONTRACT = {
    "domain": "fixed_native_finite_grid_graph_and_masks",
    "native_ambient_and_boundary_parameters": "unchanged",
    "outside_domain_fire_propagation": "not_simulated;excluded_from_this_outcome",
    "unmodeled_external_heat_or_fire_feedback": "excluded_by_analysis_assumption",
    "new_external_ignitions_or_firebrands": "excluded_by_analysis_assumption",
    "domain_sufficiency_or_real_world_safety_proven": False,
    "native_strict_invalidity": "preserved_without_modification",
}
VALIDATOR_POLICY = {
    "allowed_native_invalid_lists": [[], [EDGE_INVALID]],
    "genuine_native_fresh_extinction_certificate": True,
    "independent_actual_state_and_historical_peak_binding": True,
    "source_verified_and_zero_time_roof_ignition": True,
    "core_ignition_remains_hard_failure": True,
    "strict_and_conditional_problem_modes_are_distinct": True,
    "past_core_failure_is_separate_from_complete_fire_loss": True,
    "passing_protection_query_requires_closed_and_core_safe": True,
    "edge_invalid_failed_query_has_no_strict_strong_cut": True,
}
BOUNDARY_CONTRACT_SHA256 = content_hash(BOUNDARY_CONTRACT)
VALIDATOR_POLICY_SHA256 = content_hash(VALIDATOR_POLICY)


def _sha(value, name):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{name} must be a SHA256 fingerprint")
    return value


def _mapping(value):
    if isinstance(value, dict):
        return deepcopy(value)
    if hasattr(value, "to_mapping"):
        return deepcopy(value.to_mapping())
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("native record/check must be a mapping or typed record")


def _array_sha(value):
    value = np.ascontiguousarray(value)
    return content_hash(
        {
            "dtype": str(value.dtype),
            "shape": value.shape,
            "bytes": hashlib.sha256(value.tobytes()).hexdigest(),
        }
    )


def validator_source_fingerprints():
    files = {
        "planning/boundary_scope.py": Path(__file__),
        "planning/terminal_verification.py": Path(terminal.__file__),
        "planning/pareto.py": Path(__file__).with_name("pareto.py"),
        "village/extinction_v3.py": Path(extinction.__file__),
    }
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}


def finite_domain_fingerprint(parent_request):
    return content_hash(
        {
            "physical_grid": asdict(parent_request.grid),
            "domain_mask": _array_sha(parent_request.domain_mask),
            "known_mask": _array_sha(parent_request.known_mask),
        }
    )


def make_boundary_scope_problem(
    native_problem_sha256,
    parent_request=None,
    *,
    parent_case_sha256=None,
    finite_domain_sha256=None,
):
    """Bind a new criterion to one unchanged native physical comparison problem."""
    source_hashes = validator_source_fingerprints()
    if parent_request is not None:
        case_fingerprint = request_fingerprint(parent_request)
        domain_fingerprint = finite_domain_fingerprint(parent_request)
        if parent_case_sha256 not in (None, case_fingerprint) or finite_domain_sha256 not in (
            None,
            domain_fingerprint,
        ):
            raise ValueError("supplied native parent/domain fingerprints differ")
    else:
        case_fingerprint = _sha(parent_case_sha256, "caller-verified native parent case")
        domain_fingerprint = _sha(finite_domain_sha256, "caller-verified finite domain")
    value = {
        "schema_version": PROBLEM_SCHEMA,
        "native_problem_sha256": _sha(native_problem_sha256, "native problem"),
        "parent_case_sha256": case_fingerprint,
        "finite_domain_sha256": domain_fingerprint,
        "boundary_contract": deepcopy(BOUNDARY_CONTRACT),
        "boundary_contract_sha256": BOUNDARY_CONTRACT_SHA256,
        "validator_policy": deepcopy(VALIDATOR_POLICY),
        "validator_policy_sha256": VALIDATOR_POLICY_SHA256,
        "validator_source_files_sha256": source_hashes,
        "validator_sha256": content_hash(source_hashes),
    }
    value["analysis_problem_sha256"] = content_hash(value)
    return value


def validate_boundary_scope_problem(problem):
    if (
        not isinstance(problem, dict)
        or problem.get("schema_version") != PROBLEM_SCHEMA
        or problem.get("analysis_problem_sha256")
        != content_hash({k: v for k, v in problem.items() if k != "analysis_problem_sha256"})
        or problem.get("boundary_contract") != BOUNDARY_CONTRACT
        or problem.get("boundary_contract_sha256") != BOUNDARY_CONTRACT_SHA256
        or problem.get("validator_policy") != VALIDATOR_POLICY
        or problem.get("validator_policy_sha256") != VALIDATOR_POLICY_SHA256
        or problem.get("validator_source_files_sha256") != validator_source_fingerprints()
        or problem.get("validator_sha256") != content_hash(validator_source_fingerprints())
    ):
        raise ValueError("finite-domain analysis problem or validator fingerprint differs")
    for key in ("native_problem_sha256", "parent_case_sha256", "finite_domain_sha256"):
        _sha(problem.get(key), key)
    return problem


def _certificate_closed(certificate, protocol):
    """Independent closure checks; never invent an invalid=[] terminal view."""
    if certificate is None:
        return False
    if certificate.get("schema_version") != "v3-extinction-time-closure-1" or certificate.get(
        "certificate_sha256"
    ) != extinction.extinction_certificate_sha256(certificate):
        raise ValueError("finite-domain native extinction certificate fingerprint differs")
    for key in (
        "frozen_input_sha256",
        "actual_state_arrays_sha256",
        "historical_valid_temperature_maximum_sha256",
    ):
        _sha(certificate.get(key), key)
    actual, cap = certificate.get("actual_stop_time_s"), protocol.get("terminal_horizon_cap_s")
    if (
        any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x)
            for x in (actual, cap)
        )
        or not 0 <= actual <= cap
        or cap <= 0
    ):
        raise ValueError("invalid finite-domain closure times")
    if (
        certificate.get("requested_horizon_s") != cap
        or certificate.get("reached_requested_horizon") != (actual >= cap - 1e-8)
        or certificate.get("post_apply_events_boundary") is not True
        or certificate.get("future_prescribed_event_count") != 0
        or certificate.get("firebrands") is not None
        or certificate.get("burning_node_count") != 0
        or certificate.get("eligible_thermal_ignition_node_count") != 0
        or certificate.get("heated_water_storage_roundtrip_stationary") is not True
        or certificate.get("future_mean_hrr_W") != 0
    ):
        raise ValueError("finite-domain certificate does not establish extinction")
    for key, positive in (
        ("minimum_eligible_ignition_slack_K", True),
        ("minimum_susceptible_temperature_above_ambient_K", False),
    ):
        value = certificate.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or (value <= 0 if positive else value < 0)
        ):
            raise ValueError("finite-domain certificate violates cooling/ignition conditions")
    for key in ("maximum_zero_source_net_flux_W_m2", "maximum_ambient_zero_source_net_flux_W_m2"):
        value = certificate.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value > 0
        ):
            raise ValueError("finite-domain certificate contains positive future heat")
    return True


def _lineage(
    analysis_problem, native_summary, native_check, record, *, basis, artifact_lineage=None
):
    result = {
        "basis": basis,
        "native_problem_sha256": analysis_problem["native_problem_sha256"],
        "native_case_sha256": record["case_sha256"],
        "native_evaluation_sha256": record["evaluation_sha256"],
        "native_physical_request_sha256": record["diagnostics"]["physical_request_sha256"],
        "native_record_sha256": content_hash(record),
        "native_check_sha256": content_hash(native_check),
        "native_strict_summary_sha256": content_hash(native_summary),
    }
    if basis == "archived_genuine_native_certificate":
        expected = {
            "native_problem_sha256": analysis_problem["native_problem_sha256"],
            "native_case_sha256": record["case_sha256"],
        }
        if (
            not isinstance(artifact_lineage, dict)
            or artifact_lineage.get("all_hashes_verified") is not True
            or any(artifact_lineage.get(k) != v for k, v in expected.items())
        ):
            raise ValueError("archival finite-domain view requires caller-verified native lineage")
        for key in ("source_snapshot_sha256", "input_manifest_sha256", "artifact_manifest_sha256"):
            _sha(artifact_lineage.get(key), key)
        result["caller_verified_artifact_lineage"] = deepcopy(artifact_lineage)
    result["lineage_sha256"] = content_hash(result)
    return result


def _make_view(
    native_summary,
    native_check,
    record,
    state,
    peak,
    *,
    analysis_problem,
    source_evidence,
    basis,
    artifact_lineage=None,
):
    validate_boundary_scope_problem(analysis_problem)
    native_summary, native_check, record = map(_mapping, (native_summary, native_check, record))
    strict_complete = terminal.terminal_summary_is_complete(native_summary)
    if (
        native_summary.get("schema_version") != "heritage-terminal-fire-verification-1"
        or native_summary.get("fresh_certificate_checked") is not True
        or not isinstance(native_summary.get("engine_certificate_was_present"), bool)
        or native_summary.get("terminal_protocol_sha256")
        != content_hash(native_summary.get("model_protocol"))
        or native_summary["model_protocol"].get("parent_case_sha256")
        != analysis_problem["parent_case_sha256"]
        or record.get("case_sha256") != analysis_problem["parent_case_sha256"]
        or native_summary.get("actual_stop_time_s") != state.time_s
        or record["diagnostics"].get("simulated_until_s") != state.time_s
        or record.get("horizon_s") != native_summary["model_protocol"].get("terminal_horizon_cap_s")
        or record.get("scenario_id") != native_summary.get("scenario_id")
        or record.get("intervention_id") != native_summary.get("plan_id")
        or sorted(record["intervention"].get("demolished_ids", ()))
        != sorted(native_summary.get("demolished_ids", ()))
        or sorted(record["intervention"].get("cleared_physical_cell_ids", ()))
        != sorted(native_summary.get("cleared_physical_cell_ids", ()))
        or native_check.get("status") != native_summary.get("terminal_status")
        or record["diagnostics"].get("physical_request_sha256")
        != native_summary["model_protocol"].get("physical_request_sha256")
    ):
        raise ValueError("native summary/check/record/state identities differ")
    embedded = native_check.get("evidence", {}).get("terminal_verification")
    if embedded is not None and embedded != native_summary:
        raise ValueError("native check embeds a different terminal summary")
    if (
        native_check.get("evidence", {}).get("schema_version")
        == "heritage-terminal-fire-verification-1"
        and native_check["evidence"] != native_summary
    ):
        raise ValueError("native strict check differs from native summary")
    evidence = native_check.get("evidence", {})
    for key, expected in (
        ("evaluation_sha256", record["evaluation_sha256"]),
        (
            "source_id",
            source_evidence.get("source_id") if isinstance(source_evidence, dict) else None,
        ),
    ):
        if key in evidence and evidence[key] != expected:
            raise ValueError("native check evaluation/source identity differs")
    if (
        native_check.get("problem_sha256", analysis_problem["native_problem_sha256"])
        != analysis_problem["native_problem_sha256"]
        or native_check.get("scenario_id", record["scenario_id"]) != record["scenario_id"]
    ):
        raise ValueError("native check problem/scenario differs")
    source = source_evidence.get("source_id") if isinstance(source_evidence, dict) else None
    sources = record["metrics"].get("actual_source_ids")
    source_context = native_summary["model_protocol"].get("source_evidence") or {}
    context_problem = source_context.get(
        "comparison_problem_sha256",
        native_summary["model_protocol"].get("reference_problem_sha256"),
    )
    if context_problem != analysis_problem["native_problem_sha256"]:
        raise ValueError("native summary belongs to a different physical comparison problem")
    invalid = list(record["diagnostics"].get("invalid_reasons", ()))
    summary_invalid = list(native_summary.get("invalid_reasons", ()))
    source_verified = (
        record["diagnostics"].get("source_verified") is True
        and isinstance(source, str)
        and bool(source)
        and sources == [source]
        and record["metrics"].get("building_first_ignitions", {}).get(source) == 0
        and source_context.get("source_id") == source
    )
    expected_invalid = (
        invalid
        if record["diagnostics"].get("source_verified")
        else [*invalid, "source_not_verified"]
    )
    if summary_invalid != expected_invalid:
        raise ValueError("native invalid reasons differ between record and strict audit")
    if sorted(record["metrics"].get("ignited_core_ids", ())) != sorted(
        native_summary.get("ignited_core_ids", ())
    ):
        raise ValueError("native core failure identities differ")
    certificate = native_summary.get("temporal_closure_certificate")
    physical_closed = _certificate_closed(certificate, native_summary["model_protocol"])
    if strict_complete != (physical_closed and not summary_invalid):
        raise ValueError("preserved native summary contradicts its strict completion rule")
    fingerprints = extinction.extinction_observation_fingerprints(state, peak)
    if physical_closed and (
        any(certificate[k] != v for k, v in fingerprints.items())
        or certificate.get("actual_stop_time_s") != state.time_s
        or certificate.get("actual_step_index") != state.step_index
        or native_summary.get("closure_rejection_reasons") != []
    ):
        raise ValueError("native extinction certificate differs from actual state/peak/time")
    saved = record["diagnostics"].get("temporal_closure_certificate")
    if saved is not None and saved != certificate:
        raise ValueError("native engine certificate differs from the genuine audit")
    if native_summary["engine_certificate_was_present"] != (saved is not None):
        raise ValueError("native engine-certificate lineage differs")
    allowed = invalid in ([], [EDGE_INVALID])
    reasons = []
    if not allowed:
        reasons.append("native_invalid_reasons_other_than_only_domain_edge")
    if not source_verified:
        reasons.append("native_source_not_verified")
    if not physical_closed:
        reasons.append("native_extinction_not_closed")
    complete = not reasons
    failed = bool(native_summary["ignited_core_ids"])
    status = "unknown" if not complete else "failed" if failed else "passed"
    lineage = _lineage(
        analysis_problem,
        native_summary,
        native_check,
        record,
        basis=basis,
        artifact_lineage=artifact_lineage,
    )
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "analysis_problem": deepcopy(analysis_problem),
        "analysis_problem_sha256": analysis_problem["analysis_problem_sha256"],
        "native_problem_sha256": analysis_problem["native_problem_sha256"],
        "finite_domain_sha256": analysis_problem["finite_domain_sha256"],
        "boundary_contract_sha256": BOUNDARY_CONTRACT_SHA256,
        "validator_policy_sha256": VALIDATOR_POLICY_SHA256,
        "validator_sha256": analysis_problem["validator_sha256"],
        "temporal_mode": "finite_domain_closed",
        "plan_id": native_summary["plan_id"],
        "scenario_id": native_summary["scenario_id"],
        "demolished_ids": deepcopy(native_summary["demolished_ids"]),
        "cleared_physical_cell_ids": deepcopy(native_summary.get("cleared_physical_cell_ids", [])),
        "finite_domain_closed": complete,
        "conditional_status": status,
        "protection_feasible": status == "passed",
        "fire_loss_complete": complete,
        "native_strict_status": native_check["status"],
        "native_evaluation_protection_status": record["metrics"]["protection_status"],
        "native_strict_query_semantics": (
            "whole_fire_terminal_audit;not_early_core_protection_query"
        ),
        "native_strict_complete": strict_complete,
        "native_invalid_reasons": invalid,
        "analysis_rejection_reasons": reasons,
        "source_verified": source_verified,
        "source_id": source,
        "ignited_core_ids": deepcopy(native_summary["ignited_core_ids"]),
        "actual_stop_time_s": state.time_s,
        "reached_requested_horizon": native_summary["reached_requested_horizon"],
        "temporal_closure_certificate": deepcopy(certificate),
        "observation_fingerprints": fingerprints,
        "metrics": deepcopy(native_summary["metrics"]),
        "model_protocol": deepcopy(native_summary["model_protocol"]),
        "native_strict_summary": native_summary,
        "native_check": native_check,
        "lineage": lineage,
        "new_CA_checks": 0,
        "global_pareto_frontier_complete": False,
        "real_domain_sufficiency_proven": False,
    }
    summary["analysis_summary_sha256"] = content_hash(summary)
    finite_domain_summary_is_complete(summary)
    return summary


def finite_domain_summary_is_complete(summary):
    """Validate only the new conditional view; native strict flags stay intact."""
    if not isinstance(summary, dict) or summary.get("schema_version") != SUMMARY_SCHEMA:
        raise ValueError("finite-domain analysis summary schema required")
    if (
        summary.get("temporal_mode") != "finite_domain_closed"
        or summary.get("real_domain_sufficiency_proven") is not False
        or summary.get("new_CA_checks") != 0
    ):
        raise ValueError("finite-domain analysis scope must remain explicit and conditional")
    if summary.get("analysis_summary_sha256") != content_hash(
        {k: v for k, v in summary.items() if k != "analysis_summary_sha256"}
    ):
        raise ValueError("finite-domain analysis summary fingerprint differs")
    problem = validate_boundary_scope_problem(summary["analysis_problem"])
    if (
        any(
            summary.get(k) != problem[k]
            for k in (
                "analysis_problem_sha256",
                "native_problem_sha256",
                "finite_domain_sha256",
                "validator_sha256",
            )
        )
        or summary.get("boundary_contract_sha256") != BOUNDARY_CONTRACT_SHA256
        or summary.get("validator_policy_sha256") != VALIDATOR_POLICY_SHA256
    ):
        raise ValueError("finite-domain analysis criterion or problem differs")
    native = summary["native_strict_summary"]
    check = summary["native_check"]
    lineage = summary["lineage"]
    strict_complete = terminal.terminal_summary_is_complete(native)
    if (
        lineage.get("lineage_sha256")
        != content_hash({k: v for k, v in lineage.items() if k != "lineage_sha256"})
        or lineage.get("native_strict_summary_sha256") != content_hash(native)
        or lineage.get("native_check_sha256") != content_hash(check)
        or lineage.get("native_problem_sha256") != problem["native_problem_sha256"]
        or lineage.get("native_case_sha256") != problem["parent_case_sha256"]
        or summary.get("native_strict_status") != check.get("status")
        or check.get("status") != native.get("terminal_status")
        or summary.get("model_protocol") != native.get("model_protocol")
        or summary.get("metrics") != native.get("metrics")
        or summary.get("temporal_closure_certificate") != native.get("temporal_closure_certificate")
        or native.get("fresh_certificate_checked") is not True
        or summary.get("native_strict_complete") is not strict_complete
    ):
        raise ValueError("finite-domain view altered its preserved native lineage")
    for key in (
        "native_record_sha256",
        "native_check_sha256",
        "native_evaluation_sha256",
        "native_physical_request_sha256",
    ):
        _sha(lineage.get(key), key)
    if lineage["native_physical_request_sha256"] != summary["model_protocol"].get(
        "physical_request_sha256"
    ):
        raise ValueError("finite-domain native request lineage differs")
    for key in (
        "plan_id",
        "scenario_id",
        "demolished_ids",
        "cleared_physical_cell_ids",
        "actual_stop_time_s",
        "reached_requested_horizon",
        "ignited_core_ids",
    ):
        if summary.get(key) != native.get(key, [] if key == "cleared_physical_cell_ids" else None):
            raise ValueError("finite-domain plan/scenario observation differs from native")
    closed = _certificate_closed(summary["temporal_closure_certificate"], summary["model_protocol"])
    cert = summary["temporal_closure_certificate"]
    if strict_complete != (closed and not native.get("invalid_reasons")):
        raise ValueError("finite-domain preserved strict completion is inconsistent")
    if (
        summary.get("native_invalid_reasons") != native.get("invalid_reasons")
        and summary.get("source_verified") is True
    ):
        raise ValueError("finite-domain source/invalid flags differ from the native audit")
    context = summary["model_protocol"].get("source_evidence") or {}
    if summary.get("source_verified") is True and (
        not isinstance(summary.get("source_id"), str)
        or not summary["source_id"]
        or context.get("source_id") != summary["source_id"]
        or context.get(
            "comparison_problem_sha256", summary["model_protocol"].get("reference_problem_sha256")
        )
        != problem["native_problem_sha256"]
    ):
        raise ValueError("finite-domain verified source lineage differs")
    if summary.get("source_verified") is True:
        budget = context.get("source_budget", {})
        for key in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J"):
            value = budget.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    "finite-domain verified source lacks a positive native ignition budget"
                )
    if closed and (
        summary["actual_stop_time_s"] != cert["actual_stop_time_s"]
        or summary["reached_requested_horizon"] != cert["reached_requested_horizon"]
        or native.get("closure_rejection_reasons") != []
    ):
        raise ValueError("finite-domain actual closure boundary differs")
    if set(summary["observation_fingerprints"]) != {
        "actual_state_arrays_sha256",
        "historical_valid_temperature_maximum_sha256",
    }:
        raise ValueError("finite-domain state and peak fingerprints are both required")
    if closed and any(cert[k] != v for k, v in summary["observation_fingerprints"].items()):
        raise ValueError("finite-domain observation binding differs from native certificate")
    complete = (
        closed
        and summary["native_invalid_reasons"] in ([], [EDGE_INVALID])
        and native.get("invalid_reasons") == summary["native_invalid_reasons"]
        and summary["source_verified"] is True
        and summary["analysis_rejection_reasons"] == []
    )
    failed = bool(summary["ignited_core_ids"])
    status = "unknown" if not complete else "failed" if failed else "passed"
    if (
        summary.get("finite_domain_closed") is not complete
        or summary.get("conditional_status") != status
        or summary.get("protection_feasible") != (status == "passed")
        or summary.get("fire_loss_complete") is not complete
    ):
        raise ValueError("finite-domain completeness/core status is inconsistent")
    if lineage.get("basis") == "archived_genuine_native_certificate":
        archived = lineage.get("caller_verified_artifact_lineage", {})
        if (
            archived.get("all_hashes_verified") is not True
            or archived.get("native_problem_sha256") != problem["native_problem_sha256"]
            or archived.get("native_case_sha256") != problem["parent_case_sha256"]
        ):
            raise ValueError("finite-domain archive verification receipt differs")
        for key in ("source_snapshot_sha256", "input_manifest_sha256", "artifact_manifest_sha256"):
            _sha(archived.get(key), key)
    elif lineage.get("basis") != "fresh_native_equations":
        raise ValueError("finite-domain observation has no verified provenance basis")
    return complete


def finite_domain_observation_is_complete(summary, state, maximum_valid_surface_temperature_K):
    complete = finite_domain_summary_is_complete(summary)
    if (
        summary["observation_fingerprints"]
        != extinction.extinction_observation_fingerprints(
            state, maximum_valid_surface_temperature_K
        )
        or summary["actual_stop_time_s"] != state.time_s
    ):
        raise ValueError("finite-domain view differs from loaded state or peak arrays")
    if (
        complete
        and summary["temporal_closure_certificate"]["actual_step_index"] != state.step_index
    ):
        raise ValueError("finite-domain actual solver step differs")
    return complete


def finite_domain_protection_query(summary):
    """Separate past-core failure from complete whole-fire outcome claims.

    A core already ignited remains a failed protection query even if that fire
    is unfinished. Its final fire losses remain incomplete. A passing query
    still requires this view's verified extinction and zero core ignitions.
    This receipt supplies no physical-family failure-cut certificate.
    """
    finite_domain_summary_is_complete(summary)
    admissible = (
        summary["source_verified"] is True
        and summary["native_invalid_reasons"] in ([], [EDGE_INVALID])
        and summary["native_strict_summary"]["invalid_reasons"] == summary["native_invalid_reasons"]
    )
    status = (
        "failed"
        if admissible and summary["ignited_core_ids"]
        else "passed"
        if admissible and summary["finite_domain_closed"]
        else "unknown"
    )
    value = {
        "schema_version": "heritage-finite-domain-protection-query-1",
        "analysis_problem_sha256": summary["analysis_problem_sha256"],
        "native_problem_sha256": summary["native_problem_sha256"],
        "finite_domain_view_sha256": summary["analysis_summary_sha256"],
        "protection_query_status": status,
        "finite_domain_closed": summary["finite_domain_closed"],
        "fire_loss_complete": summary["fire_loss_complete"],
        "ignited_core_ids": deepcopy(summary["ignited_core_ids"]),
        "actual_stop_time_s": summary["actual_stop_time_s"],
        "failure_basis": "observed_past_core_ignition" if status == "failed" else None,
        "future_core_safety_claimed": status == "passed",
        "failure_family_cut_provided": False,
    }
    value["protection_query_sha256"] = content_hash(value)
    return value


def finite_domain_protection_query_status(query, summary):
    """Check a typed query receipt without treating a failed prefix as closed."""
    if query != finite_domain_protection_query(summary):
        raise ValueError("finite-domain protection query differs from its native view")
    return query["protection_query_status"]


def _mip_receipt_block(summary, query):
    value = {
        "schema_version": "heritage-finite-domain-mip-receipt-1",
        "analysis_problem_sha256": summary["analysis_problem_sha256"],
        "native_problem_sha256": summary["native_problem_sha256"],
        "finite_domain_view_sha256": summary["analysis_summary_sha256"],
        "native_check_sha256": summary["lineage"]["native_check_sha256"],
        "protection_query_sha256": query["protection_query_sha256"],
        "protection_query_status": query["protection_query_status"],
        "native_invalid_reasons": deepcopy(summary["native_invalid_reasons"]),
        "fire_loss_complete": summary["fire_loss_complete"],
        "source_id": summary["source_id"],
    }
    value["receipt_sha256"] = content_hash(value)
    return value


def make_finite_domain_mip_check(summary):
    """Create a new mathematical-criterion receipt, never a rewritten raw check.

    The distinct evidence schema deliberately has no native ``invalid_reasons``
    field. Native flags and the complete original native check remain under
    their explicit names. Additional loss metrics may be attached by callers;
    edge-invalid failures may only receive exact-plan exclusions, not a strong
    physical-family certificate from the strict joint-cut schema.
    """
    query = finite_domain_protection_query(summary)
    evidence = {
        "schema_version": "heritage-finite-domain-mip-receipt-1",
        "problem_sha256": summary["analysis_problem_sha256"],
        "source_id": summary["source_id"],
        "native_strict_status": summary["native_strict_status"],
        "native_invalid_reasons": deepcopy(summary["native_invalid_reasons"]),
        "native_check": deepcopy(summary["native_check"]),
        "finite_domain_verification": deepcopy(summary),
        "finite_domain_protection_query": query,
        "finite_domain_mip_receipt": _mip_receipt_block(summary, query),
        "simulated_until_s": summary["actual_stop_time_s"],
        "reached_horizon": summary["reached_requested_horizon"],
        "fire_loss_complete": summary["fire_loss_complete"],
    }
    check = ProtectionCheck(query["protection_query_status"], evidence)
    validate_finite_domain_mip_check(check)
    return check


def validate_finite_domain_mip_check(check, *, analysis_problem_sha256=None):
    """Validate the sealed required blocks while allowing independent extra metrics."""
    check = _mapping(check)
    evidence = check.get("evidence", {})
    if (
        evidence.get("schema_version") != "heritage-finite-domain-mip-receipt-1"
        or "invalid_reasons" in evidence
    ):
        raise ValueError(
            "new finite-domain mathematical receipt required;native flags cannot be cleared"
        )
    summary = evidence["finite_domain_verification"]
    query = evidence["finite_domain_protection_query"]
    status = finite_domain_protection_query_status(query, summary)
    if (
        check.get("status") != status
        or evidence.get("problem_sha256") != summary["analysis_problem_sha256"]
        or analysis_problem_sha256 not in (None, summary["analysis_problem_sha256"])
        or evidence.get("source_id") != summary["source_id"]
        or evidence.get("native_invalid_reasons") != summary["native_invalid_reasons"]
        or evidence.get("native_strict_status") != summary["native_strict_status"]
        or evidence.get("native_check") != summary["native_check"]
        or evidence.get("finite_domain_mip_receipt") != _mip_receipt_block(summary, query)
        or evidence.get("simulated_until_s") != summary["actual_stop_time_s"]
        or evidence.get("reached_horizon") != summary["reached_requested_horizon"]
        or evidence.get("fire_loss_complete") != summary["fire_loss_complete"]
    ):
        raise ValueError("finite-domain mathematical receipt changed its criterion/native lineage")
    certificate = evidence.get("failure_certificate")
    if certificate is not None and (
        status != "failed"
        or summary["native_invalid_reasons"] != []
        or certificate.get("schema_version") != "heritage-joint-physical-node-removal-cut-1"
        or certificate.get("problem_sha256") != summary["analysis_problem_sha256"]
        or certificate.get("source_id") != summary["source_id"]
    ):
        raise ValueError(
            "edge-invalid finite-domain query cannot carry a strict strong-cut certificate"
        )
    return status


@dataclass(frozen=True)
class BoundaryScopeVerification:
    check: ProtectionCheck
    summary: dict
    fire_loss: dict
    strict_verification: object = None


def _validate_fire_loss(summary, loss):
    if (
        not isinstance(loss, dict)
        or loss.get("saved_final_time_s") != summary["actual_stop_time_s"]
    ):
        raise ValueError("finite-domain fire loss differs from native observation time")
    metrics = summary["metrics"]
    counts = loss["physical_remaining_roof_annotation_counts"]
    if (
        metrics["N_burned_out_annotation_attribution_count"]
        != counts["burned_out_annotation_attribution_count"]
        or metrics["N_ignited_annotation_attribution_count"]
        != counts["ignited_annotation_attribution_count"]
        or metrics["burned_out_physical_covered_area_m2"]
        != loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
        or metrics["A_ignited_physical_covered_area_m2"]
        != loss["physical"]["all"]["cumulative_ignited_covered_area_m2"]
        or (
            summary["finite_domain_closed"]
            and loss["physical"]["all"]["active_burning_covered_area_m2"] != 0
        )
    ):
        raise ValueError("finite-domain native audited fire loss C/N/A differs")


def audit_finite_domain_observation(
    native_summary,
    native_check,
    record,
    state,
    maximum_valid_surface_temperature_K,
    *,
    analysis_problem,
    source_evidence,
    artifact_lineage,
    fire_loss,
):
    """Build a conditional view from caller-verified immutable native artifacts."""
    summary = _make_view(
        native_summary,
        native_check,
        record,
        state,
        maximum_valid_surface_temperature_K,
        analysis_problem=analysis_problem,
        source_evidence=source_evidence,
        basis="archived_genuine_native_certificate",
        artifact_lineage=artifact_lineage,
    )
    _validate_fire_loss(summary, fire_loss)
    return BoundaryScopeVerification(
        ProtectionCheck(summary["conditional_status"], summary), summary, deepcopy(fire_loss)
    )


def audit_finite_domain_result(
    engine,
    scenario,
    plan,
    record,
    layout,
    result,
    *,
    contact,
    analysis_problem,
    source_evidence,
    reference_horizon_s=None,
):
    """Fresh equation-based audit; original native records are never rewritten."""
    validate_boundary_scope_problem(analysis_problem)
    if (
        request_fingerprint(engine.bundle.request) != analysis_problem["parent_case_sha256"]
        or finite_domain_fingerprint(engine.bundle.request)
        != analysis_problem["finite_domain_sha256"]
    ):
        raise ValueError("finite-domain problem does not match this native engine")
    source_evidence = deepcopy(source_evidence)
    if not isinstance(source_evidence, dict) or not source_evidence.get("source_id"):
        raise ValueError("finite-domain audit requires the actual frozen source")
    if (
        source_evidence.get("comparison_problem_sha256", analysis_problem["native_problem_sha256"])
        != analysis_problem["native_problem_sha256"]
    ):
        raise ValueError("finite-domain actual source problem differs")
    source_evidence["comparison_problem_sha256"] = analysis_problem["native_problem_sha256"]
    strict = terminal.audit_terminal_result(
        engine,
        scenario,
        plan,
        record,
        layout,
        result,
        contact=contact,
        reference_horizon_s=reference_horizon_s,
        reference_problem_sha256=analysis_problem["native_problem_sha256"],
        source_evidence=source_evidence,
    )
    ignited = np.isfinite(result.final_state.ignition_time_s.ravel())
    grid = layout.physical_request.grid
    rows, cols = np.divmod(layout.cell_id[ignited], grid.width)
    edge = bool(
        np.any((rows == 0) | (rows == grid.height - 1) | (cols == 0) | (cols == grid.width - 1))
    )
    if edge != (EDGE_INVALID in record.diagnostics["invalid_reasons"]):
        raise ValueError("native edge guard differs from actual physical boundary ignition")
    summary = _make_view(
        strict.summary,
        strict.check,
        record,
        result.final_state,
        record.diagnostics["maximum_valid_surface_temperature_by_node_K"],
        analysis_problem=analysis_problem,
        source_evidence=source_evidence,
        basis="fresh_native_equations",
    )
    _validate_fire_loss(summary, strict.fire_loss)
    return BoundaryScopeVerification(
        ProtectionCheck(summary["conditional_status"], summary), summary, strict.fire_loss, strict
    )
