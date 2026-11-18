"""Freeze paired 60-minute Dong-village comparisons without changing physics."""

from __future__ import annotations

import copy
from pathlib import Path

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "examples/research/heritage"
OUT = CONFIG / "followup-20261103"
SELECTION = ROOT / "docs/results/heritage-followup-position-selection-20261103.json"

SOURCES = {
    "batuan": [
        {
            "fire_id": "drum-B0077-known-hot",
            "protocol_path": (
                "runs/research/heritage-triple-validation-20261024-c/source-protocol.json"
            ),
            "draw_id": "near-B0073-003",
            "source_id": "B0077",
            "footprint_sha256": "8f496c0e0100911578e3e4f97f2ee9b298d465fec747c75823a1c2e078c7e4a6",
            "role": "known_high_temperature_selected_plan_source_replayed_in_all_arms",
        },
        {
            "fire_id": "bridge-B0062-known",
            "protocol_path": (
                "examples/research/heritage/batuan-position-known-B0062-source-20261024.json"
            ),
            "draw_id": "near-B0120-002",
            "source_id": "B0062",
            "footprint_sha256": "44dcd0ae3c2ef6d2f8bd3d4589a3a8d84d755b02b07515af01875fe5e676bbd8",
            "role": "known_bridge_threat_replay",
        },
    ],
    "pingyan": [
        {
            "fire_id": "drum-B0164-late-ignition",
            "protocol_path": "examples/research/heritage/pingyan-heldout-20261029-source-3.json",
            "draw_id": "near-B0166-001",
            "source_id": "B0164",
            "footprint_sha256": "5b799696a15ce36c43a5300d1a76470385a18c1e67c3add440e90c5caf947727",
            "role": "previous_1169s_baseline_ignition_replay",
        },
        {
            "fire_id": "bridge-B0120-coverage",
            "protocol_path": "examples/research/heritage/pingyan-heldout-20261029-source-2.json",
            "draw_id": "near-B0035-001",
            "source_id": "B0120",
            "footprint_sha256": "3c324902d520acb8ee1c30d5e1f620010e0a6362d8621c688363a737b96507a8",
            "role": "previous_nonigniting_bridge_source_duration_coverage",
        },
    ],
}


def freeze_source(spec, inventory):
    """Keep keyed seed/draw/building anchors exactly; fix one retained source."""
    path = ROOT / spec["protocol_path"]
    original = read_json(path)
    if (
        content_hash({k: v for k, v in original.items() if k != "protocol_sha256"})
        != original["protocol_sha256"]
    ):
        raise ValueError("parent source protocol has changed")
    draw = next(d for d in original["draws"] if d["draw_id"] == spec["draw_id"])
    if spec["source_id"] not in draw["priority_ids"]:
        raise ValueError("prescribed source was not in the historical draw")
    frozen = copy.deepcopy(original)
    frozen.pop("protocol_sha256")
    frozen["draws"] = [
        {**draw, "priority_ids": [spec["source_id"]], "baseline_source_id": spec["source_id"]}
    ]
    frozen.update(
        parent_protocol_sha256=original["protocol_sha256"],
        parent_source_file_sha256=file_sha256(path),
        sampling="fixed_historical_roof_polygon_replay;not_new_independent_sampling",
        source_condition="one_prescribed_source_retained_in_every_comparison_arm",
        weight_policy="unweighted_paired_pressure_comparison;not_population_risk",
        followup_source_role=spec["role"],
    )
    frozen["protocol_sha256"] = content_hash(frozen)
    realized = realize_sources(frozen, inventory)[0]
    footprint_hash = content_hash(realized["footprint"].to_mapping())
    if realized["source_id"] != spec["source_id"] or footprint_hash != spec["footprint_sha256"]:
        raise ValueError("historical source polygon did not reproduce exactly")
    return frozen, {
        **spec,
        "parent_source_file_sha256": file_sha256(path),
        "polygon_verified": True,
        "ignition_area_m2": realized["footprint"].geometry.area,
    }


def main():
    selection = read_json(SELECTION)
    OUT.mkdir(parents=True, exist_ok=True)
    receipts = []
    for village in ("batuan", "pingyan"):
        parent_path = CONFIG / f"{village}-full-bounded-20261029.json"
        parent = read_json(parent_path)
        _, _, inventory = load_study(CONFIG / parent["study_path"])
        row = next(r for r in selection["villages"] if r["village_id"] == village)
        if inventory.sha256 != row["inventory_sha256"]:
            raise ValueError("geometry selection and physical study inventories differ")
        selected = row["existing_plan"]["demolished_ids"]
        control = row["selected_control"]["demolished_ids"]
        sources, audits = [], []
        for spec in SOURCES[village]:
            if spec["source_id"] in set(selected) | set(control):
                raise ValueError("paired source cannot be demolished")
            frozen, audit = freeze_source(spec, inventory)
            name = f"{village}-{spec['source_id']}-source.json"
            destination = OUT / name
            if destination.exists() and read_json(destination) != frozen:
                raise ValueError("refusing to overwrite a different frozen source")
            write_json(destination, frozen)
            sources.append(
                {"fire_id": spec["fire_id"], "protocol_path": name, "draw_id": spec["draw_id"]}
            )
            audits.append(audit)
        config = {
            "schema_version": "heritage-fixed-comparison-1",
            "place_name": parent["place_name"],
            "study_path": "../" + parent["study_path"],
            "source_fires": sources,
            "plans": [
                {"plan_id": "baseline", "demolished_ids": []},
                {"plan_id": "selected", "demolished_ids": selected},
                {"plan_id": "area-matched-position", "demolished_ids": control},
            ],
            "horizon_s": 3600.0,
            "checkpoints_s": [1200.0, 2400.0, 3600.0],
            "roof_scales_m": [1.0, 0.5],
            "quadrature_spacing_m": 0.5,
            "max_nodes": parent["max_nodes"],
            "max_radiation_edges": parent["max_radiation_edges"],
            "minimum_free_disk_GB": 5.0,
            "max_total_checks": 12,
            "max_wall_time_s": 14400.0,
            "core_overlap_policy": parent.get("core_overlap_policy", "reject"),
            "source_audit": audits,
            "position_selection_file": str(SELECTION),
            "position_selection_sha256": file_sha256(SELECTION),
            "scope": (
                "paired_duration_and_position_study;no_reoptimization;"
                "not_independent_risk_estimation"
            ),
            "layout_policy": "same_intervention_union_plus_cores_plus_fixed_source_in_every_arm",
            "historical_comparison": (
                "polygons_and_materials_preserved;common_refinement_changed;"
                "use_this_batch_1200s_checkpoint"
            ),
            "parent_design_config_sha256": file_sha256(parent_path),
            "no_adaptive_source_or_plan_reselection": True,
        }
        destination = OUT / f"{village}-fixed-comparison.json"
        if destination.exists() and read_json(destination) != config:
            raise ValueError("refusing to overwrite a different frozen comparison")
        write_json(destination, config)
        receipts.append(
            {
                "village": village,
                "config_path": str(destination.relative_to(ROOT)),
                "config_sha256": file_sha256(destination),
                "new_CA_planned": 12,
                "new_independent_fire_draws": 0,
                "source_audit": audits,
            }
        )
    write_json(ROOT / "docs/results/heritage-followup-inputs-20261103.json", receipts)
    for row in receipts:
        print(row["village"], row["config_path"], "12 planned CA; historical polygons verified")


if __name__ == "__main__":
    main()
