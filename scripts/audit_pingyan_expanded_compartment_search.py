"""Rebuild the bounded trial sets, costs and outcome commitments independently."""

import json
import math
from collections import defaultdict

import numpy as np
from expand_pingyan_fixed_protective_compartments import (
    A_CAP,
    BASIS,
    MAX_TRIALS,
    N_CAP,
    NAME,
    RESULTS,
    ROOT,
    SECOND_STEP_REQUIRED_SAVING,
    Experiment,
    sealed,
)

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


def verify(test, message):
    if not test:
        raise ValueError(message)


def reconstruct(exp, source, cache):
    if source in cache:
        return cache[source]
    m = exp.model
    nodes = np.array(sorted(source))
    dest = np.setdiff1d(exp.live, nodes)
    t, _, values, cont, _ = m.pairs(nodes, dest)
    terms = defaultdict(list)
    for j, x in zip(t, values, strict=True):
        terms[int(j)].append(x)
    required = set(cont.tolist()) | {j for j, vs in terms.items() if math.fsum(vs) >= 1 - 1e-10}
    cache[source] = required
    return required


def rebuild(exp, groups, cache):
    must = set().union(*(reconstruct(exp, g, cache) for g in groups))
    D = exp.D0 | {o for j in must for o in exp.L.owners[j]}
    G = exp.G0 | {int(exp.L.cell_id[j]) for j in must if not exp.L.owners[j]}
    if D & (exp.anchors | exp.core_ids) or not set(exp.inv.candidate_ids) >= D:
        return None
    removed = exp.model.mask(D, G)
    if removed[list(set().union(*groups))].any():
        return None
    roof = math.fsum(exp.geom[j].area for j in np.flatnonzero(removed & exp.model.roof))
    cost = roof + exp.L.physical_request.grid.cell_area_m2 * len(G)
    return cost, D, G


def audit():
    exp = Experiment()
    path = RESULTS / (NAME + ".json")
    signed = sealed(path)
    cache = {}
    current = exp.start
    sol = rebuild(exp, current, cache)
    start = sol[0]
    records = []
    for step, reported in enumerate(signed["search_steps"]):
        if step:
            verify(start - sol[0] >= SECOND_STEP_REQUIRED_SAVING - 1e-7, "invalid stage trigger")
        policy = exp.options(current)
        group_index = {v: i for i, v in enumerate(current)}
        # Independently collect all whole roof / grass additions from NativeSupport's exterior set.
        independently = set()
        for g in current:
            idx = group_index[g]
            for j in reconstruct(exp, g, cache):
                names = exp.L.owners[j]
                items = (
                    [
                        exp.roof_nodes[o]
                        for o in names
                        if o not in exp.D0 | exp.anchors | exp.core_ids
                    ]
                    if names
                    else [exp.ground_nodes[int(exp.L.cell_id[j])]]
                )
                for item in items:
                    new = g | item
                    a, n = exp.cap(new)
                    if new == g or a > A_CAP or n > N_CAP:
                        continue
                    state = list(current)
                    state[idx] = new
                    independently.add(tuple(state))
        verify(
            set(policy) == independently
            and len(policy) == reported["cap_admissible_unique_extensions"] <= MAX_TRIALS,
            "different extension set",
        )
        trials = []
        for candidate in policy:
            q = rebuild(exp, candidate, cache)
            actionhash = (
                None
                if q is None
                else content_hash(
                    dict(demolished_ids=sorted(q[1]), cleared_physical_cell_ids=sorted(q[2]))
                )
            )
            memberhash = content_hash([sorted(g) for g in candidate])
            trials.append((candidate, q, memberhash, actionhash))
        valid = [row for row in trials if row[1] is not None]
        verify(len(valid) == reported["feasible"], "different feasible count")
        mincost = min(r[1][0] for r in valid)
        ties = [r for r in valid if abs(r[1][0] - mincost) < 1e-7]
        records.append(
            dict(
                stage=step + 1,
                candidate_count=len(policy),
                admissible_count=len(valid),
                candidate_membership_sha256=content_hash(sorted(r[2] for r in trials)),
                protective_actions_and_costs_sha256=content_hash(
                    sorted((r[2], r[3], None if r[1] is None else r[1][0]) for r in trials)
                ),
                lowest_cost_m2=mincost,
                tied_lowest_cost_compartment_choices=len(ties),
                distinct_tied_physical_actions=len({r[3] for r in ties}),
            )
        )
        reference, _ = exp.search(current)
        if reference and reference[0][0] < sol[0] - 1e-7:
            verify(
                any(r[0] == reference[1] for r in ties), "selected action is outside tied optima"
            )
            current = reference[1]
            sol = rebuild(exp, current, cache)
    confirm = signed["model_action"]
    verify(
        abs(sol[0] - confirm["C_m2"]) < 1e-6
        and sol[1] == set(confirm["demolished_ids"])
        and sol[2] == set(confirm["cleared_physical_cell_ids"]),
        "different final action",
    )
    return dict(
        schema_version="bounded-compartment-enumeration-independent-1",
        parent_source_sha256=file_sha256(BASIS),
        published_winner_sha256=file_sha256(path),
        original_search_method_sha256=file_sha256(
            ROOT / "scripts/expand_pingyan_fixed_protective_compartments.py"
        ),
        trials=records,
        physical_budget_and_cross_warehouse_checks=exp.independent(sol, current),
        final_C_m2=sol[0],
        saving_m2=start - sol[0],
        holdout_families_used=0,
        new_CA_runs=0,
    )


if __name__ == "__main__":
    report = audit()
    report["method_sha256"] = file_sha256(__file__)
    report["receipt_sha256"] = content_hash(report)
    out = RESULTS / (NAME + "-enumeration-audit.json")
    with out.open("x") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
