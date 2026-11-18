"""Audit zero-to-2.5m hull-band action events; never simulate or alter inputs.

Exact rational predicates concern the stored binary-coordinate polygons.
Numerical GEOS buffer transition brackets are explicitly separate evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal, localcontext
from fractions import Fraction as F
from itertools import pairwise
from pathlib import Path

import numpy as np
import shapely
from prepare_heritage_all_roof_bands import parent_cell_polygons
from prepare_heritage_balanced_roof_bands import finite_retained_sources
from shapely.geometry import box

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
TAU = 1e-8


def rational_ring(ring):
    return [(F(float(x)), F(float(y))) for x, y in list(ring.coords)[:-1]]


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def edges(vertices):
    return zip(vertices, vertices[1:] + vertices[:1], strict=True)


def signed_area(vertices):
    return (
        sum((a[0] * b[1] - b[0] * a[1] for a, b in edges(vertices)), F(0)) / 2 if vertices else F(0)
    )


def clipped(vertices, hull):
    for a, b in edges(hull):
        if not vertices:
            break
        result = []
        previous = vertices[-1]
        old = cross(a, b, previous)
        for current in vertices:
            new = cross(a, b, current)
            if (old >= 0) != (new >= 0):
                t = old / (old - new)
                result.append(
                    (
                        previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]),
                    )
                )
            if new >= 0:
                result.append(current)
            previous, old = current, new
        vertices = result
    return vertices


def outside_area_exact(polygon, hull):
    rings = [rational_ring(polygon.exterior), *[rational_ring(r) for r in polygon.interiors]]
    full = abs(signed_area(rings[0])) - sum((abs(signed_area(r)) for r in rings[1:]), F(0))
    inside = abs(signed_area(clipped(rings[0], hull))) - sum(
        (abs(signed_area(clipped(r, hull))) for r in rings[1:]), F(0)
    )
    return full - inside


def point_segment_squared(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    norm = dx * dx + dy * dy
    t = (
        F(0)
        if norm == 0
        else max(F(0), min(F(1), ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / norm))
    )
    return (p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2


def segments_touch(a, b, c, d):
    x1, x2, x3, x4 = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return (
        x1 * x2 <= 0
        and x3 * x4 <= 0
        and all(
            max(min(a[k], b[k]), min(c[k], d[k])) <= min(max(a[k], b[k]), max(c[k], d[k]))
            for k in (0, 1)
        )
    )


def boundary_distance_squared_exact(polygon, hull):
    best = None
    for ring in (polygon.exterior, *polygon.interiors):
        for a, b in edges(rational_ring(ring)):
            for c, d in edges(hull):
                if segments_touch(a, b, c, d):
                    return F(0)
                value = min(
                    point_segment_squared(a, c, d),
                    point_segment_squared(b, c, d),
                    point_segment_squared(c, a, b),
                    point_segment_squared(d, a, b),
                )
                best = value if best is None else min(best, value)
    return best


def exact_distance_record(squared):
    if squared == 0:
        interval = ["0", "0"]
    else:
        with localcontext() as context:
            context.prec = 80
            value = (Decimal(squared.numerator) / Decimal(squared.denominator)).sqrt()
            low = value.quantize(Decimal("1e-18"), rounding=ROUND_FLOOR)
            high = low + Decimal("1e-18")
        assert F(str(low)) ** 2 <= squared <= F(str(high)) ** 2
        interval = [str(low), str(high)]
    return {
        "squared_distance_numerator": str(squared.numerator),
        "squared_distance_denominator": str(squared.denominator),
        "distance_decimal_enclosure_m": interval,
        "exact_for_stored_binary_coordinate_segments": True,
    }


def numerical_bracket(predicate, high, *, tolerance=1e-12):
    if not predicate(high):
        return None
    low = 0.0
    assert not predicate(low)
    while high - low > tolerance:
        middle = (low + high) / 2
        if predicate(middle):
            high = middle
        else:
            low = middle
    assert not predicate(low) and predicate(high)
    return {
        "false_width_m": low,
        "true_width_m": high,
        "bracket_width_m": high - low,
        "floating_endpoint_witness_only_not_a_real_arithmetic_proof": True,
    }


def prepare(thin_catalog_path, output):
    started = time.monotonic()
    thin = read_json(thin_catalog_path)
    config_path = (
        ROOT
        / "examples/research/heritage/area-20261110"
        / "batuan-thin-hull-longcap-comparison-20261111.json"
    )
    config = read_json(config_path)
    reference_path = Path(config["reference_master_experiment_path"])
    reference = read_json(reference_path)
    initial = {
        str(p): file_sha256(p)
        for p in [
            thin_catalog_path,
            config_path,
            reference_path,
            ROOT / "scripts/prepare_heritage_thin_hull_catalog.py",
            ROOT / "scripts/prepare_heritage_narrow_hull_catalog.py",
            ROOT / "scripts/prepare_heritage_balanced_roof_bands.py",
            ROOT / "scripts/prepare_heritage_all_roof_bands.py",
        ]
    }
    assert file_sha256(reference_path) == config["reference_master_experiment_sha256"]
    for p, digest in reference["references_sha256"].items():
        assert file_sha256(p) == digest
    bundle, _, inventory = load_study(config["study_path"])
    parent = bundle.request
    base = resolve_roof_layout(
        parent,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    seed = ("B0061", "B0071", "B0076")
    actual = finite_retained_sources(reference["problem"]["fires"], seed)
    protected = {t.building_id for t in inventory.targets} | set(actual.values())
    hull = shapely.union_all([inventory.by_id[i].geometry for i in sorted(protected)]).convex_hull
    inner = hull.buffer(0, quad_segs=64)
    vertices = rational_ring(inner.exterior)
    assert inner.bounds[2] - inner.bounds[0] > 5 and inner.bounds[3] - inner.bounds[1] > 5
    if signed_area(vertices) < 0:
        vertices.reverse()
    cell_shapes = parent_cell_polygons(parent.grid)
    eligible = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    roof_cells = {int(c) for c, o in zip(base.cell_id, base.owners, strict=True) if o}
    eligible[list(roof_cells)] = False
    # Conservative axis-aligned candidate window, then exact rational tests.
    # No floating distance predicate can silently exclude a near-boundary item.
    xmin, ymin, xmax, ymax = inner.bounds
    window = box(xmin - 2.50000001, ymin - 2.50000001, xmax + 2.50000001, ymax + 2.50000001)
    possible_g = np.flatnonzero(eligible & shapely.intersects(cell_shapes, window))
    original_shapes = np.asarray(base.geometry, dtype=object)
    roof_nodes = np.flatnonzero([bool(o) for o in base.owners])
    possible_roof = roof_nodes[shapely.intersects(original_shapes[roof_nodes], window)]
    possible_roof = np.array(
        [int(n) for n in possible_roof if set(base.owners[n]) - protected - set(seed)], int
    )
    rings = {0.0: inner.difference(inner)}

    def band(width):
        width = float(width)
        if width not in rings:
            rings[width] = inner.buffer(width, quad_segs=64).difference(inner)
        return rings[width]

    g_records = []
    g_limit = []
    outside_g_limit = []
    inside_tangent = []
    for cell in possible_g:
        geometry = cell_shapes[cell]
        outside = outside_area_exact(geometry, vertices)
        distance = boundary_distance_squared_exact(geometry, vertices)
        assert outside >= 0
        if outside <= 0 and distance > 0:
            continue
        if distance > F(25, 4):
            continue
        hit_max = bool(geometry.intersects(band(2.5)))
        if distance == 0:
            g_limit.append(int(cell))
            if outside > 0:
                outside_g_limit.append(int(cell))
            else:
                inside_tangent.append(int(cell))
        record = {
            "physical_cell_id": int(cell),
            "construction_footprint_m2": 25,
            "covered_fuel_m2": float(parent.parameters.covered_area_m2.ravel()[cell]),
            "has_positive_area_outside_hull_exact": outside > 0,
            "outside_area_m2_exact_fraction": str(outside),
            "outside_area_m2_float": float(outside),
            "inside_only_boundary_contact": outside == 0 and distance == 0,
            "closed_ring_positive_width_limit_member": distance == 0,
            "euclidean_boundary_distance": exact_distance_record(distance),
            "selected_at2_5": hit_max,
        }
        if distance > 0 and hit_max:
            record["buffer64_transition_bracket_m"] = numerical_bracket(
                lambda w, geometry=geometry: geometry.intersects(band(w)), 2.5, tolerance=1e-10
            )
            bracket = record["buffer64_transition_bracket_m"]
            low_d, high_d = map(
                float, record["euclidean_boundary_distance"]["distance_decimal_enclosure_m"]
            )
            record["buffer_transition_minus_exact_euclidean_distance_interval_m"] = [
                bracket["false_width_m"] - high_d,
                bracket["true_width_m"] - low_d,
            ]
        g_records.append(record)
    outside_by_node = {}
    owner_nodes = defaultdict(list)
    support_by_owner = defaultdict(list)
    roof_support_records = []
    for node in possible_roof:
        node = int(node)
        geometry = base.geometry[node]
        outside = outside_area_exact(geometry, vertices)
        assert outside >= 0
        if outside <= 0:
            continue
        distance = boundary_distance_squared_exact(geometry, vertices)
        if distance > F(25, 4):
            continue
        outside_by_node[node] = outside
        labels = sorted(set(base.owners[node]) - protected - set(seed))
        roof_support_records.append(
            {
                "baseline_node_id": node,
                "original_owner_ids": list(base.owners[node]),
                "outside_area_exact_fraction_m2": str(outside),
                "outside_area_m2_float": float(outside),
                "can_ever_exceed_fixed_area_threshold": outside > F(str(TAU)),
                "zero_area_threshold_support_onset": exact_distance_record(distance),
            }
        )
        for identifier in labels:
            owner_nodes[identifier].append(node)
            support_by_owner[identifier].append((distance, node))

    def requested_owners(width):
        ring = band(width)
        result = set()
        for node in possible_roof:
            if base.geometry[int(node)].intersection(ring).area > TAU:
                result.update(base.owners[int(node)])
        return set(seed) | (result - protected)

    def grass(width):
        return {int(i) for i in possible_g if cell_shapes[int(i)].intersects(band(width))}

    def action(width):
        removed = requested_owners(width)
        ground = grass(width)
        clear = np.array([bool(o) and set(o) <= removed for o in base.owners])
        roof_C = math.fsum(g.area for g, yes in zip(base.geometry, clear, strict=True) if yes)
        return {
            "width_m": width,
            "demolished_ids": sorted(removed),
            "cleared_physical_cell_ids": sorted(ground),
            "cleared_roof_node_count": int(clear.sum()),
            "roof_C_geometry_m2": roof_C,
            "grass_footprint_C_m2": 25 * len(ground),
            "C_m2": roof_C + 25 * len(ground),
            "roof_covered_removed_m2": float(
                base.node_inputs.parameters.covered_area_m2.ravel()[clear].sum()
            ),
            "grass_covered_removed_m2": float(
                parent.parameters.covered_area_m2.ravel()[sorted(ground)].sum()
            ),
            "ring_area_m2": float(band(width).area),
            "ring_is_empty": band(width).is_empty,
            "action_sha256": content_hash([sorted(removed), sorted(ground)]),
            "actual_cleared_base_node_mask_sha256": hashlib.sha256(
                (clear | np.isin(base.cell_id, sorted(ground))).tobytes()
            ).hexdigest(),
        }

    owner_records = []
    for identifier in sorted(owner_nodes):
        support, node = min(support_by_owner[identifier])
        indices = owner_nodes[identifier]

        def hit(width, indices=indices):
            return any(base.geometry[n].intersection(band(width)).area > TAU for n in indices)

        bracket = numerical_bracket(hit, 2.5)
        owner_records.append(
            {
                "building_id": identifier,
                "support_witness_node": node,
                "geometric_zero_threshold_onset": exact_distance_record(support),
                "buffer64_fixed_area_threshold_transition": bracket,
                "fixed_area_threshold_m2": TAU,
                "selected_by_w2_5": hit(2.5),
                "candidate_physical_patch_count": len(indices),
            }
        )
    zero_threshold_D = set(seed) | {
        identifier
        for identifier, items in support_by_owner.items()
        if min(d for d, n in items) == 0
    }
    small_widths = [
        0.0,
        1e-12,
        1e-11,
        1e-10,
        2e-10,
        5e-10,
        1e-9,
        2e-9,
        5e-9,
        1e-8,
        1e-7,
        1e-6,
        1e-5,
        1e-4,
        0.001,
        0.005,
        0.01,
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
    ]
    probes = [action(w) for w in small_widths]
    baseline = next(p for p in probes if p["width_m"] == 0.01)
    raw = thin.get("all_five_parameter_rows", thin["plans"])
    for old in raw:
        new = action(old["nominal_ring_width_m"])
        assert (
            new["demolished_ids"] == old["demolished_ids"]
            and new["cleared_physical_cell_ids"] == old["cleared_physical_cell_ids"]
        )
        assert abs(new["C_m2"] - old["C_construction_action_area_m2"]) < 1e-6
    geometric_zero_clear = np.array([bool(o) and set(o) <= zero_threshold_D for o in base.owners])
    zero_roof_C = math.fsum(
        g.area for g, yes in zip(base.geometry, geometric_zero_clear, strict=True) if yes
    )
    seed_clear = np.array([bool(o) and set(o) <= set(seed) for o in base.owners])
    seed_C = math.fsum(g.area for g, yes in zip(base.geometry, seed_clear, strict=True) if yes)
    lower_g_boundary = float(
        min(
            (
                Decimal(r["euclidean_boundary_distance"]["distance_decimal_enclosure_m"][0])
                for r in g_records
                if not r["closed_ring_positive_width_limit_member"] and r["selected_at2_5"]
            ),
            default=Decimal("Infinity"),
        )
    )
    monotonic_failures = []
    for a, b in pairwise(probes):
        if not set(a["demolished_ids"]) <= set(b["demolished_ids"]) or not set(
            a["cleared_physical_cell_ids"]
        ) <= set(b["cleared_physical_cell_ids"]):
            monotonic_failures.append([a["width_m"], b["width_m"]])
    # General finite-threshold continuum bound, using a deliberately loose
    # positive-width value far below the stored-coordinate resolution.
    continuum_delta = TAU / (2 * (hull.length + math.pi + 1))
    assert hull.length * continuum_delta + math.pi * continuum_delta**2 < TAU
    owner_cut_events = [r for r in owner_records if r["buffer64_fixed_area_threshold_transition"]]
    grass_events = [r for r in g_records if "buffer64_transition_bracket_m" in r]
    grass_events.sort(key=lambda r: r["buffer64_transition_bracket_m"]["true_width_m"])
    plateaus = []
    for k in range(len(grass_events) + 1):
        lower = (
            1e-6 if k == 0 else grass_events[k - 1]["buffer64_transition_bracket_m"]["true_width_m"]
        )
        upper = (
            2.5
            if k == len(grass_events)
            else grass_events[k]["buffer64_transition_bracket_m"]["false_width_m"]
        )
        witness = 0.01 if k == 0 else (lower + upper) / 2
        state = action(witness)
        state.update(
            numerically_bracketed_width_interval_m=[lower, upper],
            current_thin_widths_with_same_DG=[
                p["nominal_ring_width_m"]
                for p in raw
                if p["demolished_ids"] == state["demolished_ids"]
                and p["cleared_physical_cell_ids"] == state["cleared_physical_cell_ids"]
            ],
            interval_not_formally_verified_for_all_IEEE_widths=True,
        )
        plateaus.append(state)
    positive_events = [
        {
            "physical_cell_id": r["physical_cell_id"],
            "exact_euclidean_distance_decimal_m": r["euclidean_boundary_distance"][
                "distance_decimal_enclosure_m"
            ],
            "buffer64_numerical_transition_bracket": r["buffer64_transition_bracket_m"],
            "added_whole_cell_construction_C_m2": 25,
        }
        for r in grass_events
    ]
    limit_roof_mask = np.array([bool(o) and set(o) <= zero_threshold_D for o in base.owners])
    limit_physical_mask = limit_roof_mask | np.isin(base.cell_id, g_limit)
    seed_ground_mask = seed_clear | np.isin(base.cell_id, g_limit)
    report = {
        "schema_version": "heritage-hull-thin-width-event-audit-1",
        "new_CA_checks": 0,
        "graphs_built": 0,
        "native_CA_outcome_files_read": [],
        "width_domain_m": [0, 2.5],
        "inner_offset_m": 0,
        "reference_master_problem_sha256": reference["problem_sha256"],
        "inventory_sha256": inventory.sha256,
        "seed_ids": list(seed),
        "protected_core_and_actual_source_ids": sorted(protected),
        "actual_sources": actual,
        "current_rules": {
            "roof": (
                "per_original_patch_area_intersection_strictly_greater_than1e-8m2;"
                "owners_union;seed_always;protected_never"
            ),
            "grass": (
                "whole_eligible5m_footprint_intersects_closed_GEOS_ring_including_hole_boundary"
            ),
            "buffer_quad_segs": 64,
            "roof_area_threshold_m2": TAU,
        },
        "exact_arithmetic_scope": (
            "binary64_input_coordinates_as_rationals;convex_halfplane_area_and_segment_squared_distance;"
            "not_a_proof_of_GEOS_floating_offset_implementation"
        ),
        "grass_limit_closed_ring_ids": sorted(g_limit),
        "grass_limit_positive_outside_hull_ids": sorted(outside_g_limit),
        "inside_only_tangent_grass_ids": sorted(inside_tangent),
        "grass_critical_records": g_records,
        "roof_owner_critical_records": owner_records,
        "roof_patch_support_records": roof_support_records,
        "practical_ground_event_plateau_witnesses": plateaus,
        "sorted_positive_ground_events": positive_events,
        "monotonicity_scope": {
            "real_geometry_proof": (
                "nested_outer_buffers_with_fixed_inner_imply_nested_rings;"
                "positive_area_roof_and_closed_G_predicates_monotone;"
                "owner_AND_clearance_and_unique25m2_ground_cost_non_decreasing"
            ),
            "sampled_floating_masks_non_decreasing": not monotonic_failures,
            "formal_all_IEEE_widths_monotonicity_proof_claimed": False,
        },
        "candidate_window_is_conservative_bbox_not_distance_prefilter": True,
        "width0_01_action": baseline,
        "zero_area_threshold_geometric_support_limit": {
            "roof_area_threshold_m2": 0,
            "demolished_ids": sorted(zero_threshold_D),
            "cleared_physical_cell_ids": sorted(g_limit),
            "roof_C_geometry_m2": zero_roof_C,
            "C_m2": zero_roof_C + 25 * len(g_limit),
            "matches_width0_01_DG": sorted(zero_threshold_D) == baseline["demolished_ids"]
            and sorted(g_limit) == baseline["cleared_physical_cell_ids"],
            "matches_width0_01_actual_cleared_node_mask": hashlib.sha256(
                limit_physical_mask.tobytes()
            ).hexdigest()
            == baseline["actual_cleared_base_node_mask_sha256"],
            "first_positive_grass_euclidean_distance_lower_bound_m": None
            if not math.isfinite(lower_g_boundary)
            else lower_g_boundary,
            "is_not_current_positive_area_threshold_implementation_limit": True,
        },
        "fixed_positive_area_threshold_continuum_limit": {
            "theorem": (
                "area_each_roof_patch_intersection<=perimeter(H)*w+pi*w^2;"
                "finite_positive_tau_implies_no_additional_D_for_sufficiently_small_w"
            ),
            "roof_ids": list(seed),
            "grass_closed_ring_limit_ids": sorted(g_limit),
            "seed_roof_C_geometry_m2": seed_C,
            "ideal_continuum_C_m2": seed_C + 25 * len(g_limit),
            "sufficient_all_patch_area_below_tau_width_upper_m": continuum_delta,
            "bound_ring_area_at_that_width_m2": hull.length * continuum_delta
            + math.pi * continuum_delta**2,
            "width_bound_below_global_coordinate_ulp": continuum_delta
            < max(math.ulp(float(x)) for x in hull.bounds),
            "not_claimed_as_a_reliable_GEOS_double_width_or_a_fire_safe_strategy": True,
            "direct_seed_plus_boundary_ground_candidate_untested": {
                "demolished_ids": list(seed),
                "cleared_physical_cell_ids": sorted(g_limit),
                "C_m2": seed_C + 25 * len(g_limit),
                "actual_cleared_base_node_mask_sha256": hashlib.sha256(
                    seed_ground_mask.tobytes()
                ).hexdigest(),
                "CA_feasibility_unknown": True,
                "no_isolation_certificate_claimed": True,
            },
        },
        "floating_width_probes": probes,
        "sampled_monotonicity_violations": monotonic_failures,
        "critical_event_counts": {
            "ordinary_owner_tau_events_in0to2_5": len(owner_cut_events),
            "positive_distance_grass_events_in0to2_5": len(grass_events),
            "grass_zero_distance_limit_count": len(g_limit),
        },
        "width0_01_equals_implementation_infinitesimal_limit": False,
        "reason": (
            "fixed_positive_area_threshold_and_double_coordinate_offset_collapse_are_different_"
            "from_zero_threshold_geometric_support;do_not_identify_an_epsilon_with_a_limit"
        ),
        "strict_real_width_transition_proof_claimed_for_buffer64": False,
        "case_cost_definition": (
            "all_owner_AND_actual_roof_geometry_plus25m2_unique_ground_footprints;"
            "covered_fuel_reported_separately"
        ),
        "prior51_parameter_grid_result_unchanged": True,
        "input_sha256": initial,
        "generator_sha256": file_sha256(Path(__file__)),
        "elapsed_s": time.monotonic() - started,
    }
    for path, digest in initial.items():
        assert file_sha256(path) == digest
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thin-catalog",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-thin-hull-20261111-catalog.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-thin-width-events-20261111.json",
    )
    args = parser.parse_args()
    result = prepare(args.thin_catalog.resolve(), args.output.resolve())
    print(
        {
            "new_CA_checks": 0,
            "elapsed_s": result["elapsed_s"],
            "events": result["critical_event_counts"],
            "w0_01_equals_zero_threshold_limit": result[
                "zero_area_threshold_geometric_support_limit"
            ]["matches_width0_01_DG"],
            "w0_01_equals_implementation_limit": False,
        }
    )


if __name__ == "__main__":
    main()
