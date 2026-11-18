"""Freeze geometry-only, equal-count position controls for the two-village follow-up.

Reads the original full-search inventories without loading or running a fire model.
The controls match the existing plans' allocation to each nearest heritage core,
then minimize absolute nominal-area difference. They are matched position controls,
not independently optimized fire-protection plans or economic-cost estimates.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from math import comb, fsum, prod
from pathlib import Path

from pyfireca.planning.heritage import (
    HeritageTarget,
    content_hash,
    inventory_from_records,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/results/heritage-followup-position-selection-20261103.json"
NEAR_DISTANCE_M = 15.0
CASES = (
    {
        "village_id": "batuan",
        "place_name": "岜团村",
        "inventory_path": "runs/research/batuan-full-bounded-20261029/inventory.json",
        "expected_inventory_sha256": (
            "cdba9cb576b006d8bd363cebab5a5885b6ab2a8af6fa96acf0117266ddfe51d6"
        ),
        "existing_plan_ids": ("B0061", "B0071", "B0076"),
        "fixed_source_ids": ("B0062", "B0077"),
        "allocation_by_core": {"B0073": 2, "B0120": 1},
    },
    {
        "village_id": "pingyan",
        "place_name": "平岩村",
        "inventory_path": "runs/research/pingyan-full-bounded-20261029/inventory.json",
        "expected_inventory_sha256": (
            "288b06716cce53135b6fcc4c2288b80a54b07abfd077893794db892d99003747"
        ),
        "existing_plan_ids": ("B0119", "B0165", "B0167", "B0177", "B0190"),
        "fixed_source_ids": ("B0120", "B0164"),
        "allocation_by_core": {"B0035": 1, "B0036": 0, "B0166": 4},
    },
)


def frozen_inventory(case):
    path = ROOT / case["inventory_path"]
    raw = path.read_bytes()
    saved = json.loads(raw)
    if content_hash(saved) != case["expected_inventory_sha256"]:
        raise ValueError(f"frozen inventory changed: {case['village_id']}")
    inventory = inventory_from_records(
        saved["buildings"],
        {str(row["object_id"]): row["building_id"] for row in saved["buildings"]},
        targets=tuple(HeritageTarget(**row) for row in saved["targets"]),
        protected_ids=tuple(saved["protected_ids"]),
        fixed_source_ids=tuple(saved["fixed_source_ids"]),
        candidate_ids=tuple(saved["candidate_ids"]),
        candidate_evidence=saved["candidate_evidence"],
    )
    # GEOS may rotate polygon ring starting vertices during unary_union. Preserve
    # the original mapping fingerprint; the reconstructed geometry is used only
    # for distances and areas and is never written back to the source inventory.
    return inventory, hashlib.sha256(raw).hexdigest(), content_hash(saved)


def prepare_case(case):
    inventory, file_sha256, frozen_sha256 = frozen_inventory(case)
    buildings = inventory.by_id
    core_ids = sorted(target.building_id for target in inventory.targets)
    allocation = case["allocation_by_core"]
    existing = tuple(sorted(case["existing_plan_ids"]))
    fixed_sources = tuple(sorted(case["fixed_source_ids"]))
    original_candidates = set(inventory.candidate_ids)
    eligible = sorted(original_candidates - set(fixed_sources))
    if not set(fixed_sources) <= original_candidates:
        raise ValueError("comparison sources must be ordinary frozen candidates")
    if set(core_ids) != set(allocation) or not set(existing) <= set(eligible):
        raise ValueError("existing plan, allocation, or fixed sources are inconsistent")

    rows = {}
    for identifier, building in sorted(buildings.items()):
        distances = {
            core: float(building.geometry.distance(buildings[core].geometry)) for core in core_ids
        }
        nearest = min(core_ids, key=lambda core: (distances[core], core))
        rows[identifier] = {
            "building_id": identifier,
            "source_label": building.source_label,
            "is_core": identifier in core_ids,
            "is_original_candidate": identifier in original_candidates,
            "is_comparison_source": identifier in fixed_sources,
            "is_position_control_eligible": identifier in eligible,
            "nominal_area_m2": float(building.geometry.area),
            "nearest_core_id": nearest,
            "nearest_core_distance_m": distances[nearest],
            "core_distances_m": distances,
            "position_pool_core_id": nearest
            if identifier in eligible and distances[nearest] <= NEAR_DISTANCE_M
            else None,
        }
    pools = {
        core: [i for i in eligible if rows[i]["position_pool_core_id"] == core] for core in core_ids
    }
    observed_allocation = {
        core: sum(rows[i]["position_pool_core_id"] == core for i in existing) for core in core_ids
    }
    if observed_allocation != allocation:
        raise ValueError("declared allocation does not match the existing plan geometry")
    if case["village_id"] == "pingyan" and (pools["B0036"] or allocation["B0036"]):
        raise ValueError("the remote bridge must have an empty 15 m candidate pool")
    for core in core_ids:
        if len(pools[core]) < allocation[core]:
            raise ValueError(f"insufficient candidates in {core} pool")

    def area(ids):
        return fsum(rows[i]["nominal_area_m2"] for i in ids)

    reference_area = area(existing)

    def selection_key(ids):
        return abs(area(ids) - reference_area), ids

    combinations = (
        tuple(sorted(itertools.chain.from_iterable(parts)))
        for parts in itertools.product(
            *(itertools.combinations(pools[core], allocation[core]) for core in core_ids)
        )
    )
    best = None
    evaluated = 0
    within_one_percent = 0
    for ids in combinations:
        if ids == existing:
            continue
        evaluated += 1
        if abs(area(ids) - reference_area) <= 0.01 * reference_area:
            within_one_percent += 1
        if best is None or selection_key(ids) < selection_key(best):
            best = ids
    expected_count = prod(comb(len(pools[c]), allocation[c]) for c in core_ids) - 1
    if best is None or evaluated != expected_count:
        raise ValueError("geometry combination enumeration was incomplete")
    if set(best) & set(fixed_sources) or len(best) != len(existing):
        raise ValueError("selected control violates same-source or same-count constraints")

    def describe_plan(ids):
        return {
            "demolished_ids": list(ids),
            "demolition_count": len(ids),
            "nominal_area_m2": area(ids),
            "allocation_by_nearest_core": {
                core: sum(rows[i]["nearest_core_id"] == core for i in ids) for core in core_ids
            },
            "all_comparison_sources_retained": not bool(set(ids) & set(fixed_sources)),
            "economic_cost": None,
        }

    nearest = tuple(
        sorted(
            eligible,
            key=lambda i: (rows[i]["nearest_core_distance_m"], i),
        )[: len(existing)]
    )
    nearest_description = describe_plan(nearest)
    nearest_description.update(
        selection_rule="first_K_by_distance_to_nearest_core_then_building_ID",
        distance_order_ids=list(nearest),
        same_demolition_set_as_existing_plan=set(nearest) == set(existing),
        same_demolition_set_as_selected_control=set(nearest) == set(best),
        nominal_area_cap_applied=False,
        allocation_constraint_applied=False,
    )
    control = describe_plan(best)
    delta = area(best) - reference_area
    control.update(
        signed_area_difference_m2=delta,
        absolute_area_difference_m2=abs(delta),
        signed_area_difference_percent=100.0 * delta / reference_area,
        absolute_area_difference_percent=100.0 * abs(delta) / reference_area,
        matches_reference_area_within_one_percent=abs(delta) <= 0.01 * reference_area,
    )
    return {
        "village_id": case["village_id"],
        "place_name": case["place_name"],
        "spatial_crs": "EPSG:32649",
        "inventory_path": case["inventory_path"],
        "inventory_file_sha256": file_sha256,
        "inventory_sha256": frozen_sha256,
        "geometry_inventory_mapping_sha256": inventory.sha256,
        "candidate_evidence": inventory.candidate_evidence,
        "core_ids": core_ids,
        "original_candidate_ids": sorted(original_candidates),
        "original_candidate_count": len(original_candidates),
        "fixed_source_ids": list(fixed_sources),
        "source_exclusion_rule": "exclude_declared_sources_from_every_demolition_arm",
        "position_control_eligible_ids": eligible,
        "position_control_eligible_count": len(eligible),
        "near_distance_m": NEAR_DISTANCE_M,
        "candidate_pools_by_nearest_core": pools,
        "allocation_by_core": allocation,
        "existing_plan": describe_plan(existing),
        "selected_control": control,
        "nearest_distance_baseline": nearest_description,
        "geometry_combinations_evaluated_excluding_existing": evaluated,
        "geometry_combinations_within_one_percent_excluding_existing": within_one_percent,
        "shared_refinement_seed_ids": sorted(set(existing) | set(best)),
        "shared_refinement_with_sources_and_cores": sorted(
            set(existing) | set(best) | set(fixed_sources) | set(core_ids)
        ),
        "buildings": list(rows.values()),
        "fire_evaluations_read": 0,
        "new_fire_simulations": 0,
    }


def prepare():
    return {
        "schema_version": "heritage-followup-position-selection-1",
        "date": "2026-11-03",
        "generator_path": str(Path(__file__).resolve().relative_to(ROOT)),
        "generator_file_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rules": {
            "geometry": "union_of_frozen_building_polygons_in_metric_CRS",
            "distance": "minimum_polygon_boundary_or_interior_distance_not_centroid",
            "nearest_core_tie_break": "core_ID_lexicographic",
            "pool": "nearest_core_within_15m;ordinary_eligible;comparison_sources_excluded",
            "allocation": (
                "match_existing_plan_per_core;Batuan_1_bridge_2_drum;Pingyan_1_bridge_4_drum"
            ),
            "empty_core_pool": "Pingyan_B0036_has_no_eligible_roof_within_15m;allocate_zero",
            "selection": "exclude_existing_plan;minimize_absolute_nominal_area_difference",
            "selection_tie_break": "sorted_building_ID_tuple_lexicographic",
            "same_source": "all_arms_retain_declared_sources;reuse_exact_ignition_polygons",
            "existing_plan_role": "previously_frozen_candidate_not_newly_optimized_here",
            "interpretation": "matched_position_control_not_independent_planning_algorithm",
            "area": "nominal_polygon_area_sum;not_actual_cleared_fuel_or_economic_cost",
            "economic_cost": "unknown;no_currency_conversion_or_zero_imputation",
            "simulation": "none;no_results_used_in_control_generation",
        },
        "villages": [prepare_case(case) for case in CASES],
    }


if __name__ == "__main__":
    result = prepare()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "villages": [
                    {
                        "village_id": row["village_id"],
                        "selected_control": row["selected_control"],
                        "nearest_baseline_matches_existing": row["nearest_distance_baseline"][
                            "same_demolition_set_as_existing_plan"
                        ],
                    }
                    for row in result["villages"]
                ],
            },
            ensure_ascii=False,
        )
    )
