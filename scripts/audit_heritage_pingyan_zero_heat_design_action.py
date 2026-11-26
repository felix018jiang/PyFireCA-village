"""Independently check projected construction polygons and a zero-heat base control."""

import json
import math
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from audit_heritage_compartment_bounds import components, potential_edges
from audit_heritage_disclosed_subset_new_source_bounds import envelope
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import area_outward, verify
from verify_heritage_total_protection_and_loss import NativeSupport, direct_witness
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def audit(root):
    res = root / "docs/results"
    certificate = res / "heritage-pingyan-zero-heat-and-design-loss-20261111.json"
    gis = certificate.with_suffix(".geojson")
    original = (
        root
        / "runs/research/heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
    )
    cert = read_json(certificate)
    file = read_json(gis)
    native = read_json(original)
    verify(
        cert["receipt_sha256"]
        == content_hash({k: v for k, v in cert.items() if k != "receipt_sha256"})
        and file["certificate_file_sha256"] == file_sha256(certificate),
        "cannot bind independent receipt",
    )
    _, _, L, inv, t, _, _, families, cfg = load_bound_inputs("pingyan")
    plan = cert["model_action"]
    control = next(
        p
        for p in native["config"]["plans"]
        if p["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    D, G = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
    D0, G0 = set(control["demolished_ids"]), set(control["cleared_physical_cell_ids"])
    model = NativeSupport(L, t)
    parent = L.physical_request
    core = {x.building_id for x in inv.targets}
    is_core = np.array([not core.isdisjoint(group) for group in L.owners])
    fuel = L.node_inputs.burnable_mask.ravel()
    kept = fuel & ~model.mask(D, G)
    base_kept = fuel & ~model.mask(D0, G0)
    verify(np.all(kept[is_core]) and D0 <= D and G0 <= G, "cleared heritage or lost zero-heat base")
    # Construct ground polygons from edge coordinates in a new projection calculation.
    grid = parent.grid
    eligible = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    verify(
        D <= set(inv.candidate_ids) and D.isdisjoint(core) and all(eligible[g] for g in G),
        "action not in frozen eligibility domain",
    )
    keep_shapes = shapely.union_all([x.geometry for x in inv.buildings if x.building_id not in D])
    cleared_roofs = shapely.union_all(
        [x.geometry for x in inv.buildings if x.building_id in D]
    ).difference(keep_shapes)
    ground = []
    for g in sorted(G):
        row, col = divmod(g, grid.width)
        x, y = grid.x_min_m + col * grid.cell_size_m, grid.y_max_m - row * grid.cell_size_m
        ground.append(box(x, y - grid.cell_size_m, x + grid.cell_size_m, y))
    cleared_ground = shapely.union_all(ground)
    tf = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    returned = {
        f["properties"]["material"]: shapely.make_valid(
            transform(tf.transform, shape(f["geometry"]))
        )
        for f in file["features"]
    }
    differences = [
        returned["whole_ground_cells"].symmetric_difference(cleared_ground).area,
        returned["roof_union"].symmetric_difference(cleared_roofs).area,
    ]
    verify(
        max(differences) < 1e-4
        and math.isclose(cleared_ground.union(cleared_roofs).area, cert["C_m2"], abs_tol=1e-5),
        "exported GIS physically differs",
    )
    core_nodes = np.flatnonzero(is_core)
    exterior = np.flatnonzero(kept & ~is_core)
    targeted = shapely.STRtree(model.support[exterior]).query(
        model.support[core_nodes], predicate="dwithin", distance=t.exposure_radius_m + 1e-8
    )
    touched = shapely.STRtree(model.geo[exterior]).query(
        model.geo[core_nodes], predicate="dwithin", distance=1e-8
    )
    verify(targeted.shape[1] == touched.shape[1] == 0, "a core can receive exterior heat")
    design = []
    for typ, protocol, _, _ in families:
        (draw,) = realize_sources(protocol, inv, demolished_ids=sorted(D0))
        design.append((typ, protocol, draw["source_id"], draw["footprint"]))
    tree = shapely.STRtree(model.geo)
    verify(
        list(direct_witness((L, inv), model.geo, tree, D, G, design))
        == list(direct_witness((L, inv), model.geo, tree, D0, G0, design)),
        "final analyzed anchors changed",
    )
    area = L.node_inputs.parameters.covered_area_m2.ravel()
    unknown = set(inv.by_id) - {
        owner for j, group in enumerate(L.owners) if area[j] > 0 for owner in group
    }
    owners = {d[2] for d in design}
    allowed = np.array([not owners.isdisjoint(g) for g in L.owners]) & kept
    edges = potential_edges(L, t.exposure_radius_m)
    groups = components(edges, allowed)
    ordinary_external = np.flatnonzero(kept & ~(allowed | is_core))
    bounds = []
    comparison = []
    index = [None]
    for _, _, owner, fp in design:
        hit = tree.query(fp.geometry, predicate="intersects")
        amounts = shapely.area(shapely.intersection(model.geo[hit], fp.geometry))
        direct = np.array([L.owners[i] == (owner,) for i in hit]) & (amounts > 0)
        support = np.unique(groups[hit[direct]])
        verify(np.all(support >= 0), "direct source outside reachable set")
        inside = np.flatnonzero(np.isin(groups, support))
        target, _, dose, ct, _ = model.pairs(inside, ordinary_external)
        peak = np.bincount(target, weights=dose, minlength=1).max()
        verify(len(ct) == 0 and peak < 1 - 1e-10, "fixed design closed set lost")
        n = len(unknown.union(*(set(L.owners[i]) for i in inside)))
        a = area_outward(math.fsum(area[inside]), len(inside), cfg["max_nodes"])
        bounds.append((a, n))
        g, method = envelope(L, model.geo, edges, base_kept & ~is_core, index, hit[direct], t)
        ni = np.flatnonzero(g)
        comparison.append(
            (
                method,
                area_outward(math.fsum(area[ni]), len(ni), cfg["max_nodes"]),
                len(unknown.union(*(set(L.owners[i]) for i in ni if L.owners[i]))),
            )
        )
    upper = (max(a for a, _ in bounds), max(n for _, n in bounds))
    verify(
        upper
        == (
            cert["worst_registered_burned_area_upper_m2"],
            cert["worst_registered_original_building_owner_count_upper"],
        ),
        "separate graph component audit disagrees",
    )
    base_runs = []
    for head in sorted((root / "runs/research").glob("*/experiment.json")):
        exp = read_json(head)
        if exp.get("problem", {}).get("inventory_sha256") != inv.sha256:
            continue
        ids = [
            p["plan_id"]
            for p in exp.get("config", {}).get("plans", [])
            if set(p["demolished_ids"]) == D0 and set(p["cleared_physical_cell_ids"]) == G0
        ]
        if not ids:
            continue
        s = head.parent / "summary.json"
        summary = read_json(s) if s.exists() else {}
        matches = [
            r
            for r in summary.get("checks", [])
            if r.get("plan_id") in ids and r.get("manifest_sha256")
        ]
        base_runs.append(
            dict(
                native_config_file_sha256=exp["config_sha256"],
                exp_sha256=file_sha256(head),
                currently_saved_manifest_count=len(matches),
            )
        )
    receipt = dict(
        schema_version="zero-heat-combined-design-and-original-baseline-independent-audit-1",
        sealed_design_file_sha256=file_sha256(certificate),
        action_gis_sha256=file_sha256(gis),
        projected_GIS_maximum_symmetric_difference_m2=max(differences),
        separately_rebuilt_clearance_union_m2=cleared_ground.union(cleared_roofs).area,
        heritage_to_exterior_support_edges=0,
        heritage_to_exterior_polygon_contacts=0,
        design_source_families=4,
        design_worst_A_upper_m2=upper[0],
        design_worst_original_owner_upper=upper[1],
        baseline_clearance_m2=cert["base_zero_heat_construction_m2"],
        baseline_versus_final_fixed_source_footprints_and_budgets_verified=4,
        baseline_closed_by_local_energy_families=sum(
            s == "closed_lifetime_heat_budget" for s, _, _ in comparison
        ),
        baseline_closed_by_geometric_fallback_families=sum(
            s == "closed_geometric_component" for s, _, _ in comparison
        ),
        baseline_worst_A_upper_m2=max(a for _, a, _ in comparison),
        baseline_worst_original_owner_upper=max(n for _, _, n in comparison),
        baseline_native_inventory_snapshot=base_runs,
        baseline_native_manifest_checks_at_audit=sum(
            x["currently_saved_manifest_count"] for x in base_runs
        ),
        physical_control_scope="The analytic zero-core baseline and combined construction share four initial ordinary sources. A coarse upper bound for the baseline does not establish greater actual loss.",
        CA_evaluations=0,
        method_sha256=file_sha256(__file__),
    )
    receipt["receipt_sha256"] = content_hash(receipt)
    with (res / "heritage-pingyan-zero-heat-and-design-loss-independent-20261111.json").open(
        "x"
    ) as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        "verified combined C/A/N and bounded zero-core-only baseline",
        upper,
        receipt["baseline_worst_A_upper_m2"],
        receipt["baseline_worst_original_owner_upper"],
        flush=True,
    )


if __name__ == "__main__":
    audit(Path(__file__).resolve().parents[1])
