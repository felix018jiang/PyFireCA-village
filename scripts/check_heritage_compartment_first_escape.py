"""Conservative first-escape heat budget for a fixed protected bundle of ordinary roofs."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import (
    components,
    potential_edges,
    proven_source_witnesses,
    summarize,
)
from check_heritage_registered_roof_compartment import area_outward, footprint_budget, verify

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def load_bound_inputs(village):
    root = Path(__file__).resolve().parents[1]
    res = root / "docs/results"
    baseline = res / f"heritage-thermal-clearance-{village}-20261111.json"
    geom_result = res / f"heritage-registered-roof-compartment-{village}-20261111.json"
    energy = read_json(baseline)
    geo = read_json(geom_result)
    verify(
        geo.pop("receipt_sha256") == content_hash(geo)
        and geo["thermal_base_certificate_sha256"] == file_sha256(baseline)
        and geo["method_sha256"]
        == file_sha256(Path(__file__).with_name("check_heritage_registered_roof_compartment.py")),
        "geometric parent altered",
    )
    runroot = root / "runs/research"
    paths = [
        (
            "design",
            runroot / f"heritage-thermal-and-geometric-pareto-{village}-20261111/experiment.json",
        )
    ]
    if village == "batuan":
        paths.append(
            (
                "previously_disclosed_confirmation",
                runroot / "heritage-batuan-thermal-vs-D9-revealed-sources-20261111/experiment.json",
            )
        )
    base = read_json(paths[0][1])
    config = base["config"]
    bundle, transport, inv = load_study(Path(config["study_path"]))
    parent = bundle.request
    verify(
        request_fingerprint(parent) == base["problem"]["case_sha256"]
        and inv.sha256 == base["problem"]["inventory_sha256"]
        and transport.wind_speed_m_s == 0
        and parent.firebrands is None,
        "registered physics differs",
    )
    D = set(energy["roof_actions"])
    G = set(energy["ground_parent_actions"])
    families = []
    for kind, p in paths:
        native = read_json(p)
        sha_key = kind if kind == "design" else "disclosed_common_grid_native"
        verify(
            geo["frozen_input_campaign_files_sha256"][sha_key] == file_sha256(p),
            "unregistered source campaign or change",
        )
        for code, digest in native["source_sha256"].items():
            if code.startswith("src/"):
                verify(file_sha256(root / code) == digest, "frozen equations changed")
        for fire in native["problem"]["fires"].values():
            prot = fire["protocol"]
            (r,) = realize_sources(prot, inv, demolished_ids=sorted(D))
            s = r["source_id"]
            fp = r["footprint"]
            verify(fp.sha256 == content_hash(fire["footprints"][s]), "frozen footprint changed")
            families.append((kind, prot, s, fp))
    L = resolve_roof_layout(
        parent,
        inv,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config["core_overlap_policy"],
    )
    return geo, geom_result, L, inv, transport, D, G, families, config


def pair_conditional_doses(layout, polys, inside, outside, source_components, transport):
    p = layout.node_inputs.parameters

    def f(name):
        return getattr(p, name).ravel()

    terrain = layout.physical_request.ground_elevation_m
    z = np.zeros(len(polys)) if terrain is None else terrain.ravel()[layout.cell_id]
    src_energy = np.maximum(
        f("available_energy_J"),
        f("pyrolysable_dry_fuel_mass_kg") * f("heat_of_combustion_J_kg_dry"),
    ) * f("radiative_fraction")
    sizes = f(
        "covered_area_m2"
    )  # The receiver-area to modeled-covered-area quotient cancels in the J/J formulation below.
    cf = (
        f("effective_heated_dry_mass_kg")
        * f("specific_heat_J_kg_K")
        * (f("ignition_temperature_K") - layout.physical_request.scenario.ambient_temperature_K)
    )
    verify(
        np.all(cf[outside] > 0) and np.all(sizes[outside] > 0),
        "external target has invalid dry capacity or modeled area",
    )
    verify(np.all(src_energy[inside] > 0), "group contains invalid source material")
    support = polys[outside].copy()
    ground = np.array([not layout.owners[j] for j in outside])
    support[ground] = shapely.points(layout.xy_m[outside[ground]])
    tree = shapely.STRtree(polys[outside])
    groups = sorted({tuple(s) for s in source_components})
    max_fraction = {}
    contact = set()
    matrix_check = 0
    R = transport.exposure_radius_m
    for src_tuple in groups:
        source = np.asarray(src_tuple, dtype=int)
        if not len(source):
            raise ValueError("empty registered source compartment")
        raw = tree.query(polys[source], predicate="dwithin", distance=R + 1e-8)
        values = {}
        for j, k in zip(raw[0], raw[1], strict=True):
            s = source[j]
            t = outside[k]
            if polys[s].distance(polys[t]) <= 1e-8:
                contact.add(int(t))
            d = polys[s].distance(support[k])
            if d > R + 1e-8:
                continue
            dz = z[s] + f("source_height_m")[s] / 2 - z[t] - f("receiver_height_m")[t]
            d2 = d * d + dz * dz
            length = math.sqrt(d2)
            kernel = math.exp(-transport.atmospheric_extinction_m_inv * length) / (
                4 * math.pi * (d2 + transport.near_field_regularization_m**2)
            )
            ratio = src_energy[s] * kernel * f("receiver_area_m2")[t] * f("absorptivity")[t] / cf[t]
            values.setdefault(int(t), []).append(ratio)
        for t, parts in values.items():
            # Independent vectorized reconstruction uses per-unit-cover form and all
            # parents in this source cohort, including zero-support pairs.
            ds = shapely.distance(polys[source], support[np.searchsorted(outside, t)])
            dz = z[source] + 0.5 * f("source_height_m")[source] - (z[t] + f("receiver_height_m")[t])
            q = ds**2 + dz**2
            ks = np.where(
                ds <= R + 1e-8,
                np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(q))
                / (4 * np.pi * (q + transport.near_field_regularization_m**2)),
                0.0,
            )
            independent = (
                float(np.sum(src_energy[source] * ks))
                * (f("receiver_area_m2")[t] / sizes[t])
                * f("absorptivity")[t]
                / (cf[t] / sizes[t])
            )
            value = math.fsum(parts)
            verify(
                math.isclose(value, independent, rel_tol=1e-12, abs_tol=1e-12),
                "independent recipient dose differs",
            )
            matrix_check += 1
            max_fraction[t] = max(value, max_fraction.get(t, 0.0))
    return max_fraction, contact, matrix_check


def create(village):
    geo, parent_file, L, inv, t, D, G, families, conf = load_bound_inputs(village)
    protected = {x.building_id for x in inv.targets}
    anchors = {x[2] for x in families}
    active = L.node_inputs.burnable_mask.ravel()
    core = np.array([bool(protected.intersection(g)) for g in L.owners])
    inside = np.array([bool(anchors.intersection(g)) for g in L.owners])
    base_clear = np.array([bool(g) and set(g) <= D for g in L.owners]) | np.isin(L.cell_id, list(G))
    verify(not np.any(inside & (core | base_clear)), "fixed group/core or clearance overlap")
    edges = potential_edges(L, t.exposure_radius_m)
    labels = components(edges, active & inside)
    inside_nodes = np.flatnonzero(inside)
    source_components = []
    for _, _, s, _ in families:
        labels_s = {labels[j] for j in inside_nodes if s in L.owners[j]}
        verify(-1 not in labels_s and labels_s, "retained ordinary source group invalid")
        source_components.append(
            tuple(inside_nodes[np.isin(labels[inside_nodes], sorted(labels_s))].tolist())
        )
    external = np.flatnonzero(active & ~(inside | core | base_clear))
    polys = np.asarray(L.geometry, dtype=object)
    fractions, contacts, doublechecked = pair_conditional_doses(
        L, polys, inside_nodes, external, source_components, t
    )
    numeric_margin = 1e-10
    dose_fail = {i for i, v in fractions.items() if v >= 1 - numeric_margin}
    obligatory = contacts | dose_fail
    D1 = D | {o for i in obligatory for o in L.owners[i]}
    G1 = G | {int(L.cell_id[i]) for i in obligatory if not L.owners[i]}
    verify(
        not anchors & D1 and not protected & D1 and set(inv.candidate_ids) >= D1,
        "unremovable target",
    )
    oldplan = geo["model_action"]
    verify(
        set(oldplan["demolished_ids"]) >= D1 and set(oldplan["cleared_physical_cell_ids"]) >= G1,
        "tighter rule unexpectedly requires new controls versus geometric enclosure",
    )
    clear = np.array([bool(g) and set(g) <= D1 for g in L.owners]) | np.isin(L.cell_id, list(G1))
    verify(
        not np.any((inside | core) & clear) and all(clear[i] for i in obligatory),
        "mandatory actions not attained",
    )
    retained_ratio = max([0.0] + [v for i, v in fractions.items() if not clear[i]])
    verify(retained_ratio < 1 - numeric_margin, "an ordinary target may escape")
    # A separate polygon-to-polygon residual-contact query ignores radiation coefficients.
    ext_kept = external[~clear[external]]
    joins = shapely.STRtree(polys[ext_kept]).query(
        polys[inside], predicate="dwithin", distance=1e-8
    )
    verify(joins.shape[1] == 0, "an exterior contact survives")
    tree = shapely.STRtree(polys)
    allarea = L.node_inputs.parameters.covered_area_m2.ravel()
    unknown = {b.building_id for b in inv.buildings} - {
        o for j, g in enumerate(L.owners) if allarea[j] > 0 for o in g
    }
    scenario_rows = []
    idx = {}
    for j, (kind, prot, s, fp) in enumerate(families):
        (post,) = realize_sources(prot, inv, demolished_ids=sorted(D1))
        verify(
            post["source_id"] == s and post["footprint"].sha256 == fp.sha256,
            "different source after cleanup",
        )
        old, a = footprint_budget(L, polys, tree, s, fp, D, G)
        new, b = footprint_budget(L, polys, tree, s, fp, D1, G1)
        verify(
            old == new and np.array_equal(a, b) and set(a) <= set(source_components[j]),
            "source areal/fuel budget changed",
        )
        nodes = np.array(source_components[j])
        A = area_outward(math.fsum(allarea[nodes]), len(nodes), conf["max_nodes"])
        N = len(unknown.union(*(set(L.owners[i]) for i in nodes)))
        idx[kind] = idx.get(kind, 0) + 1
        scenario_rows.append(
            {
                "campaign": kind,
                "declared_family_ordinal": idx[kind],
                "single_roof_A_upper_m2": A,
                "single_roof_N_owner_upper": N,
            }
        )
    verify(
        scenario_rows == geo["registered_source_family_capacities"],
        "fixed-source worst departures changed",
    )
    summary = summarize(
        L,
        inv,
        edges,
        ("thermal-plus-first-escape-energy-constraint", D1, G1, True),
        unknown,
        anchors,
        proven_source_witnesses(L, inv),
        conf["max_nodes"],
    )
    C = summary["whole_action_area_m2"]
    baseC = geo["construction_area_m2"] - geo["additional_above_thermal_C_m2"]
    removed = shapely.union_all([b.geometry for b in inv.buildings if b.building_id in D1])
    other = shapely.union_all([b.geometry for b in inv.buildings if b.building_id not in D1])
    check_C = removed.difference(other).area + len(G1) * L.physical_request.grid.cell_area_m2
    verify(abs(check_C - C) < 1e-6, "independent cost differs")
    return {
        "village": village,
        "zero_cross_geometry_plan_sha256": file_sha256(parent_file),
        "thermal_base_sha256": geo["thermal_base_certificate_sha256"],
        "construction_area_m2": C,
        "additional_above_thermal_C_m2": C - baseC,
        "area_saved_vs_zero_transfer_enclosure_m2": geo["construction_area_m2"] - C,
        "additional_roof_actions": len(D1 - D),
        "additional_ground_actions": len(G1 - G),
        "model_action": {"demolished_ids": sorted(D1), "cleared_physical_cell_ids": sorted(G1)},
        "retained_known_source_footprints_25m2_and_material_budgets_verified": len(families),
        "distinct_possible_simultaneous_inside_parent_sets": len(set(source_components)),
        "retained_noncore_exterior_contact_pairs": 0,
        "independently_recomputed_nonzero_cohort_recipient_doses": doublechecked,
        "per_recipient_first_escape_dry_capacity_fraction_boundary": 1.0,
        "conservative_fraction_guard": numeric_margin,
        "maximum_recomputed_retained_exterior_fraction": retained_ratio,
        "registered_source_family_capacities": scenario_rows,
        "registered_worst_A_upper_m2": max(x["single_roof_A_upper_m2"] for x in scenario_rows),
        "registered_worst_N_upper": max(x["single_roof_N_owner_upper"] for x in scenario_rows),
        "arbitrary_single_retained_ordinary_roof_anywhere": summary,
        "observed_native_A_m2": None,
        "observed_native_N": None,
        "new_CA_runs": 0,
        "model_scope": (
            "Frozen zero-wind, no firebrands model equations. Initial direct events are "
            "restricted to one nominated retained ordinary roof per family. Each family "
            "sums all source components touched by any piece of its one fixed roof. "
            "Nonburning exterior patches cannot emit. An earliest exterior ignition would "
            "require direct contact or sufficient total absorbed energy from this family; "
            "neither can occur on a retained noncore patch. The independent heritage "
            "budget averts core ignition as a relay."
        ),
        "minimum_scope": (
            "Fixed constituent roofs and fixed thermal base actions. Any exterior noncore "
            "atom that can contact the group or violates the given energy inequality is "
            "forced to be removed. C is monotone in whole-owner AND and ground-cell "
            "actions. Contact and radiative kernel bounds are sufficient conditions; "
            "optimality applies to this rule. No claim of global minimum among flame "
            "dynamics, no new native loss."
        ),
        "source_scope": (
            "Known families consumed for design, while arbitrarily placed new ordinary "
            "sources are only covered by the reported separate full-village envelope and "
            "universal heritage condition."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("village", choices=["batuan", "pingyan"])
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    d = create(a.village)
    d["schema_version"] = "fixed-ordinary-compartment-first-escape-1"
    d["analysis_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    with a.output.open("x") as f:
        json.dump(d, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
