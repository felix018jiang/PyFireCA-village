"""Finite receiver sensitivity at a frozen Gaoyou crop; no parameter fitting."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def audit(case, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    bundle = load_v3_case_bundle(case)
    rows = []
    for extent in (0, 0.05, 0.1, 0.15, 0.2, 0.3):
        for dt in (1.0, 0.25):
            request = replace(
                bundle.request,
                scenario=replace(bundle.request.scenario, max_dt_s=dt),
                flame_contact=replace(
                    bundle.request.flame_contact, receiver_vertical_half_extent_m=extent
                ),
            )
            result = run_v3_simulation(request, retain_snapshots=False)
            budget = result.energy_budget
            closure = abs(
                budget.non_radiative_released_J
                - budget.convective_transferred_J
                - budget.nonradiative_untransferred_J
            )
            assert closure <= 1e-5 + 1e-9 * budget.released_J
            rows.append(
                dict(
                    half_extent_m=extent,
                    max_dt_s=dt,
                    ignited_cells=int(np.isfinite(result.final_state.ignition_time_s).sum()),
                    burned_out_cells=int((result.final_state.state == 4).sum()),
                    consumed_fuel_kg=result.consumed_fuel_kg,
                    energy_closure_error_J=closure,
                )
            )
    report = dict(
        source_case=str(Path(case).resolve()),
        results=rows,
        interpretation="sensitivity of an assumed receiver band; not field calibration",
        crop_boundary="no external fire sources",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(rows, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", type=Path, default=ROOT / "runs/core-contact-terrain-diagnostic-20261006-b/case"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.case, args.output)
