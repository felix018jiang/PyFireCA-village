"""Recount original physical roof owners and summarize a frozen area master.

Only saved complete evaluations are read. No archived file is modified and no
CA is run. The output remains a phase-horizon archive unless native terminal
verification is supplied by the terminal comparison workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.fire_loss import recover_original_node_owners, summarize_fire_loss
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.pareto import summarize_evaluated_pareto
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study


def enrich_saved_loss(folder, inventory, parent_request, manifest):
    """Add the new count block, requiring all native physical areas to match."""
    native = read_json(folder / "fire-loss.json")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        cells = saved["physical_cell_id"]
        offsets, ids = saved["owner_offsets"], saved["owner_ids"]
        if (
            len(offsets) != len(cells) + 1
            or offsets[0] != 0
            or offsets[-1] != len(ids)
            or np.any(np.diff(offsets) < 0)
        ):
            raise ValueError("invalid archived owner index")
        layout = SimpleNamespace(
            owners=tuple(tuple(ids[a:b]) for a, b in pairwise(offsets)),
            component_id=saved["component_id"],
            cell_id=cells,
            xy_m=saved["xy_m"],
        )
        original, audit = recover_original_node_owners(
            layout, inventory, demolished_ids=manifest["demolished_ids"]
        )
        if audit["methods_node_count"]["geometry"] or audit["methods_node_count"]["nonroof"]:
            raise ValueError("immutable original-owner components are required for recount")
        saved_post_area = saved["covered_area_m2"]
    with np.load(folder / "loss-basis.npz", allow_pickle=False) as basis:
        area = basis["baseline_covered_area_m2"].ravel()
        post = basis["post_intervention_covered_area_m2"].ravel()
        if not np.array_equal(post, saved_post_area):
            raise ValueError("layout and native loss basis differ")
        cleared = (area > 0) & (post == 0)
        if not np.allclose(area[~cleared], post[~cleared], rtol=1e-9, atol=1e-8):
            raise ValueError("whole-node clearing contract is broken")
    record = read_json(folder / "evaluation.json")
    with np.load(folder / "final-state.npz", allow_pickle=False) as state:
        time = float(state["time_s"])
        if time != record["diagnostics"]["simulated_until_s"]:
            raise ValueError("native event clocks and evaluation endpoint differ")
        recalculated = summarize_fire_loss(
            covered_area_m2=area,
            node_owners=original,
            ignition_time_s=state["ignition_time_s"],
            burnout_time_s=state["burnout_time_s"],
            observation_time_s=time,
            final_time_s=time,
            inventory_ids=inventory.by_id.keys(),
            demolished_ids=manifest["demolished_ids"],
            source_ids=record["metrics"]["actual_source_ids"],
            cleared_node_mask=cleared,
            known_node_mask=parent_request.known_mask.ravel()[cells],
        )
    for domain in ("all", "roof", "nonroof"):
        for metric, value in recalculated["physical"][domain].items():
            if not np.isclose(value, native["physical"][domain][metric], rtol=1e-12, atol=1e-8):
                raise ValueError(f"recount changed native physical loss: {domain}/{metric}")
    native["physical_remaining_roof_annotation_counts"] = recalculated[
        "physical_remaining_roof_annotation_counts"
    ]
    native["ownership_reconstruction"] = audit
    return record, native, audit


def recount_master(run, *, config_path=None):
    run = Path(run).resolve()
    root = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        *(
            root / "src/pyfireca/planning" / name
            for name in (
                "fire_loss.py",
                "pareto.py",
                "terminal_verification.py",
                "study_io.py",
                "strategy_io.py",
            )
        ),
    ]
    source_hashes = {str(path): file_sha256(path) for path in sources}
    experiment_bytes = (run / "experiment.json").read_bytes()
    experiment = json.loads(experiment_bytes)
    if experiment["schema_version"] != "heritage-area-master-config-1":
        raise ValueError("an area-master run is required")
    for path, digest in experiment["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError(f"frozen physical input changed: {path}")
    problem, config = experiment["problem"], experiment["config"]
    if content_hash(problem) != experiment["problem_sha256"]:
        raise ValueError("frozen problem metadata fingerprint differs")
    for config_key, problem_key in (
        ("horizon_s", "horizon_s"),
        ("roof_scales_m", "scales_m"),
        ("quadrature_spacing_m", "quadrature_spacing_m"),
        ("refinement_ids", "refinement_seed_ids"),
    ):
        if config[config_key] != problem[problem_key]:
            raise ValueError("frozen configuration and physical protocol differ: " + config_key)
    if config.get("core_overlap_policy", "reject") != problem["core_overlap_policy"]:
        raise ValueError("frozen core overlap policies differ")
    retained = config.get("source_policy", "fixed") == "area_bounded_retained_sources"
    coverage = problem.get("source_area_coverage")
    if retained:
        if (
            not isinstance(coverage, dict)
            or coverage["maximum_cleared_area_m2"] != config["source_area_limit_m2"]
            or coverage["area_bound_tolerance_m2"] != config.get("source_area_tolerance_m2", 0)
        ):
            raise ValueError("frozen retained-source area domain differs")
    elif config.get("source_policy", "fixed") != "fixed" or coverage is not None:
        raise ValueError("source policy and frozen coverage proof differ")
    if {f["fire_id"] for f in config["source_fires"]} != set(problem["fires"]) or problem[
        "source_policy"
    ] != "sampled_retained_sources":
        raise ValueError("source families differ from the physical problem")
    for fire in config["source_fires"]:
        draw = problem["fires"][fire["fire_id"]]["protocol"]["draws"]
        if (
            len(draw) != 1
            or draw[0]["draw_id"] != fire["draw_id"]
            or (not retained and len(draw[0]["priority_ids"]) != 1)
        ):
            raise ValueError("frozen source draw or replacement rule differs")
    study_path = Path(config["study_path"])
    if config_path is not None:
        config_path = Path(config_path).resolve()
        if (
            file_sha256(config_path) != experiment["config_sha256"]
            or read_json(config_path) != config
        ):
            raise ValueError("original configuration file changed")
        study_path = (config_path.parent / study_path).resolve()
    elif not study_path.is_absolute():
        raise ValueError("relative study_path requires --config with the original configuration")
    bundle, _, inventory = load_study(study_path)
    saved_inventory = read_json(run / "inventory.json")
    inventory = replace(
        inventory,
        candidate_ids=tuple(saved_inventory["candidate_ids"]),
        protected_ids=tuple(saved_inventory["protected_ids"]),
        fixed_source_ids=tuple(saved_inventory["fixed_source_ids"]),
        candidate_evidence=saved_inventory["candidate_evidence"],
    )
    parent = replace(
        bundle.request,
        scenario=replace(bundle.request.scenario, max_dt_s=float(config["max_dt_s"])),
    )
    if (
        inventory.sha256 != problem["inventory_sha256"]
        or content_hash(saved_inventory) != inventory.sha256
        or request_fingerprint(parent) != problem["case_sha256"]
    ):
        raise ValueError("loaded parent or inventory differs from the frozen master")
    scenario_ids = sorted(
        f"{fire['fire_id']}-h{scale:g}"
        for fire in config["source_fires"]
        for scale in config["roof_scales_m"]
    )
    protocol = {
        "inventory_sha256": inventory.sha256,
        "physical_protocol_sha256": experiment["problem_sha256"],
        "protected_core_ids": sorted(t.building_id for t in inventory.targets),
        "scenario_ids": scenario_ids,
        "horizon_s": config["horizon_s"],
        "roof_scales_m": config["roof_scales_m"],
        "quadrature_spacing_m": config["quadrature_spacing_m"],
        "max_dt_s": config["max_dt_s"],
        "source_policy": config.get("source_policy", "fixed"),
    }
    plans, receipts = {}, []
    # A directory becomes visible only after complete.json has been written
    # and its pending directory atomically renamed by the producer.
    manifests = sorted((run / "evaluations").glob("*/complete.json"))
    for path in manifests:
        if path.parent.name.endswith(".pending"):
            continue
        manifest = read_json(path)
        if manifest["problem_sha256"] != experiment["problem_sha256"]:
            raise ValueError("evaluation belongs to a different problem")
        required = {
            "evaluation.json",
            "fire-loss.json",
            "check.json",
            "layout.npz",
            "final-state.npz",
            "loss-basis.npz",
        }
        if not required <= manifest["files_sha256"].keys():
            raise ValueError("native loss artifacts are missing")
        for name, digest in manifest["files_sha256"].items():
            if Path(name).name != name or file_sha256(path.parent / name) != digest:
                raise ValueError("evaluation artifact is changed or outside its folder")
        ids, scenario = tuple(sorted(manifest["demolished_ids"])), manifest["scenario_id"]
        if (
            path.parent.name != content_hash([list(ids), scenario])
            or not set(ids) <= set(inventory.candidate_ids)
            or scenario not in scenario_ids
        ):
            raise ValueError("native plan or scenario identity differs")
        check = read_json(path.parent / "check.json")
        if (
            check["problem_sha256"] != experiment["problem_sha256"]
            or check["demolished_ids"] != list(ids)
            or check["scenario_id"] != scenario
        ):
            raise ValueError("check differs from the completed evaluation identity")
        record, loss, audit = enrich_saved_loss(path.parent, inventory, parent, manifest)
        area = record["intervention"]["cleared_roof_union_area_m2"]
        plan = plans.setdefault(
            ids,
            {
                "plan_id": "roof-" + content_hash(list(ids))[:12],
                "problem_sha256": experiment["problem_sha256"],
                "comparison_protocol": protocol,
                "temporal_mode": "phase_horizon",
                "cleared_area_m2": area,
                "construction_area_basis": "geometric_action_union_m2",
                "demolished_ids": list(ids),
                "cleared_physical_cell_ids": [],
                "scenario_evaluations": {},
                "provenance_references": [],
            },
        )
        if not np.isclose(area, plan["cleared_area_m2"], rtol=1e-9, atol=1e-7):
            raise ValueError("the same plan has different construction geometry")
        if scenario in plan["scenario_evaluations"]:
            raise ValueError("duplicate physical plan/scenario evaluation")
        plan["scenario_evaluations"][scenario] = {
            "problem_sha256": experiment["problem_sha256"],
            "artifact_manifest_verified": True,
            "check": check,
            "fire_loss": loss,
        }
        plan["provenance_references"].append(str(path))
        receipts.append(
            {
                "manifest_path": str(path),
                "manifest_sha256": file_sha256(path),
                "ownership_reconstruction": audit,
                "physical_remaining_roof_annotation_counts": loss[
                    "physical_remaining_roof_annotation_counts"
                ],
            }
        )
    report = summarize_evaluated_pareto(list(plans.values()), terminalclosed=False)
    report["recount_provenance"] = {
        "run": str(run),
        "experiment_sha256": hashlib.sha256(experiment_bytes).hexdigest(),
        "native_manifest_count": len(receipts),
        "new_CA_checks": 0,
        "historical_artifacts_modified": False,
        "native_physical_area_ledger_reproduced": True,
        "postprocessor_source_sha256": source_hashes,
        "original_owner_recount_receipts": receipts,
    }
    if any(file_sha256(path) != digest for path, digest in source_hashes.items()):
        raise ValueError("postprocessor source changed during recount; retry without changing CA")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = recount_master(args.run, config_path=args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(
        {
            "output": str(args.output),
            "new_CA_checks": 0,
            "feasible": report["complete_feasible_plan_count"],
            "nondominated": len(report["non_dominated_archive"]),
        }
    )


if __name__ == "__main__":
    main()
