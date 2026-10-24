"""Attribute saved bridge exposure to roof identities without new thermal runs."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from pyfireca.village.physics_v2 import _burn_curve_antiderivative
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def attribute(folder, target_id):
    request = load_v3_case_bundle(folder / "node-storage-not-map").request
    layout = json.loads((folder / "layout.json").read_text())
    record = json.loads((folder / "evaluation.json").read_text())
    owners = {part["node"]: tuple(part["building_ids"]) for part in layout["roof_components"]}
    targets = np.asarray([node for node, ids in owners.items() if target_id in ids], int)
    with np.load(folder / "final-state.npz") as state:
        times = state["ignition_time_s"].ravel()
        finite = targets[np.isfinite(times[targets])]
        if not len(finite):
            raise ValueError("bridge path audit requires an observed core ignition")
        critical = int(finite[np.argmin(times[finite])])
        clock = float(times[critical])
        released_final = state["released_energy_J"].ravel()
        expected = float(state["incident_radiation_J"].ravel()[targets].sum())
    physics, edges = request.parameters, request.exposure_edges
    active = np.flatnonzero(np.isfinite(times) & (times < clock))
    released = np.zeros(request.grid.width)
    released[active] = physics.peak_hrr_W.ravel()[active] * _burn_curve_antiderivative(
        clock - times[active],
        physics.growth_time_s.ravel()[active],
        physics.steady_time_s.ravel()[active],
        physics.decay_time_s.ravel()[active],
    )
    released = np.minimum(released, physics.available_energy_J.ravel())
    take = edges.target == critical
    source_nodes = edges.source[take]
    contributions = edges.weight[take] * released[source_nodes]
    contributions *= physics.radiative_fraction.ravel()[source_nodes]
    by_identity = defaultdict(float)
    for node, energy in zip(source_nodes, contributions, strict=True):
        by_identity["+".join(owners.get(int(node), ())) or "ground"] += float(energy)
    total = sum(by_identity.values())
    whole = np.isin(edges.target, targets)
    reconstructed = float(
        np.sum(
            edges.weight[whole]
            * released_final[edges.source[whole]]
            * physics.radiative_fraction.ravel()[edges.source[whole]]
        )
    )
    if not np.isclose(reconstructed, expected, rtol=1e-10, atol=1e-4):
        raise AssertionError("saved release does not reconstruct bridge incident radiation")
    with np.load(folder / "contact.npz") as contact:
        sources = np.flatnonzero(np.any(contact["neighbors"] == critical, axis=1))
        external = sorted(
            {
                "+".join(owners.get(int(node), ())) or "ground"
                for node in sources
                if target_id not in owners.get(int(node), ())
            }
        )
    return {
        "case": str(folder),
        "source_ids": record["metrics"]["actual_source_ids"],
        "first_bridge_ignition_s": clock,
        "critical_node": critical,
        "external_contact_source_identities": external,
        "critical_incident_radiation_J": total,
        "critical_radiation_contributors": [
            {"owners": identity.split("+"), "energy_J": energy, "fraction": energy / total}
            for identity, energy in sorted(by_identity.items(), key=lambda item: -item[1])
            if energy > 0
        ],
        "building_first_ignitions": record["metrics"]["building_first_ignitions"],
        "final_bridge_incident_reconstruction_error_J": abs(reconstructed - expected),
        "interpretation": "incident_energy_at_first_ignition_not_unique_causal_paths",
    }


def audit(root, study):
    spec = importlib.util.spec_from_file_location(
        "roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    _, _, inventory = runner.load_study(study)
    core = inventory.by_id["B0120"].geometry
    nearby = []
    for building in inventory.buildings:
        distance = building.geometry.distance(core)
        if building.building_id == "B0120" or distance > 30:
            continue
        overlaps = []
        for other in inventory.buildings:
            if other.building_id == building.building_id:
                continue
            area = building.geometry.intersection(other.geometry).area
            if area > 1e-6:
                overlaps.append({"other_id": other.building_id, "area_m2": area})
        nearby.append(
            {
                "building_id": building.building_id,
                "distance_to_bridge_m": distance,
                "footprint_area_m2": building.geometry.area,
                "overlaps": overlaps,
            }
        )
    return {
        "schema_version": "heritage-bridge-path-audit-1",
        "new_thermal_runs": 0,
        "inventory_sha256": inventory.sha256,
        "neighbours": sorted(nearby, key=lambda row: row["distance_to_bridge_m"]),
        "runs": [
            attribute(folder, "B0120") for folder in sorted(root.glob("near-B0120-*/baseline"))
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(audit(args.root, args.study), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    )
