"""Frozen P02 D0 geometry experiment; use a new output directory each time.

    python examples/planning/geometry_validation.py --output runs/planning-p02-new

Rigidly rotate the entire isotropic scene, not just the line or wind. A fixed
north-up grid is retained. No projected GIS or dynamic wind is implied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from math import hypot, isfinite
from pathlib import Path
from time import perf_counter

import numpy as np
import shapely

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.adapters import CAPABILITIES, centre_labels, evaluate, evaluate_pair
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
    octile_time_s,
    parallel_band_time_s,
    rectangle_detour_infimum_s,
    rotate_point,
)

CELL_SIZES = (8.0, 4.0, 2.0)
PHASES = ((0.0, 0.0), (0.5, 0.0), (0.25, 0.5))
ANGLES = (0.0, 22.5, 45.0)
REPRESENTATIONS = ("vector", "centre_mask")
CASES = (("finite-ideal", "M0", 12.0), ("slow-band", "M1", 50.0))


class HalfSpaceRate:
    """Synthetic discontinuous background, sampled at the source centre."""

    def __init__(self, grid):
        self.grid = grid

    def spread_rate_m_s(self, row, col, offset):
        return 1.0 if self.grid.centre(row, col)[0] < 0 else 2.0


def background_interface_records() -> list[dict]:
    """Expose source-cell bias against an independent 1D piecewise integral."""
    records = []
    for h in CELL_SIZES:
        grid = PlanningGrid((1, round(160 / h)), h, -80, h / 2)
        for direction, a, b in (
            ("forward", (-40, 0), (40, 0)),
            ("reverse", (40, 0), (-40, 0)),
        ):
            result = evaluate(
                grid=grid,
                domain_mask=np.ones(grid.shape, dtype=bool),
                background=HalfSpaceRate(grid),
                plan=Plan("empty"),
                scenario=Scenario(direction, (Ignition(a),), (Target("asset", b),), 200),
            )
            mapped_a, mapped_b = grid.centre(*grid.cell_at(a)), grid.centre(*grid.cell_at(b))
            left, right = sorted((mapped_a[0], mapped_b[0]))
            reference = -left + right / 2
            records.append(
                {
                    "direction": direction,
                    "cell_size_m": h,
                    "arrival_s": result.target_arrivals_s[0],
                    "mapped_continuum_s": reference,
                    "physical_continuum_s": 60.0,
                    "mapped_error_s": result.target_arrivals_s[0] - reference,
                }
            )
    return records


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_study(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    study = {
        "schema": "planning-p02-geometry-study-1",
        "evidence_level": "D0_synthetic",
        "cell_sizes_m": CELL_SIZES,
        "grid_phase_fractions_xy": PHASES,
        "rigid_scene_rotations_deg": ANGLES,
        "representations": REPRESENTATIONS,
        "cases": CASES,
        "local_source_m": (-40.0, 0.0),
        "local_target_m": (40.0, 0.0),
        "width_m": 2.0,
        "background_rate_m_s": 1.0,
        "treated_rate_m_s": 0.25,
        "horizon_s": 87.0,
        "cost_units_per_m2": 1.0,
        "call_budget": 222,
        "evaluation_records": 108,
        "background_interface": {
            "left_rate_m_s": 1.0,
            "right_rate_m_s": 2.0,
            "interface_x_m": 0.0,
            "physical_points_m": [(-40, 0), (40, 0)],
            "both_directions": True,
            "solver_calls": 6,
        },
        "tolerances": {
            "baseline_octile_absolute_s": 1e-8,
            "vector_area_absolute_m2": 1e-8,
            "reference_lower_bound_absolute_s": 1e-8,
            "pilot_practical_arrival_relative": 0.02,
            "pilot_practical_exposed_weight": 0.05,
        },
        "capabilities": CAPABILITIES,
        "interpretation": [
            "reference is conditional continuum mathematics, not wildfire validation",
            "fixed eight-neighbour direction bias need not vanish with cell refinement",
            "physical points and widths are fixed across cell sizes and phases",
            "rotations transform ignition, target and treatment together in isotropic background",
            "grid bounds shift by phase; ample margin, no real construction envelope",
            "physical vector cost is common to both propagation representations",
            "raster area is a diagnostic, not a silently changed budget",
            "no optimization, annual probabilities, CVaR or heterogenous-fuel validation",
        ],
    }
    # All experiment sizes, endpoints and acceptance thresholds precede solves.
    write_json(output / "study.resolved.json", study)
    evidence = Evidence("synthetic", "P02 constant-rate continuum comparison", "D0 math only")
    records, timings = [], []
    for case, mode, half_length in CASES:
        profile = TreatmentProfile(
            "treated",
            mode,
            evidence,
            1,
            ConstantDirectionalSpreadRate(0.25) if mode == "M1" else None,
        )
        for angle in ANGLES:
            source, target = (rotate_point(p, angle) for p in ((-40.0, 0.0), (40.0, 0.0)))
            treatment = Treatment(
                "strip",
                rotate_point((0.0, -half_length), angle),
                rotate_point((0.0, half_length), angle),
                2.0,
                "treated",
            )
            plan = Plan(case, (treatment,))
            scenario = Scenario(case, (Ignition(source),), (Target("asset", target),), 87)
            physical_reference = (
                rectangle_detour_infimum_s((-40, 0), (40, 0), 2, half_length)
                if mode == "M0"
                else parallel_band_time_s((-40, 0), (40, 0), 2, 1, 0.25)
            )
            for cell_size in CELL_SIZES:
                cells = round(160 / cell_size)
                for phase_x, phase_y in PHASES:
                    grid = PlanningGrid(
                        (cells, cells),
                        cell_size,
                        -80 + phase_x * cell_size,
                        80 + phase_y * cell_size,
                    )
                    mapped_source = grid.centre(*grid.cell_at(source))
                    mapped_target = grid.centre(*grid.cell_at(target))
                    local_source = rotate_point(mapped_source, -angle)
                    local_target = rotate_point(mapped_target, -angle)
                    mapped_reference = (
                        rectangle_detour_infimum_s(local_source, local_target, 2, half_length)
                        if mode == "M0"
                        else parallel_band_time_s(local_source, local_target, 2, 1, 0.25)
                    )
                    if mode == "M1":
                        # Any end-bypass path must reach y=+/-L; its vertical
                        # travel alone lower-bounds its time at max rate 1.
                        bypass_lower = min(
                            abs(local_source[1] - y) + abs(local_target[1] - y)
                            for y in (-half_length, half_length)
                        )
                        if bypass_lower <= mapped_reference:
                            raise ValueError("finite M1 strip lacks valid infinite-band reference")
                    else:
                        bypass_lower = None
                    prepared = prepare_plan(grid, plan, (profile,))
                    raster_area = (
                        float(np.count_nonzero(centre_labels(grid, prepared) >= 0)) * cell_size**2
                    )
                    for representation in REPRESENTATIONS:
                        started = perf_counter()
                        pair = evaluate_pair(
                            grid=grid,
                            domain_mask=np.ones(grid.shape, dtype=bool),
                            background=ConstantDirectionalSpreadRate(1),
                            plan=plan,
                            scenario=scenario,
                            profiles=(profile,),
                            representation=representation,
                        )
                        timings.append(
                            {"record_index": len(records), "pair_wall_s": perf_counter() - started}
                        )
                        arrival = pair.intervention.target_arrivals_s[0]
                        baseline = pair.baseline.target_arrivals_s[0]
                        analytic_baseline = octile_time_s(mapped_source, mapped_target)
                        record = {
                            "case": case,
                            "mode": mode,
                            "angle_deg": angle,
                            "cell_size_m": cell_size,
                            "phase_xy": [phase_x, phase_y],
                            "grid": asdict(grid),
                            "physical_source_m": source,
                            "physical_target_m": target,
                            "treatment": asdict(treatment),
                            "mapped_source_m": mapped_source,
                            "mapped_target_m": mapped_target,
                            "source_mapping_distance_m": hypot(
                                mapped_source[0] - source[0], mapped_source[1] - source[1]
                            ),
                            "target_mapping_distance_m": hypot(
                                mapped_target[0] - target[0], mapped_target[1] - target[1]
                            ),
                            "representation": representation,
                            "arrival_s": arrival if isfinite(arrival) else None,
                            "graph_unreachable": not isfinite(arrival),
                            "baseline_arrival_s": baseline,
                            "analytic_octile_baseline_s": analytic_baseline,
                            "baseline_octile_error_s": baseline - analytic_baseline,
                            "mapped_euclidean_baseline_s": hypot(
                                mapped_source[0] - mapped_target[0],
                                mapped_source[1] - mapped_target[1],
                            ),
                            "physical_continuum_baseline_s": 80.0,
                            "physical_continuum_treated_s": physical_reference,
                            "mapped_continuum_treated_s": mapped_reference,
                            "mapping_reference_change_s": mapped_reference - physical_reference,
                            "mapped_arrival_error_s": arrival - mapped_reference
                            if isfinite(arrival)
                            else None,
                            "physical_arrival_relative_error": (arrival - physical_reference)
                            / physical_reference
                            if isfinite(arrival)
                            else None,
                            "baseline_exposed_weight": pair.baseline.exposed_weight,
                            "exposed_weight": pair.intervention.exposed_weight,
                            "exposure_reduction": pair.exposure_reduction,
                            "physical_continuum_exposed_weight": float(physical_reference <= 87),
                            "physical_area_m2": pair.intervention.treated_area_m2,
                            "physical_area_error_m2": pair.intervention.treated_area_m2
                            - 4 * half_length,
                            "raster_area_m2": raster_area,
                            "raster_area_error_m2": raster_area - 4 * half_length,
                            "cost_units": pair.intervention.cost_units,
                            "end_bypass_time_lower_bound_s": bypass_lower,
                            "boundary_contact_at_horizon": (
                                pair.intervention.boundary_contact_at_horizon
                            ),
                        }
                        records.append(record)
    write_json(output / "evaluations.json", records)
    write_json(output / "timings.json", timings)
    background_records = background_interface_records()
    write_json(output / "background_interface.json", background_records)
    vectors = [r for r in records if r["representation"] == "vector"]
    checks = {
        "analytic_graph_baseline_pass": all(
            abs(r["baseline_octile_error_s"]) <= 1e-8 for r in records
        ),
        "vector_area_pass": all(abs(r["physical_area_error_m2"]) <= 1e-8 for r in records),
        "vector_continuum_lower_bound_pass": all(
            r["mapped_arrival_error_s"] is not None and r["mapped_arrival_error_s"] >= -1e-8
            for r in vectors
        ),
        "vector_physical_2pct_accuracy_pass": all(
            r["physical_arrival_relative_error"] is not None
            and abs(r["physical_arrival_relative_error"]) <= 0.02
            for r in vectors
        ),
        "vector_physical_horizon_decision_pass": all(
            abs(r["exposed_weight"] - r["physical_continuum_exposed_weight"]) <= 0.05
            for r in vectors
        ),
    }
    summary = {
        "evaluation_records": len(records),
        "solver_calls": 2 * len(records) + len(background_records),
        "checks": checks,
        "p03_continuum_planning_gate": "not_passed" if not all(checks.values()) else "D0_only",
        "worst_vector_physical_relative_error": max(
            abs(r["physical_arrival_relative_error"])
            for r in vectors
            if r["physical_arrival_relative_error"] is not None
        ),
        "worst_centre_mask_area_absolute_error_m2": max(
            abs(r["raster_area_error_m2"]) for r in records
        ),
        "remaining": [
            "general heterogeneous background integration",
            "independent field validation",
            "P00 novelty matrix",
            "stencil/continuum consistency",
        ],
    }
    write_json(output / "validation.json", summary)
    root = Path(__file__).resolve().parents[2]
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "shapely": shapely.__version__,
        "source_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                *(root / "src/pyfireca/planning").glob("*.py"),
                root / "src/pyfireca/arrival.py",
                root / "src/pyfireca/neighborhood.py",
                root / "src/pyfireca/propagation.py",
            ]
        },
    }
    try:
        provenance["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        provenance["dirty_status"] = subprocess.check_output(
            ["git", "status", "--short"], cwd=root, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        provenance["head"] = None
    for relative in provenance["source_sha256"]:
        snapshot = output / "source_snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((root / relative).read_bytes())
    write_json(output / "provenance.json", provenance)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_study(args.output), indent=2))
