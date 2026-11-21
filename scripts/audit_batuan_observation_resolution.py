"""Quantify shared-cell annotation resolution without changing the observer or solving CA."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.observations import build_object_observations
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(output):
    protocol = json.loads((output / "protocol.json").read_text())
    for name, expected in {**protocol["source_hashes"], **protocol["input_hashes"]}.items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"frozen source/input changed: {name}")
    annotations = output / "buildings.source.json"
    if sha(annotations) != protocol["annotation_sha256"]:
        raise ValueError("frozen annotation snapshot changed")
    bundle = load_v3_case_bundle(ROOT / "runs/batuan-slope-contact-20261016/case")
    if request_fingerprint(bundle.request) != protocol["base_fingerprint"]:
        raise ValueError("full-domain input fingerprint differs")
    structures = json.loads(annotations.read_text())["buildings"]
    observer = build_object_observations(
        bundle.request, structures, bundle.metadata["building_labels"]
    )
    if observer.sha256 != protocol["full_observation"]["observation_sha256"]:
        raise ValueError("full observation geometry differs")
    owners = bundle.request.building_index.ravel()
    cell_area = bundle.request.grid.cell_size_m**2
    cell_counts = np.zeros(owners.size, dtype=np.int32)
    for obj in observer.objects:
        cell_counts[obj.cell_ids] += 1
    rows = []
    for obj in observer.objects:
        ids, areas = obj.cell_ids, obj.areas_m2
        cross_owner = owners[ids] != obj.object_id
        shared = cell_counts[ids] > 1
        tiny = areas < 0.01 * cell_area
        rows.append(
            {
                "object_id": obj.object_id,
                "label": obj.label,
                "source_label": obj.source_label,
                "vector_area_m2": obj.vector_area_m2,
                "observed_area_m2": float(areas.sum()),
                "observation_cells": len(ids),
                "shared_cells": int(shared.sum()),
                "shared_observed_area_m2": float(areas[shared].sum()),
                "different_owner_cells": int(cross_owner.sum()),
                "different_owner_observed_area_m2": float(areas[cross_owner].sum()),
                "different_owner_area_fraction": float(areas[cross_owner].sum() / areas.sum()),
                "minimum_cell_intersection_m2": float(areas.min()),
                "cells_below_one_percent_grid_cell": int(tiny.sum()),
                "area_below_one_percent_grid_cell_m2": float(areas[tiny].sum()),
                "fuel_owners_observed": {
                    str(int(owner)): float(areas[owners[ids] == owner].sum())
                    for owner in np.unique(owners[ids])
                },
                "small_or_different_owner_cells": [
                    {
                        "row": int(cid // bundle.request.grid.width),
                        "column": int(cid % bundle.request.grid.width),
                        "fuel_owner_id": int(owners[cid]),
                        "intersection_area_m2": float(area),
                    }
                    for cid, area in zip(
                        ids[tiny | cross_owner], areas[tiny | cross_owner], strict=True
                    )
                ],
            }
        )
    geometry = {
        s["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in s["polygons"]])
        for s in structures
    }
    byid = {r["object_id"]: r for r in rows}
    bridge_pairs = []
    for row in rows:
        if row["source_label"] != "fengyuqiao":
            continue
        for owner in map(int, row["fuel_owners_observed"]):
            if owner == row["object_id"]:
                continue
            other = byid[owner]["label"]
            a, b = geometry[row["label"]], geometry[other]
            bridge_pairs.append(
                {
                    "bridge": row["label"],
                    "other_label": other,
                    "vector_gap_m": a.distance(b),
                    "vector_overlap_m2": a.intersection(b).area,
                    "bridge_area_in_other_owner_cells_m2": row["fuel_owners_observed"][str(owner)],
                }
            )
    return {
        "protocol_sha256": sha(output / "protocol.json"),
        "observation_sha256": observer.sha256,
        "grid_cell_size_m": bundle.request.grid.cell_size_m,
        "object_count": len(rows),
        "shared_cells": int((cell_counts > 1).sum()),
        "objects_with_shared_cells": sum(r["shared_cells"] > 0 for r in rows),
        "objects_with_different_owner_cells": sum(r["different_owner_cells"] > 0 for r in rows),
        "objects_with_small_intersections": sum(
            r["cells_below_one_percent_grid_cell"] > 0 for r in rows
        ),
        "objects_without_own_fuel_owner": [
            r["label"] for r in rows if str(r["object_id"]) not in r["fuel_owners_observed"]
        ],
        "objects": rows,
        "bridge_shared_owner_geometry": bridge_pairs,
        "interpretation": (
            "The frozen observer reports ignition when any intersected modeled roof cell ignites. "
            "Shared cells have one temperature and one fuel supply. Geometric intersections are "
            "not independent subcell thermal predictions. Small-area bins are diagnostics, "
            "not replacement ignition thresholds; no observations are removed or rescored."
        ),
        "ca_solves": 0,
        "held_or_internal_outcomes_read": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.output.resolve())
    dest = args.output / "independent-audits/observation-resolution.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "objects"}))
    print(json.dumps([r for r in result["objects"] if r["source_label"] == "fengyuqiao"]))
