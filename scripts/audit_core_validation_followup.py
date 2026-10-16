"""Resolve finite-horizon ignition differences and diagnose point-height sensitivity."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def audit(folder):
    report_path = folder / "report.json"
    report = json.loads(report_path.read_text())
    r = load_v3_case_bundle(folder / "crops/shrub/base").request
    results = []
    rows = []
    for dt in [1.0, 0.5, 0.25, 0.125]:
        q = replace(r, scenario=replace(r.scenario, max_dt_s=dt, end_time_s=1260))
        x = run_v3_simulation(q, retain_snapshots=False)
        results.append(x)
        rows.append(
            {
                "dt_s": dt,
                "cell_145_255_ignition_s": float(x.final_state.ignition_time_s[26, 12]),
                **check_final(q, x),
            }
        )
    endpoint = {
        "end_time_s": 1260,
        "runs": rows,
        "1_vs_0125": compare_steps(results[0], results[-1]),
        "025_vs_0125": compare_steps(results[2], results[-1]),
    }
    r = load_v3_case_bundle(folder / "crops/grass/base").request
    rows = []
    for delta in [-0.0001, 0, 0.0001]:
        overrides = dict(r.flame_contact.receiver_height_overrides_m)
        overrides[1] = 0.15 + delta
        contact = replace(r.flame_contact, receiver_height_overrides_m=tuple(overrides.items()))
        q = replace(r, flame_contact=contact, scenario=replace(r.scenario, max_dt_s=0.25))
        x = run_v3_simulation(q, retain_snapshots=False)
        rows.append({"grass_contact_height_delta_m": delta, **check_final(q, x)})
    full = report["stages"]["full_village"][0]
    r = load_v3_case_bundle(full["case"]).request
    row, col = full["point"]["row"], full["point"]["col"]
    sid = row * r.grid.width + col
    op = FlameContactOperator.build(
        r.grid,
        r.burnable_mask,
        r.parameters,
        r.flame_contact,
        r.ground_elevation_m,
        r.material_index,
    )
    source_ground = r.ground_elevation_m[row, col]
    top = source_ground + r.parameters.source_height_m[row, col]
    neighbors = []
    for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        nr, nc = row + dr, col + dc
        tid = nr * r.grid.width + nc
        height = dict(r.flame_contact.receiver_height_overrides_m).get(
            int(r.material_index[nr, nc]), r.parameters.receiver_height_m[nr, nc]
        )
        z = r.ground_elevation_m[nr, nc] + height
        neighbors.append(
            {
                "row": nr,
                "col": nc,
                "material": int(r.material_index[nr, nc]),
                "burnable": bool(r.burnable_mask[nr, nc]),
                "terrain_delta_m": float(r.ground_elevation_m[nr, nc] - source_ground),
                "receiver_minus_source_top_m": float(z - top),
                "receiver_minus_source_base_m": float(z - source_ground),
                "contact_edge_present": bool(tid in op.neighbors[sid]),
            }
        )
    followup = {
        "input_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "endpoint_followup": endpoint,
        "point_geometry_sensitivity": {
            "runs": rows,
            "radiation_unchanged": True,
            "exploratory_only": True,
            "default_modified": False,
        },
        "grass_neighbor_diagnostics": neighbors,
        "engineering_validation_only": True,
    }
    (folder / "followup.json").write_text(json.dumps(followup, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "endpoint_counts": [r["ignited_cells"] for r in endpoint["runs"]],
                "grass_sensitivity_counts": [r["ignited_cells"] for r in rows],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return followup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    audit(args.folder)
