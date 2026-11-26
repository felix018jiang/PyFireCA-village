"""Postprocess fully evaluated construction/fire-loss tradeoffs without new CA.

Inputs are explicit plan records, each with ``plan_id``, ``problem_sha256``,
``comparison_protocol``, ``cleared_area_m2``, ``construction_area_basis``,
``demolished_ids`` and a ``scenario_evaluations`` mapping. The construction
basis is ``geometric_action_union_m2``: the complete geometric action union,
never the sum of overlapping labels or construction plus fire damage.

Each evaluation supplies ``problem_sha256``, ``artifact_manifest_verified``,
``check`` (status/evidence), and ``fire_loss``. Terminal mode additionally
requires a native-audited ``terminal_summary``. This module verifies its
structure and fingerprints; the caller verifies artifact manifests and the
native state through terminal_verification before supplying these records.

``comparison_protocol`` binds inventory, scenarios, horizon, support, numerical
settings and source policy. Its mandatory keys are documented in
``PROTOCOL_KEYS``. A physical protocol hash binds any additional settings.
Unknown or incomplete plans remain in the output but cannot become feasible.
No finite archive is promoted to a globally complete Pareto frontier.
"""

from __future__ import annotations

from copy import deepcopy
from math import fsum, isclose, isfinite

from pyfireca.planning.boundary_scope import (
    BOUNDARY_CONTRACT_SHA256,
    VALIDATOR_POLICY_SHA256,
    finite_domain_summary_is_complete,
)
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.terminal_verification import terminal_summary_is_complete

PROTOCOL_KEYS = frozenset(
    {
        "inventory_sha256",
        "physical_protocol_sha256",
        "scenario_ids",
        "horizon_s",
        "roof_scales_m",
        "quadrature_spacing_m",
        "max_dt_s",
        "source_policy",
        "protected_core_ids",
    }
)
CONSTRUCTION_AREA_BASIS = "geometric_action_union_m2"
COUNT_BLOCK = "physical_remaining_roof_annotation_counts"


def _ids(values, name):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    return tuple(sorted(values))


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and nonnegative")
    value = float(value)
    if not isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _ground_cells(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("ground cell IDs require unique nonnegative integers")
    values = tuple(values)
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in values):
        raise ValueError("ground cell IDs require unique nonnegative integers")
    if len(set(values)) != len(values):
        raise ValueError("ground cell IDs require unique nonnegative integers")
    return tuple(sorted(values))


def _protocol(value):
    if not isinstance(value, dict) or not value.keys() >= PROTOCOL_KEYS:
        raise ValueError("complete comparison_protocol required")
    result = deepcopy(value)
    for key in ("inventory_sha256", "physical_protocol_sha256", "source_policy"):
        _text(result[key], key)
    result["scenario_ids"] = list(_ids(result["scenario_ids"], "scenario_ids"))
    if not result["scenario_ids"]:
        raise ValueError("a nonempty scenario set is required")
    result["protected_core_ids"] = list(_ids(result["protected_core_ids"], "protected_core_ids"))
    if not result["protected_core_ids"]:
        raise ValueError("a nonempty protected core set is required")
    for key in ("horizon_s", "max_dt_s", "quadrature_spacing_m"):
        result[key] = _number(result[key], key, positive=True)
    if isinstance(result["roof_scales_m"], (str, bytes)) or not result["roof_scales_m"]:
        raise ValueError("a nonempty common support set is required")
    result["roof_scales_m"] = sorted(
        _number(h, "roof_scales_m", positive=True) for h in result["roof_scales_m"]
    )
    if len(set(result["roof_scales_m"])) != len(result["roof_scales_m"]):
        raise ValueError("duplicate model supports")
    return result


def _bounds(value, name):
    if not isinstance(value, dict) or set(value) != {"lower", "upper"}:
        raise ValueError(f"{name} requires lower and upper attribution bounds")
    result = {k: _number(value[k], name) for k in ("lower", "upper")}
    if result["lower"] > result["upper"] or any(v != int(v) for v in result.values()):
        raise ValueError(f"{name} requires ordered integer count bounds")
    return {k: int(v) for k, v in result.items()}


def _loss(loss):
    """Read physical original-owner counts; never fall back to removed labels."""
    if not isinstance(loss, dict) or COUNT_BLOCK not in loss:
        return None, "missing_physical_original_owner_fire_counts"
    counts = loss[COUNT_BLOCK]
    if counts.get("schema_version") != "physical-original-owner-fire-counts-1":
        raise ValueError("physical original-owner count schema required")
    unrepresented = _ids(counts["unrepresented_original_ids"], "unrepresented_original_ids")
    burned_count = _bounds(counts["burned_out_annotation_attribution_count"], "burned-out N")
    affected_count = _bounds(counts["ignited_annotation_attribution_count"], "affected N")
    if any(burned_count[k] > affected_count[k] for k in ("lower", "upper")):
        raise ValueError("burned-out owner counts exceed affected owner counts")
    values = {}
    for output, field in (
        ("A_burned_out_m2", "cumulative_burned_out_covered_area_m2"),
        ("A_affected_m2", "cumulative_ignited_covered_area_m2"),
        ("A_active_m2", "active_burning_covered_area_m2"),
    ):
        partition = loss["physical"]
        total = _number(partition["all"][field], field)
        components = [_number(partition[k][field], field) for k in ("roof", "nonroof")]
        if not isclose(total, fsum(components), rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("physical total differs from disjoint roof plus nonroof")
        values[output] = total
    if (
        values["A_burned_out_m2"] > values["A_affected_m2"] + 1e-6
        or values["A_active_m2"] > values["A_affected_m2"] + 1e-6
    ):
        raise ValueError("burned-out or active area exceeds cumulative affected area")
    values.update(
        N_burned_out=burned_count,
        N_affected=affected_count,
        unrepresented_original_ids=list(unrepresented),
        fire_depleted_pyrolysable_fuel_kg=_number(
            loss["fuel"]["fire_depleted_kg"], "fire-depleted pyrolysable fuel"
        ),
        affected_equals_burned_out=isclose(
            values["A_burned_out_m2"], values["A_affected_m2"], rel_tol=1e-10, abs_tol=1e-6
        ),
    )
    return values, None


def _dominates(left, right):
    return all(a <= b for a, b in zip(left, right, strict=True)) and any(
        a < b for a, b in zip(left, right, strict=True)
    )


def _equivalence(problems, protocol, groups):
    if len(problems) == 1:
        return None
    for group in groups:
        if (
            group.get("schema_version") == "explicit-physical-protocol-equivalence-1"
            and set(group.get("problem_sha256s", ())) >= problems
            and _protocol(group["comparison_protocol"]) == protocol
            and group.get("physical_protocol_sha256") == protocol["physical_protocol_sha256"]
            and isinstance(group.get("evidence_reference"), str)
            and group["evidence_reference"]
        ):
            _text(group.get("equivalence_group_id"), "equivalence group ID")
            return deepcopy(group)
    raise ValueError("different frozen problems require an explicit physical equivalence group")


def summarize_evaluated_pareto(
    plan_evaluations,
    *,
    terminalclosed=True,
    temporal_mode=None,
    equivalence_groups=(),
    area_anchor_certificates=(),
):
    """Return conservative and interval-sensitive nondominated evaluated plans.

    Objectives are minimized componentwise: C geometric construction area,
    worst-scenario N burned-out original-owner attribution upper bound, and
    worst-scenario A whole-domain physical burned-out area. Affected counts,
    affected area, remaining active area and fuel consumption remain secondary
    observations. Demolition counts are never added to pure fire-loss N.

    The conservative archive uses N's upper bound. The robust archive keeps
    every point not dominated even when its N is best-case and a competitor's
    N is worst-case. Possible dominance uses the opposite interval endpoints;
    these two sensitivity reports prevent uncertain attribution from silently
    becoming an exact building identity. Equal objective vectors are retained.

    ``terminalclosed=False`` creates a separate phase-horizon archive; a real
    full-horizon check is required. Terminal mode requires verified extinction,
    not merely reaching a time cap. All inputs must have matching protocol and
    ``temporal_mode`` (``terminalclosed`` or ``phase_horizon``). Explicit
    ``temporal_mode='finite_domain_closed'`` instead reads separate conditional
    summaries while preserving each original native check, including unknown.
    """
    if not isinstance(terminalclosed, bool):
        raise ValueError("terminalclosed must be an explicit boolean")
    mode = "terminalclosed" if terminalclosed else "phase_horizon"
    if temporal_mode is not None:
        if temporal_mode not in {"terminalclosed", "phase_horizon", "finite_domain_closed"}:
            raise ValueError("unsupported explicit temporal mode")
        if temporal_mode == "finite_domain_closed" and not terminalclosed:
            raise ValueError("finite-domain closure cannot be a phase-horizon mode")
        if temporal_mode != "finite_domain_closed" and temporal_mode != mode:
            raise ValueError("temporal mode conflicts with terminalclosed")
        mode = temporal_mode
    closed_mode = mode != "phase_horizon"
    finite_domain = mode == "finite_domain_closed"
    plans = list(plan_evaluations)
    if not plans:
        raise ValueError("at least one declared plan and its protocol are required")
    protocol = _protocol(plans[0]["comparison_protocol"])
    scenarios = protocol["scenario_ids"]
    problems, names, rows, unrepresented_scope = set(), set(), [], None
    for plan in plans:
        name = _text(plan["plan_id"], "plan_id")
        if name in names:
            raise ValueError("plan IDs must be unique")
        names.add(name)
        if _protocol(plan["comparison_protocol"]) != protocol:
            raise ValueError("horizon, support, source rule or physical protocols cannot be mixed")
        if plan.get("temporal_mode") != mode:
            raise ValueError("phase-horizon and terminalclosed evaluations cannot be mixed")
        problem = _text(plan["problem_sha256"], "problem_sha256")
        problems.add(problem)
        ids = _ids(plan["demolished_ids"], "demolished_ids")
        ground = _ground_cells(plan.get("cleared_physical_cell_ids", ()))
        if plan.get("construction_area_basis") != CONSTRUCTION_AREA_BASIS:
            raise ValueError("construction C requires the complete geometric action union")
        area = _number(plan["cleared_area_m2"], "construction area")
        evaluations = plan["scenario_evaluations"]
        if not isinstance(evaluations, dict) or not set(evaluations) <= set(scenarios):
            raise ValueError("evaluations contain an undeclared scenario")
        reasons, observations, failed = [], {}, False
        for scenario in scenarios:
            if scenario not in evaluations:
                reasons.append({"scenario_id": scenario, "reason": "missing_scenario"})
                continue
            evaluation = evaluations[scenario]
            if evaluation.get("problem_sha256") != problem:
                raise ValueError("scenario evaluation belongs to a different frozen problem")
            if evaluation.get("artifact_manifest_verified") is not True:
                reasons.append({"scenario_id": scenario, "reason": "artifact_manifest_unverified"})
                continue
            check = evaluation.get("check", {})
            status, evidence = check.get("status"), check.get("evidence", {})
            if status not in {"passed", "failed", "unknown"}:
                raise ValueError("explicit passed/failed/unknown physical check required")
            if finite_domain:
                summary = evaluation.get("finite_domain_summary")
                if summary is None or not finite_domain_summary_is_complete(summary):
                    reasons.append(
                        {"scenario_id": scenario, "reason": "finite_domain_closure_unknown"}
                    )
                    continue
                if (
                    summary["analysis_problem_sha256"] != problem
                    or content_hash(check) != summary["lineage"]["native_check_sha256"]
                    or protocol.get("native_problem_sha256") != summary["native_problem_sha256"]
                    or protocol.get("finite_domain_sha256") != summary["finite_domain_sha256"]
                    or protocol.get("boundary_contract_sha256") != BOUNDARY_CONTRACT_SHA256
                    or protocol.get("validator_policy_sha256") != VALIDATOR_POLICY_SHA256
                    or protocol.get("boundary_scope_validator_sha256")
                    != summary["validator_sha256"]
                ):
                    raise ValueError("finite-domain protocol/native check/analysis problem differs")
                status = summary["conditional_status"]
            if status == "failed":
                failed = True
            if status != "passed":
                reasons.append({"scenario_id": scenario, "reason": f"physical_check_{status}"})
                continue
            if closed_mode:
                if not finite_domain:
                    summary = evaluation.get("terminal_summary")
                if summary is None or (
                    not finite_domain and not terminal_summary_is_complete(summary)
                ):
                    reasons.append({"scenario_id": scenario, "reason": "terminal_closure_unknown"})
                    continue
                if (
                    summary["scenario_id"] != scenario
                    or summary["plan_id"] != name
                    or _ids(summary["demolished_ids"], "terminal demolished IDs") != ids
                    or _ground_cells(summary.get("cleared_physical_cell_ids", ())) != ground
                    or (
                        not finite_domain
                        and (
                            (summary["model_protocol"].get("source_evidence") or {}).get(
                                "comparison_problem_sha256",
                                summary["model_protocol"].get("reference_problem_sha256"),
                            )
                            != problem
                        )
                    )
                    or summary["model_protocol"].get("terminal_horizon_cap_s")
                    != protocol["horizon_s"]
                    or summary["model_protocol"].get("max_dt_s") != protocol["max_dt_s"]
                ):
                    raise ValueError("terminal summary differs from its scenario, plan or problem")
                if not summary["protection_feasible"]:
                    failed = True
                    reasons.append({"scenario_id": scenario, "reason": "core_ignition_at_closure"})
                    continue
            else:
                actual = _number(evidence.get("simulated_until_s"), "actual observation time")
                cores = evidence.get("core_outcomes")
                if (
                    evidence.get("reached_horizon") is not True
                    or not isclose(actual, protocol["horizon_s"], rel_tol=0, abs_tol=1e-8)
                    or evidence.get("invalid_reasons") != []
                    or not isinstance(cores, list)
                    or not cores
                    or sorted(c.get("building_id", "") for c in cores)
                    != protocol["protected_core_ids"]
                    or any(c.get("ignited") is not False for c in cores)
                ):
                    reasons.append(
                        {"scenario_id": scenario, "reason": "incomplete_valid_core_horizon"}
                    )
                    continue
            values, rejection = _loss(evaluation.get("fire_loss"))
            if rejection:
                reasons.append({"scenario_id": scenario, "reason": rejection})
                continue
            scope = values["unrepresented_original_ids"]
            if unrepresented_scope is None:
                unrepresented_scope = scope
            elif scope != unrepresented_scope:
                raise ValueError("original unrepresented-owner domains cannot be mixed")
            if closed_mode and values["A_active_m2"] != 0:
                raise ValueError("terminal fire loss still contains active burning area")
            actual = summary["actual_stop_time_s"] if closed_mode else evidence["simulated_until_s"]
            if evaluation["fire_loss"].get("saved_final_time_s") != actual:
                raise ValueError("fire-loss observation differs from the verified saved time")
            if closed_mode:
                metrics = summary["metrics"]
                if (
                    metrics.get("N_burned_out_annotation_attribution_count")
                    != values["N_burned_out"]
                    or metrics.get("N_ignited_annotation_attribution_count") != values["N_affected"]
                    or not isclose(
                        _number(metrics.get("C_construction_action_area_m2"), "audited C"),
                        area,
                        rel_tol=1e-10,
                        abs_tol=1e-6,
                    )
                    or metrics.get("burned_out_physical_covered_area_m2")
                    != values["A_burned_out_m2"]
                    or metrics.get("A_ignited_physical_covered_area_m2") != values["A_affected_m2"]
                ):
                    raise ValueError("terminal audited metrics differ from supplied C/N/A losses")
            observations[scenario] = values
        feasible = not reasons and len(observations) == len(scenarios)
        row = {
            "plan_id": name,
            "problem_sha256": problem,
            "demolished_ids": list(ids),
            "cleared_physical_cell_ids": list(ground),
            "cleared_area_m2": area,
            "construction_area_basis": CONSTRUCTION_AREA_BASIS,
            "status": "passed" if feasible else "failed" if failed else "unknown",
            "unresolved_or_failure_reasons": reasons,
            "scenario_observations": observations,
            "objective_values": None,
            "worst_case": None,
            "provenance_references": deepcopy(plan.get("provenance_references", [])),
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
            row["worst_case"] = worst
            row["objective_values"] = [
                area,
                worst["N_burned_out"]["upper"],
                worst["A_burned_out_m2"],
            ]
        rows.append(row)
    equivalence = _equivalence(problems, protocol, equivalence_groups)
    feasible = [r for r in rows if r["status"] == "passed"]
    unresolved = [r for r in rows if r["status"] == "unknown"]
    for row in feasible:
        lo = [
            row["cleared_area_m2"],
            row["worst_case"]["N_burned_out"]["lower"],
            row["worst_case"]["A_burned_out_m2"],
        ]
        upper, robust, possible = [], [], []
        for other in feasible:
            if other is row:
                continue
            other_lo = [
                other["cleared_area_m2"],
                other["worst_case"]["N_burned_out"]["lower"],
                other["worst_case"]["A_burned_out_m2"],
            ]
            if _dominates(other["objective_values"], row["objective_values"]):
                upper.append(other["plan_id"])
            if _dominates(other["objective_values"], lo):
                robust.append(other["plan_id"])
            if _dominates(other_lo, row["objective_values"]):
                possible.append(other["plan_id"])
        row.update(
            upper_bound_dominated_by=sorted(upper),
            robustly_dominated_by=sorted(robust),
            possibly_dominated_by=sorted(possible),
            unresolved_potential_dominator_ids=sorted(
                r["plan_id"] for r in unresolved if r["cleared_area_m2"] <= row["cleared_area_m2"]
            ),
        )
    feasible.sort(key=lambda r: (r["objective_values"], r["plan_id"]))
    return {
        "schema_version": "heritage-evaluated-pareto-1",
        "temporal_mode": mode,
        "outcome_scope": "fixed_finite_native_domain_without_external_fire_or_heat_feedback"
        if finite_domain
        else "native_strict_domain_acceptance",
        "native_strict_statuses_preserved": True,
        "comparison_protocol": protocol,
        "problem_sha256s": sorted(problems),
        "explicit_equivalence_group": equivalence,
        "objective_names": [
            "C_geometric_construction_m2",
            "N_burned_out_original_owner_upper",
            "A_physical_burned_out_m2",
        ],
        "loss_aggregation": "componentwise_maximum_over_every_declared_scenario",
        "audited_C_numeric_binding_tolerance": {"absolute_m2": 1e-6, "relative": 1e-10},
        "objective_dominance_tolerance": 0,
        "N_scope": (
            "physical_original_owner_attribution_bounds;"
            "unrepresented_owners_unknown_in_upper;not_real_structural_destruction"
        ),
        "unrepresented_original_ids": unrepresented_scope,
        "all_original_inventory_owners_physically_represented": unrepresented_scope == [],
        "non_dominated_archive": [r for r in feasible if not r["upper_bound_dominated_by"]],
        "robust_non_dominated_archive": [r for r in feasible if not r["robustly_dominated_by"]],
        "possible_dominance_free_archive": [r for r in feasible if not r["possibly_dominated_by"]],
        "fully_evaluated_feasible_plans": feasible,
        "unresolved_plans": unresolved,
        "failed_plans": [r for r in rows if r["status"] == "failed"],
        "declared_plan_count": len(rows),
        "complete_feasible_plan_count": len(feasible),
        "area_anchor_certificate_references": deepcopy(list(area_anchor_certificates)),
        "area_anchor_certificates_reverified_by_this_module": False,
        "area_anchor_certificate_interpretation": (
            "references_only;no_optimality_transferred_across_temporal_or_source_protocol"
        ),
        "frontier_scope": "only_fully_evaluated_feasible_declared_plans",
        "global_pareto_frontier_complete": False,
        "global_minimum_area_proven_by_this_module": False,
        "new_CA_checks": 0,
    }
