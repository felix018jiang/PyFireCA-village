"""Argument parser and command handlers for village workflows."""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import webbrowser
from pathlib import Path

import numpy as np

from pyfireca.village.bootstrap import bootstrap_village_case
from pyfireca.village.exposure import estimate_exposure_resources
from pyfireca.village.gaoyou_v3 import build_gaoyou_v3_case
from pyfireca.village.hypothetical import build_hypothetical_material_case
from pyfireca.village.inspection_v3 import inspect_v3_run
from pyfireca.village.interactive_v3 import (
    serve_interactive_components_v3,
    serve_interactive_v3,
)
from pyfireca.village.materials_v2 import load_material_catalog_v2
from pyfireca.village.materials_v3 import load_material_catalog_v3
from pyfireca.village.preflight_v3 import preflight_v3_case
from pyfireca.village.prepare import prepare_village_case
from pyfireca.village.prepared_workflow_v2 import load_prepared_v2_case, run_prepared_v2_case
from pyfireca.village.resolution_workflow_v3 import resolve_survey_v3
from pyfireca.village.resource_sensitivity_v3 import build_resource_sensitivity_report
from pyfireca.village.rgb_hypothesis_v3 import complete_rgb_hypothesis_spatial
from pyfireca.village.survey_mapping_v3 import validate_unknown_mapping_v3
from pyfireca.village.unknown_review_v3 import review_unknown_v3
from pyfireca.village.vegetation_prepare import prepare_vegetation_spatial
from pyfireca.village.wind_v3 import inspect_wind
from pyfireca.village.workflow import run_village_config, validate_village_run
from pyfireca.village.workflow_v2 import load_v2_file_case, run_v2_file_case
from pyfireca.village.workflow_v3 import load_v3_case_bundle, run_v3_case_bundle


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
    hypothetical = commands.add_parser(
        "hypothesize-materials",
        help="derive an explicitly hypothetical material map from RGB imagery",
    )
    hypothetical.add_argument("imagery", type=Path)
    hypothetical.add_argument("--labelme-buildings", type=Path, required=True)
    hypothetical.add_argument("--output", type=Path, required=True)
    hypothetical.add_argument("--cell-size-m", type=float, default=2.0)
    vegetation_spatial = commands.add_parser(
        "prepare-vegetation-spatial",
        help="reproject imagery/buildings and create the V01 land-cover spatial package",
    )
    vegetation_spatial.add_argument("imagery", type=Path)
    vegetation_spatial.add_argument("--labelme-buildings", type=Path, required=True)
    vegetation_spatial.add_argument("--output", type=Path, required=True)
    vegetation_spatial.add_argument("--cell-size-m", type=float, default=2.0)
    vegetation_spatial.add_argument("--working-crs")
    vegetation_spatial.add_argument("--ignition-building-id", default="B0233")
    materials_v2 = commands.add_parser(
        "validate-materials-v2", help="validate a material-catalog-2 evidence file"
    )
    materials_v2.add_argument("catalog", type=Path)
    materials_v3 = commands.add_parser(
        "validate-materials-v3", help="validate parameter-level material evidence and gaps"
    )
    materials_v3.add_argument("catalog", type=Path)
    validate_v2 = commands.add_parser(
        "validate-v2", help="validate a complete effective_exposure_ca_v2 file case"
    )
    validate_v2.add_argument("case", type=Path)
    run_v2 = commands.add_parser("run-v2", help="run a complete effective_exposure_ca_v2 case")
    run_v2.add_argument("case", type=Path)
    run_v2.add_argument("--output", type=Path, required=True)
    validate_v3 = commands.add_parser(
        "validate-v3", help="validate a serialized effective_exposure_ca_v3 case bundle"
    )
    validate_v3.add_argument("case", type=Path)
    wind_v3 = commands.add_parser(
        "inspect-wind-v3", help="validate prescribed wind and compute advection (not fire spread)"
    )
    wind_v3.add_argument("wind", type=Path)
    wind_v3.add_argument("--end-time-s", type=float, required=True)
    wind_v3.add_argument("--output", type=Path, required=True)
    flame_axes = commands.add_parser(
        "inspect-component-flames-v3",
        help="diagnose prescribed component flame axes (no wind-coupled fire spread)",
    )
    flame_axes.add_argument("run", type=Path)
    flame_axes.add_argument("--wind", type=Path, required=True)
    flame_axes.add_argument("--geometry", type=Path, required=True)
    flame_axes.add_argument("--output", type=Path, required=True)
    flame_axes.add_argument("--end-time-s", type=float)
    flame_axes.add_argument("--sample-interval-s", type=float, default=10)
    flame_axes.add_argument("--max-records", type=int, default=200_000)
    run_v3 = commands.add_parser(
        "run-v3", help="run a serialized effective_exposure_ca_v3 case bundle"
    )
    run_v3.add_argument("case", type=Path)
    run_v3.add_argument("--output", type=Path, required=True)
    run_v3.add_argument("--prepared", type=Path)
    run_v3.add_argument("--flame-contact-config", type=Path)
    components_v3 = commands.add_parser(
        "run-components-v3", help="run an opt-in fuel-component experiment with optional replay"
    )
    components_v3.add_argument("case", type=Path)
    components_v3.add_argument("--components-config", type=Path, required=True)
    components_v3.add_argument("--output", type=Path, required=True)
    components_v3.add_argument("--flame-contact-config", type=Path)
    components_v3.add_argument("--internal-radiation-config", type=Path)
    components_v3.add_argument("--component-curves-config", type=Path)
    components_v3.add_argument("--receiver-surfaces-config", type=Path)
    components_v3.add_argument("--finite-radiation-config", type=Path)
    components_v3.add_argument("--radiation-only", action="store_true")
    components_v3.add_argument("--max-nodes", type=int, default=20_000)
    components_v3.add_argument("--max-radiation-edges", type=int, default=500_000)
    components_v3.add_argument("--viewer", action="store_true")
    components_v3.add_argument("--prepared", type=Path)
    components_v3.add_argument("--viewer-chunk-frames", type=int, default=32)
    components_v3.add_argument("--max-viewer-memory-mib", type=int, default=256)
    run_v3.add_argument(
        "--chunk-frames",
        type=int,
        help="write viewer dynamic arrays as bounded frame chunks",
    )
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
    view_v3 = commands.add_parser("view-v3", help="serve an existing V3 village result viewer")
    view_v3.add_argument("run", type=Path)
    view_v3.add_argument("--port", type=int, default=8765)
    simulate_v3 = commands.add_parser(
        "simulate-v3", help="choose an ignition cell and duration in the V3 browser map"
    )
    simulate_v3.add_argument("case", type=Path)
    simulate_v3.add_argument("--initial-run", type=Path, required=True)
    simulate_v3.add_argument("--output-root", type=Path, required=True)
    simulate_v3.add_argument("--port", type=int, default=8812)
    simulate_v3.add_argument("--no-browser", action="store_true")
    simulate_v3.add_argument("--flame-contact-config", type=Path)
    simulate_components = commands.add_parser(
        "simulate-components-v3", help="select ignition using a saved component experiment"
    )
    simulate_components.add_argument("run", type=Path)
    simulate_components.add_argument("--output-root", type=Path, required=True)
    simulate_components.add_argument("--port", type=int, default=8813)
    simulate_components.add_argument("--no-browser", action="store_true")
    simulate_components.add_argument("--max-nodes", type=int, default=20_000)
    simulate_components.add_argument("--max-radiation-edges", type=int, default=500_000)
    simulate_components.add_argument("--max-viewer-memory-mib", type=int, default=256)
    inspect_v3 = commands.add_parser(
        "inspect-v3", help="check representative V3 replay frames against backend arrays"
    )
    inspect_v3.add_argument("run", type=Path)
    inspect_v3.add_argument("--output", type=Path)
    inspect_v3.add_argument(
        "--cell-id",
        type=int,
        action="append",
        default=[],
        help="cell id to include in first/middle/final frame details (repeatable)",
    )
    preflight_v3 = commands.add_parser(
        "preflight-v3", help="audit a V3 case before whole-domain simulation"
    )
    preflight_v3.add_argument("case", type=Path)
    preflight_v3.add_argument("--output", type=Path)
    preflight_v3.add_argument("--max-edge-count", type=int, default=5_000_000)
    preflight_v3.add_argument("--max-memory-mb", type=int, default=2_048)
    review_unknown = commands.add_parser(
        "review-unknown-v3", help="summarize unresolved V3 cells without assigning materials"
    )
    review_unknown.add_argument("prepared", type=Path)
    review_unknown.add_argument("case", type=Path)
    review_unknown.add_argument("--output", type=Path, required=True)
    validate_mapping = commands.add_parser(
        "validate-unknown-mapping-v3",
        help="check field material mappings against a V3 unknown-area review",
    )
    validate_mapping.add_argument("review", type=Path)
    validate_mapping.add_argument("survey", type=Path)
    validate_mapping.add_argument("--output", type=Path)
    resolve_survey = commands.add_parser(
        "resolve-survey-v3", help="resolve a field survey into auditable M02 quantities"
    )
    resolve_survey.add_argument("survey", type=Path)
    resolve_survey.add_argument("--material-cards", type=Path, required=True)
    resolve_survey.add_argument("--assemblies", type=Path, required=True)
    resolve_survey.add_argument(
        "--moisture",
        type=Path,
        help="optional measured/scenario water file; omitted uses packaged material defaults",
    )
    resolve_survey.add_argument("--geometry-areas", type=Path, required=True)
    resolve_survey.add_argument(
        "--mode", choices=("demonstration", "evidence_checked"), default="demonstration"
    )
    resolve_survey.add_argument("--case-id", default="v3-resolved-survey")
    resolve_survey.add_argument("--output", type=Path, required=True)
    rgb_case = commands.add_parser(
        "build-gaoyou-rgb-v3",
        help="build an explicitly hypothetical full-domain V3 bundle from RGB completion",
    )
    rgb_case.add_argument("prepared", type=Path)
    rgb_case.add_argument("physics", type=Path)
    rgb_case.add_argument("transport", type=Path)
    rgb_case.add_argument("--output", type=Path, required=True)
    rgb_case.add_argument("--case-snapshot", type=Path)
    rgb_case.add_argument("--end-time-s", type=float, default=30_000.0)
    rgb_case.add_argument("--sample-interval-s", type=float, default=300.0)
    rgb_case.add_argument(
        "--max-edge-count",
        type=int,
        help="explicit resource override for this new bundle; the snapshot is not modified",
    )
    rgb_case.add_argument(
        "--max-memory-mb",
        type=int,
        help="explicit construction-memory override for this new bundle",
    )
    complete_rgb = commands.add_parser(
        "complete-rgb-hypothesis-v3",
        help="fill V01 unknown cells with an explicit inert RGB hypothesis",
    )
    complete_rgb.add_argument("prepared", type=Path)
    complete_rgb.add_argument("--output", type=Path, required=True)
    resource_sensitivity = commands.add_parser(
        "resource-sensitivity-v3",
        help="estimate full-domain V3 graph and replay resources without building or running",
    )
    resource_sensitivity.add_argument("spatial", type=Path)
    resource_sensitivity.add_argument("physics", type=Path)
    resource_sensitivity.add_argument("--case-snapshot", type=Path, required=True)
    resource_sensitivity.add_argument("--output", type=Path, required=True)
    resource_sensitivity.add_argument(
        "--radii-m", type=float, nargs="+", default=[4.0, 6.0, 8.0, 10.0]
    )
    resource_sensitivity.add_argument("--end-time-s", type=float, default=30_000.0)
    resource_sensitivity.add_argument("--sample-interval-s", type=float, default=300.0)


def run_village_command(args: argparse.Namespace) -> int:
    if args.village_command == "run-components-v3":
        from dataclasses import replace

        from pyfireca.village.component_curves_v3 import load_component_curve_configuration
        from pyfireca.village.component_outputs_v3 import (
            check_component_viewer_memory,
            write_component_viewer,
        )
        from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
        from pyfireca.village.component_surfaces_v3 import load_receiver_surface_configuration
        from pyfireca.village.components_v3 import (
            load_component_configuration,
            resolve_components,
            run_component_simulation,
            write_component_run,
        )
        from pyfireca.village.finite_radiation_v3 import load_finite_radiation_configuration
        from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters

        if args.radiation_only and args.flame_contact_config:
            raise ValueError("--radiation-only conflicts with --flame-contact-config")
        if args.output.exists():
            raise FileExistsError(args.output)
        if args.prepared and not args.viewer:
            raise ValueError("--prepared requires --viewer for component replay")
        if args.viewer and args.viewer_chunk_frames <= 0:
            raise ValueError("--viewer-chunk-frames must be positive")
        bundle = load_v3_case_bundle(args.case)
        request = bundle.request
        if args.viewer and request.sample_interval_s is None:
            request = replace(request, sample_interval_s=60.0)
        contact = (
            load_flame_contact_parameters(args.flame_contact_config)
            if args.flame_contact_config
            else request.flame_contact
        )
        resolved = resolve_components(
            request,
            load_component_configuration(args.components_config),
            contact=None if args.radiation_only else contact,
            internal_radiation=(
                load_internal_radiation_parameters(args.internal_radiation_config)
                if args.internal_radiation_config
                else None
            ),
            curves=(
                load_component_curve_configuration(args.component_curves_config)
                if args.component_curves_config
                else None
            ),
            surfaces=(
                load_receiver_surface_configuration(args.receiver_surfaces_config)
                if args.receiver_surfaces_config
                else None
            ),
            finite_radiation=(
                load_finite_radiation_configuration(args.finite_radiation_config)
                if args.finite_radiation_config
                else None
            ),
            max_nodes=args.max_nodes,
            max_radiation_edges=args.max_radiation_edges,
        )
        if args.viewer:
            check_component_viewer_memory(resolved, args.max_viewer_memory_mib)
        result = run_component_simulation(resolved, retain_snapshots=args.viewer)
        output = write_component_run(
            resolved,
            result,
            args.output,
            material_labels=bundle.material_labels,
            landcover_labels=bundle.landcover_labels,
            scenario_metadata=bundle.metadata,
        )
        if args.viewer:
            write_component_viewer(
                resolved,
                result,
                output,
                prepared_dir=args.prepared,
                material_labels=bundle.material_labels,
                landcover_labels=bundle.landcover_labels,
                scenario_metadata=bundle.metadata,
                chunk_frames=args.viewer_chunk_frames,
                max_memory_mib=args.max_viewer_memory_mib,
            )
        print(f"Component experiment complete: {output.resolve()}")
        print("Component ignitions do not establish crown fire or whole-building loss.")
        return 0
    if args.village_command == "inspect-component-flames-v3":
        from pyfireca.village.flame_geometry_v3 import inspect_component_flames

        inspect_component_flames(
            args.run,
            args.wind,
            args.geometry,
            args.output,
            end_time_s=args.end_time_s,
            sample_interval_s=args.sample_interval_s,
            max_records=args.max_records,
        )
        print(f"Flame-axis diagnostic written to {args.output}; heat transport unchanged")
        return 0
    if args.village_command == "inspect-wind-v3":
        report = inspect_wind(args.wind, end_time_s=args.end_time_s)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Wind diagnostic written to {args.output}; fire simulation not executed")
        return 0
    if args.village_command == "prepare-vegetation-spatial":
        prepared = prepare_vegetation_spatial(
            args.imagery,
            args.labelme_buildings,
            args.output,
            cell_size_m=args.cell_size_m,
            working_crs=args.working_crs,
            ignition_building_id=args.ignition_building_id,
        )
        print(f"V01 vegetation spatial package: {prepared.resolve()}")
        print("Combustion profiles remain unresolved until V02; no fire simulation was run.")
        return 0
    if args.village_command == "hypothesize-materials":
        case, scenario = build_hypothetical_material_case(
            args.imagery,
            args.labelme_buildings,
            args.output,
            cell_size_m=args.cell_size_m,
        )
        print(f"Hypothetical material case: {case.resolve()}")
        print(f"Hypothetical v2 scenario: {scenario.resolve()}")
        print("RGB-derived classes and all combustion parameters are synthetic assumptions.")
        return 0
    if args.village_command == "validate-materials-v3":
        catalog = load_material_catalog_v3(args.catalog)
        candidates = [
            material
            for material in catalog.materials
            if material.scope == "village_candidate" and material.burnable
        ]
        print(f"Valid material-catalog-3: {args.catalog.resolve()}")
        print(
            f"Materials: {len(catalog.materials)}; village fuel candidates: {len(candidates)}; "
            f"parameter-complete: {len(catalog.parameter_complete_village_materials)}; "
            f"sourced parameters: {catalog.evidenced_parameter_count}"
        )
        print(f"Production ready: {catalog.production_ready}; status: {catalog.catalog_status}")
        for material in candidates:
            if material.missing_runtime_parameters:
                print(f"  {material.id}: missing {', '.join(material.missing_runtime_parameters)}")
        return 0
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
    if args.village_command == "validate-v3":
        case = load_v3_case_bundle(args.case)
        request = case.request
        print(f"Valid village v3 case: {args.case.resolve()}")
        print(
            f"Grid: {request.grid.height} x {request.grid.width}; "
            f"burnable cells: {int(np.count_nonzero(request.burnable_mask))}; "
            f"unknown policy: {request.unknown_policy}"
        )
        print(f"Exposure edges: {len(request.exposure_edges.source):,}")
        return 0
    if args.village_command == "run-v3":
        result, output = run_v3_case_bundle(
            args.case,
            args.output,
            prepared_dir=args.prepared,
            chunk_frames=args.chunk_frames,
            flame_contact_config=args.flame_contact_config,
        )
        ignited = int(np.count_nonzero(np.isfinite(result.final_state.ignition_time_s)))
        print(f"Village v3 run complete: {output.resolve()}")
        print(f"Ignited cells: {ignited}")
        print(f"Consumed fuel: {result.consumed_fuel_kg:.6f} kg")
        return 0
    if args.village_command == "inspect-v3":
        report = inspect_v3_run(args.run, output=args.output, cell_ids=args.cell_id)
        checks = ", ".join(f"{key}={value}" for key, value in report["backend_checks"].items())
        print(f"V3 replay inspection: {args.run.resolve()}")
        print(
            f"Representative frames: {report['frame_count']} total; "
            f"visual acceptance: {report['visual_acceptance']}"
        )
        if checks:
            print(f"Backend checks: {checks}")
        if args.output:
            print(f"Inspection report: {args.output.resolve()}")
        return 0
    if args.village_command == "preflight-v3":
        report = preflight_v3_case(
            args.case,
            output=args.output,
            max_edge_count=args.max_edge_count,
            max_memory_mb=args.max_memory_mb,
        )
        print(f"V3 preflight: {args.case.resolve()}")
        print(f"Status: {report['status']}")
        print(
            f"Cells: {report['grid']['cell_count']:,}; "
            f"burnable: {report['classification']['burnable_cell_count']:,}; "
            f"unknown: {report['classification']['unknown_cell_count']:,}; "
            f"edges: {report['transport']['edge_count']:,}"
        )
        for reason in report["research_gate_reasons"] + report["resource_gate_reasons"]:
            print(f"  BLOCK: {reason}")
        if args.output:
            print(f"Preflight report: {args.output.resolve()}")
        return 0
    if args.village_command == "review-unknown-v3":
        report = review_unknown_v3(args.prepared, args.case, output_dir=args.output)
        print(f"V3 unknown review: {args.output.resolve()}")
        print(
            f"Unknown cells: {report['unknown_cell_count']:,}; "
            f"area: {report['unknown_area_m2']:.1f} m²; "
            f"components: {report['component_count']:,}"
        )
        print("No case arrays were modified; choose a policy before rebuilding a runnable bundle.")
        return 0
    if args.village_command == "validate-unknown-mapping-v3":
        report = validate_unknown_mapping_v3(args.review, args.survey, output=args.output)
        print(f"V3 survey mapping validation: {args.survey.resolve()}")
        print(
            f"Status: {report['status']}; mapped components: "
            f"{report['mapped_component_count']:,}/{report['expected_component_count']:,}"
        )
        for issue in report["issues"]:
            print(f"  BLOCK: {issue}")
        if args.output:
            print(f"Mapping validation report: {args.output.resolve()}")
        return 0
    if args.village_command == "resolve-survey-v3":
        resolved = resolve_survey_v3(
            args.survey,
            material_cards_path=args.material_cards,
            assemblies_path=args.assemblies,
            moisture_path=args.moisture,
            geometry_areas_path=args.geometry_areas,
            mode=args.mode,
            case_id=args.case_id,
            output=args.output,
        )
        print(f"V3 survey resolution: {args.survey.resolve()}")
        print(f"Status: {'ready' if resolved.can_run else 'blocked'}")
        print(f"Records: {len(resolved.records):,}; issues: {len(resolved.issues):,}")
        if resolved.issues:
            for issue in resolved.issues[:10]:
                print(f"  BLOCK: {issue.code}: {issue.message}")
        print(f"Resolved survey report: {args.output.resolve()}")
        return 0
    if args.village_command == "build-gaoyou-rgb-v3":
        output = build_gaoyou_v3_case(
            args.prepared,
            args.physics,
            args.transport,
            args.output,
            case_snapshot=args.case_snapshot,
            end_time_s=args.end_time_s,
            sample_interval_s=args.sample_interval_s,
            fill_unknown_as_bare_or_road=True,
            max_edge_count=args.max_edge_count,
            max_memory_mb=args.max_memory_mb,
        )
        print(f"Gaoyou RGB-hypothesis V3 bundle: {output.resolve()}")
        print("Unknown domain cells were assigned to hypothetical inert bare/road receivers.")
        print("All materials and combustion parameters remain synthetic assumptions.")
        return 0
    if args.village_command == "complete-rgb-hypothesis-v3":
        output = complete_rgb_hypothesis_spatial(args.prepared, args.output)
        report = json.loads((output / "hypothetical_completion_report.json").read_text())
        print(f"RGB-hypothesis spatial package: {output.resolve()}")
        print(
            f"Filled bare/road cells: {report['filled_bare_or_road_cell_count']:,}; "
            f"final known cells: {report['final_known_cell_count']:,}"
        )
        print("Status: hypothetical_only; combustion parameters were not changed.")
        return 0
    if args.village_command == "resource-sensitivity-v3":
        report = build_resource_sensitivity_report(
            args.spatial,
            args.physics,
            args.case_snapshot,
            output=args.output,
            radii_m=args.radii_m,
            end_time_s=args.end_time_s,
            sample_interval_s=args.sample_interval_s,
        )
        active = report["active_case"]
        estimate = active["estimate"]["estimate"]
        print(f"V3 resource sensitivity: {args.output.resolve()}")
        print(
            f"Active radius: {active['radius_m']:.1f} m; "
            f"candidate edges: {estimate['candidate_edge_upper_bound']:,}; "
            f"status: {report['status']}"
        )
        print(
            "No transport edges were built and no simulation was run; "
            "radius entries are diagnostic alternatives."
        )
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
    if args.village_command in {"view", "view-v3"}:
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
    if args.village_command == "simulate-components-v3":
        serve_interactive_components_v3(
            args.run,
            args.output_root,
            port=args.port,
            open_browser=not args.no_browser,
            max_nodes=args.max_nodes,
            max_radiation_edges=args.max_radiation_edges,
            max_viewer_memory_mib=args.max_viewer_memory_mib,
        )
        return 0
    if args.village_command == "simulate-v3":
        serve_interactive_v3(
            args.case,
            args.initial_run,
            args.output_root,
            port=args.port,
            open_browser=not args.no_browser,
            flame_contact_config=args.flame_contact_config,
        )
        return 0
    raise ValueError(f"unsupported village command: {args.village_command}")
