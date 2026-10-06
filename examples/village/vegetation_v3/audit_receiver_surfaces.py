"""Receiver-only geometry sensitivity, with frozen combustion and inter-cell graph."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np
from audit_components import _budget_errors
from audit_spread import make_request

from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
from pyfireca.village.component_surfaces_v3 import load_receiver_surface_configuration
from pyfireca.village.components_v3 import (
    component_summary,
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_audit(output: Path, case: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    components = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    internal = load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    front = load_receiver_surface_configuration(DIRECTORY / "receiver-surfaces.synthetic.yml")
    wall = front.surfaces["building"]["upper_combustible"]
    methods = {
        "horizontal_legacy": None,
        "vertical_front": front,
        "vertical_back": replace(
            front, surfaces={"building": {"upper_combustible": replace(wall, azimuth_deg=0)}}
        ),
        "vertical_two_sided": replace(
            front,
            surfaces={
                "building": {"upper_combustible": replace(wall, azimuth_deg=0, two_sided=True)}
            },
        ),
    }
    specs = (
        ("direct_building", ("building",)),
        ("grass_building", ("grass", "building")),
        ("shrub_building", ("shrub", "building")),
        ("road_building", ("grass", "bare_or_road", "building")),
    )
    inputs = list((ROOT / "src/pyfireca/village").glob("*.py"))
    inputs.extend(
        DIRECTORY / name
        for name in (
            "profiles.synthetic.yml",
            "components.synthetic.yml",
            "flame-contact.synthetic.yml",
            "internal-radiation.synthetic.yml",
            "receiver-surfaces.synthetic.yml",
            "audit_spread.py",
            "audit_components.py",
            "audit_receiver_surfaces.py",
        )
    )
    if case:
        inputs.extend(case.glob("*.npy"))
        inputs.append(case / "manifest.json")
    inputs = sorted(set(inputs))
    study = {
        "evidence": "synthetic_receiver_geometry_experiment_not_field_validation",
        "surfaces": {name: cfg.to_mapping() if cfg else None for name, cfg in methods.items()},
        "components": components.to_mapping(),
        "internal_radiation": internal.to_mapping(),
        "contact": contact.to_mapping(),
        "scenarios": [{"id": sid, "labels": labels} for sid, labels in specs],
        "max_dt_s": [0.5, 0.25],
        "ignition_time_acceptance_s": 2,
        "source_budget_acceptance_J": 1e-3,
        "synthetic_end_time_s": 1200,
        "crop_end_time_s": 600,
        "case": str(case.resolve()) if case else None,
        "negative_control": "road_building",
        "receiver_changes_affect": "same_cell_radiation_only",
        "held_fixed": [
            "fuel",
            "water",
            "thermal_properties",
            "HRR",
            "flame_cylinder",
            "contact",
            "intercell_radiation",
        ],
        "input_sha256": {
            str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
    }
    _json(output / "study.resolved.json", study)
    for p in inputs:
        target = output / "input_snapshot" / p.resolve().relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
    scenarios = [(sid, make_request(labels)) for sid, labels in specs]
    if case:
        scenarios.append(("gaoyou", load_v3_case_bundle(case).request))
    rows, stability, timing = [], [], []
    reference_physics, reference_frozen = {}, {}
    for sid, request in scenarios:
        for method, surfaces in methods.items():
            pair = []
            for dt in study["max_dt_s"]:
                started = perf_counter()
                resolved = resolve_components(
                    replace(request, scenario=replace(request.scenario, max_dt_s=dt)),
                    components,
                    contact=contact,
                    internal_radiation=internal,
                    surfaces=surfaces,
                )
                physics = resolved._node_request.parameters
                if sid not in reference_physics:
                    reference_physics[sid] = {
                        key: getattr(physics, key).copy() for key in physics.__dataclass_fields__
                    }
                assert all(
                    np.array_equal(getattr(physics, key), value, equal_nan=True)
                    for key, value in reference_physics[sid].items()
                )
                graph = resolved.internal_radiation_graph
                edge = resolved._node_request.exposure_edges
                intercell = resolved.cell_id[edge.source] != resolved.cell_id[edge.target]
                frozen = tuple(
                    getattr(edge, key)[intercell] for key in ("source", "target", "weight")
                )
                if sid not in reference_frozen:
                    reference_frozen[sid] = tuple(a.copy() for a in frozen)
                assert all(
                    np.array_equal(a, b) for a, b in zip(frozen, reference_frozen[sid], strict=True)
                )
                result = run_component_simulation(resolved)
                ignition = result.final_state.ignition_time_s.ravel()
                pair.append(ignition)
                radiative = (
                    result.final_state.released_energy_J * physics.radiative_fraction
                ).ravel()
                incident = np.bincount(
                    graph.edges.target,
                    weights=radiative[graph.edges.source] * graph.edges.weight,
                    minlength=len(resolved.cell_id),
                )
                rows.append(
                    {
                        "scenario": sid,
                        "method": method,
                        "dt_s": dt,
                        "component_summary": component_summary(resolved, result),
                        "ignition_time_s": [float(t) if np.isfinite(t) else None for t in ignition],
                        "internal_incident_radiation_J": incident.tolist(),
                        "internal_edges": {
                            key: getattr(graph.edges, key).tolist()
                            for key in ("source", "target", "weight")
                        },
                        "geometry_diagnostics": graph.summary(),
                        "energy_budget_errors_J": _budget_errors(result, physics),
                    }
                )
                name = f"{sid}-{method}-dt{dt:g}"
                timing.append({"run": name, "wall_s": perf_counter() - started})
                if dt == 0.25:
                    write_component_run(resolved, result, output / name)
            finite = np.isfinite(pair[0]) & np.isfinite(pair[1])
            same = bool(np.array_equal(np.isfinite(pair[0]), np.isfinite(pair[1])))
            diff = float(np.max(np.abs(pair[0][finite] - pair[1][finite]))) if finite.any() else 0.0
            stability.append(
                {
                    "scenario": sid,
                    "method": method,
                    "same_ignition_mask": same,
                    "max_ignition_time_difference_s": diff,
                    "passed": same and diff <= study["ignition_time_acceptance_s"],
                }
            )
    budget = max(v for row in rows for v in row["energy_budget_errors_J"].values())
    negative = all(
        sum(part["ignited"] for part in row["component_summary"].values()) == 1
        for row in rows
        if row["scenario"] == "road_building"
    )
    report = {
        "solve_count": len(rows),
        "rows": rows,
        "timestep_comparisons": stability,
        "all_timestep_comparisons_passed": all(row["passed"] for row in stability),
        "negative_control_passed": negative,
        "max_source_budget_error_J": budget,
        "source_budget_passed": budget <= study["source_budget_acceptance_J"],
        "combustion_and_intercell_arrays_unchanged": True,
        "requires_independent_calibration": True,
    }
    _json(output / "report.json", report)
    _json(output / "timing.json", timing)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", type=Path)
    args = parser.parse_args()
    report = run_audit(args.output, args.case)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"rows", "timestep_comparisons"}
            },
            indent=2,
        )
    )
