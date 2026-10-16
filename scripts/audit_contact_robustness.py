"""Frozen multi-material contact robustness experiment, not DEM calibration.

Compare perturbations against each model's own baseline. Radiation stays frozen
to isolate contact; independent-cell noise is a stress test, not a DEM error law.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def graph(request):
    return FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )


def audit(validation, output):
    output.mkdir(parents=True, exist_ok=False)
    parent = validation / "report.json"
    points = json.loads(parent.read_text())["points"]
    plan = {
        "scenarios": points,
        "models_half_extent_m": [0.0, 0.15],
        "seed": 20261007,
        "independent_noise_amplitudes_m": [0.0001, 0.001, 0.01],
        "signs": [-1, 1],
        "smooth_planar_amplitude_m": 0.01,
        "max_dt_s": 1.0,
        "baseline_time_step_control_s": 0.25,
        "expected_simulations": len(points) * 2 * 10,
        "tolerances": {
            "small_ignited_set_exact_below": 50,
            "large_set_min_jaccard": 0.98,
            "p95_relative_ignition_difference": 0.05,
            "relative_consumed_fuel_difference": 0.02,
        },
        "interpretation": "predeclared engineering screening, not empirical validation",
        "radiation_frozen": True,
        "default_modified": False,
        "crop_external_sources_excluded": True,
        "parent_report_sha256": hashlib.sha256(parent.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    # Save criteria before running; never select tolerance after seeing outcomes.
    (output / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    records = []
    inputs = {}
    for index, point in enumerate(points):
        name = point["name"]
        case = validation / "crops" / name / "base"
        r = load_v3_case_bundle(case).request
        inputs[name] = {
            "case": str(case.resolve()),
            "duration_s": r.scenario.end_time_s,
            "hashes": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(case.iterdir())
                if p.is_file()
            },
        }
        noise = np.random.default_rng(plan["seed"] + index).uniform(-1, 1, r.grid.shape)
        rr, cc = np.indices(r.grid.shape, dtype=np.float64)
        plane = rr - (r.grid.height - 1) / 2 + cc - (r.grid.width - 1) / 2
        plane /= np.max(np.abs(plane))
        for extent in plan["models_half_extent_m"]:
            model = "point" if extent == 0 else "band015"
            q = replace(
                r,
                flame_contact=replace(r.flame_contact, receiver_vertical_half_extent_m=extent),
                scenario=replace(r.scenario, max_dt_s=plan["max_dt_s"]),
            )
            base_graph = graph(q)
            baseline = run_v3_simulation(q, retain_snapshots=False)
            baseline_check = check_final(q, baseline)
            jobs = [("baseline", q)]
            for amplitude in plan["independent_noise_amplitudes_m"]:
                for sign in plan["signs"]:
                    jobs.append(
                        (
                            f"iid_{sign * amplitude:g}m",
                            replace(
                                q,
                                ground_elevation_m=r.ground_elevation_m + sign * amplitude * noise,
                            ),
                        )
                    )
            for sign in plan["signs"]:
                jobs.append(
                    (
                        f"plane_{sign * plan['smooth_planar_amplitude_m']:g}m",
                        replace(
                            q,
                            ground_elevation_m=r.ground_elevation_m
                            + sign * plan["smooth_planar_amplitude_m"] * plane,
                        ),
                    )
                )
            jobs.append(("dt025", replace(q, scenario=replace(q.scenario, max_dt_s=0.25))))
            for control, request in jobs:
                if control == "baseline":
                    result, final_check = baseline, baseline_check
                else:
                    result = run_v3_simulation(request, retain_snapshots=False)
                    final_check = check_final(request, result)
                op = graph(request)
                comparison = compare_steps(result, baseline)
                boundary = np.zeros(r.grid.shape, dtype=bool)
                boundary[[0, -1], :] = True
                boundary[:, [0, -1]] = True
                artifact = f"{name}-{model}-{control}.npz"
                np.savez_compressed(
                    output / artifact,
                    ignition_time_s=result.final_state.ignition_time_s,
                    fuel_remaining_kg=result.final_state.fuel_remaining_kg,
                )
                record = {
                    "scenario": name,
                    "model": model,
                    "control": control,
                    "half_extent_m": extent,
                    "max_dt_s": request.scenario.max_dt_s,
                    "final_arrays": artifact,
                    **final_check,
                    "comparison_to_same_model_baseline": comparison,
                    "changed_contact_edge_slots": int((op.neighbors != base_graph.neighbors).sum()),
                    "max_geometry_weight_change": float(
                        np.max(np.abs(op.geometry_fraction - base_graph.geometry_fraction))
                    ),
                    "ignited_crop_boundary_cells": int(
                        np.isfinite(result.final_state.ignition_time_s[boundary]).sum()
                    ),
                }
                records.append(record)
                (output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "completed": len(records),
                            "scenario": name,
                            "model": model,
                            "control": control,
                            "ignited": record["ignited_cells"],
                            "engineering_pass": comparison["engineering_tolerance_pass"],
                        }
                    ),
                    flush=True,
                )
    for name, entry in inputs.items():
        for filename, digest in entry["hashes"].items():
            if hashlib.sha256((Path(entry["case"]) / filename).read_bytes()).hexdigest() != digest:
                raise AssertionError(f"source changed during audit: {name}/{filename}")
    summary = []
    for point in points:
        for model in ("point", "band015"):
            subset = [x for x in records if x["scenario"] == point["name"] and x["model"] == model]
            perturbed = [x for x in subset if x["control"] not in {"baseline", "dt025"}]
            baseline = next(x for x in subset if x["control"] == "baseline")
            summary.append(
                {
                    "scenario": point["name"],
                    "model": model,
                    "baseline_ignited": baseline["ignited_cells"],
                    "perturbed_ignited_range": [
                        min(x["ignited_cells"] for x in perturbed),
                        max(x["ignited_cells"] for x in perturbed),
                    ],
                    "perturbations_passed": sum(
                        x["comparison_to_same_model_baseline"]["engineering_tolerance_pass"]
                        for x in perturbed
                    ),
                    "perturbations_total": len(perturbed),
                    "dt025_pass": next(x for x in subset if x["control"] == "dt025")[
                        "comparison_to_same_model_baseline"
                    ]["engineering_tolerance_pass"],
                }
            )
    report = {
        "plan": plan,
        "inputs": inputs,
        "runs": records,
        "summary": summary,
        "source_hashes_unchanged": True,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation", type=Path, default=ROOT / "runs/gaoyou-core-validation-20261006"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.validation, args.output)
