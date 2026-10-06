"""Frozen local terrain-case ablation, not real-fire validation.

The point is the first ignition in the earlier vegetation-front experiment;
we freeze it before comparing the two transport mechanisms. Material classes
and parameters remain RGB hypotheses and synthetic equivalents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def run_crop_audit(output: Path, source: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    study = {
        "source_case": str(source.resolve()),
        "source_manifest_sha256": hashlib.sha256(
            (source / "manifest.json").read_bytes()
        ).hexdigest(),
        "rows": [183, 200],
        "cols": [382, 399],
        "ignition_global_row_col": [191, 390],
        "end_time_s": 600,
        "max_dt_s": [0.5, 0.25],
        "evidence": "same_existing_terrain_RGB_hypothesis_case_not_observed_fire",
        "contact": load_flame_contact_parameters(
            DIRECTORY / "flame-contact.synthetic.yml"
        ).to_mapping(),
    }
    (output / "study.resolved.json").write_text(json.dumps(study, indent=2) + "\n")
    crop_v3_case_bundle(
        source,
        output / "base-case",
        row_start=183,
        row_stop=200,
        col_start=382,
        col_stop=399,
        end_time_s=600,
        sample_interval_s=30,
        ignition=VillageIgnitionEvent(0, 8, 8),
    )
    base = load_v3_case_bundle(output / "base-case")
    rows = []
    for enabled in (False, True):
        for step in (0.5, 0.25):
            contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
            request = replace(
                base.request,
                flame_contact=contact if enabled else None,
                scenario=replace(base.request.scenario, max_dt_s=step),
            )
            name = f"{'contact' if enabled else 'radiation'}-dt{step:g}"
            case_path = output / f"{name}-case"
            write_v3_case_bundle(
                request,
                case_path,
                material_labels=base.material_labels,
                landcover_labels=base.landcover_labels,
                metadata=base.metadata,
            )
            if step == 0.25:
                result, _ = run_v3_case_bundle(case_path, output / f"{name}-run", chunk_frames=4)
            else:
                result = run_v3_simulation(request, retain_snapshots=False)
            final = result.final_state
            ignited = np.isfinite(final.ignition_time_s)
            rows.append(
                {
                    "contact": enabled,
                    "dt_s": step,
                    "ignited_cells": int(np.count_nonzero(ignited)),
                    "ignited_building_cells": int(
                        np.count_nonzero(ignited & (request.building_index >= 0))
                    ),
                    "ignited_by_landcover": {
                        label: int(np.count_nonzero(ignited & (request.landcover_index == index)))
                        for index, label in base.landcover_labels.items()
                    },
                    "energy_budget": asdict(result.energy_budget),
                    "consumed_fuel_kg": result.consumed_fuel_kg,
                }
            )
            np.save(output / f"{name}-ignition.npy", final.ignition_time_s, allow_pickle=False)
    summary = {
        "runs": len(rows),
        "cells": base.request.grid.height * base.request.grid.width,
        "numerical_count_consistency": all(
            rows[2 * i]["ignited_cells"] == rows[2 * i + 1]["ignited_cells"] for i in (0, 1)
        ),
        "field_validation": False,
        "crop_boundary": "no external sources; cannot infer whole-domain fire outcome",
        "results": rows,
    }
    (output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "runs/gaoyou-vegetation-v3/v12-gaoyou-terrain-coupled-full-domain-case",
    )
    args = parser.parse_args()
    print(json.dumps(run_crop_audit(args.output, args.source), indent=2))
