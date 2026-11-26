"""Unified prepared-village input → saved-check search → GIS/list/cost package.

This milestone executes no CA; missing saved checks remain unknown. Preparing a
new physical case and running new fire scenarios require their own explicit budget.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from replay_heritage_strategy import replay_inventory

from pyfireca.planning.strategy_io import (
    export_strategy,
    file_sha256,
    load_strategy_inputs,
    read_json,
    write_json,
)
from pyfireca.planning.strategy_map import render_strategy_map


def export_core_outcomes(inputs, replay, output):
    best, strategy = replay["best_ids"], replay["strategy_result"]
    scenarios = strategy["strategy_problem"]["scenario_ids"]
    saved = {}
    for name in ("physical_root", "additional_root"):
        if name not in inputs.paths:
            continue
        for path in inputs.paths[name].glob("evaluations/*/check.json"):
            record = read_json(path)
            if record["demolished_ids"] != best:
                continue
            evidence = record["evidence"]
            native = read_json(path.with_name("evaluation.json"))
            if (
                native["evaluation_sha256"] != evidence["evaluation_sha256"]
                or native["metrics"]["core_outcomes"] != evidence["core_outcomes"]
                or native["diagnostics"]["inventory_sha256"] != inputs.inventory.sha256
                or not evidence["reached_horizon"]
                or record["status"] != "passed"
            ):
                raise ValueError(
                    "best-strategy outcomes differ from the native physical evaluation"
                )
            saved[record["scenario_id"]] = (path, record)
    if set(saved) != set(scenarios):
        raise ValueError("best strategy must have complete saved scenario coverage")
    rows, references = [], {}
    for scenario in scenarios:
        path, record = saved[scenario]
        evidence = record["evidence"]
        references[str(path)] = file_sha256(path)
        references[str(path.with_name("evaluation.json"))] = file_sha256(
            path.with_name("evaluation.json")
        )
        targets = {t.building_id for t in inputs.inventory.targets}
        if {r["building_id"] for r in evidence["core_outcomes"]} != targets:
            raise ValueError("saved outcomes do not cover every declared heritage target")
        for core in evidence["core_outcomes"]:
            rows.append(
                {
                    "scenario_id": scenario,
                    "actual_source_id": evidence["source_id"],
                    "source_replaced": evidence["source_replaced"],
                    "scale_m": evidence["scale_m"],
                    "building_id": core["building_id"],
                    "role": core["role"],
                    "ignited": core["ignited"],
                    "first_ignition_s": core["first_ignition_s"],
                    "maximum_valid_surface_temperature_K": core[
                        "maximum_valid_surface_temperature_K"
                    ],
                    "simulated_until_s": evidence["simulated_until_s"],
                }
            )
        native_ledger = read_json(path.with_name("evaluation.json"))["intervention"]
        ledger = read_json(output / "cost-summary.json")
        for native_name, export_name in (
            ("demolished_footprint_area_m2", "nominal_footprint_area_m2"),
            ("cleared_roof_union_area_m2", "cleared_roof_union_area_m2"),
            ("retained_shared_roof_area_m2", "retained_shared_roof_area_m2"),
        ):
            if not np.isclose(native_ledger[native_name], ledger[export_name], rtol=0, atol=1e-6):
                raise ValueError(
                    f"exported vector area differs from physical ledger: {export_name}"
                )
    with (output / "heritage-protection.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {
        "scenario_count": len(scenarios),
        "core_outcome_count": len(rows),
        "all_best_core_outcomes_unignited": all(not r["ignited"] for r in rows),
        "native_evaluation_references_sha256": references,
        "exported_areas_match_all_best_native_ledgers": True,
    }


def run(manifest_path, output, font_path):
    inputs = load_strategy_inputs(manifest_path)
    replay = replay_inventory(
        inputs.inventory,
        inputs.paths["physical_root"],
        inputs.paths.get("additional_root"),
        inputs.costs,
        inputs.manifest["search"]["max_saved_check_lookups"],
    )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ledger = export_strategy(inputs, replay, output)
    protection = export_core_outcomes(inputs, replay, output)
    render_strategy_map(inputs, replay, output, font_path)
    report = {
        "schema_version": "heritage-unified-strategy-validation-1",
        "village_id": inputs.manifest["village_id"],
        "input_validation_passed": True,
        "output_directory": str(output),
        "best_ids": replay["best_ids"],
        "optimality_proven_in_declared_candidate_set": replay["optimality_proven"],
        "new_physical_CA_checks": 0,
        "saved_physical_record_hits": replay["physical_record_hits"],
        "missing_physical_record_lookups": replay["uncomputed_scenario_lookups"],
        "whole_village_search_started": False,
        "case_request_sha256": inputs.receipt["case_request_sha256"],
        "physical_problem_sha256": inputs.receipt["physical_problem_sha256"],
        "inventory_sha256": inputs.receipt["inventory_sha256"],
        "materials": inputs.receipt["materials"],
        "area_and_costs": ledger,
        "protection": protection,
        "exporter_source_sha256": {
            str(p.relative_to(Path(__file__).resolve().parents[1])): file_sha256(p)
            for p in (
                Path(__file__).resolve(),
                Path(__file__).with_name("replay_heritage_strategy.py"),
                *(
                    Path(__file__).resolve().parents[1] / "src/pyfireca/planning" / name
                    for name in (
                        "study_io.py",
                        "strategy_io.py",
                        "strategy_map.py",
                        "heritage_strategy.py",
                        "building_search.py",
                    )
                ),
            )
        },
        "output_sha256": {p.name: file_sha256(p) for p in sorted(output.iterdir()) if p.is_file()},
    }
    write_json(output / "validation.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Medium.ttc")
    )
    args = parser.parse_args()
    result = run(args.manifest, args.output, args.font)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "best_ids",
                    "new_physical_CA_checks",
                    "saved_physical_record_hits",
                    "missing_physical_record_lookups",
                    "optimality_proven_in_declared_candidate_set",
                )
            }
        )
    )
