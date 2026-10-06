"""Contact height and time-step sensitivity using unchanged village physics."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def audit(case_root: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    records = []
    for source in ("grass", "shrub"):
        case = case_root / source / "case"
        base = load_v3_case_bundle(case).request
        for height in (0.1, 0.2, 0.5):
            for dt in (0.25, 0.125):
                request = replace(
                    base,
                    flame_contact=replace(
                        base.flame_contact, receiver_height_overrides_m=((5, height),)
                    ),
                    scenario=replace(base.scenario, max_dt_s=dt),
                    sample_interval_s=None,
                )
                result = run_v3_simulation(request, retain_snapshots=False)
                times = result.final_state.ignition_time_s
                ignited = np.isfinite(times)
                budget = result.energy_budget
                closure = abs(
                    budget.non_radiative_released_J
                    - budget.convective_transferred_J
                    - budget.nonradiative_untransferred_J
                )
                if closure > 1e-5 + 1e-9 * budget.released_J:
                    raise AssertionError("contact energy budget did not close")
                record = dict(
                    source=source,
                    source_case=str(case.resolve()),
                    contact_height_m=height,
                    dt_s=dt,
                    ignited=int(ignited.sum()),
                    buildings_ignited=int((ignited & (request.material_index == 5)).sum()),
                    contact_energy_closure_error_J=closure,
                    ignition_times_s=np.where(ignited, times, -1).tolist(),
                )
                records.append(record)
                print(
                    {key: value for key, value in record.items() if key != "ignition_times_s"},
                    flush=True,
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    audit(args.case_root, args.output)
