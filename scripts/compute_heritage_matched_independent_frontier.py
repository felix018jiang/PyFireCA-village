"""Evaluate the strict held-out frontier on registered families and identical physical fires."""

import json
import math
from pathlib import Path

import schedule_heritage_registered_joint_families as s

MAP_NAMES = (
    "pingyan-combined-clearance.geojson",
    "pingyan-local-2.5m.geojson",
    "pingyan-local-15.01m.geojson",
)
PAIR_FIELDS = (
    "source_id",
    "source_footprint_sha256",
    "source_budget",
    "common_layout_sha256",
    "frozen_integrated_node_mask_sha256",
)


def maps_for(original):
    bound = {}
    for plan, cost, name in zip(
        original["plans"], original["canonical_design_C_m2"], MAP_NAMES, strict=True
    ):
        p = s.ROOT / "output/heritage-firebreak-actions-20261111" / name
        geo = s.read(p)
        members = {
            feature["properties"]["material"]: feature["properties"]["action_ids"]
            for feature in geo["features"]
        }
        if (
            set(members) != {"roof_union", "whole_ground_cells"}
            or set(members["roof_union"]) != set(plan["demolished_ids"])
            or set(members["whole_ground_cells"]) != set(plan["cleared_physical_cell_ids"])
            or not math.isclose(geo["construction_m2"], cost, rel_tol=0, abs_tol=1e-6)
        ):
            raise ValueError("defensive map resources disagree with the frozen physical actions")
        bound[plan["plan_id"]] = dict(
            workspace_path=str(p.relative_to(s.ROOT)),
            sha256=s.digest(p),
            canonical_C_m2=cost,
            roof_actions=len(members["roof_union"]),
            ground_actions=len(members["whole_ground_cells"]),
            scope=(
                "protective clearance only; outcomes in the final receipt supersede "
                "design-set outcomes in map metadata"
            ),
        )
    return bound


def _describe(cases):
    if not cases:
        raise ValueError("one or more complete, eligible scenarios are required")
    max_total = max(r["A_total_m2"] for r in cases)
    return dict(
        number_of_mesh_observations=len(cases),
        families=sorted({r["family"] for r in cases}),
        max_total_burned_A_m2=max_total,
        observed_protected_ignitions_across_eligible_cases=sum(
            r["protected_ignitions"] for r in cases
        ),
        maximum_observed_protected_surface_temperature_K=max(
            r["peak_protected_surface_K"] for r in cases
        ),
        worst_burned_original_building_owner_bounds={
            "lower": max(r["N_original_owner_bounds"]["lower"] for r in cases),
            "upper": max(r["N_original_owner_bounds"]["upper"] for r in cases),
        },
        max_roof_burned_m2=max(r["A_roof_m2"] for r in cases),
        max_nonroof_burned_m2=max(r["A_nonroof_m2"] for r in cases),
        native_cases_attaining_exact_max_total=[
            {
                k: r[k]
                for k in (
                    "family",
                    "roof_grid_m",
                    "A_total_m2",
                    "A_roof_m2",
                    "A_nonroof_m2",
                    "N_original_owner_bounds",
                )
            }
            for r in cases
            if r["A_total_m2"] == max_total
        ],
        cases=cases,
    )


def reduce_front(eligible, costs, observations, ordinals):
    if not eligible or not ordinals:
        return dict(
            eligible=list(eligible),
            observed_mesh_cases=list(ordinals),
            results={},
            exact_non_dominated_plan_ids=[],
            comparison_possible=False,
        )
    output = {}
    for plan in eligible:
        r = _describe([observations[plan][i] for i in ordinals])
        r["C_m2"] = costs[plan]
        r["objective_vector_C_maxA_maxNupper"] = [
            r["C_m2"],
            r["max_total_burned_A_m2"],
            r["worst_burned_original_building_owner_bounds"]["upper"],
        ]
        output[plan] = r
    for row in output.values():
        target = row["objective_vector_C_maxA_maxNupper"]
        row["strictly_dominated_by"] = []
        for q, other in output.items():
            x = other["objective_vector_C_maxA_maxNupper"]
            if all(value <= limit for value, limit in zip(x, target, strict=True)) and any(
                value < limit for value, limit in zip(x, target, strict=True)
            ):
                row["strictly_dominated_by"].append(q)
    return dict(
        eligible=list(eligible),
        observed_mesh_cases=list(ordinals),
        results=output,
        exact_non_dominated_plan_ids=[
            p for p in eligible if not output[p]["strictly_dominated_by"]
        ],
        comparison_possible=len(eligible) > 1,
    )


def generate(strict, done):
    if (
        strict["temporal_mode"] != "strict"
        or strict["native_comparison_problem_sha256"] != s.PROBLEM
    ):
        raise ValueError("common-source comparison requires the unmodified strict physical problem")
    exp = s.read(s.RUN / "experiment.json")
    cfg = exp["config"]
    original = s.read(s.CONFIG.parent / "final-candidates.json")
    if s.digest(s.CONFIG.parent / "final-candidates.json") != cfg["final_candidates_sha256"]:
        raise ValueError("final candidate declaration changed")
    maps = maps_for(original)
    feasible = {p["plan_id"]: p for p in strict["archive"]["fully_evaluated_feasible_plans"]}
    eligible = [pid for pid in cfg["execution_priority_plan_ids"] if pid in feasible]
    costs = {pid: feasible[pid]["cleared_area_m2"] for pid in eligible}
    all_cases = list(range(1, 9))
    observations = {}
    signatures = {}
    for pid in eligible:
        declared = [
            p["scenario_id"] for p in cfg["execution_case_selection"] if p["plan_id"] == pid
        ]
        if len(set(declared)) != 8 or len(declared) != 8:
            raise ValueError(
                "registered strict plan has other than eight distinct mesh observations"
            )
        observations[pid] = {}
        signatures[pid] = {}
        for i, sc in enumerate(declared, 1):
            checks = [
                r for r in strict["native_checks"] if r["plan_id"] == pid and r["scenario_id"] == sc
            ]
            if (
                len(checks) != 1
                or checks[0]["status"] != "passed"
                or not checks[0]["terminal_complete"]
            ):
                raise ValueError("unverified or incomplete plan in the full-arm eligible set")
            ref = checks[0]
            manifest = Path(ref["manifest"])
            if s.digest(manifest) != ref["manifest_sha256"]:
                raise ValueError("archived native witness changed")
            m = s.read(manifest)
            base = manifest.parent
            if m["problem_sha256"] != s.PROBLEM or m["plan_id"] != pid or m["scenario_id"] != sc:
                raise ValueError("mixed native identity")
            for name in ("check.json", "fire-loss.json"):
                if s.digest(base / name) != m["files_sha256"][name]:
                    raise ValueError("native physical support or loss decomposition changed")
            ev = s.read(base / "check.json")["evidence"]
            loss = s.read(base / "fire-loss.json")
            phys = loss["physical"]
            a = {
                k: phys[k]["cumulative_burned_out_covered_area_m2"]
                for k in ("all", "roof", "nonroof")
            }
            count = loss["physical_remaining_roof_annotation_counts"][
                "burned_out_annotation_attribution_count"
            ]
            checked = feasible[pid]["scenario_observations"][sc]
            if (
                a["all"] != checked["A_burned_out_m2"]
                or count != checked["N_burned_out"]
                or not math.isclose(a["roof"] + a["nonroof"], a["all"], rel_tol=1e-10, abs_tol=1e-7)
            ):
                raise ValueError(
                    "physical area or owner bounds differ from strict native postprocessing"
                )
            if abs(ev["source_budget"]["covered_area_m2"] - 25) > 1e-5:
                raise ValueError("actual ignition support differs from the frozen 25 m2 patch")
            family = (i + 1) // 2
            scale = 1.0 if i % 2 else 0.5
            if not sc.endswith(f"-h{scale:g}"):
                raise ValueError("wrong native mesh ordering")
            core = ev["core_outcomes"]
            if len(core) != len(original["core_ids"]) or any(node["ignited"] for node in core):
                raise ValueError(
                    "strict full-arm eligibility conflicts with the protected surfaces"
                )
            observations[pid][i] = dict(
                protected_ignitions=sum(node["ignited"] for node in core),
                peak_protected_surface_K=max(
                    node["maximum_valid_surface_temperature_K"] for node in core
                ),
                family=family,
                roof_grid_m=scale,
                N_original_owner_bounds=count,
                A_total_m2=a["all"],
                A_roof_m2=a["roof"],
                A_nonroof_m2=a["nonroof"],
                native_manifest_sha256=ref["manifest_sha256"],
            )
            signatures[pid][i] = json.dumps(
                [ev[k] for k in PAIR_FIELDS],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
    full = reduce_front(eligible, costs, observations, all_cases)
    if set(full["exact_non_dominated_plan_ids"]) != {
        p["plan_id"] for p in strict["archive"]["non_dominated_archive"]
    }:
        raise ValueError("independent exact frontier does not match frozen strict postprocessing")
    support_ref = cfg["supplemental_geometry_receipt"]
    support = Path(support_ref["path"])
    if s.digest(support) != support_ref["sha256"]:
        raise ValueError("frozen four-family physical support descriptor changed")
    eligible_or_all_common = []
    original_arms = cfg["execution_priority_plan_ids"]
    for row in s.read(support)["rows"]:
        entries = row["physical_support_by_plan"]
        physical = {
            json.dumps(
                [entries[pid]["footprint_sha256"], entries[pid]["native_budget"]],
                sort_keys=True,
                allow_nan=False,
            )
            for pid in original_arms
        }
        if len(physical) == 1:
            eligible_or_all_common.append(
                2 * (row["draw_ordinal"] - 1) + (1 if row["scale_m"] == 1.0 else 2)
            )
    eligible_or_all_common = sorted(eligible_or_all_common)
    if any(
        (i in eligible_or_all_common) != ((i + 1) in eligible_or_all_common) for i in (1, 3, 5, 7)
    ):
        raise ValueError("registered common physical footprint changed between mesh scales")
    common = []
    if len(eligible) >= 2:
        for family in range(1, 5):
            coarse, fine = 2 * family - 1, 2 * family
            match = [len({signatures[p][i] for p in eligible}) == 1 for i in (coarse, fine)]
            if match[0] != match[1]:
                raise ValueError("a declared family lost common physical support across scales")
            if all(match):
                common.extend((coarse, fine))
    if len(eligible) == 1:
        # A lone eligible plan has no cross-plan contrast. Retain meaningful N/A
        # on the actual patches shared by all three original frozen actions.
        common = eligible_or_all_common
    if len(eligible) > 1 and not set(eligible_or_all_common) <= set(common):
        raise ValueError("native verification disagrees with the original common physical support")
    matched = reduce_front(eligible, costs, observations, common)
    matched["original_preverified_all_three_common_mesh_ordinals"] = eligible_or_all_common
    matched["common_set_basis"] = (
        "fully eligible plans' verified native supports"
        if len(eligible) > 1
        else "frozen common physical support across original three; no cross-plan comparison"
    )
    raw_skips = s.RUN / "execution-skips.json"
    stop = s.read(raw_skips) if raw_skips.exists() else {"skipped_cases": []}
    positions = {
        (p["plan_id"], sc): idx
        for p in original["plans"]
        for idx, sc in enumerate(
            [
                v["scenario_id"]
                for v in cfg["execution_case_selection"]
                if v["plan_id"] == p["plan_id"]
            ],
            1,
        )
    }
    reasons = {}
    for e in stop["skipped_cases"]:
        p = e["plan_id"]
        i = positions[(p, e["scenario_id"])]
        reasons.setdefault(p, []).append(
            dict(
                family=(i + 1) // 2,
                roof_grid_m=1 if i % 2 else 0.5,
                reason=e["reason"],
                witness_native_manifest_sha256=e["failure_witness"]["manifest_sha256"],
            )
        )
    if sum(map(len, reasons.values())) != done["skipped_by_verified_core_failure"]:
        raise ValueError("an original skipped slot lost its native strict witness")
    return dict(
        original_question=(
            "4 frozen new families x 2 roof refinements = 8 proposed native observations per "
            "arm. Each arm requires eight strict passes for either frontier. An original "
            "verified strict core failure stops unissued members of its own arm."
        ),
        exact_minimization_axes=[
            "C_m2",
            "max_A_total_burned_m2",
            "max_N_original_owner_upper",
        ],
        dominance_comparison_tolerance=0,
        full_policy=(
            "Per-plan worst losses across all four retained-source-policy families. Actual "
            "physical patches can differ by plan in the same family."
        ),
        same_source_policy=(
            "Use only families whose actual retained source, footprint, heat/fuel/area "
            "budget, common geometry and frozen integration membership coincide for every "
            "fully qualified candidate, at both scales."
        ),
        scope_of_area=(
            "Full model-domain covered fuel. Maxima of roof and nonroof can occur in "
            "different observations; the exact maximum-total cases list their own matched "
            "components."
        ),
        all_strict_original_families=full,
        common_actual_physical_source_families=matched,
        archived_native_skip_causes=reasons,
        protective_action_maps=maps,
        aggregation_method_sha256=s.digest(Path(__file__)),
        direct_ignition_coordinates_or_building_identifiers_disclosed=False,
        additional_CA_starts=0,
    )
