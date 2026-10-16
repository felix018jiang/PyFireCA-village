"""Observe exact thermal steps in an isolated diagnostic process; formulas unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.exposure import SourceIndexedExposure
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.physics_v2 import advance_surface_heating_and_evaporation
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle


class ContactObserver:
    def __init__(self, request, selected):
        self.request = request
        self.selected = selected
        self.operator = FlameContactOperator.build(
            request.grid,
            request.burnable_mask,
            request.parameters,
            request.flame_contact,
            request.ground_elevation_m,
            request.material_index,
        )
        self.parameters = self.operator.parameters
        self.exposure = SourceIndexedExposure.from_edges(request.exposure_edges)
        self.time_s = 0.0
        self.records = []

    def transfer(self, hrr, temperature, susceptible):
        transfer = self.operator.transfer(hrr, temperature, susceptible)
        self.susceptible_ids = np.flatnonzero(susceptible)
        self.transfer_result = transfer
        self.absorbed_power = (
            self.exposure.target_power_W(
                (hrr * self.request.parameters.radiative_fraction).ravel()
            ).reshape(self.request.grid.shape)
            * self.request.parameters.absorptivity
        )
        return transfer

    def advance(self, temperature, water, dry_mass, heat, net_flux, dt_s, **kwargs):
        after, next_water = advance_surface_heating_and_evaporation(
            temperature, water, dry_mass, heat, net_flux, dt_s, **kwargs
        )
        for row, col in self.selected:
            tid = row * self.request.grid.width + col
            slots = np.flatnonzero(self.susceptible_ids == tid)
            if not slots.size:
                continue
            k = slots[0]
            area = self.request.parameters.covered_area_m2[row, col]
            absorbed = self.absorbed_power[row, col]
            contact = self.transfer_result.target_power_W[row, col]
            net = net_flux[k] * area
            self.records.append(
                dict(
                    crop_row_col=[row, col],
                    step_end_s=self.time_s + dt_s,
                    dt_s=dt_s,
                    temperature_before_K=float(temperature[k]),
                    temperature_after_K=float(after[k]),
                    water_before_kg=float(water[k] * area),
                    water_after_kg=float(next_water[k] * area),
                    absorbed_radiation_W=float(absorbed),
                    contact_W=float(contact),
                    net_heating_W=float(net),
                    surface_loss_W=float(absorbed + contact - net),
                )
            )
        self.time_s += dt_s
        return after, next_water


def audit(previous, output):
    if output.exists():
        raise FileExistsError(output)
    report = json.loads((previous / "report.json").read_text())
    r = load_v3_case_bundle(report["inputs"]["tree"]["case"]).request
    i = next(i for i, p in enumerate(report["plan"]["scenarios"]) if p["name"] == "tree")
    noise = np.random.default_rng(report["plan"]["seed"] + i).uniform(-1, 1, r.grid.shape)
    cases = []
    for amplitude in (0, 0.001):
        q = replace(
            r,
            ground_elevation_m=r.ground_elevation_m + amplitude * noise,
            flame_contact=replace(r.flame_contact, receiver_vertical_half_extent_m=0.15),
        )
        observer = ContactObserver(q, [(21, 24), (22, 24)])
        with patch(
            "pyfireca.village.solver_v3.advance_surface_heating_and_evaporation", observer.advance
        ):
            x = _run_resolved_v3_simulation(q, contact=observer, retain_snapshots=False)
        if abs(observer.time_s - q.scenario.end_time_s) > 1e-6:
            raise AssertionError("thermal observer clock differs from solver clock")
        control = "baseline" if amplitude == 0 else "iid_0.001m"
        with np.load(previous / f"tree-band015-{control}.npz") as original:
            if not np.array_equal(
                x.final_state.ignition_time_s, original["ignition_time_s"], equal_nan=True
            ) or not np.array_equal(x.final_state.fuel_remaining_kg, original["fuel_remaining_kg"]):
                raise AssertionError("observer changed the original simulation result")
        cells = []
        for row, col in observer.selected:
            steps = [s for s in observer.records if s["crop_row_col"] == [row, col]]
            threshold = q.parameters.ignition_temperature_K[row, col]
            onset = next((s for s in steps if s["temperature_after_K"] >= threshold), None)
            at = x.final_state.ignition_time_s[row, col]
            if onset is not None and onset["step_end_s"] != at:
                raise AssertionError("observed threshold crossing differs from ignition clock")
            cells.append(
                dict(
                    crop_row_col=[row, col],
                    material=int(q.material_index[row, col]),
                    ignition_time_s=float(at) if np.isfinite(at) else None,
                    exact_susceptible_temperature_max_K=max(
                        s["temperature_after_K"] for s in steps
                    ),
                    absorbed_radiation_J=sum(s["absorbed_radiation_W"] * s["dt_s"] for s in steps),
                    contact_J=sum(s["contact_W"] * s["dt_s"] for s in steps),
                    surface_loss_J=sum(s["surface_loss_W"] * s["dt_s"] for s in steps),
                    threshold_crossing_step=onset,
                )
            )
        cases.append(
            dict(
                noise_amplitude_m=amplitude,
                **check_final(q, x),
                cells=cells,
                exact_step_records=observer.records,
            )
        )
    result = dict(
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        parent_report_sha256=hashlib.sha256((previous / "report.json").read_bytes()).hexdigest(),
        diagnostic_observation_only=True,
        original_final_arrays_identical=True,
        cases=cases,
    )
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    for case in cases:
        print(json.dumps(dict(noise_m=case["noise_amplitude_m"], cells=case["cells"])), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.previous, args.output)
