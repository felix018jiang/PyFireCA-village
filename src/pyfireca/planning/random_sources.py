"""Frozen stratified building fires with common random priorities across plans."""

from __future__ import annotations

import hashlib

import numpy as np
from shapely.geometry import Point

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import fixed_area_ignition, unambiguous_roof


def _rng(seed, *keys):
    digest = hashlib.sha256(f"{seed}:{':'.join(map(str, keys))}".encode()).digest()
    return np.random.Generator(np.random.PCG64(int.from_bytes(digest[:16], "big")))


def _uniform_anchor(geometry, rng):
    xmin, ymin, xmax, ymax = geometry.bounds
    for _ in range(100_000):
        x, y = rng.uniform((xmin, ymin), (xmax, ymax))
        if geometry.contains(Point(x, y)):
            return [float(x), float(y)]
    raise ValueError("uniform roof-point sampling exhausted its rejection limit")


def freeze_source_protocol(inventory, *, seed, draws_per_stratum, area_m2, near_distance_m):
    """Uniform buildings within nearest-core strata; no annual-rate interpretation.

    Each draw has an independent full permutation. Removing buildings means
    selecting the first survivor, which is uniform on the retained stratum.
    Anchors depend on draw and building identity, never on plan evaluation order.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("source seed must be a nonnegative integer")
    if (
        isinstance(draws_per_stratum, bool)
        or not isinstance(draws_per_stratum, int)
        or draws_per_stratum < 1
    ):
        raise ValueError("positive integer draws per stratum required")
    if not np.isfinite(area_m2) or area_m2 <= 0:
        raise ValueError("positive ignition area required")
    if not np.isfinite(near_distance_m) or near_distance_m <= 0 or not inventory.targets:
        raise ValueError("positive near distance and heritage targets required")
    cores = sorted(inventory.targets, key=lambda t: t.building_id)
    strata = {f"near-{t.building_id}": [] for t in cores}
    strata["background"] = []
    members, excluded = {}, []
    for obj in sorted(inventory.buildings, key=lambda b: b.building_id):
        if obj.building_id in {t.building_id for t in cores} or obj.source_label != "building":
            continue
        try:
            roof = unambiguous_roof(inventory, obj.building_id)
        except ValueError:
            excluded.append({"building_id": obj.building_id, "reason": "no_unambiguous_roof"})
            continue
        if roof.area < area_m2:
            excluded.append(
                {
                    "building_id": obj.building_id,
                    "reason": "unambiguous_roof_smaller_than_prescribed_fire",
                    "unambiguous_area_m2": float(roof.area),
                }
            )
            continue
        nearest = min(
            cores,
            key=lambda t: (
                obj.geometry.distance(inventory.by_id[t.building_id].geometry),
                t.building_id,
            ),
        )
        distance = obj.geometry.distance(inventory.by_id[nearest.building_id].geometry)
        stratum = f"near-{nearest.building_id}" if distance <= near_distance_m else "background"
        strata[stratum].append(obj.building_id)
        members[obj.building_id] = {
            "stratum": stratum,
            "unambiguous_area_m2": float(roof.area),
            "nearest_core_id": nearest.building_id,
            "distance_to_nearest_core_m": float(distance),
        }
    if not members:
        raise ValueError("no buildings can hold the prescribed fire area")
    draws = []
    for stratum, ids in sorted(strata.items()):
        if not ids:
            continue
        for index in range(draws_per_stratum):
            draw_id = f"{stratum}-{index + 1:03d}"
            priority = _rng(seed, draw_id, "priority").permutation(ids).tolist()
            draws.append(
                {
                    "draw_id": draw_id,
                    "stratum": stratum,
                    "priority_ids": priority,
                    "baseline_source_id": priority[0],
                }
            )
    protocol = {
        "schema_version": "heritage-random-sources-1",
        "inventory_sha256": inventory.sha256,
        "seed": seed,
        "rng": "numpy-PCG64;SHA256-keyed-per-draw-and-building",
        "draws_per_stratum": draws_per_stratum,
        "sampling": "independent_with_replacement_uniform_buildings_within_stratum",
        "source_condition": "one_fire_in_retained_eligible_ordinary_buildings",
        "ignition_area_m2": area_m2,
        "near_distance_m": near_distance_m,
        "strata": strata,
        "members": members,
        "excluded_sources": excluded,
        "anchor_policy": "uniform_in_frozen_unambiguous_roof;disk_intersection_exact_area",
        "demolished_source_policy": "first_retained_in_frozen_priority;never_no_fire_success",
        "weight_policy": "retained_stratum_count/retained_total/draws_per_stratum",
        "draws": draws,
    }
    protocol["protocol_sha256"] = content_hash(protocol)
    return protocol


def realize_sources(protocol, inventory, *, demolished_ids=()):
    """Resolve a frozen protocol for a plan; preserve every declared draw."""
    if protocol["inventory_sha256"] != inventory.sha256:
        raise ValueError("random source protocol inventory mismatch")
    unhashed = {k: v for k, v in protocol.items() if k != "protocol_sha256"}
    if content_hash(unhashed) != protocol["protocol_sha256"]:
        raise ValueError("random source protocol fingerprint mismatch")
    removed = set(demolished_ids)
    if not removed <= set(inventory.candidate_ids):
        raise ValueError("removed source IDs must be eligible demolition candidates")
    counts = {key: len(set(ids) - removed) for key, ids in protocol["strata"].items()}
    total = sum(counts.values())
    if total == 0:
        raise ValueError("one-fire condition requires a retained eligible source")
    results = []
    for draw in protocol["draws"]:
        stratum = draw["stratum"]
        if counts[stratum] == 0:
            # Empty strata carry zero probability, never a successful fire run.
            results.append(
                {
                    **draw,
                    "source_id": None,
                    "weight": 0.0,
                    "status": "empty_stratum",
                    "footprint": None,
                }
            )
            continue
        source_id = next(i for i in draw["priority_ids"] if i not in removed)
        roof = unambiguous_roof(inventory, source_id)
        anchor = _uniform_anchor(roof, _rng(protocol["seed"], draw["draw_id"], source_id, "anchor"))
        footprint = fixed_area_ignition(inventory, source_id, anchor, protocol["ignition_area_m2"])
        results.append(
            {
                "draw_id": draw["draw_id"],
                "stratum": stratum,
                "source_id": source_id,
                "baseline_source_id": draw["baseline_source_id"],
                "source_replaced": source_id != draw["baseline_source_id"],
                "anchor_m": anchor,
                "weight": counts[stratum] / total / protocol["draws_per_stratum"],
                "status": "fire",
                "footprint": footprint,
            }
        )
    return results


def summarize_protection(protocol, realizations, runs, *, plan_id, scales_m, core_ids):
    """Union failures over scales per fire; missing/invalid runs remain unknown."""
    selected = [r for r in runs if r["plan_id"] == plan_id]
    by_key = {(r["draw_id"], r["scale_m"]): r for r in selected}
    if len(by_key) != len(selected):
        raise ValueError("duplicate plan/draw/scale evaluations")
    draws = []
    for realization in realizations:
        if realization["status"] == "empty_stratum":
            draws.append(
                {
                    "draw_id": realization["draw_id"],
                    "weight": 0.0,
                    "status": "empty_stratum",
                    "core_ignited": {},
                }
            )
            continue
        rows = [by_key.get((realization["draw_id"], scale)) for scale in scales_m]
        complete = all(
            r is not None and r["complete"] and r["protection_status"] in {"passed", "failed"}
            for r in rows
        )
        core_ignited = {
            core: any(
                r is not None
                and any(o["building_id"] == core and o["ignited"] for o in r["core_outcomes"])
                for r in rows
            )
            for core in core_ids
        }
        failed = any(core_ignited.values())
        draws.append(
            {
                "draw_id": realization["draw_id"],
                "stratum": realization["stratum"],
                "source_id": realization["source_id"],
                "source_replaced": realization["source_replaced"],
                "weight": realization["weight"],
                "core_ignited": core_ignited,
                "status": "failed" if failed else "passed" if complete else "unknown",
                "all_scales_complete": complete,
            }
        )
    known = all(r["status"] != "unknown" and r.get("all_scales_complete", True) for r in draws)
    return {
        "plan_id": plan_id,
        "protocol_sha256": protocol["protocol_sha256"],
        "all_evaluations_complete": known,
        "draws": draws,
        "weighted_joint_failure_fraction": (
            sum(r["weight"] for r in draws if r["status"] == "failed") if known else None
        ),
        "weighted_core_failure_fractions": {
            core: sum(r["weight"] for r in draws if r["core_ignited"].get(core)) if known else None
            for core in core_ids
        },
        "interpretation": "pilot_conditional_scenario_fraction_not_annual_risk_or_safety_proof",
    }
