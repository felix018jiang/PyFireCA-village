"""Freeze and compare component pathways; no field calibration or loss prediction.

python examples/village/vegetation_v3/audit_components.py --output runs/components-new
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

import numpy as np
import yaml
from audit_spread import make_request

from pyfireca.village.components_v3 import (
    FuelComponent,
    component_summary,
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _budget_errors(result, physics):
    budget = result.energy_budget
    fuel_energy = float(
        np.sum(
            (result.initial_fuel_kg - result.final_state.fuel_remaining_kg)
            * physics.heat_of_combustion_J_kg_dry
        )
    )
    return {
        "fuel_energy": abs(budget.released_J - fuel_energy),
        "emission_partition": abs(
            budget.released_J - budget.radiative_emitted_J - budget.non_radiative_released_J
        ),
        "radiative_partition": abs(
            budget.radiative_emitted_J
            - budget.radiative_intercepted_J
            - budget.radiative_unintercepted_J
        ),
        "nonradiative_partition": abs(
            budget.non_radiative_released_J
            - budget.convective_transferred_J
            - budget.nonradiative_untransferred_J
        ),
    }


def run_audit(output: Path, gaoyou_case: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    config = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    tree = config.recipes["tree_equivalent"]
    low = replace(
        config,
        recipes={
            **config.recipes,
            "tree_equivalent": (
                tree[0],
                replace(tree[1], source_base_height_m=0.2, receiver_height_m=0.3),
            ),
        },
    )
    ladder = replace(
        config,
        recipes={
            **config.recipes,
            "tree_equivalent": (
                replace(tree[0], dead_fraction=0.2, area_fraction=0.2),
                FuelComponent("ladder_fuel", 0.1, 0.2, 0.2, 1, 0.2, 3, 0.3),
                replace(tree[1], live_fraction=0.9, dead_fraction=0.6, area_fraction=0.6),
            ),
        },
    )
    configs = {"surface_upper": config, "low_upper_control": low, "ladder_hypothesis": ladder}
    specs = (
        ("grass_building", ("grass", "building"), False, "surface_upper"),
        ("shrub_building", ("shrub", "building"), False, "surface_upper"),
        (
            "grass_tree_chain",
            ("grass", "tree_equivalent", "tree_equivalent"),
            False,
            "surface_upper",
        ),
        ("road_building", ("grass", "bare_or_road", "building"), False, "surface_upper"),
        ("wet_tree", ("grass", "tree_equivalent"), True, "surface_upper"),
        ("surface_tree_source", ("tree_equivalent", "tree_equivalent"), False, "surface_upper"),
        ("low_upper_control", ("grass", "tree_equivalent"), False, "low_upper_control"),
        ("ladder_hypothesis", ("grass", "tree_equivalent"), False, "ladder_hypothesis"),
    )
    study = {
        "evidence": "synthetic_component_pathways_not_real_fire_validation",
        "contact": contact.to_mapping(),
        "component_configs": {key: value.to_mapping() for key, value in configs.items()},
        "scenarios": [
            {"id": sid, "labels": labels, "wet_control": wet, "config": cfg}
            for sid, labels, wet, cfg in specs
        ],
        "methods": ["cell_radiation", "cell_contact", "components_radiation", "components_contact"],
        "max_dt_s": [0.5, 0.25],
        "end_time_s": 1200,
        "cell_size_m": 2,
        "time_stability_acceptance_s": 2,
        "comparison_caveat": "component direct ignition targets lowest part only; "
        "tree-source comparison changes initial burning fuel, not a pure transport ablation",
        "gaoyou_case": str(gaoyou_case.resolve()) if gaoyou_case else None,
    }
    if gaoyou_case:
        study["gaoyou_manifest_sha256"] = hashlib.sha256(
            (gaoyou_case / "manifest.json").read_bytes()
        ).hexdigest()
    _write_json(output / "study.resolved.json", study)
    for key, value in configs.items():
        (output / f"{key}.yml").write_text(yaml.safe_dump(value.to_mapping(), sort_keys=False))
    rows, times, stability = [], [], []
    saved = {}
    for sid, labels, wet, cfg in specs:
        for method in study["methods"]:
            steps = []
            for dt in study["max_dt_s"]:
                request = make_request(labels, end_time_s=1200, max_dt_s=dt, wet=wet)
                enabled = method.endswith("contact")
                started = perf_counter()
                if method.startswith("components"):
                    resolved = resolve_components(
                        request, configs[cfg], contact=contact if enabled else None
                    )
                    result = run_component_simulation(resolved)
                    summary = component_summary(resolved, result)
                    cell_ids = resolved.cell_id
                    physics = resolved._node_request.parameters
                    if sid == "grass_building" and method == "components_contact" and dt == 0.25:
                        write_component_run(resolved, result, output / "grass-building-run")
                else:
                    result = run_v3_simulation(
                        replace(request, flame_contact=contact if enabled else None),
                        retain_snapshots=False,
                    )
                    summary = None
                    cell_ids = np.arange(request.domain_mask.size)
                    physics = request.parameters
                wall = perf_counter() - started
                ignition = result.final_state.ignition_time_s.ravel()
                mask = np.isfinite(ignition)
                rows.append(
                    {
                        "scenario": sid,
                        "method": method,
                        "dt_s": dt,
                        "ignited_nodes": int(mask.sum()),
                        "physical_cells_any_part_ignited": len(np.unique(cell_ids[mask])),
                        "component_summary": summary,
                        "ignition_time_s": [float(t) if np.isfinite(t) else None for t in ignition],
                        "consumed_fuel_kg": result.consumed_fuel_kg,
                        "energy_budget": asdict(result.energy_budget),
                        "budget_max_abs_errors_J": _budget_errors(result, physics),
                    }
                )
                times.append({"scenario": sid, "method": method, "dt_s": dt, "wall_s": wall})
                steps.append(ignition.copy())
                saved[f"{sid}-{method}-{dt:g}"] = ignition
            mask = np.isfinite(steps[0])
            same = np.array_equal(mask, np.isfinite(steps[1]))
            difference = (
                float(np.max(np.abs(steps[0][mask] - steps[1][mask])))
                if same and mask.any()
                else None
            )
            stability.append(
                {
                    "scenario": sid,
                    "method": method,
                    "ignition_mask_equal": same,
                    "max_time_difference_s": difference,
                }
            )
    crop_rows = []
    if gaoyou_case:
        base = load_v3_case_bundle(gaoyou_case).request
        for enabled in (False, True):
            steps = []
            for dt in study["max_dt_s"]:
                request = replace(base, scenario=replace(base.scenario, max_dt_s=dt))
                resolved = resolve_components(request, config, contact=contact if enabled else None)
                started = perf_counter()
                result = run_component_simulation(resolved)
                times.append(
                    {
                        "scenario": "gaoyou",
                        "method": f"components_{enabled}",
                        "dt_s": dt,
                        "wall_s": perf_counter() - started,
                    }
                )
                write_component_run(
                    resolved,
                    result,
                    output / f"gaoyou-{'contact' if enabled else 'radiation'}-dt{dt:g}",
                )
                ignition = result.final_state.ignition_time_s.ravel()
                steps.append(ignition.copy())
                crop_rows.append(
                    {
                        "contact": enabled,
                        "dt_s": dt,
                        "component_summary": component_summary(resolved, result),
                        "node_count": len(resolved.cell_id),
                        "ignited_nodes": int(np.isfinite(ignition).sum()),
                        "budget_max_abs_errors_J": _budget_errors(
                            result, resolved._node_request.parameters
                        ),
                    }
                )
            mask = np.isfinite(steps[0])
            same = np.array_equal(mask, np.isfinite(steps[1]))
            difference = (
                float(np.max(np.abs(steps[0][mask] - steps[1][mask])))
                if same and mask.any()
                else None
            )
            stability.append(
                {
                    "scenario": "gaoyou",
                    "method": f"components_{enabled}",
                    "ignition_mask_equal": same,
                    "max_time_difference_s": difference,
                }
            )
    np.savez(output / "ignition_times.npz", **saved)
    maximum_error = max(
        value for row in [*rows, *crop_rows] for value in row["budget_max_abs_errors_J"].values()
    )
    validation = {
        "completed_synthetic_runs": len(rows),
        "completed_crop_runs": len(crop_rows),
        "all_dt_masks_equal": all(row["ignition_mask_equal"] for row in stability),
        "max_ignition_time_difference_s": max(
            row["max_time_difference_s"]
            for row in stability
            if row["max_time_difference_s"] is not None
        ),
        "max_source_energy_budget_error_J": maximum_error,
        "source_energy_budget_errors_below_1mJ": maximum_error < 0.001,
        "time_stability_within_frozen_2s": all(
            row["max_time_difference_s"] is not None and row["max_time_difference_s"] <= 2
            for row in stability
        ),
        "field_validation_performed": False,
    }
    _write_json(output / "results.json", rows)
    _write_json(output / "gaoyou.results.json", crop_rows)
    _write_json(output / "stability.json", stability)
    _write_json(output / "validation.json", validation)
    _write_json(output / "timings.json", times)
    paths = [
        *ROOT.joinpath("src/pyfireca/village").rglob("*.py"),
        *DIRECTORY.glob("*.py"),
        *DIRECTORY.glob("*.yml"),
        ROOT / "tests/village/test_components_v3.py",
        ROOT / "pyproject.toml",
    ]
    hashes = {}
    for source in paths:
        relative = source.relative_to(ROOT)
        data = source.read_bytes()
        target = output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    _write_json(output / "source_sha256.json", hashes)
    return validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gaoyou-case", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_audit(args.output, args.gaoyou_case), indent=2))
