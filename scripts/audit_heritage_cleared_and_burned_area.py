"""Compare clearance and burnout on the same original material basis."""

import math
from pathlib import Path

from audit_heritage_core_exposure import RESULTS, ROOT, RUNS, sources
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def physical_areas(manifest):
    files = read_json(manifest)["files_sha256"]
    for name in ("check.json", "fire-loss.json"):
        if file_sha256(manifest.parent / name) != files[name]:
            raise ValueError("Native evidence digest disagrees with terminal manifest")
    check = read_json(manifest.parent / "check.json")
    evidence = check["evidence"]
    if (
        check["status"] != "passed"
        or evidence["invalid_reasons"]
        or not evidence["terminal_verification"]["terminal_complete"]
    ):
        raise ValueError("Strict terminal observation required")
    physical = read_json(manifest.parent / "fire-loss.json")["physical"]
    areas = {}
    for group in ("all", "roof", "nonroof"):
        record = physical[group]
        cleared = record["cleared_covered_area_m2"]
        burned = record["cumulative_burned_out_covered_area_m2"]
        combined = record["clearing_plus_burned_out_union_area_m2"]
        if not math.isclose(cleared + burned, combined, rel_tol=1e-12, abs_tol=1e-7):
            raise ValueError("Construction and burnout overlap on original fuel patches")
        areas[group] = dict(cleared_m2=cleared, burned_m2=burned, union_m2=combined)
    for k, value in areas["all"].items():
        if not math.isclose(areas["roof"][k] + areas["nonroof"][k], value, abs_tol=1e-7):
            raise ValueError("Roof and ground do not account for the entire fuel area")
    return areas


def summarize(manifests):
    observations = [physical_areas(p) for p in manifests]
    if not observations:
        raise ValueError("Missing observations")
    return dict(
        count=len(observations),
        max_within_set_m2={
            group: {
                k: max(row[group][k] for row in observations)
                for k in ("cleared_m2", "burned_m2", "union_m2")
            }
            for group in ("all", "roof", "nonroof")
        },
        terminal_manifest_sha256=sorted(file_sha256(p) for p in manifests),
    )


def audit():
    refs, cohorts = sources()
    rows = []
    for village, plan, cohort, dirs, hashes in cohorts:
        available = {file_sha256(p): p for d in dirs for p in d.glob("evaluations/*/complete.json")}
        rows.append(
            dict(
                village=village,
                plan=plan,
                cohort=cohort,
                **summarize([available[h] for h in hashes]),
            )
        )

    matched = RUNS / "heritage-pingyan-eight-design-matched-two-1m-native-20261111"
    newer = list(matched.glob("evaluations/*/complete.json"))
    if len(newer) != 2:
        raise ValueError("Both matched replications must finish")
    registered = (
        RESULTS / "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json"
    )
    previous = RUNS / "heritage-pingyan-independent-three-stage-supplement-20261111"
    ids = {read_json(p)["scenario_id"] for p in newer}
    earlier = []
    for p in previous.glob("**/evaluations/*/complete.json"):
        m = read_json(p)
        if m["plan_id"] == "fixed-core-and-first-escape-compartment" and m["scenario_id"] in ids:
            earlier.append(p)
    if (
        len(earlier) != 2
        or content_hash(sorted(map(file_sha256, earlier)))
        != read_json(registered)["matched_manifest_commitment"]
    ):
        raise ValueError("Matched baseline differs from the original same-source commitment")
    refs[registered.name] = file_sha256(registered)
    for plan, cases in (("four-family-joint", earlier), ("eight-known-zero-heat", newer)):
        rows.append(
            dict(village="pingyan", plan=plan, cohort="two_exactly_matched_1m", **summarize(cases))
        )
    return dict(
        measurement=(
            "Fuel-bearing original physical patch area removed by clearance or exhausted by fire. "
            "For each group the maximum is taken over that cohort. This supplementary measure "
            "does not change the original objective weights."
        ),
        rows=rows,
        source_receipts_sha256=refs,
        method_sha256=file_sha256(Path(__file__)),
        new_simulations=0,
    )


if __name__ == "__main__":
    result = audit()
    result["receipt_sha256"] = content_hash(result)
    immutable(
        ROOT / "docs/results/heritage-cleared-and-burned-union-area-20261111.json", encoded(result)
    )
