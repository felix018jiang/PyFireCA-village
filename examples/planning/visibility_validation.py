"""Reproduce P02A and evaluate the separately named P02B isotropic graph.

python examples/planning/visibility_validation.py --output runs/planning-p02b-new
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from itertools import pairwise
from math import isfinite
from pathlib import Path
from time import perf_counter

import numpy as np
import shapely

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.contracts import (
    Evidence,
    Ignition,
    Plan,
    PlanningGrid,
    Scenario,
    Target,
    Treatment,
    TreatmentProfile,
)
from pyfireca.planning.geometry import prepare_plan
from pyfireca.planning.references import (
    parallel_band_time_s,
    rectangle_detour_infimum_s,
    rotate_point,
)
from pyfireca.planning.visibility import (
    CAPABILITIES,
    IntegratedEdges,
    VisibilityConfig,
    evaluate_visibility,
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_study(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    config = VisibilityConfig()
    study = {
        "schema": "planning-p02b-visibility-study-1",
        "evidence_level": "D0_synthetic",
        "original_matrix": "reproduce geometry_validation.py without changes",
        "original_vector_records": 54,
        "holdout_angles_deg": [11.25, 33.75, 67.5],
        "oblique_local_points_m": [[-40, -12], [40, 12]],
        "oblique_angles_deg": [0, 22.5, 33.75],
        "oblique_boundary_spacings_m": [8, 4, 2],
        "clearance_values_m": [0.1, 0.01, 0.001],
        "clearance_angles_deg": [0, 22.5, 45],
        "default_config": asdict(config),
        "additional_grid": {"cell_size_m": 4, "phase_xy": [0.25, 0.5]},
        "background_interface": "same 1/2 m/s field, physical endpoints, both directions",
        "visibility_solver_call_budget": 144,
        "raster_solver_call_budget": 222,
        "horizon_s": 87,
        "arrival_relative_tolerance": 0.02,
        "area_absolute_tolerance_m2": 1e-8,
        "baseline_and_path_absolute_tolerance_s": 1e-8,
        "capabilities": CAPABILITIES,
        "interpretation": [
            "old mapped-centre endpoints and new physical endpoints are separately declared",
            "continuous-edge graph removes eight-direction restriction and point snapping",
            "samples approximate refracted paths; no global optimum guarantee",
            "background is piecewise constant on declared raster cells",
            "M0 uses explicit positive physical clearance, not silent boundary erosion",
            "only target/node arrivals are returned, not a full raster fire-front field",
            "cold solve timings are not comparable as equivalent full-field outputs",
            "passes enable only small D0 isotropic enumeration, not real firebreak recommendations",
        ],
    }
    write_json(output / "study.resolved.json", study)
    # Keep a complete old-method reproduction, not a conveniently chosen subset.
    old_script = Path(__file__).with_name("geometry_validation.py")
    spec = importlib.util.spec_from_file_location("p02a_frozen_runner", old_script)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    old.run_study(output / "raster_baseline")
    old_records = json.loads((output / "raster_baseline/evaluations.json").read_text())
    evidence = Evidence("synthetic", "P02B constant-rate scene", "D0 isotropic only")
    records, timings = [], []

    def evaluate_case(
        group,
        mode,
        angle,
        grid,
        source=(-40, 0),
        target=(40, 0),
        cfg=config,
        old_record=None,
        run_baseline=False,
    ):
        half = 12 if mode == "M0" else 50
        treatment = Treatment(
            "strip", rotate_point((0, -half), angle), rotate_point((0, half), angle), 2, "treated"
        )
        profile = TreatmentProfile(
            "treated",
            mode,
            evidence,
            1,
            ConstantDirectionalSpreadRate(0.25) if mode == "M1" else None,
        )
        plan = Plan(group, (treatment,))
        scenario = Scenario(
            group,
            (Ignition(rotate_point(source, angle)),),
            (Target("asset", rotate_point(target, angle)),),
            87,
        )
        ref = (
            rectangle_detour_infimum_s(source, target, 2, half)
            if mode == "M0"
            else parallel_band_time_s(source, target, 2, 1, 0.25)
        )
        if mode == "M1":
            lower = min(abs(source[1] - y) + abs(target[1] - y) for y in (-half, half))
            if lower <= ref:
                raise ValueError("finite band lacks the declared continuum reference support")
        args = dict(
            grid=grid,
            domain_mask=np.ones(grid.shape, bool),
            background_rates_m_s=np.ones(grid.shape),
            plan=plan,
            scenario=scenario,
            profiles=(profile,),
            config=cfg,
        )
        started = perf_counter()
        result = evaluate_visibility(**args)
        baseline = evaluate_visibility(**(args | {"plan": Plan("empty")})) if run_baseline else None
        timings.append(
            {
                "record_index": len(records),
                "wall_s": perf_counter() - started,
                "solver_calls": 2 if run_baseline else 1,
            }
        )
        arrival = result.target_arrivals_s[0]
        path = result.target_paths_m[0]
        edges = IntegratedEdges(
            grid,
            args["domain_mask"],
            args["background_rates_m_s"],
            prepare_plan(grid, plan, (profile,)),
        )
        path_sum = sum(edges.travel_time_s(a, b) for a, b in pairwise(path))
        distance = float(np.linalg.norm(np.asarray(source) - np.asarray(target)))
        record = {
            "group": group,
            "mode": mode,
            "angle_deg": angle,
            "grid": asdict(grid),
            "source_m": scenario.ignitions[0].point_m,
            "target_m": scenario.targets[0].point_m,
            "treatment": asdict(treatment),
            "config": asdict(cfg),
            "continuum_reference_s": ref,
            "arrival_s": arrival if isfinite(arrival) else None,
            "graph_unreachable": not isfinite(arrival),
            "relative_error": (arrival - ref) / ref if isfinite(arrival) else None,
            "exposed_weight": result.exposed_weight,
            "reference_exposed_weight": float(ref <= 87),
            "baseline_arrival_s": baseline.target_arrivals_s[0] if baseline else None,
            "baseline_reference_s": distance,
            "exposure_reduction": baseline.exposed_weight - result.exposed_weight
            if baseline
            else None,
            "area_m2": result.treated_area_m2,
            "area_error_m2": result.treated_area_m2 - 4 * half,
            "cost_units": result.cost_units,
            "node_count": result.node_count,
            "tested_edge_count": result.tested_edge_count,
            "finite_edge_count": result.finite_edge_count,
            "path_m": path,
            "path_sum_error_s": path_sum - arrival if path else None,
            "old_vector_arrival_s": old_record["arrival_s"] if old_record else None,
            "old_vector_relative_error": old_record["physical_arrival_relative_error"]
            if old_record
            else None,
            "old_mapped_reference_s": old_record["mapped_continuum_treated_s"]
            if old_record
            else None,
        }
        records.append(record)

    for row in old_records:
        if row["representation"] != "vector":
            continue
        data = row["grid"]
        grid = PlanningGrid(
            tuple(data["shape"]), data["cell_size_m"], data["x_min_m"], data["y_max_m"]
        )
        evaluate_case(
            "original", row["mode"], row["angle_deg"], grid, old_record=row, run_baseline=True
        )
    grid = PlanningGrid((40, 40), 4, -79, 82)
    for angle in (11.25, 33.75, 67.5):
        for mode in ("M0", "M1"):
            evaluate_case("holdout-angle", mode, angle, grid, run_baseline=True)
    for angle in (0, 22.5, 33.75):
        for spacing in (8, 4, 2):
            evaluate_case(
                "oblique-refraction",
                "M1",
                angle,
                grid,
                (-40, -12),
                (40, 12),
                VisibilityConfig(boundary_spacing_m=spacing),
            )
    for angle in (0, 22.5, 45):
        for clearance in (0.1, 0.01, 0.001):
            evaluate_case(
                "clearance", "M0", angle, grid, cfg=VisibilityConfig(clearance_m=clearance)
            )
    background = []
    for h in (8, 4, 2):
        interface_grid = PlanningGrid((1, round(160 / h)), h, -80, h / 2)
        rates = np.array(
            [
                [
                    1 if interface_grid.centre(0, c)[0] < 0 else 2
                    for c in range(interface_grid.shape[1])
                ]
            ]
        )
        for direction, a, b in (("forward", (-40, 0), (40, 0)), ("reverse", (40, 0), (-40, 0))):
            result = evaluate_visibility(
                grid=interface_grid,
                domain_mask=np.ones(interface_grid.shape, bool),
                background_rates_m_s=rates,
                plan=Plan("empty"),
                scenario=Scenario(direction, (Ignition(a),), (Target("asset", b),), 200),
            )
            background.append(
                {
                    "cell_size_m": h,
                    "direction": direction,
                    "arrival_s": result.target_arrivals_s[0],
                    "physical_reference_s": 60,
                    "error_s": result.target_arrivals_s[0] - 60,
                }
            )
    write_json(output / "evaluations.json", records)
    write_json(output / "background_interface.json", background)
    write_json(output / "timings.json", timings)
    checks = {
        "physical_arrival_2pct_pass": all(
            r["relative_error"] is not None and abs(r["relative_error"]) <= 0.02 for r in records
        ),
        "horizon_decision_pass": all(
            r["exposed_weight"] == r["reference_exposed_weight"] for r in records
        ),
        "reference_lower_bound_pass": all(
            r["arrival_s"] is not None and r["arrival_s"] >= r["continuum_reference_s"] - 1e-8
            for r in records
        ),
        "area_pass": all(abs(r["area_error_m2"]) <= 1e-8 for r in records),
        "physical_baseline_pass": all(
            abs(r["baseline_arrival_s"] - r["baseline_reference_s"]) <= 1e-8
            for r in records
            if r["baseline_arrival_s"] is not None
        ),
        "path_sum_pass": all(
            r["path_sum_error_s"] is not None and abs(r["path_sum_error_s"]) <= 1e-8
            for r in records
        ),
        "background_interface_pass": all(abs(r["error_s"]) <= 1e-8 for r in background),
    }
    summary = {
        "evaluation_records": len(records),
        "background_interface_records": len(background),
        "visibility_solver_calls": sum(t["solver_calls"] for t in timings) + len(background),
        "checks": checks,
        "worst_relative_error": max(
            abs(r["relative_error"]) for r in records if r["relative_error"] is not None
        ),
        "worst_original_relative_error": max(
            abs(r["relative_error"])
            for r in records
            if r["group"] == "original" and r["relative_error"] is not None
        ),
        "max_nodes": max(r["node_count"] for r in records),
        "p03_scope_gate": "eligible_small_D0_isotropic_only"
        if all(checks.values())
        else "not_passed",
        "not_validated": [
            "anisotropic ROS/wind",
            "large domains",
            "complex heterogeneous path convergence",
            "real fuel treatment",
            "thermal/spotting crossing",
            "P00 novelty review",
        ],
    }
    write_json(output / "validation.json", summary)
    root = Path(__file__).resolve().parents[2]
    paths = [
        Path(__file__),
        old_script,
        *(root / "src/pyfireca/planning").glob("*.py"),
        root / "src/pyfireca/arrival.py",
        root / "src/pyfireca/neighborhood.py",
        root / "src/pyfireca/propagation.py",
        root / "src/pyfireca/state.py",
        root / "pyproject.toml",
    ]
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "shapely": shapely.__version__,
        "source_sha256": {},
    }
    for path in paths:
        relative = path.relative_to(root)
        data = path.read_bytes()
        provenance["source_sha256"][str(relative)] = hashlib.sha256(data).hexdigest()
        snapshot = output / "source_snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
    try:
        provenance["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        provenance["dirty_status"] = subprocess.check_output(
            ["git", "status", "--short"], cwd=root, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        provenance["head"] = None
    write_json(output / "provenance.json", provenance)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_study(args.output), indent=2))
