"""Read-only attribution of saved full-village incident energy, no thermal solves."""

import argparse
import json
from pathlib import Path

import numpy as np

from pyfireca.village.physics_v2 import _burn_curve_antiderivative
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def attribute(folder, source_id, target_id):
    request = load_v3_case_bundle(folder / "node-storage-not-map").request
    layout = json.loads((folder / "layout.json").read_text())
    record = json.loads((folder / "evaluation.json").read_text())
    count = request.grid.width
    groups = np.full(count, 3, int)
    targets = []
    for part in layout["roof_components"]:
        node, owners = part["node"], part["building_ids"]
        if owners == [source_id]:
            groups[node] = 0 if part["prescribed_ignition_source_id"] == source_id else 1
        elif source_id in owners:
            groups[node] = 2
        if owners == [target_id]:
            targets.append(node)
    targets = np.asarray(targets)
    with np.load(folder / "final-state.npz") as data:
        times = data["ignition_time_s"].ravel()
        finite = times[targets][np.isfinite(times[targets])]
        clock = float(finite.min()) if len(finite) else float(data["time_s"])
        critical = (
            int(targets[np.nanargmin(times[targets])])
            if len(finite)
            else int(
                targets[
                    np.argmax(
                        np.asarray(
                            record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"]
                        )[targets]
                    )
                ]
            )
        )
        final_released = data["released_energy_J"].ravel()
        final_incident = data["incident_radiation_J"].ravel()
    p, edges = request.parameters, request.exposure_edges

    def incoming(released, target_ids):
        take = np.isin(edges.target, target_ids)
        s = edges.source[take]
        return np.bincount(
            groups[s],
            weights=edges.weight[take] * released[s] * p.radiative_fraction.ravel()[s],
            minlength=4,
        )

    reconstructed = incoming(final_released, targets).sum()
    expected = float(final_incident[targets].sum())
    if not np.isclose(reconstructed, expected, rtol=1e-10, atol=1e-4):
        raise AssertionError("saved source release does not reconstruct incident radiation")
    active = np.flatnonzero(np.isfinite(times) & (times < clock))
    released = np.zeros(count)
    released[active] = p.peak_hrr_W.ravel()[active] * _burn_curve_antiderivative(
        clock - times[active],
        p.growth_time_s.ravel()[active],
        p.steady_time_s.ravel()[active],
        p.decay_time_s.ravel()[active],
    )
    released = np.minimum(released, p.available_energy_J.ravel())
    values = incoming(released, [critical])
    labels = (
        "prescribed_source_patch",
        "secondary_unambiguous_source_roof",
        "ambiguous_roof_containing_source_id",
        "other_objects",
    )
    return {
        "evaluation": str(folder / "evaluation.json"),
        "clock_s": clock,
        "critical_core_node": critical,
        "critical_core_incident_J_at_clock": dict(zip(labels, map(float, values), strict=True)),
        "critical_core_incident_fractions_at_clock": dict(
            zip(labels, map(float, values / values.sum()), strict=True)
        ),
        "full_core_final_incident_J": expected,
        "incident_reconstruction_error_J": float(abs(reconstructed - expected)),
        "interpretation": "incoming energy contributions, not unique ignition causation",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    original = json.loads(
        (ROOT / "docs/results/heritage-ignition-scale-20261023.json").read_text()
    )["runs"]
    rows = []
    for row in original:
        if row["h_m"] not in (1.0, 0.5) or row["phase"] != [0.0, 0.0] or row["dt_s"] != 1.0:
            continue
        result = attribute(Path(row["evaluation"]).parent, row["source_id"], row["target_id"])
        rows.append({"probe": row["probe_id"], "area": row["area_m2"], "h_m": row["h_m"], **result})
    args.output.write_text(json.dumps({"runs": rows, "new_simulations": 0}, indent=2) + "\n")
