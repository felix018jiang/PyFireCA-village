"""Audit eight disclosed inputs on four already committed construction placements."""

import itertools
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from check_heritage_registered_roof_compartment import footprint_budget, verify
from enumerate_pingyan_zero_heat_compartments import combinations
from expand_pingyan_fixed_protective_compartments import Experiment, sealed
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs/results"
REG = RES / "heritage-pingyan-eight-known-zero-heat-registration-20261111.json"
EIGHT = RES / "heritage-pingyan-eight-known-zero-heat-and-design-loss-20261111.json"
MIN = RES / "heritage-pingyan-zero-heat-first-extensions-minimum-20261111.json"
MIN2 = RES / "heritage-pingyan-zero-heat-first-extensions-independent-20261111.json"
OLD = (
    ROOT
    / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111/experiment.json"
)
LIMIT = (316.61, 3)
NATIVE_DASHBOARD = (
    "docs/results/heritage-pingyan-independent-three-arm-protection-and-loss-20261111.json"
)


def tied_actions(e, record):
    hashes = set(record["canonical_tied_placements_sha256"])
    options = [[] for _ in e.start]
    for groups in e.options(e.start):
        (k,) = (k for k, g in enumerate(groups) if g != e.start[k])
        options[k].append(groups[k] - e.start[k])
    families = []
    for initial, items in zip(e.start, options, strict=True):
        members = set()
        for sub in combinations(items):
            group = initial.union(*sub)
            a, n = e.cap(group)
            if (
                a <= record["physical_burned_area_cap_m2"]
                and n <= record["original_owner_upper_cap"]
            ):
                members.add(group)
        families.append(members)
    found = {}
    for groups in itertools.product(*families):
        result = e.solution(groups)
        if result is None:
            continue
        cost, roofs, ground = result
        if abs(cost - record["minimum_C_m2"]) > 1e-8:
            continue
        k = content_hash(
            dict(demolished_ids=sorted(roofs), cleared_physical_cell_ids=sorted(ground))
        )
        verify(k in hashes, "unexpected tied layout")
        found[k] = (roofs, ground, cost)
    verify(set(found) == hashes, "committed tied placements not all reproduced")
    return dict(sorted(found.items()))


def close_group(e, live, mask, initial):
    group = frozenset(initial.tolist())
    verify(all(not mask[i] and e.fuel[i] and not e.core[i] for i in group), "invalid start")
    while True:
        a, n = e.cap(group)
        if a > LIMIT[0] or n > LIMIT[1]:
            return dict(
                signed=False,
                area_capacity_exceeded=a > LIMIT[0],
                owner_capacity_exceeded=n > LIMIT[1],
            )
        src = np.array(sorted(group))
        outside = np.setdiff1d(live, src, assume_unique=True)
        dest, _, dose, ct, _ = e.model.pairs(src, outside)
        values = defaultdict(list)
        for i, v in zip(dest, dose, strict=True):
            values[int(i)].append(v)
        forced = set(ct.tolist())
        forced.update(i for i, vs in values.items() if math.fsum(vs) >= 1 - 1e-10)
        check = {i for i in e.requirements(group) if not mask[i]}
        verify(forced == check, "independent first-escape calculation differs")
        if not forced:
            return dict(signed=True, area=a, owners=n)
        group = group.union(forced)


def summary(rows):
    signed = [r for r in rows if r["signed"]]
    return dict(
        families=len(rows),
        matched_eight_design_physical_source_and_budget=sum(r["matched"] for r in rows),
        matched_and_certified=sum(r["matched"] for r in signed),
        substituted_and_certified=sum(not r["matched"] for r in signed),
        certified_on_actual_physical_policy_source=len(signed),
        maximum_certified_A_m2=max((r["area"] for r in signed), default=None),
        maximum_certified_Nupper=max((r["owners"] for r in signed), default=None),
        failed_sufficient_set_capacity=len(rows) - len(signed),
        capacity_beyond_A=sum(r.get("area_capacity_exceeded", False) for r in rows),
        capacity_beyond_N=sum(r.get("owner_capacity_exceeded", False) for r in rows),
    )


def audit():
    registry, eight, mini, independent = map(sealed, (REG, EIGHT, MIN, MIN2))
    for name, digest in registry["inputs_sha256"].items():
        if name != NATIVE_DASHBOARD:
            verify(file_sha256(ROOT / name) == digest, "changed registered physical/cohort input")
    verify(
        eight["request_condition_and_inputs_sha256"] == file_sha256(REG)
        and independent["source_receipt_sha256"] == file_sha256(MIN)
        and independent["distinct_exact_tied_placements"] == 3,
        "wrong cohort or placements",
    )
    e = Experiment()
    actions = tied_actions(e, mini)
    big = eight["model_action"]
    bigD, bigG = set(big["demolished_ids"]), set(big["cleared_physical_cell_ids"])
    bigkey = content_hash(dict(demolished_ids=sorted(bigD), cleared_physical_cell_ids=sorted(bigG)))
    actions[bigkey] = (bigD, bigG, big["C_m2"])
    protocols = [p for p, _, _ in e.families]
    protocols += [f["protocol"] for f in read_json(OLD)["problem"]["fires"].values()]
    verify(len(set(map(content_hash, protocols))) == 8, "expected eight distinct known protocols")

    def realize(protocol, D, G):
        (r,) = realize_sources(protocol, e.inv, demolished_ids=sorted(D))
        fp = r["footprint"]
        b, hits = footprint_budget(e.L, e.geom, e.tree, r["source_id"], fp, D, G)
        return (r["source_id"], fp.sha256, b), hits

    bases = [realize(p, bigD, bigG)[0] for p in protocols]
    verify(
        content_hash(
            [(content_hash(p), fp, b) for p, (_, fp, b) in zip(protocols, bases, strict=True)]
        )
        == eight["source_receipt_aggregate_sha256"],
        "eight realized sources differ",
    )
    results = []
    for key, (roofs, grass, cost) in actions.items():
        verify(roofs.issuperset(e.D0) and grass.issuperset(e.G0), "zero-heat construction lost")
        mask = e.model.mask(roofs, grass)
        verify(not np.any(mask & e.core), "heritage damaged by construction")
        live = np.flatnonzero(e.fuel & ~e.core & ~mask)
        dest, _, _, touch, _ = e.model.pairs(live, np.flatnonzero(e.core))
        verify(not len(dest) and not len(touch), "incoming geometric core clearance fails")
        rows = []
        for protocol, reference in zip(protocols, bases, strict=True):
            actual, hits = realize(protocol, roofs, grass)
            same = actual[:2] == reference[:2] and np.array_equal(actual[2], reference[2])
            rows.append(dict(matched=same, **close_group(e, live, mask, hits)))
        results.append(
            dict(
                frozen_action_sha256=key,
                C_m2=cost,
                original_four=summary(rows[:4]),
                other_disclosed_four=summary(rows[4:]),
                all_eight=summary(rows),
            )
        )
    verify(
        results[-1]["all_eight"]["certified_on_actual_physical_policy_source"] == 8,
        "eight-scenario action did not close",
    )
    return dict(
        schema_version="pingyan-four-frozen-placements-eight-first-escape-coverage-1",
        village="pingyan",
        scope="Eight disclosed design scenarios under the frozen model.",
        fixed_ceiling_A_m2=LIMIT[0],
        fixed_ceiling_original_Nupper=LIMIT[1],
        prescribed_source_area_m2=25,
        source_reference=(
            "Actual ordinary roof, frozen footprint and positive physical fuel/energy "
            "in the eight-scenario design."
        ),
        method=(
            "Start at every parent atom with positive overlap of the whole actual footprint. "
            "Repeatedly include every retained exterior atom with polygon contact or combined "
            "whole-lifetime radiation >= (1-1e-10) of the dry ignition energy. Positive "
            "coefficients make this the least closed set under this screen. Stop as soon "
            "as its conservative area or original label capacity exceeds the target."
        ),
        interpretation=(
            "Each reported A and N is a separate maximum over certified families. Threshold "
            "failure is evidence about the sufficient screen only. Actual losses are not "
            "estimated here. Only the physical inputs of the registration are consulted. Its "
            "separately updated three-arm native-outcomes dashboard is not a physical input."
        ),
        archived_eight_policy_original_four_common_compartment_bound=eight[
            "original_four_in_this_eight_design"
        ],
        placements=results,
        protected_heritage_count=len(e.core_ids),
        all_four_constructions_zero_external_heritage_heat=True,
        new_source_draws=0,
        new_CA_runs=0,
        new_construction_placements=0,
        input_files_sha256={
            str(p.relative_to(ROOT)): file_sha256(p) for p in [REG, EIGHT, MIN, MIN2, OLD]
        },
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    report = audit()
    report["receipt_sha256"] = content_hash(report)
    immutable(
        RES / "heritage-pingyan-four-fixed-placements-eight-coverage-20261111.json", encoded(report)
    )
