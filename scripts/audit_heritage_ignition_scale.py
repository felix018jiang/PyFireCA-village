"""Bounded, predeclared source-area/roof-scale controls; no demolition or search."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.roof_ignition import (
    fixed_area_ignition,
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout

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


def case_matrix(config):
    rows = []
    for h in config["roof_scales_m"]:
        for area in config["areas_m2"]:
            rows.append(
                {"h_m": h, "area_m2": area, "phase": [0.0, 0.0], "dt_s": 1.0, "kind": "factorial"}
            )
    rows.extend(config["confirmations"])
    for row in rows:
        text = (
            f"A{row['area_m2']}-h{row['h_m']}-p{row['phase'][0]}-{row['phase'][1]}-dt{row['dt_s']}"
        )
        row["case_id"] = text.replace(".", "p")
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("experiment matrix contains duplicate conditions")
    return rows


def source_budget(layout):
    mask = np.asarray([v is not None for v in layout.ignition_owner])
    return {
        name: float(getattr(layout.node_inputs.parameters, name).ravel()[mask].sum())
        for name in (
            "covered_area_m2",
            "initial_dry_fuel_mass_kg",
            "water_mass_kg",
            "available_energy_J",
            "configured_curve_energy_J",
            "peak_hrr_W",
        )
    }


def run(config_path, probe_id, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-ignition-scale-1":
        raise ValueError("unsupported scale-control schema")
    probe = next(p for p in config["technical_probes"] if p["probe_id"] == probe_id)
    runner = load_runner()
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = runner.load_study(study)
    if inventory.candidate_ids or inventory.fixed_source_ids:
        raise ValueError("this technical audit requires real source/eligibility still pending")
    case_before, inventory_before = request_fingerprint(bundle.request), inventory.sha256
    base = resolve_roof_layout(bundle.request, inventory)
    matrix = case_matrix(config)
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "config.json", config)
    dump(output / "matrix.json", matrix)
    dump(output / "study-input.json", json.loads(study.read_text()))
    dump(output / "inventory.json", inventory.to_mapping())
    paths = [
        Path(__file__),
        Path(__file__).with_name("run_heritage_roof_study.py"),
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
    ]
    hashes = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        data = path.read_bytes()
        destination = output / "source-snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    dump(
        output / "protocol.json",
        {
            "schema_version": "heritage-ignition-scale-protocol-1",
            "config": config,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "probe": probe,
            "parent_case_sha256": case_before,
            "inventory_sha256": inventory_before,
            "source_sha256": hashes,
            "matrix_predeclared": matrix,
            "candidate_eligibility_pending": True,
            "fixed_study_source_pending": True,
            "optimization": False,
        },
    )
    footprints = {
        str(area): fixed_area_ignition(inventory, probe["source_id"], probe["anchor_m"], area)
        for area in config["areas_m2"]
    }
    dump(output / "ignition-footprints.json", {k: f.to_mapping() for k, f in footprints.items()})
    budgets, results = {}, []
    for row in matrix:
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError(
                "free-disk guard: retain completed controls; do not allocate more cases"
            )
        print(f"Starting {probe_id}/{row['case_id']}", flush=True)
        footprint = footprints[str(row["area_m2"])]
        layout = refine_roof_layout(
            base,
            inventory,
            cell_size_m=row["h_m"],
            refined_ids=(probe["source_id"], probe["target_id"]),
            footprints=(footprint,),
            phase_fraction=tuple(row["phase"]),
            max_nodes=config["max_nodes"],
        )
        budget = source_budget(layout)
        reference = budgets.setdefault(str(row["area_m2"]), budget)
        if any(
            not np.isclose(value, reference[name], rtol=1e-8, atol=1e-6)
            for name, value in budget.items()
        ):
            raise AssertionError("mesh changed the prescribed initial source fuel or heat budget")
        scenario = EvaluationScenario(
            row["case_id"], footprint_ignitions(layout), config["horizon_s"]
        )
        physical = replace(
            bundle.request, scenario=replace(bundle.request.scenario, max_dt_s=row["dt_s"])
        )
        # Only the time-step control derives a different clock; physical arrays remain identical.
        physical_bundle = replace(bundle, request=physical)
        layout = replace(layout, physical_request=physical)
        engine = RoofHeritageEvaluationEngine(
            physical_bundle,
            transport,
            inventory,
            source_policy="technical_probe",
            code_version=content_hash(hashes),
            prepared_layout=layout,
            contact_builder=refined_contact_operator,
            max_radiation_edges=config["max_radiation_edges"],
        )
        trace = runner.SelectedTrace(layout, (probe["source_id"], probe["target_id"]))

        def progress(now, end):
            if now == 0 or now >= end:
                print(f"  thermal clock {now:g}/{end:g} s", flush=True)

        record, resolved, result = engine.simulate(
            scenario,
            BuildingDemolitionPlan("baseline"),
            snapshot_sink=trace,
            progress_callback=progress,
        )
        record.diagnostics["prescribed_initial_source_budget"] = budget
        record.diagnostics["prescribed_ignition_geometry_sha256"] = footprint.sha256
        folder = output / row["case_id"]
        runner.save_evaluation(folder, record, resolved, result, refined_contact_operator(resolved))
        dump(
            folder / "selected-trace.json",
            {"node_ids": trace.nodes.tolist(), "frames": trace.frames},
        )
        outcome = next(
            v for v in record.metrics["core_outcomes"] if v["building_id"] == probe["target_id"]
        )
        results.append(
            {
                **row,
                "probe_id": probe_id,
                "source_id": probe["source_id"],
                "target_id": probe["target_id"],
                "source_budget": budget,
                "ignition_geometry_sha256": footprint.sha256,
                "outcome": outcome,
                "protection_status": record.metrics["protection_status"],
                "evaluation_sha256": record.evaluation_sha256,
                "evaluation": str(folder / "evaluation.json"),
                "reached_requested_horizon": record.diagnostics["reached_requested_horizon"],
                "max_energy_closure_error_J": max(
                    record.diagnostics["energy_closure_errors_J"].values()
                ),
                "energy_tolerance_J": record.diagnostics["energy_tolerance_J"],
                "nodes": len(layout.cell_id),
                "radiation_edges": record.diagnostics["radiation_edge_count"],
            }
        )
        dump(
            output / "summary.json",
            {
                "schema_version": "heritage-ignition-scale-results-1",
                "runs": results,
                "planned_cases": len(matrix),
                "complete": len(results) == len(matrix),
            },
        )
        print(
            f"  complete: first ignition {outcome['first_ignition_s']}; "
            f"max valid T {outcome['maximum_valid_surface_temperature_K']:.3f} K",
            flush=True,
        )
    if request_fingerprint(bundle.request) != case_before or inventory.sha256 != inventory_before:
        raise AssertionError("scale audit mutated base case or study inventory")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.probe, args.output)
