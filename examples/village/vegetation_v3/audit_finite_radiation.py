"""Frozen small-scene comparison of inherited and complete finite radiation graphs."""

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
from pyfireca.village.finite_radiation_v3 import load_finite_radiation_configuration
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_audit(output):
    output.mkdir(parents=True, exist_ok=False)
    components = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    internal = load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")
    finite = load_finite_radiation_configuration(DIRECTORY / "finite-radiation.synthetic.yml")
    front = load_receiver_surface_configuration(DIRECTORY / "receiver-surfaces.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    wall = front.surfaces["building"]["upper_combustible"]
    back = replace(
        front, surfaces={"building": {"upper_combustible": replace(wall, azimuth_deg=0)}}
    )
    methods = {
        "inherited": {"internal_radiation": internal},
        "finite_front": {"finite_radiation": finite, "surfaces": front},
        "finite_back": {"finite_radiation": finite, "surfaces": back},
        "finite_unobstructed": {
            "finite_radiation": replace(
                finite, receiver_visibility=False, terrain_visibility=False
            ),
            "surfaces": front,
        },
    }
    specs = (
        ("grass_building", ("grass", "building"), False, False),
        ("shrub_building", ("shrub", "building"), False, False),
        ("grass_tree_chain", ("grass", "tree_equivalent", "tree_equivalent"), False, False),
        ("road_building", ("grass", "bare_or_road", "building"), False, False),
        ("terrain_step", ("grass", "bare_or_road", "building"), False, True),
        ("wet_tree_chain", ("grass", "tree_equivalent", "tree_equivalent"), True, False),
    )
    inputs = sorted(
        set(
            list((ROOT / "src/pyfireca/village").glob("*.py"))
            + [
                DIRECTORY / name
                for name in (
                    "profiles.synthetic.yml",
                    "components.synthetic.yml",
                    "internal-radiation.synthetic.yml",
                    "finite-radiation.synthetic.yml",
                    "receiver-surfaces.synthetic.yml",
                    "flame-contact.synthetic.yml",
                    "audit_spread.py",
                    "audit_components.py",
                    "audit_finite_radiation.py",
                )
            ]
        )
    )
    study = {
        "evidence": "synthetic_geometry_and_thermal_sensitivity_not_field_validation",
        "methods": {
            name: {key: value.to_mapping() for key, value in fields.items()}
            for name, fields in methods.items()
        },
        "components": components.to_mapping(),
        "contact": contact.to_mapping(),
        "scenarios": [
            {"id": sid, "labels": labels, "wet": wet, "terrain_step": hill}
            for sid, labels, wet, hill in specs
        ],
        "max_dt_s": [0.5, 0.25],
        "end_time_s": 1200,
        "ignition_time_acceptance_s": 2,
        "source_budget_acceptance_J": 1e-3,
        "negative_controls": ["road_building", "wet_tree_chain", "terrain_step"],
        "held_fixed": [
            "fuel",
            "water",
            "HRR",
            "thermal_properties",
            "contact",
            "cylinder_geometry",
        ],
        "fine_references": ["grass_building", "grass_tree_chain"],
        "fine_reference_integration": "start12/max24, relative0.001, absolute0.00001",
        "fine_reference_weight_acceptance": "0.00001 + 0.01 * abs(fine_weight)",
        "large_crop_not_in_this_study": True,
        "input_sha256": {
            str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
    }
    _json(output / "study.resolved.json", study)
    for p in inputs:
        target = output / "input_snapshot" / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
    rows, comparisons, timing, references = [], [], [], []
    scenarios, physics_reference, coarse = {}, {}, {}
    for sid, labels, wet, hill in specs:
        request = make_request(labels, wet=wet)
        if hill:
            request = replace(request, ground_elevation_m=np.array([[0.0, 3.0, 0.0]]))
        scenarios[sid] = request
        for method, kwargs in methods.items():
            ignition_pair = []
            for dt in study["max_dt_s"]:
                started = perf_counter()
                resolved = resolve_components(
                    replace(request, scenario=replace(request.scenario, max_dt_s=dt)),
                    components,
                    contact=contact,
                    **kwargs,
                )
                p = resolved._node_request.parameters
                if sid not in physics_reference:
                    physics_reference[sid] = {
                        key: getattr(p, key).copy() for key in p.__dataclass_fields__
                    }
                assert all(
                    np.array_equal(getattr(p, key), ref, equal_nan=True)
                    for key, ref in physics_reference[sid].items()
                )
                result = run_component_simulation(resolved)
                ignition = result.final_state.ignition_time_s.ravel()
                ignition_pair.append(ignition)
                graph = resolved.finite_radiation_graph
                name = f"{sid}-{method}-dt{dt:g}"
                timing.append({"run": name, "wall_s": perf_counter() - started})
                rows.append(
                    {
                        "scenario": sid,
                        "method": method,
                        "dt_s": dt,
                        "component_summary": component_summary(resolved, result),
                        "ignition_time_s": [float(t) if np.isfinite(t) else None for t in ignition],
                        "energy_budget_errors_J": _budget_errors(result, p),
                        "graph_diagnostics": graph.summary() if graph else None,
                    }
                )
                if dt == 0.25:
                    write_component_run(resolved, result, output / name)
                    if method == "finite_front":
                        coarse[sid] = graph, ignition
            finite_pair = np.isfinite(ignition_pair[0]) & np.isfinite(ignition_pair[1])
            same = bool(
                np.array_equal(np.isfinite(ignition_pair[0]), np.isfinite(ignition_pair[1]))
            )
            diff = (
                float(np.max(np.abs(ignition_pair[0][finite_pair] - ignition_pair[1][finite_pair])))
                if finite_pair.any()
                else 0.0
            )
            comparisons.append(
                {
                    "scenario": sid,
                    "method": method,
                    "same_ignition_mask": same,
                    "max_ignition_difference_s": diff,
                    "passed": same and diff <= study["ignition_time_acceptance_s"],
                }
            )
    finer = replace(
        finite,
        integration=replace(
            finite.integration,
            quadrature_order=12,
            max_quadrature_order=24,
            relative_tolerance=0.001,
        ),
    )
    for sid in study["fine_references"]:
        started = perf_counter()
        request = scenarios[sid]
        resolved = resolve_components(
            replace(request, scenario=replace(request.scenario, max_dt_s=0.25)),
            components,
            contact=contact,
            finite_radiation=finer,
            surfaces=front,
        )
        result = run_component_simulation(resolved)
        graph, old_ignition = coarse[sid]
        fine_graph = resolved.finite_radiation_graph
        assert np.array_equal(graph.edges.source, fine_graph.edges.source)
        assert np.array_equal(graph.edges.target, fine_graph.edges.target)
        difference = np.abs(graph.edges.weight - fine_graph.edges.weight)
        acceptable = difference <= 1e-5 + 0.01 * np.abs(fine_graph.edges.weight)
        ignition = result.final_state.ignition_time_s.ravel()
        both = np.isfinite(ignition) & np.isfinite(old_ignition)
        diff = float(np.max(np.abs(ignition[both] - old_ignition[both]))) if both.any() else 0.0
        same = bool(np.array_equal(np.isfinite(ignition), np.isfinite(old_ignition)))
        references.append(
            {
                "scenario": sid,
                "edge_weight_passed": bool(acceptable.all()),
                "max_edge_absolute_difference": float(difference.max(initial=0)),
                "same_ignition_mask": same,
                "max_ignition_difference_s": diff,
                "thermal_passed": same and diff <= 2,
                "energy_budget_errors_J": _budget_errors(result, resolved._node_request.parameters),
            }
        )
        write_component_run(resolved, result, output / f"{sid}-fine-reference")
        timing.append({"run": f"{sid}-fine-reference", "wall_s": perf_counter() - started})
    budget = max(v for row in rows + references for v in row["energy_budget_errors_J"].values())
    negative = all(
        sum(part["ignited"] for part in row["component_summary"].values()) == 1
        for row in rows
        if row["scenario"] in study["negative_controls"]
    )
    report = {
        "solve_count": len(rows) + len(references),
        "rows": rows,
        "timestep_comparisons": comparisons,
        "fine_references": references,
        "all_timestep_comparisons_passed": all(row["passed"] for row in comparisons),
        "negative_controls_passed": negative,
        "max_source_budget_error_J": budget,
        "source_budget_passed": budget <= 1e-3,
        "all_fine_reference_checks_passed": all(
            row["edge_weight_passed"] and row["thermal_passed"] for row in references
        ),
        "combustion_arrays_unchanged": True,
        "requires_independent_calibration": True,
    }
    _json(output / "report.json", report)
    _json(output / "timing.json", timing)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(args.output)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"rows", "timestep_comparisons", "fine_references"}
            },
            indent=2,
        )
    )
