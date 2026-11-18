"""Read-only C/N/A frontier from one frozen terminal joint campaign; zero CA.

Native cache checks run in a fresh interpreter using that campaign's verified
source snapshot. Current pure Pareto code performs the separate analysis step.
Original archives, their budgets and their source snapshots are never written.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text())


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def verify_header(run_root, config_path):
    experiment_path = run_root / "experiment.json"
    experiment = read_json(experiment_path)
    config = read_json(config_path)
    if (
        experiment.get("schema_version") != "heritage-joint-comparison-1"
        or config != experiment["config"]
        or file_sha256(config_path) != experiment["config_sha256"]
        or experiment["problem_sha256"] != content_hash(experiment["problem"])
    ):
        raise ValueError("joint experiment, original configuration or physical problem differs")
    if config.get("terminal_fire_verification") is not True:
        raise ValueError("terminal archive requires explicit terminal_fire_verification=True")
    temporal = experiment["problem"].get("evaluation_temporal_protocol", {})
    if (
        temporal.get("terminal_horizon_cap_s") != config.get("terminal_horizon_cap_s")
        or temporal.get("reference_horizon_s") != config["horizon_s"]
        or temporal.get("stop_at_extinction") is not True
        or temporal.get("stop_at_first_core_ignition") is not False
    ):
        raise ValueError("terminal and reference temporal protocols differ")
    snapshot = run_root / "source-snapshot"
    sources = experiment["source_sha256"]
    if not sources.keys() >= {
        "scripts/run_heritage_joint_comparison.py",
        "src/pyfireca/planning/terminal_verification.py",
        "src/pyfireca/village/extinction_v3.py",
    }:
        raise ValueError("terminal native verification source snapshot is incomplete")
    for relative, expected in sources.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe frozen source path")
        if file_sha256(snapshot / path) != expected:
            raise ValueError("frozen native source changed: " + relative)
    undeclared = {str(path.relative_to(snapshot)) for path in snapshot.rglob("*.py")} - set(sources)
    if undeclared:
        raise ValueError("source snapshot has unbound executable Python files")
    for path, expected in experiment["references_sha256"].items():
        if file_sha256(path) != expected:
            raise ValueError("frozen physical input or reference changed: " + path)
    return experiment


def native_worker(run_root, config_path, output):
    """Read native states with the original frozen code; never build a fire graph."""
    experiment = verify_header(run_root, config_path)
    snapshot = run_root / "source-snapshot"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import numpy as np
    import run_heritage_joint_comparison as runner

    from pyfireca.planning.area_candidates import build_cleared_area_function
    from pyfireca.planning.contracts import Ignition
    from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
    from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine
    from pyfireca.planning.evaluation import EvaluationScenario
    from pyfireca.planning.fire_loss import recover_original_node_owners
    from pyfireca.planning.roof_thermal import resolve_roof_layout
    from pyfireca.planning.study_io import load_study
    from pyfireca.village.extinction_v3 import (
        extinction_certificate_sha256,
        extinction_observation_fingerprints,
    )
    from pyfireca.village.solver_v3 import V3State

    def forbidden_simulation(*args, **kwargs):
        raise RuntimeError("postprocessing must not invoke a CA simulation")

    RoofHeritageEvaluationEngine.simulate = forbidden_simulation
    config, problem = experiment["config"], experiment["problem"]
    runner.validate_config(config)
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, _, inventory = load_study(study)
    if inventory.sha256 != problem["inventory_sha256"] or bundle.request.grid.cell_size_m != 5:
        raise ValueError("original geometry inventory or 5 m physical grid differs")
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    area = build_cleared_area_function(
        base.owners,
        [geometry.area for geometry in base.geometry],
        area_basis="original_disjoint_roof_geometry;all_owners_removed",
    )
    checker = object.__new__(RoofJointEvaluationEngine)
    checker.bundle, checker.layout = bundle, base
    point = inventory.buildings[0].geometry.representative_point()
    geometry_scenario = EvaluationScenario(
        "postprocessing-geometry-only", (Ignition((point.x, point.y)),), config["horizon_s"]
    )
    core_ids = sorted(target.building_id for target in inventory.targets)
    if not core_ids:
        raise ValueError("protected core identities are required")

    def array_sha(value):
        value = np.ascontiguousarray(value)
        return content_hash(
            {
                "dtype": str(value.dtype),
                "shape": value.shape,
                "bytes": hashlib.sha256(value.tobytes()).hexdigest(),
            }
        )

    finite_domain_sha256 = content_hash(
        {
            "physical_grid": asdict(bundle.request.grid),
            "domain_mask": array_sha(bundle.request.domain_mask),
            "known_mask": array_sha(bundle.request.known_mask),
        }
    )
    finite_catalog = config.get("source_coverage_policy") == "finite_declared_plans"
    if finite_catalog:
        proof = runner.finite_source_catalog_certificate(
            [runner.plan_from_mapping(row) for row in config["plans"]],
            problem["fires"],
            inventory_sha256=inventory.sha256,
            reference_problem_sha256=problem["reference_master_problem_sha256"],
        )
        if problem.get(
            "source_coverage_application"
        ) != "finite_declared_plans" or proof != problem.get("finite_declared_source_catalog"):
            raise ValueError("finite declared source coverage proof changed")
    protocol = {
        "inventory_sha256": inventory.sha256,
        "physical_protocol_sha256": experiment["problem_sha256"],
        "physical_protocol_binding_scope": (
            "full_declared_frozen_problem;no_cross_problem_equivalence"
        ),
        "scenario_ids": sorted({scenario for _, scenario in runner.expected_checks(config)}),
        "horizon_s": config["terminal_horizon_cap_s"],
        "reference_horizon_s": config["horizon_s"],
        "roof_scales_m": config["roof_scales_m"],
        "quadrature_spacing_m": config["quadrature_spacing_m"],
        "max_dt_s": config["max_dt_s"],
        "source_policy": config.get("source_policy", "fixed"),
        "protected_core_ids": core_ids,
    }
    plans, native_checks, area_ledger = {}, [], []
    for row in config["plans"]:
        plan = runner.plan_from_mapping(row)
        if not set(plan.demolished_ids) <= set(inventory.candidate_ids):
            raise ValueError("postprocessing contains an ineligible roof action")
        checker._vegetation_nodes(geometry_scenario, plan)
        roof = area(plan.demolished_ids)
        if (
            not finite_catalog
            and config.get("source_area_limit_m2") is not None
            and roof
            > math.fsum([config["source_area_limit_m2"], config.get("source_area_tolerance_m2", 0)])
        ):
            raise ValueError("roof action exceeds its frozen retained-source coverage cap")
        ground = len(plan.cleared_physical_cell_ids) * bundle.request.grid.cell_area_m2
        cost = math.fsum([roof, ground])
        plans[plan.plan_id] = {
            "plan_id": plan.plan_id,
            "problem_sha256": experiment["problem_sha256"],
            "comparison_protocol": protocol,
            "temporal_mode": "terminalclosed",
            "cleared_area_m2": cost,
            "construction_area_basis": "geometric_action_union_m2",
            "demolished_ids": list(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "scenario_evaluations": {},
            "provenance_references": [],
        }
        area_ledger.append(
            {
                "plan_id": plan.plan_id,
                "canonical_C_m2": cost,
                "roof_geometry_union_m2": roof,
                "whole_ground_footprints_m2": ground,
                "ground_cell_footprint_m2": bundle.request.grid.cell_area_m2,
                "roof_source_area_cap_m2": config.get("source_area_limit_m2"),
                "source_cap_applies_to_roof_only": not finite_catalog,
                "reference_cap_is_an_admissibility_constraint": not finite_catalog,
                "finite_declared_source_certificate_sha256": proof["certificate_sha256"]
                if finite_catalog
                else None,
                "raw_scenario_C": [],
            }
        )
    ledger_by_id = {row["plan_id"]: row for row in area_ledger}
    declared_sources = {
        (row["plan_id"], row["scenario_id"]): row["actual_source_id"]
        for row in experiment["source_realizations"]
    }
    expected_folders = set()
    for plan, scenario in runner.expected_checks(config):
        folder = runner.result_folder(run_root, plan, scenario)
        expected_folders.add(folder.name)
        destination = plans[plan.plan_id]
        if (folder / "complete.json").is_file():
            check = runner.read_complete(
                folder,
                experiment["problem_sha256"],
                plan_id=plan.plan_id,
                scenario_id=scenario,
                demolished_ids=plan.demolished_ids,
                cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
                terminal_required=True,
            )
            valid, _, _, record = runner.terminal_observation_status(folder, check)
            if sorted(c["building_id"] for c in record["metrics"]["core_outcomes"]) != core_ids:
                raise ValueError("saved observation omits a protected core identity")
            ledger = record["intervention"]
            if (
                ledger["plan_id"] != plan.plan_id
                or ledger["demolished_ids"] != list(plan.demolished_ids)
                or ledger["cleared_physical_cell_ids"] != list(plan.cleared_physical_cell_ids)
                or check["evidence"]["source_id"] != declared_sources[(plan.plan_id, scenario)]
                or record["metrics"]["actual_source_ids"]
                != [declared_sources[(plan.plan_id, scenario)]]
            ):
                raise ValueError("saved action geometry or actual source differs from frozen plan")
            with np.load(folder / "layout.npz", allow_pickle=False) as saved:
                cells, shares = saved["physical_cell_id"], saved["area_fraction"]
                offsets, identifiers = saved["owner_offsets"], saved["owner_ids"]
                owners = tuple(
                    tuple(identifiers[offsets[n] : offsets[n + 1]]) for n in range(len(cells))
                )
                identity = SimpleNamespace(
                    owners=owners,
                    component_id=saved["component_id"],
                    cell_id=cells,
                    xy_m=saved["xy_m"],
                )
                original, _ = recover_original_node_owners(
                    identity, inventory, demolished_ids=plan.demolished_ids
                )
                saved_post_area = saved["covered_area_m2"].ravel()
            with np.load(folder / "loss-basis.npz", allow_pickle=False) as saved:
                before = saved["baseline_covered_area_m2"].ravel()
                after = saved["post_intervention_covered_area_m2"].ravel()
            removed = set(plan.demolished_ids)
            roof_clear = np.asarray([bool(group) and set(group) <= removed for group in original])
            ground_clear = np.isin(cells, plan.cleared_physical_cell_ids)
            if (
                any(group and ground_clear[n] for n, group in enumerate(original))
                or not np.array_equal(saved_post_area, after)
                or not np.array_equal(after, np.where(roof_clear | ground_clear, 0, before))
                or not np.allclose(
                    before,
                    bundle.request.parameters.covered_area_m2.ravel()[cells] * shares,
                    rtol=1e-9,
                    atol=1e-7,
                )
            ):
                raise ValueError(
                    "saved physical clearing differs from building-owner and grass IDs"
                )
            loss = read_json(folder / "fire-loss.json")
            compact_loss = {
                key: loss[key]
                for key in (
                    "schema_version",
                    "observation_time_s",
                    "saved_final_time_s",
                    "physical",
                    "fuel",
                )
            }
            count_block = loss["physical_remaining_roof_annotation_counts"]
            compact_loss["physical_remaining_roof_annotation_counts"] = {
                key: count_block[key]
                for key in (
                    "schema_version",
                    "unrepresented_original_ids",
                    "ignited_annotation_attribution_count",
                    "burned_out_annotation_attribution_count",
                )
            }
            terminal = check["evidence"]["terminal_verification"]
            # The native terminal reader deliberately returns False early for
            # strict-invalid summaries. Independently bind even those saved
            # extinction certificates to their actual state/peak observations.
            with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
                values = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
            values["time_s"], values["step_index"] = (
                float(values["time_s"]),
                int(values["step_index"]),
            )
            state = V3State(**values)
            observations = extinction_observation_fingerprints(
                state, record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"]
            )
            certificate = terminal["temporal_closure_certificate"]
            if certificate is not None and (
                certificate.get("certificate_sha256") != extinction_certificate_sha256(certificate)
                or any(certificate[key] != value for key, value in observations.items())
                or certificate.get("actual_stop_time_s") != state.time_s
                or certificate.get("actual_step_index") != state.step_index
            ):
                raise ValueError("native extinction certificate differs from saved state/peak")
            if record["case_sha256"] != problem["case_sha256"]:
                raise ValueError("native observation belongs to a different parent case")
            raw_cost = record["intervention"]["cleared_action_geometry_union_area_m2"]
            if not math.isclose(
                raw_cost, destination["cleared_area_m2"], rel_tol=1e-10, abs_tol=1e-6
            ):
                raise ValueError(
                    "saved action C differs from original geometry plus whole ground cells"
                )
            ledger_by_id[plan.plan_id]["raw_scenario_C"].append(
                {
                    "scenario_id": scenario,
                    "raw_C_m2": raw_cost,
                    "raw_minus_canonical_m2": raw_cost - destination["cleared_area_m2"],
                }
            )
            manifest = folder / "complete.json"
            destination["scenario_evaluations"][scenario] = {
                "problem_sha256": experiment["problem_sha256"],
                "artifact_manifest_verified": True,
                "check": check,
                "terminal_summary": terminal,
                "fire_loss": compact_loss,
                "native_observation": {
                    "folder": str(folder),
                    "manifest_sha256": file_sha256(folder / "complete.json"),
                    "files_sha256": read_json(folder / "complete.json")["files_sha256"],
                    "state_and_peak_fingerprints": observations,
                    "certificate_observation_bound": certificate is not None,
                },
            }
            native_checks.append(
                {
                    "plan_id": plan.plan_id,
                    "scenario_id": scenario,
                    "status": check["status"],
                    "terminal_complete": valid,
                    "actual_stop_time_s": terminal["actual_stop_time_s"],
                    "core_ignition_observed": terminal["core_ignition_observed"],
                    "terminal_observation": terminal["terminal_observation"],
                    "closure_rejection_reasons": terminal["closure_rejection_reasons"],
                    "invalid_reasons": terminal["invalid_reasons"],
                    "metrics": terminal["metrics"],
                    "manifest": str(manifest),
                    "manifest_sha256": file_sha256(manifest),
                    "certificate_observation_bound": certificate is not None,
                }
            )
            destination["provenance_references"].append(native_checks[-1]["manifest"])
        elif (folder / "unknown-manifest.json").is_file():
            check = runner.read_unknown_source(folder, experiment["problem_sha256"], plan, scenario)
            destination["scenario_evaluations"][scenario] = {
                "problem_sha256": experiment["problem_sha256"],
                "artifact_manifest_verified": True,
                "check": check,
                "fire_loss": None,
            }
            native_checks.append(
                {
                    "plan_id": plan.plan_id,
                    "scenario_id": scenario,
                    "status": "unknown",
                    "terminal_complete": False,
                    "reason": "no_retained_source_in_frozen_prefix",
                    "new_CA_checks": 0,
                    "manifest": str(folder / "unknown-manifest.json"),
                    "manifest_sha256": file_sha256(folder / "unknown-manifest.json"),
                }
            )
        else:
            native_checks.append(
                {
                    "plan_id": plan.plan_id,
                    "scenario_id": scenario,
                    "status": "not_evaluated",
                    "terminal_complete": False,
                    "reason": "pending_incomplete_artifacts"
                    if folder.exists() or folder.with_name(folder.name + ".pending").exists()
                    else "no_saved_evaluation",
                }
            )
    evaluations = run_root / "evaluations"
    if evaluations.exists():
        for folder in evaluations.iterdir():
            if folder.name not in expected_folders and (
                (folder / "complete.json").exists() or (folder / "unknown-manifest.json").exists()
            ):
                raise ValueError("archive contains an undeclared plan/scenario evaluation")
    verify_header(run_root, config_path)
    result = {
        "plan_evaluations": list(plans.values()),
        "native_checks": native_checks,
        "canonical_construction_area_ledger": area_ledger,
        "protected_core_ids": core_ids,
        "problem_sha256": experiment["problem_sha256"],
        "parent_case_sha256": problem["case_sha256"],
        "finite_domain_sha256": finite_domain_sha256,
        "reference_joint_3600_problem_sha256": problem["evaluation_temporal_protocol"][
            "reference_problem_sha256"
        ],
        "reference_master_3600_problem_sha256": problem.get("reference_master_problem_sha256"),
        "reference_source_area_coverage": problem.get("source_area_coverage"),
        "new_CA_checks": 0,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))


def summarize(run_root, config_path, output, *, temporal_mode="strict"):
    run_root, config_path, output = map(
        lambda p: Path(p).resolve(), (run_root, config_path, output)
    )
    if output.is_relative_to(run_root):
        raise ValueError("postprocessing output must be outside the original run archive")
    experiment = verify_header(run_root, config_path)
    if temporal_mode not in {"strict", "finite_domain_closed"}:
        raise ValueError("explicit strict or finite_domain_closed temporal mode required")
    analysis_paths = [
        Path(__file__),
        ROOT / "src/pyfireca/planning/pareto.py",
        ROOT / "src/pyfireca/planning/terminal_verification.py",
        ROOT / "src/pyfireca/planning/boundary_scope.py",
        ROOT / "src/pyfireca/village/extinction_v3.py",
    ]
    protected_files = {
        config_path,
        *[Path(path).resolve() for path in experiment["references_sha256"]],
        *[path.resolve() for path in analysis_paths],
    }
    if output in protected_files:
        raise ValueError("postprocessing output cannot overwrite an original input or source file")
    analysis_hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in analysis_paths}
    started = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="pyfireca-terminal-pareto-") as temporary:
        worker_output = Path(temporary) / "verified.json"
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--native-worker",
                "--run-root",
                str(run_root),
                "--config",
                str(config_path),
                "--output",
                str(worker_output),
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(run_root / "source-snapshot/src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise ValueError(
                "frozen native verification failed:\n" + process.stdout + process.stderr
            )
        native = read_json(worker_output)
    sys.path.insert(0, str(ROOT / "src"))
    from pyfireca.planning.pareto import summarize_evaluated_pareto

    analysis_problem = None
    conditional_checks = []
    if temporal_mode == "finite_domain_closed":
        import numpy as np

        from pyfireca.planning.boundary_scope import (
            audit_finite_domain_observation,
            make_boundary_scope_problem,
        )
        from pyfireca.village.solver_v3 import V3State

        analysis_problem = make_boundary_scope_problem(
            native["problem_sha256"],
            parent_case_sha256=native["parent_case_sha256"],
            finite_domain_sha256=native["finite_domain_sha256"],
        )
        for plan in native["plan_evaluations"]:
            plan["problem_sha256"] = analysis_problem["analysis_problem_sha256"]
            plan["temporal_mode"] = "finite_domain_closed"
            plan["comparison_protocol"] = {
                **plan["comparison_protocol"],
                "native_problem_sha256": native["problem_sha256"],
                "finite_domain_sha256": analysis_problem["finite_domain_sha256"],
                "boundary_contract_sha256": analysis_problem["boundary_contract_sha256"],
                "validator_policy_sha256": analysis_problem["validator_policy_sha256"],
                "boundary_scope_validator_sha256": analysis_problem["validator_sha256"],
            }
            for scenario, evaluation in plan["scenario_evaluations"].items():
                evaluation["problem_sha256"] = analysis_problem["analysis_problem_sha256"]
                observation = evaluation.get("native_observation")
                if observation is None:
                    continue
                folder = Path(observation["folder"])
                if file_sha256(folder / "complete.json") != observation["manifest_sha256"]:
                    raise ValueError(
                        "native observation manifest changed after frozen-reader validation"
                    )

                def verified_bytes(name, *, folder=folder, files=observation["files_sha256"]):
                    payload = (folder / name).read_bytes()
                    if hashlib.sha256(payload).hexdigest() != files[name]:
                        raise ValueError(
                            "native observation changed after frozen-reader validation: " + name
                        )
                    return payload

                record = json.loads(verified_bytes("evaluation.json"))
                with np.load(
                    io.BytesIO(verified_bytes("final-state.npz")), allow_pickle=False
                ) as saved:
                    values = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
                values["time_s"], values["step_index"] = (
                    float(values["time_s"]),
                    int(values["step_index"]),
                )
                state = V3State(**values)
                summary = evaluation["terminal_summary"]
                source = summary["model_protocol"].get("source_evidence") or {}
                view = audit_finite_domain_observation(
                    summary,
                    evaluation["check"],
                    record,
                    state,
                    record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"],
                    analysis_problem=analysis_problem,
                    source_evidence=source,
                    artifact_lineage={
                        "native_problem_sha256": native["problem_sha256"],
                        "native_case_sha256": record["case_sha256"],
                        "all_hashes_verified": True,
                        "source_snapshot_sha256": content_hash(experiment["source_sha256"]),
                        "input_manifest_sha256": content_hash(experiment["references_sha256"]),
                        "artifact_manifest_sha256": observation["manifest_sha256"],
                    },
                    fire_loss=evaluation["fire_loss"],
                )
                evaluation["finite_domain_summary"] = view.summary
                conditional_checks.append(
                    {
                        "plan_id": plan["plan_id"],
                        "scenario_id": scenario,
                        "native_strict_status": evaluation["check"]["status"],
                        "conditional_status": view.check.status,
                        "finite_domain_closed": view.summary["finite_domain_closed"],
                        "native_invalid_reasons": view.summary["native_invalid_reasons"],
                        "analysis_summary_sha256": view.summary["analysis_summary_sha256"],
                        "lineage": view.summary["lineage"],
                    }
                )

    anchors = []
    for key, digest_key in (
        ("roof_seed_receipt_path", "roof_seed_receipt_sha256"),
        ("roof_seed_search_snapshot_path", "roof_seed_search_sha256"),
    ):
        if experiment["config"].get(key):
            path = (config_path.parent / experiment["config"][key]).resolve()
            anchors.append(
                {
                    "path": str(path),
                    "sha256": experiment["config"][digest_key],
                    "scope": "original_roof_only_action_domain_and_3600s_reference_source_protocol",
                    "terminal_or_joint_optimality_transferred": False,
                }
            )
    archive = summarize_evaluated_pareto(
        native["plan_evaluations"],
        terminalclosed=True,
        area_anchor_certificates=anchors,
        temporal_mode="finite_domain_closed" if analysis_problem else None,
    )
    if any(file_sha256(ROOT / path) != digest for path, digest in analysis_hashes.items()):
        raise ValueError("analysis implementation changed during postprocessing")
    verify_header(run_root, config_path)
    report = {
        "schema_version": "heritage-joint-terminal-pareto-postprocessing-1",
        "run_root": str(run_root),
        "config_path": str(config_path),
        "experiment_sha256": file_sha256(run_root / "experiment.json"),
        "comparison_problem_sha256": native["problem_sha256"]
        if analysis_problem is None
        else analysis_problem["analysis_problem_sha256"],
        "temporal_mode": temporal_mode,
        "native_comparison_problem_sha256": native["problem_sha256"],
        "analysis_problem": analysis_problem,
        "analysis_problem_sha256": None
        if analysis_problem is None
        else analysis_problem["analysis_problem_sha256"],
        "finite_domain_checks": conditional_checks,
        "reference_joint_3600_problem_sha256": native["reference_joint_3600_problem_sha256"],
        "reference_master_3600_problem_sha256": native["reference_master_3600_problem_sha256"],
        "analysis_source_sha256": analysis_hashes,
        "native_snapshot_source_sha256": experiment["source_sha256"],
        "physical_references_sha256": experiment["references_sha256"],
        "native_saved_state_and_peak_certificates_verified": True,
        "native_checks": native["native_checks"],
        "canonical_construction_area_ledger": native["canonical_construction_area_ledger"],
        "reference_source_area_coverage": native["reference_source_area_coverage"],
        "source_and_area_anchor_scope": {
            "retained_source_cap_applies_to_roof_geometry_only": experiment["config"].get(
                "source_coverage_policy"
            )
            != "finite_declared_plans",
            "grass_action_cost_is_complete_25m2_parent_footprints": True,
            "old_3600_area_optimality_transferred_to_terminal_or_joint": False,
            "horizon_extension_can_change_final_clipped_timestep": True,
            "finite_archive_is_not_globally_complete_pareto_frontier": True,
        },
        "archive": archive,
        "new_CA_checks": 0,
        "original_run_writes": 0,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--temporal-mode", choices=("strict", "finite_domain_closed"), default="strict"
    )
    args = parser.parse_args()
    if args.native_worker:
        native_worker(args.run_root.resolve(), args.config.resolve(), args.output.resolve())
        return
    report = summarize(args.run_root, args.config, args.output, temporal_mode=args.temporal_mode)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "new_CA_checks": 0,
                "feasible_complete_plans": report["archive"]["complete_feasible_plan_count"],
                "frontier_plan_ids": [
                    p["plan_id"] for p in report["archive"]["non_dominated_archive"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
