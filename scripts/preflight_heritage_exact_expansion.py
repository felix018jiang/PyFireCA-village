"""Bound geometry capacity and possible-source coverage, without running any CA."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path

import numpy as np
from shapely import points
from shapely.strtree import STRtree

from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refine_roof_layout


def edge_bounds(layout, transport, refined_ids):
    """Count raw point pairs and upper-bound integrated pairs in small batches.

    Demolition only removes sources/targets on this common mesh, so the intact
    layout bounds every allowed demolition. No kernel weights or CA are built.
    """
    source = np.flatnonzero(layout.thermal.source_mask)
    target = np.flatnonzero(layout.thermal.target_mask)
    selected = np.array([bool(set(ids) & set(refined_ids)) for ids in layout.owners])
    tree = STRtree(points(layout.xy_m[target]))
    raw, ordinary = 0, 0
    for start in range(0, len(source), 128):
        ids = source[start : start + 128]
        pair = tree.query(
            points(layout.xy_m[ids]), predicate="dwithin", distance=transport.exposure_radius_m
        )
        s, t = ids[pair[0]], target[pair[1]]
        valid = s != t
        raw += int(valid.sum())
        ordinary += int((valid & ~selected[s] & ~selected[t]).sum())

    def polygon_pairs(sources, targets):
        if not len(sources) or not len(targets):
            return 0
        tree = STRtree([layout.geometry[i] for i in targets])
        count = 0
        for start in range(0, len(sources), 128):
            ids = sources[start : start + 128]
            pair = tree.query(
                [layout.geometry[i] for i in ids],
                predicate="dwithin",
                distance=transport.exposure_radius_m,
            )
            count += int((ids[pair[0]] != targets[pair[1]]).sum())
        return count

    area_upper = ordinary + polygon_pairs(source[selected[source]], target)
    area_upper += polygon_pairs(source[~selected[source]], target[selected[target]])
    return {"raw_point_edges": raw, "integrated_edges_upper_bound": area_upper}


def preflight(config_path):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text())
    spec = importlib.util.spec_from_file_location(
        "preflight_roof_study", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    bundle, transport, inventory = runner.load_study(config_path.parent / config["study_path"])
    fires = []
    for row in config["fires"]:
        original = json.loads((config_path.parent / row["protocol_path"]).read_text())
        assert original["protocol_sha256"] == content_hash(
            {k: v for k, v in original.items() if k != "protocol_sha256"}
        )
        protocol = {
            **original,
            "draws": [d for d in original["draws"] if d["draw_id"] == row["draw_id"]],
        }
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        fires.append({"fire_id": row["fire_id"], "protocol": protocol})
    rows, summaries, geometry_cache = [], [], {}
    for scope, candidates in (
        ("expanded8", tuple(config["candidate_ids"])),
        ("all331_eligibility_geometry_only", inventory.candidate_ids),
    ):
        oracle = RoofDemolitionOracle(
            bundle,
            transport,
            inventory,
            candidate_ids=candidates,
            fires=fires,
            code_version="geometry-preflight-only",
            refinement_ids=config["refinement_ids"],
            maximum_demolitions=config["maximum_demolitions"],
        )
        source_coverage = []
        for label, fire in oracle.fires.items():
            priority = fire["protocol"]["draws"][0]["priority_ids"]
            # Full331 source coverage uses the exact prefix argument, not 6m
            # combinations. Expanded8 additionally checks every <=3 subset.
            checked = 0
            if scope == "expanded8":
                for k in range(config["maximum_demolitions"] + 1):
                    for removed in itertools.combinations(candidates, k):
                        actual = next((i for i in priority if i not in removed), None)
                        assert actual is None or actual in fire["footprints"]
                        checked += 1
            source_coverage.append(
                {
                    "fire_id": label,
                    "possible_sources": list(fire["footprints"]),
                    "prefix_bound": config["maximum_demolitions"] + 1,
                    "expanded_plans_checked": checked,
                    "empty_source_is_unknown": True,
                }
            )
            for scale in config["roof_scales_m"]:
                key = content_hash(
                    [
                        scale,
                        fire["refined_ids"],
                        [f.to_mapping() for f in fire["footprints"].values()],
                    ]
                )
                if key not in geometry_cache:
                    layout = refine_roof_layout(
                        oracle.base,
                        inventory,
                        cell_size_m=scale,
                        refined_ids=fire["refined_ids"],
                        footprints=tuple(fire["footprints"].values()),
                        max_nodes=config["max_nodes"],
                    )
                    bounds = edge_bounds(layout, transport, fire["refined_ids"])
                    geometry_cache[key] = {
                        "nodes": len(layout.owners),
                        **bounds,
                        "capacity_passed": len(layout.owners) <= config["max_nodes"]
                        and max(bounds.values()) <= config["max_radiation_edges"],
                        "conservation_max_abs_error": layout.conservation,
                    }
                row = {
                    "scope": scope,
                    "fire_id": label,
                    "scale_m": scale,
                    "refined_ids": list(fire["refined_ids"]),
                    "geometry_key": key,
                    **geometry_cache[key],
                }
                rows.append(row)
                print(
                    json.dumps(
                        {
                            k: row[k]
                            for k in ("scope", "fire_id", "scale_m", "nodes", "capacity_passed")
                        }
                    ),
                    flush=True,
                )
        dominance = oracle.proven_dominated_candidates()
        exclusive = {}
        for i in candidates:
            nodes = [j for j, owners in enumerate(oracle.base.owners) if owners == (i,)]
            exclusive[i] = float(sum(oracle.base.geometry[j].area for j in nodes))
        summaries.append(
            {
                "scope": scope,
                "candidate_count": len(candidates),
                "source_coverage": source_coverage,
                "strict_dominance_candidates": sorted(dominance),
                "candidates_with_positive_exclusive_thermal_area": sum(
                    a > 0 for a in exclusive.values()
                ),
                "B0149_exclusive_thermal_area_m2": exclusive.get("B0149"),
                "base_nodes": len(oracle.base.owners),
            }
        )
    return {
        "schema_version": "heritage-exact-expansion-preflight-1",
        "complete": True,
        "config_sha256": content_hash(config),
        "new_CA_checks": 0,
        "whole_village_search_started": False,
        "node_limit": config["max_nodes"],
        "radiation_edge_limit": config["max_radiation_edges"],
        "all_capacity_bounds_passed": all(r["capacity_passed"] for r in rows),
        "summaries": summaries,
        "layouts": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = preflight(args.config)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"all_capacity_bounds_passed": report["all_capacity_bounds_passed"]}))
