"""Bound construction to reporting precision, with componentwise exact-rational LP certificates."""

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import audit_heritage_all_roof_compartments as physical
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
Q = Fraction


def split_components(groups, pairs, obligations):
    adjacency = {g: set() for g in groups}
    for a, b in pairs:
        adjacency[a].add(b)
        adjacency[b].add(a)
    uses = defaultdict(list)
    for g in groups:
        for c in obligations[g]:
            uses[c].append(g)
    for users in uses.values():
        for other in users[1:]:
            adjacency[users[0]].add(other)
            adjacency[other].add(users[0])
    remain = set(groups)
    out = []
    while remain:
        start = min(remain)
        remain.remove(start)
        queue = [start]
        seen = []
        while queue:
            i = queue.pop()
            seen.append(i)
            for j in sorted(adjacency[i] & remain):
                remain.remove(j)
                queue.append(j)
        out.append(sorted(seen))
    return out


def bound_component(groups, pairs, obligations, areas, ref, deadline, counters):
    shared = set(groups)
    ix = {g: i for i, g in enumerate(groups)}
    n = len(groups)
    edges = {(ix[a], ix[b]) for a, b in pairs if a in shared}
    by = [obligations[g] for g in groups]
    cells = sorted(set().union(*by))
    ci = {c: n + i for i, c in enumerate(cells)}
    c = [areas[g] for g in groups] + [Q(-25)] * len(cells)
    keep = {ix[g] for g in groups if g in ref}

    def price(kept):
        return sum(c[i] for i in kept) - 25 * len(set().union(*(by[i] for i in kept)))

    target = price(keep)
    if n == 1:
        counters["single_group_components"] += 1
        return max(Q(0), price({0}))
    near = defaultdict(set)
    rows = []
    for a, b in sorted(edges):
        near[a].add(b)
        near[b].add(a)
        rows.append(({a: 1, b: 1}, 1))
    cliques = set()
    for a, b in sorted(edges):
        for k in sorted(near[a] & near[b]):
            if k > b:
                group = {a, b, k}
                for other in sorted(near[a] & near[b] & near[k]):
                    if group <= near[other]:
                        group.add(other)
                cliques.add(tuple(sorted(group)))
    rows.extend(({i: 1 for i in clique}, 1) for clique in sorted(cliques))
    for i in range(n):
        for cell in by[i]:
            rows.append(({i: 1, ci[cell]: -1}, 0))
    row_index = []
    columns = []
    values = []
    for r, (coeff, _) in enumerate(rows):
        for i, v in coeff.items():
            row_index.append(r)
            columns.append(i)
            values.append(v)
    B = np.array([r[1] for r in rows])
    M = coo_matrix((values, (row_index, columns)), shape=(len(rows), len(c))).tocsr()
    obj = np.array([-float(v) for v in c])
    # Less than one hundred-thousandth of a square metre per nontrivial component.
    tol = Q(1, 10**7)

    def visit(on, off):
        counters["rational_LP_nodes"] += 1
        lo = np.zeros(len(c), int)
        hi = np.ones(len(c), int)
        for i in on:
            lo[i] = 1
            for x in by[i]:
                lo[ci[x]] = 1
        for i in off:
            hi[i] = 0
        physical.verify(np.all(lo <= hi), "inconsistent branch")
        result = linprog(
            obj,
            A_ub=M,
            b_ub=B,
            bounds=np.column_stack([lo, hi]),
            method="highs",
            options={"threads": 1},
        )
        physical.verify(result.success, "LP solver did not return a dual candidate")
        # For Ax<=b and any nonnegative multipliers y, use U=y*b+max_bound (c-A'y)*x.
        lam = [Q.from_float(max(0.0, -float(v))) for v in result.ineqlin.marginals]
        residual = c.copy()
        U = sum(y * int(b) for y, b in zip(lam, B, strict=True))
        for w, (coeff, _) in zip(lam, rows, strict=True):
            for i, coef in coeff.items():
                residual[i] -= w * coef
        U += sum(q * (int(hi[i]) if q >= 0 else int(lo[i])) for i, q in enumerate(residual))
        if target + tol >= U:
            counters["dual_pruned_nodes"] += 1
            return U
        selected = {i for i in range(n) if result.x[i] > 0.5}
        if (
            on <= selected
            and not selected & off
            and not any(i in selected and j in selected for i, j in edges)
            and price(selected) + tol >= U
        ):
            counters["relaxed_integral_nodes"] += 1
            return U
        undecided = set(range(n)) - on - off
        if not undecided or time.monotonic() >= deadline:
            counters["bounded_remaining_nodes"] += 1
            return U
        # The children exhaust all binary values. Neighbours of an enabled group are off.
        split = max(undecided, key=lambda j: (min(result.x[j], 1 - result.x[j]), len(near[j]), -j))
        low = visit(on, off | {split})
        high = visit(on | {split}, off | near[split])
        return min(U, max(low, high))

    return visit(set(), set())


def audit(village, seconds):
    res = ROOT / "docs/results"
    runs = ROOT / "runs/research"
    source = res / f"heritage-all-ordinary-roofs-tight-cap-{village}-independent-20261111.json"
    orig = physical.sealed(source)
    actionpath = (
        runs
        / "heritage-universal-tight-cap-audit-inputs-20261111"
        / f"{village}-protective-actions.json"
    )
    control = physical.sealed(actionpath)["controls"]["minimum_clearance"]
    E = read_json(
        runs / f"heritage-thermal-and-geometric-pareto-{village}-20261111" / "experiment.json"
    )
    base = next(
        r for r in E["config"]["plans"] if r["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    removed = set(control["demolished_ids"])
    counters = Counter()
    report = {}
    original_bound = physical.dual_bounds

    def extra(areas, labels, eligible, pairs, obligations, allclear):
        fixed = {i for i in eligible if removed.isdisjoint(labels[i])}
        physical.verify(
            all(removed.isdisjoint(g) or g <= removed for g in labels), "partial roof group"
        )
        pieces = split_components(eligible, pairs, obligations)
        deadline = time.monotonic() + seconds
        dual = [
            bound_component(g, pairs, obligations, areas, fixed, deadline, counters) for g in pieces
        ]
        upperC = (
            allclear
            - sum(areas[g] for g in fixed)
            + 25
            * (len(control["cleared_physical_cell_ids"]) - len(base["cleared_physical_cell_ids"]))
        )
        lowerC = allclear - sum(dual)
        physical.verify(
            lowerC <= upperC and abs(float(upperC) - orig["feasible_upper_C_m2"]) < 1e-6,
            "invalid global bound",
        )

        def cents(v):
            return math.floor(100 * v + Q(1, 2))

        report.update(
            village=village,
            scope="Fixed whole-roof groups and registered containment screening.",
            components=len(pieces),
            largest_component_roof_groups=max(map(len, pieces)),
            C_lower_m2=math.nextafter(float(lowerC), -math.inf),
            C_feasible_upper_m2=math.nextafter(float(upperC), math.inf),
            interval_width_m2=math.nextafter(float(upperC - lowerC), math.inf),
            lower_bound_exact_rational=[str(lowerC.numerator), str(lowerC.denominator)],
            feasible_cost_exact_rational=[str(upperC.numerator), str(upperC.denominator)],
            rounded_minimum_C_m2=(cents(upperC) / 100 if cents(lowerC) == cents(upperC) else None),
            bounds_round_to_same_hundredth_m2=cents(lowerC) == cents(upperC),
            exact_equal_for_stored_atom_costs=(lowerC == upperC),
            branch_counts=dict(counters),
            arithmetic=(
                "Nonnegative floating dual multipliers are converted to exact rationals. "
                "Residuals are maximized over binary branch bounds. Shared ground costs "
                "link groups into the same component."
            ),
            new_native_fire_runs=0,
            per_village_branch_time_limit_s=seconds,
        )
        return original_bound(areas, labels, eligible, pairs, obligations, allclear)

    with patch.object(physical, "dual_bounds", extra):
        reverified = physical.audit(village)
    physical.verify(
        reverified["candidates"] == orig["candidates"], "independent feasible witness changed"
    )
    report["applied_to_physical_certificate_sha256"] = file_sha256(source)
    report["registered_action_inputs_sha256"] = file_sha256(actionpath)
    report["physical_auditor_sha256"] = file_sha256(physical.__file__)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("village", choices=["batuan", "pingyan"])
    p.add_argument("--seconds", type=float, default=90.0)
    a = p.parse_args()
    d = audit(a.village, a.seconds)
    d["method_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    out = (
        ROOT
        / f"docs/results/heritage-all-ordinary-roofs-tight-cap-{a.village}-cost-proof-20261111.json"
    )
    with out.open("x") as f:
        json.dump(d, f, indent=2, allow_nan=False)
        f.write("\n")
