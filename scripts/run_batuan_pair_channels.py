"""Separate radiation/contact in the unresolved closest Batuan building pair."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import ROOT, dump, sha

from pyfireca.planning.building_pairs import isolate_pair_request
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def run(task):
    output, revision, source, channel = task
    parent = ROOT / f"runs/research/batuan-building-pairs-20261017-{revision}"
    protocol = json.loads((parent / "protocol.json").read_text())
    pair = next(p for p in protocol["selected"] if p["pair_id"] == "B0163-B0180")
    bundle = load_v3_case_bundle(protocol["cases"][pair["pair_id"]])
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    request = isolate_pair_request(
        bundle,
        pair,
        source,
        transport,
        ground_enabled=False,
        source_profile=protocol.get("source_profile", "single_cell"),
    )
    if channel == "radiation_only":
        request = replace(request, flame_contact=None)
    elif channel == "contact_only":
        edges = ExposureEdges(
            np.array([], dtype=np.int64),
            np.array([], dtype=np.int64),
            np.array([], dtype=float),
            request.exposure_edges.cell_count,
        )
        request = replace(request, exposure_edges=edges)
    result = run_v3_simulation(request, retain_snapshots=False)
    target = next(i for i in pair["owner_ids"] if i != source)
    mask = request.building_index == target
    values = result.final_state.ignition_time_s[mask]
    values = values[np.isfinite(values)]
    record = {
        "profile": protocol.get("source_profile", "single_cell"),
        "source_owner": source,
        "receiver_owner": target,
        "channel": channel,
        "first_receiver_ignition_s": float(values.min()) if len(values) else None,
        "receiver_absorbed_radiation_J": float(result.final_state.absorbed_radiation_J[mask].sum()),
        "receiver_contact_energy_J": float(result.final_state.received_convection_J[mask].sum()),
        "actual_horizon_s": result.final_state.time_s,
        "validation": check_final(request, result),
        "scope": "5m raster has no resolved 3.5m gap; not a real clearance threshold",
    }
    path = Path(output) / f"{revision}-from{source}-{channel}.json"
    dump(path, record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    sources = [
        *ROOT.joinpath("src/pyfireca/village").glob("*.py"),
        *ROOT.joinpath("src/pyfireca/planning").rglob("*.py"),
        Path(__file__),
    ]
    dump(
        out / "protocol.json",
        {
            "pair": "B0163-B0180",
            "profiles": ["single_cell", "source_roof_cells"],
            "horizon_s": 5400,
            "dt_s": 1,
            "ground_enabled": False,
            "source_hashes": {str(p.relative_to(ROOT)): sha(p) for p in sources},
            "channels": ["both", "radiation_only", "contact_only"],
            "policy": "preserve radiative fraction; removed channel energy not redistributed",
        },
    )
    tasks = [
        (str(out), r, s, c)
        for r in ["r2", "r3"]
        for s in [162, 179]
        for c in ["both", "radiation_only", "contact_only"]
    ]
    with ProcessPoolExecutor(2) as pool:
        records = list(pool.map(run, tasks))
    dump(out / "report.json", {"records": records, "protocol_sha256": sha(out / "protocol.json")})
    print(json.dumps(records, ensure_ascii=False))
