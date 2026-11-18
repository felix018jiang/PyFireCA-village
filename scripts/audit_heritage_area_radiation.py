"""Run the bounded Batuan source/core area-radiation correction audit."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from functools import partial
from pathlib import Path
from time import perf_counter

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


def source_budget(layout):
    mask = np.asarray([owner is not None for owner in layout.ignition_owner])
    return {
        name: float(getattr(layout.node_inputs.parameters, name).ravel()[mask].sum())
        for name in (
            "covered_area_m2",
            "initial_dry_fuel_mass_kg",
            "water_mass_kg",
            "available_energy_J",
            "peak_hrr_W",
        )
    }


def direct_peak_coupling(layout, graph, source_id, target_id):
    source_nodes, target_nodes = layout.nodes(source_id), layout.nodes(target_id)
    mask = np.isin(graph.source, source_nodes) & np.isin(graph.target, target_nodes)
    peak = layout.node_inputs.parameters.peak_hrr_W.ravel()
    return float(np.sum(peak[graph.source[mask]] * graph.weight[mask]))


def case_id(probe, area, scale):
    return f"{probe}-A{area}-h{scale}".replace(".", "p")


def run(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-area-radiation-1":
        raise ValueError("unsupported area-radiation audit schema")
    runner = load_runner()
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = runner.load_study(study)
    if inventory.candidate_ids or inventory.fixed_source_ids:
        raise ValueError(
            "technical audit requires source and candidate selection to remain pending"
        )
    case_before, inventory_before = request_fingerprint(bundle.request), inventory.sha256
    base = resolve_roof_layout(bundle.request, inventory)
    matrix = [
        {
            "case_id": case_id(probe["probe_id"], area, scale),
            "probe": probe,
            "area_m2": area,
            "roof_scale_m": scale,
        }
        for probe in config["technical_probes"]
        for area in config["areas_m2"]
        for scale in config["roof_scales_m"]
    ]
    output.mkdir(parents=True, exist_ok=False)
    paths = [
        Path(__file__),
        ROOT / "src/pyfireca/planning/roof_thermal.py",
        ROOT / "src/pyfireca/planning/roof_ignition.py",
        ROOT / "src/pyfireca/planning/engines/roof_heritage.py",
        Path(__file__).with_name("run_heritage_roof_study.py"),
    ]
    hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    dump(output / "config.json", config)
    dump(output / "matrix.json", matrix)
    dump(
        output / "protocol.json",
        {
            "schema_version": "heritage-area-radiation-protocol-1",
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "parent_case_sha256": case_before,
            "inventory_sha256": inventory_before,
            "source_sha256": hashes,
            "fixed_ignition_polygons": True,
            "material_and_transport_parameters_unchanged": True,
            "candidate_eligibility_pending": True,
            "fixed_study_source_pending": True,
            "optimization": False,
        },
    )
    footprints = {
        (probe["probe_id"], str(area)): fixed_area_ignition(
            inventory, probe["source_id"], probe["anchor_m"], area
        )
        for probe in config["technical_probes"]
        for area in config["areas_m2"]
    }
    dump(
        output / "ignition-footprints.json",
        {f"{probe}:{area}": value.to_mapping() for (probe, area), value in footprints.items()},
    )
    runs, budget_reference = [], {}
    for row in matrix:
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("free-disk guard stopped the remaining predeclared cases")
        probe = row["probe"]
        footprint = footprints[(probe["probe_id"], str(row["area_m2"]))]
        print(f"Starting {row['case_id']}", flush=True)
        layout = refine_roof_layout(
            base,
            inventory,
            cell_size_m=row["roof_scale_m"],
            refined_ids=(probe["source_id"], probe["target_id"]),
            footprints=(footprint,),
            max_nodes=config["max_nodes"],
        )
        budget = source_budget(layout)
        key = (probe["probe_id"], str(row["area_m2"]))
        reference = budget_reference.setdefault(key, budget)
        if any(
            not np.isclose(value, reference[name], rtol=1e-8, atol=1e-6)
            for name, value in budget.items()
        ):
            raise AssertionError("roof scale changed the prescribed source budget")
        builder = partial(
            roof_area_integrated_radiation_graph,
            integrated_ids=(probe["source_id"], probe["target_id"]),
            quadrature_spacing_m=config["quadrature_spacing_m"],
        )
        graph_start = perf_counter()
        graph = builder(layout, transport, max_edges=config["max_radiation_edges"])
        graph_seconds = perf_counter() - graph_start
        coupling = direct_peak_coupling(layout, graph, probe["source_id"], probe["target_id"])
        reference_coupling, quadrature_error = None, None
        if row["roof_scale_m"] == config["reference_scale_m"]:
            reference_graph = roof_area_integrated_radiation_graph(
                layout,
                transport,
                integrated_ids=(probe["source_id"], probe["target_id"]),
                quadrature_spacing_m=config["reference_quadrature_spacing_m"],
                max_edges=config["max_radiation_edges"],
            )
            reference_coupling = direct_peak_coupling(
                layout, reference_graph, probe["source_id"], probe["target_id"]
            )
            quadrature_error = abs(coupling - reference_coupling) / reference_coupling
        scenario = EvaluationScenario(
            row["case_id"], footprint_ignitions(layout), config["horizon_s"]
        )
        engine = RoofHeritageEvaluationEngine(
            bundle,
            transport,
            inventory,
            source_policy="technical_probe",
            code_version=content_hash(hashes),
            prepared_layout=layout,
            contact_builder=refined_contact_operator,
            radiation_builder=builder,
            max_radiation_edges=config["max_radiation_edges"],
        )
        trace = runner.SelectedTrace(layout, (probe["source_id"], probe["target_id"]))
        simulation_start = perf_counter()
        record, _, _ = engine.simulate(
            scenario,
            BuildingDemolitionPlan("baseline"),
            snapshot_sink=trace,
        )
        simulation_seconds = perf_counter() - simulation_start
        record.diagnostics["prescribed_initial_source_budget"] = budget
        record.diagnostics["prescribed_ignition_geometry_sha256"] = footprint.sha256
        folder = output / row["case_id"]
        folder.mkdir()
        dump(folder / "evaluation.json", record.to_mapping())
        dump(
            folder / "selected-trace.json",
            {"node_ids": trace.nodes.tolist(), "frames": trace.frames},
        )
        outcome = next(
            value
            for value in record.metrics["core_outcomes"]
            if value["building_id"] == probe["target_id"]
        )
        runs.append(
            {
                **{key: value for key, value in row.items() if key != "probe"},
                "probe_id": probe["probe_id"],
                "source_id": probe["source_id"],
                "target_id": probe["target_id"],
                "source_budget": budget,
                "ignition_geometry_sha256": footprint.sha256,
                "outcome": outcome,
                "protection_status": record.metrics["protection_status"],
                "nodes": len(layout.cell_id),
                "radiation_edges": len(graph.source),
                "graph_seconds": graph_seconds,
                "simulation_seconds": simulation_seconds,
                "direct_source_core_peak_coupling_W": coupling,
                "reference_quadrature_peak_coupling_W": reference_coupling,
                "quadrature_relative_error": quadrature_error,
                "max_energy_closure_error_J": max(
                    record.diagnostics["energy_closure_errors_J"].values()
                ),
                "energy_tolerance_J": record.diagnostics["energy_tolerance_J"],
                "evaluation_sha256": record.evaluation_sha256,
            }
        )
        dump(
            output / "summary.json",
            {
                "schema_version": "heritage-area-radiation-results-1",
                "runs": runs,
                "planned_cases": len(matrix),
                "complete": len(runs) == len(matrix),
            },
        )
        print(
            f"  complete: ignition={outcome['first_ignition_s']}; "
            f"peak={outcome['maximum_valid_surface_temperature_K']:.3f} K",
            flush=True,
        )
    if request_fingerprint(bundle.request) != case_before or inventory.sha256 != inventory_before:
        raise AssertionError("area-radiation audit mutated the base case or inventory")
    return runs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.output)
