"""A binary area master over all eligible demolition actions.

This is a proposal and lower-bound problem, not a fire simulator. Shared
physical patches are charged only if every owner is chosen. Valid cold-removal
failure witnesses and already assessed exact plans can be excluded. An optimal
master proposal has unknown fire-protection status until the physical oracle
evaluates it. SciPy is optional and imported only when solving is requested.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from math import isfinite

import numpy as np

from pyfireca.planning.area_candidates import ClearedAreaFunction
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.failure_cuts import PHYSICAL_GROUP_CUT_CAPABILITIES
from pyfireca.planning.heritage import content_hash


def _ids(values, name):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    return tuple(sorted(values))


def _nonnegative(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and nonnegative")
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _validated_cache(cache, problem, candidates, scenarios, area, fixed_retained):
    """Bind each exclusion to its physical problem, plan and scenario record."""
    grouped = defaultdict(dict)
    cuts = {}
    eligible, scenario_set = set(candidates), set(scenarios)
    known_owners = eligible | set(fixed_retained)
    for key, check in ({} if cache is None else cache).items():
        if not isinstance(key, tuple) or len(key) != 3:
            raise ValueError("cache keys must bind (problem, plan, scenario)")
        fingerprint, raw_ids, scenario = key
        if fingerprint != problem:
            continue
        ids = _ids(raw_ids, "cached plan")
        if tuple(raw_ids) != ids or not set(ids) <= eligible:
            raise ValueError("cached plans must be canonical eligible action sets")
        if scenario not in scenario_set:
            raise ValueError("cached check is outside the declared scenario problem")
        if not isinstance(check, ProtectionCheck):
            raise TypeError("cached oracle results must be ProtectionCheck")
        declared_problem = check.evidence.get("problem_sha256", problem)
        if declared_problem != problem:
            raise ValueError("oracle evidence conflicts with its cache problem")
        grouped[ids][scenario] = check
        value = check.evidence.get("failure_certificate")
        if value is None:
            continue
        raw = {name: item for name, item in value.items() if name != "certificate_sha256"}
        if (
            check.status != "failed"
            or value.get("schema_version")
            not in {"heritage-cold-removal-cut-1", "heritage-physical-node-removal-cut-1"}
            or value.get("problem_sha256") != problem
            or value.get("scenario_id") != scenario
            or value.get("required_demolished_ids") != list(ids)
            or content_hash(raw) != value.get("certificate_sha256")
        ):
            raise ValueError("failure certificate does not bind its failed physical check")
        removed = _ids(value["required_demolished_ids"], "certificate removed IDs")
        retained = _ids(value.get("required_retained_ids", ()), "certificate retained IDs")
        if set(removed) & set(retained) or not (set(removed) | set(retained)) <= known_owners:
            raise ValueError("failure certificate has invalid action/owner identities")
        source = value.get("source_id")
        if (
            source not in known_owners
            or source in removed
            or (source in eligible and source not in retained)
            or check.evidence.get("source_id", source) != source
        ):
            raise ValueError("failure certificate must preserve and bind its actual source")
        if value["schema_version"] == "heritage-physical-node-removal-cut-1":
            capabilities = value.get("physical_cut_capabilities")
            raw_groups = value.get("required_clearance_owner_groups", ())
            groups = [tuple(_ids(group, "physical cut owner group")) for group in raw_groups]
            if (
                capabilities != PHYSICAL_GROUP_CUT_CAPABILITIES
                or value.get("physical_cut_capabilities_sha256")
                != content_hash(PHYSICAL_GROUP_CUT_CAPABILITIES)
                or value.get("physical_group_sha256") != content_hash(raw_groups)
                or value.get("physical_core_failure_observed") is not True
                or list(raw_groups) != [list(group) for group in groups]
                or len(set(groups)) != len(groups)
                or any(not group or not set(group) <= known_owners for group in groups)
                or any(set(group) & set(removed) for group in groups)
                or (source in eligible and (source,) not in groups)
            ):
                raise ValueError("physical group certificate has an invalid frozen capability seal")
            witnesses = value.get("physical_group_witnesses", ())
            if len(witnesses) != len(groups) or any(
                witness.get("remaining_owner_ids") != list(group)
                or isinstance(witness.get("positive_burnable_node_count"), bool)
                or not isinstance(witness.get("positive_burnable_node_count"), int)
                or witness["positive_burnable_node_count"] < 1
                or not isfinite(witness.get("covered_area_m2", float("nan")))
                or witness["covered_area_m2"] <= 0
                or not witness.get("physical_node_indices_sha256")
                for group, witness in zip(groups, witnesses, strict=True)
            ):
                raise ValueError(
                    "physical group certificate lacks positive physical node witnesses"
                )
        cuts[value["certificate_sha256"]] = value

    exclusions, passed = [], []
    for ids, checks in sorted(grouped.items()):
        failed = sorted(s for s, check in checks.items() if check.status == "failed")
        all_passed = set(checks) == scenario_set and all(
            check.status == "passed" for check in checks.values()
        )
        if not failed and not all_passed:
            continue
        status = "failed" if failed else "passed"
        evidence = {
            "demolished_ids": list(ids),
            "status": status,
            "reason": "valid_failure_witness" if failed else "all_declared_scenarios_passed",
            "checked_scenario_ids": sorted(checks),
            "failed_scenario_ids": failed,
            "checks_sha256": content_hash(
                {scenario: asdict(check) for scenario, check in sorted(checks.items())}
            ),
            "cleared_area_m2": area(ids),
        }
        exclusions.append(evidence)
        if all_passed:
            passed.append(evidence)
    passed.sort(key=lambda row: (row["cleared_area_m2"], row["demolished_ids"]))
    return list(cuts.values()), exclusions, passed


def solve_area_master(
    candidate_ids,
    cleared_area,
    scenario_ids,
    *,
    problem_sha256,
    cache=None,
    fixed_retained_ids=(),
    time_limit_s=None,
    mip_relative_gap=0.0,
    maximum_cleared_area_m2=None,
):
    """Find an unexcluded area-minimal proposal with a MIP dual lower bound.

    ``cleared_area`` must be the disjoint physical-patch ``ClearedAreaFunction``;
    arbitrary nonadditive functions cannot be represented by this AND model.
    A patch with any owner outside ``candidate_ids`` cannot be cleared. The
    full candidate list, geometry, protection rules, source rules and scenarios
    must belong to the caller's frozen ``problem_sha256``.

    ``cache`` uses ``(problem_sha256, canonical_ids, scenario_id)`` keys and
    ``ProtectionCheck`` values. Any valid failed check excludes its exact plan.
    A passed plan is excluded only after *every* declared scenario has passed.
    Unknown/incomplete plans are never excluded. A failed check carrying a
    bound and hashed cold-removal certificate additionally excludes its entire
    proven failure family: D superset A and D disjoint H. Outside retained
    identities must be declared in ``fixed_retained_ids``; they supply no
    flippable variable in the cut inequality.

    The dual bound applies to the *remaining* master after these exclusions;
    known passed plans are retained separately. A full physical optimum would
    require a separately verified feasible upper bound to meet this remaining
    lower bound (or exhaustion), with every exclusion valid. Floating-point
    HiGHS bounds and gaps are reported as such, not exact rational proofs.
    An optional area cap restricts the entire remaining master domain. Cached
    observations outside that domain are rejected, and infeasibility then
    applies only within the declared cap.
    """
    candidates = _ids(candidate_ids, "candidate_ids")
    if not isinstance(cleared_area, ClearedAreaFunction):
        raise TypeError("master requires an explicit disjoint physical-patch area function")
    fixed_retained = _ids(fixed_retained_ids, "fixed_retained_ids")
    if set(fixed_retained) & set(candidates) or not set(fixed_retained) <= cleared_area.owner_ids:
        raise ValueError("fixed retained IDs must be physical owners outside the action universe")
    scenarios = _ids(scenario_ids, "scenario_ids")
    if not scenarios:
        raise ValueError("nonempty declared scenario IDs required")
    if not isinstance(problem_sha256, str) or not problem_sha256:
        raise ValueError("frozen problem fingerprint required")
    gap = _nonnegative(mip_relative_gap, "MIP relative gap")
    cap = (
        None
        if maximum_cleared_area_m2 is None
        else _nonnegative(maximum_cleared_area_m2, "maximum cleared area")
    )
    if time_limit_s is not None:
        time_limit_s = _nonnegative(time_limit_s, "time limit")
    cuts, exclusions, known_passed = _validated_cache(
        cache, problem_sha256, candidates, scenarios, cleared_area, fixed_retained
    )
    if cap is not None:
        for (problem, ids, _), _check in ({} if cache is None else cache).items():
            if problem == problem_sha256 and cleared_area(ids) > cap:
                raise ValueError("cached physical plan is outside the declared area cap")
    index = {identifier: position for position, identifier in enumerate(candidates)}
    objective = [0.0] * len(candidates)
    shared = []
    ignored_groups = 0
    for owners, area in cleared_area.owner_groups:
        if not owners <= index.keys():
            ignored_groups += 1
            continue
        if len(owners) == 1:
            objective[index[next(iter(owners))]] += area
        else:
            shared.append((tuple(sorted(owners)), area))
            objective.append(area)
    and_groups = {
        owners: offset for offset, (owners, _) in enumerate(shared, start=len(candidates))
    }
    extra_group_variables = 0
    for cut in cuts:
        if cut["schema_version"] != "heritage-physical-node-removal-cut-1":
            continue
        for group in cut["required_clearance_owner_groups"]:
            group = tuple(group)
            if len(group) > 1 and set(group) <= index.keys() and group not in and_groups:
                and_groups[group] = len(objective)
                objective.append(0.0)
                extra_group_variables += 1
    variable_count = len(objective)
    row_ids, column_ids, coefficients, lower, upper = [], [], [], [], []

    def add_row(values, lo, hi):
        row = len(lower)
        for column, coefficient in values.items():
            if coefficient:
                row_ids.append(row)
                column_ids.append(column)
                coefficients.append(coefficient)
        lower.append(lo)
        upper.append(hi)

    # y == AND(x_i for each patch owner): y <= every x_i, and
    # sum(x_i) - y <= k - 1. No area sum of individual buildings is substituted.
    for owners, offset in and_groups.items():
        for owner in owners:
            add_row({offset: 1.0, index[owner]: -1.0}, -np.inf, 0.0)
        add_row(
            {**dict.fromkeys((index[i] for i in owners), 1.0), offset: -1.0},
            -np.inf,
            len(owners) - 1,
        )
    for cut in cuts:
        removed = cut["required_demolished_ids"]
        values = {index[i]: -1.0 for i in removed}
        if cut["schema_version"] == "heritage-physical-node-removal-cut-1":
            for raw_group in cut["required_clearance_owner_groups"]:
                group = tuple(raw_group)
                if not set(group) <= index.keys():
                    continue  # A fixed surviving owner makes this literal false.
                variable = index[group[0]] if len(group) == 1 else and_groups[group]
                values[variable] = values.get(variable, 0.0) + 1.0
        else:
            retained = [i for i in cut["required_retained_ids"] if i in index]
            values.update({index[i]: 1.0 for i in retained})
        add_row(values, 1 - len(removed), np.inf)
    for excluded in exclusions:
        ids = set(excluded["demolished_ids"])
        add_row({index[i]: -1.0 if i in ids else 1.0 for i in candidates}, 1 - len(ids), np.inf)
    if cap is not None:
        add_row(dict(enumerate(objective)), -np.inf, cap)

    formulation = {
        "schema_version": "heritage-area-mip-formulation-1",
        "problem_sha256": problem_sha256,
        "candidate_ids": candidates,
        "scenario_ids": scenarios,
        "fixed_retained_ids": fixed_retained,
        "area_basis": cleared_area.area_basis,
        "physical_owner_groups": [
            {"owners": sorted(owners), "area_m2": area}
            for owners, area in cleared_area.owner_groups
        ],
        "failure_certificates": cuts,
        "exact_plan_exclusions": exclusions,
    }
    if cap is not None:
        formulation["maximum_cleared_area_m2"] = cap
    result = {
        "schema_version": "heritage-area-master-1",
        "problem_sha256": problem_sha256,
        "master_formulation_sha256": content_hash(formulation),
        "candidate_ids": list(candidates),
        "candidate_count": len(candidates),
        "scenario_ids": list(scenarios),
        "fixed_retained_ids": list(fixed_retained),
        "area_basis": cleared_area.area_basis,
        "binary_variable_count": variable_count,
        "shared_patch_variable_count": len(shared),
        "physical_cut_auxiliary_variable_count": extra_group_variables,
        "unclearable_fixed_owner_group_count": ignored_groups,
        "constraint_count": len(lower),
        "failure_cut_count": len(cuts),
        "physical_group_failure_cut_count": sum(
            cut["schema_version"] == "heritage-physical-node-removal-cut-1" for cut in cuts
        ),
        "failure_certificates": cuts,
        "exact_plan_exclusion_count": len(exclusions),
        "exact_plan_exclusions": exclusions,
        "known_passed_plan_count": len(known_passed),
        "known_passed_best_ids": None if not known_passed else known_passed[0]["demolished_ids"],
        "known_passed_best_area_m2": None
        if not known_passed
        else known_passed[0]["cleared_area_m2"],
        "next_ids": None,
        "next_area_m2": None,
        "next_physical_protection_status": "unknown",
        "physical_optimality_proven": False,
        "proposal_tie_break_policy": "solver_arbitrary;no_epsilon_count_penalty",
        "necessary_demolition_identities_proven": False,
        "maximum_cleared_area_m2": cap,
        "infeasibility_scope": "declared_area_cap" if cap is not None else "full_action_universe",
        "master_bound_scope": (
            "remaining_unexcluded_plans_within_area_cap;known_passed_plans_separate"
            if cap is not None
            else "remaining_unexcluded_plans;known_passed_plans_separate"
        ),
        "bound_type": "floating_point_HiGHS_MIP_dual_bound;not_exact_arithmetic_certificate",
        "requested_mip_relative_gap": gap,
    }
    if not variable_count:
        infeasible = any(lo > 0 or hi < 0 for lo, hi in zip(lower, upper, strict=True))
        result.update(
            solver="not_needed_empty_action_universe",
            status="infeasible" if infeasible else "optimal_master",
            solver_status=2 if infeasible else 0,
            solver_message="exact empty action universe",
            remaining_scope_empty=infeasible,
            remaining_master_lower_bound_m2=None if infeasible else 0.0,
            mip_dual_bound_m2=None if infeasible else 0.0,
            mip_gap=None if infeasible else 0.0,
            mip_node_count=0,
        )
        if not infeasible:
            result.update(next_ids=[], next_area_m2=0.0)
        return result

    try:
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import csc_matrix
    except ImportError as error:
        raise ImportError(
            "solve_area_master requires optional SciPy with scipy.optimize.milp"
        ) from error

    options = {"mip_rel_gap": gap}
    if time_limit_s is not None:
        options["time_limit"] = time_limit_s
    matrix = csc_matrix((coefficients, (row_ids, column_ids)), shape=(len(lower), variable_count))
    answer = milp(
        np.asarray(objective, dtype=float),
        integrality=np.ones(variable_count, dtype=np.int32),
        bounds=Bounds(np.zeros(variable_count), np.ones(variable_count)),
        constraints=LinearConstraint(matrix, np.asarray(lower), np.asarray(upper)),
        options=options,
    )
    status = {0: "optimal_master", 1: "limit_reached", 2: "infeasible", 3: "unbounded"}.get(
        int(answer.status), "solver_error"
    )

    def finite_or_none(value):
        return float(value) if value is not None and isfinite(value) else None

    dual = finite_or_none(getattr(answer, "mip_dual_bound", None))
    result.update(
        solver="scipy.optimize.milp/HiGHS",
        status=status,
        solver_status=int(answer.status),
        solver_message=str(answer.message),
        remaining_scope_empty=int(answer.status) == 2,
        mip_dual_bound_m2=dual,
        remaining_master_lower_bound_m2=None
        if int(answer.status) == 2
        else max(0.0, dual if dual is not None else 0.0),
        mip_gap=finite_or_none(getattr(answer, "mip_gap", None)),
        mip_node_count=int(getattr(answer, "mip_node_count", 0) or 0),
        solver_objective_m2=finite_or_none(getattr(answer, "fun", None)),
    )
    if answer.x is not None:
        rounded = np.rint(answer.x)
        if np.max(abs(answer.x - rounded)) > 1e-5:
            raise ValueError("MILP returned a nonintegral proposal")
        # Recheck all logical rows on rounded binary values, independently of
        # the solver's feasibility tolerances, before returning a proposal.
        values = matrix @ rounded
        # Logical rows contain integer coefficients. The optional final area
        # row instead uses floating geometry, and is checked below by the same
        # fsum set function as the physical cap guard (not sparse summation).
        logical_end = len(lower) - (cap is not None)
        if np.any(values[:logical_end] < np.asarray(lower[:logical_end])) or np.any(
            values[:logical_end] > np.asarray(upper[:logical_end])
        ):
            raise ValueError("rounded MILP proposal violates logical constraints")
        ids = tuple(i for i in candidates if rounded[index[i]] == 1)
        actual = cleared_area(ids)
        if cap is not None and actual > cap:
            raise ValueError("MILP rounded proposal is outside the declared physical area cap")
        linear = float(np.dot(np.asarray(objective), rounded))
        if abs(actual - linear) > 1e-8 * max(1.0, actual):
            raise ValueError("MILP objective disagrees with physical shared-patch area")
        result.update(next_ids=list(ids), next_area_m2=actual)
    return result
