"""Explain failed robustness cases without changing parameters or pass criteria."""

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


class SampledTemperatureMaximum:
    def __init__(self, shape):
        self.maximum = np.full(shape, -np.inf)

    def append(self, snapshot):
        np.maximum(
            self.maximum,
            np.where(snapshot.temperature_valid_mask, snapshot.surface_temperature_K, -np.inf),
            out=self.maximum,
        )


def audit(folder):
    report_path = folder / "report.json"
    report = json.loads(report_path.read_text())
    tree = load_v3_case_bundle(report["inputs"]["tree"]["case"]).request
    index = next(i for i, p in enumerate(report["plan"]["scenarios"]) if p["name"] == "tree")
    noise = np.random.default_rng(report["plan"]["seed"] + index).uniform(-1, 1, tree.grid.shape)
    rows = []
    for end in (1260, 1800):
        baseline = None
        for amplitude in (0, -0.001, 0.001, -0.01, 0.01):
            q = replace(
                tree,
                scenario=replace(tree.scenario, end_time_s=end),
                flame_contact=replace(tree.flame_contact, receiver_vertical_half_extent_m=0.15),
                ground_elevation_m=tree.ground_elevation_m + amplitude * noise,
            )
            result = run_v3_simulation(q, retain_snapshots=False)
            if amplitude == 0:
                baseline = result
            rows.append(
                {
                    "end_time_s": end,
                    "signed_noise_amplitude_m": amplitude,
                    **check_final(q, result),
                    "comparison": compare_steps(result, baseline),
                }
            )
            print(
                json.dumps(
                    {"tree_end_s": end, "noise_m": amplitude, "ignited": rows[-1]["ignited_cells"]}
                ),
                flush=True,
            )
    r = load_v3_case_bundle(report["inputs"]["vegetation_building_interface"]["case"]).request
    event = r.scenario.ignitions[0]
    sid = event.row * r.grid.width + event.col
    interface = []
    for extent in (0, 0.15):
        q = replace(
            r,
            flame_contact=replace(r.flame_contact, receiver_vertical_half_extent_m=extent),
            scenario=replace(r.scenario, end_time_s=1800),
        )
        op = FlameContactOperator.build(
            q.grid,
            q.burnable_mask,
            q.parameters,
            q.flame_contact,
            q.ground_elevation_m,
            q.material_index,
        )
        hrr = np.zeros(q.grid.shape)
        hrr[event.row, event.col] = q.parameters.peak_hrr_W[event.row, event.col]
        susceptible = q.burnable_mask.copy()
        susceptible[event.row, event.col] = False
        temperature = np.full(q.grid.shape, q.scenario.ambient_temperature_K)
        transfer = op.transfer(hrr, temperature, susceptible)
        sink = SampledTemperatureMaximum(q.grid.shape)
        result = run_v3_simulation(q, snapshot_sink=sink, retain_snapshots=False)
        targets = []
        for nr, nc in (
            (event.row - 1, event.col),
            (event.row + 1, event.col),
            (event.row, event.col - 1),
            (event.row, event.col + 1),
        ):
            tid = nr * q.grid.width + nc
            slots = np.flatnonzero(op.neighbors[sid] == tid)
            ignition = result.final_state.ignition_time_s[nr, nc]
            targets.append(
                {
                    "crop_row_col": [nr, nc],
                    "material": int(q.material_index[nr, nc]),
                    "terrain_delta_m": float(
                        q.ground_elevation_m[nr, nc] - q.ground_elevation_m[event.row, event.col]
                    ),
                    "source_geometry_weight": float(op.geometry_fraction[sid, slots[0]])
                    if slots.size
                    else 0.0,
                    "isolated_source_peak_contact_power_at_ambient_W": float(
                        transfer.target_power_W[nr, nc]
                    ),
                    "sampled_valid_temperature_max_K": float(sink.maximum[nr, nc]),
                    "ignition_temperature_K": float(q.parameters.ignition_temperature_K[nr, nc]),
                    "ignition_time_s": float(ignition) if np.isfinite(ignition) else None,
                }
            )
        interface.append(
            {
                "half_extent_m": extent,
                "end_time_s": 1800,
                "source_material": int(q.material_index[event.row, event.col]),
                "temperature_sampling_s": q.sample_interval_s,
                "neighbors": targets,
                **check_final(q, result),
            }
        )
        print(
            json.dumps({"interface_extent_m": extent, "ignited": interface[-1]["ignited_cells"]}),
            flush=True,
        )
    followup = {
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "tree_horizon_checks": rows,
        "interface_source_checks": interface,
        "default_modified": False,
        "scope": "contact-only diagnostic; isolated peak powers are not trajectory maxima",
    }
    (folder / "followup.json").write_text(json.dumps(followup, indent=2, ensure_ascii=False) + "\n")
    return followup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    audit(parser.parse_args().folder)
