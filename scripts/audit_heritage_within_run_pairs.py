"""Compare two claimed protective policies on a single audited common-mesh campaign."""

import argparse
import math
from pathlib import Path

import numpy as np
from prepare_heritage_joint_longcap import encoded, immutable
from summarize_heritage_joint_pareto import file_sha256, read_json, verify_header


def checked(path, digest):
    if file_sha256(path) != digest:
        raise ValueError(f"native artifact changed: {path.name}")
    return read_json(path)


def observe(row, conditional):
    path = Path(row["manifest"])
    manifest = checked(path, row["manifest_sha256"])
    files = manifest["files_sha256"]
    evidence = checked(path.parent / "check.json", files["check.json"])["evidence"]
    loss = checked(path.parent / "fire-loss.json", files["fire-loss.json"])
    counts = loss["physical_remaining_roof_annotation_counts"]
    area = loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
    roof_area = loss["physical"]["roof"]["cumulative_burned_out_covered_area_m2"]
    nonroof_area = loss["physical"]["nonroof"]["cumulative_burned_out_covered_area_m2"]
    if not math.isclose(area, row["metrics"]["burned_out_physical_covered_area_m2"], abs_tol=1e-8):
        raise ValueError("saved whole-domain area differs from native certificate observations")
    if not math.isclose(area, roof_area + nonroof_area, rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError("saved roof and nonroof burnout areas do not sum to the modeled domain")
    strict = (
        row["status"] == "passed"
        and row["terminal_complete"]
        and row["core_ignition_observed"] is False
        and row["certificate_observation_bound"]
        and not row["invalid_reasons"]
    )
    finite = None
    if conditional is not None:
        if (
            conditional["native_strict_status"] != row["status"]
            or conditional["native_invalid_reasons"] != row["invalid_reasons"]
        ):
            raise ValueError("finite-domain audit does not bind this strict case")
        finite = (
            conditional["conditional_status"] == "passed" and conditional["finite_domain_closed"]
        )
    return {
        "strict": strict,
        "finite_domain": finite,
        "raw_status": row["status"],
        "raw_invalid_reasons": row["invalid_reasons"],
        "source": evidence["source_id"],
        "footprint": evidence["source_footprint_sha256"],
        "budget": evidence["source_budget"],
        "mesh": evidence["common_layout_sha256"],
        "membership": evidence["frozen_integrated_node_mask_sha256"],
        "N": counts["burned_out_annotation_attribution_count"],
        "unrepresented": sorted(counts["unrepresented_original_ids"]),
        "A": area,
        "A_roof": roof_area,
        "A_nonroof": nonroof_area,
        "manifest_sha256": row["manifest_sha256"],
    }


def audit(strict_path, conditional_path, plans):
    strict = read_json(strict_path)
    run = Path(strict["run_root"])
    experiment = verify_header(run, Path(strict["config_path"]))
    cfg = experiment["config"]
    if (
        strict["temporal_mode"] != "strict"
        or strict["experiment_sha256"] != file_sha256(run / "experiment.json")
        or not strict["native_saved_state_and_peak_certificates_verified"]
        or len(set(plans)) != 2
        or not set(plans) <= {p["plan_id"] for p in cfg["plans"]}
    ):
        raise ValueError("two distinct real plans on the declared strictly audited run required")
    finite = {}
    if conditional_path is not None:
        cond = read_json(conditional_path)
        if (
            cond["temporal_mode"] != "finite_domain_closed"
            or cond["experiment_sha256"] != strict["experiment_sha256"]
            or not cond["native_saved_state_and_peak_certificates_verified"]
        ):
            raise ValueError("invalid finite-domain evidence or different native campaign")
        finite = {(c["plan_id"], c["scenario_id"]): c for c in cond["finite_domain_checks"]}
    observations = {}
    for row in strict["native_checks"]:
        if row["plan_id"] not in plans:
            continue
        key = row["plan_id"], row["scenario_id"]
        if key in observations:
            raise ValueError("duplicate native plan-scenario record")
        observations[key] = observe(row, finite.get(key))
    costs = {
        p["plan_id"]: p["canonical_C_m2"] for p in strict["canonical_construction_area_ledger"]
    }
    pairs = []
    for index, fire in enumerate(cfg["source_fires"], 1):
        for scale in cfg["roof_scales_m"]:
            case = f"{fire['fire_id']}-h{scale:g}"
            pair = [observations.get((p, case)) for p in plans]
            out = {"draw_index": index, "scale_m": scale}
            if any(p is None for p in pair):
                pairs.append({**out, "status": "incomplete"})
                continue
            left, right = pair
            if (
                left["mesh"] != right["mesh"]
                or left["membership"] != right["membership"]
                or left["unrepresented"] != right["unrepresented"]
            ):
                raise ValueError("comparison lacks common mesh, operator membership or owner scope")
            same_source = (left["source"], left["footprint"]) == (
                right["source"],
                right["footprint"],
            )
            if same_source and (
                left["budget"].keys() != right["budget"].keys()
                or not all(
                    np.isclose(v, right["budget"][k], rtol=1e-9, atol=1e-7)
                    for k, v in left["budget"].items()
                )
            ):
                raise ValueError("same-source comparison lacks identical physical support")
            scope = (
                "strict"
                if all(p["strict"] for p in pair)
                else "finite_domain_closed"
                if all(p["finite_domain"] for p in pair)
                else "unresolved_or_infeasible"
            )
            out.update(
                status=scope,
                matched_actual_source_and_footprint=same_source,
                raw_statuses=[p["raw_status"] for p in pair],
                raw_invalid_reasons=[p["raw_invalid_reasons"] for p in pair],
                manifest_sha256s=[p["manifest_sha256"] for p in pair],
            )
            if scope in {"strict", "finite_domain_closed"}:
                a, b = left["N"], right["N"]
                out.update(
                    burned_area_m2=[p["A"] for p in pair],
                    burned_roof_area_m2=[p["A_roof"] for p in pair],
                    burned_nonroof_area_m2=[p["A_nonroof"] for p in pair],
                    burned_original_owner_bounds=[a, b],
                    delta_area_first_minus_second_m2=left["A"] - right["A"],
                    delta_count_conservative_bounds=[
                        a["lower"] - b["upper"],
                        a["upper"] - b["lower"],
                    ],
                    delta_count_endpoint_differences=[
                        a["lower"] - b["lower"],
                        a["upper"] - b["upper"],
                    ],
                )
            pairs.append(out)
    return {
        "schema_version": "protective-within-run-damage-comparison-1",
        "native_experiment_sha256": strict["experiment_sha256"],
        "strict_archive_sha256": file_sha256(strict_path),
        "finite_domain_archive_sha256": file_sha256(conditional_path) if conditional_path else None,
        "plans_in_delta_order": plans,
        "canonical_clearing_m2": [costs[p] for p in plans],
        "per_scenario_comparisons": pairs,
        "effect_scope": (
            "same actual source and footprint gives controlled action contrast on common mesh; "
            "a changed retained source measures the full action-plus-source policy effect"
        ),
        "new_CA_checks": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", type=Path, required=True)
    parser.add_argument("--finite-domain", type=Path)
    parser.add_argument("--plans", nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    immutable(args.output, encoded(audit(args.strict, args.finite_domain, args.plans)))
