"""Audit physical equivalence and merge terminal joint catalogs without CA.

Each input is re-read with its original immutable producer snapshot. Catalog
membership and native problem names may differ; physical model inputs, source
families, numerical support and deterministic heat operators must be identical.
Native and conditional summaries keep their own identities throughout.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import summarize_heritage_joint_pareto as single

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-joint-catalog-physical-equivalence-1"
# These affect admission, provenance or bookkeeping, not the already prepared
# finite physical experiment. Every unlisted/new problem field is compared.
ADMINISTRATIVE_PROBLEM_FIELDS = frozenset(
    {
        "code_version",
        "joint_plans",
        "candidate_ids",
        "maximum_demolitions",
        "reference_master_problem_sha256",
        "reference_master_experiment_sha256",
        "finite_declared_source_catalog",
        "source_coverage_application",
        "source_area_coverage",
        "new_independent_fire_draw_count",
    }
)


def physical_problem_projection(problem):
    value = {k: deepcopy(v) for k, v in problem.items() if k not in ADMINISTRATIVE_PROBLEM_FIELDS}
    temporal = value.get("evaluation_temporal_protocol")
    if temporal is not None:
        temporal.pop("reference_problem_sha256", None)
    # The producer iterates the footprint mapping in insertion order when
    # splitting polygons. Preserve that order in addition to canonical hashes.
    for fire in value.get("fires", {}).values():
        fire["ordered_footprint_ids"] = list(fire["footprints"])
    return value


def runner_physical_leaves(path):
    """Bind the actual mesh/ignition builders and terminal invocation AST.

    Full src files must match separately. Differing runner bookkeeping cannot
    conceal a different prepared-layout or ignition path here.
    """
    tree = ast.parse(Path(path).read_text())
    oracle = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JointComparisonOracle"
    )
    methods = {n.name: n for n in oracle.body if isinstance(n, ast.FunctionDef)}
    result = {
        "module_imports": single.content_hash(
            [
                ast.dump(n, include_attributes=False)
                for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))
            ]
        )
    }
    for name in ("engine_for_scenario", "scenario_for"):
        result[name] = single.content_hash(ast.dump(methods[name], include_attributes=False))
    terminal_calls = [
        n
        for n in ast.walk(methods["evaluate"])
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "evaluate_terminal_joint"
    ]
    if len(terminal_calls) != 1:
        raise ValueError("exactly one native terminal invocation is required")
    result["terminal_invocation"] = single.content_hash(
        ast.dump(terminal_calls[0], include_attributes=False)
    )
    super_calls = [
        n
        for n in ast.walk(methods["__init__"])
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "__init__"
        and isinstance(n.func.value, ast.Call)
        and isinstance(n.func.value.func, ast.Name)
        and n.func.value.func.id == "super"
    ]
    if len(super_calls) != 1:
        raise ValueError("exactly one native physical oracle constructor is required")
    result["physical_oracle_constructor"] = single.content_hash(
        ast.dump(super_calls[0], include_attributes=False)
    )
    return result


def native_protocol_worker(run_root, config_path, output):
    """Inspect original arrays and saved partitions, never rebuilding a graph."""
    import numpy as np

    experiment = single.verify_header(run_root, config_path)
    snapshot = run_root / "source-snapshot"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import run_heritage_joint_comparison as producer

    from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
    from pyfireca.planning.engines.village import request_fingerprint
    from pyfireca.planning.fire_loss import recover_original_node_owners
    from pyfireca.planning.study_io import load_study

    def forbidden(*args, **kwargs):
        raise RuntimeError("equivalence audit cannot run CA or construct radiation graphs")

    RoofHeritageEvaluationEngine.simulate = forbidden
    producer.roof_area_integrated_radiation_graph = forbidden
    config, problem = experiment["config"], experiment["problem"]
    producer.validate_config(config)
    bundle, transport, inventory = load_study((config_path.parent / config["study_path"]).resolve())
    parent = replace(
        bundle.request,
        scenario=replace(bundle.request.scenario, max_dt_s=float(config["max_dt_s"])),
    )
    if (
        request_fingerprint(parent) != problem["case_sha256"]
        or inventory.sha256 != problem["inventory_sha256"]
    ):
        raise ValueError("native physical parent case or inventory differs")

    def array_fingerprint(value):
        a = np.ascontiguousarray(value)
        return {
            "dtype": str(a.dtype),
            "shape": list(a.shape),
            "bytes_sha256": hashlib.sha256(a.tobytes()).hexdigest(),
        }

    def fingerprint(value):
        if isinstance(value, np.ndarray):
            return array_fingerprint(value)
        if is_dataclass(value):
            return {f.name: fingerprint(getattr(value, f.name)) for f in fields(value)}
        if isinstance(value, dict):
            return {str(k): fingerprint(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [fingerprint(v) for v in value]
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if hasattr(value, "to_mapping"):
            return fingerprint(value.to_mapping())
        raise TypeError("unsupported native physical input fingerprint")

    protocol = {
        "parent_request_fields": fingerprint(parent),
        "transport_parameters": fingerprint(transport),
        "inventory_sha256": inventory.sha256,
        "protected_core_ids": sorted(t.building_id for t in inventory.targets),
        "native_physical_problem": physical_problem_projection(problem),
        "numerics": {
            k: config.get(k)
            for k in (
                "roof_scales_m",
                "quadrature_spacing_m",
                "max_dt_s",
                "horizon_s",
                "terminal_horizon_cap_s",
            )
        },
        "source_policy": config.get("source_policy", "fixed"),
        "source_sha256": {
            k: v for k, v in experiment["source_sha256"].items() if k.startswith("src/")
        },
        "runner_physical_leaves": runner_physical_leaves(
            snapshot / "scripts/run_heritage_joint_comparison.py"
        ),
    }
    if not protocol["protected_core_ids"]:
        raise ValueError("protected core identities are required")
    baseline_partitions, source_witnesses, artifact_bindings = {}, {}, []
    for plan, scenario in producer.expected_checks(config):
        folder = producer.result_folder(run_root, plan, scenario)
        if not (folder / "complete.json").is_file():
            continue
        check = producer.read_complete(
            folder,
            experiment["problem_sha256"],
            plan_id=plan.plan_id,
            scenario_id=scenario,
            demolished_ids=plan.demolished_ids,
            cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
            terminal_required=True,
        )
        evidence = check["evidence"]
        label, scale = evidence["fire_id"], evidence["scale_m"]
        if scenario != f"{label}-h{scale:g}" or scale not in config["roof_scales_m"]:
            raise ValueError("native family or support identity differs")
        fire = problem["fires"][label]
        source_protocol = fire["protocol"]
        if source_protocol["inventory_sha256"] != inventory.sha256 or source_protocol[
            "protocol_sha256"
        ] != single.content_hash(
            {k: v for k, v in source_protocol.items() if k != "protocol_sha256"}
        ):
            raise ValueError("native registered source protocol or inventory differs")
        draw = source_protocol["draws"]
        if len(draw) != 1:
            raise ValueError("one priority draw per family is required")
        priority = draw[0]["priority_ids"]
        if len(priority) != len(set(priority)) or not set(priority) <= inventory.by_id.keys():
            raise ValueError("source priority contains duplicate identities")
        if config.get("source_policy", "fixed") == "retained_sources" and (
            draw[0].get("stratum") != "all_retained_sources"
            or set(priority) != set(source_protocol["strata"]["all_retained_sources"])
            or draw[0].get("baseline_source_id") != priority[0]
        ):
            raise ValueError("native retained source draw omits legal registered sources")
        actual = next((i for i in priority if i not in plan.demolished_ids), None)
        if actual is None or actual not in fire["footprints"] or actual != evidence["source_id"]:
            raise ValueError("native actual source violates complete first-retained priority")
        footprint = fire["footprints"][actual]
        if evidence["source_footprint_sha256"] != single.content_hash(footprint):
            raise ValueError("native source polygon differs from registered family")
        budget = evidence["source_budget"]
        if any(
            isinstance(budget.get(k), bool)
            or not isinstance(budget.get(k), (int, float))
            or not math.isfinite(budget[k])
            or budget[k] <= 0
            for k in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        ):
            raise ValueError("native source needs a positive finite area/fuel/energy budget")
        if not math.isclose(
            budget["covered_area_m2"], footprint["area_m2"], rel_tol=1e-6, abs_tol=1e-6
        ):
            raise ValueError("native source area budget differs from polygon")
        record = single.read_json(folder / "evaluation.json")
        diagnostic = record["diagnostics"]
        if (
            diagnostic["layout_sha256"] != evidence["common_layout_sha256"]
            or not diagnostic["source_verified"]
            or record["metrics"]["actual_source_ids"] != [actual]
        ):
            raise ValueError("native source or common layout identity differs")
        resolution = diagnostic["roof_resolution_protocol"]
        if (
            resolution["refined_ids"] != sorted(fire["refined_ids"])
            or resolution["cell_size_m"] != scale
            or resolution["phase_fraction"] != [0.0, 0.0]
            or resolution["footprints"] != list(fire["footprints"].values())
        ):
            raise ValueError("native support/phase/refinement/footprint partition differs")
        with np.load(folder / "layout.npz", allow_pickle=False) as saved:
            arrays = {k: saved[k].copy() for k in saved.files}
        owners = tuple(
            tuple(arrays["owner_ids"][arrays["owner_offsets"][n] : arrays["owner_offsets"][n + 1]])
            for n in range(len(arrays["physical_cell_id"]))
        )
        original, recovery = recover_original_node_owners(
            SimpleNamespace(
                owners=owners,
                component_id=arrays["component_id"],
                cell_id=arrays["physical_cell_id"],
                xy_m=arrays["xy_m"],
            ),
            inventory,
            demolished_ids=plan.demolished_ids,
        )
        integrated = np.asarray(
            [bool(set(fire["refined_ids"]).intersection(group)) for group in original], dtype=bool
        )
        integrated_sha = hashlib.sha256(integrated.tobytes()).hexdigest()
        if integrated_sha != evidence["frozen_integrated_node_mask_sha256"]:
            raise ValueError("frozen radiation integration membership differs from original owners")
        with np.load(folder / "loss-basis.npz", allow_pickle=False) as saved:
            baseline = saved["baseline_covered_area_m2"].copy()
        partition = {
            "common_layout_sha256": evidence["common_layout_sha256"],
            "baseline_arrays": {
                k: array_fingerprint(arrays[k])
                for k in (
                    "physical_cell_id",
                    "xy_m",
                    "component_id",
                    "area_fraction",
                    "physical_grid_shape",
                )
            },
            "original_owners_sha256": single.content_hash(original),
            "baseline_covered_area": array_fingerprint(baseline),
            "frozen_integration_mask_sha256": integrated_sha,
        }
        if scenario in baseline_partitions and baseline_partitions[scenario] != partition:
            raise ValueError("plans in one catalog have different baseline physical partitions")
        baseline_partitions[scenario] = partition
        source_key = scenario + ":" + actual
        source = {
            "source_id": actual,
            "source_budget": budget,
            "source_footprint_sha256": evidence["source_footprint_sha256"],
        }
        if source_key in source_witnesses and source_witnesses[source_key] != source:
            raise ValueError("same family/support/source has different physical budgets")
        source_witnesses[source_key] = source
        artifact_bindings.append(
            {
                "plan_id": plan.plan_id,
                "scenario_id": scenario,
                "native_actual_source": source,
                "native_physical_request_sha256": diagnostic["physical_request_sha256"],
                "original_owner_recovery": recovery,
                "manifest_sha256": single.file_sha256(folder / "complete.json"),
            }
        )
    single.verify_header(run_root, config_path)
    result = {
        "protocol": protocol,
        "physical_protocol_sha256": single.content_hash(protocol),
        "baseline_partitions": baseline_partitions,
        "source_witnesses": source_witnesses,
        "artifact_bindings": artifact_bindings,
        "new_CA_checks": 0,
        "radiation_graphs_constructed": 0,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))


def verify_equivalence(audits, *, expected_scenario_count=8):
    if not audits or isinstance(expected_scenario_count, bool) or expected_scenario_count <= 0:
        raise ValueError("nonempty native audits and positive declared scenario count required")
    first = audits[0]
    protocol = first["protocol"]
    for other in audits:
        if other["physical_protocol_sha256"] != single.content_hash(other["protocol"]):
            raise ValueError("physical equivalence audit fingerprint differs")
        if other["protocol"] != protocol:
            different = [
                k
                for k in set(protocol) | set(other["protocol"])
                if protocol.get(k) != other["protocol"].get(k)
            ]
            raise ValueError(
                "physical catalogs are not equivalent: " + ", ".join(sorted(different))
            )
    scenarios = {
        f"{label}-h{scale:g}"
        for label in protocol["native_physical_problem"]["fires"]
        for scale in protocol["numerics"]["roof_scales_m"]
    }
    if len(scenarios) != expected_scenario_count:
        raise ValueError("complete declared source/support scenario count differs")
    partitions, sources = {}, {}
    for audit in audits:
        if not set(audit["baseline_partitions"]) <= scenarios:
            raise ValueError("saved baseline belongs to an undeclared scenario")
        for key, value in audit["baseline_partitions"].items():
            if key in partitions and partitions[key] != value:
                raise ValueError("cross-catalog baseline layout or integration membership differs")
            partitions[key] = value
        for key, value in audit["source_witnesses"].items():
            if key in sources and sources[key] != value:
                raise ValueError("cross-catalog physical source budget or polygon differs")
            sources[key] = value
    return {
        "schema_version": SCHEMA,
        "physical_protocol_sha256": first["physical_protocol_sha256"],
        "physical_protocol": deepcopy(protocol),
        "scenario_ids": sorted(scenarios),
        "verified_saved_baseline_partitions": partitions,
        "source_witnesses": sources,
        "comparison_rule": "identical_native_discrete_model_and_family_first_retained_source_rule",
        "different_plan_masks_and_actual_retained_sources_are_allowed": True,
        "no_new_CA_or_radiation_graph": True,
    }


def merge_verified_archives(reports, receipt):
    """Combine already independently validated rows without rebinding summaries."""
    if not reports:
        raise ValueError("at least one reverified terminal report is required")
    mode = reports[0]["temporal_mode"]
    if mode not in {"strict", "finite_domain_closed"} or any(
        r["temporal_mode"] != mode for r in reports
    ):
        raise ValueError("strict and finite-domain pools cannot be merged")
    feasible, unresolved, failed, lineage = [], [], [], []
    unrepresented = reports[0]["archive"]["unrepresented_original_ids"]
    for index, report in enumerate(reports):
        archive = report["archive"]
        if archive["unrepresented_original_ids"] not in (None, unrepresented):
            if unrepresented is None:
                unrepresented = archive["unrepresented_original_ids"]
            else:
                raise ValueError("unrepresented original-owner scopes differ")
        if archive["comparison_protocol"]["scenario_ids"] != receipt["scenario_ids"]:
            raise ValueError("archive source/support scenarios differ from equivalence receipt")
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
        for field, destination in (
            ("fully_evaluated_feasible_plans", feasible),
            ("unresolved_plans", unresolved),
            ("failed_plans", failed),
        ):
            for value in archive[field]:
                row = deepcopy(value)
                row["native_plan_id"], row["batch_id"] = row["plan_id"], batch
                row["plan_id"] = batch + "::" + row["plan_id"]
                # Dominance is recomputed over the combined validated set.
                for key in (
                    "upper_bound_dominated_by",
                    "robustly_dominated_by",
                    "possibly_dominated_by",
                    "unresolved_potential_dominator_ids",
                ):
                    row.pop(key, None)
                if field == "fully_evaluated_feasible_plans" and (
                    set(row["scenario_observations"]) != set(receipt["scenario_ids"])
                    or row["worst_case"]["A_active_m2"] != 0
                ):
                    raise ValueError(
                        "merged feasible plan lacks every closed source/support observation"
                    )
                destination.append(row)

    def dominates(a, b):
        return all(x <= y for x, y in zip(a, b, strict=True)) and any(
            x < y for x, y in zip(a, b, strict=True)
        )

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
            if dominates(other["objective_values"], row["objective_values"]):
                upper.append(other["plan_id"])
            if dominates(other["objective_values"], lo):
                robust.append(other["plan_id"])
            if dominates(other_lo, row["objective_values"]):
                possible.append(other["plan_id"])
        row.update(
            upper_bound_dominated_by=sorted(upper),
            robustly_dominated_by=sorted(robust),
            possibly_dominated_by=sorted(possible),
            unresolved_potential_dominator_ids=sorted(
                r["plan_id"] for r in unresolved if r["cleared_area_m2"] <= row["cleared_area_m2"]
            ),
        )
    feasible.sort(key=lambda row: (row["objective_values"], row["plan_id"]))
    return {
        "schema_version": "heritage-equivalent-catalog-evaluated-pareto-1",
        "temporal_mode": mode,
        "lineage": lineage,
        "objective_names": reports[0]["archive"]["objective_names"],
        "objective_dominance_tolerance": 0,
        "loss_aggregation": "componentwise_maximum_over_every_declared_scenario",
        "unrepresented_original_ids": unrepresented,
        "non_dominated_archive": [r for r in feasible if not r["upper_bound_dominated_by"]],
        "robust_non_dominated_archive": [r for r in feasible if not r["robustly_dominated_by"]],
        "possible_dominance_free_archive": [r for r in feasible if not r["possibly_dominated_by"]],
        "fully_evaluated_feasible_plans": feasible,
        "unresolved_plans": unresolved,
        "failed_plans": failed,
        "complete_feasible_plan_count": len(feasible),
        "global_pareto_frontier_complete": False,
        "global_minimum_area_proven": False,
        "native_summaries_and_problem_identities_modified": False,
        "frontier_scope": "only_fully_evaluated_feasible_plans_in_explicitly_equivalent_catalogs",
        "new_CA_checks": 0,
    }


def merge_catalogs(catalogs, output, *, temporal_mode="strict", expected_scenario_count=8):
    output = Path(output).resolve()
    catalogs = [
        {"run_root": Path(c["run_root"]).resolve(), "config": Path(c["config"]).resolve()}
        for c in catalogs
    ]
    sources = {
        str(p): single.file_sha256(p)
        for p in (Path(__file__).resolve(), Path(single.__file__).resolve())
    }
    for item in catalogs:
        experiment = single.verify_header(item["run_root"], item["config"])
        if output.is_relative_to(item["run_root"]) or output in {
            item["config"],
            *map(lambda p: Path(p).resolve(), experiment["references_sha256"]),
            *map(Path, sources),
        }:
            raise ValueError(
                "merge output cannot overwrite an original archive, input or analysis source"
            )
    started, reports, audits = datetime.now(UTC).isoformat(), [], []
    with tempfile.TemporaryDirectory(prefix="pyfireca-physical-equivalence-") as temporary:
        for index, item in enumerate(catalogs):
            reports.append(
                single.summarize(
                    item["run_root"],
                    item["config"],
                    Path(temporary) / f"report{index}.json",
                    temporal_mode=temporal_mode,
                )
            )
            target = Path(temporary) / f"audit{index}.json"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--native-protocol-worker",
                "--run-root",
                str(item["run_root"]),
                "--config",
                str(item["config"]),
                "--output",
                str(target),
            ]
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(item["run_root"] / "source-snapshot/src"),
                },
            )
            if process.returncode:
                raise ValueError(
                    "native physical equivalence audit failed:\n" + process.stdout + process.stderr
                )
            audits.append(single.read_json(target))
    receipt = verify_equivalence(audits, expected_scenario_count=expected_scenario_count)
    receipt["temporal_mode"] = temporal_mode
    receipt["native_problem_sha256s"] = sorted(
        {r["native_comparison_problem_sha256"] for r in reports}
    )
    receipt["analysis_problem_sha256s"] = sorted(
        {r["analysis_problem_sha256"] for r in reports if r["analysis_problem_sha256"]}
    )
    receipt["validator_source_sha256"] = sources
    receipt["certificate_sha256"] = single.content_hash(receipt)
    archive = merge_verified_archives(reports, receipt)
    for item, report in zip(catalogs, reports, strict=True):
        single.verify_header(item["run_root"], item["config"])
        for observed in report["native_checks"]:
            if (
                observed.get("manifest")
                and single.file_sha256(observed["manifest"]) != observed["manifest_sha256"]
            ):
                raise ValueError("native manifest changed during equivalence comparison")
            if observed.get("manifest"):
                manifest = Path(observed["manifest"])
                for name, digest in single.read_json(manifest).get("files_sha256", {}).items():
                    if single.file_sha256(manifest.parent / name) != digest:
                        raise ValueError("native artifact changed during equivalence comparison")
    if any(single.file_sha256(p) != sha for p, sha in sources.items()):
        raise ValueError("equivalence implementation changed during analysis")
    result = {
        "schema_version": "heritage-joint-catalog-merge-receipt-1",
        "temporal_mode": temporal_mode,
        "comparison_analysis_problem_sha256": receipt["certificate_sha256"],
        "physical_equivalence_certificate": receipt,
        "independently_reverified_native_reports": reports,
        "native_partition_and_source_audits": audits,
        "archive": archive,
        "new_CA_checks": 0,
        "original_run_writes": 0,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
    }
    result["merge_receipt_sha256"] = single.content_hash(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogs", type=Path, help="JSON list of {run_root,config}; relative to this file"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--temporal-mode", choices=("strict", "finite_domain_closed"), default="strict"
    )
    parser.add_argument("--expected-scenario-count", type=int, default=8)
    parser.add_argument("--native-protocol-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.native_protocol_worker:
        native_protocol_worker(
            args.run_root.resolve(), args.config.resolve(), args.output.resolve()
        )
        return
    if args.catalogs is None:
        parser.error("--catalogs is required")
    if args.output.resolve() == args.catalogs.resolve():
        parser.error("merge output cannot overwrite its catalog registration")
    rows = single.read_json(args.catalogs)
    catalogs = [
        {
            k: str((args.catalogs.resolve().parent / row[k]).resolve())
            for k in ("run_root", "config")
        }
        for row in rows
    ]
    report = merge_catalogs(
        catalogs,
        args.output,
        temporal_mode=args.temporal_mode,
        expected_scenario_count=args.expected_scenario_count,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "new_CA_checks": 0,
                "complete_feasible_plans": report["archive"]["complete_feasible_plan_count"],
                "frontier_plan_ids": [
                    r["plan_id"] for r in report["archive"]["non_dominated_archive"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
