"""Protective partition design and aggregate single-ordinary-roof terminal loss bounds."""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from numba import njit

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def check(test, reason):
    if not test:
        raise ValueError(reason)


@njit
def components(edges, kept):
    parent = np.arange(len(kept), dtype=np.int32)
    size = np.ones(len(kept), dtype=np.int32)
    for a, b in edges:
        if not kept[a] or not kept[b]:
            continue
        aa = a
        bb = b
        while parent[aa] != aa:
            parent[aa] = parent[parent[aa]]
            aa = parent[aa]
        while parent[bb] != bb:
            parent[bb] = parent[parent[bb]]
            bb = parent[bb]
        if aa != bb:
            if size[aa] < size[bb]:
                aa, bb = bb, aa
            parent[bb] = aa
            size[aa] += size[bb]
    for i in range(len(kept)):
        j = i
        while parent[j] != j:
            parent[j] = parent[parent[j]]
            j = parent[j]
        parent[i] = j if kept[i] else -1
    return parent


def potential_edges(layout, radius):
    active = np.flatnonzero(layout.node_inputs.burnable_mask.ravel())
    ground = np.array([not layout.owners[i] for i in active])
    support = np.array([layout.geometry[i] for i in active], dtype=object)
    support[ground] = shapely.points(layout.xy_m[active[ground]])
    check(
        radius > math.sqrt(2) * layout.physical_request.grid.cell_size_m,
        "physical contact must fit the enclosing geometric support radius",
    )
    tree = shapely.STRtree(support)
    pieces = []
    for begin in range(0, len(active), 1024):
        left, right = tree.query(
            support[begin : begin + 1024], predicate="dwithin", distance=radius + 1e-8
        )
        left = left + begin
        keep = left < right
        pieces.append(np.column_stack((active[left[keep]], active[right[keep]])).astype(np.int32))
    return np.concatenate(pieces)


def design(layout, inventory, thermal, radius):
    ids = set(thermal["roof_actions"])
    cells = set(thermal["ground_parent_actions"])
    A = set(inventory.candidate_ids)
    full = tuple(b.geometry for b in inventory.buildings)
    hull = shapely.convex_hull(shapely.union_all(full))
    ground = np.flatnonzero(
        layout.node_inputs.burnable_mask.ravel() & np.array([not group for group in layout.owners])
    )
    pts = shapely.points(layout.xy_m[ground])
    d = shapely.distance(pts, hull)
    width = radius + 0.05
    shell = set(layout.cell_id[ground[(d > 0) & (d <= width)]].tolist())
    ordinary_shapes = [
        b.geometry
        for b in inventory.buildings
        if b.source_label == "building" and b.building_id not in ids
    ]
    near = shapely.STRtree(ordinary_shapes).query(pts, predicate="dwithin", distance=width)
    interface = set(layout.cell_id[ground[np.unique(near[0])]].tolist())
    yield "thermal-plus-perimeter", ids, cells | shell
    yield "thermal-plus-building-ground-separation", ids, cells | interface
    groups_tree = shapely.STRtree(ordinary_shapes)
    a, b = groups_tree.query(ordinary_shapes, predicate="dwithin", distance=radius + 1e-8)
    choose = a < b
    cluster_labels = components(
        np.column_stack((a[choose], b[choose])).astype(np.int32),
        np.ones(len(ordinary_shapes), dtype=bool),
    )
    clusters = defaultdict(list)
    for label, shape in zip(cluster_labels, ordinary_shapes, strict=True):
        clusters[int(label)].append(shape)
    hulls = [shapely.convex_hull(shapely.union_all(shapes)) for shapes in clusters.values()]
    boundary = shapely.STRtree(hulls)
    near = boundary.query(pts, predicate="dwithin", distance=width)[0]
    inside = boundary.query(pts, predicate="covered_by")[0]
    cluster_ground = set(layout.cell_id[ground[np.setdiff1d(near, inside)]].tolist())
    yield "thermal-plus-roof-cluster-perimeters", ids, cells | cluster_ground
    grid = layout.physical_request.grid
    period = 150.0
    lo, bt, hi, top = hull.bounds
    stripe = []
    for axis, origin, low, high in ((0, grid.x_min_m, lo, hi), (1, grid.y_max_m, bt, top)):
        for z in range(
            math.floor((low - origin) / period), math.ceil((high - origin) / period) + 1
        ):
            value = origin + z * period
            if axis == 0:
                box = shapely.box(value - width / 2, bt - width, value + width / 2, top + width)
            else:
                box = shapely.box(lo - width, value - width / 2, hi + width, value + width / 2)
            stripe.append(box)
    bands = shapely.union_all(stripe)
    extra_roofs = {
        b.building_id
        for b in inventory.buildings
        if b.building_id in A and b.geometry.intersects(bands)
    }
    inside_stripes = (d <= width) & shapely.intersects(pts, bands)
    stripe_ground = set(layout.cell_id[ground[inside_stripes]].tolist())
    yield (
        "thermal-plus-perimeter-and-150m-compartments",
        ids | extra_roofs,
        cells | shell | stripe_ground,
    )


def proven_source_witnesses(layout, inventory):
    # A 6 m axis-aligned square wholly within a positive, originally exclusive roof
    # supports a 25 m² disk, including its ordinary polygonized representation.
    # This is a conservative count; the loss bound itself includes every surviving roof.
    eligible = {b.building_id for b in inventory.buildings if b.source_label == "building"}
    polygons = defaultdict(list)
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    for j, g in enumerate(layout.owners):
        if len(g) == 1 and area[j] > 0 and g[0] in eligible:
            polygons[g[0]].append(layout.geometry[j])
    confirmed = set()
    for owner, parts in polygons.items():
        union = shapely.union_all(parts)
        centres = [union.representative_point(), union.centroid]
        erosion = union.buffer(-3.05)
        if not erosion.is_empty:
            centres.append(erosion.representative_point())
        if any(union.covers(shapely.box(z.x - 3, z.y - 3, z.x + 3, z.y + 3)) for z in centres):
            confirmed.add(owner)
    return confirmed


def summarize(
    layout, inv, edges, action, unrepresented, registered_footprints, witnesses, max_nodes
):
    name, removed, cleared, universal = action
    active = layout.node_inputs.burnable_mask.ravel()
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    domain_roofs = set(inv.candidate_ids)
    ordinary = {b.building_id for b in inv.buildings if b.source_label == "building"}
    protected = {p.building_id for p in inv.targets}
    eligible = (
        layout.physical_request.domain_mask
        & layout.physical_request.known_mask
        & layout.physical_request.burnable_mask
        & (layout.physical_request.building_index < 0)
        & (layout.physical_request.parameters.covered_area_m2 > 0)
    ).ravel()
    check(
        removed <= domain_roofs and not removed & protected and all(eligible[c] for c in cleared),
        "illegal action",
    )
    clear_d = np.array([bool(g) and set(g) <= removed for g in layout.owners])
    clear_g = np.isin(layout.cell_id, list(cleared))
    check(
        clear_g.sum() == len(cleared)
        and not any(layout.owners[i] for i in np.flatnonzero(clear_g)),
        "ground must clear full legal cells",
    )
    for i in np.flatnonzero(clear_g):
        check(area[i] > 0, "empty ground action")
    core = np.array([bool(protected.intersection(g)) for g in layout.owners])
    check(not np.any(core & (clear_d | clear_g)), "protected roof clearing")
    kept = active & ~(clear_d | clear_g)
    propagation = kept & ~core if universal else kept
    labels = components(edges, propagation)
    c_area = np.bincount(labels[propagation], weights=area[propagation], minlength=len(labels))
    c_owner = defaultdict(set)
    source = defaultdict(set)
    for i, g in enumerate(layout.owners):
        if g and propagation[i] and area[i] > 0:
            c_owner[labels[i]].update(g)
            for owner in g:
                if owner in ordinary and owner not in protected and owner not in removed:
                    source[owner].add(labels[i])
    # Some ordinary roofs have disjoint parts. A single 25 m² direct footprint may touch
    # several components of the same house. Sum the distinct component areas jointly.
    max_area = 0.0
    max_count = 0
    sources_multicomponent = 0
    for comp in source.values():
        reach = unrepresented.copy()
        for c in comp:
            reach.update(c_owner[c])
        # Re-sum original nonnegative physical areas in binary64 compensated summation below.
        total = math.fsum(c_area[c] for c in comp)
        max_area = max(max_area, total)
        max_count = max(max_count, len(reach))
        sources_multicomponent += len(comp) > 1
    # Positive-sum error bound for np.bincount followed by fsum. Widen out, then round up.
    n = len(labels) + max(len(g) for g in source.values()) + 3
    u = 2.0**-53
    # Native refinement permits a per-parent conservation residual of 1e-6 m²
    # plus 1e-9 of its original covered area. Budget this across every parent.
    # Additional outward rounding covers the positive sums on the finest allowed mesh.
    refinement_slack = len(labels) * 1e-6 + max_area * 1e-9
    max_area = (max_area / (1 - n * u) + refinement_slack) / (1 - (max_nodes + 5) * u)
    max_area = math.ceil(math.nextafter(max_area, math.inf) * 100) / 100.0
    tested_kept = len(set(registered_footprints) - removed)
    check(tested_kept > 0, "all previously qualified footprint sources disappeared")
    roof_union = math.fsum(layout.geometry[i].area for i in np.flatnonzero(clear_d))
    return {
        "plan": name,
        "whole_action_area_m2": roof_union
        + len(cleared) * layout.physical_request.grid.cell_area_m2,
        "roof_union_m2": roof_union,
        "ground_cells_cleared": len(cleared),
        "roof_actions": len(removed),
        "retained_ordinary_source_roofs_overincluded": len(source),
        "source_roofs_with_multiple_components": sources_multicomponent,
        "original_registered_footprint_roofs_retained": tested_kept,
        "confirmed_ordinary_roofs_with_space_for_a_25m2_unambiguous_disk": len(witnesses - removed),
        "fraction_of_original_confirmed_source_roofs_retained": len(witnesses - removed)
        / len(witnesses),
        "physical_refinement_area_slack_m2": refinement_slack,
        "full_fire_observed_N": None,
        "full_fire_observed_A_m2": None,
        "unrepresented_original_owners_included_in_N_upper": len(unrepresented),
        "any_single_retained_ordinary_roof_burned_area_upper_m2_rounded_up": max_area,
        "any_single_retained_ordinary_roof_N_original_owners_upper": max_count,
        "protected_roofs_proved_nonigniting_for_arbitrary_retained_exterior_source": universal,
        "distinct_reachable_components_from_ordinary_sources": len(set().union(*source.values())),
        "action_contract_digest": content_hash(
            {"demolished_ids": sorted(removed), "cleared_physical_cell_ids": sorted(cleared)}
        ),
    }


def run(village, output):
    res = Path("docs/results")
    sep = read_json(res / f"heritage-core-separation-{village}-20261111.json")
    energy = read_json(res / f"heritage-thermal-clearance-{village}-20261111.json")
    epath = res / f"heritage-thermal-clearance-{village}-independent-20261111.json"
    audit = read_json(epath)
    check(
        audit.pop("receipt_sha256") == content_hash(audit)
        and audit["source_certificate_file_sha256"]
        == file_sha256(res / f"heritage-thermal-clearance-{village}-20261111.json"),
        "thermal audit fingerprint",
    )
    en = read_json(Path(sep["native_reference_run"]) / "experiment.json")
    config = en["config"]
    bundle, transport, inv = load_study(Path(config["study_path"]))
    check(
        request_fingerprint(bundle.request) == en["problem"]["case_sha256"]
        and inv.sha256 == en["problem"]["inventory_sha256"],
        "parent changed",
    )
    root = Path(__file__).resolve().parents[1]
    for p, h in en["source_sha256"].items():
        if p.startswith("src/"):
            check(file_sha256(root / p) == h, "reader differs from original")
    L = resolve_roof_layout(
        bundle.request,
        inv,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config["core_overlap_policy"],
    )
    edges = potential_edges(L, transport.exposure_radius_m)
    inventory_ids = {b.building_id for b in inv.buildings}
    area = L.node_inputs.parameters.covered_area_m2.ravel()
    represented = {owner for i, g in enumerate(L.owners) if area[i] > 0 for owner in g}
    unrepresented = inventory_ids - represented
    fp = set.union(*(set(x["footprints"]) for x in en["problem"]["fires"].values()))
    ref = next(
        p
        for p in config["plans"]
        if p["plan_id"]
        == (
            "direct-thin-D9-with-no-grass"
            if village == "batuan"
            else "roof-seed-protected-union-ring-b0-w15.01"
        )
    )
    actions = [
        ("no-clearance", set(), set(), False),
        (
            "historical-design-frontier",
            set(ref["demolished_ids"]),
            set(ref["cleared_physical_cell_ids"]),
            False,
        ),
        (
            "thermal-certificate",
            set(energy["roof_actions"]),
            set(energy["ground_parent_actions"]),
            True,
        ),
    ]
    actions.extend((*a, True) for a in design(L, inv, energy, transport.exposure_radius_m))
    witnesses = proven_source_witnesses(L, inv)
    rows = [
        summarize(L, inv, edges, a, unrepresented, fp, witnesses, int(config["max_nodes"]))
        for a in actions
    ]
    result = {
        "schema_version": "whole-village-single-ordinary-source-compartment-upper-bounds-2",
        "basis_sha256": file_sha256(epath),
        "method_sha256": file_sha256(__file__),
        "village": village,
        "number_parent_nodes": len(L.owners),
        "undirected_possible_transfer_pairs": len(edges),
        "model_radius_m": transport.exposure_radius_m,
        "complete_burnable_domain_covered_area_m2": math.fsum(
            area[L.node_inputs.burnable_mask.ravel()]
        ),
        "original_inventory_count": len(inventory_ids),
        "original_ordinary_candidates": len(inv.candidate_ids),
        "new_CA_trajectories": 0,
        "plans": rows,
        "proof": [
            (
                "The parent graph encloses every possible radiative pair in every "
                "refinement. Ground is an unsplit centre, roof support is the closed "
                "polygon. The 15 m radius exceeds the furthest parent-centre distance for "
                "any directly contacting 5 m ground squares."
            ),
            (
                "For one direct ignition footprint on one retained ordinary roof, all "
                "reachable potential components of every surviving patch bearing that "
                "owner are included. Given positive covered area, selecting more patches "
                "or more components only enlarges the bound."
            ),
            (
                "An independent universal thermal certificate allows protected parents to "
                "be removed from the propagation graph. Additional clearing removes "
                "external fuels, so the same contact and accumulated energy inequalities "
                "still hold. Empirically screened alternatives retain all core parents "
                "when computing the bound."
            ),
            (
                "The number bound unions original roof owners across included components, "
                "adds unrepresented original owners, and includes failed identity claims "
                "conservatively. The area uses physical covered fuel area after clearing."
            ),
            (
                "The bounds assume the closed frozen domain, no imposed fire entering "
                "later from outside, no spotting, no active ignition on a cleared patch, "
                "and one ordinary roof as the only source. They measure maximum possible "
                "modeled losses, not observed losses."
            ),
        ],
    }
    result["receipt_sha256"] = content_hash(result)
    with output.open("x") as h:
        json.dump(result, h, indent=2, allow_nan=False)
        h.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village", choices=["batuan", "pingyan"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.village, args.output)
