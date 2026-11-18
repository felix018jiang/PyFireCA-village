"""Freeze new roof anchors only after the bounded design winner is saved."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol, realize_sources
from pyfireca.planning.retained_sources import with_global_fallback
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study


def prepare(config_path, design_root, destination):
    config_path, design_root, destination = map(
        lambda p: Path(p).resolve(), (config_path, design_root, destination)
    )
    config, result = read_json(config_path), read_json(design_root / "search-result.json")
    if result["best_ids"] is None:
        raise ValueError("no verified feasible plan to freeze for confirmation")
    if destination.exists():
        raise FileExistsError("held-out protocol is already frozen")
    _, _, inventory = load_study(config_path.parent / config["study_path"])
    original = freeze_source_protocol(
        inventory, seed=2026102921, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    draws = list(original["draws"])
    covered = {d["stratum"] for d in draws}
    empty = []
    for target in inventory.targets:
        name = f"near-{target.building_id}"
        if name in covered:
            continue
        geometry = inventory.by_id[target.building_id].geometry
        ids = sorted(
            i for i in original["members"] if geometry.distance(inventory.by_id[i].geometry) <= 60
        )
        if not ids:
            raise ValueError("empty near-core stratum needs a declared pressure source pool")
        rng = np.random.default_rng(2026102922)
        priority = rng.permutation(ids).tolist()
        draws.append(
            {
                "draw_id": name + "-nearest60-001",
                "stratum": name,
                "priority_ids": priority,
                "baseline_source_id": priority[0],
            }
        )
        empty.append(
            {
                "core_id": target.building_id,
                "within15m_count": 0,
                "pressure_source_pool_within60m": ids,
                "interpretation": "separate_targeted_pressure;not_15m_stratum_risk",
            }
        )
    reference = read_json(design_root / "experiment.json")
    old_shapes = [
        p["geometry"]
        for fire in reference["problem"]["fires"].values()
        for p in fire["footprints"].values()
    ]
    from shapely.geometry import shape

    old_geometry = [shape(g) for g in old_shapes]
    new_geometry, source_audit, fires = [], [], []
    for number, draw in enumerate(draws):
        item = {**original, "draws": [draw]}
        item.pop("protocol_sha256")
        item["protocol_sha256"] = content_hash(item)
        protocol = with_global_fallback(item, inventory, fallback_seed=2026102930 + number)
        for arm, ids in (("baseline", ()), ("frozen_plan", tuple(result["best_ids"]))):
            realization = realize_sources(protocol, inventory, demolished_ids=ids)[0]
            footprint = realization["footprint"].geometry
            duplicates = [
                index
                for index, previous in enumerate(old_geometry)
                if footprint.equals_exact(previous, 1e-7)
            ]
            source_audit.append(
                {
                    "draw_id": draw["draw_id"],
                    "arm": arm,
                    "source_id": realization["source_id"],
                    "area_m2": footprint.area,
                    "duplicates_design_polygon_indices": duplicates,
                }
            )
            if arm == "baseline":
                new_geometry.append(footprint)
        name = destination.stem + f"-source-{number + 1}.json"
        write_json(destination.parent / name, protocol)
        fires.append(
            {"fire_id": draw["draw_id"], "draw_id": draw["draw_id"], "protocol_path": name}
        )
    audit = {
        "seed": 2026102921,
        "created_after_frozen_design_result": True,
        "design_result_sha256": file_sha256(design_root / "search-result.json"),
        "empty_near_strata": empty,
        "actual_source_polygons": source_audit,
        "no_design_polygon_repeated": not any(
            r["duplicates_design_polygon_indices"] for r in source_audit
        ),
        "distinct_baseline_polygons": all(
            not a.equals_exact(b, 1e-7)
            for n, a in enumerate(new_geometry)
            for b in new_geometry[n + 1 :]
        ),
        "draw_count": len(draws),
        "dual_scales_are_not_independent_samples": True,
    }
    output = {
        **config,
        "run_mode": "fixed_confirmation",
        "frozen_plan_ids": result["best_ids"],
        "stop_at_first_core_ignition": False,
        "fires": fires,
        "max_total_checks": len(draws) * 4,
        "max_wall_time_s": 7200,
        "parent_design_result_sha256": audit["design_result_sha256"],
        "heldout_source_audit": audit,
        "scope": "fixed_winner_vs_baseline;new_source_polygons;no_reoptimization",
    }
    write_json(destination, output)
    print(
        {
            "frozen_plan": result["best_ids"],
            "draws": len(draws),
            "CA_budget": len(draws) * 4,
            "no_design_polygon_repeated": audit["no_design_polygon_repeated"],
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--design-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.config, args.design_root, args.output)
