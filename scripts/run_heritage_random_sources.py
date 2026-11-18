"""Freeze random building fires and compare explicit heritage demolition plans."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import shutil
import zipfile
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from time import perf_counter

import numpy as np

from pyfireca.planning.engines.heritage import compare_retained_ordinary
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import (
    freeze_source_protocol,
    realize_sources,
    summarize_protection,
)
from pyfireca.planning.roof_ignition import (
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import (
    resolve_roof_layout,
    roof_area_integrated_radiation_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_row(row):
    return {**row, "footprint": row["footprint"].to_mapping() if row["footprint"] else None}


def run(config_path, output, *, prepare_only=False, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-random-source-pilot-1":
        raise ValueError("unsupported random source pilot schema")
    runner = load_runner()
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = runner.load_study(study_path)
    baseline_hash, inventory_hash = request_fingerprint(bundle.request), inventory.sha256
    plans = [
        BuildingDemolitionPlan(**{**row, "demolished_ids": tuple(row["demolished_ids"])})
        for row in config["plans"]
    ]
    if len(plans) < 2 or plans[0].demolished_ids or any(not p.demolished_ids for p in plans[1:]):
        raise ValueError("explicit comparison requires a baseline and nonempty demolition plans")
    if len({p.plan_id for p in plans}) != len(plans):
        raise ValueError("comparison plan IDs must be unique")
    base = resolve_roof_layout(bundle.request, inventory)
    removed = set()
    for plan in plans[1:]:
        action = set(plan.demolished_ids)
        if not action <= set(inventory.candidate_ids):
            raise ValueError("demolition must contain eligible whole buildings")
        if action.intersection(base.ambiguous_ids) and plan.overlap_policy != "retain_shared_roof":
            raise ValueError("ambiguous demolition requires explicit retain_shared_roof policy")
        removed.update(action)
    protocol = freeze_source_protocol(
        inventory,
        seed=config["seed"],
        draws_per_stratum=config["draws_per_stratum"],
        area_m2=config["ignition_area_m2"],
        near_distance_m=config["near_distance_m"],
    )
    realizations = {
        plan.plan_id: realize_sources(protocol, inventory, demolished_ids=plan.demolished_ids)
        for plan in plans
    }
    paths = [
        Path(__file__),
        Path(__file__).with_name("run_heritage_roof_study.py"),
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
    ]
    source_text = {str(p.relative_to(ROOT)): p.read_text() for p in paths}
    experiment = {
        "config": config,
        "study": json.loads(study_path.read_text()),
        "parent_case_sha256": baseline_hash,
        "inventory_sha256": inventory_hash,
        "source_protocol": protocol,
        "source_sha256": {key: content_hash(value) for key, value in source_text.items()},
        "transport": asdict(transport),
        "source_policy": "sampled_retained_sources",
        "optimization": False,
        "annual_risk": False,
    }
    experiment_hash = content_hash(experiment)
    if resume:
        saved = json.loads((output / "experiment.json").read_text())
        if content_hash(saved) != experiment_hash:
            raise ValueError("cannot resume: frozen experiment inputs or producer code changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(output / "experiment.json", experiment)
        dump(output / "source-protocol.json", protocol)
        dump(output / "inventory.json", inventory.to_mapping())
        dump(
            output / "realized-sources.json",
            {key: [_manifest_row(row) for row in value] for key, value in realizations.items()},
        )
        for relative, source in source_text.items():
            destination = output / "source-snapshot" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source)
    core_ids = tuple(t.building_id for t in inventory.targets)
    all_rows = []
    planned_runs = sum(row["status"] == "fire" for rows in realizations.values() for row in rows)
    planned_runs *= len(config["roof_scales_m"])
    if prepare_only:
        print(
            json.dumps(
                {
                    "planned_runs": planned_runs,
                    "source_pool": len(protocol["members"]),
                    "protocol_sha256": protocol["protocol_sha256"],
                }
            ),
            flush=True,
        )
        return protocol

    def summary():
        return {
            "schema_version": "heritage-random-source-pilot-results-1",
            "experiment_sha256": experiment_hash,
            "planned_runs": planned_runs,
            "complete": len(all_rows) == planned_runs,
            "plans": [
                summarize_protection(
                    protocol,
                    realizations[p.plan_id],
                    all_rows,
                    plan_id=p.plan_id,
                    scales_m=config["roof_scales_m"],
                    core_ids=core_ids,
                )
                for p in plans
            ],
            "runs": all_rows,
            "demolition": plans[-1].to_mapping(),
            "demolitions": [p.to_mapping() for p in plans[1:]],
            "source_pool_size": len(protocol["members"]),
            "parent_case_unchanged": request_fingerprint(bundle.request) == baseline_hash,
            "inventory_unchanged": inventory.sha256 == inventory_hash,
            "optimization": False,
        }

    for index, draw in enumerate(protocol["draws"]):
        fired = [rows[index] for rows in realizations.values() if rows[index]["status"] == "fire"]
        if not fired:
            continue
        footprints = {row["source_id"]: row["footprint"] for row in fired}
        selected_ids = tuple(sorted(set(footprints) | set(core_ids) | removed))
        for scale in config["roof_scales_m"]:
            run_id = f"{draw['draw_id']}-h{scale}".replace(".", "p")
            layout = refine_roof_layout(
                base,
                inventory,
                cell_size_m=scale,
                refined_ids=selected_ids,
                footprints=tuple(footprints.values()),
                max_nodes=config["max_nodes"],
            )
            builder = partial(
                roof_area_integrated_radiation_graph,
                integrated_ids=selected_ids,
                quadrature_spacing_m=config["quadrature_spacing_m"],
            )
            engine = RoofHeritageEvaluationEngine(
                bundle,
                transport,
                inventory,
                source_policy="sampled_retained_sources",
                code_version=experiment_hash,
                prepared_layout=layout,
                contact_builder=refined_contact_operator,
                radiation_builder=builder,
                max_radiation_edges=config["max_radiation_edges"],
            )
            baseline_record = None
            source_budget_reference = {}
            for plan in plans:
                realization = realizations[plan.plan_id][index]
                if realization["status"] != "fire":
                    continue
                folder = output / run_id / plan.plan_id
                row_path = folder / "run-summary.json"
                if resume and row_path.exists():
                    row = json.loads(row_path.read_text())
                    all_rows.append(row)
                    if not plan.demolished_ids:
                        from pyfireca.planning.evaluation import EvaluationResult

                        baseline_record = EvaluationResult(
                            **json.loads((folder / "evaluation.json").read_text())
                        )
                    print(f"Reusing {run_id}/{plan.plan_id}", flush=True)
                    continue
                if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
                    raise RuntimeError("insufficient free disk for remaining pilot runs")
                # Exact polygons are shared across plans/scales; only this draw's
                # realized source is ignited if a demolished source was replaced.
                ignition_layout = replace(
                    layout,
                    ignition_owner=tuple(
                        owner if owner == realization["source_id"] else None
                        for owner in layout.ignition_owner
                    ),
                )
                scenario = EvaluationScenario(
                    run_id, footprint_ignitions(ignition_layout), config["horizon_s"]
                )
                print(f"Starting {run_id}/{plan.plan_id}: {realization['source_id']}", flush=True)
                trace = runner.SelectedTrace(layout, tuple(sorted(set(core_ids) | set(footprints))))
                started = perf_counter()
                record, resolved, result = engine.simulate(scenario, plan, snapshot_sink=trace)
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
                reference = source_budget_reference.setdefault(realization["source_id"], budget)
                if any(
                    not np.isclose(budget[k], reference[k], rtol=1e-9, atol=1e-6) for k in budget
                ):
                    raise AssertionError("paired demolition changed retained source budgets")
                record.diagnostics.update(
                    random_source_protocol_sha256=protocol["protocol_sha256"],
                    random_draw_id=draw["draw_id"],
                    random_source_weight=realization["weight"],
                    source_replaced=realization["source_replaced"],
                    prescribed_source_budget=budget,
                    prescribed_ignition_geometry_sha256=realization["footprint"].sha256,
                )
                runner.save_evaluation(
                    folder, record, resolved, result, refined_contact_operator(resolved)
                )
                trace_data = {"node_ids": trace.nodes.tolist(), "frames": trace.frames}
                if config.get("archive_native_bundle", False):
                    # The extracted bundle keeps its native format and relative paths.
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
                if not plan.demolished_ids:
                    baseline_record = record
                elif not realization["source_replaced"]:
                    dump(
                        folder / "matched-ordinary-comparison.json",
                        compare_retained_ordinary(baseline_record, record),
                    )
                row = {
                    "draw_id": draw["draw_id"],
                    "stratum": draw["stratum"],
                    "scale_m": scale,
                    "plan_id": plan.plan_id,
                    "source_id": realization["source_id"],
                    "source_replaced": realization["source_replaced"],
                    "weight": realization["weight"],
                    "protection_status": record.metrics["protection_status"],
                    "core_outcomes": record.metrics["core_outcomes"],
                    "complete": record.diagnostics["reached_requested_horizon"],
                    "invalid_reasons": record.diagnostics["invalid_reasons"],
                    "source_verified": record.diagnostics["source_verified"],
                    "source_budget": budget,
                    "footprint_sha256": realization["footprint"].sha256,
                    "demolished_count": record.intervention["demolished_count"],
                    "demolished_area_m2": record.intervention["demolished_footprint_area_m2"],
                    "intervention": {
                        **record.intervention,
                        "demolished_ids": list(record.intervention["demolished_ids"]),
                    },
                    "energy_error_J": max(record.diagnostics["energy_closure_errors_J"].values()),
                    "energy_tolerance_J": record.diagnostics["energy_tolerance_J"],
                    "elapsed_s": perf_counter() - started,
                    "evaluation_sha256": record.evaluation_sha256,
                }
                dump(row_path, row)
                all_rows.append(row)
                dump(output / "summary.json", summary())
                print(
                    f"  complete: {row['protection_status']}; "
                    f"cores={record.metrics['ignited_core_ids']}",
                    flush=True,
                )
    final = summary()
    if not final["parent_case_unchanged"] or not final["inventory_unchanged"]:
        raise AssertionError("pilot mutated frozen inputs")
    dump(output / "summary.json", final)
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, prepare_only=args.prepare_only, resume=args.resume)
