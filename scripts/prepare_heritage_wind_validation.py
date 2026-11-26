"""Freeze the held-out ignition roofs for the paired terminal wind validation."""

import argparse
import os
from pathlib import Path

from prepare_heritage_wind_strategies import FIRE_AREA_M2, NEAR_M, ROOT, fire_row
from shapely import union_all

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import _rng, _uniform_anchor
from pyfireca.planning.roof_ignition import fixed_area_ignition, unambiguous_roof
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ORIGINAL = ROOT / "output/heritage-wind-20261112"
DEFAULT_OUTPUT = ROOT / "output/heritage-wind-validation-20261112"
ORIGINAL_SHA256 = "6c6478782ede5faced9de991edec0bfb8c88bc7f91a1497ea5bfe221b9b3e240"
SEED = 20261113
FAR_DRAWS = 30
REMAINING_ROOFS = {"pingyan": (23, 893), "batuan": (20, 282)}


def prepare_village(village, original, physical, source, output):
    previous = {f["source_id"] for f in original["fires"]}
    if len(previous) != 8:
        raise ValueError(f"{village}: expected eight distinct previous ignition roofs")
    input_files = dict(original["input_files_sha256"])
    for entry in physical["files"]:
        expected = input_files.setdefault(entry["path"], entry["actual_sha256"])
        if expected != entry["actual_sha256"]:
            raise ValueError(f"Contradictory input seals: {entry['path']}")
    for name, sha in input_files.items():
        if file_sha256(ROOT / name) != sha:
            raise ValueError(f"Changed study or material input: {name}")
    _, _, inventory = load_study(original["study_path"])
    if inventory.sha256 != original["inventory_sha256"]:
        raise ValueError(f"{village}: inventory changed")
    heritage = union_all([inventory.by_id[k].geometry for k in original["core_ids"]])
    excluded = previous | set(original["core_ids"])
    for plan in original["plans"]:
        excluded.update(plan["demolished_ids"])

    fires, remaining = [], {}
    for stratum, original_key, expected_size in zip(
        ("near", "far"), ("near", "background"), REMAINING_ROOFS[village], strict=True
    ):
        candidates = original["random_eligible_ids_by_stratum"][original_key]
        eligible = sorted(set(candidates) - previous)
        if (
            len(candidates) != len(set(candidates))
            or len(eligible) != expected_size
            or excluded.intersection(eligible)
        ):
            raise ValueError(f"{village}/{stratum}: inconsistent frozen candidate frame")
        remaining[stratum] = eligible
        if stratum == "near":
            selected = eligible
        else:
            rng = _rng(SEED, village, original_key, "permutation")
            selected = rng.permutation(eligible)[:FAR_DRAWS].tolist()
        for index, roof_id in enumerate(selected, 1):
            roof = unambiguous_roof(inventory, roof_id)
            distance = inventory.by_id[roof_id].geometry.distance(heritage)
            if (distance <= NEAR_M) != (stratum == "near") or roof.area < FIRE_AREA_M2:
                raise ValueError(f"{village}: selected roof violates registered eligibility")
            anchor = _uniform_anchor(roof, _rng(SEED, village, original_key, roof_id, "anchor"))
            footprint = fixed_area_ignition(inventory, roof_id, anchor, FIRE_AREA_M2)
            if footprint.geometry.difference(roof).area > 1e-7:
                raise ValueError(f"{village}: ignition footprint extends beyond its source")
            fires.append(
                fire_row(
                    f"{stratum}-{index:03d}",
                    "random",
                    anchor,
                    footprint,
                    stratum=stratum,
                    stratum_population=len(eligible),
                )
            )
    if len({f["source_id"] for f in fires}) != len(fires):
        raise ValueError(f"{village}: validation roofs overlap strata")

    geography = source / original["geojson_path"]
    if file_sha256(geography) != original["geojson_sha256"]:
        raise ValueError(f"{village}: original layout geometry changed")
    return {
        **original,
        "geojson_path": os.path.relpath(geography, output),
        "fires": fires,
        "excluded_previous_source_ids": sorted(previous),
        "random_eligible_ids_by_stratum": remaining,
        "input_files_sha256": input_files,
        "layout_catalog_sha256": content_hash(original["plans"]),
    }


def register(source, output):
    parent = source / "registration.json"
    if file_sha256(parent) != ORIGINAL_SHA256:
        raise ValueError("Expected the originally frozen five-layout, nine-wind registration")
    old = read_json(parent)
    verification_path = source / "physics-verification.json"
    verification = read_json(verification_path)
    if verification["wind_registration_file_sha256"] != ORIGINAL_SHA256 or verification[
        "verification_sha256"
    ] != content_hash({k: v for k, v in verification.items() if k != "verification_sha256"}):
        raise ValueError("Physical input verification seal does not match the frozen study")
    villages = {
        v: prepare_village(v, s, verification["villages"][v], source, output)
        for v, s in old["villages"].items()
    }
    count = sum(len(s["fires"]) * len(s["plans"]) * len(old["winds"]) for s in villages.values())
    if count != 4635:
        raise ValueError(f"Expected 4,635 held-out trials, got {count}")
    data = {
        "schema_version": "heritage-terminal-wind-independent-validation-1",
        "scope": (
            "new validation roofs only; observe at 3600 s and continue to certified extinction"
        ),
        "original_registration_sha256": ORIGINAL_SHA256,
        "original_registration_content_sha256": old["registration_sha256"],
        "physics_verification_file_sha256": file_sha256(verification_path),
        "original_solver_sha256": verification["solver_sha256"],
        "winds": old["winds"],
        "wind_scope": old["wind_scope"],
        "primary": {**old["primary"], "planned_pairs": count},
        "terminal_endpoint": "certified extinction in the original study domain",
        "geometry_rule": old["geometry_rule"],
        "construction_rule": old["construction_rule"],
        "random_design": {
            "seed": SEED,
            "source_area_m2": FIRE_AREA_M2,
            "near_distance_m": NEAR_M,
            "sampling": "Exclude the eight previous roofs in each village. "
            "Census of remaining near roofs, "
            "simple random sample of 30 remaining far roofs without replacement. PCG64 keyed "
            "by SHA-256 on seed/village/original stratum/permutation. One uniform anchor within "
            "each unambiguous roof keyed by seed/village/original stratum/roof ID/anchor.",
            "original_stratum_keys": {"near": "near", "far": "background"},
            "near_rule": "minimum ordinary-to-heritage outline distance <= 50 m",
            "far_rule": "minimum ordinary-to-heritage outline distance > 50 m",
            "far_draws_per_village": FAR_DRAWS,
        },
        "villages": villages,
    }
    data["registration_sha256"] = content_hash(data)
    output.mkdir(parents=True, exist_ok=True)
    dest = output / "registration.json"
    staged = dest.with_name(f".registration-{os.getpid()}.tmp")
    try:
        write_json(staged, data)
        # Link without replacing: concurrent or repeated registration must match byte-for-byte.
        try:
            os.link(staged, dest)
        except FileExistsError:
            if staged.read_bytes() != dest.read_bytes():
                raise ValueError("Existing frozen validation registration differs") from None
    finally:
        staged.unlink(missing_ok=True)
    print(f"{count} cases; registration file SHA-256 {file_sha256(dest)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ORIGINAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    register(args.source.resolve(), args.output.resolve())
