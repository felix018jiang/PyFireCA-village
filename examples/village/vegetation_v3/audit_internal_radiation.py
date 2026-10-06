"""Internal finite-area radiation ablation, with frozen inter-cell transport.

The close/dry/large-ground-fuel case is an explicit mechanism stress test,
selected during development, not a realistic stand or a preregistered finding.
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
ROOT = DIRECTORY.parents[2]


def _json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _dry(request):
    physics = request.parameters
    return replace(
        request,
        parameters=replace(
            physics,
            live_water_mass_kg=physics.live_dry_fuel_mass_kg * 0.01,
            dead_water_mass_kg=physics.dead_dry_fuel_mass_kg * 0.01,
            water_mass_kg=physics.initial_dry_fuel_mass_kg * 0.01,
            moisture_content_dry_basis=np.where(request.burnable_mask, 0.01, 0),
        ),
    )


def _max_valid_sample_temperature(result):
    shape = result.final_state.state.shape
    temperature = np.full(shape, np.nan)
    for snapshot in result.snapshots:
        value = np.where(snapshot.temperature_valid_mask, snapshot.surface_temperature_K, np.nan)
        temperature = np.fmax(temperature, value)
    return [float(v) if np.isfinite(v) else None for v in temperature.ravel()]


def _record(result, resolved):
    final = result.final_state
    ignition = final.ignition_time_s.ravel()
    internal = resolved.internal_radiation_graph
    return {
        "component_summary": component_summary(resolved, result),
        "ignition_time_s": [float(t) if np.isfinite(t) else None for t in ignition],
        "sampled_max_valid_temperature_K": _max_valid_sample_temperature(result),
        "received_convection_J": float(final.received_convection_J.sum()),
        "absorbed_radiation_J": float(final.absorbed_radiation_J.sum()),
        "consumed_fuel_kg": result.consumed_fuel_kg,
        "energy_budget_errors_J": _budget_errors(result, resolved._node_request.parameters),
        "internal_diagnostics": internal.summary() if internal else None,
    }


def run_audit(output: Path, gaoyou_case: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    config = load_component_configuration(DIRECTORY / "components.synthetic.yml")
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    radiation = load_internal_radiation_parameters(DIRECTORY / "internal-radiation.synthetic.yml")
    parts = config.recipes["tree_equivalent"]
    close = replace(
        config,
        recipes={
            "tree_equivalent": (
                replace(parts[0], dead_fraction=0.95),
                replace(
                    parts[1], dead_fraction=0.05, source_base_height_m=0.6, receiver_height_m=0.6
                ),
            )
        },
    )
    far = replace(
        close,
        recipes={
            "tree_equivalent": (
                close.recipes["tree_equivalent"][0],
                replace(
                    close.recipes["tree_equivalent"][1],
                    source_base_height_m=2.5,
                    receiver_height_m=2.5,
                ),
            )
        },
    )
    configs = {"original_components": config, "close_stress": close, "far_stress": far}
    specs = (
        ("original_tree", ("tree_equivalent",), False, "original_components"),
        ("close_dry_stress", ("tree_equivalent",), True, "close_stress"),
        ("far_dry_control", ("tree_equivalent",), True, "far_stress"),
        ("close_original_moisture_control", ("tree_equivalent",), False, "close_stress"),
        ("grass_building", ("grass", "building"), False, "original_components"),
        (
            "grass_tree_chain",
            ("grass", "tree_equivalent", "tree_equivalent"),
            False,
            "original_components",
        ),
        ("road_building", ("grass", "bare_or_road", "building"), False, "original_components"),
    )
    fine = replace(radiation, quadrature_order=6, max_quadrature_order=12)
    study = {
        "evidence": "synthetic_internal_radiation_ablation_not_field_validation",
        "component_configs": {k: v.to_mapping() for k, v in configs.items()},
        "internal_radiation": radiation.to_mapping(),
        "fine_quadrature_reference": fine.to_mapping(),
        "contact": contact.to_mapping(),
        "methods": {"internal": [False, True], "contact": [False, True]},
        "scenarios": [
            {"id": sid, "labels": labels, "dry_0_01_kg_kg": dry, "config": cfg}
            for sid, labels, dry, cfg in specs
        ],
        "end_time_s": 1200,
        "sample_interval_s": 5,
        "max_dt_s": [0.5, 0.25],
        "frozen_time_difference_acceptance_s": 2,
        "stress_case_selection": "development-selected activation example; "
        "not preregistered evidence or real stand",
        "fine_simulations": ["close_dry_stress_no_contact_dt0.25", "gaoyou_contact_dt0.25"],
        "gaoyou_case": str(gaoyou_case.resolve()) if gaoyou_case else None,
    }
    if gaoyou_case:
        study["gaoyou_manifest_sha256"] = hashlib.sha256(
            (gaoyou_case / "manifest.json").read_bytes()
        ).hexdigest()
    _json(output / "study.resolved.json", study)
    rows, times, stability, graph_checks = [], [], [], []
    saved = {}
    reference = {}
    for sid, labels, dry, cfg in specs:
        request = replace(make_request(labels), sample_interval_s=5)
        if dry:
            request = _dry(request)
        for internal_enabled in (False, True):
            for contact_enabled in (False, True):
                ignition_pair = []
                for dt in study["max_dt_s"]:
                    req = replace(request, scenario=replace(request.scenario, max_dt_s=dt))
                    started = perf_counter()
                    resolved = resolve_components(
                        req,
                        configs[cfg],
                        contact=contact if contact_enabled else None,
                        internal_radiation=radiation if internal_enabled else None,
                    )
                    result = run_component_simulation(resolved, retain_snapshots=True)
                    wall = perf_counter() - started
                    name = f"{sid}-internal{internal_enabled}-contact{contact_enabled}-dt{dt:g}"
                    row = {
                        "scenario": sid,
                        "internal": internal_enabled,
                        "contact": contact_enabled,
                        "dt_s": dt,
                        **_record(result, resolved),
                    }
                    rows.append(row)
                    times.append({"run": name, "wall_s": wall})
                    saved[name] = result.final_state.ignition_time_s.ravel()
                    ignition_pair.append(saved[name])
                    if (
                        internal_enabled
                        and not contact_enabled
                        and dt == 0.25
                        and sid in ("original_tree", "close_dry_stress")
                    ):
                        write_component_run(resolved, result, output / name)
                    if internal_enabled and dt == 0.25:
                        reference[(sid, contact_enabled)] = (resolved, result)
                valid = np.isfinite(ignition_pair[0])
                same = np.array_equal(valid, np.isfinite(ignition_pair[1]))
                difference = (
                    float(np.max(np.abs(ignition_pair[0][valid] - ignition_pair[1][valid])))
                    if same and valid.any()
                    else None
                )
                stability.append(
                    {
                        "scenario": sid,
                        "internal": internal_enabled,
                        "contact": contact_enabled,
                        "mask_equal": same,
                        "max_ignition_difference_s": difference,
                    }
                )
    crop_rows = []
    if gaoyou_case:
        request = load_v3_case_bundle(gaoyou_case).request
        for internal_enabled in (False, True):
            for contact_enabled in (False, True):
                ignition_pair = []
                for dt in study["max_dt_s"]:
                    req = replace(request, scenario=replace(request.scenario, max_dt_s=dt))
                    started = perf_counter()
                    resolved = resolve_components(
                        req,
                        config,
                        contact=contact if contact_enabled else None,
                        internal_radiation=radiation if internal_enabled else None,
                    )
                    result = run_component_simulation(resolved, retain_snapshots=True)
                    name = f"gaoyou-internal{internal_enabled}-contact{contact_enabled}-dt{dt:g}"
                    times.append({"run": name, "wall_s": perf_counter() - started})
                    crop_rows.append(
                        {
                            "internal": internal_enabled,
                            "contact": contact_enabled,
                            "dt_s": dt,
                            **_record(result, resolved),
                        }
                    )
                    saved[name] = result.final_state.ignition_time_s.ravel()
                    ignition_pair.append(saved[name])
                    if internal_enabled and contact_enabled and dt == 0.25:
                        write_component_run(resolved, result, output / name)
                        reference[("gaoyou", True)] = (resolved, result)
                valid = np.isfinite(ignition_pair[0])
                same = np.array_equal(valid, np.isfinite(ignition_pair[1]))
                difference = (
                    float(np.max(np.abs(ignition_pair[0][valid] - ignition_pair[1][valid])))
                    if same and valid.any()
                    else None
                )
                stability.append(
                    {
                        "scenario": "gaoyou",
                        "internal": internal_enabled,
                        "contact": contact_enabled,
                        "mask_equal": same,
                        "max_ignition_difference_s": difference,
                    }
                )
    # Independent finer graph on every geometry; thermal references on two frozen cases.
    fine_rows = []
    for (sid, contact_enabled), (coarse, coarse_result) in reference.items():
        refined = resolve_components(
            coarse.physical_request,
            coarse.configuration,
            contact=contact if contact_enabled else None,
            internal_radiation=fine,
        )
        original = coarse.internal_radiation_graph.edges
        finer = refined.internal_radiation_graph.edges
        same_ids = np.array_equal(original.source, finer.source) and np.array_equal(
            original.target, finer.target
        )
        error = np.abs(original.weight - finer.weight)
        graph_checks.append(
            {
                "scenario": sid,
                "contact": contact_enabled,
                "edge_ids_equal": same_ids,
                "max_absolute_difference": float(error.max(initial=0)),
                "within_configured_tolerance": bool(
                    np.all(
                        error
                        <= radiation.absolute_tolerance
                        + radiation.relative_tolerance * np.abs(finer.weight)
                    )
                ),
            }
        )
        if (sid, contact_enabled) not in [("close_dry_stress", False), ("gaoyou", True)]:
            continue
        started = perf_counter()
        result = run_component_simulation(refined, retain_snapshots=True)
        times.append({"run": f"{sid}-fine-reference", "wall_s": perf_counter() - started})
        a = coarse_result.final_state.ignition_time_s.ravel()
        b = result.final_state.ignition_time_s.ravel()
        valid = np.isfinite(a)
        same = np.array_equal(valid, np.isfinite(b))
        difference = float(np.max(np.abs(a[valid] - b[valid]))) if same and valid.any() else None
        fine_rows.append(
            {
                "scenario": sid,
                "contact": contact_enabled,
                "mask_equal": same,
                "max_ignition_difference_s": difference,
                **_record(result, refined),
            }
        )
    validation = {
        "synthetic_runs": len(rows),
        "crop_runs": len(crop_rows),
        "fine_reference_runs": len(fine_rows),
        "all_dt_masks_equal": all(r["mask_equal"] for r in stability),
        "max_dt_ignition_difference_s": max(
            r["max_ignition_difference_s"]
            for r in stability
            if r["max_ignition_difference_s"] is not None
        ),
        "within_frozen_2s": all(
            r["max_ignition_difference_s"] is not None and r["max_ignition_difference_s"] <= 2
            for r in stability
        ),
        "all_fine_masks_equal": all(r["mask_equal"] for r in fine_rows),
        "max_fine_ignition_difference_s": max(
            r["max_ignition_difference_s"]
            for r in fine_rows
            if r["max_ignition_difference_s"] is not None
        ),
        "all_graphs_within_tolerance": all(
            r["edge_ids_equal"] and r["within_configured_tolerance"] for r in graph_checks
        ),
        "max_source_budget_error_J": max(
            v for r in [*rows, *crop_rows, *fine_rows] for v in r["energy_budget_errors_J"].values()
        ),
        "field_validation": False,
        "intercell_geometry_rebuilt": False,
    }
    for name, data in [
        ("results", rows),
        ("gaoyou.results", crop_rows),
        ("stability", stability),
        ("graph_refinement", graph_checks),
        ("fine_reference", fine_rows),
        ("validation", validation),
        ("timings", times),
    ]:
        _json(output / f"{name}.json", data)
    np.savez(output / "ignition_times.npz", **saved)
    paths = [
        *ROOT.joinpath("src/pyfireca/village").rglob("*.py"),
        *DIRECTORY.glob("*.py"),
        *DIRECTORY.glob("*.yml"),
        ROOT / "tests/village/test_component_radiation_v3.py",
        ROOT / "pyproject.toml",
    ]
    hashes = {}
    for source in paths:
        relative = source.relative_to(ROOT)
        target = output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    _json(output / "source_sha256.json", hashes)
    return validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gaoyou-case", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_audit(args.output, args.gaoyou_case), indent=2))
