"""Declare full-action retained-source area searches before new CA runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def prepare(village):
    directory = ROOT / "examples/research/heritage"
    parent_path = directory / f"{village}-full-bounded-20261029.json"
    parent = read_json(parent_path)
    fixed_config = read_json(directory / f"area-20261110/{village}-area-master.json")
    study_path = (directory / parent["study_path"]).resolve()
    bundle, _, inventory = load_study(study_path)
    policy = parent.get("core_overlap_policy", "reject")
    base = resolve_roof_layout(
        bundle.request, inventory, max_nodes=parent["max_nodes"], core_overlap_policy=policy
    )
    area = build_cleared_area_function(
        base.owners, [g.area for g in base.geometry], area_basis="physical_roof_geometry_m2"
    )
    cap = area(parent["initial_ids"])
    search_path = ROOT / f"runs/research/heritage-area-master-{village}-20261110/search.json"
    original_bytes = search_path.read_bytes()
    search = json.loads(original_bytes)
    seed_rows = [p for p in search["verified_feasible_plans"] if p["cleared_area_m2"] <= cap + 1e-6]
    candidates = {
        tuple(sorted(parent["initial_ids"])),
        *(tuple(p["demolished_ids"]) for p in seed_rows),
    }
    seeds = sorted(candidates, key=lambda ids: (area(ids), ids))
    config = {
        "schema_version": "heritage-area-master-config-1",
        "village_id": village,
        "place_name": parent["place_name"],
        "study_path": str(study_path),
        "source_policy": "area_bounded_retained_sources",
        "fixed_source_ids": [],
        "source_fires": [
            dict(fire, protocol_path=str((directory / fire["protocol_path"]).resolve()))
            for fire in parent["fires"]
        ],
        "source_area_limit_m2": cap,
        "source_area_tolerance_m2": 1e-6,
        "refinement_ids": fixed_config["refinement_ids"],
        "seed_plans": [list(ids) for ids in seeds],
        "prefetch_baseline_witnesses": True,
        "enable_physical_group_cuts": True,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "horizon_s": 3600.0,
        "max_dt_s": 1.0,
        "max_new_checks": 64,
        "max_wall_time_s": 14400.0,
        "max_mip_time_s": 1200.0,
        "mip_call_time_limit_s": 60.0,
        "area_gap_absolute_tolerance_m2": 1e-6,
        "area_gap_relative_tolerance": 1e-8,
        "max_nodes": parent["max_nodes"],
        "max_radiation_edges": 60000000,
        "minimum_free_disk_GB": 10.0,
        "core_overlap_policy": policy,
        "new_independent_fire_draws": 0,
        "initial_cap_is_verified_upper_bound_for_new_problem": False,
        "cap_selection_rule": "geometric_actual_clearance_of_historical_initial_plan",
        "prior_fixed_problem_seed_receipt": {
            "problem_sha256": search["problem_sha256"],
            "source_search_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "verified_feasible_rows": seed_rows,
            "validity": "proposal_only;all_seeds_must_be_reverified_in_new_source_mesh",
        },
        "parent_full_config_sha256": file_sha256(parent_path),
        "generator_sha256": file_sha256(Path(__file__)),
        "scope": (
            "all_inventory_ordinary_actions;four_historical_global_retained_source_priorities;"
            "60min_dual_model_support;area_cap_domain_not_assumed_feasible;"
            "no_new_independent_draws;not_joint_global_loss_or_field_safety"
        ),
    }
    destination = directory / f"area-20261110/{village}-retained-area-master.json"
    if destination.exists() and read_json(destination) != config:
        raise ValueError("refusing to replace an already frozen retained study")
    write_json(destination, config)
    print(
        {
            "village": village,
            "full_actions": len(inventory.candidate_ids),
            "area_domain_cap_m2": cap,
            "source_families": len(parent["fires"]),
            "seed_count": len(seeds),
            "new_CA_checks": 0,
        }
    )


def main():
    for village in ("batuan", "pingyan"):
        prepare(village)


if __name__ == "__main__":
    main()
