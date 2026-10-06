"""Report existing fixed moisture defaults and demonstrate why pools stay separate.

This audit introduces no new material values or run-time moisture configuration.
Uniform-live/dead and zero-water variants are developer ablations, not presets
recommended for aerial-image simulations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from audit_components import _budget_errors
from audit_spread import make_request

from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
from pyfireca.village.components_v3 import (
    component_summary,
    load_component_configuration,
    resolve_components,
    run_component_simulation,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.vegetation_materials import load_vegetation_profiles
from pyfireca.village.workflow_v3 import load_v3_case_bundle

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _nullable(values):
    return [float(v) if np.isfinite(v) else None for v in values.ravel()]


def run_audit(output: Path, case: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    library = load_vegetation_profiles(DIRECTORY / "profiles.synthetic.yml")
    defaults = []
    for profile in library.profiles:
        row = {
            "id": profile.id,
            "landcover": profile.landcover_id,
            "burnable": profile.burnable,
            "evidence": profile.evidence,
            "moisture_unit": "kg_water/kg_dry",
            "user_moisture_inputs_required": 0,
        }
        if profile.burnable:
            row.update(
                {
                    key: profile.value(key)
                    for key in (
                        "live_dry_mass_fraction",
                        "dead_dry_mass_fraction",
                        "live_moisture_content_dry_basis",
                        "dead_moisture_content_dry_basis",
                    )
                }
            )
            row["weighted_moisture_content_dry_basis"] = profile.weighted_moisture_content_dry_basis
            row["moisture_provenance"] = {
                key: profile.parameters[key].provenance
                for key in ("live_moisture_content_dry_basis", "dead_moisture_content_dry_basis")
            }
        else:
            row["weighted_moisture_content_dry_basis"] = None
        defaults.append(row)
    inputs = [
        DIRECTORY / name
        for name in (
            "profiles.synthetic.yml",
            "components.synthetic.yml",
            "flame-contact.synthetic.yml",
            "internal-radiation.synthetic.yml",
            "audit_spread.py",
            "audit_components.py",
            "audit_moisture_defaults.py",
        )
    ]
    if case:
        inputs.extend(sorted(case.glob("*.npy")))
        inputs.append(case / "manifest.json")
    study = {
        "evidence": "existing_synthetic_defaults_and_pool_redistribution_ablation",
        "defaults": defaults,
        "methods": [
            "existing_defaults",
            "uniform_live_dead_same_total_water",
            "zero_water_control",
        ],
        "max_dt_s": [0.5, 0.25],
        "ignition_time_difference_acceptance_s": 2,
        "source_budget_acceptance_J": 1e-3,
        "synthetic_scenario": ["grass", "tree_equivalent", "tree_equivalent"],
        "synthetic_end_time_s": 1200,
        "case": str(case.resolve()) if case else None,
        "input_sha256": {
            str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
        "uniform_and_zero_are_not_new_material_defaults": True,
        "refinement_rule": {
            "condition": "case zero-water ignition masks differ at 0.5/0.25 s",
            "dt_s": [0.125, 0.0625],
            "selection": "development-selected numerical follow-up; original failure retained",
        },
    }
    _json(output / "study.resolved.json", study)
    for source in inputs:
        relative = (
            source.resolve().relative_to(ROOT)
            if source.resolve().is_relative_to(ROOT)
            else Path("external_case") / source.name
        )
        target = output / "input_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    config = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    internal = load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")
    scenarios = {"synthetic_chain": make_request(tuple(study["synthetic_scenario"]))}
    if case:
        scenarios["case"] = load_v3_case_bundle(case).request
    rows, stability, refinements = [], [], []
    for sid, request in scenarios.items():
        original = request.parameters
        for method in study["methods"]:
            physics = original
            if method != "existing_defaults":
                mc = (
                    original.moisture_content_dry_basis
                    if method.startswith("uniform")
                    else np.zeros(request.grid.shape)
                )
                live_water = original.live_dry_fuel_mass_kg * mc
                dead_water = original.dead_dry_fuel_mass_kg * mc
                physics = replace(
                    original,
                    live_water_mass_kg=live_water,
                    dead_water_mass_kg=dead_water,
                    water_mass_kg=live_water + dead_water,
                    moisture_content_dry_basis=mc,
                )
            if method.startswith("uniform"):
                assert np.allclose(
                    physics.water_mass_kg, original.water_mass_kg, rtol=1e-12, atol=1e-12
                )
            pair = []
            for dt in study["max_dt_s"]:
                resolved = resolve_components(
                    replace(
                        request, parameters=physics, scenario=replace(request.scenario, max_dt_s=dt)
                    ),
                    config,
                    contact=contact,
                    internal_radiation=internal,
                )
                result = run_component_simulation(resolved)
                p = resolved._node_request.parameters
                times = result.final_state.ignition_time_s
                pair.append(times)
                rows.append(
                    {
                        "scenario": sid,
                        "method": method,
                        "dt_s": dt,
                        "component_summary": component_summary(resolved, result),
                        "ignition_time_s": _nullable(times),
                        "component_moisture_content_dry_basis": _nullable(
                            p.moisture_content_dry_basis
                        ),
                        "initial_total_water_kg": float(physics.water_mass_kg.sum()),
                        "initial_heated_water_kg": float(
                            (p.effective_heated_dry_mass_kg * p.moisture_content_dry_basis).sum()
                        ),
                        "energy_budget_errors_J": _budget_errors(result, p),
                    }
                )
            finite = np.isfinite(pair[0]) & np.isfinite(pair[1])
            same = bool(np.array_equal(np.isfinite(pair[0]), np.isfinite(pair[1])))
            delta = float(np.max(np.abs(pair[0][finite] - pair[1][finite]))) if finite.any() else 0
            stability.append(
                {
                    "scenario": sid,
                    "method": method,
                    "same_ignition_mask": same,
                    "max_ignition_time_difference_s": delta,
                    "passed": same and delta <= study["ignition_time_difference_acceptance_s"],
                }
            )
            if sid == "case" and method == "zero_water_control" and not same:
                previous = pair[-1]
                for dt in study["refinement_rule"]["dt_s"]:
                    resolved = resolve_components(
                        replace(
                            request,
                            parameters=physics,
                            scenario=replace(request.scenario, max_dt_s=dt),
                        ),
                        config,
                        contact=contact,
                        internal_radiation=internal,
                    )
                    result = run_component_simulation(resolved)
                    times = result.final_state.ignition_time_s
                    common = np.isfinite(previous) & np.isfinite(times)
                    same_refined = bool(np.array_equal(np.isfinite(previous), np.isfinite(times)))
                    diff = (
                        float(np.max(np.abs(previous[common] - times[common])))
                        if common.any()
                        else 0
                    )
                    refinements.append(
                        {
                            "scenario": sid,
                            "method": method,
                            "dt_s": dt,
                            "component_summary": component_summary(resolved, result),
                            "ignition_time_s": _nullable(times),
                            "same_mask_as_previous_finer_step": same_refined,
                            "max_time_difference_from_previous_step_s": diff,
                            "passed": same_refined
                            and diff <= study["ignition_time_difference_acceptance_s"],
                            "energy_budget_errors_J": _budget_errors(
                                result, resolved._node_request.parameters
                            ),
                        }
                    )
                    previous = times
    error = max(v for row in [*rows, *refinements] for v in row["energy_budget_errors_J"].values())
    report = {
        "simulation_count": len(rows) + len(refinements),
        "rows": rows,
        "timestep_comparisons": stability,
        "all_timestep_comparisons_passed": all(row["passed"] for row in stability),
        "refinement_runs": refinements,
        "max_source_budget_error_J": error,
        "source_budgets_passed": error <= study["source_budget_acceptance_J"],
        "production_ready": False,
        "material_values_are_assumptions_not_rgb_measurements": True,
    }
    _json(output / "report.json", report)
    tracked = [*ROOT.joinpath("src/pyfireca/village").glob("*.py"), ROOT / "pyproject.toml"]
    sha = {}
    for source in tracked:
        relative = source.relative_to(ROOT)
        target = output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        sha[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    _json(output / "source_sha256.json", sha)
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
                k: v
                for k, v in report.items()
                if k not in {"rows", "timestep_comparisons", "refinement_runs"}
            },
            indent=2,
        )
    )
