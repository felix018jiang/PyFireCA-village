"""Bounded reallocation of a whole retained roof or grass cell to a registered compartment."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import area_outward, footprint_budget, verify
from export_heritage_joint_loss_actions import physical_features
from verify_heritage_total_protection_and_loss import NativeSupport

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs/results"
BASIS = RESULTS / "heritage-pingyan-zero-heat-and-design-loss-20261111.json"
NAME = "heritage-pingyan-expanded-zero-heat-and-design-loss-20261111"
MAX_TRIALS = 400
SECOND_STEP_REQUIRED_SAVING = 25.0
A_CAP = 241.49
N_CAP = 3


def sealed(p):
    d = read_json(p)
    verify(d.pop("receipt_sha256") == content_hash(d), "parent certificate changed")
    return d


class Experiment:
    def __init__(self):
        self.parent = sealed(BASIS)
        _, _, L, inv, t, _, _, prior, cfg = load_bound_inputs("pingyan")
        self.L = L
        self.inv = inv
        self.t = t
        self.cfg = cfg
        e = read_json(
            ROOT
            / "runs/research/heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
        )
        row = next(
            r
            for r in e["config"]["plans"]
            if r["plan_id"] == "minimum-ground-centre-core-clearance"
        )
        self.D0 = set(row["demolished_ids"])
        self.G0 = set(row["cleared_physical_cell_ids"])
        self.model = NativeSupport(L, t)
        self.geom = self.model.geo
        self.tree = shapely.STRtree(self.geom)
        self.base = self.model.mask(self.D0, self.G0)
        self.core_ids = {r.building_id for r in inv.targets}
        self.core = np.array([bool(self.core_ids.intersection(g)) for g in L.owners])
        self.fuel = L.node_inputs.burnable_mask.ravel()
        self.area = L.node_inputs.parameters.covered_area_m2.ravel()
        self.unknown = set(inv.by_id) - {
            o for i, g in enumerate(L.owners) if self.area[i] > 0 for o in g
        }
        self.families = []
        for _, prot, _, _ in prior:
            (s,) = realize_sources(prot, inv, demolished_ids=sorted(self.D0))
            self.families.append((prot, s["source_id"], s["footprint"]))
        verify(len(self.families) == 4, "changed source cohort")
        self.anchors = {r[1] for r in self.families}
        pool = np.flatnonzero([bool(self.anchors.intersection(g)) for g in L.owners])
        verify(
            not np.any(self.core[pool] | self.base[pool] | ~self.fuel[pool]),
            "invalid ordinary anchor",
        )
        aa, bb = shapely.STRtree(self.geom[pool]).query(
            self.geom[pool], predicate="dwithin", distance=t.exposure_radius_m + 1e-8
        )
        idx = aa < bb
        cc = components(
            np.column_stack((aa[idx], bb[idx])).astype(np.int32), np.ones(len(pool), bool)
        )
        self.family_sets = []
        self.budgets = []
        for _, owner, fp in self.families:
            budget, hit = footprint_budget(L, self.geom, self.tree, owner, fp, self.D0, self.G0)
            selected = np.unique(cc[np.searchsorted(pool, hit)])
            self.family_sets.append(frozenset(pool[np.isin(cc, selected)].tolist()))
            self.budgets.append(budget)
        self.start = tuple(dict.fromkeys(self.family_sets))
        self.assignment = [self.start.index(f) for f in self.family_sets]
        self.live = np.flatnonzero(self.fuel & ~self.core & ~self.base)
        self.tree_live = shapely.STRtree(self.geom[self.live])
        self.roof_nodes = defaultdict(set)
        self.ground_nodes = defaultdict(set)
        self.roof_cost = defaultdict(list)
        for i, g in enumerate(L.owners):
            if g:
                self.roof_cost[tuple(g)].append(self.geom[i].area)
            if not self.base[i] and self.fuel[i] and not self.core[i]:
                if g:
                    for o in g:
                        self.roof_nodes[o].add(i)
                else:
                    self.ground_nodes[int(L.cell_id[i])].add(i)
        self.costs = {g: math.fsum(xs) for g, xs in self.roof_cost.items()}
        self.required_cache = {}
        self.counter = Counter()

    def requirements(self, group):
        if group in self.required_cache:
            return self.required_cache[group]
        src = np.array(sorted(group))
        aa, bb = self.tree_live.query(
            self.geom[src], predicate="dwithin", distance=self.t.exposure_radius_m + 1e-8
        )
        s = src[aa]
        t = self.live[bb]
        sel = ~np.isin(t, src)
        s, t = s[sel], t[sel]
        dist = shapely.distance(self.geom[s], self.geom[t])
        bad = set(t[dist <= 1e-8].tolist())
        m = self.model
        dist = shapely.distance(m.support[s], m.support[t])
        sel = dist <= self.t.exposure_radius_m + 1e-8
        s, t, d = s[sel], t[sel], dist[sel]
        z = m.srcz[s] - m.dstz[t]
        r = np.hypot(d, z)
        p = self.t
        vals = (
            m.emit[s]
            * m.receiver[t]
            * np.exp(-p.atmospheric_extinction_m_inv * r)
            / (4 * np.pi * (d * d + z * z + p.near_field_regularization_m**2) * m.capacity[t])
        )
        verify(np.isfinite(vals).all() and np.all(vals >= 0), "invalid grouped dose")
        dest, rev = np.unique(t, return_inverse=True)
        totals = np.bincount(rev, weights=vals, minlength=len(dest))
        bad.update(dest[totals >= 1 - 1e-10].tolist())
        self.required_cache[group] = bad
        return bad

    def cap(self, g):
        arr = sorted(g)
        return (
            area_outward(math.fsum(self.area[arr]), len(arr), self.cfg["max_nodes"]),
            len(self.unknown.union(*(set(self.L.owners[j]) for j in g))),
        )

    def solution(self, groups):
        bad = set().union(*(self.requirements(g) for g in groups))
        D = self.D0 | {o for i in bad for o in self.L.owners[i]}
        G = self.G0 | {int(self.L.cell_id[i]) for i in bad if not self.L.owners[i]}
        if D & (self.anchors | self.core_ids) or not set(self.inv.candidate_ids) >= D:
            return None
        all_inside = set().union(*groups)
        for i in all_inside:
            g = self.L.owners[i]
            if (g and set(g) <= D) or (not g and int(self.L.cell_id[i]) in G):
                return None
        cost = math.fsum(v for grp, v in self.costs.items() if set(grp) <= D) + 25 * len(G)
        return cost, D, G

    def options(self, groups):
        options = {}
        for k, group in enumerate(groups):
            bad = self.requirements(group)
            roof_ids = sorted(
                {o for i in bad for o in self.L.owners[i]} - self.D0 - self.core_ids - self.anchors
            )
            grass = sorted({int(self.L.cell_id[i]) for i in bad if not self.L.owners[i]} - self.G0)
            items = [("ordinary_roof", o, self.roof_nodes[o]) for o in roof_ids]
            items += [("whole_ground_cell", g, self.ground_nodes[g]) for g in grass]
            for kind, _, addition in items:
                big = group | addition
                if big == group:
                    continue
                a, n = self.cap(big)
                if a > A_CAP or n > N_CAP:
                    continue
                extended = list(groups)
                extended[k] = frozenset(big)
                key = tuple(extended)
                options.setdefault(key, kind)
        return options

    def search(self, group0):
        batch = self.options(group0)
        candidates = list(batch.items())
        stats = Counter()
        best = None
        seen = 0
        for groups, kind in candidates[:MAX_TRIALS]:
            stats[kind] += 1
            seen += 1
            sol = self.solution(groups)
            if sol is None:
                stats["incompatible_retained_compartment"] += 1
                continue
            stats["feasible"] += 1
            if best is None or sol[0] < best[0][0]:
                best = (sol, groups)
        return best, dict(
            cap_admissible_unique_extensions=len(candidates),
            evaluated=seen,
            exhaustive_for_that_single_extension_step=(seen == len(candidates)),
            **stats,
        )

    def independent(self, sol, groups):
        C, D, G = sol
        L = self.L
        m = self.model
        mask = m.mask(D, G)
        verify(
            not mask[self.core].any() and self.D0 <= D and self.G0 <= G, "core baseline was lost"
        )
        ground = np.isin(L.cell_id, sorted(G))
        verify(
            not np.any(ground & m.roof) and int(ground.sum()) == len(G),
            "invalid whole grass action",
        )
        legal = (
            L.physical_request.domain_mask
            & L.physical_request.known_mask
            & L.physical_request.burnable_mask
            & (L.physical_request.building_index < 0)
        ).ravel()
        verify(legal[sorted(G)].all(), "selected grass lacks model support")
        external = np.flatnonzero(self.fuel & ~mask & ~self.core)
        pc = np.flatnonzero(self.core)
        q = shapely.STRtree(m.support[external]).query(
            self.geom[pc], predicate="dwithin", distance=self.t.exposure_radius_m + 1e-8
        )
        h = shapely.STRtree(self.geom[external]).query(
            self.geom[pc], predicate="dwithin", distance=1e-8
        )
        verify(not q.shape[1] and not h.shape[1], "core does not have zero external transfer")
        caps = []
        peaks = []
        for i, (prot, owner, fp) in enumerate(self.families):
            (now,) = realize_sources(prot, self.inv, demolished_ids=sorted(D))
            verify(
                now["source_id"] == owner and now["footprint"].sha256 == fp.sha256,
                "registered source changed",
            )
            budget, hits = footprint_budget(L, self.geom, self.tree, owner, fp, D, G)
            g = groups[self.assignment[i]]
            source = np.array(sorted(g))
            verify(
                np.array_equal(budget, self.budgets[i])
                and set(hits) <= g
                and not mask[source].any(),
                "footprint or material changed",
            )
            outside = np.setdiff1d(external, source)
            target, _, dose, contact, _ = m.pairs(source, outside)
            sums = defaultdict(list)
            for t, v in zip(target, dose, strict=True):
                sums[int(t)].append(v)
            maxdose = max((math.fsum(v) for v in sums.values()), default=0.0)
            peaks.append(maxdose)
            verify(
                not len(contact) and maxdose < 1 - 1.0e-10,
                "independent external dose/contact fails",
            )
            A, N = self.cap(g)
            verify(A <= A_CAP and N <= N_CAP, "compartment exceeds capacity")
            caps.append((A, N))
        roof = math.fsum(self.geom[i].area for i in np.flatnonzero(mask & m.roof))
        ground = 25 * len(G)
        verify(abs(C - (roof + ground)) < 1e-6, "area union mismatch")
        return dict(
            roof_union_m2=roof,
            whole_ground_m2=ground,
            worst_A_upper_m2=max(a for a, _ in caps),
            worst_original_N_upper=max(n for _, n in caps),
            largest_group_to_exterior_energy_fraction=max(peaks),
            positive_external_contact_edges=0,
            external_core_radiation_edges=0,
            external_core_contact_edges=0,
            unchanged_25m2_source_mass_and_energy_budgets=4,
        )


def execute():
    model = Experiment()
    groups = model.start
    current = model.solution(groups)
    confirm = model.parent["model_action"]
    verify(
        current
        and abs(current[0] - confirm["C_m2"]) < 1e-6
        and current[1] == set(confirm["demolished_ids"])
        and current[2] == set(confirm["cleared_physical_cell_ids"]),
        "original action not reproduced using cross-warehouse checks",
    )
    incumbent = current
    bestgroups = groups
    one, records = model.search(groups)
    steps = [records]
    if one and one[0][0] < incumbent[0] - 1e-7:
        incumbent, bestgroups = one
        if current[0] - incumbent[0] >= SECOND_STEP_REQUIRED_SAVING:
            two, counts = model.search(bestgroups)
            steps.append(counts)
            if two and two[0][0] < incumbent[0] - 1e-7:
                incumbent, bestgroups = two
    checked = model.independent(incumbent, bestgroups)
    improved = incumbent[0] < current[0] - 1e-6
    r = dict(
        schema_version="fixed-cohort-zero-heat-bounded-compartment-expansion-1",
        village="pingyan",
        parent_certificate_sha256=file_sha256(BASIS),
        zero_core_baseline_m2=model.parent["base_zero_heat_construction_m2"],
        original_clearance_m2=current[0],
        present_C_m2=incumbent[0],
        saved_clearance_m2=current[0] - incumbent[0],
        A_cap_m2=A_CAP,
        N_upper_cap=N_CAP,
        distinct_original_compartments=len(groups),
        improved=improved,
        checked=checked,
        search_steps=steps,
        step_limit=MAX_TRIALS,
        second_step_minimum_saving_trigger_m2=SECOND_STEP_REQUIRED_SAVING,
        enumerated_domain=(
            "Per unique compartment, add one whole retained roof or grass cell "
            "from its current escape violations, within the same loss caps. "
            "The second round begins only from the best first-round solution. "
            "All other receivers, including all other compartments, must satisfy "
            "the same contact and cumulative-energy guards."
        ),
        physical_scope=(
            "Four unchanged registered physical sources. Core has zero external heat "
            "for any exterior ignition. Village losses are analytical upper bounds."
        ),
        new_CA_runs=0,
        new_source_draws=0,
    )
    if improved:
        C, D, G = incumbent
        plan = dict(
            plan_id="bounded-expanded-zero-heat-compartment",
            demolished_ids=sorted(D),
            cleared_physical_cell_ids=sorted(G),
            C_m2=C,
        )
        features, crs = physical_features(Path(model.cfg["study_path"]), plan)
        gis = dict(
            type="FeatureCollection",
            name="pingyan_expanded_zero_heat_defensive_clearance",
            metric_crs=crs,
            construction_m2=C,
            features=features,
        )
        p = RESULTS / (NAME + ".geojson")
        with p.open("x") as f:
            json.dump(gis, f, allow_nan=False)
            f.write("\n")
        r["model_action"] = plan
        r["protection_geojson_sha256"] = file_sha256(p)
    r["method_sha256"] = file_sha256(__file__)
    r["receipt_sha256"] = content_hash(r)
    with (RESULTS / (NAME + ".json")).open("x") as f:
        json.dump(r, f, indent=2, allow_nan=False)
        f.write("\n")
    print(
        {
            k: r[k]
            for k in ["improved", "saved_clearance_m2", "present_C_m2", "checked", "search_steps"]
        }
    )


if __name__ == "__main__":
    execute()
