"""Compose complete native cases under one explicitly audited discrete model.

Original summaries, artifacts and native problem identities are never changed.
The optional clock bridge accepts exactly the audited former-error-path return.
Only all-scenario, closed, core-safe action sets enter the evaluated frontier.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import merge_heritage_joint_pareto as catalogs

single = catalogs.single
SOLVER = "src/pyfireca/village/solver_v3.py"
ADDED_CLOCK_BRANCH = (
    b"            # Adding tolerance can round up to a still-future due time. Only\n"
    b"            # extend this former error path; every old successful path is exact.\n"
    b"            if state.time_s < due:\n"
    b"                return\n"
)
OLD_CLOCK_GUARD = (
    b"        if state.time_s + tolerance < due:\n"
    b"            return\n"
    b"        if abs(state.time_s - due) > tolerance:\n"
    b'            raise RuntimeError("v3 solver stepped past an exact sample time")\n'
)


def verify_clock_bridge(audit_path, solver_paths):
    audit_path = Path(audit_path).resolve()
    audit = single.read_json(audit_path)
    if (
        audit.get("schema_version") != "v3-sample-clock-error-path-extension-audit-1"
        or audit.get("audit_sha256")
        != single.content_hash({k: v for k, v in audit.items() if k != "audit_sha256"})
        or audit.get("exact_added_utf8") != ADDED_CLOCK_BRANCH.decode()
    ):
        raise ValueError("clock compatibility audit schema or seal differs")
    old_sha, new_sha = audit["old_solver_sha256"], audit["new_solver_sha256"]
    payloads = {}
    for path in solver_paths:
        payload = Path(path).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest not in {old_sha, new_sha}:
            raise ValueError("clock bridge cannot allow any other solver bytes")
        payloads[digest] = payload
    if set(payloads) != {old_sha, new_sha}:
        raise ValueError("clock bridge requires both registered native solver versions")
    old, new = payloads[old_sha], payloads[new_sha]
    expected_guard = OLD_CLOCK_GUARD.replace(
        b"            raise RuntimeError", ADDED_CLOCK_BRANCH + b"            raise RuntimeError"
    )
    if (
        old.count(OLD_CLOCK_GUARD) != 1
        or new.count(expected_guard) != 1
        or new.count(ADDED_CLOCK_BRANCH) != 1
        or new.replace(ADDED_CLOCK_BRANCH, b"", 1) != old
    ):
        raise ValueError("clock compatibility must be exactly the four-line error-path addition")
    result = {
        "schema_version": "native-clock-successful-trajectory-bridge-1",
        "original_audit_path": str(audit_path),
        "original_audit_file_sha256": single.file_sha256(audit_path),
        "original_audit_seal": audit["audit_sha256"],
        "old_solver_sha256": old_sha,
        "canonical_extended_solver_sha256": new_sha,
        "entire_file_identity_after_removing_exact_added_branch": True,
        "old_successful_execution_never_enters_modified_exception_branch": True,
        "old_successful_native_trajectory_is_bitwise_unchanged_given_same_runtime_and_inputs": True,
        "newly_defined_error_path_has_no_old_completed_result_to_transfer": True,
        "old_failed_partial_artifacts_are_not_native_cases": True,
        "other_physical_source_differences_permitted": False,
    }
    result["certificate_sha256"] = single.content_hash(result)
    return result


def canonical_equivalence(audits, *, expected_scenario_count=8, clock_bridge=None):
    if clock_bridge is not None and (
        clock_bridge.get("schema_version") != "native-clock-successful-trajectory-bridge-1"
        or clock_bridge.get("certificate_sha256")
        != single.content_hash({k: v for k, v in clock_bridge.items() if k != "certificate_sha256"})
    ):
        raise ValueError("exact clock bridge certificate seal differs")
    normalized = deepcopy(audits)
    for original, canonical in zip(audits, normalized, strict=True):
        if original["physical_protocol_sha256"] != single.content_hash(original["protocol"]):
            raise ValueError("original native protocol audit fingerprint differs")
        if clock_bridge is not None:
            source = canonical["protocol"]["source_sha256"]
            if source.get(SOLVER) not in {
                clock_bridge["old_solver_sha256"],
                clock_bridge["canonical_extended_solver_sha256"],
            }:
                raise ValueError("native solver is outside the exact registered clock bridge")
            source[SOLVER] = clock_bridge["canonical_extended_solver_sha256"]
            canonical["physical_protocol_sha256"] = single.content_hash(canonical["protocol"])
    receipt = catalogs.verify_equivalence(
        normalized, expected_scenario_count=expected_scenario_count
    )
    receipt["native_physical_protocol_sha256s"] = sorted(
        {a["physical_protocol_sha256"] for a in audits}
    )
    receipt["clock_successful_trajectory_bridge"] = deepcopy(clock_bridge)
    receipt["original_native_protocol_audits_modified"] = False
    return receipt


def native_case_worker(run_root, config_path, output):
    """Original snapshot reader plus exact saved native state/peak identities."""
    with tempfile.TemporaryDirectory(prefix="native-case-protocol-") as temporary:
        path = Path(temporary) / "protocol.json"
        catalogs.native_protocol_worker(run_root, config_path, path)
        result = single.read_json(path)
    # The protocol worker has selected this run's immutable import roots.
    import numpy as np
    import run_heritage_joint_comparison as producer
    import scipy
    import shapely

    from pyfireca.village.extinction_v3 import (
        extinction_certificate_sha256,
        extinction_observation_fingerprints,
    )
    from pyfireca.village.solver_v3 import V3State

    experiment = single.verify_header(run_root, config_path)
    result["protocol"]["nonrunner_script_source_sha256"] = {
        name: digest
        for name, digest in experiment["source_sha256"].items()
        if name.startswith("scripts/") and name != "scripts/run_heritage_joint_comparison.py"
    }
    result["physical_protocol_sha256"] = single.content_hash(result["protocol"])
    plans = {p.plan_id: p for p in map(producer.plan_from_mapping, experiment["config"]["plans"])}
    result["physical_plan_contracts"] = {
        name: {
            "demolished_ids": sorted(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "ground_policy": plan.ground_policy,
            "overlap_policy": plan.overlap_policy,
        }
        for name, plan in plans.items()
    }
    for binding in result["artifact_bindings"]:
        folder = producer.result_folder(run_root, plans[binding["plan_id"]], binding["scenario_id"])
        manifest_path = folder / "complete.json"
        if single.file_sha256(manifest_path) != binding["manifest_sha256"]:
            raise ValueError("case manifest changed after native protocol verification")
        manifest = single.read_json(manifest_path)

        def verified_bytes(name, *, folder=folder, manifest=manifest):
            data = (folder / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest["files_sha256"][name]:
                raise ValueError("native case artifact changed: " + name)
            return data

        record = json.loads(verified_bytes("evaluation.json"))
        check = json.loads(verified_bytes("check.json"))
        with np.load(io.BytesIO(verified_bytes("final-state.npz")), allow_pickle=False) as saved:
            values = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
        values["time_s"], values["step_index"] = float(values["time_s"]), int(values["step_index"])
        state = V3State(**values)
        observation = extinction_observation_fingerprints(
            state, record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"]
        )
        terminal = check["evidence"]["terminal_verification"]
        certificate = terminal["temporal_closure_certificate"]
        if certificate is not None and (
            certificate["certificate_sha256"] != extinction_certificate_sha256(certificate)
            or any(certificate[k] != v for k, v in observation.items())
            or certificate["actual_stop_time_s"] != state.time_s
            or certificate["actual_step_index"] != state.step_index
        ):
            raise ValueError("native case certificate differs from its saved state/peak")
        binding["native_behavior_fingerprint"] = {
            "physical_request_sha256": record["diagnostics"]["physical_request_sha256"],
            "state_and_peak_fingerprints": observation,
            "actual_stop_time_s": state.time_s,
            "actual_step_index": state.step_index,
            "actual_source": binding["native_actual_source"],
            "core_outcomes": record["metrics"]["core_outcomes"],
            "native_invalid_reasons": record["diagnostics"]["invalid_reasons"],
        }
        binding["native_check_sha256"] = single.content_hash(check)
        binding["native_evaluation_file_sha256"] = manifest["files_sha256"]["evaluation.json"]
        binding["native_certificate_sha256"] = (
            None if certificate is None else certificate["certificate_sha256"]
        )
        binding["manifest_path"] = str(manifest_path)
    result["reader_runtime"] = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "shapely_version": shapely.__version__,
        "geos_version": shapely.geos_version_string,
        "machine": platform.machine(),
    }
    single.verify_header(run_root, config_path)
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))


def verify_runtime_attestation(path, reports, audits):
    value = single.read_json(path)
    if (
        value.get("schema_version") != "heritage-native-runtime-attestation-1"
        or value.get("receipt_sha256")
        != single.content_hash({k: v for k, v in value.items() if k != "receipt_sha256"})
        or not {r["native_comparison_problem_sha256"] for r in reports}
        <= set(value.get("native_problem_sha256s", ()))
        or not value.get("attestation_basis")
        or not value.get("evidence_references")
        or not value.get("attested_by")
    ):
        raise ValueError("explicit original-runtime controller attestation is missing or invalid")
    runtime = value.get("runtime")
    required_runtime = {
        "python_executable",
        "python_version",
        "numpy_version",
        "scipy_version",
        "shapely_version",
        "geos_version",
        "machine",
    }
    if (
        not isinstance(runtime, dict)
        or set(runtime) != required_runtime
        or any(not isinstance(v, str) or not v for v in runtime.values())
    ):
        raise ValueError("attested native runtime requires its actual Python executable")
    resolved = {
        **runtime,
        "python_executable": str(Path(runtime["python_executable"]).resolve()),
    }
    if any(a["reader_runtime"] != resolved for a in audits):
        raise ValueError("current native readers differ from the attested original runtime")
    for evidence in value["evidence_references"]:
        if single.file_sha256(evidence["path"]) != evidence["sha256"]:
            raise ValueError("runtime controller attestation evidence changed")
    return {
        "schema_version": "native-runtime-attestation-verification-1",
        "attestation_path": str(Path(path).resolve()),
        "attestation_file_sha256": single.file_sha256(path),
        "controller_attestation": value,
        "resolved_runtime": resolved,
        "each_current_native_reader_matches_attested_original_runtime": True,
        "original_archives_embedded_this_runtime_observation": False,
    }


def _rows(report):
    return [
        row
        for field in ("fully_evaluated_feasible_plans", "unresolved_plans", "failed_plans")
        for row in report["archive"][field]
    ]


def _rank(feasible, unresolved):
    def dominates(a, b):
        return all(x <= y for x, y in zip(a, b, strict=True)) and any(
            x < y for x, y in zip(a, b, strict=True)
        )

    def lower(row):
        return [
            row["cleared_area_m2"],
            row["worst_case"]["N_burned_out"]["lower"],
            row["worst_case"]["A_burned_out_m2"],
        ]

    for row in feasible:
        row["upper_bound_dominated_by"] = sorted(
            other["plan_id"]
            for other in feasible
            if other is not row and dominates(other["objective_values"], row["objective_values"])
        )
        row["robustly_dominated_by"] = sorted(
            other["plan_id"]
            for other in feasible
            if other is not row and dominates(other["objective_values"], lower(row))
        )
        row["possibly_dominated_by"] = sorted(
            other["plan_id"]
            for other in feasible
            if other is not row and dominates(lower(other), row["objective_values"])
        )
        row["unresolved_potential_dominator_ids"] = sorted(
            other["plan_id"]
            for other in unresolved
            if other["cleared_area_m2"] <= row["cleared_area_m2"]
        )
    feasible.sort(key=lambda row: (row["objective_values"], row["plan_id"]))


def compose_cases(reports, audits, receipt):
    if not reports or len(reports) != len(audits):
        raise ValueError("each native report requires its independently verified case audit")
    mode, scenarios = reports[0]["temporal_mode"], receipt["scenario_ids"]
    if mode not in {"strict", "finite_domain_closed"} or any(
        r["temporal_mode"] != mode for r in reports
    ):
        raise ValueError("strict and finite-domain cases cannot be pooled")
    groups, lineage, unrepresented = {}, [], None
    for index, (report, audit) in enumerate(zip(reports, audits, strict=True)):
        if report["archive"]["comparison_protocol"]["scenario_ids"] != scenarios:
            raise ValueError("native report scenario set differs from the common physical protocol")
        scope = report["archive"]["unrepresented_original_ids"]
        if scope is not None:
            if unrepresented is not None and scope != unrepresented:
                raise ValueError("unrepresented physical owner scopes differ")
            unrepresented = scope
        batch = f"batch{index + 1}"
        lineage.append(
            {
                "batch_id": batch,
                "run_root": report["run_root"],
                "native_problem_sha256": report["native_comparison_problem_sha256"],
                "analysis_problem_sha256": report.get("analysis_problem_sha256"),
                "independent_report_sha256": single.content_hash(report),
            }
        )
        native = {(c["plan_id"], c["scenario_id"]): c for c in report["native_checks"]}
        conditional = {
            (c["plan_id"], c["scenario_id"]): c for c in report.get("finite_domain_checks", ())
        }
        bindings = {(c["plan_id"], c["scenario_id"]): c for c in audit["artifact_bindings"]}
        for row in _rows(report):
            name = row["plan_id"]
            contract = audit["physical_plan_contracts"][name]
            if (
                contract["demolished_ids"] != row["demolished_ids"]
                or contract["cleared_physical_cell_ids"] != row["cleared_physical_cell_ids"]
            ):
                raise ValueError("native plan row differs from its registered physical actions")
            action = single.content_hash(contract)
            group = groups.setdefault(
                action,
                {
                    "contract": contract,
                    "area": row["cleared_area_m2"],
                    "aliases": [],
                    "cases": {s: [] for s in scenarios},
                },
            )
            if group["contract"] != contract or group["area"] != row["cleared_area_m2"]:
                raise ValueError("same physical action has inconsistent construction geometry")
            alias = {
                "batch_id": batch,
                "native_plan_id": name,
                "native_problem_sha256": report["native_comparison_problem_sha256"],
                "analysis_problem_sha256": report.get("analysis_problem_sha256"),
                "run_root": report["run_root"],
            }
            group["aliases"].append(alias)
            for scenario in scenarios:
                key = name, scenario
                raw = native[key]
                binding = bindings.get(key)
                observation = row["scenario_observations"].get(scenario)
                condition = conditional.get(key)
                if observation is not None:
                    admissible = (
                        raw.get("terminal_complete") is True and raw["status"] == "passed"
                        if mode == "strict"
                        else condition is not None
                        and condition["finite_domain_closed"] is True
                        and condition["conditional_status"] == "passed"
                    )
                    if not admissible or binding is None:
                        raise ValueError("passed case is not independently complete and core safe")
                    status = "passed"
                else:
                    failed = (
                        raw.get("terminal_complete") is True and raw["status"] == "failed"
                        if mode == "strict"
                        else condition is not None
                        and condition["finite_domain_closed"] is True
                        and condition["conditional_status"] == "failed"
                    )
                    if failed and binding is None:
                        raise ValueError(
                            "failed case lacks its independently verified native record"
                        )
                    status = "failed" if failed else "unknown"
                group["cases"][scenario].append(
                    {
                        **alias,
                        "scenario_id": scenario,
                        "status": status,
                        "native_strict_status": raw["status"],
                        "native_invalid_reasons": deepcopy(raw.get("invalid_reasons")),
                        "native_reason": raw.get("reason"),
                        "observation": deepcopy(observation),
                        "binding": deepcopy(binding),
                    }
                )
    rows = []
    for action, group in groups.items():
        observations, evidence, reasons, failed = {}, {}, [], False
        for scenario, candidates in group["cases"].items():
            actual = [c for c in candidates if c["binding"] is not None]
            if actual:
                first = actual[0]
                for other in actual[1:]:
                    if (
                        single.content_hash(other["binding"]["native_behavior_fingerprint"])
                        != single.content_hash(first["binding"]["native_behavior_fingerprint"])
                        or other["status"] != first["status"]
                        or single.content_hash(other["observation"])
                        != single.content_hash(first["observation"])
                    ):
                        raise ValueError(
                            "contradictory duplicate physical case; "
                            "choosing a preferred run is forbidden"
                        )
                if any(
                    c["native_reason"] == "no_retained_source_in_frozen_prefix" for c in candidates
                ):
                    raise ValueError(
                        "same physical case has both a source-empty and actual-source result"
                    )
                selected = first
            else:
                selected = candidates[0]
            evidence[scenario] = {
                "status": selected["status"],
                "original_native_cases": candidates,
                "duplicate_actual_case_count": max(0, len(actual) - 1),
            }
            if selected["status"] == "passed":
                observations[scenario] = selected["observation"]
            else:
                failed |= selected["status"] == "failed"
                reasons.append(
                    {
                        "scenario_id": scenario,
                        "reason": "core_failure_at_closure"
                        if selected["status"] == "failed"
                        else "no_complete_admissible_native_case",
                    }
                )
        feasible = not reasons and len(observations) == len(scenarios)
        row = {
            "plan_id": "action-" + action[:20],
            "physical_action_sha256": action,
            "demolished_ids": group["contract"]["demolished_ids"],
            "cleared_physical_cell_ids": group["contract"]["cleared_physical_cell_ids"],
            "physical_intervention_contract": group["contract"],
            "plan_aliases": group["aliases"],
            "cleared_area_m2": group["area"],
            "construction_area_basis": "geometric_action_union_m2",
            "status": "passed" if feasible else "failed" if failed else "unknown",
            "unresolved_or_failure_reasons": reasons,
            "scenario_observations": observations,
            "case_composition_evidence": evidence,
            "objective_values": None,
            "worst_case": None,
        }
        if feasible:
            worst = {
                field: max(v[field] for v in observations.values())
                for field in (
                    "A_burned_out_m2",
                    "A_affected_m2",
                    "A_active_m2",
                    "fire_depleted_pyrolysable_fuel_kg",
                )
            }
            for field in ("N_burned_out", "N_affected"):
                worst[field] = {
                    side: max(v[field][side] for v in observations.values())
                    for side in ("lower", "upper")
                }
            if worst["A_active_m2"] != 0:
                raise ValueError("case-composed terminal plan still has active burning area")
            row["worst_case"] = worst
            row["objective_values"] = [
                group["area"],
                worst["N_burned_out"]["upper"],
                worst["A_burned_out_m2"],
            ]
        rows.append(row)
    if len({r["plan_id"] for r in rows}) != len(rows):
        raise ValueError("physical action ID prefix collision")
    feasible = [r for r in rows if r["status"] == "passed"]
    unresolved = [r for r in rows if r["status"] == "unknown"]
    _rank(feasible, unresolved)
    return {
        "schema_version": "heritage-case-composed-evaluated-pareto-1",
        "temporal_mode": mode,
        "lineage": lineage,
        "objective_names": reports[0]["archive"]["objective_names"],
        "loss_aggregation": "componentwise_maximum_over_every_declared_scenario",
        "objective_dominance_tolerance": 0,
        "unrepresented_original_ids": unrepresented,
        "non_dominated_archive": [r for r in feasible if not r["upper_bound_dominated_by"]],
        "robust_non_dominated_archive": [r for r in feasible if not r["robustly_dominated_by"]],
        "possible_dominance_free_archive": [r for r in feasible if not r["possibly_dominated_by"]],
        "fully_evaluated_feasible_plans": feasible,
        "unresolved_plans": unresolved,
        "failed_plans": [r for r in rows if r["status"] == "failed"],
        "declared_unique_physical_action_count": len(rows),
        "complete_feasible_plan_count": len(feasible),
        "global_pareto_frontier_complete": False,
        "global_minimum_area_proven": False,
        "native_summaries_and_problem_identities_modified": False,
        "outcome_scope": (
            "fixed_finite_native_domain_without_external_fire_or_heat_feedback"
            if mode == "finite_domain_closed"
            else "native_strict_domain_acceptance"
        ),
        "N_scope": (
            "physical_original_owner_burned_out_attribution_bounds;"
            "not_real_structural_destruction_or_demolition_count"
        ),
        "area_anchor_optimality_transferred": False,
        "new_CA_checks": 0,
    }


def compose_catalogs(
    inputs,
    output,
    *,
    temporal_mode="strict",
    expected_scenario_count=8,
    clock_compatibility=None,
    runtime_attestation=None,
):
    output = Path(output).resolve()
    inputs = [
        {"run_root": Path(c["run_root"]).resolve(), "config": Path(c["config"]).resolve()}
        for c in inputs
    ]
    sources = {
        str(p.resolve()): single.file_sha256(p)
        for p in (Path(__file__), Path(catalogs.__file__), Path(single.__file__))
    }
    protected = {
        *map(Path, sources),
        *[Path(p).resolve() for p in (clock_compatibility, runtime_attestation) if p is not None],
    }
    if runtime_attestation is not None:
        runtime_claim = single.read_json(runtime_attestation)
        protected.update(
            Path(row["path"]).resolve() for row in runtime_claim.get("evidence_references", ())
        )
        if runtime_claim.get("runtime", {}).get("python_executable"):
            protected.add(Path(runtime_claim["runtime"]["python_executable"]).resolve())
    for item in inputs:
        experiment = single.verify_header(item["run_root"], item["config"])
        if output.is_relative_to(item["run_root"]) or output in protected | {
            item["config"],
            *[Path(p).resolve() for p in experiment["references_sha256"]],
        }:
            raise ValueError(
                "composition output cannot overwrite original archives, inputs or analysis sources"
            )
    started, reports, audits = datetime.now(UTC).isoformat(), [], []
    with tempfile.TemporaryDirectory(prefix="joint-case-composition-") as temporary:
        for index, item in enumerate(inputs):
            reports.append(
                single.summarize(
                    item["run_root"],
                    item["config"],
                    Path(temporary) / f"report{index}.json",
                    temporal_mode=temporal_mode,
                )
            )
            target = Path(temporary) / f"cases{index}.json"
            process = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--native-case-worker",
                    "--run-root",
                    str(item["run_root"]),
                    "--config",
                    str(item["config"]),
                    "--output",
                    str(target),
                ],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONPATH": str(item["run_root"] / "source-snapshot/src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            if process.returncode:
                raise ValueError(
                    "native case verification failed:\n" + process.stdout + process.stderr
                )
            audits.append(single.read_json(target))
    clock = (
        None
        if clock_compatibility is None
        else verify_clock_bridge(
            clock_compatibility, [item["run_root"] / "source-snapshot" / SOLVER for item in inputs]
        )
    )
    receipt = canonical_equivalence(
        audits, expected_scenario_count=expected_scenario_count, clock_bridge=clock
    )
    if runtime_attestation is None:
        raise ValueError("case composition requires an explicit original-runtime attestation")
    runtime = verify_runtime_attestation(runtime_attestation, reports, audits)
    receipt["runtime_attestation"] = runtime
    receipt["certificate_sha256"] = single.content_hash(receipt)
    archive = compose_cases(reports, audits, receipt)
    upstream_sources = {}
    for report in reports:
        for name, digest in report["analysis_source_sha256"].items():
            path = str((single.ROOT / name).resolve())
            if path in upstream_sources and upstream_sources[path] != digest:
                raise ValueError("native analysis criteria changed between case batches")
            upstream_sources[path] = digest
    problem = {
        "schema_version": "heritage-joint-case-composition-problem-1",
        "temporal_mode": temporal_mode,
        "canonical_physical_protocol_sha256": receipt["physical_protocol_sha256"],
        "clock_bridge_sha256": None if clock is None else clock["certificate_sha256"],
        "runtime_attestation_file_sha256": runtime["attestation_file_sha256"],
        "scenario_ids": receipt["scenario_ids"],
        "physical_action_sha256s": sorted(
            r["physical_action_sha256"]
            for field in ("fully_evaluated_feasible_plans", "unresolved_plans", "failed_plans")
            for r in archive[field]
        ),
        "native_problem_sha256s": sorted({r["native_comparison_problem_sha256"] for r in reports}),
        "native_criterion_analysis_problem_sha256s": sorted(
            {r["analysis_problem_sha256"] for r in reports if r["analysis_problem_sha256"]}
        ),
        "analysis_source_sha256": sources,
        "upstream_analysis_criteria_source_sha256": upstream_sources,
        "criterion": (
            "all_declared_cases_closed_core_safe;worst_case_C_Nupper_A;finite_evaluated_set_only"
        ),
    }
    problem["analysis_problem_sha256"] = single.content_hash(problem)
    for item, report in zip(inputs, reports, strict=True):
        single.verify_header(item["run_root"], item["config"])
        for observed in report["native_checks"]:
            if observed.get("manifest"):
                manifest = Path(observed["manifest"])
                if single.file_sha256(manifest) != observed["manifest_sha256"]:
                    raise ValueError("native manifest changed during case composition")
                for name, sha in single.read_json(manifest).get("files_sha256", {}).items():
                    if single.file_sha256(manifest.parent / name) != sha:
                        raise ValueError("native artifact changed during case composition")
    if any(single.file_sha256(path) != sha for path, sha in (sources | upstream_sources).items()):
        raise ValueError("case-composition implementation changed during analysis")
    result = {
        "schema_version": "heritage-joint-case-composition-receipt-1",
        "temporal_mode": temporal_mode,
        "analysis_problem": problem,
        "analysis_problem_sha256": problem["analysis_problem_sha256"],
        "physical_equivalence_certificate": receipt,
        "independently_reverified_native_reports": reports,
        "native_case_audits": audits,
        "archive": archive,
        "new_CA_checks": 0,
        "original_run_writes": 0,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
    }
    result["composition_receipt_sha256"] = single.content_hash(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--temporal-mode", choices=("strict", "finite_domain_closed"), default="strict"
    )
    parser.add_argument("--expected-scenario-count", type=int, default=8)
    parser.add_argument("--clock-compatibility", type=Path)
    parser.add_argument("--runtime-attestation", type=Path)
    parser.add_argument("--native-case-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.native_case_worker:
        native_case_worker(args.run_root.resolve(), args.config.resolve(), args.output.resolve())
        return
    if args.catalogs is None:
        parser.error("--catalogs is required")
    if args.catalogs.resolve() == args.output.resolve():
        parser.error("composition output cannot overwrite its catalog registration")
    rows = single.read_json(args.catalogs)
    inputs = [
        {
            key: (args.catalogs.resolve().parent / row[key]).resolve()
            for key in ("run_root", "config")
        }
        for row in rows
    ]
    result = compose_catalogs(
        inputs,
        args.output,
        temporal_mode=args.temporal_mode,
        expected_scenario_count=args.expected_scenario_count,
        clock_compatibility=args.clock_compatibility,
        runtime_attestation=args.runtime_attestation,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "new_CA_checks": 0,
                "complete_feasible_plans": result["archive"]["complete_feasible_plan_count"],
                "frontier_plan_ids": [
                    r["plan_id"] for r in result["archive"]["non_dominated_archive"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
