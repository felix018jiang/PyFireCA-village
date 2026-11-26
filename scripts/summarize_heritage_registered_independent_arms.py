"""Aggregate the three frozen independent treatment arms with strict and conditional denominators."""

import argparse
import itertools
import json
import math
import time

import audit_heritage_registered_family_endpoints as endpoint
import continue_heritage_registered_corridor_arms as corridor
import schedule_heritage_registered_joint_families as dispatch

OUT = (
    dispatch.ROOT
    / "docs/results/heritage-pingyan-independent-three-arm-protection-and-loss-20261111.json"
)
FINAL = dispatch.CONFIG.parent / "final-candidates.json"
SOURCE_AUDIT = (
    dispatch.ROOT
    / "docs/results/heritage-pingyan-independent-three-stage-bounds-20261111.json"
)


def update():
    n = dispatch.frozen_native()
    exp, cfg, rows = dispatch.tables(n)
    final = dispatch.read(FINAL)
    costs = {
        p["plan_id"]: cost
        for p, cost in zip(final["plans"], final["canonical_design_C_m2"], strict=True)
    }
    if dispatch.digest(FINAL) != cfg["final_candidates_sha256"]:
        raise ValueError("final candidates changed")
    observations, evidence, actual_charged = {}, {}, []
    admitted, stopped_plans = set(), set()
    for i, (plan, scenario) in enumerate(rows, 1):
        area = dispatch.AREA if i <= 8 else corridor.AREA
        home = (
            dispatch.RUN
            if i == 1
            else dispatch.child_home(i) if i <= 8 else corridor.folder(i)
        )
        budget = home / "execution-budget.json"
        if i == 1 and (dispatch.AREA / "resident-budget-before-merge.json").exists():
            budget = dispatch.AREA / "resident-budget-before-merge.json"
        if budget.exists():
            admitted.add(i)
        if budget.exists() and dispatch.read(budget)["started_checks"] > 0:
            actual_charged.append(i)
        folder = n.result_folder(home, plan, scenario)
        manifest = folder / "complete.json"
        if not manifest.exists():
            continue
        receipt = area / f"verified-endpoint-{i:02d}.json"
        if not receipt.exists():
            dispatch.save(receipt, endpoint.audit(i))
        result = dispatch.read(receipt)
        if (
            result["native_manifest_sha256"] != dispatch.digest(manifest)
            or result["native_problem_sha256"] != dispatch.PROBLEM
        ):
            raise ValueError(
                "previously verified endpoint no longer matches the native archive"
            )
        # Split the same audited covered-area metric. No settlement polygon is
        # imposed on the full-domain grass/fuel footprint by the original experiment.
        loss_path = folder / "fire-loss.json"
        if (
            dispatch.digest(loss_path)
            != n.read_json(manifest)["files_sha256"][loss_path.name]
        ):
            raise ValueError("native roof/ground loss decomposition changed")
        physical = n.read_json(loss_path)["physical"]
        if result["burned_physical_m2"] is not None:
            roof = physical["roof"]["cumulative_burned_out_covered_area_m2"]
            ground = physical["nonroof"]["cumulative_burned_out_covered_area_m2"]
            if not math.isclose(
                roof + ground, result["burned_physical_m2"], rel_tol=1e-10, abs_tol=1e-7
            ):
                raise ValueError(
                    "the full native fire area does not reconcile to roof and nonroof components"
                )
            result = {**result, "burned_roof_physical_m2": roof}
        observations[i] = {
            **result,
            "native_full_domain_baseline_covered_m2": physical["all"][
                "baseline_covered_area_m2"
            ],
        }
        evidence[i] = n.read_json(folder / "check.json")["evidence"]
        if result["strict_status"] == "failed" and result["strict_complete"]:
            wit = n.native_protection_failure_witness(
                home, dispatch.PROBLEM, plan, scenario, terminal_required=True
            )
            if not wit or not cfg["stop_plan_after_protection_failure"]:
                raise ValueError("strict reported stop lacks its original witness")
            stopped_plans.add(plan.plan_id)
    ledger = n.read_execution_skips(dispatch.RUN, exp)
    by_key = {(p.plan_id, s): i for i, (p, s) in enumerate(rows, 1)}
    skipped = (
        []
        if ledger is None
        else [by_key[(x["plan_id"], x["scenario_id"])] for x in ledger["skipped_cases"]]
    )
    after_stop = {
        i
        for i, (p, _) in enumerate(rows, 1)
        if p.plan_id in stopped_plans and i not in admitted
    }
    if not set(skipped) <= after_stop:
        raise ValueError(
            "canonical skip receipt conflicts with native admissions or strict stops"
        )
    arms = []
    for ai, pid in enumerate(cfg["execution_priority_plan_ids"]):
        ordinals = list(range(ai * 8 + 1, ai * 8 + 9))
        hits = {i: observations[i] for i in ordinals if i in observations}
        strict = [
            i
            for i, r in hits.items()
            if r["strict_status"] == "passed" and r["strict_complete"]
        ]
        failed = [
            i
            for i, r in hits.items()
            if r["strict_status"] == "failed" and r["strict_complete"]
        ]
        cond = [
            i
            for i, r in hits.items()
            if r["finite_domain_conditional_status"] == "passed"
            and r["finite_domain_conditional_closed"]
        ]
        completed_families = sum(
            ordinals[j] in strict and ordinals[j + 1] in strict for j in range(0, 8, 2)
        )
        definitive = len(strict) == 8

        def worst(keys):
            if not keys:
                return None
            return {
                "N_burned_original_owner_lower": max(
                    hits[i]["burned_buildings_original_owner_bounds"]["lower"]
                    for i in keys
                ),
                "N_burned_original_owner_upper": max(
                    hits[i]["burned_buildings_original_owner_bounds"]["upper"]
                    for i in keys
                ),
                "A_burned_physical_m2": max(
                    hits[i]["burned_physical_m2"] for i in keys
                ),
            }

        arms.append(
            dict(
                plan=pid,
                canonical_C_m2=costs[pid],
                registered_families=4,
                registered_mesh_cases=8,
                charged_native_slots=sum(i in actual_charged for i in ordinals),
                native_manifests=len(hits),
                strict_pass_count=len(strict),
                strict_protection_failure_count=len(failed),
                strict_unknown_count=sum(
                    r["strict_status"] == "unknown" for r in hits.values()
                ),
                strict_pass_families_at_both_scales=completed_families,
                finite_domain_pass_count=len(cond),
                not_run_due_to_verified_core_failure=sum(
                    i in after_stop for i in ordinals
                ),
                counterexample_stops_already_merged_into_canonical_ledger=sum(
                    i in skipped for i in ordinals
                ),
                strict_arm_status=(
                    "passed" if definitive else "failed" if failed else "unknown"
                ),
                strict_full_arm_worst=worst(strict) if definitive else None,
                observed_strict_pass_subset_max_only=(
                    worst(strict) if not definitive else None
                ),
                finite_domain_full_arm_worst=worst(cond) if len(cond) == 8 else None,
                partial_maxima_are_not_the_full_arm_worst=not definitive,
            )
        )
    n.verify_pairing(
        [{"scenario_id": rows[i - 1][1], "evidence": e} for i, e in evidence.items()]
    )
    frozen_support = cfg["supplemental_geometry_receipt"]
    if (
        str(SOURCE_AUDIT) != frozen_support["path"]
        or dispatch.digest(SOURCE_AUDIT) != frozen_support["sha256"]
    ):
        raise ValueError("the prospectively verified physical footprints changed")
    design = dispatch.read(SOURCE_AUDIT)["rows"]
    if len(design) != 8:
        raise ValueError("eight frozen scale-specific physical support checks required")
    pairs = []
    for a, b in itertools.combinations(range(3), 2):
        equal_support = 0
        matched = []
        ap, bp = (
            cfg["execution_priority_plan_ids"][a],
            cfg["execution_priority_plan_ids"][b],
        )
        for srow in design:
            family, scale = srow["draw_ordinal"], srow["scale_m"]
            pos = 2 * (family - 1) + (1 if scale == 1.0 else 2)
            pair = [a * 8 + pos, b * 8 + pos]
            support = srow["physical_support_by_plan"]
            equal = support[ap]["footprint_sha256"] == support[bp]["footprint_sha256"]
            equal_support += int(equal)
            if not all(i in evidence for i in pair):
                continue
            left, right = [evidence[i] for i in pair]
            if (
                left["source_footprint_sha256"] == right["source_footprint_sha256"]
            ) != equal:
                raise ValueError(
                    "registered support and observed native signatures disagree"
                )
            if equal:
                for key in (
                    "source_id",
                    "source_footprint_sha256",
                    "source_budget",
                    "common_layout_sha256",
                ):
                    if left[key] != right[key]:
                        raise ValueError(
                            "equal-footprint pair has inconsistent physical or mesh support"
                        )
                if all(
                    observations[i]["strict_status"] == "passed"
                    and observations[i]["strict_complete"]
                    for i in pair
                ):
                    l, r = [observations[i] for i in pair]
                    matched.append(
                        dict(
                            family=family,
                            roof_scale_m=scale,
                            difference_second_minus_first_A_m2=r["burned_physical_m2"]
                            - l["burned_physical_m2"],
                            first_N_bounds=l["burned_buildings_original_owner_bounds"],
                            second_N_bounds=r["burned_buildings_original_owner_bounds"],
                        )
                    )
        pairs.append(
            dict(
                plan_ids=[ap, bp],
                same_physical_source_cases=equal_support,
                out_of=8,
                native_verified_strict_paired_observations=matched,
                unequal_physical_source_cases_excluded_from_paired_contrasts=8
                - equal_support,
            )
        )
    report = dict(
        schema="heritage-fixed-independent-three-arm-protection-and-loss-1",
        native_problem_sha256=dispatch.PROBLEM,
        original_native_experiment_sha256=dispatch.digest(
            dispatch.RUN / "experiment.json"
        ),
        final_preunblind_candidate_sha256=dispatch.digest(FINAL),
        total_original_slots=24,
        charged_native_ordinals=actual_charged,
        completed_native_ordinals=sorted(observations),
        strict_failure_skip_ordinals=sorted(after_stop),
        canonical_native_skip_receipts_merged=sorted(skipped),
        separate_candidate_arms=arms,
        physical_source_pairing=pairs,
        native_protection_and_loss_by_registered_ordinal={
            i: {
                k: v
                for k, v in r.items()
                if k not in {"registered_joint_ordinal", "registered_campaign_ordinal"}
            }
            for i, r in observations.items()
        },
        area_scope="Cumulative fuel-covered area burned out in the original simulated domain, split into roof and nonroof; no surveyed village-border clipping in this metric.",
        registered_after_optimizing_design=True,
        later_design_candidates_included=False,
        validation_refers_to_four_sampled_families_and_two_meshes=True,
        global_best_claim=False,
    )
    if not OUT.exists() or dispatch.read(OUT) != json.loads(json.dumps(report)):
        dispatch.save(OUT, report)
    return len(skipped) + len(observations) == 24


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    while True:
        done = update()
        if done or not args.watch:
            break
        time.sleep(40)
