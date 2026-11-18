"""Enumerate simultaneous choices among every cap-admissible first enclosure addition."""

import argparse
import itertools
import math
from pathlib import Path

from expand_pingyan_fixed_protective_compartments import (
    A_CAP,
    BASIS,
    N_CAP,
    Experiment,
    physical_features,
    verify,
)
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


def combinations(items):
    for k in range(len(items) + 1):
        yield from itertools.combinations(items, k)


def run(output):
    exp = Experiment()
    base = exp.start
    alternatives = [[] for _ in base]
    for groups in exp.options(base):
        indexes = [i for i, (g, b) in enumerate(zip(groups, base, strict=True)) if g != b]
        verify(len(indexes) == 1, "inconsistent initial single addition")
        i = indexes[0]
        alternatives[i].append(groups[i] - base[i])
    catalogues = []
    for i, items in enumerate(alternatives):
        distinct = set()
        for subset in combinations(items):
            g = base[i].union(*subset)
            area, owners = exp.cap(g)
            if area <= A_CAP and owners <= N_CAP:
                distinct.add(g)
        catalogues.append(sorted(distinct, key=lambda g: tuple(sorted(g))))
    total = math.prod(map(len, catalogues))
    checked, feasible = 0, 0
    best, winners = math.inf, {}
    for groups in itertools.product(*catalogues):
        checked += 1
        solution = exp.solution(groups)
        if solution is None:
            continue
        feasible += 1
        cost, roofs, grass = solution
        if cost > best + 1e-8:
            continue
        if cost < best - 1e-8:
            winners = {}
        best = min(best, cost)
        action = dict(demolished_ids=sorted(roofs), cleared_physical_cell_ids=sorted(grass))
        winners[content_hash(action)] = (groups, solution, action)
    verify(checked == total and winners, "enumeration incomplete")
    # Tied treatment hashes select a deterministic representative without using fire outcomes.
    groups, sol, action = winners[min(winners)]
    receipt = exp.independent(sol, groups)
    verify(abs(best - sol[0]) < 1e-8, "representative action misses the attained minimum")
    cost = sol[0]
    action.update(C_m2=cost, plan_id="minimum-among-first-adjacency-enclosure-combinations")
    features, crs = physical_features(Path(exp.cfg["study_path"]), action)
    result = dict(
        schema_version="simultaneous-enclosure-expansion-enumeration-1",
        scope=(
            "All simultaneous combinations of initially cap-admissible one-whole-roof "
            "or one-whole-ground-cell additions to each fixed starting compartment, "
            "with joint construction obligations counted once. Additional layers are "
            "outside this finite design family."
        ),
        original_independent_fires_used=0,
        design_families=4,
        new_CA_runs=0,
        original_C_m2=exp.parent["C_m2"],
        minimum_C_m2=cost,
        saved_vs_original_m2=exp.parent["C_m2"] - cost,
        physical_burned_area_cap_m2=A_CAP,
        original_owner_upper_cap=N_CAP,
        initial_single_extensions=sum(map(len, alternatives)),
        candidate_binary_selections=2 ** sum(map(len, alternatives)),
        cap_admissible_distinct_compartment_combinations=checked,
        feasible_joint_compartment_combinations=feasible,
        tied_minimum_distinct_placements=len(winners),
        canonical_tied_placements_sha256=sorted(winners),
        checked_physical_protection_and_loss=receipt,
        model_action=action,
        source_basis_sha256=file_sha256(BASIS),
        expansion_physics_sha256=file_sha256(
            Path(__file__).with_name("expand_pingyan_fixed_protective_compartments.py")
        ),
        method_sha256=file_sha256(__file__),
    )
    result["receipt_sha256"] = content_hash(result)
    immutable(output, encoded(result))
    gis = dict(
        type="FeatureCollection",
        name="pingyan_zero_heat_enclosure_minimum",
        metric_crs=crs,
        features=features,
        construction_m2=cost,
        certificate_file_sha256=file_sha256(output),
    )
    immutable(output.with_suffix(".geojson"), encoded(gis))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output)
