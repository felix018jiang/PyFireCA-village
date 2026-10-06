"""Paired slope/material receiver tests and frozen multi-ignition village crops."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.flame_contact_v3 import FlameContactOperator, load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "examples/village/vegetation_v3"
PAIRS = [
    ("grass", "grass"),
    ("shrub", "shrub"),
    ("tree_equivalent", "tree_equivalent"),
    ("grass", "shrub"),
    ("grass", "tree_equivalent"),
    ("grass", "building"),
    ("shrub", "building"),
]
POINTS = [(159, 353), (190, 391), (205, 397), (241, 132)]


def make_pair(fixture, labels, angle, dt, contact):
    request = fixture.make_request(labels, max_dt_s=dt, end_time_s=900)
    # Zero wind/tilt only: builder uses source_height/2 as the point-source z.
    # Add a common datum to obtain actual source and receiver absolute heights.
    ground = np.array([[50.0, 50.0 + 2 * np.tan(np.deg2rad(angle))]])
    physics = request.parameters
    edges = build_v3_radiative_transport_edges(
        request.grid,
        request.source_mask,
        request.target_mask,
        physics.receiver_area_m2,
        physics.source_height_m + 2 * ground,
        physics.receiver_height_m + ground,
        np.ones(request.grid.shape),
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
    )
    return replace(
        request,
        ground_elevation_m=ground,
        exposure_edges=edges,
        flame_contact=contact,
        sample_interval_s=30,
    )


def measure(request):
    operator = FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )
    result = run_v3_simulation(request, retain_snapshots=False)
    budget = result.energy_budget
    closure = abs(
        budget.non_radiative_released_J
        - budget.convective_transferred_J
        - budget.nonradiative_untransferred_J
    )
    assert closure <= 1e-5 + 1e-9 * budget.released_J
    final = result.final_state
    assert np.all(final.fuel_remaining_kg >= 0)
    assert np.all(final.fuel_remaining_kg <= result.initial_fuel_kg + 1e-9)
    return dict(
        ignited_cells=int(np.isfinite(final.ignition_time_s).sum()),
        ignition_times_s=[
            float(t) if np.isfinite(t) else None for t in final.ignition_time_s.ravel()
        ],
        burned_out_cells=int((final.state == 4).sum()),
        consumed_fuel_kg=result.consumed_fuel_kg,
        energy_closure_error_J=closure,
        first_source_contact_weights=operator.geometry_fraction[0].tolist(),
    )


def audit(output, source, *, include_crops=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("matrix_fixture", DIRECTORY / "audit_spread.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    base = load_flame_contact_parameters(DIRECTORY / "flame-contact-low-building.synthetic.yml")
    matrix = []
    for labels in PAIRS:
        for angle in (-20, -10, -5, 0, 5, 10, 20):
            for extent in (0.0, 0.15):
                for dt in (1.0, 0.25):
                    record = dict(
                        materials=list(labels), angle_deg=angle, half_extent_m=extent, dt_s=dt
                    )
                    request = make_pair(
                        fixture,
                        labels,
                        angle,
                        dt,
                        replace(base, receiver_vertical_half_extent_m=extent),
                    )
                    record.update(measure(request))
                    matrix.append(record)
        print("Completed pair:", labels, flush=True)
    crops = []
    if include_crops:
        for row, col in POINTS:
            case = output / f"crop-{row}-{col}"
            crop_v3_case_bundle(
                source,
                case,
                row_start=row - 8,
                row_stop=row + 9,
                col_start=col - 8,
                col_stop=col + 9,
                end_time_s=1200,
                sample_interval_s=30,
                ignition=VillageIgnitionEvent(0, 8, 8),
            )
            bundle = load_v3_case_bundle(case)
            for extent in (0.0, 0.15):
                for dt in (1.0, 0.25):
                    request = replace(
                        bundle.request,
                        scenario=replace(bundle.request.scenario, max_dt_s=dt),
                        flame_contact=replace(base, receiver_vertical_half_extent_m=extent),
                    )
                    record = dict(row=row, col=col, half_extent_m=extent, dt_s=dt)
                    record.update(measure(request))
                    crops.append(record)
            print("Completed crop:", row, col, flush=True)
    disagreements = []
    max_time_diff = 0.0
    for records, keys in [
        (matrix, ("materials", "angle_deg", "half_extent_m")),
        (crops, ("row", "col", "half_extent_m")),
    ]:
        for i in range(0, len(records), 2):
            coarse, fine = records[i : i + 2]
            signature = {k: coarse[k] for k in keys}
            a, b = coarse["ignition_times_s"], fine["ignition_times_s"]
            differing = sum((x is None) != (y is None) for x, y in zip(a, b, strict=True))
            if differing:
                disagreements.append(dict(**signature, changed_ignition_cells=differing))
            times = [
                abs(x - y) for x, y in zip(a, b, strict=True) if x is not None and y is not None
            ]
            max_time_diff = max(max_time_diff, max(times, default=0.0))
    report = dict(
        matrix=matrix,
        crops=crops,
        summary=dict(
            synthetic_runs=len(matrix),
            crop_runs=len(crops),
            time_step_outcome_disagreements=disagreements,
            max_matched_ignition_time_diff_s=max_time_diff,
        ),
        inputs_sha256={
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                DIRECTORY / "audit_spread.py",
                DIRECTORY / "profiles.synthetic.yml",
                DIRECTORY / "flame-contact-low-building.synthetic.yml",
            ]
        },
        limitations=[
            "900s synthetic pair horizon; unignited means unignited by this time.",
            "17x17 crops exclude external sources; not whole-village validation.",
            "Finite band is synthetic; no empirical slope acceleration is imposed.",
            "More ignition is not an accuracy score; numerical sensitivity is reported.",
        ],
    )
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    )
    print(json.dumps(report["summary"], indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "runs/gaoyou-core-full-20261006/case")
    parser.add_argument("--skip-crops", action="store_true")
    args = parser.parse_args()
    audit(args.output, args.source, include_crops=not args.skip_crops)
