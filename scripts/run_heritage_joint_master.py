"""Frozen joint roof/whole-ground area master with long-horizon protection queries.

This independent producer never imports legacy roof failure certificates.
Failures stop at core ignition; passing requires strict extinction. It proves
only minimum construction C in its registered action/source/model domain, not
an entire C/N/A Pareto frontier. Existing production snapshots remain untouched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from copy import deepcopy
from dataclasses import asdict
from math import isfinite
from pathlib import Path

import numpy as np
import run_heritage_area_master as controller
from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_fixed_comparison import atomic_json, result_curves, write_compact_arrays

from pyfireca.planning.boundary_scope import (
    finite_domain_observation_is_complete,
    validate_finite_domain_mip_check,
)
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.joint_search import JointProtectionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.joint_action_prototype import solve_joint_action_master, validate_joint_cut
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study
from pyfireca.planning.terminal_verification import (
    close_terminal_event_curves,
    terminal_observation_is_complete,
    terminal_summary_is_complete,
)
from pyfireca.village.extinction_v3 import extinction_observation_fingerprints
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-joint-action-master-config-1"
REQUIRED = frozenset(
    {
        "check.json",
        "evaluation.json",
        "curves.json",
        "fire-loss.json",
        "layout.npz",
        "final-state.npz",
        "loss-basis.npz",
    }
)


def validate_config(config):
    if config.get("schema_version") != SCHEMA:
        raise ValueError("independent joint-action master schema required")
    if config.get("source_policy") != controller.RETAINED_SOURCE_POLICY:
        raise ValueError("joint master requires total-area bounded retained-source families")
    if config.get("terminal_validation_scope", "strict") not in {
        "strict",
        "finite_mask_conditioned",
    }:
        raise ValueError("unknown terminal validation scope")
    for key in (
        "horizon_s",
        "max_dt_s",
        "quadrature_spacing_m",
        "max_wall_time_s",
        "max_mip_time_s",
        "mip_call_time_limit_s",
        "minimum_free_disk_GB",
    ):
        value = config[key]
        if isinstance(value, bool) or not isfinite(value) or value <= 0:
            raise ValueError("positive finite joint configuration required: " + key)
    for key in ("max_new_checks", "max_nodes", "max_radiation_edges"):
        if isinstance(config[key], bool) or not isinstance(config[key], int) or config[key] < 1:
            raise ValueError("positive integer joint configuration required: " + key)
    for key in ("source_area_limit_m2", "source_area_tolerance_m2"):
        value = config.get(key, 0)
        if isinstance(value, bool) or not isfinite(value) or value < 0:
            raise ValueError("finite nonnegative total area cap and tolerance required")
    if not config.get("source_fires") or not config.get("refinement_ids"):
        raise ValueError("frozen source families and refinement required")
    if not config.get("experimental_test_protocol", False) and (
        config["horizon_s"] < 3600
        or tuple(config["roof_scales_m"]) != (1.0, 0.5)
        or config["quadrature_spacing_m"] != 0.5
    ):
        raise ValueError("production joint master requires dual support, q0.5 and long horizon")
    for row in config.get("seed_action_plans", []):
        controller.canonical_ids(row["action_ids"])


def source_paths():
    # Full source closure avoids missing any future lazy local import.
    return [
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *[
            ROOT / "scripts" / name
            for name in (
                "run_heritage_joint_master.py",
                "run_heritage_area_master.py",
                "run_heritage_fixed_comparison.py",
                "run_heritage_area_study.py",
                "run_heritage_roof_study.py",
                "preflight_heritage_exact_expansion.py",
            )
        ],
    ]


def make_oracle(config_path, config, code_hash):
    from dataclasses import replace

    validate_config(config)
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study_path)
    if config.get("candidate_ids") is not None and tuple(sorted(config["candidate_ids"])) != tuple(
        sorted(inventory.candidate_ids)
    ):
        raise ValueError("joint master must preserve the complete inventory roof-action set")
    if bundle.request.grid.cell_size_m != 5:
        raise ValueError("whole ground actions require the original 5m physical grid")
    fires, references = controller.load_retained_fires(config_path, config, inventory)
    bundle = replace(
        bundle,
        request=replace(
            bundle.request,
            scenario=replace(bundle.request.scenario, max_dt_s=float(config["max_dt_s"])),
        ),
    )
    oracle = JointProtectionOracle(
        bundle, transport, inventory, config=config, fires=fires, code_version=code_hash
    )
    if config.get("reference_phase_master_experiment_path"):
        reference_path = (
            config_path.parent / config["reference_phase_master_experiment_path"]
        ).resolve()
        if file_sha256(reference_path) != config.get("reference_phase_master_experiment_sha256"):
            raise ValueError("phase preparation reference bytes changed")
        reference = read_json(reference_path)
        if content_hash(reference["problem"]) != reference["problem_sha256"]:
            raise ValueError("phase preparation reference fingerprint differs")
        for key in (
            "case_sha256",
            "inventory_sha256",
            "transport_parameters",
            "candidate_ids",
            "refinement_seed_ids",
            "scales_m",
            "quadrature_spacing_m",
            "core_overlap_policy",
            "fires",
            "freeze_integration_membership",
            "source_area_coverage",
        ):
            if content_hash(oracle.roof.problem.get(key)) != content_hash(
                reference["problem"].get(key)
            ):
                raise ValueError("joint physical preparation differs from reference phase: " + key)
        references[str(reference_path)] = file_sha256(reference_path)
    for row in config.get("seed_action_plans", []):
        for proof in oracle.source_coverages.values():
            proof.validate_actions(row["action_ids"])
    study = read_json(study_path)
    inputs = [study_path]
    for key in ("labelme_annotations", "image_reference", "structures_path"):
        if study.get(key):
            inputs.append((study_path.parent / study[key]).resolve())
    inputs.extend(
        p for p in (study_path.parent / study["case_dir"]).resolve().rglob("*") if p.is_file()
    )
    references.update({str(p): file_sha256(p) for p in inputs})
    return oracle, references


def prepare(config_path, output):
    config = read_json(config_path)
    validate_config(config)
    controller.require_mip_solver()
    output.mkdir(parents=True, exist_ok=False)
    paths = tuple(source_paths())
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in paths}
    config_hash = file_sha256(config_path)
    for path in paths:
        target = output / "source-snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    oracle, references = make_oracle(config_path, config, content_hash(hashes))
    partitions, rows = {}, []
    for scenario, (label, _) in oracle.scenarios.items():
        layout, universe = oracle.prepared_geometry(scenario)
        bounds = edge_bounds(layout, oracle.transport, oracle.fires[label]["refined_ids"])
        if (
            len(layout.owners) > config["max_nodes"]
            or max(bounds.values()) > config["max_radiation_edges"]
        ):
            raise ValueError("joint geometry capacity failed before CA")
        partitions[scenario] = universe.physical_partition_sha256
        rows.append(
            {
                "scenario_id": scenario,
                "nodes": len(layout.owners),
                **bounds,
                "physical_partition_sha256": partitions[scenario],
            }
        )
        del layout, universe
    if (
        file_sha256(config_path) != config_hash
        or any(file_sha256(p) != hashes[str(p.relative_to(ROOT))] for p in paths)
        or any(file_sha256(p) != h for p, h in references.items())
    ):
        raise ValueError("joint source or physical input changed while freezing")
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
            "native_problem": oracle.native_problem,
            "native_problem_sha256": oracle.native_problem_sha256,
            "terminal_validation_scope": oracle.terminal_validation_scope,
            "boundary_scope_problem": oracle.boundary_scope_problem,
            "joint_universe_sha256": oracle.logical_universe.sha256,
            "scenario_partitions": partitions,
            "new_CA_checks": 0,
            "old_roof_or_phase_certificates_imported": False,
        },
    )
    write_json(output / "preflight.json", {"rows": rows, "passed": True})
    atomic_json(
        output / "execution-budget.json",
        {
            "elapsed_s": 0.0,
            "mip_elapsed_s": 0.0,
            "started_checks": 0,
            "active": None,
        },
    )
    return {
        "prepared": True,
        "candidate_count": len(oracle.candidate_ids),
        "scenario_count": len(partitions),
        "new_CA_checks": 0,
    }


def checked_status(check):
    if not isinstance(check, ProtectionCheck):
        raise TypeError("typed physical check required")
    if check.evidence.get("schema_version") == "heritage-finite-domain-mip-receipt-1":
        validate_finite_domain_mip_check(check)
        return check
    if check.evidence.get("invalid_reasons"):
        return ProtectionCheck("unknown", {**check.evidence, "reason": "invalid_physical_domain"})
    if check.status == "passed":
        terminal = check.evidence.get("terminal_verification")
        if (
            terminal is None
            or not terminal_summary_is_complete(terminal)
            or not terminal["protection_feasible"]
        ):
            return ProtectionCheck(
                "unknown", {**check.evidence, "reason": "strict_terminal_closure_missing"}
            )
    if check.status == "failed" and not any(
        c.get("ignited") for c in check.evidence.get("core_outcomes", [])
    ):
        return ProtectionCheck(
            "unknown", {**check.evidence, "reason": "native_core_failure_missing"}
        )
    return check


def master_adapter(oracle):
    def solve(
        candidates,
        area,
        scenarios,
        *,
        problem_sha256,
        cache=None,
        fixed_retained_ids=(),
        time_limit_s=None,
        mip_relative_gap=0,
        maximum_cleared_area_m2=None,
    ):
        if tuple(candidates) != oracle.candidate_ids or area is not oracle.logical_universe.area:
            raise ValueError("controller joint universe or canonical C differs")
        if fixed_retained_ids or mip_relative_gap != 0:
            raise ValueError(
                "joint fixed owners are bound by the typed universe, and MIP gap is zero"
            )
        for key, check in (cache or {}).items():
            if key[0] != problem_sha256:
                continue
            if oracle.terminal_validation_scope == "finite_mask_conditioned":
                if check.status != "unknown":
                    validate_finite_domain_mip_check(check, analysis_problem_sha256=problem_sha256)
            elif check.evidence.get("schema_version") == "heritage-finite-domain-mip-receipt-1":
                raise ValueError("conditional receipts cannot enter a strict native problem")
        try:
            answer = solve_joint_action_master(
                oracle.logical_universe,
                scenarios,
                problem_sha256=problem_sha256,
                cache=cache,
                area_cap_m2=maximum_cleared_area_m2,
                time_limit_s=time_limit_s,
                scenario_partition_sha256s=oracle.expected_partitions,
                max_matrix_nonzeros=oracle.config.get("max_matrix_nonzeros", 5_000_000),
            )
        except MemoryError as error:
            return {
                "next_ids": None,
                "remaining_scope_empty": False,
                "remaining_master_lower_bound_m2": None,
                "computation_status": str(error),
            }
        ids = answer.get("next_ids")
        if ids is not None and all(
            (cache or {})
            .get((problem_sha256, tuple(ids), scenario), ProtectionCheck("unknown", {}))
            .status
            == "passed"
            for scenario in scenarios
        ):
            # The joint MIP keeps passed plans. Its bound can close the gap first;
            # otherwise retain the bound and stop without repeating native CA.
            answer = {**answer, "next_ids": None, "known_passed_proposal_not_rerun": True}
        return answer

    return solve


def folder_for(output, actions, scenario):
    return output / "evaluations" / content_hash([list(actions), scenario])


def persist(output, oracle, config, actions, scenario, check, record, layout, result, loss):
    folder = folder_for(output, actions, scenario)
    pending = folder.with_name(folder.name + ".pending")
    pending.mkdir(parents=True, exist_ok=False)
    plan = oracle.logical_universe.plan(actions, plan_id="joint-" + content_hash(actions))
    write_json(
        pending / "check.json",
        {
            "problem_sha256": oracle.problem_sha256,
            "action_ids": list(actions),
            "scenario_id": scenario,
            **asdict(check),
        },
    )
    write_json(pending / "evaluation.json", record.to_mapping())
    write_json(pending / "fire-loss.json", loss)
    checkpoints = config.get("checkpoints_s", [config["horizon_s"]])
    curves = close_terminal_event_curves(
        result_curves(record, layout, result, checkpoints), check.evidence["terminal_verification"]
    )
    write_json(pending / "curves.json", curves)
    write_compact_arrays(pending, layout, result)
    before, after = layout.node_inputs.parameters, layout.node_request.parameters
    np.savez_compressed(
        pending / "loss-basis.npz",
        baseline_covered_area_m2=before.covered_area_m2,
        post_intervention_covered_area_m2=after.covered_area_m2,
        baseline_pyrolysable_fuel_kg=before.pyrolysable_dry_fuel_mass_kg,
        initial_fuel_kg=result.initial_fuel_kg,
        initial_total_dry_fuel_kg=result.initial_total_dry_fuel_kg,
        landcover_index=layout.node_request.landcover_index,
    )
    files = {p.name: file_sha256(p) for p in pending.iterdir() if p.is_file()}
    write_json(
        pending / "complete.json",
        {
            "schema_version": "heritage-joint-action-master-evaluation-1",
            "problem_sha256": oracle.problem_sha256,
            "action_ids": list(actions),
            "scenario_id": scenario,
            "demolished_ids": list(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "files_sha256": files,
        },
    )
    pending.rename(folder)


def persist_unknown(output, oracle, actions, scenario, check):
    """Persist pre-native unknowns so a restart cannot silently retry them."""
    folder = folder_for(output, actions, scenario)
    pending = folder.with_name(folder.name + ".pending")
    pending.mkdir(parents=True, exist_ok=False)
    write_json(
        pending / "check.json",
        {
            "problem_sha256": oracle.problem_sha256,
            "action_ids": list(actions),
            "scenario_id": scenario,
            **asdict(check),
        },
    )
    plan = oracle.logical_universe.plan(actions)
    write_json(
        pending / "complete.json",
        {
            "schema_version": "heritage-joint-action-master-unknown-1",
            "problem_sha256": oracle.problem_sha256,
            "action_ids": list(actions),
            "scenario_id": scenario,
            "demolished_ids": list(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "query_slot_consumed": True,
            "native_result_available": False,
            "files_sha256": {"check.json": file_sha256(pending / "check.json")},
        },
    )
    pending.rename(folder)


def read_complete(folder, oracle, actions, scenario):
    if (
        tuple(actions) != oracle.logical_universe.canonical_actions(actions)
        or scenario not in oracle.scenarios
    ):
        raise ValueError("joint cache action or scenario is outside the declared domain")
    manifest = read_json(folder / "complete.json")
    if (
        manifest.get("schema_version")
        not in {
            "heritage-joint-action-master-evaluation-1",
            "heritage-joint-action-master-unknown-1",
        }
        or manifest.get("problem_sha256") != oracle.problem_sha256
        or manifest.get("action_ids") != list(actions)
        or manifest.get("scenario_id") != scenario
    ):
        raise ValueError("joint native cache problem/action/scenario identity differs")
    plan = oracle.logical_universe.plan(actions)
    if manifest["demolished_ids"] != list(plan.demolished_ids) or manifest[
        "cleared_physical_cell_ids"
    ] != list(plan.cleared_physical_cell_ids):
        raise ValueError("joint native cache roof or ground action differs")
    for name, digest in manifest["files_sha256"].items():
        if Path(name).name != name or file_sha256(folder / name) != digest:
            raise ValueError("joint native cached artifact changed")
    row = read_json(folder / "check.json")
    if any(row.get(k) != manifest.get(k) for k in ("problem_sha256", "action_ids", "scenario_id")):
        raise ValueError("joint native check identity differs")
    check = ProtectionCheck(row["status"], row["evidence"])
    if check.evidence.get("problem_sha256", oracle.problem_sha256) != oracle.problem_sha256:
        raise ValueError("joint check criterion differs from its native manifest")
    if manifest["schema_version"] == "heritage-joint-action-master-unknown-1":
        if (
            set(manifest["files_sha256"]) != {"check.json"}
            or check.status != "unknown"
            or not check.evidence.get("reason")
        ):
            raise ValueError("pre-native unknown receipt cannot claim physical feasibility")
        return check
    if not manifest["files_sha256"].keys() >= REQUIRED:
        raise ValueError("joint native cache lacks a required artifact hash")
    record = read_json(folder / "evaluation.json")
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        values = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
    values["time_s"], values["step_index"] = float(values["time_s"]), int(values["step_index"])
    state = V3State(**values)
    peak = record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"]
    if (
        extinction_observation_fingerprints(state, peak)
        != check.evidence["observation_fingerprints"]
    ):
        raise ValueError("joint native state or historical peak differs from saved observation")
    if check.evidence["physical_partition_sha256"] != oracle.expected_partitions[scenario]:
        raise ValueError("joint native physical partition differs")
    expected_source = oracle.source_coverages[oracle.scenarios[scenario][0]].validate_actions(
        actions
    )["source_id"]
    if (
        check.evidence["source_id"] != expected_source
        or record["metrics"]["actual_source_ids"] != [expected_source]
        or record["horizon_s"] != oracle.horizon_s
        or record["diagnostics"]["simulated_until_s"] != state.time_s
        or check.evidence["simulated_until_s"] != state.time_s
        or record["intervention"]["demolished_ids"] != list(plan.demolished_ids)
        or record["intervention"]["cleared_physical_cell_ids"]
        != list(plan.cleared_physical_cell_ids)
    ):
        raise ValueError("joint cached source, action or actual clock differs")
    if check.evidence["core_outcomes"] != record["metrics"]["core_outcomes"]:
        raise ValueError("joint query core outcomes differ from the saved native record")
    conditional = check.evidence.get("schema_version") == "heritage-finite-domain-mip-receipt-1"
    if conditional:
        if oracle.terminal_validation_scope != "finite_mask_conditioned":
            raise ValueError("conditional cache cannot be used by a strict native problem")
        validate_finite_domain_mip_check(check, analysis_problem_sha256=oracle.problem_sha256)
        view = check.evidence["finite_domain_verification"]
        if (
            view["native_problem_sha256"] != oracle.native_problem_sha256
            or view["lineage"]["native_record_sha256"] != content_hash(record)
            or view["native_invalid_reasons"] != record["diagnostics"]["invalid_reasons"]
        ):
            raise ValueError("conditional cache differs from the unchanged native record")
        finite_domain_observation_is_complete(view, state, peak)
    elif oracle.terminal_validation_scope != "strict":
        raise ValueError("native cache cannot be relabeled as a conditional criterion")
    elif check.evidence["invalid_reasons"] != record["diagnostics"]["invalid_reasons"]:
        raise ValueError("strict query changed native invalid reasons")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        offsets, owners = saved["owner_offsets"], saved["owner_ids"]
        groups = [set(owners[offsets[n] : offsets[n + 1]]) for n in range(len(offsets) - 1)]
    actual_core_ids = []
    for target in oracle.inventory.targets:
        nodes = [n for n, group in enumerate(groups) if target.building_id in group]
        if not nodes:
            raise ValueError("joint cached layout omits a protected core")
        if np.isfinite(state.ignition_time_s.ravel()[nodes]).any():
            actual_core_ids.append(target.building_id)
    if sorted(actual_core_ids) != sorted(record["metrics"]["ignited_core_ids"]):
        raise ValueError("joint cached core failure differs from actual ignition state")
    if check.status == "passed":
        complete = (
            finite_domain_observation_is_complete(
                check.evidence["finite_domain_verification"], state, peak
            )
            if conditional
            else terminal_observation_is_complete(
                check.evidence["terminal_verification"], state, peak
            )
        )
        if not complete:
            raise ValueError("joint passed cache lacks its criterion's extinction closure")
    if check.status == "failed" and check.evidence.get("failure_certificate") is not None:
        cert = check.evidence["failure_certificate"]
        if (
            cert["native_case_sha256"] != record["case_sha256"]
            or cert["native_evaluation_sha256"] != record["evaluation_sha256"]
            or cert["native_physical_request_sha256"]
            != record["diagnostics"]["physical_request_sha256"]
        ):
            raise ValueError("joint failure certificate native observation binding differs")
        validate_joint_cut(
            check.evidence["failure_certificate"],
            oracle.logical_universe,
            problem_sha256=oracle.problem_sha256,
            scenario_id=scenario,
            actions=actions,
            physical_partition_sha256=oracle.expected_partitions[scenario],
        )
    return checked_status(check)


def publish_report(output, oracle, report, *, cache=None):
    value = {**deepcopy(report), "schema_version": "heritage-joint-action-master-search-1"}
    for row in value["verified_feasible_plans"]:
        actions = row.pop("demolished_ids")
        plan = oracle.logical_universe.plan(actions)
        row.update(
            action_ids=actions,
            demolished_ids=list(plan.demolished_ids),
            cleared_physical_cell_ids=list(plan.cleared_physical_cell_ids),
        )
        row["legacy_controller_loss_metrics"] = row.pop("worst_case_loss_metrics")
        summaries = [
            (cache or {}).get((oracle.problem_sha256, tuple(actions), scenario))
            for scenario in oracle.scenarios
        ]
        if all(check is not None and check.status == "passed" for check in summaries):
            metrics = [check.evidence["terminal_verification"]["metrics"] for check in summaries]
            row["worst_case_terminal_C_N_A"] = {
                "C_geometric_construction_m2": row["cleared_area_m2"],
                "N_burned_out_original_owner_count": {
                    side: max(m["N_burned_out_annotation_attribution_count"][side] for m in metrics)
                    for side in ("lower", "upper")
                },
                "A_physical_burned_out_m2": max(
                    m["burned_out_physical_covered_area_m2"] for m in metrics
                ),
            }
        else:
            row["worst_case_terminal_C_N_A"] = None
    for row in value["trace"] + value["unresolved_plans"]:
        if "demolished_ids" in row:
            row["action_ids"] = row.pop("demolished_ids")
    all_ground = oracle.logical_universe.grass_scope == "all_eligible_parent_cells"
    value["legacy_controller_required_loss_metrics"] = value.pop("required_loss_metrics")
    value["legacy_controller_loss_metrics_complete"] = value.pop(
        "all_verified_feasible_loss_metrics_complete"
    )
    value.update(
        scope="minimum_joint_geometric_construction_C;declared_sources_and_dual_model_support",
        grass_scope=oracle.logical_universe.grass_scope,
        registered_grass_cell_count=len(oracle.logical_universe.grass_cell_ids),
        all_eligible_grass_cell_count=oracle.logical_universe.eligible_grass_cell_count,
        total_action_area_cap_m2=controller.source_area_cap(oracle.config),
        source_projection_proof="roof_C<=joint_total_C;same_declared_cap",
        old_roof_or_phase_certificates_imported=False,
        strict_extinction_required_for_feasible=True,
        terminal_validation_scope=oracle.terminal_validation_scope,
        native_problem_sha256=oracle.native_problem_sha256,
        finite_domain_analysis_problem_sha256=(
            oracle.problem_sha256
            if oracle.terminal_validation_scope == "finite_mask_conditioned"
            else None
        ),
        native_strict_invalidity_preserved=True,
        domain_sufficiency_or_real_world_safety_proven=False,
        minimum_area_proven_in_registered_domain=value["global_minimum_area_proven"],
        minimum_area_proven_in_registered_domain_within_tolerance=value[
            "global_minimum_area_proven_within_tolerance"
        ],
        global_minimum_area_proven=value["global_minimum_area_proven"] and all_ground,
        global_minimum_area_proven_within_tolerance=value[
            "global_minimum_area_proven_within_tolerance"
        ]
        and all_ground,
        full_joint_action_space_registered=all_ground,
        query_slot_count=value["budget"]["started_checks"],
        completed_native_CA_checks=None
        if cache is None
        else sum(
            c.evidence.get("new_CA_checks") == 1
            for key, c in cache.items()
            if key[0] == oracle.problem_sha256
        ),
        native_CA_count_unknown_receipts=None
        if cache is None
        else sum(
            c.evidence.get("native_CA_started") == "unknown"
            for key, c in cache.items()
            if key[0] == oracle.problem_sha256
        ),
        global_pareto_frontier_complete=False,
    )
    atomic_json(output / "search.json", value)
    return value


def resume(config_path, output):
    snapshot = output / "source-snapshot/scripts" / Path(__file__).name
    if Path(__file__).resolve() != snapshot.resolve():
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
    experiment, config = read_json(output / "experiment.json"), read_json(config_path)
    validate_config(config)
    if config != experiment["config"] or file_sha256(config_path) != experiment["config_sha256"]:
        raise ValueError("joint master frozen configuration changed")
    for relative, digest in experiment["source_sha256"].items():
        if file_sha256(output / "source-snapshot" / relative) != digest:
            raise ValueError("joint master frozen source changed")
    for path, digest in experiment["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("joint master frozen physical input changed")
    oracle, _ = make_oracle(config_path, config, content_hash(experiment["source_sha256"]))
    if oracle.problem_sha256 != experiment["problem_sha256"]:
        raise ValueError("joint master physical problem differs after restart")
    if (
        content_hash(oracle.native_problem) != content_hash(experiment["native_problem"])
        or oracle.native_problem_sha256 != experiment["native_problem_sha256"]
        or content_hash(oracle.boundary_scope_problem)
        != content_hash(experiment["boundary_scope_problem"])
        or oracle.terminal_validation_scope != experiment["terminal_validation_scope"]
    ):
        raise ValueError("joint master terminal criterion/native predecessor differs")
    oracle.expected_partitions = experiment["scenario_partitions"]
    cache = {}
    for folder in (
        sorted((output / "evaluations").glob("*")) if (output / "evaluations").exists() else ()
    ):
        if not (folder / "complete.json").is_file():
            continue
        manifest = read_json(folder / "complete.json")
        ids, scenario = tuple(manifest["action_ids"]), manifest["scenario_id"]
        cache[oracle.problem_sha256, ids, scenario] = read_complete(folder, oracle, ids, scenario)
    oracle.on_evaluation = lambda ids, scenario, *native: persist(
        output, oracle, config, ids, scenario, *native
    )
    budget = controller.ExecutionBudget(output / "execution-budget.json")
    loop_config = {
        **config,
        "seed_plans": [
            {"demolished_ids": row["action_ids"]} for row in config.get("seed_action_plans", [])
        ],
    }

    def report(value):
        publish_report(output, oracle, value, cache=cache)

    def evaluate(actions, scenario):
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("joint master declared free-disk floor reached")
        try:
            check = oracle(actions, scenario)
        except MemoryError as error:
            check = ProtectionCheck(
                "unknown",
                {
                    "reason": "computation_resource_limit",
                    "detail": str(error),
                    "new_CA_checks": None,
                    "native_CA_started": "unknown",
                    "problem_sha256": oracle.problem_sha256,
                    "invalid_reasons": [],
                },
            )
        if not folder_for(output, actions, scenario).exists():
            if check.status != "unknown":
                raise ValueError("a physical success/failure returned without native persistence")
            persist_unknown(output, oracle, actions, scenario, check)
        return check

    try:
        value = controller.run_master_loop(
            loop_config,
            candidate_ids=oracle.candidate_ids,
            scenario_ids=tuple(oracle.scenarios),
            area=oracle.logical_universe.area,
            problem_sha256=oracle.problem_sha256,
            evaluate=evaluate,
            budget=budget,
            cache=cache,
            master_solver=master_adapter(oracle),
            status_checker=checked_status,
            on_report=report,
        )
        return publish_report(output, oracle, value, cache=cache)
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
    result = (
        prepare(args.config.resolve(), args.output.resolve())
        if args.prepare_only
        else resume(args.config.resolve(), args.output.resolve())
    )
    print({key: result[key] for key in ("prepared", "stop_reason", "best_ids") if key in result})


if __name__ == "__main__":
    main()
