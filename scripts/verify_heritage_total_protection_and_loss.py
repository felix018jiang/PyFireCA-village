"""Independent bidirectional dose and integer cost checks for a fixed protective compartment."""

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_thermal_optimum import UNIT, prove_cover_impossible
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import area_outward, verify
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def read_sealed(p):
    v = read_json(p)
    require = v.pop("receipt_sha256")
    verify(require == content_hash(v), "binding seal changed")
    return v


class NativeSupport:
    def __init__(self, layout, transport):
        self.layout = layout
        self.t = transport
        self.geo = np.array(layout.geometry, dtype=object)
        self.support = self.geo.copy()
        self.roof = np.array([bool(g) for g in layout.owners])
        ground = np.flatnonzero(~self.roof)
        self.support[ground] = shapely.points(layout.xy_m[ground])
        p = layout.node_inputs.parameters
        self.parameters = p
        e = np.maximum(
            p.available_energy_J, p.pyrolysable_dry_fuel_mass_kg * p.heat_of_combustion_J_kg_dry
        )
        self.emit = (e * p.radiative_fraction).ravel()
        amb = layout.physical_request.scenario.ambient_temperature_K
        self.capacity = (
            p.effective_heated_dry_mass_kg
            * p.specific_heat_J_kg_K
            * (p.ignition_temperature_K - amb)
        ).ravel()
        self.receiver = (p.receiver_area_m2 * p.absorptivity).ravel()
        Z = layout.physical_request.ground_elevation_m
        height = np.zeros(len(self.geo)) if Z is None else Z.ravel()[layout.cell_id]
        self.srcz = height + p.source_height_m.ravel() / 2.0
        self.dstz = height + p.receiver_height_m.ravel()
        self.group = [
            tuple(g) if g else (int(layout.cell_id[i]),) for i, g in enumerate(layout.owners)
        ]

    def pairs(self, src, dst):
        # Both support centers are used for a ground-ground pair; each roof stays a polygon.
        # The candidate receiver subset is identified by an independent tree over the sources.
        a, b = shapely.STRtree(self.support[src]).query(
            self.support[dst], predicate="dwithin", distance=self.t.exposure_radius_m + 1e-8
        )
        source = src[b]
        target = dst[a]
        dist = shapely.distance(self.support[source], self.support[target])
        dz = self.srcz[source] - self.dstz[target]
        r = np.hypot(dist, dz)
        values = (
            (self.emit[source] * self.receiver[target] / self.capacity[target])
            * np.exp(-self.t.atmospheric_extinction_m_inv * r)
            / (4 * np.pi * (r * r + self.t.near_field_regularization_m**2))
        )
        verify(np.isfinite(values).all() and np.all(values >= 0), "invalid dose table")
        x, y = shapely.STRtree(self.geo[src]).query(
            self.geo[dst], predicate="dwithin", distance=1e-8
        )
        return target, source, values, dst[x], src[y]

    def mask(self, D, G):
        return np.array([bool(g) and set(g) <= D for g in self.layout.owners]) | np.isin(
            self.layout.cell_id, list(G)
        )

    def grouping(self, targets, source, target, values):
        by = defaultdict(lambda: defaultdict(list))
        for t, s, q in zip(target, source, values, strict=True):
            by[t][self.group[s]].append(q)
        return [{g: math.fsum(v) for g, v in by[t].items()} for t in targets]


def direct_witness(layout, polygons, tree, D, G, families):
    for _, protocol, src, footprint in families:
        (actual,) = realize_sources(protocol, layout[1], demolished_ids=sorted(D))
        verify(
            actual["source_id"] == src and actual["footprint"].sha256 == footprint.sha256,
            "realized source changed",
        )
        L = layout[0]
        hits = tree.query(footprint.geometry, predicate="intersects")
        areas = shapely.area(shapely.intersection(polygons[hits], footprint.geometry))
        unique = np.array([L.owners[j] == (src,) for j in hits])
        keep = unique & (areas > 0)
        verify(
            math.fsum(areas[~unique]) <= 1e-8 and abs(math.fsum(areas[keep]) - 25) < 1e-7,
            "incomplete or overlapping footprint",
        )
        j = hits[keep]
        verify(src not in D and not G.intersection(L.cell_id[j]), "removed source fuel")
        w = areas[keep] / shapely.area(polygons[j])
        p = L.node_inputs.parameters
        obs = tuple(
            math.fsum(w * getattr(p, k).ravel()[j])
            for k in ["covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J"]
        )
        verify(abs(obs[0] - 25) < 1e-6 and min(obs) > 0, "source area or mass invalid")
        yield obs


def evaluate(village):
    root = Path(__file__).resolve().parents[1]
    r = root / "docs/results"
    solfile = r / f"heritage-total-protection-and-loss-{village}-20261111.json"
    mandatory_file = r / f"heritage-first-escape-mandatory-{village}-20261111.json"
    result = read_sealed(solfile)
    locks = read_sealed(mandatory_file)
    verify(
        result["mandatory_action_file_sha256"] == file_sha256(mandatory_file)
        and result["method_sha256"]
        == file_sha256(root / "scripts/optimize_heritage_protection_and_loss.py"),
        "candidate or parent changed",
    )
    geo, _, L, inv, transport, oldD, oldG, families, config = load_bound_inputs(village)
    model = NativeSupport(L, transport)
    core_ids = {x.building_id for x in inv.targets}
    anchors = {f[2] for f in families}
    active = L.node_inputs.burnable_mask.ravel()
    core = np.flatnonzero([bool(set(g) & core_ids) for g in L.owners])
    inside = np.flatnonzero([bool(set(g) & anchors) for g in L.owners])
    outside = np.setdiff1d(np.flatnonzero(active), np.r_[core, inside])
    rows_inside = np.setdiff1d(np.flatnonzero(active), core)
    verify(
        not set(core) & set(inside) and set(locks["must_retain_ordinary_roof_ids"]) == anchors,
        "anchor/core conflict",
    )
    # Independently partition the fixed group with SciPy CSR connected components.
    a, b = shapely.STRtree(model.geo[inside]).query(
        model.geo[inside], predicate="dwithin", distance=transport.exposure_radius_m + 1e-8
    )
    _, label = connected_components(
        coo_matrix((np.ones(len(a)), (a, b)), shape=(len(inside), len(inside))).tocsr(),
        directed=False,
    )
    tie = []
    ranklock = set()
    actual_budget_rows = []
    areas = L.node_inputs.parameters.covered_area_m2.ravel()
    missing = {b.building_id for b in inv.buildings} - {
        o for j, g in enumerate(L.owners) if areas[j] > 0 for o in g
    }
    count = Counter()
    for kind, prot, src, _ in families:
        selection = {label[k] for k, j in enumerate(inside) if src in L.owners[j]}
        group = inside[np.isin(label, list(selection))]
        tie.append(tuple(group.tolist()))
        pr = prot["draws"][0]["priority_ids"]
        ranklock.update(pr[: pr.index(src)])
        count[kind] += 1
        actual_budget_rows.append(
            dict(
                campaign=kind,
                declared_family_ordinal=count[kind],
                single_roof_A_upper_m2=area_outward(
                    math.fsum(areas[group]), len(group), config["max_nodes"]
                ),
                single_roof_N_owner_upper=len(missing.union(*(set(L.owners[j]) for j in group))),
            )
        )
    verify(
        actual_budget_rows
        == result["registered_family_loss_bounds"]
        == geo["registered_source_family_capacities"]
        and ranklock == set(locks["source_priority_mandatory_removed_roof_ids"]),
        "registered bounds or source priorities changed",
    )
    group_contact = set()
    dose_max = defaultdict(float)
    for group in set(tie):
        target, source, values, ctr, _ = model.pairs(np.array(group), outside)
        group_contact.update(int(k) for k in ctr)
        reduced = defaultdict(list)
        for t, q in zip(target, values, strict=True):
            reduced[int(t)].append(q)
        for t, v in reduced.items():
            dose_max[t] = max(dose_max[t], math.fsum(v))
    forced_escape = group_contact | {
        i for i, v in dose_max.items() if v >= 1 - locks["fraction_guard"]
    }
    roofE = {o for i in forced_escape for o in L.owners[i]}
    grassE = {int(L.cell_id[i]) for i in forced_escape if not L.owners[i]}
    verify(
        roofE == set(locks["first_escape_mandatory_roof_ids"])
        and grassE == set(locks["first_escape_mandatory_ground_parent_cells"]),
        "all-original escape action list disagrees",
    )
    target, source, values, _, core_contact = model.pairs(rows_inside, core)
    rows = model.grouping(core, source, target, values)
    roofC = {o for i in core_contact for o in L.owners[i]}
    grassC = {int(L.cell_id[i]) for i in core_contact if not L.owners[i]}
    Fd = roofE | ranklock | roofC
    Fg = grassE | grassC
    verify(not anchors & Fd, "mandatory clearing removes a registered retained source")
    all_core_groups = set.union(*(set(row) for row in rows))
    optional_roof = {o for g in all_core_groups for o in g if isinstance(o, str)} - Fd - anchors
    verify(not optional_roof, "new optional roof would require expanded subset enumeration")
    eligible_ground = sorted({g[0] for g in all_core_groups if isinstance(g[0], int)} - Fg)
    # Any action outside forced or core-influencing sets leaves the inequalities
    # unchanged after removing that action, and costs are monotone.
    p_area = L.physical_request.grid.cell_area_m2

    def require_model(d):
        return math.fsum(model.geo[j].area for j, g in enumerate(L.owners) if g and set(g) <= d)

    baseC = require_model(Fd) + len(Fg) * p_area
    tree = shapely.STRtree(model.geo)
    original_budgets = list(direct_witness((L, inv), model.geo, tree, oldD, oldG, families))
    lower = []
    for candidate in result["optimized_total_construction"]:
        ceiling = candidate["core_energy_ceiling"]
        D = set(candidate["demolished_ids"])
        G = set(candidate["cleared_physical_cell_ids"])
        verify(
            Fd <= D and Fg <= G and not anchors & D and set(inv.candidate_ids) >= D,
            "illegal binary action or missing lock",
        )
        mask = model.mask(D, G)
        ground_clear = np.isin(L.cell_id, list(G))
        native = L.physical_request
        legal = (
            native.domain_mask
            & native.known_mask
            & native.burnable_mask
            & (native.building_index < 0)
            & (native.parameters.covered_area_m2 > 0)
        ).ravel()
        verify(
            np.all(legal[list(G)])
            and int(ground_clear.sum()) == len(G)
            and not np.any(ground_clear & model.roof)
            and not np.any(mask[np.r_[core, inside]]),
            "illegal ground clearance or destroyed protected support",
        )
        dose = max(math.fsum(v for g, v in r.items() if not set(g) <= (D | G)) for r in rows)
        escape = max([0.0] + [q for i, q in dose_max.items() if not mask[i]])
        verify(
            dose <= ceiling
            and dose < 1
            and escape < 1 - locks["fraction_guard"]
            and abs(dose - candidate["max_core_absorbed_energy_fraction"]) < 1e-11,
            "nonignition certificate fails",
        )
        verify(
            list(direct_witness((L, inv), model.geo, tree, D, G, families)) == original_budgets,
            "source budgets differ",
        )
        parentcost = require_model(D) + len(G) * p_area
        charged = shapely.union_all([b.geometry for b in inv.buildings if b.building_id in D])
        remaining = shapely.union_all([b.geometry for b in inv.buildings if b.building_id not in D])
        raw = charged.difference(remaining).area + len(G) * p_area
        verify(
            abs(parentcost - candidate["C_m2"]) < 1e-6 and abs(raw - parentcost) < 1e-6,
            "construction basis disagrees",
        )
        # Project any cheaper action onto compulsory actions plus relevant grass.
        # Co-owned roof atoms may additionally clear only if an anchor owner is removed,
        # which is prohibited, or a wholly irrelevant owner is added, which does not
        # cut any core or escape dose. Both numerical instances have no optional roof.
        k = round((parentcost - baseC) / p_area)
        verify(
            abs(parentcost - baseC - k * p_area) < 1e-6
            and Fd == D
            and (G - Fg) <= set(eligible_ground),
            "cost is not an integer ground increment",
        )
        fixed = []
        grass = []

        def q(x, threshold=ceiling):
            return math.floor(max(0.0, x / threshold - 1e-10) * UNIT)

        for row in rows:
            fixed.append(
                sum(q(v) for g, v in row.items() if isinstance(g[0], str) and not set(g) <= Fd)
            )
            grass.append([q(row.get((cell,), 0.0)) for cell in eligible_ground])
        w = np.array(grass, dtype=np.int64)
        need = w.sum(axis=1) + np.array(fixed, dtype=np.int64) - UNIT
        counts = Counter()
        impossible = prove_cover_impossible(w, need, k - 1, counts, time.monotonic() + 300)
        lower.append(
            dict(
                core_ceiling=ceiling,
                actual_total_C_m2=parentcost,
                original_owner_union_C_m2=raw,
                mandatory_C_m2=baseC,
                optional_roof_count=len(optional_roof),
                optional_relevant_ground_cells=len(eligible_ground),
                minimum_additional_whole_ground_cells_if_proven=k,
                preceding_projected_cost_tier_m2=parentcost - p_area,
                no_strictly_cheaper_plan_under_fixed_inequalities_proved=impossible,
                integer_relaxation_certificate_leaf_counts=dict(counts),
                max_external_to_core_dry_fraction=dose,
                max_group_to_surviving_exterior_dry_fraction=escape,
                all_direct_25m2_source_and_fuel_budgets_preserved=len(families),
                retained_core_contact_pairs=0,
                retained_cross_compartment_contact_pairs=0,
            )
        )
    return {
        "schema_version": "independent-joint-protection-and-loss-integer-proof-1",
        "village": village,
        "joint_candidate_sha256": file_sha256(solfile),
        "mandatory_receipt_sha256": file_sha256(mandatory_file),
        "thermal_and_escape_calculation_rebuilt_from_original_unintervened_fuel": True,
        "both_criteria_include_all_original_positive_potential_edges": True,
        "registered_equation_and_cost_table_scope": (
            "Threshold coefficients recomputed in binary64 with independent vectorized "
            "distance/heat accounting. Cheaper ground covers are excluded using exact "
            "integer inequalities on a downward-rounded relaxation."
        ),
        "thresholds": lower,
        "registered_family_worst_A_upper_m2": max(
            x["single_roof_A_upper_m2"] for x in actual_budget_rows
        ),
        "registered_family_worst_N_owner_upper": max(
            x["single_roof_N_owner_upper"] for x in actual_budget_rows
        ),
        "bounds_apply_to_fixed_families_not_arbitrary_settlement_loss": True,
        "native_fire_runs": 0,
        "solver_optimal_flag_used_for_lower_proof": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("village", choices=("batuan", "pingyan"))
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    d = evaluate(a.village)
    d["independent_audit_source_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    with a.output.open("x") as h:
        json.dump(d, h, indent=2, allow_nan=False)
        h.write("\n")


if __name__ == "__main__":
    main()
