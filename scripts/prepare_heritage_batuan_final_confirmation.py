"""Freeze B D9/G0 and its execution amendment before independent-source reveal.

The original preregistration and binder are unchanged. This helper records all
execution overrides before calling the original binder, then runs geometry-only
capacity and source-budget checks. It never builds graph weights or runs CA.
"""

from __future__ import annotations

import argparse
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import prepare_heritage_independent_confirmation as original
import run_heritage_joint_comparison as runner
from prepare_heritage_joint_longcap import encoded, immutable
from shapely.geometry import shape
from shapely.strtree import STRtree

from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_ID = "631393acf6e2c7904225a0e20ecddf2fa1222c97ed2fe293d2e96b103bc8d5a1"
ACCEPTANCE_SHA = "988e71cc3677b2aaa015d3e27f13fcfc0378d3f31e3e79712d97fcac05c2fb5b"
SOLVER_SHA = "e12f3a92bf4d04b4c2971cc4e32bcb7bcca3b9071a07ec51a1d24a6f2acc5af4"


def final_manifest(registration, acceptance, design_config, cost):
    plan = deepcopy(acceptance["plan"])
    old_refinement = design_config["refinement_ids"]
    core = sorted(acceptance["maximum_core_temperature_K"])
    if (
        plan["plan_id"] != "direct-thin-D9-with-no-grass"
        or len(plan["demolished_ids"]) != 9
        or plan["cleared_physical_cell_ids"] != []
        or acceptance["summary"]["complete8_strict_passed_and_closed"] is not True
        or registration["registration_sha256"] != REGISTRATION_ID
    ):
        raise ValueError("only the complete-eight D9/G0 point is frozen for confirmation")
    return {
        "schema_version": original.FINAL_SCHEMA,
        "frozen": True,
        "village": "batuan",
        "inventory_sha256": registration["inventory_sha256"],
        "confirmation_registration_sha256": REGISTRATION_ID,
        "plans": [plan],
        "refinement_ids": sorted(set(old_refinement) | set(plan["demolished_ids"]) | set(core)),
        "original_design_refinement_ids_retained": old_refinement,
        "core_ids": core,
        "core_overlap_policy": design_config["core_overlap_policy"],
        "design_complete8_receipt_sha256": ACCEPTANCE_SHA,
        "canonical_construction_C_m2": acceptance["summary"]["C_m2"],
        "original_physical_action_geometry_receipt": cost,
        "source_priorities_revealed_before_this_final_freeze": False,
        "scope": "one_final_action;not_entire_Pareto_archive_or_action_space",
    }


def execution_amendment(registration, final_path, clock_path):
    """All constants are fixed without reading any confirmatory priorities."""
    return {
        "schema_version": "heritage-pre-unblinding-execution-amendment-1",
        "frozen_before_calling_freeze_four_protocols_or_bind": True,
        "registration_id": REGISTRATION_ID,
        "registration_original_bytes_must_remain_unchanged": True,
        "final_candidates_path": str(final_path),
        "final_candidates_sha256": file_sha256(final_path),
        "reason": "design_data_already_show_6h_insufficient;extend_execution_before_source_reveal",
        "overrides": {
            "terminal_horizon_cap_s": {"base": 21600.0, "effective": 172800.0},
            "max_wall_time_s": {"base": 28800.0, "effective": 172800.0},
            "max_total_checks": {"base": 40, "effective": 8},
            "max_new_checks": {"base": 8, "effective": 8},
            "execution_case_order": {"base": "declared", "effective": "plans_first"},
            "execution_priority_plan_ids": {
                "base": None,
                "effective": ["direct-thin-D9-with-no-grass"],
            },
            "stop_plan_after_protection_failure": {"base": False, "effective": True},
            "required_solver_source_sha256": {"base": None, "effective": SOLVER_SHA},
            "clock_compatibility_receipt_path": {"base": None, "effective": str(clock_path)},
            "clock_compatibility_receipt_sha256": {
                "base": None,
                "effective": file_sha256(clock_path),
            },
        },
        "unchanged_numerics": {
            "horizon_s": 3600.0,
            "checkpoints_s": [1200.0, 2400.0, 3600.0],
            "roof_scales_m": [1.0, 0.5],
            "quadrature_spacing_m": 0.5,
            "max_dt_s": 1.0,
            "max_nodes": 500000,
            "max_radiation_edges": 60000000,
            "minimum_free_disk_GB": 5.0,
            "terminal_fire_verification": True,
            "new_independent_fire_draw_count": 4,
        },
        "unchanged_sampling": {
            key: registration[key]
            for key in (
                "seed",
                "rng",
                "rng_namespace",
                "allocation",
                "ignition_area_m2",
                "near_distance_m",
                "empty_near_pool_policy",
                "pressure_distance_m",
                "preferred_pool_policy",
                "fallback_policy",
                "anchor_policy",
                "resampling_rule",
                "confirmation_failure_rule",
                "weight_policy",
            )
        },
        "unchanged_source_generation_files_sha256": registration["generator_source_files_sha256"],
        "no_material_or_fuel_tuning": True,
        "stop_at_first_core_ignition": False,
        "stop_at_extinction": True,
        "acceptance_rule": "all8_true_native_core_passes_with_strict_valid_extinction_closure",
        "raw_edge_invalid_flags_are_not_promoted_to_primary_confirmation_pass": True,
        "time_or_capacity_exceeded": "resource_or_time_unknown;no_regrid_no_redraw_no_false_pass",
        "one_valid_core_failure_can_refute_conjunction": True,
        "skipped_cases_remain_missing": True,
        "failure_action": registration["confirmation_failure_rule"],
        "mesh_scope": (
            "new_per_source_confirmatory_mesh;retain_old9_union_D9_core_plus_source_prefix"
        ),
        "old_design_losses_or_optimality_transferred": False,
        "new_CA_checks": 0,
    }


def clone_execution_config(base, amendment, additional):
    config = deepcopy(base)
    for key, value in amendment["unchanged_numerics"].items():
        if config.get(key) != value:
            raise ValueError("the original confirmation binder changed fixed numerics: " + key)
    for key, change in amendment["overrides"].items():
        if config.get(key, change["base"]) != change["base"]:
            raise ValueError("base configuration differs from pre-unblinding amendment: " + key)
        config[key] = deepcopy(change["effective"])
    config["additional_frozen_inputs_sha256"] = additional
    config["scope"] = (
        "independent_D9_G0_confirmation;new_source_mesh;no_design_loss_or_optimality_transfer;"
        "strict_primary_all8;unclosed_or_edge_invalid_remains_unknown"
    )
    return config


def source_guard(oracle, binding):
    """Reintegrate source budgets from immutable original physical roof atoms."""
    polygons = np.asarray(oracle.base.geometry, dtype=object)
    tree = STRtree(polygons)
    parameters = oracle.base.node_inputs.parameters
    parent = oracle.bundle.request
    rows = []
    binding_by_fire = {
        row["fire_id"]: row for row in binding["common_source_and_refinement_requirements"]
    }
    removed = set(next(iter(oracle.plans.values())).demolished_ids)
    for fire_id, fire in oracle.fires.items():
        priority = fire["protocol"]["draws"][0]["priority_ids"]
        actual = next(i for i in priority if i not in removed)
        original_row = binding_by_fire[fire_id]
        expected = original_row["per_plan"][0]
        if actual != expected["source_id"]:
            raise ValueError("source changed between original binding and current runner")
        footprint = shape(original_row["actual_source_footprints"][actual]["geometry"])
        if (
            actual not in fire["footprints"]
            or abs(footprint.area - 25) > 1e-8
            or footprint.difference(oracle.inventory.by_id[actual].geometry).area > 1e-8
            or fire["footprints"][actual].sha256 != expected["footprint_sha256"]
            or not set(original_row["minimum_common_refined_ids"]) <= set(fire["refined_ids"])
        ):
            raise ValueError("the independent source lost its frozen 25m2 footprint")
        nodes = tree.query(footprint, predicate="intersects")
        overlap = np.array([polygons[n].intersection(footprint).area for n in nodes])
        nodes, overlap = nodes[overlap > 1e-10], overlap[overlap > 1e-10]
        if (
            not len(nodes)
            or any(oracle.base.owners[n] != (actual,) for n in nodes)
            or any(set(oracle.base.owners[n]) <= removed for n in nodes)
            or not parent.known_mask.ravel()[oracle.base.cell_id[nodes]].all()
            or abs(float(overlap.sum()) - 25) > 1e-6
        ):
            raise ValueError(
                "actual source is ambiguous, unknown, cleared or physically incomplete"
            )
        fractions = overlap / np.array([polygons[n].area for n in nodes])
        budget = {
            key: float(np.sum(getattr(parameters, key).ravel()[nodes] * fractions))
            for key in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        }
        if abs(budget["covered_area_m2"] - 25) > 1e-6 or any(v <= 0 for v in budget.values()):
            raise ValueError("independent physical ignition budget is not full positive fuel")
        rows.append(
            {
                "fire_id": fire_id,
                "actual_source_id": actual,
                "source_replaced": expected["source_replaced"],
                "source_footprint_sha256": fire["footprints"][actual].sha256,
                "independently_integrated_source_budget": budget,
                "common_refined_ids": list(fire["refined_ids"]),
            }
        )
    return rows


def prepare(registration_path, acceptance_path, design_config_path, directory, clock_path):
    started = time.monotonic()
    registration_path, acceptance_path, design_config_path, directory, clock_path = [
        Path(p).resolve()
        for p in (registration_path, acceptance_path, design_config_path, directory, clock_path)
    ]
    registration = original._checked_registration(registration_path)
    if file_sha256(acceptance_path) != ACCEPTANCE_SHA:
        raise ValueError("the immutable complete-eight acceptance receipt changed")
    acceptance, design_config, clock = map(
        read_json, (acceptance_path, design_config_path, clock_path)
    )
    design_experiment_path = Path(acceptance["run_directory"]) / "experiment.json"
    design_experiment = read_json(design_experiment_path)
    if (
        file_sha256(design_experiment_path) != acceptance["experiment_sha256"]
        or file_sha256(design_config_path) != design_experiment["config_sha256"]
        or design_config != design_experiment["config"]
        or file_sha256(design_config["vegetation_proposals_path"])
        != design_config["vegetation_proposals_sha256"]
    ):
        raise ValueError("the design receipt/config/cost catalog identity changed")
    if (
        file_sha256(ROOT / "src/pyfireca/village/solver_v3.py") != SOLVER_SHA
        or clock.get("schema_version") != "v3-sample-clock-error-path-extension-audit-1"
        or clock.get("new_solver_sha256") != SOLVER_SHA
        or clock.get("regression_validation", {}).get("total_passed", 0) < 123
        or clock.get("remove_exact_added_error_branch_restores_entire_old_file_byte_for_byte")
        is not True
        or clock.get("audit_sha256")
        != content_hash({k: v for k, v in clock.items() if k != "audit_sha256"})
    ):
        raise ValueError("the exact audited success-path-compatible solver is required")
    catalog = read_json(design_config["vegetation_proposals_path"])
    cost = next(p for p in catalog["plans"] if p["plan_id"] == acceptance["plan"]["plan_id"])
    final_path, amendment_path = (
        directory / "final-candidates.json",
        directory / "execution-amendment.json",
    )
    base_dir = directory / "original-binding"
    if list(base_dir.glob("source-*.json")) and not (
        final_path.exists() and amendment_path.exists()
    ):
        raise ValueError("sources were revealed before both immutable pre-unblinding receipts")
    inputs = {
        registration_path,
        acceptance_path,
        design_config_path,
        clock_path,
        Path(design_config["vegetation_proposals_path"]),
        Path(__file__).resolve(),
        *map(Path, registration["generator_source_files_sha256"]),
        design_experiment_path,
        ROOT / "src/pyfireca/village/solver_v3.py",
        Path(runner.__file__).resolve(),
    }
    before = {str(p): file_sha256(p) for p in inputs}
    # These two immutable writes MUST precede the first priority materialization.
    immutable(final_path, encoded(final_manifest(registration, acceptance, design_config, cost)))
    amendment = execution_amendment(registration, final_path, clock_path)
    immutable(amendment_path, encoded(amendment))
    if any(file_sha256(p) != digest for p, digest in before.items()):
        raise ValueError("a pre-unblinding input changed")
    binding = original.bind(registration_path, final_path, base_dir)
    base_path = base_dir / "confirmation-config.json"
    binding_path = base_dir / "binding-receipt.json"
    additional = {
        **before,
        **{str(p): file_sha256(p) for p in (final_path, amendment_path, base_path, binding_path)},
        **binding["source_protocol_files_sha256"],
    }
    config = clone_execution_config(read_json(base_path), amendment, additional)
    config_path = directory / "confirmation-longcap-config.json"
    runner.validate_config(config)
    immutable(config_path, encoded(config))

    def forbidden(*args, **kwargs):
        raise RuntimeError("confirmation preparation must not construct graph weights or run CA")

    rows, guards, resource_error = [], [], None
    with (
        patch.object(runner, "roof_area_integrated_radiation_graph", forbidden),
        patch.object(runner, "refined_contact_operator", forbidden),
        patch.object(runner.JointComparisonOracle, "engine_for_scenario", forbidden),
        patch.object(RoofJointEvaluationEngine, "simulate", forbidden),
    ):
        oracle, references = runner.make_oracle(config_path, config, file_sha256(runner.__file__))
        parent_before = request_fingerprint(oracle.bundle.request)
        guards = source_guard(oracle, binding)
        for label, fire in oracle.fires.items():
            for scale in oracle.scales_m:
                try:
                    layout = refine_roof_layout(
                        oracle.base,
                        oracle.inventory,
                        cell_size_m=scale,
                        refined_ids=fire["refined_ids"],
                        footprints=tuple(fire["footprints"].values()),
                        max_nodes=500000,
                    )
                    bounds = runner.edge_bounds(layout, oracle.transport, fire["refined_ids"])
                    row = {
                        "fire_id": label,
                        "scale_m": scale,
                        "nodes": len(layout.owners),
                        **bounds,
                        "passed": len(layout.owners) <= 500000 and max(bounds.values()) <= 60000000,
                    }
                    rows.append(row)
                    immutable(directory / f"geometry-preflight-{len(rows)}.json", encoded(row))
                    print({"geometry_preflight": row}, flush=True)
                    if not row["passed"]:
                        resource_error = "capacity_bound_exceeded;unknown;no_regrid_or_redraw"
                        break
                except ValueError as error:
                    resource_error = str(error)
                    break
            if resource_error:
                break
        if oracle._engine is not None:
            raise ValueError("a scenario engine was instantiated during zero-CA preparation")
        if request_fingerprint(oracle.bundle.request) != parent_before:
            raise ValueError("geometry preparation modified original parent inputs")
    if any(file_sha256(p) != digest for p, digest in additional.items()):
        raise ValueError("a bound execution/source input changed during preflight")
    receipt = {
        "schema_version": "heritage-independent-D9-G0-confirmation-preparation-1",
        "registration_path": str(registration_path),
        "registration_id": REGISTRATION_ID,
        "final_candidates_path": str(final_path),
        "final_candidates_sha256": file_sha256(final_path),
        "amendment_path": str(amendment_path),
        "amendment_sha256": file_sha256(amendment_path),
        "config_path": str(config_path),
        "config_sha256": file_sha256(config_path),
        "base_binding_receipt_path": str(binding_path),
        "base_binding_receipt_sha256": file_sha256(binding_path),
        "source_guards": guards,
        "geometry_preflight": rows,
        "capacity_passed": resource_error is None and len(rows) == 8,
        "resource_unknown_reason": resource_error,
        "new_CA_checks": 0,
        "graph_weight_builds": 0,
        "producer_frozen": False,
        "planned_independent_draws": 4,
        "planned_scenario_checks": 8,
        "new_mesh_not_identical_to_design_mesh": True,
        "parent_request_unchanged": True,
        "old_losses_or_optimality_transferred": False,
        "additional_frozen_inputs_sha256": additional,
        "physical_references_sha256": references,
        "validation_problem_sha256_not_execution_identity": oracle.problem_sha256,
        "elapsed_s": time.monotonic() - started,
    }
    immutable(directory / "preparation-receipt.json", encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--design-config", type=Path, required=True)
    parser.add_argument("--clock-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(
        args.registration, args.acceptance, args.design_config, args.output, args.clock_receipt
    )
    print({k: result[k] for k in ("config_path", "capacity_passed", "new_CA_checks", "elapsed_s")})


if __name__ == "__main__":
    main()
