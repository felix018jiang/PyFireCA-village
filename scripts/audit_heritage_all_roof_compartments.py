"""Independently verify aggregate protection and finite-group clearance bounds."""

import argparse
import json
import math
from collections import defaultdict
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

import numpy as np
import shapely
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import fixed_area_ignition, unambiguous_roof
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def verify(ok, msg):
    if not ok:
        raise ValueError(msg)


def sealed(p, key="receipt_sha256"):
    d = read_json(p)
    verify(d.pop(key) == content_hash(d), "invalid certificate")
    return d


def grouping(owners, D0, core_ids):
    parent = {}

    def leader(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ordinary = {}
    for j, group in enumerate(owners):
        live = set(group) - D0
        if live and not core_ids.intersection(group):
            for o in live:
                parent.setdefault(o, o)
            first = next(iter(live))
            for name in live:
                parent[leader(name)] = leader(first)
            ordinary[j] = first
    parts = defaultdict(list)
    for j, first in ordinary.items():
        parts[leader(first)].append(j)
    return [np.array(j, dtype=int) for j in sorted(parts.values(), key=lambda js: js[0])]


def outward(area, count, maxnodes):
    bounded = (area * (1 + 1e-9) + count * 1e-6) / (1 - (maxnodes + 5) * 2.0**-53)
    return math.ceil(math.nextafter(bounded, math.inf) * 100) / 100


class Geometry:
    def __init__(self, L, t):
        self.L = L
        self.t = t
        self.geom = np.array(L.geometry, dtype=object)
        self.roof = np.array([bool(o) for o in L.owners])
        self.target_geometry = self.geom.copy()
        self.target_geometry[~self.roof] = shapely.points(L.xy_m[~self.roof])
        p = L.node_inputs.parameters

        def f(name):
            return getattr(p, name).ravel()

        self.emission = np.maximum(
            f("available_energy_J"),
            f("pyrolysable_dry_fuel_mass_kg") * f("heat_of_combustion_J_kg_dry"),
        ) * f("radiative_fraction")
        self.coverage = f("covered_area_m2")
        self.chemical = f("available_energy_J")
        self.mass = f("initial_dry_fuel_mass_kg")
        amb = L.physical_request.scenario.ambient_temperature_K
        self.dry = (
            f("effective_heated_dry_mass_kg")
            * f("specific_heat_J_kg_K")
            * (f("ignition_temperature_K") - amb)
        )
        self.coupling = f("receiver_area_m2") * f("absorptivity")
        z = L.physical_request.ground_elevation_m
        ground = np.zeros(len(self.geom)) if z is None else z.ravel()[L.cell_id]
        self.hsrc = ground + f("source_height_m") * 0.5
        self.hdst = ground + f("receiver_height_m")

    def dose(self, src, dest, tree):
        a, b = tree.query(
            self.geom[src], predicate="dwithin", distance=self.t.exposure_radius_m + 1.0e-8
        )
        s = src[a]
        t = dest[b]
        polygon = shapely.distance(self.geom[s], self.geom[t])
        gap = shapely.distance(self.geom[s], self.target_geometry[t])
        contacts = t[polygon <= 1e-8]
        near = gap <= self.t.exposure_radius_m + 1e-8
        s, t, d = s[near], t[near], gap[near]
        z = self.hsrc[s] - self.hdst[t]
        rr = np.hypot(d, z)
        vals = (
            self.emission[s]
            * self.coupling[t]
            * np.exp(-self.t.atmospheric_extinction_m_inv * rr)
            / (4 * np.pi * (d * d + z * z + self.t.near_field_regularization_m**2) * self.dry[t])
        )
        verify(np.isfinite(vals).all() and np.all(vals >= 0), "illegal energy terms")
        # Sort on destination before fsum. The producer uses a flat array of group/target keys.
        order = np.argsort(t)
        t = t[order]
        vals = vals[order]
        uniq, idx = np.unique(t, return_index=True)
        total = np.array(
            [math.fsum(vals[i:j]) for i, j in zip(idx, np.r_[idx[1:], len(t)], strict=True)]
        )
        return contacts, uniq, total

    def source_counts(self, inv):
        # An original exclusive area <25 cannot contain a legal 25 m2 roof-only start.
        # A native footprint and a material check provide a constructive lower count.
        tree = shapely.STRtree(self.geom)
        all_valid = set()
        not_ruled_out = set()
        known = (
            self.L.node_inputs.domain_mask.ravel()
            & self.L.node_inputs.known_mask.ravel()
            & self.L.node_inputs.burnable_mask.ravel()
        )
        for o in inv.candidate_ids:
            try:
                roof = unambiguous_roof(inv, o)
            except ValueError:
                continue
            if roof.area < 25:
                continue
            not_ruled_out.add(o)
            fp = fixed_area_ignition(inv, o, roof.representative_point().coords[0], 25.0)
            hits = tree.query(fp.geometry, predicate="intersects")
            parts = shapely.area(shapely.intersection(self.geom[hits], fp.geometry))
            active = parts > 0
            hits = hits[active]
            parts = parts[active]
            own = np.array([self.L.owners[i] == (o,) for i in hits], dtype=bool)
            usable = own & known[hits]
            f = parts[usable] / shapely.area(self.geom[hits[usable]])
            if (
                math.fsum(parts[~usable]) < 1e-8
                and abs(math.fsum(parts[usable]) - 25) < 1e-7
                and abs(math.fsum(f * self.coverage[hits[usable]]) - 25) < 1e-6
                and math.fsum(f * self.mass[hits[usable]]) > 0
                and math.fsum(f * self.chemical[hits[usable]]) > 0
            ):
                all_valid.add(o)
        return all_valid, not_ruled_out


def dual_bounds(areas, labels, eligible, pairs, obligations, allclear):
    # Maximize saved roof area minus cost of the union of extra required grass cells.
    # Dropping the nonempty-source constraint and uncertain edges gives an upper relaxation.
    groups = sorted(eligible)
    gindex = {g: i for i, g in enumerate(groups)}
    cells = sorted(set().union(*(obligations[g] for g in groups)))
    gnum = len(groups)
    cindex = {c: gnum + i for i, c in enumerate(cells)}
    rows = []
    neighbors = defaultdict(set)
    for a, b in sorted(pairs):
        rows.append(({gindex[a]: 1, gindex[b]: 1}, 1))
        neighbors[a].add(b)
        neighbors[b].add(a)
    # Pairwise-incompatible cliques impose at most one retained group.
    cliques = set()
    for a, b in sorted(pairs):
        for k in sorted(neighbors[a] & neighbors[b]):
            if k > b:
                group = {a, b, k}
                for extra in sorted(neighbors[a] & neighbors[b] & neighbors[k]):
                    if group <= neighbors[extra]:
                        group.add(extra)
                cliques.add(tuple(sorted(group)))
    rows.extend(({gindex[g]: 1 for g in clique}, 1) for clique in sorted(cliques))
    for g in groups:
        for c in obligations[g]:
            rows.append(({gindex[g]: 1, cindex[c]: -1}, 0))
    ri = []
    ci = []
    entries = []
    for j, (row, _) in enumerate(rows):
        for i, v in row.items():
            ri.append(j)
            ci.append(i)
            entries.append(v)
    matrix = coo_matrix((entries, (ri, ci)), shape=(len(rows), gnum + len(cells))).tocsr()
    b = np.array([r[1] for r in rows])

    def upper(c):
        obj = np.array(list(map(float, c)))
        result = linprog(-obj, A_ub=matrix, b_ub=b, bounds=(0, 1), method="highs")
        verify(result.success, "LP relaxation failed")
        lam = [Fraction.from_float(max(0.0, -float(y))) for y in result.ineqlin.marginals]
        # All arithmetic after choosing the nonnegative dual multipliers is exact.
        upper = sum(x * int(y) for x, y in zip(lam, b, strict=True))
        residual = list(c)
        for y, (row, _) in zip(lam, rows, strict=True):
            for i, a in row.items():
                residual[i] -= a * y
        return upper + sum(max(0, q) for q in residual)

    U = upper([areas[g] for g in groups] + [Fraction(-25)] * len(cells))
    retention = upper([Fraction(len(labels[g])) for g in groups] + [Fraction(0)] * len(cells))
    return (
        math.nextafter(float(allclear - U), -math.inf),
        math.floor(retention),
        len(pairs),
        len(cells),
        len(cliques),
    )


def audit(village):
    res = ROOT / "docs/results"
    runs = ROOT / "runs/research"
    summary = res / f"heritage-all-ordinary-roofs-tight-cap-{village}-20261111.json"
    action_file = (
        runs
        / "heritage-universal-tight-cap-audit-inputs-20261111"
        / f"{village}-protective-actions.json"
    )
    zero_path = res / "heritage-ground-centre-core-separation-20261111.json"
    exp_path = (
        runs / f"heritage-thermal-and-geometric-pareto-{village}-20261111" / "experiment.json"
    )
    report = sealed(summary)
    actions = sealed(action_file)
    zero = sealed(zero_path, "audit_sha256")
    exp = read_json(exp_path)
    verify(
        actions["original_receipt_file_sha256"] == file_sha256(summary)
        and report["input_experiment_sha256"] == file_sha256(exp_path)
        and report["input_zero_heat_sha256"] == file_sha256(zero_path),
        "unbound action set",
    )
    cfg = exp["config"]
    bundle, t, inv = load_study(Path(cfg["study_path"]))
    phys = bundle.request
    verify(
        request_fingerprint(phys) == exp["problem"]["case_sha256"]
        and asdict(t) == exp["problem"]["transport_parameters"]
        and t.wind_speed_m_s == 0
        and phys.firebrands is None
        and inv.sha256 == exp["problem"]["inventory_sha256"],
        "different frozen physics",
    )
    L = resolve_roof_layout(
        phys, inv, max_nodes=cfg["max_nodes"], core_overlap_policy=cfg["core_overlap_policy"]
    )
    basis = next(
        row
        for row in exp["config"]["plans"]
        if row["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    D0 = set(basis["demolished_ids"])
    G0 = set(basis["cleared_physical_cell_ids"])
    z = next(v for v in zero["villages"] if v["village"] == village)
    verify(
        content_hash(dict(demolished_ids=sorted(D0), cleared_physical_cell_ids=sorted(G0)))
        == z["canonical_actions_sha256"],
        "different zero-heat baseline",
    )
    M = Geometry(L, t)
    core_ids = {r.building_id for r in inv.targets}
    hascore = np.array([bool(core_ids.intersection(os)) for os in L.owners])
    active = L.node_inputs.burnable_mask.ravel()
    ground = ~M.roof & active

    def clear(D, G):
        return np.array([bool(os) and set(os) <= D for os in L.owners]) | np.isin(
            L.cell_id, list(G)
        )

    zero_mask = clear(D0, G0)
    core = np.flatnonzero(hascore)
    src = np.flatnonzero(active & ~hascore & ~zero_mask)
    v, _ = shapely.STRtree(M.target_geometry[src]).query(
        M.geom[core], predicate="dwithin", distance=t.exposure_radius_m + 1e-8
    )
    a, _ = shapely.STRtree(M.geom[src]).query(M.geom[core], predicate="dwithin", distance=1e-8)
    verify(
        not len(v) and not len(a), "unblocked source can contact or irradiate the protected core"
    )
    groups = grouping(L.owners, D0, core_ids)
    tags = np.full(len(L.owners), -1, int)
    labels = []
    A = []
    N = []
    costs = []
    unknown = set(inv.by_id) - {o for j, os in enumerate(L.owners) if M.coverage[j] > 0 for o in os}
    for g, js in enumerate(groups):
        tags[js] = g
        original = set().union(*(set(L.owners[j]) for j in js))
        labels.append(original - D0)
        A.append(outward(math.fsum(M.coverage[js]), len(js), cfg["max_nodes"]))
        N.append(len(original | unknown))
        costs.append(sum(Fraction.from_float(float(M.geom[j].area)) for j in js))
    verify(
        len(groups) == report["total_groups"] and set().union(*labels) <= set(inv.candidate_ids),
        "owner partition mismatch",
    )
    limits = report["loss_caps"]
    allowed = {
        g
        for g in range(len(groups))
        if A[g] <= limits["physical_burned_m2"] and N[g] <= limits["original_owner_upper"]
    }
    verify(len(allowed) == report["eligible_groups"], "different group capacity exclusions")
    eligible = np.flatnonzero(np.isin(tags, list(allowed)) | (ground & ~zero_mask))
    tree = shapely.STRtree(M.geom[eligible])
    pairs = set()
    grounds = defaultdict(set)
    radiation = {}
    contacts = {}
    for g in sorted(allowed):
        close, dest, vals = M.dose(groups[g], eligible, tree)
        outside = tags[dest] != g
        dest, vals = dest[outside], vals[outside]
        contacts[g] = np.unique(close[tags[close] != g])
        radiation[g] = (dest, vals)
        # Conservative subset of mandatory inequalities for a certified cost lower bound.
        bad = np.union1d(contacts[g], dest[vals >= 1 + 1.0e-8])
        for node in bad:
            other = tags[node]
            if other >= 0:
                pairs.add(tuple(sorted((g, int(other)))))
            else:
                grounds[g].add(int(L.cell_id[node]))
    roof0 = sum(
        Fraction.from_float(float(M.geom[j].area)) for j in np.flatnonzero(zero_mask & M.roof)
    )
    allclear = roof0 + 25 * len(G0) + sum(costs)
    lower, retention_bound, edge_count, cells, cliques = dual_bounds(
        costs, labels, allowed, pairs, grounds, allclear
    )
    original_area = math.fsum(M.geom[j].area for j in np.flatnonzero(M.roof & ~hascore))
    legal = (
        phys.domain_mask
        & phys.known_mask
        & phys.burnable_mask
        & (phys.building_index < 0)
        & (phys.parameters.covered_area_m2 > 0)
    ).ravel()
    witness, potential = M.source_counts(inv)
    outputs = {}
    for named, control in actions["controls"].items():
        r = report[named]
        D = set(control["demolished_ids"])
        G = set(control["cleared_physical_cell_ids"])
        verify(
            content_hash(control) == r["protective_action_sha256"]
            and D0 <= D
            and G0 <= G
            and set(inv.candidate_ids) >= D
            and not D & core_ids
            and legal[list(G)].all(),
            "binding or non-legal construction",
        )
        removed = clear(D, G)
        selected = []
        for g, js in enumerate(groups):
            verify(removed[js].all() or not removed[js].any(), "split original shared-roof group")
            if not removed[js].any():
                selected.append(g)
        verify(
            set(selected) <= allowed and not removed[core].any(),
            "illegal retained group or removed heritage",
        )
        survive = set().union(*(labels[g] for g in selected))
        remaining = set(inv.candidate_ids) - D
        verify(
            survive <= remaining and (remaining - survive) <= unknown,
            "retained label escaped the bounded partition",
        )
        grass = np.isin(L.cell_id, list(G))
        verify(
            not np.any(grass & M.roof) and int(grass.sum()) == len(G),
            "ground cells affect roof support",
        )
        contacts_remaining = 0
        peak = 0.0
        for g in selected:
            contacts_remaining += int(np.count_nonzero(~removed[contacts[g]]))
            ds, vs = radiation[g]
            peak = max(peak, float(np.max(vs[~removed[ds]], initial=0.0)))
        verify(
            contacts_remaining == 0 and peak < 1 - 1.0e-10,
            "cross-group ignition certificate not established",
        )
        roof = math.fsum(M.geom[j].area for j in np.flatnonzero(removed & M.roof))
        C = roof + 25 * len(G)
        numerator = math.fsum(M.geom[j].area for j in np.flatnonzero(M.roof & ~hascore & ~removed))
        cnt = len(survive & witness)
        upper = len(survive & potential)
        verify(
            cnt > 0
            and cnt >= r["retained_roof_labels_with_proven_25m2_footprint"]
            and abs(C - r["C_m2"]) < 1e-6
            and len(survive) == r["retained_ordinary_roof_labels"]
            and abs(numerator - r["retained_modeled_ordinary_roof_area_m2"]) < 1e-6
            and max(A[g] for g in selected) == r["group_worst_burned_area_upper_m2"]
            and max(N[g] for g in selected) == r["group_worst_burned_owner_count_upper"],
            "published totals differ",
        )
        outputs[named] = dict(
            C_m2=C,
            roof_clearance_m2=roof,
            ground_clearance_m2=25 * len(G),
            ordinary_roof_labels_retained=len(survive),
            retained_fraction_of_original_ordinary_labels=len(survive) / len(inv.candidate_ids),
            retained_ordinary_roof_m2=numerator,
            retained_fraction_of_original_ordinary_roof_area=numerator / original_area,
            retained_native_25m2_area_fuel_energy_witness_count=cnt,
            upper_number_of_retained_25m2_possible_labels=upper,
            retained_source_fraction_lower=cnt / len(potential),
            retained_source_fraction_upper=min(1.0, upper / len(witness)),
            every_single_admissible_start_entirely_contained_in_one_group=True,
            any_single_roof_worst_area_upper_m2=max(A[g] for g in selected),
            any_single_roof_worst_original_owner_count_upper=max(N[g] for g in selected),
            noncore_clearance_contact_edges_remaining=contacts_remaining,
            max_group_to_surviving_exterior_dry_energy_ratio=peak,
            heritage_zero_contact_and_radiation=True,
        )
    ub = outputs["minimum_clearance"]["C_m2"]
    achieved = outputs["maximum_roof_retention"]["ordinary_roof_labels_retained"]
    verify(achieved <= retention_bound, "dual upper bound below achieved retention")
    verify(lower <= ub + 1e-8, "fractional dual exceeds feasible construction")
    return dict(
        schema_version="universal-initial-roof-group-independent-1",
        village=village,
        original_ordinary_roof_labels=len(inv.candidate_ids),
        original_ordinary_roof_area_m2=original_area,
        upper_original_labels_with_25m2_unambiguous_geometry=len(potential),
        original_native_25m2_roof_fuel_witness_count=len(witness),
        candidates=outputs,
        certified_lower_C_m2_for_registered_group_domain=lower,
        feasible_upper_C_m2=ub,
        certified_interval_width_m2=ub - lower,
        raw_solver_reported_lower_m2=report["solver_minimum_lower_bound_m2"],
        independent_rational_dual_inequality_pairs=edge_count,
        independent_rational_dual_distinct_grass_obligations=cells,
        independent_rational_dual_clique_rows=cliques,
        independent_ordinary_roof_retention_upper=retention_bound,
        achieved_retention_proved_maximal_in_registered_domain=(achieved == retention_bound),
        strict_minimum_claimed=False,
        source_scope=(
            "Any single admissible start entirely on a retained ordinary roof, "
            "under either mesh of the registered finite-fuel model."
        ),
        applicability=(
            "Single ordinary-roof starts for whole-village fire loss. The cost bound "
            "uses the registered whole roof group and whole grass cell decisions."
        ),
        new_source_draws=0,
        new_CA_runs=0,
        input_sha256={
            str(p.relative_to(ROOT)): file_sha256(p)
            for p in (summary, action_file, zero_path, exp_path)
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village", choices=["batuan", "pingyan"])
    v = parser.parse_args().village
    d = audit(v)
    d["auditor_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    path = (
        ROOT / f"docs/results/heritage-all-ordinary-roofs-tight-cap-{v}-independent-20261111.json"
    )
    with path.open("x") as out:
        json.dump(d, out, indent=2, allow_nan=False)
        out.write("\n")
