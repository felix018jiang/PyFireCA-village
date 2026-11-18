"""Measure entire modeled roof footprints lost jointly to clearance and burnout."""

import argparse
import itertools
from pathlib import Path

import numpy as np
from audit_heritage_core_exposure import sources
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.fire_loss import _COMPONENT_ID
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs/results"


def count(manifest):
    parent = manifest.parent
    hashes = read_json(manifest)["files_sha256"]
    for name in ("layout.npz", "loss-basis.npz", "final-state.npz", "check.json", "fire-loss.json"):
        if file_sha256(parent / name) != hashes[name]:
            raise ValueError("original material or timing evidence changed")
    check = read_json(parent / "check.json")
    if (
        check["status"] != "passed"
        or check["evidence"]["invalid_reasons"]
        or not check["evidence"]["terminal_verification"]["terminal_complete"]
    ):
        raise ValueError("strict terminal case required")
    reports = read_json(parent / "fire-loss.json")["physical_remaining_roof_annotation_counts"]
    if reports["unrepresented_original_ids"]:
        raise ValueError("additional unrepresented roofs would need separate accounting")
    with (
        np.load(parent / "layout.npz") as layout,
        np.load(parent / "loss-basis.npz") as basis,
        np.load(parent / "final-state.npz") as state,
    ):
        original = basis["baseline_covered_area_m2"].ravel()
        retained = basis["post_intervention_covered_area_m2"].ravel()
        burned = np.isfinite(state["burnout_time_s"].ravel())
        if np.any(burned & (retained <= 0)):
            raise ValueError("cleared material counted as burned")
        labels = layout["component_id"]
        cell = layout["physical_cell_id"].ravel()
        before, after_clearing, after_fire, affected = set(), set(), set(), set()
        for i in np.flatnonzero(original > 0):
            match = _COMPONENT_ID.fullmatch(labels[i])
            if match is None or int(match[1]) != cell[i]:
                raise ValueError("original nodes could not be identified")
            if match[2] == "ground":
                continue
            owners = match[2].split("+")
            before.update(owners)
            if retained[i] > 0:
                after_clearing.update(owners)
                (affected if burned[i] else after_fire).update(owners)
        if before - after_clearing != set(reports["fully_cleared_ids"]):
            raise ValueError("reconstructed construction-only losses differ")
        if len(affected) != reports["burned_out_annotation_attribution_count"]["upper"]:
            raise ValueError("original burn-affected footprint count differs")
        return dict(
            whole_cleared=len(before - after_clearing),
            additional_whole_lost_after_fire=len(after_clearing - after_fire),
            total_whole_lost=len(before - after_fire),
        )


def audit():
    refs, cohorts = sources()
    rows = []
    for village, plan, cohort, paths, expected in cohorts:
        observed = {
            file_sha256(p): p for run in paths for p in run.glob("evaluations/*/complete.json")
        }
        q = [count(observed[h]) for h in expected]
        rows.append(
            dict(
                village=village,
                plan=plan,
                cohort=cohort,
                cases=len(q),
                worst_affected_by_clearance_and_fire={k: max(v[k] for v in q) for k in q[0]},
            )
        )
    match_file = (
        RESULTS / "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json"
    )
    matched = read_json(match_file)
    run = ROOT / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
    pool = {}
    for p in run.glob("**/evaluations/*/complete.json"):
        m = read_json(p)
        if m["plan_id"] == "fixed-core-and-first-escape-compartment" and m["scenario_id"].endswith(
            "-h1"
        ):
            pool[file_sha256(p)] = p
    pairs = [
        b
        for b in itertools.combinations(sorted(pool), 2)
        if content_hash(list(b)) == matched["matched_manifest_commitment"]
    ]
    if len(pairs) != 1:
        raise ValueError("unique two matched original cases unavailable")
    old = max(count(pool[h])["total_whole_lost"] for h in pairs[0])
    ret = RESULTS / "heritage-ordinary-roof-retention-two-villages-20261111.json"
    inventory = next(v for v in read_json(ret)["villages"] if v["village"] == "pingyan")
    new = next(r for r in inventory["policies"] if r["policy"] == "zero-heat-known-eight")
    construction = new["fully_physically_removed_original_ordinary_labels"]
    bound = construction + matched["new_matched_analytic_original_N_upper"]
    return dict(
        schema_version="original-roof-footprint-whole-loss-by-construction-or-fire-1",
        basis=(
            "Count an original roof label only when every original positive physical patch "
            "was cleared or burned. Shared patches retain the original annotation membership. "
            "Partial clearance followed by burnout of the remainder is included."
        ),
        cohorts=rows,
        old_matched_cases=2,
        old_measured_worst_entire_original_roofs_lost_including_construction=old,
        new_physically_whole_roofs_removed_by_construction=construction,
        new_matched_worst_entire_roofs_lost_including_construction_upper=bound,
        matched_reduction_in_worst_entire_modeled_roof_footprint_loss_at_least=old - bound,
        bound_logic=(
            "Every additional entire footprint lost after construction belongs to at least "
            "one original roof label intersecting burned material. The matched-case upper "
            "bound on that count can thus be added to the pre-fire full removals."
        ),
        original_measured_new_analytical=True,
        real_building_collapse_measured=False,
        source_receipts_sha256=refs,
        paired_file_sha256=file_sha256(match_file),
        retention_file_sha256=file_sha256(ret),
        new_CA_runs=0,
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    args = cli.parse_args()
    result = audit()
    result["receipt_sha256"] = content_hash(result)
    immutable(args.output, encoded(result))
