"""Compare a few declared bridge plans against saved random-source baselines."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import shutil
import zipfile
from dataclasses import replace
from functools import partial
from pathlib import Path
from time import perf_counter

import numpy as np
from shapely.geometry import shape

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout, roof_area_integrated_radiation_graph

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def run(config_path, output, *, prepare_only=False, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-bridge-manual-plans-1":
        raise ValueError("unsupported bridge manual protocol")
    reference = (config_path.parent / config["reference_dir"]).resolve()
    reference_experiment = json.loads((reference / "experiment.json").read_text())
    source_protocol = json.loads((reference / "source-protocol.json").read_text())
    baseline_id = reference_experiment["config"]["plans"][0]["plan_id"]
    reference_sources = json.loads((reference / "realized-sources.json").read_text())
    reference_sources = {
        plan_id: {row["draw_id"]: row for row in rows}
        for plan_id, rows in reference_sources.items()
    }
    runner = load_runner()
    bundle, transport, inventory = runner.load_study(config_path.parent / config["study_path"])
    parent_sha = request_fingerprint(bundle.request)
    if (
        parent_sha != reference_experiment["parent_case_sha256"]
        or inventory.sha256 != reference_experiment["inventory_sha256"]
    ):
        raise ValueError("saved baseline and current physical case/inventory differ")
    old_config = reference_experiment["config"]
    if any(
        config[key] != old_config[key]
        for key in ("roof_scales_m", "quadrature_spacing_m", "horizon_s")
    ):
        raise ValueError("manual comparison must preserve baseline physical protocol")
    plans = [
        BuildingDemolitionPlan(
            **{
                key: tuple(value) if key == "demolished_ids" else value
                for key, value in row.items()
                if key != "draw_ids"
            }
        )
        for row in config["plans"]
    ]
    required = set(config.get("required_demolished_ids", []))
    if any(not required <= set(plan.demolished_ids) for plan in plans):
        raise ValueError("manual plans must retain every required demolition")
    comparison_id = config.get("comparison_plan_id", baseline_id)
    if comparison_id not in reference_sources:
        raise ValueError("comparison plan is absent from the saved reference")
    realizations = {
        plan.plan_id: {
            r["draw_id"]: r
            for r in realize_sources(source_protocol, inventory, demolished_ids=plan.demolished_ids)
        }
        for plan in plans
    }
    matrix = [
        {
            "plan_id": plan.plan_id,
            "draw_id": draw_id,
            "scale_m": scale,
            "source_id": realizations[plan.plan_id][draw_id]["source_id"],
            "source_replaced": realizations[plan.plan_id][draw_id]["source_replaced"],
        }
        for plan, row in zip(plans, config["plans"], strict=True)
        for draw_id in row["draw_ids"]
        for scale in config["roof_scales_m"]
    ]
    if any(row["source_id"] is None for row in matrix):
        raise ValueError("focused manual comparison requires a real retained source in every draw")
    source_paths = [
        Path(__file__),
        Path(__file__).with_name("run_heritage_roof_study.py"),
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
    ]
    source_text = {str(p.relative_to(ROOT)): p.read_text() for p in source_paths}
    experiment = {
        "config": config,
        "matrix": matrix,
        "parent_case_sha256": parent_sha,
        "inventory_sha256": inventory.sha256,
        "reference_experiment_sha256": content_hash(reference_experiment),
        "source_protocol_sha256": source_protocol["protocol_sha256"],
        "mechanism_evidence_sha256": content_hash(
            json.loads((config_path.parent / config["evidence_path"]).read_text())
        ),
        "source_sha256": {key: content_hash(value) for key, value in source_text.items()},
        "optimization": False,
    }
    signature = content_hash(experiment)
    if resume:
        if content_hash(json.loads((output / "experiment.json").read_text())) != signature:
            raise ValueError("manual experiment changed; use a new output directory")
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(output / "experiment.json", experiment)
        dump(
            output / "realized-sources.json",
            {
                key: [
                    {**r, "footprint": r["footprint"].to_mapping() if r["footprint"] else None}
                    for r in rows.values()
                ]
                for key, rows in realizations.items()
            },
        )
        for relative, text in source_text.items():
            destination = output / "source-snapshot" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
    if prepare_only:
        print(json.dumps({"planned_runs": len(matrix), "matrix": matrix}), flush=True)
        return experiment
    base = resolve_roof_layout(bundle.request, inventory)
    core_ids = tuple(target.building_id for target in inventory.targets)
    plan_lookup = {plan.plan_id: plan for plan in plans}
    rows = []
    for case in matrix:
        run_id = f"{case['draw_id']}-h{case['scale_m']}".replace(".", "p")
        folder = output / run_id / case["plan_id"]
        if resume and (folder / "run-summary.json").exists():
            rows.append(json.loads((folder / "run-summary.json").read_text()))
            print(f"Reusing {run_id}/{case['plan_id']}", flush=True)
            continue
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("insufficient disk for remaining manual cases")
        reference_footprints = {}
        for sources in reference_sources.values():
            row = sources[case["draw_id"]]
            if row["footprint"] is not None:
                footprint = row["footprint"]
                reference_footprints[row["source_id"]] = RoofIgnitionFootprint(
                    footprint["source_id"], shape(footprint["geometry"]), footprint["provenance"]
                )
        realized = realizations[case["plan_id"]][case["draw_id"]]
        saved_folder = reference / run_id / baseline_id
        saved_record = json.loads((saved_folder / "evaluation.json").read_text())
        comparison = json.loads(
            (reference / run_id / comparison_id / "evaluation.json").read_text()
        )
        old_ids = saved_record["diagnostics"]["roof_resolution_protocol"]["refined_ids"]
        baseline_layout = refine_roof_layout(
            base,
            inventory,
            cell_size_m=case["scale_m"],
            refined_ids=old_ids,
            footprints=tuple(reference_footprints.values()),
            max_nodes=config["max_nodes"],
        )
        if baseline_layout.sha256 != saved_record["diagnostics"]["layout_sha256"]:
            raise ValueError("cannot reuse saved baseline: physical roof layout differs")
        if case["source_id"] not in reference_footprints:
            integrated_ids = sorted(set(old_ids) | {case["source_id"]})
            layout = refine_roof_layout(
                base,
                inventory,
                cell_size_m=case["scale_m"],
                refined_ids=integrated_ids,
                footprints=(*reference_footprints.values(), realized["footprint"]),
                max_nodes=config["max_nodes"],
            )
        else:
            layout, integrated_ids = baseline_layout, old_ids
            if realized["footprint"].sha256 != reference_footprints[case["source_id"]].sha256:
                raise ValueError("same-source manual plan changed its ignition polygon")
        ignition_layout = replace(
            layout,
            ignition_owner=tuple(
                owner if owner == case["source_id"] else None for owner in layout.ignition_owner
            ),
        )
        scenario = EvaluationScenario(
            run_id, footprint_ignitions(ignition_layout), config["horizon_s"]
        )
        engine = RoofHeritageEvaluationEngine(
            bundle,
            transport,
            inventory,
            source_policy="sampled_retained_sources",
            prepared_layout=layout,
            contact_builder=refined_contact_operator,
            radiation_builder=partial(
                roof_area_integrated_radiation_graph,
                integrated_ids=integrated_ids,
                quadrature_spacing_m=config["quadrature_spacing_m"],
            ),
            code_version=signature,
            max_radiation_edges=config["max_radiation_edges"],
        )
        print(f"Starting {run_id}/{case['plan_id']}: source {case['source_id']}", flush=True)
        trace = runner.SelectedTrace(layout, tuple(sorted(set(core_ids) | {case["source_id"]})))
        started = perf_counter()
        record, resolved, result = engine.simulate(
            scenario, plan_lookup[case["plan_id"]], snapshot_sink=trace
        )
        direct = [event.col for event in resolved.node_request.scenario.ignitions]
        budget = {
            name: float(getattr(resolved.thermal.parameters, name).ravel()[direct].sum())
            for name in (
                "covered_area_m2",
                "initial_dry_fuel_mass_kg",
                "water_mass_kg",
                "available_energy_J",
                "peak_hrr_W",
            )
        }
        if not case["source_replaced"]:
            original_budget = saved_record["diagnostics"]["prescribed_source_budget"]
            for key, value in budget.items():
                np.testing.assert_allclose(value, original_budget[key], rtol=1e-8, atol=1e-6)
        same_comparison_source = comparison["metrics"]["actual_source_ids"] == [case["source_id"]]
        if same_comparison_source:
            for key, value in budget.items():
                np.testing.assert_allclose(
                    value,
                    comparison["diagnostics"]["prescribed_source_budget"][key],
                    rtol=1e-8,
                    atol=1e-6,
                )
        record.diagnostics.update(
            source_protocol_sha256=source_protocol["protocol_sha256"],
            source_replaced=case["source_replaced"],
            prescribed_source_budget=budget,
            prescribed_ignition_geometry_sha256=realized["footprint"].sha256,
            reused_baseline_evaluation_sha256=saved_record["evaluation_sha256"],
            matched_source_and_layout=not case["source_replaced"],
        )
        runner.save_evaluation(folder, record, resolved, result, refined_contact_operator(resolved))
        trace_data = {"node_ids": trace.nodes.tolist(), "frames": trace.frames}
        if config.get("archive_native_bundle", False):
            native_dir = folder / "node-storage-not-map"
            archive = shutil.make_archive(
                str(native_dir), "zip", root_dir=folder, base_dir=native_dir.name
            )
            with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as saved:
                if config.get("archive_layout", False):
                    saved.write(folder / "layout.json", arcname="layout.json")
                if saved.testzip() is not None:
                    raise ValueError("native bundle archive failed its CRC check")
            shutil.rmtree(native_dir)
            if config.get("archive_layout", False):
                (folder / "layout.json").unlink()
            with gzip.open(folder / "selected-trace.json.gz", "wt", encoding="utf-8") as f:
                json.dump(trace_data, f, ensure_ascii=False, allow_nan=False)
        else:
            dump(folder / "selected-trace.json", trace_data)
        row = {
            **case,
            "complete": record.diagnostics["reached_requested_horizon"],
            "invalid_reasons": record.diagnostics["invalid_reasons"],
            "source_verified": record.diagnostics["source_verified"],
            "protection_status": record.metrics["protection_status"],
            "core_outcomes": record.metrics["core_outcomes"],
            "intervention": record.intervention,
            "source_budget": budget,
            "footprint_sha256": realized["footprint"].sha256,
            "baseline_core_outcomes": saved_record["metrics"]["core_outcomes"],
            "baseline_evaluation_sha256": saved_record["evaluation_sha256"],
            "comparison_plan_id": comparison_id,
            "comparison_core_outcomes": comparison["metrics"]["core_outcomes"],
            "comparison_evaluation_sha256": comparison["evaluation_sha256"],
            "same_source_as_comparison": same_comparison_source,
            "energy_error_J": max(record.diagnostics["energy_closure_errors_J"].values()),
            "energy_tolerance_J": record.diagnostics["energy_tolerance_J"],
            "elapsed_s": perf_counter() - started,
        }
        dump(folder / "run-summary.json", row)
        rows.append(row)
        dump(
            output / "summary.json",
            {
                "schema_version": "heritage-bridge-manual-results-1",
                "complete": len(rows) == len(matrix),
                "runs": rows,
                "planned_runs": len(matrix),
                "optimization": False,
            },
        )
        print(
            f"  complete: {row['protection_status']}; cores={record.metrics['ignited_core_ids']}",
            flush=True,
        )
    if request_fingerprint(bundle.request) != parent_sha:
        raise AssertionError("manual experiment mutated the parent case")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, prepare_only=args.prepare_only, resume=args.resume)
