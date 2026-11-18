"""Frozen whole-candidate minimum-area search with a physical CA oracle.

The MIP supplies proposals and lower bounds; only complete CA checks under the
frozen source rule establish feasible upper bounds. Unknown results stop the search. Area
optimality within explicit numerical tolerances and the unresolved multi-loss
Pareto problem are reported separately. No field-safety claim is made.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import asdict, replace
from math import fsum, isfinite
from pathlib import Path

import numpy as np
from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_fixed_comparison import (
    atomic_json,
    load_fires,
    result_curves,
    write_compact_arrays,
)

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.area_master import solve_area_master
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.fire_loss import fire_loss_from_result
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-area-master-config-1"
REQUIRED = frozenset(
    {
        "check.json",
        "evaluation.json",
        "curves.json",
        "fire-loss.json",
        "final-state.npz",
        "layout.npz",
        "loss-basis.npz",
    }
)
LOSS_METRICS = (
    "ignited_roof_area_m2",
    "ignited_nonroof_area_m2",
    "consumed_fuel_kg",
    "roof_fire_depleted_kg",
    "nonroof_fire_depleted_kg",
    "active_roof_area_m2",
    "active_nonroof_area_m2",
    "clearing_plus_ignited_roof_area_m2",
    "clearing_plus_ignited_annotation_count_lower",
    "clearing_plus_ignited_annotation_count_upper",
)
RETAINED_SOURCE_POLICY = "area_bounded_retained_sources"


def source_policy(config):
    return config.get("source_policy", "fixed")


def source_area_cap(config):
    if source_policy(config) != RETAINED_SOURCE_POLICY:
        return None
    return fsum((config["source_area_limit_m2"], config.get("source_area_tolerance_m2", 0.0)))


def canonical_ids(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("IDs must be a collection")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError("unique nonempty IDs required")
    return tuple(sorted(values))


def seeds(config):
    rows = [
        canonical_ids(p["demolished_ids"] if isinstance(p, dict) else p)
        for p in config.get("seed_plans", [])
    ]
    if len(rows) != len(set(rows)):
        raise ValueError("duplicate seed plans")
    return rows


def validate_config(config):
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported area master schema")
    if source_policy(config) not in {"fixed", RETAINED_SOURCE_POLICY}:
        raise ValueError("unsupported explicit source policy")
    for key in ("prefetch_baseline_witnesses", "enable_physical_group_cuts"):
        if not isinstance(config.get(key, False), bool):
            raise ValueError(f"{key} must be an explicit boolean")
    if source_policy(config) == RETAINED_SOURCE_POLICY:
        for key in ("source_area_limit_m2", "source_area_tolerance_m2"):
            value = config.get(key, 0.0) if key.endswith("tolerance_m2") else config[key]
            if isinstance(value, bool) or not isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        try:
            if not isfinite(source_area_cap(config)):
                raise ValueError("effective retained-source cap must be finite")
        except OverflowError as error:
            raise ValueError("effective retained-source cap overflows") from error
    elif (
        config.get("source_area_limit_m2") is not None
        or config.get("source_area_tolerance_m2", 0) != 0
    ):
        raise ValueError("a source area cap requires area_bounded_retained_sources policy")
    if config.get("roof_scales_m") != [1.0, 0.5] or config.get("quadrature_spacing_m") != 0.5:
        raise ValueError("master requires the explicit frozen dual-support and quadrature rules")
    if config.get("horizon_s") != 3600:
        raise ValueError("master requires the declared 3600 s protection horizon")
    checkpoints = config.get("checkpoints_s", [1200.0, 2400.0, 3600.0])
    if (
        not checkpoints
        or any(isinstance(t, bool) or not isfinite(t) or t <= 0 for t in checkpoints)
        or checkpoints != sorted(set(checkpoints))
        or checkpoints[-1] != config["horizon_s"]
    ):
        raise ValueError("checkpoints must increase and end at the full horizon")
    for key in (
        "max_dt_s",
        "max_wall_time_s",
        "max_mip_time_s",
        "mip_call_time_limit_s",
        "minimum_free_disk_GB",
    ):
        value = config[key]
        if isinstance(value, bool) or not isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be finite and positive")
    for key in ("max_new_checks", "max_nodes", "max_radiation_edges"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("area_gap_absolute_tolerance_m2", "area_gap_relative_tolerance"):
        value = config.get(key, 0.0)
        if isinstance(value, bool) or not isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and nonnegative")
    if not config.get("source_fires") or "refinement_ids" not in config:
        raise ValueError("explicit same-source fires and a fixed refinement set required")
    canonical_ids(config["refinement_ids"])
    canonical_ids(config.get("fixed_source_ids", []))
    fire_ids = [f["fire_id"] for f in config["source_fires"]]
    if len(set(fire_ids)) != len(fire_ids) or any(
        not i or Path(i).name != i or i in {".", ".."} for i in fire_ids
    ):
        raise ValueError("unique safe fire IDs required")
    seeds(config)


def source_paths():
    return [
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *(
            ROOT / "scripts" / name
            for name in (
                "run_heritage_area_master.py",
                "run_heritage_fixed_comparison.py",
                "run_heritage_roof_study.py",
                "preflight_heritage_exact_expansion.py",
            )
        ),
    ]


def require_mip_solver():
    try:
        from scipy.optimize import milp
    except ImportError as error:
        raise ImportError("area master requires SciPy scipy.optimize.milp before any CA") from error
    return milp


def load_retained_fires(config_path, config, inventory):
    """Load existing complete global priorities verbatim; generate no fallbacks."""
    fires, references = [], {}
    for item in config["source_fires"]:
        path = (config_path.parent / item["protocol_path"]).resolve()
        protocol = read_json(path)
        if protocol.get("protocol_sha256") != content_hash(
            {k: v for k, v in protocol.items() if k != "protocol_sha256"}
        ):
            raise ValueError("retained-source protocol hash mismatch")
        if protocol.get("inventory_sha256") != inventory.sha256:
            raise ValueError("retained-source protocol inventory mismatch")
        draws = [d for d in protocol["draws"] if d["draw_id"] == item["draw_id"]]
        if len(draws) != 1:
            raise ValueError("one frozen draw required per retained-source family")
        draw = draws[0]
        priority = draw.get("priority_ids", [])
        canonical_ids(priority)
        if (
            not priority
            or draw.get("baseline_source_id") != priority[0]
            or draw.get("stratum") != "all_retained_sources"
            or set(priority) != set(protocol.get("strata", {}).get("all_retained_sources", []))
        ):
            raise ValueError(
                "retained-source family requires the complete all_retained_sources priority"
            )
        selected = {**protocol, "draws": draws}
        selected.pop("protocol_sha256")
        selected["protocol_sha256"] = content_hash(selected)
        fires.append({"fire_id": item["fire_id"], "protocol": selected})
        references[str(path)] = file_sha256(path)
    return fires, references


def make_oracle(config_path, config, code_hash):
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study_path)
    fixed = set(canonical_ids(config.get("fixed_source_ids", []))) | set(inventory.fixed_source_ids)
    if not fixed <= inventory.by_id.keys():
        raise ValueError("fixed source must belong to the inventory")
    retained = source_policy(config) == RETAINED_SOURCE_POLICY
    if retained:
        if fixed & set(inventory.candidate_ids):
            raise ValueError("retained-source mode must preserve every inventory candidate action")
        candidates = tuple(sorted(inventory.candidate_ids))
        fires, references = load_retained_fires(config_path, config, inventory)
    else:
        blocked = fixed | set(inventory.protected_ids) | {t.building_id for t in inventory.targets}
        candidates = tuple(
            sorted(
                i
                for i in inventory.candidate_ids
                if i not in blocked and inventory.by_id[i].source_label == "building"
            )
        )
        fires, references = load_fires(config_path, config, candidates)
    if (
        config.get("candidate_ids") is not None
        and canonical_ids(config["candidate_ids"]) != candidates
    ):
        raise ValueError("master must include every eligible ordinary non-source action")
    if any(not set(plan) <= set(candidates) for plan in seeds(config)):
        raise ValueError("seed plans contain ineligible, protected or fixed-source buildings")
    actual_sources = {f["protocol"]["draws"][0]["baseline_source_id"] for f in fires}
    if not retained and not actual_sources <= fixed:
        raise ValueError("every frozen fire source must be explicitly fixed retained")
    request = replace(
        bundle.request,
        scenario=replace(bundle.request.scenario, max_dt_s=float(config["max_dt_s"])),
    )
    bundle = replace(bundle, request=request)
    source_options = {}
    if retained:
        source_options.update(
            source_area_limit_m2=config["source_area_limit_m2"],
            source_area_tolerance_m2=config.get("source_area_tolerance_m2", 0.0),
        )
    if config.get("enable_physical_group_cuts", False):
        source_options["enable_physical_group_cuts"] = True
    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=candidates,
        refinement_ids=canonical_ids(config["refinement_ids"]),
        maximum_demolitions=len(candidates),
        fires=fires,
        code_version=code_hash,
        horizon_s=config["horizon_s"],
        scales_m=tuple(config["roof_scales_m"]),
        quadrature_spacing_m=config["quadrature_spacing_m"],
        max_nodes=config["max_nodes"],
        max_radiation_edges=config["max_radiation_edges"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
        stop_at_first_core_ignition=True,
        freeze_integration_membership=True,
        **source_options,
    )
    if not retained and any(len(f["footprints"]) != 1 for f in oracle.fires.values()):
        raise ValueError("fixed-source master cannot replace removed ignition buildings")
    if retained:
        for coverage in oracle.source_area_coverages.values():
            for ids in seeds(config):
                coverage.validate_plan(ids, cleared_area=oracle.source_area_function)
    oracle.enable_failure_certificates = True
    area = build_cleared_area_function(
        oracle.base.owners,
        [g.area for g in oracle.base.geometry],
        area_basis="disjoint_physical_roof_geometry;clear_only_if_all_owners_removed",
    )
    study = read_json(study_path)
    inputs = [study_path]
    for key in ("labelme_annotations", "image_reference", "structures_path"):
        if study.get(key):
            inputs.append((study_path.parent / study[key]).resolve())
    case_dir = (study_path.parent / study["case_dir"]).resolve()
    inputs.extend(p for p in sorted(case_dir.rglob("*")) if p.is_file())
    references.update({str(p): file_sha256(p) for p in inputs})
    return oracle, area, references, sorted(fixed)


def prepare(config_path, output):
    config = read_json(config_path)
    validate_config(config)
    require_mip_solver()
    output.mkdir(parents=True, exist_ok=False)
    paths = source_paths()
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in paths}
    config_hash = file_sha256(config_path)
    # Capture producers before geometry preflight; no CA is run during preparation.
    for path in paths:
        target = output / "source-snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    oracle, area, references, fixed = make_oracle(config_path, config, content_hash(hashes))
    rows = []
    for label, fire in oracle.fires.items():
        for scale in oracle.scales_m:
            layout = refine_roof_layout(
                oracle.base,
                oracle.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=config["max_nodes"],
            )
            bounds = edge_bounds(layout, oracle.transport, fire["refined_ids"])
            row = {"fire_id": label, "scale_m": scale, "nodes": len(layout.owners), **bounds}
            row["passed"] = (
                row["nodes"] <= config["max_nodes"]
                and max(bounds.values()) <= config["max_radiation_edges"]
            )
            rows.append(row)
            print({"preflight": row}, flush=True)
            if not row["passed"]:
                write_json(output / "preflight.json", {"rows": rows, "all_passed": False})
                raise ValueError("capacity failed before any CA")
    if (
        file_sha256(config_path) != config_hash
        or any(file_sha256(path) != hashes[str(path.relative_to(ROOT))] for path in paths)
        or any(file_sha256(path) != digest for path, digest in references.items())
    ):
        raise ValueError("inputs or producer changed while freezing the study")
    write_json(
        output / "experiment.json",
        {
            "schema_version": SCHEMA,
            "config": config,
            "config_sha256": config_hash,
            "source_sha256": hashes,
            "references_sha256": references,
            "problem": oracle.problem,
            "problem_sha256": oracle.problem_sha256,
            "fixed_source_ids": fixed,
            "source_policy": source_policy(config),
            "source_area_limit_m2": config.get("source_area_limit_m2"),
            "admissible_source_area_cap_m2": source_area_cap(config),
            "initial_source_area_cap_is_verified_feasible_upper_bound": False,
            "candidate_ids": list(oracle.candidate_ids),
            "area_basis": area.area_basis,
            "scope": (
                "frozen_source_family_3600s_dual_support_model;"
                "no_field_safety_or_unseen_fire_guarantee"
            ),
            "global_pareto_frontier_complete": False,
            "wall_budget_policy": (
                "cumulative_execution;checked_between_solves;started_solve_may_finish"
            ),
        },
    )
    write_json(output / "inventory.json", oracle.inventory.to_mapping())
    (output / "input-snapshot").mkdir()
    write_json(output / "input-snapshot/config.json", config)
    write_json(
        output / "input-snapshot/study.json", read_json(config_path.parent / config["study_path"])
    )
    for label, fire in oracle.fires.items():
        write_json(output / f"input-snapshot/{label}.json", fire["protocol"])
    write_json(output / "preflight.json", {"rows": rows, "all_passed": True})
    atomic_json(
        output / "execution-budget.json",
        {
            "elapsed_s": 0.0,
            "mip_elapsed_s": 0.0,
            "started_checks": 0,
            "active": None,
        },
    )


def checked_status(check, horizon_s, *, allow_source_replacement=False):
    """A passing status without complete valid observation is unknown, never feasible."""
    if not isinstance(check, ProtectionCheck):
        raise TypeError("physical oracle must return ProtectionCheck")
    evidence = check.evidence
    invalid = bool(evidence.get("invalid_reasons")) or (
        evidence.get("source_replaced", False) and not allow_source_replacement
    )
    incomplete = check.status == "passed" and (
        evidence.get("reached_horizon") is not True
        or evidence.get("simulated_until_s", -1) < horizon_s - 1e-8
    )
    if invalid or incomplete:
        return ProtectionCheck(
            "unknown", {**evidence, "reason": "invalid_or_incomplete_physical_observation"}
        )
    return check


def read_complete(folder, problem, *, ids=None, scenario=None):
    manifest = read_json(folder / "complete.json")
    if (
        manifest.get("problem_sha256") != problem
        or (ids is not None and manifest.get("demolished_ids") != list(ids))
        or (scenario is not None and manifest.get("scenario_id") != scenario)
    ):
        raise ValueError("cached physical problem/plan/scenario differs")
    if not manifest.get("files_sha256", {}).keys() >= REQUIRED:
        raise ValueError("cache lacks required artifact hashes")
    for name, digest in manifest["files_sha256"].items():
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("unsafe cached artifact path")
        if not (folder / name).is_file() or file_sha256(folder / name) != digest:
            raise ValueError("cached artifact changed or is missing")
    row = read_json(folder / "check.json")
    if any(
        row.get(k) != manifest.get(k) for k in ("problem_sha256", "demolished_ids", "scenario_id")
    ):
        raise ValueError("cached check/manifest identity differs")
    return ProtectionCheck(row["status"], row["evidence"])


class ExecutionBudget:
    """Cumulative started-CA, MIP and execution clocks, durable across resumes."""

    def __init__(self, path, clock=time.monotonic):
        self.path, self.clock = Path(path), clock
        self.value = read_json(self.path)
        if self.value.get("active") is not None:
            raise ValueError("unclosed previous execution; inspect before resuming")
        for key in ("elapsed_s", "mip_elapsed_s", "started_checks"):
            value = self.value[key]
            if isinstance(value, bool) or not isfinite(value) or value < 0:
                raise ValueError("invalid cumulative execution budget")
        if not isinstance(self.value["started_checks"], int):
            raise ValueError("started check count must be integer")
        self.previous, self.started = self.value["elapsed_s"], clock()
        self.value["active"] = {"pid": os.getpid()}
        self.save()

    def save(self):
        self.value["elapsed_s"] = self.previous + self.clock() - self.started
        atomic_json(self.path, self.value)

    def close(self):
        self.value["active"] = None
        self.save()


def feasible_archive(cache, problem, scenarios, area):
    plans = sorted({ids for p, ids, _ in cache if p == problem})
    rows = []
    for ids in plans:
        checks = [cache.get((problem, ids, s)) for s in scenarios]
        if not all(c is not None and c.status == "passed" for c in checks):
            continue
        missing, losses = [], {}
        for name in LOSS_METRICS:
            values = [c.evidence.get("loss_metrics", {}).get(name) for c in checks]
            if any(v is None for v in values):
                missing.append(name)
                losses[name] = None
            elif any(isinstance(v, bool) or not isfinite(v) or v < 0 for v in values):
                raise ValueError("loss metrics must be finite nonnegative values")
            else:
                losses[name] = max(values)
        rows.append(
            {
                "demolished_ids": list(ids),
                "cleared_area_m2": area(ids),
                "worst_case_loss_metrics": losses,
                "loss_metrics_complete": not missing,
                "unavailable_loss_metrics": missing,
            }
        )
    return sorted(rows, key=lambda r: (r["cleared_area_m2"], r["demolished_ids"]))


def run_master_loop(
    config,
    *,
    candidate_ids,
    scenario_ids,
    area,
    problem_sha256,
    evaluate,
    budget,
    cache=None,
    fixed_retained_ids=(),
    master_solver=solve_area_master,
    status_checker=None,
    on_report=None,
):
    """Sequentially validate seeds, then bound/propose/physically check plans."""
    cache = {} if cache is None else cache
    scenarios = tuple(scenario_ids)
    retained = source_policy(config) == RETAINED_SOURCE_POLICY
    cap = source_area_cap(config)
    trace, unresolved, master = [], [], None
    stop_reason, closed, lower, upper = None, False, 0.0, None

    def usage_limit(*, for_ca=False):
        budget.save()
        if budget.value["elapsed_s"] >= config["max_wall_time_s"]:
            return "wall_time_limit"
        if for_ca and budget.value["started_checks"] >= config["max_new_checks"]:
            return "CA_check_limit"
        return None

    def status(check):
        if status_checker is not None:
            return status_checker(check)
        return checked_status(check, config["horizon_s"], allow_source_replacement=retained)

    def assess(ids, *, seed=False, prefetch=False):
        nonlocal stop_reason
        ids = canonical_ids(ids)
        if not set(ids) <= set(candidate_ids):
            raise ValueError("physical plan contains an ineligible candidate action")
        if cap is not None and area(ids) > cap:
            raise ValueError("physical plan exceeds the declared retained-source area cap")
        failed = False
        for scenario in scenarios:
            key = problem_sha256, ids, scenario
            if key not in cache:
                stop_reason = usage_limit(for_ca=True)
                if stop_reason:
                    unresolved.append(
                        {
                            "demolished_ids": list(ids),
                            "scenario_id": scenario,
                            "reason": stop_reason,
                        }
                    )
                    return "unknown"
                budget.value["started_checks"] += 1
                budget.save()  # A crash or unknown consumes a started-call slot.
                check = status(evaluate(ids, scenario))
                cache[key] = check
                budget.save()
            check = status(cache[key])
            cache[key] = check
            trace.append(
                {
                    "demolished_ids": list(ids),
                    "scenario_id": scenario,
                    "seed_verification": seed,
                    "baseline_witness_prefetch": prefetch,
                    **asdict(check),
                }
            )
            if check.status == "unknown":
                stop_reason = "unknown_physical_check"
                unresolved.append(
                    {
                        "demolished_ids": list(ids),
                        "scenario_id": scenario,
                        "reason": check.evidence.get("reason", "unknown"),
                    }
                )
                return "unknown"
            if check.status == "failed":
                failed = True
                if not prefetch:
                    return "failed"
        return "failed" if failed else "passed"

    def report():
        archive = feasible_archive(cache, problem_sha256, scenarios, area)
        best = archive[0] if archive else None
        ub = None if best is None else best["cleared_area_m2"]
        lb = min(ub, lower) if ub is not None else lower
        gap = None if ub is None else max(0.0, ub - lb)
        value = {
            "schema_version": "heritage-area-master-search-1",
            "problem_sha256": problem_sha256,
            "stop_reason": stop_reason,
            "candidate_ids": list(candidate_ids),
            "candidate_count": len(candidate_ids),
            "scenario_ids": list(scenarios),
            "source_policy": source_policy(config),
            "source_area_limit_m2": config.get("source_area_limit_m2") if retained else None,
            "source_area_tolerance_m2": config.get("source_area_tolerance_m2", 0)
            if retained
            else 0,
            "admissible_source_area_cap_m2": cap,
            "initial_source_area_cap_is_verified_feasible_upper_bound": False,
            "verified_feasible_upper_bound_available": best is not None,
            "outside_area_cap_scope": (
                "area_dominated_by_verified_feasible_upper_bound"
                if cap is not None and best is not None
                else "unresolved_outside_declared_cap"
                if cap is not None
                else "uncapped"
            ),
            "baseline_witness_prefetch_requested": config.get("prefetch_baseline_witnesses", False),
            "baseline_witness_checks_completed": sum(
                (problem_sha256, (), s) in cache
                and cache[problem_sha256, (), s].status in {"passed", "failed"}
                for s in scenarios
            ),
            "baseline_witness_prefetch_all_scenarios_complete": config.get(
                "prefetch_baseline_witnesses", False
            )
            and all(
                (problem_sha256, (), s) in cache
                and cache[problem_sha256, (), s].status in {"passed", "failed"}
                for s in scenarios
            ),
            "global_area_gap_closed": closed,
            "global_minimum_area_proven_within_tolerance": closed and best is not None,
            "global_minimum_area_proven": closed and best is not None and gap <= 1e-8,
            "area_gap_absolute_tolerance_m2": config.get("area_gap_absolute_tolerance_m2", 0.0),
            "area_gap_relative_tolerance": config.get("area_gap_relative_tolerance", 0.0),
            "global_area_lower_bound_m2": lb,
            "verified_area_upper_bound_m2": ub,
            "global_area_absolute_gap_m2": gap,
            "global_area_relative_gap": None if gap is None else gap / max(ub, 1e-12),
            "best_ids": None if best is None else best["demolished_ids"],
            "verified_feasible_plans": archive,
            "required_loss_metrics": list(LOSS_METRICS),
            "all_verified_feasible_loss_metrics_complete": all(
                p["loss_metrics_complete"] for p in archive
            ),
            "unresolved_plans": unresolved,
            "global_pareto_frontier_complete": False,
            "loss_tie_break_optimality_proven": False,
            "minimum_demolition_count_proven": False,
            "necessary_demolition_identities_proven": False,
            "full_candidate_space_unresolved": not closed,
            "remaining_scope_empty": False if master is None else master["remaining_scope_empty"],
            "last_master": master,
            "trace": trace,
            "budget": {k: v for k, v in budget.value.items() if k != "active"},
            "scope": (
                "minimum_physical_roof_clearance_under_frozen_model_fires;"
                "not_grass_joint_optimization_or_field_safety"
            ),
            "bound_type": "floating_point_MIP_dual_bounds;not_exact_arithmetic_certificate",
            "exact_arithmetic_optimality_certificate": False,
            "dual_support_interpretation": (
                "alternative_model_support_assumptions;not_spatial_convergence"
            ),
            "declared_scope": config.get("scope"),
            "new_independent_fire_draws": 0,
        }
        if on_report:
            on_report(value)
        return value

    # A cached unknown must not be hidden by a different cheap proposal.
    for key, check in list(cache.items()):
        if key[0] == problem_sha256:
            if cap is not None and area(key[1]) > cap:
                raise ValueError("cached plan exceeds the declared retained-source area cap")
            cache[key] = status(check)
    for (p, ids, scenario), check in cache.items():
        if p == problem_sha256 and check.status == "unknown":
            stop_reason = "unknown_physical_check"
            unresolved.append(
                {
                    "demolished_ids": list(ids),
                    "scenario_id": scenario,
                    "reason": check.evidence.get("reason", "cached_unknown"),
                }
            )
            return report()
    if config.get("prefetch_baseline_witnesses", False):
        outcome = assess((), prefetch=True)
        if outcome == "unknown":
            return report()
        if outcome == "passed" and area(()) == 0:
            closed, stop_reason = True, "verified_zero_area_baseline"
            return report()
        report()
    for ids in seeds(config):
        if assess(ids, seed=True) == "unknown":
            return report()
        report()
    while True:
        stop_reason = usage_limit()
        if stop_reason:
            return report()
        remaining_mip = config["max_mip_time_s"] - budget.value["mip_elapsed_s"]
        if remaining_mip <= 0:
            stop_reason = "MIP_time_limit"
            return report()
        allowance = min(
            remaining_mip,
            config["mip_call_time_limit_s"],
            config["max_wall_time_s"] - budget.value["elapsed_s"],
        )
        started = budget.clock()
        try:
            domain_options = {} if cap is None else {"maximum_cleared_area_m2": cap}
            master = master_solver(
                candidate_ids,
                area,
                scenarios,
                problem_sha256=problem_sha256,
                cache=cache,
                fixed_retained_ids=fixed_retained_ids,
                time_limit_s=allowance,
                mip_relative_gap=0.0,
                **domain_options,
            )
        finally:
            budget.value["mip_elapsed_s"] += budget.clock() - started
            budget.save()
        archive = feasible_archive(cache, problem_sha256, scenarios, area)
        upper = None if not archive else archive[0]["cleared_area_m2"]
        bound = master.get("remaining_master_lower_bound_m2")
        if bound is not None:
            lower = max(lower, float(bound))
        if master["remaining_scope_empty"]:
            if upper is not None:
                lower, closed, stop_reason = upper, True, "remaining_master_exhausted"
            else:
                stop_reason = (
                    "no_feasible_plan_within_declared_area_cap"
                    if cap is not None
                    else "no_feasible_plan_in_frozen_action_space"
                )
            return report()
        if upper is not None:
            tolerance = max(
                config.get("area_gap_absolute_tolerance_m2", 0.0),
                config.get("area_gap_relative_tolerance", 0.0) * upper,
            )
            if upper - min(upper, lower) <= tolerance + 1e-8:
                closed, stop_reason = True, "global_area_gap_closed"
                return report()
        if master.get("next_ids") is None:
            stop_reason = "MIP_no_proposal"
            return report()
        ids = canonical_ids(master["next_ids"])
        if cap is not None and area(ids) > cap:
            stop_reason = "MIP_proposal_outside_declared_area_cap"
            unresolved.append({"demolished_ids": list(ids), "reason": stop_reason})
            return report()
        if not set(ids) <= set(candidate_ids):
            raise ValueError("master proposed an ineligible action")
        if all((problem_sha256, ids, s) in cache for s in scenarios) or any(
            cache.get((problem_sha256, ids, s), ProtectionCheck("unknown", {})).status == "failed"
            for s in scenarios
        ):
            raise ValueError("master proposed an already excluded complete or failed plan")
        if assess(ids) == "unknown":
            return report()
        report()


def persist_result(output, oracle, area, config, ids, scenario, check, record, layout, result):
    check = checked_status(
        check,
        config["horizon_s"],
        allow_source_replacement=source_policy(config) == RETAINED_SOURCE_POLICY,
    )
    check.evidence["problem_sha256"] = oracle.problem_sha256
    actual_area = record.intervention["cleared_roof_union_area_m2"]
    if not np.isclose(actual_area, area(ids), rtol=1e-9, atol=1e-7):
        raise ValueError("master area differs from physically applied roof clearance")
    losses = fire_loss_from_result(
        layout,
        result,
        oracle.inventory,
        demolished_ids=ids,
        source_ids=record.metrics["actual_source_ids"],
        original_node_owners=oracle._engine.layout.owners,
    )
    check.evidence["loss_metrics"] = {
        "ignited_roof_area_m2": losses["physical"]["roof"]["cumulative_ignited_covered_area_m2"],
        "ignited_nonroof_area_m2": losses["physical"]["nonroof"][
            "cumulative_ignited_covered_area_m2"
        ],
        "consumed_fuel_kg": result.consumed_fuel_kg,
        "roof_fire_depleted_kg": losses["fuel"]["roof_fire_depleted_kg"],
        "nonroof_fire_depleted_kg": losses["fuel"]["nonroof_fire_depleted_kg"],
        "active_roof_area_m2": losses["physical"]["roof"]["active_burning_covered_area_m2"],
        "active_nonroof_area_m2": losses["physical"]["nonroof"]["active_burning_covered_area_m2"],
        "clearing_plus_ignited_roof_area_m2": losses["physical"]["roof"][
            "clearing_plus_ignited_union_area_m2"
        ],
        "clearing_plus_ignited_annotation_count_lower": losses["annotation_counts"][
            "clearing_plus_ignited_annotation_count"
        ]["lower"],
        "clearing_plus_ignited_annotation_count_upper": losses["annotation_counts"][
            "clearing_plus_ignited_annotation_count"
        ]["upper"],
    }
    folder = output / "evaluations" / content_hash([list(ids), scenario])
    pending = folder.with_name(folder.name + ".pending")
    pending.mkdir(parents=True, exist_ok=False)
    row = {
        "problem_sha256": oracle.problem_sha256,
        "demolished_ids": list(ids),
        "scenario_id": scenario,
        **asdict(check),
    }
    write_json(pending / "check.json", row)
    write_json(pending / "evaluation.json", record.to_mapping())
    write_json(pending / "fire-loss.json", losses)
    end = float(result.final_state.time_s)
    checkpoints = [t for t in config.get("checkpoints_s", [1200.0, 2400.0, 3600.0]) if t <= end]
    if not checkpoints or checkpoints[-1] < end:
        checkpoints.append(end)
    curves = result_curves(record, layout, result, checkpoints)
    curves["requested_horizon_s"] = config["horizon_s"]
    curves["full_horizon_loss_observed"] = end >= config["horizon_s"] - 1e-8
    write_json(pending / "curves.json", curves)
    write_compact_arrays(pending, layout, result)
    np.savez_compressed(
        pending / "loss-basis.npz",
        baseline_covered_area_m2=layout.node_inputs.parameters.covered_area_m2,
        initial_fuel_kg=result.initial_fuel_kg,
        post_intervention_covered_area_m2=layout.node_request.parameters.covered_area_m2,
        landcover_index=layout.node_request.landcover_index,
    )
    write_json(
        pending / "complete.json",
        {
            "problem_sha256": oracle.problem_sha256,
            "demolished_ids": list(ids),
            "scenario_id": scenario,
            "files_sha256": {p.name: file_sha256(p) for p in pending.iterdir() if p.is_file()},
        },
    )
    pending.rename(folder)
    print(
        {
            "completed": scenario,
            "ids": list(ids),
            "status": check.status,
            "observed_until_s": end,
            "solve_s": check.evidence.get("solve_s"),
        },
        flush=True,
    )


def resume(config_path, output):
    snapshot = output / "source-snapshot/scripts" / Path(__file__).name
    if Path(__file__).resolve() != snapshot.resolve():
        if not snapshot.is_file():
            raise ValueError("prepare-only must freeze the source before resume")
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(snapshot),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")},
        )
    config, experiment = read_json(config_path), read_json(output / "experiment.json")
    validate_config(config)
    require_mip_solver()
    if config != experiment["config"] or file_sha256(config_path) != experiment["config_sha256"]:
        raise ValueError("frozen configuration changed")
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in source_paths()}
    if hashes != experiment["source_sha256"] or any(
        file_sha256(path) != digest for path, digest in experiment["references_sha256"].items()
    ):
        raise ValueError("frozen source or physical input changed")
    if not read_json(output / "preflight.json")["all_passed"]:
        raise ValueError("passing geometry preflight required")
    if list((output / "evaluations").glob("*.pending")):
        raise ValueError("incomplete pending evaluation requires inspection")
    budget = ExecutionBudget(output / "execution-budget.json")
    try:
        oracle, area, _, _ = make_oracle(config_path, config, content_hash(hashes))
        if oracle.problem_sha256 != experiment["problem_sha256"]:
            raise ValueError("frozen physical problem changed")
        cache = {}
        for folder in sorted((output / "evaluations").glob("*")):
            manifest = read_json(folder / "complete.json")
            ids, scenario = canonical_ids(manifest["demolished_ids"]), manifest["scenario_id"]
            if not set(ids) <= set(oracle.candidate_ids) or scenario not in oracle.scenarios:
                raise ValueError("cached action/scenario outside frozen search")
            if folder.name != content_hash([list(ids), scenario]):
                raise ValueError("cached folder identity differs")
            cache[oracle.problem_sha256, ids, scenario] = read_complete(
                folder, oracle.problem_sha256, ids=ids, scenario=scenario
            )
        if budget.value["started_checks"] < len(cache):
            raise ValueError("started-check budget is smaller than persisted physical checks")
        oracle.on_evaluation = lambda ids, s, c, r, layout, result: persist_result(
            output, oracle, area, config, ids, s, c, r, layout, result
        )

        def evaluate(ids, scenario):
            if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
                return ProtectionCheck("unknown", {"reason": "minimum_free_disk_reached"})
            return oracle(ids, scenario)

        def save_report(value):
            value.update(
                fixed_source_ids=experiment["fixed_source_ids"],
                protected_targets=[asdict(t) for t in oracle.inventory.targets],
                horizon_s=config["horizon_s"],
                max_dt_s=config["max_dt_s"],
                roof_scales_m=config["roof_scales_m"],
                quadrature_spacing_m=config["quadrature_spacing_m"],
                source_fires={
                    label: list(fire["footprints"]) for label, fire in oracle.fires.items()
                },
                frozen_source_family_count=len(oracle.fires),
                frozen_source_family_baseline_ids={
                    label: fire["protocol"]["draws"][0]["baseline_source_id"]
                    for label, fire in oracle.fires.items()
                },
                source_family_interpretation=(
                    "reused_design_priority_draws;backup_sources_are_not_new_independent_draws"
                ),
            )
            atomic_json(output / "search.json", value)

        return run_master_loop(
            config,
            candidate_ids=oracle.candidate_ids,
            scenario_ids=oracle.scenarios,
            area=area,
            problem_sha256=oracle.problem_sha256,
            evaluate=evaluate,
            budget=budget,
            cache=cache,
            fixed_retained_ids=sorted(area.owner_ids - set(oracle.candidate_ids)),
            on_report=save_report,
        )
    finally:
        budget.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.prepare_only:
        prepare(args.config.resolve(), args.output.resolve())
    else:
        report = resume(args.config.resolve(), args.output.resolve())
        print(
            {
                k: report[k]
                for k in (
                    "stop_reason",
                    "global_area_gap_closed",
                    "global_area_lower_bound_m2",
                    "verified_area_upper_bound_m2",
                )
            },
            flush=True,
        )


if __name__ == "__main__":
    main()
