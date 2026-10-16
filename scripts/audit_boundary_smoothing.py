"""Screen a fixed numerical boundary transition and trace threshold-sensitive cells."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from contact_boundary_experiment import build_smoothed_contact

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation, run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


class TemperatureTrace:
    def __init__(self, shape):
        self.maximum = np.full(shape, -np.inf)

    def append(self, snapshot):
        np.maximum(
            self.maximum,
            np.where(snapshot.temperature_valid_mask, snapshot.surface_temperature_K, -np.inf),
            out=self.maximum,
        )


def audit(previous, output):
    output.mkdir(parents=True, exist_ok=False)
    report_path = previous / "report.json"
    previous_report = json.loads(report_path.read_text())
    plan = dict(previous_report["plan"])
    plan.update(
        model="experimental_point_boundary_smoothstep",
        transition_half_width_m=0.01,
        models_half_extent_m=[0],
        expected_simulations=50,
        rationale="fixed 1 cm numerical transition; not DEM accuracy or physical receiver extent",
        original_point_areas_preserved_outside_transition=True,
        model_selection_based_on_more_spread=False,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        helper_sha256=hashlib.sha256(
            Path(__file__).with_name("contact_boundary_experiment.py").read_bytes()
        ).hexdigest(),
        parent_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    records, baseline_traces = [], {}
    for index, point in enumerate(plan["scenarios"]):
        name = point["name"]
        case = Path(previous_report["inputs"][name]["case"])
        r = load_v3_case_bundle(case).request
        for filename, digest in previous_report["inputs"][name]["hashes"].items():
            if hashlib.sha256((case / filename).read_bytes()).hexdigest() != digest:
                raise AssertionError(f"source changed: {name}/{filename}")
        z = r.ground_elevation_m
        noise = np.random.default_rng(plan["seed"] + index).uniform(-1, 1, r.grid.shape)
        rr, cc = np.indices(r.grid.shape, dtype=float)
        plane = rr - (r.grid.height - 1) / 2 + cc - (r.grid.width - 1) / 2
        plane /= np.max(np.abs(plane))
        jobs = [("baseline", r)]
        for amplitude in plan["independent_noise_amplitudes_m"]:
            for sign in plan["signs"]:
                jobs.append(
                    (
                        f"iid_{sign * amplitude:g}m",
                        replace(r, ground_elevation_m=z + sign * amplitude * noise),
                    )
                )
        for sign in plan["signs"]:
            jobs.append(
                (
                    f"plane_{sign * plan['smooth_planar_amplitude_m']:g}m",
                    replace(
                        r, ground_elevation_m=z + sign * plan["smooth_planar_amplitude_m"] * plane
                    ),
                )
            )
        jobs.append(("dt025", replace(r, scenario=replace(r.scenario, max_dt_s=0.25))))
        baseline = None
        base_op = build_smoothed_contact(r, plan["transition_half_width_m"])
        for control, q in jobs:
            op = build_smoothed_contact(q, plan["transition_half_width_m"])
            sink = TemperatureTrace(q.grid.shape)
            x = _run_resolved_v3_simulation(
                q, contact=op, snapshot_sink=sink, retain_snapshots=False
            )
            if control == "baseline":
                baseline = x
                baseline_traces[name] = sink.maximum
            record = dict(
                scenario=name,
                control=control,
                **check_final(q, x),
                comparison=compare_steps(x, baseline),
                max_geometry_weight_change=float(
                    np.max(np.abs(op.geometry_fraction - base_op.geometry_fraction))
                ),
            )
            np.savez_compressed(
                output / f"{name}-{control}.npz",
                ignition_time_s=x.final_state.ignition_time_s,
                maximum_sampled_valid_temperature_K=sink.maximum,
                absorbed_radiation_J=x.final_state.absorbed_radiation_J,
                received_convection_J=x.final_state.received_convection_J,
            )
            records.append(record)
            print(
                json.dumps(
                    dict(
                        scenario=name,
                        control=control,
                        ignited=record["ignited_cells"],
                        passed=record["comparison"]["engineering_tolerance_pass"],
                    )
                ),
                flush=True,
            )
    # Detailed traces for the previously unstable tree-band case at matched geometry.
    tree = load_v3_case_bundle(previous_report["inputs"]["tree"]["case"]).request
    index = next(i for i, p in enumerate(plan["scenarios"]) if p["name"] == "tree")
    noise = np.random.default_rng(plan["seed"] + index).uniform(-1, 1, tree.grid.shape)
    band_results = []
    for amplitude in (0, 0.001):
        q = replace(
            tree,
            ground_elevation_m=tree.ground_elevation_m + amplitude * noise,
            flame_contact=replace(tree.flame_contact, receiver_vertical_half_extent_m=0.15),
        )
        sink = TemperatureTrace(q.grid.shape)
        x = run_v3_simulation(q, snapshot_sink=sink, retain_snapshots=False)
        op = FlameContactOperator.build(
            q.grid,
            q.burnable_mask,
            q.parameters,
            q.flame_contact,
            q.ground_elevation_m,
            q.material_index,
        )
        band_results.append((q, x, sink, op))
    a, b = band_results
    changed = np.isfinite(a[1].final_state.ignition_time_s) ^ np.isfinite(
        b[1].final_state.ignition_time_s
    )
    traces = []
    for row, col in np.argwhere(changed):
        tid = row * tree.grid.width + col
        stages = []
        for q, x, sink, op in band_results:
            incoming = np.argwhere(op.neighbors == tid)
            sources = []
            for source, slot in incoming:
                sr, sc = divmod(int(source), tree.grid.width)
                at = x.final_state.ignition_time_s[sr, sc]
                sources.append(
                    dict(
                        crop_row_col=[sr, sc],
                        weight=float(op.geometry_fraction[source, slot]),
                        ignition_time_s=float(at) if np.isfinite(at) else None,
                    )
                )
            at = x.final_state.ignition_time_s[row, col]
            stages.append(
                dict(
                    ignition_time_s=float(at) if np.isfinite(at) else None,
                    sampled_temperature_max_K=float(sink.maximum[row, col]),
                    ignition_threshold_K=float(q.parameters.ignition_temperature_K[row, col]),
                    received_convection_J=float(x.final_state.received_convection_J[row, col]),
                    absorbed_radiation_J=float(x.final_state.absorbed_radiation_J[row, col]),
                    heated_water_remaining_kg=float(
                        x.final_state.heated_water_remaining_kg[row, col]
                    ),
                    incoming_sources=sources,
                )
            )
        traces.append(
            dict(
                crop_row_col=[int(row), int(col)],
                material=int(tree.material_index[row, col]),
                unperturbed=stages[0],
                plus_1mm=stages[1],
            )
        )
    summary = []
    for point in plan["scenarios"]:
        subset = [x for x in records if x["scenario"] == point["name"]]
        perturbed = [x for x in subset if x["control"] not in {"baseline", "dt025"}]
        summary.append(
            dict(
                scenario=point["name"],
                baseline_ignited=subset[0]["ignited_cells"],
                perturbation_range=[
                    min(x["ignited_cells"] for x in perturbed),
                    max(x["ignited_cells"] for x in perturbed),
                ],
                perturbations_passed=sum(
                    x["comparison"]["engineering_tolerance_pass"] for x in perturbed
                ),
                perturbations_total=len(perturbed),
                dt025_pass=subset[-1]["comparison"]["engineering_tolerance_pass"],
            )
        )
    result = dict(
        plan=plan,
        runs=records,
        summary=summary,
        tree_band_threshold_traces=traces,
        default_modified=False,
        simulation_count=len(records) + 2,
    )
    (output / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, default=ROOT / "runs/contact-robustness-20261007")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.previous, args.output)
