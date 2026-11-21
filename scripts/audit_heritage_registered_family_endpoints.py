"""Verify individual independent Pingyan family endpoints and export protection/loss only."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import continue_heritage_registered_corridor_arms as corridor
import schedule_heritage_registered_joint_families as dispatch


def audit(slot):
    reg = dispatch.verify_registration()
    if slot not in range(1, 25):
        raise ValueError("slot lies outside the original campaign")
    if slot in reg["joint_ordinals"]:
        owner = dispatch.RUN if slot == 1 else dispatch.child_home(slot)
    else:
        corridor.verify()
        owner = corridor.folder(slot)
    within_arm = (slot - 1) % 8 + 1
    n = dispatch.frozen_native()
    _, _, rows = dispatch.tables(n)
    plan, scenario = rows[slot - 1]
    folder = n.result_folder(owner, plan, scenario)
    if not (folder / "complete.json").is_file():
        raise RuntimeError("the requested registered observation is still pending")
    with tempfile.TemporaryDirectory() as t:
        report_path = Path(t) / "report.json"
        subprocess.run(
            [
                sys.executable,
                str(dispatch.ROOT / "scripts/summarize_heritage_joint_pareto.py"),
                "--run-root",
                str(owner),
                "--config",
                str(dispatch.CONFIG),
                "--output",
                str(report_path),
                "--temporal-mode",
                "finite_domain_closed",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        report = json.loads(report_path.read_text())

    def matched(record):
        return record["plan_id"] == plan.plan_id and record["scenario_id"] == scenario

    raw = next(x for x in report["native_checks"] if matched(x))
    cond = next(x for x in report["finite_domain_checks"] if matched(x))
    terminal = n.read_json(folder / "check.json")["evidence"]["terminal_verification"]
    cores = n.read_json(folder / "check.json")["evidence"]["core_outcomes"]
    if (
        raw["manifest_sha256"] != dispatch.digest(folder / "complete.json")
        or report["native_comparison_problem_sha256"] != dispatch.PROBLEM
    ):
        raise ValueError("independent postprocessing failed to bind the native state")
    if raw["status"] in ("passed", "failed") and not raw["terminal_complete"]:
        raise ValueError("decisive native status needs strict terminal verification")
    resolved = raw["terminal_complete"] or cond["finite_domain_closed"]
    metrics = terminal.get("metrics") or {}
    loss = n.read_json(folder / "fire-loss.json")
    count = loss["physical_remaining_roof_annotation_counts"][
        "burned_out_annotation_attribution_count"
    ]
    area = loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
    if resolved and (
        "burned_out_physical_covered_area_m2" in metrics
        and metrics["burned_out_physical_covered_area_m2"] != area
    ):
        raise ValueError("endpoint loss differs from burned physical area")
    result = dict(
        registered_joint_ordinal=slot if slot <= 8 else None,
        registered_family=(within_arm + 1) // 2,
        roof_scale_m=1.0 if slot % 2 else 0.5,
        native_problem_sha256=dispatch.PROBLEM,
        registered_plan=plan.plan_id,
        canonical_action_m2=next(
            row["canonical_C_m2"]
            for row in report["canonical_construction_area_ledger"]
            if row["plan_id"] == plan.plan_id
        ),
        strict_status=raw["status"],
        strict_complete=raw["terminal_complete"],
        strict_invalid_reasons=raw["invalid_reasons"],
        finite_domain_conditional_status=cond["conditional_status"],
        finite_domain_conditional_closed=cond["finite_domain_closed"],
        terminal_model_time_s=raw["actual_stop_time_s"],
        native_manifest_sha256=raw["manifest_sha256"],
        source_and_model_manifest_sha256=report["experiment_sha256"],
        protected_buildings=len(cores),
        protected_ignited=sum(row["ignited"] for row in cores),
        max_protected_surface_temperature_K=max(
            row["maximum_valid_surface_temperature_K"] for row in cores
        ),
        burned_buildings_original_owner_bounds=count if resolved else None,
        burned_physical_m2=area if resolved else None,
        burned_nonroof_physical_m2=(
            loss["physical"]["nonroof"]["cumulative_burned_out_covered_area_m2"]
            if resolved
            else None
        ),
        unresolved_endpoint_losses_reported=False,
        global_or_plans_complete_claim=False,
    )
    if slot > 8:
        result.update(registered_campaign_ordinal=slot, ordinal_within_frozen_arm=within_arm)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slot", required=True, type=int)
    args = p.parse_args()
    out = (
        dispatch.AREA if args.slot <= 8 else corridor.AREA
    ) / f"verified-endpoint-{args.slot:02d}.json"
    result = audit(args.slot)
    if out.exists() and dispatch.read(out) != result:
        raise RuntimeError("a previously audited endpoint changed")
    dispatch.save(out, result)
    print(json.dumps(result, ensure_ascii=False))
