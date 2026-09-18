"""Argument parser and command handlers for village workflows."""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import webbrowser
from pathlib import Path

import numpy as np

from pyfireca.village.bootstrap import bootstrap_village_case
from pyfireca.village.exposure import estimate_exposure_resources
from pyfireca.village.materials_v2 import load_material_catalog_v2
from pyfireca.village.prepare import prepare_village_case
from pyfireca.village.prepared_workflow_v2 import load_prepared_v2_case, run_prepared_v2_case
from pyfireca.village.workflow import run_village_config, validate_village_run
from pyfireca.village.workflow_v2 import load_v2_file_case, run_v2_file_case


def add_village_subcommands(subparsers: argparse._SubParsersAction) -> None:
    village = subparsers.add_parser("village", help="prepare and run village-fire CA cases")
    commands = village.add_subparsers(dest="village_command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="create a synthetic trial case around a satellite GeoTIFF"
    )
    bootstrap.add_argument("imagery", type=Path)
    bootstrap.add_argument("--output", type=Path, required=True)
    bootstrap.add_argument("--cell-size-m", type=float, default=10.0)
    bootstrap.add_argument("--end-time-s", type=float, default=180.0)
    bootstrap.add_argument("--sample-interval-s", type=float, default=5.0)
    bootstrap.add_argument(
        "--labelme-buildings",
        type=Path,
        help="optional LabelMe JSON whose building shapes match the satellite image",
    )
    materials_v2 = commands.add_parser(
        "validate-materials-v2", help="validate a material-catalog-2 evidence file"
    )
    materials_v2.add_argument("catalog", type=Path)
    validate_v2 = commands.add_parser(
        "validate-v2", help="validate a complete effective_exposure_ca_v2 file case"
    )
    validate_v2.add_argument("case", type=Path)
    run_v2 = commands.add_parser("run-v2", help="run a complete effective_exposure_ca_v2 case")
    run_v2.add_argument("case", type=Path)
    run_v2.add_argument("--output", type=Path, required=True)
    validate_prepared_v2 = commands.add_parser(
        "validate-prepared-v2", help="validate v2 profiles against a prepared village"
    )
    validate_prepared_v2.add_argument("prepared", type=Path)
    validate_prepared_v2.add_argument("--scenario", type=Path, required=True)
    run_prepared_v2 = commands.add_parser(
        "run-prepared-v2", help="run v2 on a prepared village and write a browser replay"
    )
    run_prepared_v2.add_argument("prepared", type=Path)
    run_prepared_v2.add_argument("--scenario", type=Path, required=True)
    run_prepared_v2.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="validate and prepare a village case")
    prepare.add_argument("case", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="validate a prepared case and scenario")
    validate.add_argument("prepared", type=Path)
    validate.add_argument("--scenario", type=Path, required=True)
    run = commands.add_parser("run", help="run a prepared village case")
    run.add_argument("prepared", type=Path)
    run.add_argument("--scenario", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    view = commands.add_parser("view", help="serve an existing village result viewer")
    view.add_argument("run", type=Path)
    view.add_argument("--port", type=int, default=8765)


def run_village_command(args: argparse.Namespace) -> int:
    if args.village_command == "validate-prepared-v2":
        case = load_prepared_v2_case(args.prepared, args.scenario)
        request = case.request
        print(f"Valid prepared village v2 scenario: {args.scenario.resolve()}")
        print(
            f"Grid: {request.grid.height} x {request.grid.width}; "
            f"burnable cells: {int(np.count_nonzero(request.burnable_mask))}"
        )
        print(f"Exposure edges: {len(request.exposure_edges.source):,}")
        return 0
    if args.village_command == "run-prepared-v2":
        result, artifacts = run_prepared_v2_case(args.prepared, args.scenario, args.output)
        ignited = int(np.count_nonzero(np.isfinite(result.final_state.ignition_time_s)))
        print(f"Prepared village v2 run complete: {artifacts.directory.resolve()}")
        print(f"Ignited cells: {ignited}")
        print(f"Consumed fuel: {result.consumed_fuel_kg:.6f} kg")
        return 0
    if args.village_command == "validate-v2":
        case = load_v2_file_case(args.case)
        request = case.request
        print(f"Valid village v2 case: {args.case.resolve()}")
        print(
            f"Grid: {request.grid.height} x {request.grid.width}; "
            f"burnable cells: {int(np.count_nonzero(request.burnable_mask))}"
        )
        print(f"Exposure edges: {len(request.exposure_edges.source)}; evidence: {case.evidence}")
        return 0
    if args.village_command == "run-v2":
        result, artifacts = run_v2_file_case(args.case, args.output)
        ignited = int(np.count_nonzero(np.isfinite(result.final_state.ignition_time_s)))
        print(f"Village v2 run complete: {artifacts.directory.resolve()}")
        print(f"Ignited cells: {ignited}")
        print(f"Consumed fuel: {result.consumed_fuel_kg:.6f} kg")
        return 0
    if args.village_command == "validate-materials-v2":
        catalog = load_material_catalog_v2(args.catalog)
        fuel_materials = [
            material
            for material in catalog.materials
            if material.burnable and material.role != "reference_only"
        ]
        incomplete = [
            material for material in fuel_materials if material.missing_runtime_parameters
        ]
        print(f"Valid material-catalog-2: {args.catalog.resolve()}")
        print(
            f"Materials: {len(catalog.materials)}; "
            f"runnable fuel materials: {len(catalog.runnable_material_ids)}"
        )
        print(
            f"Production ready: {catalog.production_ready}; "
            f"catalog status: {catalog.catalog_status}"
        )
        if incomplete:
            print("Incomplete village fuel materials:")
            for material in incomplete:
                print(f"  {material.id}: {', '.join(material.missing_runtime_parameters)}")
        return 0
    if args.village_command == "bootstrap":
        case, scenario = bootstrap_village_case(
            args.imagery,
            args.output,
            cell_size_m=args.cell_size_m,
            end_time_s=args.end_time_s,
            sample_interval_s=args.sample_interval_s,
            labelme_path=args.labelme_buildings,
        )
        print(f"Synthetic village case: {case.resolve()}")
        print(f"Synthetic scenario: {scenario.resolve()}")
        if args.labelme_buildings is None:
            print("Only the satellite GeoTIFF is observational; all other inputs are placeholders.")
        else:
            print(
                "Satellite imagery and building geometry are observational; "
                "materials and fire parameters are placeholders."
            )
        return 0
    if args.village_command == "prepare":
        path = prepare_village_case(args.case, args.output)
        print(f"Prepared village case: {path.resolve()}")
        return 0
    if args.village_command == "validate":
        request, scenario = validate_village_run(args.prepared, args.scenario)
        estimate = estimate_exposure_resources(
            request.domain, exposure_radius_m=scenario.exposure_radius_m
        )
        print(f"Valid village simulation; exposure edges: {len(request.exposure_edges.source):,}")
        print(
            "Conservative graph estimate: "
            f"{estimate.exposure_edge_upper_bound:,} edges, "
            f"{estimate.exposure_arrays_upper_bound_bytes / (1024**2):.1f} MiB"
        )
        return 0
    if args.village_command == "run":
        result, artifacts = run_village_config(args.prepared, args.scenario, args.output)
        ignited = int(np.count_nonzero(np.isfinite(result.final_state.ignition_time_s)))
        print(f"Village run complete: {artifacts.directory.resolve()}")
        print(f"Ignited cells: {ignited}")
        print(f"Consumed fuel: {result.consumed_fuel_kg:.6f} kg")
        return 0
    if args.village_command == "view":
        viewer = (args.run / "viewer").resolve()
        if not (viewer / "manifest.json").is_file():
            raise FileNotFoundError(f"village viewer not found: {viewer}")
        if not 1 <= args.port <= 65535:
            raise ValueError("port must lie in [1, 65535]")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=viewer)
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            url = f"http://127.0.0.1:{args.port}/"
            print(f"Village viewer: {url}")
            print("Press Ctrl+C to stop.")
            webbrowser.open(url)
            with contextlib.suppress(KeyboardInterrupt):
                server.serve_forever()
        return 0
    raise ValueError(f"unsupported village command: {args.village_command}")
