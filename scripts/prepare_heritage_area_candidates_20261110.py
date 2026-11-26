"""Generate geometry-only area proposals from the full two-village inventories."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from pyfireca.planning.area_candidates import build_cleared_area_function, generate_area_candidates
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def prepare(output):
    started = time.monotonic()
    rows = []
    for village, sources, expected in (
        ("batuan", ("B0077", "B0062"), 443.84196874011786),
        ("pingyan", ("B0164", "B0120"), 985.6078355425702),
    ):
        config_path = (
            ROOT
            / "examples/research/heritage/followup-20261103"
            / f"{village}-fixed-comparison.json"
        )
        config = read_json(config_path)
        study_path = (config_path.parent / config["study_path"]).resolve()
        bundle, _, inventory = load_study(study_path)
        layout = resolve_roof_layout(
            bundle.request,
            inventory,
            max_nodes=config["max_nodes"],
            core_overlap_policy=config.get("core_overlap_policy", "reject"),
        )
        clearing = build_cleared_area_function(
            layout.owners,
            [polygon.area for polygon in layout.geometry],
            area_basis="disjoint_original_roof_partition_geometry_m2;all_owners_removed",
        )
        seed = next(p["demolished_ids"] for p in config["plans"] if p["plan_id"] == "selected")
        result = generate_area_candidates(
            inventory,
            seed,
            clearing,
            fixed_source_ids=sources,
            max_generated=20_000,
            max_plans=12,
            max_bundle_size=3,
        )
        seed_area = clearing(seed)
        if abs(seed_area - expected) > 1e-4:
            raise ValueError(f"historical seed clearing area did not reproduce: {village}")
        rows.append(
            {
                "village_id": village,
                "fixed_comparison_path": str(config_path.relative_to(ROOT)),
                "fixed_comparison_sha256": file_sha256(config_path),
                "study_path": str(study_path.relative_to(ROOT)),
                "study_sha256": file_sha256(study_path),
                "inventory_sha256": inventory.sha256,
                "inventory_object_count": len(inventory.buildings),
                "physical_partition_node_count": len(layout.owners),
                "core_overlap_policy": config.get("core_overlap_policy", "reject"),
                "fixed_source_ids": list(sources),
                "seed_actual_cleared_area_m2": seed_area,
                "historical_seed_actual_cleared_area_m2": expected,
                "seed_area_difference_m2": seed_area - expected,
                "parent_geometry_and_materials_unchanged": True,
                **result,
            }
        )
        print(
            {
                "village_id": village,
                "seed_area_m2": seed_area,
                "plans": len(result["plans"]),
                "elapsed_s": time.monotonic() - started,
            },
            flush=True,
        )
        del layout, bundle
    report = {
        "schema_version": "heritage-full-inventory-area-proposals-1",
        "date": "2026-11-10",
        "new_CA_checks": 0,
        "scope": "geometry_only_budgeted_local_proposals;no_fire_feasibility_or_optimality_claim",
        "basis": "same original 20261103 study inventories;pre-ignition disjoint roof atoms",
        "generator_sha256": file_sha256(Path(__file__)),
        "elapsed_s": time.monotonic() - started,
        "villages": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/results/heritage-area-candidates-20261110.json",
    )
    prepare(parser.parse_args().output.resolve())


if __name__ == "__main__":
    main()
