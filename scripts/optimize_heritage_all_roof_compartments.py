"""Price a universal loss cap for a single ordinary-roof fire with zero heat at heritage."""

import argparse
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from audit_heritage_compartment_bounds import proven_source_witnesses
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import area_outward, verify
from prepare_heritage_joint_longcap import encoded, immutable
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from verify_heritage_total_protection_and_loss import NativeSupport

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
CAPS = {"pingyan": (241.49, 3), "batuan": (420.73, 4)}


def partition(layout, inventory, D0, support):
    core = {c.building_id for c in inventory.targets}
    candidates = sorted(set(inventory.candidate_ids) - D0)
    positions = {label: i for i, label in enumerate(candidates)}
    edges, owners = [], {}
    for i, annotations in enumerate(layout.owners):
        if not annotations or not core.isdisjoint(annotations):
            continue
        surviving = set(annotations) - D0
        if surviving:
            indexes = sorted(positions[k] for k in surviving)
            owners[i] = indexes[0]
            edges.extend((indexes[0], j) for j in indexes[1:])
    x, y = zip(*edges, strict=True) if edges else ([], [])
    graph = coo_matrix((np.ones(len(x)), (x, y)), shape=(len(candidates), len(candidates)))
    _, labels = connected_components(graph, directed=False)
    nodes, removable = defaultdict(list), defaultdict(set)
    for i, j in owners.items():
        nodes[labels[j]].append(i)
    for name, j in positions.items():
        removable[labels[j]].add(name)
    groups = sorted(nodes)
    tags = np.full(len(layout.owners), -1, int)
    for g, original in enumerate(groups):
        tags[nodes[original]] = g
    verify(all(support.roof[i] for i in owners), "a roof group contains ground")
    return tags, [np.asarray(nodes[k]) for k in groups], [removable[k] for k in groups]


def exclusion_rows(support, tags, retained, destinations):
    # Each ratio covers one initially burning group over its entire fuel lifetime.
    target, source, ratios, ct, cs = support.pairs(retained, destinations)
    size = len(tags)
    keys = tags[source] * size + target
    unique, inverse = np.unique(keys, return_inverse=True)
    totals = np.bincount(inverse, weights=ratios)
    unsafe = set(unique[totals >= 1 - 1e-10].tolist())
    unsafe.update((tags[cs] * size + ct).tolist())
    conflicts, grounds = set(), defaultdict(set)
    for value in unsafe:
        g, t = divmod(value, size)
        other = tags[t]
        if other >= 0 and other != g:
            conflicts.add(tuple(sorted((g, int(other)))))
        elif not support.roof[t]:
            grounds[g].add(int(support.layout.cell_id[t]))
    return sorted(conflicts), grounds


def optimize(allowed, cost, grounds, conflicts, confirmed, want_retention=None):
    usable = set(np.flatnonzero(allowed))
    cell_uses = defaultdict(list)
    for g in usable:
        for cell in grounds[g]:
            cell_uses[cell].append(g)
    shared = sorted(c for c, uses in cell_uses.items() if len(uses) > 1)
    n = len(cost)
    objective = np.r_[-cost, np.full(len(shared), 25.0)]
    for uses in cell_uses.values():
        if len(uses) == 1:
            objective[uses[0]] += 25
    if want_retention is not None:
        objective = np.r_[-np.asarray(want_retention, float), np.zeros(len(shared))]
    ix, jx, coeff, bound = [], [], [], []

    def row(columns, values, upper):
        ix.extend([len(bound)] * len(columns))
        jx.extend(columns)
        coeff.extend(values)
        bound.append(upper)

    for i, j in conflicts:
        if i in usable and j in usable:
            row([i, j], [1, 1], 1)
    for k, cell in enumerate(shared, n):
        for g in cell_uses[cell]:
            row([g, k], [1, -1], 0)
    real = sorted(usable & confirmed)
    verify(bool(real), "no group supports an admissible positive-area ordinary roof fire")
    row(real, [-1] * len(real), -1)
    matrix = coo_matrix((coeff, (ix, jx)), shape=(len(bound), len(objective))).tocsc()
    upper = np.r_[allowed.astype(float), np.ones(len(shared))]
    result = milp(
        objective,
        integrality=np.ones(len(objective)),
        bounds=Bounds(np.zeros(len(objective)), upper),
        constraints=LinearConstraint(matrix, -np.inf, bound),
        options={"time_limit": 240, "mip_rel_gap": 0, "threads": 1},
    )
    if result.x is None:
        raise RuntimeError(f"no feasible universal placement: {result.message}")
    integer = np.rint(result.x)
    verify(
        np.max(np.abs(integer - result.x)) < 1e-5
        and np.all(matrix @ integer <= np.array(bound) + 1e-8)
        and np.all(integer <= upper)
        and np.all(integer >= 0),
        "rounded solution violates the integer feasibility conditions",
    )
    selected = set(np.flatnonzero(integer[:n]))
    return selected, result


def run(village):
    _, _, layout, inv, transport, _, _, _, cfg = load_bound_inputs(village)
    baseline = ROOT / "docs/results/heritage-ground-centre-core-separation-20261111.json"
    zero = read_json(baseline)
    verify(zero.pop("audit_sha256") == content_hash(zero), "zero-heat basis changed")
    reference = (
        ROOT
        / f"runs/research/heritage-thermal-and-geometric-pareto-{village}-20261111/experiment.json"
    )
    exp = read_json(reference)
    plan = next(
        p for p in exp["config"]["plans"] if p["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    D0, G0 = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
    certificate = next(v for v in zero["villages"] if v["village"] == village)
    verify(
        content_hash(dict(demolished_ids=sorted(D0), cleared_physical_cell_ids=sorted(G0)))
        == certificate["canonical_actions_sha256"],
        "zero-heat plan differs",
    )
    support = NativeSupport(layout, transport)
    nodes, groups, owning = partition(layout, inv, D0, support)
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    uncertain_ids = set(inv.by_id) - {
        k for j, own in enumerate(layout.owners) if area[j] > 0 for k in own
    }
    A, N = CAPS[village]
    area_caps = [area_outward(math.fsum(area[js]), len(js), cfg["max_nodes"]) for js in groups]
    n_caps = [len(uncertain_ids.union(*(set(layout.owners[j]) for j in js))) for js in groups]
    allowed = np.array([a <= A and n <= N for a, n in zip(area_caps, n_caps, strict=True)])
    cost = np.array([math.fsum(layout.geometry[j].area for j in js) for js in groups])
    witnesses = proven_source_witnesses(layout, inv)
    supported = {i for i, ids in enumerate(owning) if not ids.isdisjoint(witnesses)}
    verify(np.all(support.roof[np.flatnonzero(nodes >= 0)]), "ordinary-group mask changed")
    ground = ~support.roof & ~support.mask(D0, G0) & layout.node_inputs.burnable_mask.ravel()
    eligible_nodes = np.concatenate([groups[g] for g in np.flatnonzero(allowed)])
    destinations = np.sort(np.r_[eligible_nodes, np.flatnonzero(ground)])
    conflicts, grounds = exclusion_rows(support, nodes, eligible_nodes, destinations)
    upper_cost = certificate["minimum_C_m2_for_mixed_support_rule"] + float(sum(cost))

    def details(selected):
        D = D0.union(*(own for g, own in enumerate(owning) if g not in selected))
        G = G0.union(*(grounds[g] for g in selected))
        mask = support.mask(D, G)
        all_kept = np.flatnonzero((nodes >= 0) & ~mask)
        verify(
            np.array_equal(all_kept, np.sort(np.concatenate([groups[g] for g in selected]))),
            "original ownership clearing changed the selected groups",
        )
        verify(
            all(not (i in selected and j in selected) for i, j in conflicts),
            "retained groups fail separation",
        )
        verify(
            np.all(layout.physical_request.building_index.ravel()[sorted(G)] < 0)
            and len(G) == np.count_nonzero(mask & ~support.roof),
            "invalid whole-ground action",
        )
        roofs = math.fsum(layout.geometry[j].area for j in np.flatnonzero(mask & support.roof))
        cost_m2 = roofs + 25 * len(G)
        retained_owners = set.union(*(owning[g] for g in selected))
        return dict(
            C_m2=cost_m2,
            roof_clearance_m2=roofs,
            whole_ground_area_m2=25 * len(G),
            retained_ordinary_roof_labels=len(retained_owners),
            retained_roof_labels_with_proven_25m2_footprint=len(retained_owners & witnesses),
            retained_modeled_ordinary_roof_area_m2=math.fsum(cost[list(selected)]),
            group_worst_burned_area_upper_m2=max(area_caps[g] for g in selected),
            group_worst_burned_owner_count_upper=max(n_caps[g] for g in selected),
            protective_action_sha256=content_hash(
                dict(demolished_ids=sorted(D), cleared_physical_cell_ids=sorted(G))
            ),
        )

    choice, result = optimize(allowed, cost, grounds, conflicts, supported)
    minimum = details(choice)
    lower = upper_cost + float(result.mip_dual_bound)
    verify(
        abs(upper_cost + result.fun - minimum["C_m2"]) < 1e-5 and lower <= minimum["C_m2"] + 1e-5,
        "optimizer and original physical area disagree",
    )
    preservation, second = optimize(
        allowed, cost, grounds, conflicts, supported, [len(s) for s in owning]
    )
    most = details(preservation)
    return dict(
        village=village,
        whole_village_original_ordinary_roof_labels=len(inv.candidate_ids),
        scope=(
            "Every single retained ordinary-roof ignition with all directly ignited material "
            "on that roof, both supported meshes; any exterior ignition for core zero heat. "
            "Fixed action domain of whole shared-roof groups; other ground fires do not have "
            "the village loss cap."
        ),
        admissibility=(
            "At least one retained label has an originally exclusive physical footprint "
            "capable of supporting 25 square metres of direct ignition."
        ),
        screening=(
            "Each shared-ownership group fits the caps, every pair of retained groups passes "
            "the group-to-receiver lifetime dose and contact tests, and every required "
            "ground cell is cleared."
        ),
        loss_caps=dict(physical_burned_m2=A, original_owner_upper=N),
        eligible_groups=int(allowed.sum()),
        total_groups=len(groups),
        conflicting_group_pairs=len(conflicts),
        minimum_clearance=minimum,
        solver_minimum_lower_bound_m2=lower,
        solver_minimum_gap_m2=max(0.0, minimum["C_m2"] - lower),
        solver_status=result.message,
        maximum_roof_retention=most,
        solver_upper_bound_retained_labels=min(
            len(inv.candidate_ids), math.floor(-second.mip_dual_bound + 1e-7)
        ),
        maximum_retention_solver_status=second.message,
        new_fire_draws=0,
        native_fire_runs=0,
        input_zero_heat_sha256=file_sha256(baseline),
        input_experiment_sha256=file_sha256(reference),
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village", choices=CAPS)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.village)
    report["receipt_sha256"] = content_hash(report)
    immutable(args.output, encoded(report))
