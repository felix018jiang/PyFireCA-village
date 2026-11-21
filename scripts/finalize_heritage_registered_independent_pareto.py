"""Publish the two separately audited Pareto readouts after reconciling the original 24 slots."""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import compute_heritage_matched_independent_frontier as matched
import continue_heritage_registered_corridor_arms as corridor
import schedule_heritage_registered_joint_families as dispatch


def calculate():
    receipt_path = corridor.AREA / "complete-registration.json"
    done = dispatch.read(receipt_path)
    if (
        done["original_declared"] != 24
        or done["completed_native"] + done["skipped_by_verified_core_failure"] != 24
        or done["native_summary_sha256"] != dispatch.digest(dispatch.RUN / "summary.json")
    ):
        raise ValueError(
            "every original slot must have a validated native terminal file or an "
            "authenticated strict-failure skip"
        )
    output = {}
    with tempfile.TemporaryDirectory() as tmp:
        for mode in ("strict", "finite_domain_closed"):
            path = Path(tmp) / f"{mode}.json"
            subprocess.run(
                [
                    sys.executable,
                    str(dispatch.ROOT / "scripts/summarize_heritage_joint_pareto.py"),
                    "--run-root",
                    str(dispatch.RUN),
                    "--config",
                    str(dispatch.CONFIG),
                    "--output",
                    str(path),
                    "--temporal-mode",
                    mode,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            full = dispatch.read(path)
            archive = full["archive"]
            if mode == "strict":
                matched_receipt = matched.generate(full, done)
            if (
                full["native_comparison_problem_sha256"] != dispatch.PROBLEM
                or archive["declared_plan_count"] != 3
                or not full["native_saved_state_and_peak_certificates_verified"]
                or full["new_CA_checks"] != 0
            ):
                raise ValueError("final Pareto archive is not the verified original comparison")
            acceptable = []
            for p in archive["fully_evaluated_feasible_plans"]:
                if len(p["scenario_observations"]) != 8:
                    raise ValueError("incomplete candidate entered the complete feasible archive")
                w = p["worst_case"]
                acceptable.append(
                    {
                        "plan_id": p["plan_id"],
                        "C_area_m2": p["cleared_area_m2"],
                        "worst_burned_building_owner_bounds": w["N_burned_out"],
                        "worst_burned_total_m2": w["A_burned_out_m2"],
                        "upper_count_dominated_by": p["upper_bound_dominated_by"],
                        "robustly_dominated_by": p["robustly_dominated_by"],
                        "possibly_dominated_by": p["possibly_dominated_by"],
                    }
                )
            output[mode] = dict(
                native_problem_sha256=full["native_comparison_problem_sha256"],
                analysis_problem_sha256=full["comparison_problem_sha256"],
                complete_feasible=acceptable,
                failed=[p["plan_id"] for p in archive["failed_plans"]],
                unresolved=[p["plan_id"] for p in archive["unresolved_plans"]],
                nondominated_on_cost_upper_count_area=[
                    p["plan_id"] for p in archive["non_dominated_archive"]
                ],
                robust_non_dominated=[
                    p["plan_id"] for p in archive["robust_non_dominated_archive"]
                ],
                possible_dominance_free=[
                    p["plan_id"] for p in archive["possible_dominance_free_archive"]
                ],
                objectives=archive["objective_names"],
                frontier_scope=archive["frontier_scope"],
            )
    return dict(
        schema="heritage-fixed-three-arm-independent-final-pareto-1",
        reconciliation_sha256=dispatch.digest(receipt_path),
        preregistered_candidate_sha256=dispatch.read(dispatch.CONFIG)["final_candidates_sha256"],
        design_data_in_objectives=False,
        independent_draw_families=4,
        roof_scales_per_family=2,
        declared_candidates=3,
        completed_native=done["completed_native"],
        not_run_due_to_strict_core_failure=done["skipped_by_verified_core_failure"],
        charged_native=done["total_global_budget"]["started_checks"],
        geometry_matches_required_for_direct_paired_contrasts=True,
        candidate_comparison_basis=(
            "Same predeclared retained-source policy; per-plan worst losses. Actual patches "
            "can differ between arms."
        ),
        selected_preference_weights=None,
        global_minimum_claim=False,
        stronger_real_site_conclusion=False,
        area_scope=(
            "full model domain; per-case roof and nonroof components in the companion "
            "three-arm aggregate"
        ),
        analyses=output,
        strict_full_and_matched_source_frontiers=matched_receipt,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--await-final", action="store_true")
    args = parser.parse_args()
    receipt = corridor.AREA / "complete-registration.json"
    if args.await_final:
        while not receipt.exists():
            time.sleep(24)
    result = calculate()
    target = (
        dispatch.ROOT
        / "docs/results/heritage-pingyan-independent-confirmatory-pareto-20261111.json"
    )
    if target.exists() and dispatch.read(target) != result:
        raise ValueError("a previous final native report differs")
    dispatch.save(target, result)
    print(
        json.dumps(
            {k: v["nondominated_on_cost_upper_count_area"] for k, v in result["analyses"].items()}
        )
    )
