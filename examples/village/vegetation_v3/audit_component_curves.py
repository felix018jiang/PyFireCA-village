"""Clock-only sensitivity; prescribed curves and ceilings are explicitly synthetic.

Fuel, moisture, ignition threshold, area, radiative fraction and geometry are
fixed. Only tree surface_dead/building combustible_base clocks change.
"""

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

from pyfireca.village.component_curves_v3 import (
    ComponentBurnCurve,
    ComponentCurveConfiguration,
    load_component_curve_configuration,
)
from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
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


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_audit(output: Path, gaoyou_case: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    config = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    internal = load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")
    half = load_component_curve_configuration(DIRECTORY / "component-curves.synthetic.yml")
    double = replace(
        half,
        provenance="synthetic:double_inherited_lower_component_clocks_not_calibration",
        curves={
            label: {
                name: replace(
                    curve,
                    growth_time_s=curve.growth_time_s * 4,
                    steady_time_s=curve.steady_time_s * 4,
                    decay_time_s=curve.decay_time_s * 4,
                )
                for name, curve in parts.items()
            }
            for label, parts in half.curves.items()
        },
    )
    methods = {"inherited": None, "half": half, "double": double}
    specs = (
        ("grass_building", ("grass", "building"), False),
        ("shrub_building", ("shrub", "building"), False),
        ("grass_tree_chain", ("grass", "tree_equivalent", "tree_equivalent"), False),
        ("shrub_tree_chain", ("shrub", "tree_equivalent", "tree_equivalent"), False),
        ("road_building", ("grass", "bare_or_road", "building"), False),
        ("wet_tree_chain", ("grass", "tree_equivalent", "tree_equivalent"), True),
        ("direct_tree", ("tree_equivalent",), False),
        ("direct_building", ("building",), False),
    )
    inputs = [
        DIRECTORY / name
        for name in (
            "profiles.synthetic.yml",
            "components.synthetic.yml",
            "flame-contact.synthetic.yml",
            "internal-radiation.synthetic.yml",
            "component-curves.synthetic.yml",
            "audit_spread.py",
            "audit_components.py",
            "audit_component_curves.py",
        )
    ]
    if gaoyou_case:
        inputs.extend(sorted(gaoyou_case.glob("*.npy")))
        inputs.append(gaoyou_case / "manifest.json")
    study = {
        "evidence": "synthetic_clock_sensitivity_not_field_validation",
        "curves": {name: cfg.to_mapping() if cfg else None for name, cfg in methods.items()},
        "components": config.to_mapping(),
        "internal_radiation": internal.to_mapping(),
        "contact": contact.to_mapping(),
        "scenarios": [{"id": sid, "labels": labels, "wet": wet} for sid, labels, wet in specs],
        "synthetic_end_time_s": 1200,
        "gaoyou_end_time_s": 600,
        "max_dt_s": [0.5, 0.25],
        "ignition_time_acceptance_s": 2,
        "source_budget_acceptance_J": 1e-3,
        "negative_controls": ["road_building", "wet_tree_chain"],
        "radiation_only_extra": "direct_tree",
        "saved_runs": "dt0.25 grass_building, grass_tree_chain, direct_tree contact-on and gaoyou",
        "gaoyou_case": str(gaoyou_case.resolve()) if gaoyou_case else None,
        "input_sha256": {
            str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
    }
    # Fixed comparison rules written before any simulation.
    _json(output / "study.resolved.json", study)
    root = DIRECTORY.parents[2]
    for source in inputs:
        target = output / "input_snapshot" / source.resolve().relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    rows, timings, stability = [], [], []
    base = load_v3_case_bundle(gaoyou_case).request if gaoyou_case else None
    scenarios = [(sid, make_request(labels, wet=wet), True) for sid, labels, wet in specs]
    scenarios.append(("direct_tree_radiation_only", make_request(("tree_equivalent",)), False))
    if base:
        scenarios.append(("gaoyou", base, True))
    frozen_edges = {}
    for sid, request, use_contact in scenarios:
        for method, curves in methods.items():
            ignition_pair = []
            for dt in study["max_dt_s"]:
                started = perf_counter()
                request_dt = replace(request, scenario=replace(request.scenario, max_dt_s=dt))
                resolved = resolve_components(
                    request_dt,
                    config,
                    contact=contact if use_contact else None,
                    internal_radiation=internal,
                    curves=curves,
                )
                edge = resolved._node_request.exposure_edges
                edge_fields = (edge.source, edge.target, edge.weight)
                if sid not in frozen_edges:
                    frozen_edges[sid] = tuple(v.copy() for v in edge_fields)
                assert all(
                    np.array_equal(v, ref)
                    for v, ref in zip(edge_fields, frozen_edges[sid], strict=True)
                )
                result = run_component_simulation(resolved)
                final = result.final_state
                ignition = final.ignition_time_s.ravel()
                ignition_pair.append(ignition)
                p = resolved._node_request.parameters
                row = {
                    "scenario": sid,
                    "method": method,
                    "dt_s": dt,
                    "component_summary": component_summary(resolved, result),
                    "ignition_time_s": [float(t) if np.isfinite(t) else None for t in ignition],
                    "burnout_time_s": [
                        float(t) if np.isfinite(t) else None for t in final.burnout_time_s.ravel()
                    ],
                    "consumed_fuel_kg": result.consumed_fuel_kg,
                    "released_energy_J": float(final.released_energy_J.sum()),
                    "energy_budget_errors_J": _budget_errors(result, p),
                    "curve_diagnostics": resolved.curve_diagnostics,
                }
                rows.append(row)
                name = f"{sid}-{method}-dt{dt:g}"
                timings.append({"run": name, "wall_s": perf_counter() - started})
                if dt == 0.25 and sid in {
                    "grass_building",
                    "grass_tree_chain",
                    "direct_tree",
                    "gaoyou",
                }:
                    write_component_run(resolved, result, output / name)
            finite = np.isfinite(ignition_pair[0]) & np.isfinite(ignition_pair[1])
            diff = (
                float(np.max(np.abs(ignition_pair[0][finite] - ignition_pair[1][finite])))
                if finite.any()
                else 0
            )
            same = bool(
                np.array_equal(np.isfinite(ignition_pair[0]), np.isfinite(ignition_pair[1]))
            )
            stability.append(
                {
                    "scenario": sid,
                    "method": method,
                    "same_ignition_mask": same,
                    "max_ignition_time_difference_s": diff,
                    "passed": same and diff <= study["ignition_time_acceptance_s"],
                }
            )
    rejected = ComponentCurveConfiguration(
        "synthetic",
        "test:30s_fine_clock_with_fixed_fuel",
        {"tree_equivalent": {"surface_dead": ComponentBurnCurve(2, 10, 18, 400000)}},
    )
    try:
        resolve_components(make_request(("tree_equivalent",)), config, curves=rejected)
    except ValueError as error:
        rejection = str(error)
    else:
        raise AssertionError("30s input should exceed the fixed 400 kW/m2 ceiling")
    negative_ok = all(
        sum(v["ignited"] for v in row["component_summary"].values()) == 1
        for row in rows
        if row["scenario"] in study["negative_controls"]
    )
    budget_error = max(value for row in rows for value in row["energy_budget_errors_J"].values())
    report = {
        "simulation_count": len(rows),
        "rows": rows,
        "timestep_comparisons": stability,
        "all_timestep_comparisons_passed": all(row["passed"] for row in stability),
        "all_geometry_graphs_identical_across_curves": True,
        "negative_controls_passed": negative_ok,
        "max_source_budget_error_J": budget_error,
        "source_budgets_passed": budget_error <= study["source_budget_acceptance_J"],
        "short_curve_rejection": {"configuration": rejected.to_mapping(), "error": rejection},
        "requires_independent_calibration": True,
    }
    _json(output / "report.json", report)
    _json(output / "timings.json", timings)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gaoyou-case", type=Path)
    args = parser.parse_args()
    report = run_audit(args.output, args.gaoyou_case)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"rows", "timestep_comparisons", "short_curve_rejection"}
            },
            indent=2,
        )
    )
