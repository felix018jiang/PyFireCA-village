"""Run the frozen P01 synthetic study and write new, deterministic artifacts.

    python examples/planning/single_line.py --output runs/planning-p01-example

Requires an installed/editable PyFireCA and its ``planning`` extra. This is a
local-metre mathematical experiment, not a wildfire calibration or optimizer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import mapping

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.adapters import CAPABILITIES, evaluate_pair
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
from pyfireca.planning.geometry import treatment_footprint


def run_study(output: Path) -> dict:
    """Freeze the study before evaluation; reject reuse of an output directory."""
    output.mkdir(parents=True, exist_ok=False)
    grid = PlanningGrid((9, 11), 10, y_max_m=90)
    scenario = Scenario("single-event", (Ignition((5, 45)),), (Target("asset", (105, 45)),), 105)
    evidence = Evidence("synthetic", "P01 independent constant-rate calculation", "D0 math only")
    profiles = (
        TreatmentProfile("ideal", "M0", evidence, 1),
        TreatmentProfile("slow", "M1", evidence, 1, ConstantDirectionalSpreadRate(0.25)),
    )
    plans = [Plan("baseline")]
    for mode in ("ideal", "slow"):
        for width in (0.02, 2.0, 4.0, 8.0):
            plans.append(
                Plan(f"{mode}-w{width:g}", (Treatment("full", (55, 0), (55, 90), width, mode),))
            )
    top = Treatment("top", (55, 45), (55, 90), 2, "ideal")
    bottom = Treatment("bottom", (55, 0), (55, 45), 2, "ideal")
    plans.extend(
        (Plan("top-only", (top,)), Plan("bottom-only", (bottom,)), Plan("connected", (top, bottom)))
    )
    study = {
        "schema": "planning-p01-study-1",
        "evidence_level": "D0_synthetic",
        "question": "Physical strip crossing, endpoint bypass and connected ideal barriers",
        "grid": asdict(grid),
        "scenario": asdict(scenario),
        "plans": [asdict(plan) for plan in plans],
        "profiles": [
            {
                "profile_id": p.profile_id,
                "mode": p.mode,
                "evidence": asdict(p.evidence),
                "cost_units_per_m2": p.cost_units_per_m2,
                "constant_rate_m_s": 0.25 if p.mode == "M1" else None,
            }
            for p in profiles
        ],
        "background_constant_rate_m_s": 1.0,
        "capabilities": CAPABILITIES,
        "target_endpoint": "fixed point mapped to containing cell, weight=1, horizon=105s",
        "synthetic_practical_difference_weight": 0.05,
        "arrival_absolute_tolerance_s": 1e-9,
        "area_absolute_tolerance_m2": 1e-9,
        "probability": "single deterministic conditional event, no annual or tail-risk inference",
        "construction_envelope": "whole local rectangle; no real access/land-use feasibility",
        "limits": [
            "no calibrated fuel treatment",
            "no spotting or thermal crossing",
            "no GIS CRS",
            "no optimization or convergence claim",
        ],
    }
    (output / "study.resolved.json").write_text(json.dumps(study, indent=2) + "\n")
    records = []
    features = []
    for plan in plans:
        pair = evaluate_pair(
            grid=grid,
            domain_mask=np.ones(grid.shape, dtype=bool),
            background=ConstantDirectionalSpreadRate(1),
            plan=plan,
            scenario=scenario,
            profiles=profiles,
        )
        result = pair.intervention
        arrival = result.target_arrivals_s[0]
        # JSON cannot represent infinity; null is explicitly paired with the
        # graph-unreachable flag, never silently interpreted as zero delay.
        records.append(
            {
                "plan_id": plan.plan_id,
                "target_arrival_s": arrival if np.isfinite(arrival) else None,
                "target_graph_unreachable": not np.isfinite(arrival),
                "target_exposed_at_horizon": bool(arrival <= scenario.horizon_s),
                "exposed_weight": result.exposed_weight,
                "exposure_reduction": pair.exposure_reduction,
                "horizon_capped_target_delay_s": pair.horizon_capped_target_delay_s[0],
                "treated_area_m2": result.treated_area_m2,
                "cost_units": result.cost_units,
                "reached_cell_count_at_horizon": result.reached_cell_count_at_horizon,
                "unreachable_burnable_cell_count": result.unreachable_burnable_cell_count,
                "boundary_contact_at_horizon": result.boundary_contact_at_horizon,
                "arrival_array_sha256": hashlib.sha256(
                    np.asarray(result.arrival_times_s, dtype="<f8").tobytes()
                ).hexdigest(),
            }
        )
        np.save(output / f"{plan.plan_id}.arrival.npy", result.arrival_times_s)
        for treatment in plan.treatments:
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(treatment_footprint(treatment)),
                    "properties": {"plan_id": plan.plan_id, **asdict(treatment)},
                }
            )
    (output / "evaluations.json").write_text(json.dumps(records, indent=2, allow_nan=False) + "\n")
    # Local metre geometry is deliberately not published as WGS84 GeoJSON.
    (output / "treatments.local-metre.json").write_text(
        json.dumps({"coordinate_frame": "local_metre", "features": features}, indent=2) + "\n"
    )
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
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return {"study_count": len(records), "output": str(output.resolve()), "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = run_study(args.output)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))
