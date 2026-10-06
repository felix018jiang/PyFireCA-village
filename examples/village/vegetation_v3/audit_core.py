"""One-command checks/replays of the four basic fire-CA functions.

python examples/village/vegetation_v3/audit_core.py --output runs/core-demo
Uses existing V3 material defaults, radiation and bounded neighbor contact.
No components, dynamic wind, finite-surface quadrature or firebrands.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.outputs_v3 import write_v3_run_outputs
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.vegetation_prepare import LANDCOVER_IDS
from pyfireca.village.workflow_v3 import write_v3_case_bundle

DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("core_fixture", DIRECTORY / "audit_spread.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

CASES = {
    "grass_chain": (("grass",) * 4, 1200, False),
    "shrub_chain": (("shrub",) * 4, 1200, False),
    "tree_chain": (("tree_equivalent",) * 3, 1800, False),
    "vegetation_to_building": (("shrub", "building"), 1200, True),
    "inert_gap": (("grass", "bare_or_road", "grass"), 1200, False),
    "heat_without_ignition": (("grass", "grass"), 300, False),
}


def make_core_request(name, *, max_dt_s=0.5):
    if name not in CASES:
        raise ValueError(f"unknown core scenario: {name}")
    labels, end, low = CASES[name]
    request = replace(
        fixture.make_request(labels, max_dt_s=max_dt_s, end_time_s=end, low_building_receiver=low),
        sample_interval_s=10,
        flame_contact=load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml"),
    )
    if name == "heat_without_ignition":
        threshold = request.parameters.ignition_temperature_K.copy()
        threshold[0, 1] = 2000  # Explicit threshold control; no change to heat input.
        request = replace(
            request, parameters=replace(request.parameters, ignition_temperature_K=threshold)
        )
    return request


def check_core_result(name, request, result):
    state = result.final_state
    initial = result.initial_fuel_kg.ravel()
    fuel = state.fuel_remaining_kg.ravel()
    times = state.ignition_time_s.ravel()
    burnable = request.burnable_mask.ravel()
    if np.any(fuel < 0) or np.any(fuel > initial + 1e-9):
        raise AssertionError("fuel must remain within its original mass budget")
    if any(
        np.any(
            np.diff(np.asarray([s.fuel_remaining_kg.ravel()[i] for s in result.snapshots])) > 1e-9
        )
        for i in range(initial.size)
    ):
        raise AssertionError("fuel must not increase during replay")
    if np.any(np.isfinite(times[~burnable])) or np.any(fuel[~burnable]):
        raise AssertionError("inert material cannot ignite or contain burning fuel")
    budget = result.energy_budget
    errors = dict(
        fuel_release=abs(
            float(np.sum((initial - fuel) * request.parameters.heat_of_combustion_J_kg_dry.ravel()))
            - budget.released_J
        ),
        release_split=abs(
            budget.released_J - budget.radiative_emitted_J - budget.non_radiative_released_J
        ),
        radiation_split=abs(
            budget.radiative_emitted_J
            - budget.radiative_intercepted_J
            - budget.radiative_unintercepted_J
        ),
        contact_split=abs(
            budget.non_radiative_released_J
            - budget.convective_transferred_J
            - budget.nonradiative_untransferred_J
        ),
    )
    if max(errors.values()) > 1e-5 + 1e-9 * budget.released_J:
        raise AssertionError("basic simulation energy budget failed")
    peaks = np.max(np.stack([s.surface_temperature_K for s in result.snapshots]), axis=0).ravel()
    if name in ("grass_chain", "shrub_chain", "tree_chain", "vegetation_to_building"):
        if not np.all(np.isfinite(times)) or not np.all(np.diff(times) > 0):
            raise AssertionError("each neighboring fuel cell must ignite after its source")
        if (
            not np.all(state.state == 4)
            or not np.all(fuel == 0)
            or np.any(state.interval_mean_hrr_W)
        ):
            raise AssertionError("all ignited fuel must burn out and stop releasing heat")
    if name == "inert_gap" and (np.isfinite(times[1:]).any() or fuel[2] != initial[2]):
        raise AssertionError("this inert-gap control must not ignite downstream fuel")
    if name == "heat_without_ignition" and (
        np.isfinite(times[1]) or fuel[1] != initial[1] or peaks[1] <= 300
    ):
        raise AssertionError("subthreshold target must heat up without igniting")
    return dict(
        name=name,
        material_labels=list(CASES[name][0]),
        ignition_time_s=[float(t) if np.isfinite(t) else None for t in times],
        burnout_time_s=[float(t) if np.isfinite(t) else None for t in state.burnout_time_s.ravel()],
        final_state=state.state.ravel().tolist(),
        final_fuel_kg=fuel.tolist(),
        peak_surface_temperature_K=peaks.tolist(),
        initial_material_moisture_dry_basis=request.parameters.moisture_content_dry_basis.ravel().tolist(),
        energy_budget=asdict(budget),
        energy_closure_errors_J=errors,
        known_scope="synthetic preset, not measured spread rate or whole-building loss",
    )


def audit_core(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    results = []
    for name in CASES:
        request = make_core_request(name)
        result = run_v3_simulation(request)
        record = check_core_result(name, request, result)
        metadata = dict(
            case_id=name,
            evidence="synthetic",
            purpose="basic_fire_ca_acceptance",
            building_receiver_assumption="0.2 m combustible exterior receiver"
            if name == "vegetation_to_building"
            else None,
            threshold_control_K=2000 if name == "heat_without_ignition" else None,
            simulation_scope="single cell state; preset moisture; radiation and contact",
        )
        display = {
            "grass": "草地",
            "shrub": "灌木",
            "tree_equivalent": "树木等效材质",
            "building": "建筑可燃材质",
            "bare_or_road": "裸地/道路",
        }
        labels = {i: display.get(label, label) for i, label in enumerate(LANDCOVER_IDS)}
        write_v3_case_bundle(
            request,
            output / name / "case",
            material_labels=labels,
            landcover_labels=labels,
            metadata=metadata,
        )
        write_v3_run_outputs(
            result,
            request,
            output / name / "run",
            material_labels=labels,
            landcover_labels=labels,
            scenario_metadata=metadata,
        )
        results.append(record)
    paths = [
        DIRECTORY / "audit_core.py",
        DIRECTORY / "audit_spread.py",
        DIRECTORY / "profiles.synthetic.yml",
        DIRECTORY / "flame-contact.synthetic.yml",
    ]
    report = dict(
        schema_version="village-core-acceptance-1",
        cases=results,
        advanced_physics_required=False,
        initial_moisture_input="fixed_by_material_preset",
        input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        limitations=[
            "Material presets and contact duty remain synthetic",
            "Building means a combustible equivalent cell, not complete structural damage",
            "Inert gap result is specific to this scenario; radiation can cross gaps",
            "No resolved wind, flame shape, crowns, firebrands or enclosure dynamics",
        ],
    )
    (output / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(f"Core acceptance passed: {len(results)} scenarios; {output}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    audit_core(parser.parse_args().output)
