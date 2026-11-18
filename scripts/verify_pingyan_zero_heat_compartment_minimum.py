"""Verify the finite enclosure catalogue with a separate boundary graph and rational area sums."""

import argparse
import itertools
import math
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np
from expand_pingyan_fixed_protective_compartments import Experiment, sealed, verify
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


def verify_minimum(path):
    proposal = sealed(path)
    exp = Experiment()
    A, N = proposal["physical_burned_area_cap_m2"], proposal["original_owner_upper_cap"]
    source_pool = set.union(*(set(g) for g in exp.start))

    def graph(pool):
        dst, src, q, contact_dst, contact_src = exp.model.pairs(np.array(sorted(pool)), exp.live)
        contact = defaultdict(set)
        power = defaultdict(list)
        for s, d, ratio in zip(src, dst, q, strict=True):
            power[int(s)].append((int(d), float(ratio)))
        for s, d in zip(contact_src, contact_dst, strict=True):
            contact[int(s)].add(int(d))

        def exterior(group):
            forced = set()
            terms = defaultdict(list)
            for s in group:
                forced.update(contact[s])
                for target, value in power[s]:
                    if target not in group:
                        terms[target].append(value)
            forced.difference_update(group)
            forced.update(
                target for target, values in terms.items() if math.fsum(values) >= 1 - 1e-10
            )
            return forced

        return exterior

    original_boundary = graph(source_pool)
    families, entirely = [], set(source_pool)
    binary = 0
    for group in exp.start:
        possible = set()
        for j in original_boundary(group):
            owners = set(exp.L.owners[j])
            items = (
                [exp.roof_nodes[o] for o in owners - exp.D0 - exp.anchors - exp.core_ids]
                if owners
                else [exp.ground_nodes[int(exp.L.cell_id[j])]]
            )
            for item in items:
                enlarged = group | item
                a, n = exp.cap(enlarged)
                if enlarged != group and a <= A and n <= N:
                    possible.add(frozenset(enlarged - group))
        binary += len(possible)
        # Grow the power set by induction, independent of the producer's combination loops.
        selections = {group}
        for item in possible:
            selections |= {g | item for g in selections}
        feasible = []
        for g in selections:
            a, n = exp.cap(g)
            if a <= A and n <= N:
                feasible.append(g)
                entirely.update(g)
        families.append(feasible)
    requirements = graph(entirely)
    requested = {}
    for g in set(itertools.chain.from_iterable(families)):
        outside = requirements(g)
        requested[g] = (
            {o for i in outside for o in exp.L.owners[i]},
            {int(exp.L.cell_id[i]) for i in outside if not exp.L.owners[i]},
        )
    roof_price_cache = {}
    winners = set()
    distinct_costs = set()
    least = None
    valid = 0
    for member in itertools.product(*families):
        roofs = frozenset(exp.D0.union(*(requested[g][0] for g in member)))
        ground = exp.G0.union(*(requested[g][1] for g in member))
        if roofs & (exp.anchors | exp.core_ids) or not roofs <= set(exp.inv.candidate_ids):
            continue
        if any(
            (bool(exp.L.owners[i]) and set(exp.L.owners[i]) <= roofs)
            or (not exp.L.owners[i] and int(exp.L.cell_id[i]) in ground)
            for i in set.union(*(set(g) for g in member))
        ):
            continue
        valid += 1
        if roofs not in roof_price_cache:
            roof_price_cache[roofs] = sum(
                Fraction.from_float(v)
                for names, parts in exp.roof_cost.items()
                if set(names) <= roofs
                for v in parts
            )
        cost = roof_price_cache[roofs] + 25 * len(ground)
        distinct_costs.add(cost)
        if least is None or cost < least:
            least, winners = cost, set()
        if cost == least:
            winners.add(
                content_hash(
                    dict(demolished_ids=sorted(roofs), cleared_physical_cell_ids=sorted(ground))
                )
            )
    ordered = sorted(distinct_costs)
    verify(
        2**binary == proposal["candidate_binary_selections"]
        and math.prod(map(len, families))
        == proposal["cap_admissible_distinct_compartment_combinations"]
        and valid == proposal["feasible_joint_compartment_combinations"]
        and sorted(winners) == proposal["canonical_tied_placements_sha256"]
        and abs(float(least) - proposal["minimum_C_m2"]) < 1e-7,
        "independent enumeration differs from the proposed minimum",
    )
    return dict(
        source_receipt_sha256=file_sha256(path),
        binary_action_choices=2**binary,
        feasible_four_family_combinations=valid,
        distinct_integer_ground_and_original_roof_union_costs=len(ordered),
        minimum_C_m2=float(least),
        next_larger_C_m2=float(ordered[1]) if len(ordered) > 1 else None,
        distinct_exact_tied_placements=len(winners),
        exact_binary_geometry_area_sum=[str(least.numerator), str(least.denominator)],
        proof=(
            "Every admissible initial addition and simultaneous combination was checked "
            "on a rebuilt transfer graph. Roof construction uses exact rational sums of "
            "the original binary64 polygon areas on the metric inventory."
        ),
        new_CA_runs=0,
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    args = cli.parse_args()
    result = verify_minimum(args.source)
    result["receipt_sha256"] = content_hash(result)
    immutable(args.output, encoded(result))
