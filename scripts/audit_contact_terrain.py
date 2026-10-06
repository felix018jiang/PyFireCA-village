"""Contact-terrain-only ablation at Gaoyou grass 159,353; never a production preset."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def audit(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    crop_v3_case_bundle(
        source,
        output / "case",
        row_start=151,
        row_stop=168,
        col_start=345,
        col_stop=362,
        end_time_s=600,
        sample_interval_s=10,
        ignition=VillageIgnitionEvent(0, 8, 8),
    )
    bundle = load_v3_case_bundle(output / "case")
    rows = []
    for mode in ("original_terrain", "flat_contact_terrain_only"):
        request = replace(
            bundle.request,
            ground_elevation_m=bundle.request.ground_elevation_m
            if mode == "original_terrain"
            else None,
        )
        result = run_v3_simulation(request, retain_snapshots=False)
        rows.append(
            dict(
                mode=mode,
                ignited_cells=int(np.isfinite(result.final_state.ignition_time_s).sum()),
                burned_out_cells=int((result.final_state.state == 4).sum()),
                consumed_fuel_kg=result.consumed_fuel_kg,
            )
        )
    report = dict(
        source=[159, 353],
        crop=[17, 17],
        duration_s=600,
        results=rows,
        unchanged="material, moisture, burn curve, ignition threshold, precomputed radiation edges",
        experimental_control="suppress terrain in contact gate only; keep radiation geometry",
        scope="numerical mechanism diagnostic; not a prediction or proposed terrain removal",
    )
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "runs/gaoyou-core-full-20261006/case")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.source, args.output)
