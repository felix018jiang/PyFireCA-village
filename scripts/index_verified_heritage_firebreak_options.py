"""Index frozen protective actions and aggregate evidence without running a fire model."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs/results"
OUT = ROOT / "output/heritage-firebreak-actions-20261111"
TARGET = OUT / "verified-options.json"
DATE = "-20261111"
LAYERS = ["cleared_roof_geometry", "whole_cell_grass_action"]
WIDTH = "roof-seed-protected-union-ring-b0-w"
COMPACT = "local-band-with-observed-clearance-subset"
JOINT = "fixed-core-and-first-escape-compartment"
D9 = "direct-thin-D9-with-no-grass"


def check(ok, why):
    if not ok:
        raise ValueError(why)


def read(path):
    return json.loads(path.read_text())


def rel(path):
    return os.path.relpath(path, OUT)


def record_path(name, suffix=".json"):
    return RES / ("heritage-" + name + DATE + suffix)


def pick(data, *keys):
    return {k: data[k] for k in keys}


def same_area(left, right):
    check(abs(left - right) < 1e-4, f"physical clearance disagrees: {left}, {right}")


def actions(d):
    return content_hash({k: sorted(d[k]) for k in ("demolished_ids", "cleared_physical_cell_ids")})


def compare_manifest(path, expected, complete_files=False):
    check(file_sha256(path) == expected, f"changed evaluation manifest {path}")
    if complete_files:
        for key, digest in read(path)["files_sha256"].items():
            check(file_sha256(path.parent / key) == digest, f"native evidence changed: {key}")


class Inventory:
    def __init__(self):
        self.options = {}
        self.evidence = {}
        self.data = {}

    def receipt(self, name):
        if name in self.data:
            return self.data[name]
        path = record_path(name)
        d = read(path)
        ev = {"path": rel(path), "file_sha256": file_sha256(path)}
        seal = next((k for k in ("receipt_sha256", "audit_sha256") if k in d), None)
        if seal:
            check(
                content_hash({k: v for k, v in d.items() if k != seal}) == d[seal],
                f"invalid self seal: {name}",
            )
            ev["canonical_content_seal"] = {seal: d[seal]}
        elif "native_checks" in d:
            check(d["native_saved_state_and_peak_certificates_verified"], name)
            completed = [row for row in d["native_checks"] if row["status"] != "not_evaluated"]
            for row in completed:
                compare_manifest(Path(row["manifest"]), row["manifest_sha256"])
            ev["rechecked_native_manifest_count"] = len(completed)
        elif name == "batuan-direct-D9-G0-complete8-audit":
            for row in d["scenario_rows"]:
                check(
                    row["artifact_hashes_verified"]
                    and row["strict_passed"]
                    and row["whole_fire_loss_complete"],
                    name,
                )
                compare_manifest(Path(row["folder"]) / "complete.json", row["manifest_sha256"])
            ev["rechecked_native_manifest_count"] = len(d["scenario_rows"])
        elif name == "pingyan-narrow-independent-strict-counterexample":
            base = (
                ROOT / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
            )
            files = [
                p
                for p in base.glob("evaluations/*/complete.json")
                if file_sha256(p) == d["native_manifest_sha256"]
            ]
            check(
                len(files) == 1 and read(files[0])["problem_sha256"] == d["native_problem_sha256"],
                name,
            )
            compare_manifest(files[0], d["native_manifest_sha256"], complete_files=True)
            check(
                d["strict_extinction_complete"]
                and not d["native_invalid_reasons"]
                and d["protected_ignited"] > 0,
                name,
            )
            ev["rechecked_native_manifest_and_artifacts"] = True
        elif name == "pingyan-independent-confirmatory-pareto":
            base = (
                ROOT / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
            )
            ledger = (
                base
                / "resource-scheduling/registered-corridor-family-shards/complete-registration.json"
            )
            done = read(ledger)
            cand = (
                ROOT
                / "examples/research/heritage/confirmation-20261110"
                / "pingyan-three-fixed-plans-20261111/final-candidates.json"
            )
            check(
                file_sha256(ledger) == d["reconciliation_sha256"]
                and file_sha256(base / "summary.json") == done["native_summary_sha256"],
                name,
            )
            check(file_sha256(cand) == d["preregistered_candidate_sha256"], name)
            check(
                done["original_declared"]
                == d["completed_native"] + d["not_run_due_to_strict_core_failure"]
                and done["completed_native"] == d["completed_native"] == d["charged_native"],
                name,
            )
            ev["verified_original_ledger"] = {
                "path": rel(ledger),
                "file_sha256": file_sha256(ledger),
            }
            ev["verified_native_summary"] = {
                "path": rel(base / "summary.json"),
                "file_sha256": done["native_summary_sha256"],
            }
            ev["verified_candidate_declaration_sha256"] = file_sha256(cand)
        else:
            raise ValueError(f"unverified evidence format: {name}")
        self.data[name], self.evidence[name] = d, ev
        return d

    def option(self, key, village, C, cost_receipt):
        self.receipt(cost_receipt)
        d = dict(village=village, C_m2=C, cost_receipt=cost_receipt, maps=[], evidence_by_scope=[])
        check(key not in self.options, key)
        self.options[key] = d
        return d

    def claim(self, option, scope, receipts, **metrics):
        for name in receipts:
            self.receipt(name)
        option["evidence_by_scope"].append(dict(scope=scope, receipts=receipts, **metrics))

    def map(self, option, path, filters=None, links=None, expected_action=None):
        d = read(path)
        filters = filters or {}
        props = [
            f
            for f in d["features"]
            if all(f["properties"].get(k) in val for k, val in filters.items())
        ]
        check(bool(props), f"no matching protective features: {path}")
        for field, name in (links or {}).items():
            self.receipt(name)
            check(
                d[field] == self.evidence[name]["file_sha256"],
                f"GIS receipt mismatch: {path} {field}",
            )
        # The multi-band map uses the native UTM grid. Dedicated action maps use WGS84.
        native = "layer" in filters
        crs = d.get("metric_crs", d.get("source_native_crs", "EPSG:32649"))
        project = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
        result = {}
        for f in props:
            p = f["properties"]
            material = p.get("material", p.get("cleared_land_type", p.get("layer")))
            geom = shape(f["geometry"])
            area = geom.area if native else transform(project, geom).area
            stated = p.get("actual_m2", p.get("actual_area_m2", area))
            same_area(area, stated)
            result[material] = result.get(material, 0.0) + stated
        same_area(math.fsum(result.values()), option["C_m2"])
        entry = {
            "path": rel(path),
            "file_sha256": file_sha256(path),
            "feature_filter_allowed_values": filters,
            "coordinate_crs": crs if native else "EPSG:4326",
            "verified_material_area_m2": result,
            "map_receipt_bindings": links or {},
        }
        if expected_action:
            ids = {f["properties"]["material"]: f["properties"]["action_ids"] for f in props}
            observed = actions(
                dict(
                    demolished_ids=ids["roof_union"],
                    cleared_physical_cell_ids=ids["whole_ground_cells"],
                )
            )
            check(observed == expected_action, f"wrong map action: {path}")
            entry["action_sha256"] = observed
        option["maps"].append(entry)
        return d


def local_options(i):
    base = "pingyan-six-options-loss-capped-minimum"
    frontier = "pingyan-six-options-native-pareto-witness-audit"
    failures = "pingyan-w0.01-first-ignition-prefix"
    comp = "pingyan-three-policies-complete8-table"
    reg = "pingyan-observed-clearance-subset-registration"
    low = "pingyan-w2.5-and-w15.01-native-strict"
    wide = "pingyan-local-union-strict-complete8"
    independent = "pingyan-narrow-independent-strict-counterexample"
    extrap = "pingyan-compact-new-source-extrapolation"
    d, f = i.receipt(base), i.receipt(frontier)
    check(f["complete_six_candidate_frontier"] and not f["unresolved_candidates"], frontier)
    check(f["prior_six_set_certificate_sha256"] == i.evidence[base]["file_sha256"], frontier)
    for p, digest in d["signed_input_file_hashes"].items():
        check(file_sha256(ROOT / p) == digest, f"six candidates' bound input changed: {p}")
    wits = {r["plan"]: r for r in f["intermediate_policy_decisions"]}
    for row in d["observations"]:
        plan = row["policy"]
        o = i.option(plan, "pingyan", row["C_m2"], base)
        evidence = [base, frontier]
        targets = {
            "core_no_ignition_all": row["core_safe_across_full_set"],
            "strict_full_endpoints": row["strict_closed_full_checks"],
            "worst_measured_A_m2": row["max_A_m2"],
            "worst_measured_original_N": row["worst_original_N"],
            "nondominated_in_six": plan in f["definitely_nondominated"],
        }
        if plan == WIDTH + "0.01":
            evidence.append(failures)
            targets["observed_heritage_ignition_on_physical_prefix"] = bool(
                i.receipt(failures)["first_protected_ignition_at_saved_stop"]
            )
        if plan in wits:
            targets.update(
                pick(
                    wits[plan],
                    "strict_safe_closed_witnesses",
                    "lower_bound_on_maximum_A_m2",
                    "lower_bound_on_maximum_original_N_upper",
                    "decision",
                )
            )
        maps = {
            WIDTH + "2.5": ("pingyan-local-2.5m", {"certificate_file_sha256": low}),
            WIDTH + "15.01": ("pingyan-local-15.01m", {"certificate_file_sha256": wide}),
            COMPACT: (
                "pingyan-local-observed-subset",
                {"verified_native_receipt_file_sha256": comp, "action_registration_sha256": reg},
            ),
        }
        if plan in maps:
            filename, links = maps[plan]
            i.map(o, OUT / (filename + ".geojson"), links=links)
            evidence.extend(links.values())
        else:
            i.map(
                o,
                record_path("pingyan-protected-union", ".geojson"),
                {"plan_id": [plan], "layer": LAYERS},
            )
        i.claim(o, "pingyan_original_four_design_two_meshes", evidence, **targets)
        if plan == WIDTH + "2.5":
            x = i.receipt(independent)
            same_area(x["candidate_C_m2"], o["C_m2"])
            check(
                x["registered_plan"] == plan and not x["full_eight_case_loss_summary_available"],
                plan,
            )
            i.claim(
                o,
                "pingyan_preregistered_independent_failure_witness",
                [independent],
                core_no_ignition_all=False,
                protected_count=x["protected_total"],
                protected_ignited_in_witness=x["protected_ignited"],
                single_strict_terminal_A_m2=x["A_burned_out_m2"],
                single_strict_terminal_original_N=x["N_burned_owner_bounds"],
                roof_scale_m=x["roof_scale_m"],
                whole_cohort_worst_A_m2=None,
            )
        if plan == COMPACT:
            x = i.receipt(extrap)
            r = next(z for z in x["results"] if z["plan_id"] == plan)
            check(
                not x["candidate_added_to_registered_arms"]
                and not x["independent_compact_dominance_established"],
                extrap,
            )
            i.claim(
                o,
                "pingyan_compact_static_extrapolation_to_four_independent_rankings",
                [extrap],
                analyzed_families=r["families_bounded"],
                heritage_nonignition_proved_families=r["heritage_nonignition_proved_families"],
                native_cases=x["native_CA_evaluations_for_this_audit"],
                independent_feasibility="undetermined",
            )
    return dict(
        receipts=[base, frontier],
        eligible_objectives=["C_m2", "worst_A_m2", "worst_original_N_upper"],
        tested_options=[r["policy"] for r in d["observations"]],
        nondominated=f["definitely_nondominated"],
        minimum_C_given_heritage_safe=f["unique_least_C_heritage_safe"],
        minimum_C_given_registered_loss_caps=d["finite_unique_minimum"],
        loss_caps=d["constraints"],
    )


def full_zero(i, o, name):
    x = i.receipt(name)
    same_area(x["C_m2"], o["C_m2"])
    check(x["zero_core_heat_confirmed_for_all_heritage_and_8_native_checks"], name)
    i.claim(
        o,
        "pingyan_original_four_fixed_zero_heat_design_two_meshes",
        [name],
        strict_terminal=x["strict_terminal"],
        observed_peak_heritage_rise_K=x["peak_above_initial_ambient_K"],
        observed_heritage_ignitions=0,
    )


def zero_options(i):
    core = "ground-centre-core-separation"
    baseline = "pingyan-zero-core-only-coarse-control-witness"
    g = next(r for r in i.receipt(core)["villages"] if r["village"] == "pingyan")
    base = i.option(
        "pingyan-zero-heat-core-only", "pingyan", g["minimum_C_m2_for_mixed_support_rule"], core
    )
    i.map(
        base,
        OUT / "pingyan-clearance-options.geojson",
        {"plan_id": ["minimum-ground-centre-core-clearance"]},
    )
    i.claim(
        base,
        "all_modelled_exterior_ignitions",
        [core],
        zero_external_core_heat=not (
            g["possible_cross_core_contact_pairs"] or g["possible_cross_core_radiation_pairs"]
        ),
    )
    witness = i.receipt(baseline)["observables_for_first_valid_witness"]
    check(
        witness["qualifies"] and witness["monotone_prefix_without_native_physical_invalid_reasons"],
        baseline,
    )
    i.claim(
        base,
        "one_matched_1m_monotone_prefix",
        [baseline],
        **pick(
            witness,
            "full_natural_extinction",
            "observed_burned_area_lower_bound_m2",
            "observed_unique_original_buildings_lower_bound",
        ),
    )
    prior = "pingyan-zero-heat-and-design-loss"
    x = i.receipt(prior)
    old = i.option("pingyan-zero-heat-four-previous", "pingyan", x["C_m2"], prior)
    i.map(
        old,
        record_path(prior, ".geojson"),
        links={"certificate_file_sha256": prior},
        expected_action=actions(x["model_action"]),
    )
    i.claim(
        old,
        "all_modelled_exterior_ignitions",
        [prior],
        zero_external_core_heat=x[
            "core_forced_external_heat_zero_K_rise_from_ambient_for_all_exterior_ignitions"
        ],
    )
    i.claim(
        old,
        "pingyan_four_registered_zero_heat_design_sources",
        [prior],
        certified_A_upper_m2=x["worst_registered_burned_area_upper_m2"],
        certified_original_N_upper=x["worst_registered_original_building_owner_count_upper"],
    )
    full_zero(i, old, prior + "-native-eight-endpoints")
    minimum = "pingyan-zero-heat-first-extensions-minimum"
    audit = "pingyan-zero-heat-first-extensions-independent"
    coverage = "pingyan-four-fixed-placements-eight-coverage"
    x, verify, c = i.receipt(minimum), i.receipt(audit), i.receipt(coverage)
    check(verify["source_receipt_sha256"] == i.evidence[minimum]["file_sha256"], audit)
    same_area(x["minimum_C_m2"], verify["minimum_C_m2"])
    rep = actions(x["model_action"])
    maps = record_path("pingyan-three-tied-minimum-firebreaks", ".geojson")
    groups = {
        r["properties"]["placement_action_sha256"]: r["properties"]["placement_number"]
        for r in read(maps)["features"]
    }
    check(
        set(groups) == set(x["canonical_tied_placements_sha256"])
        and len(groups)
        == x["tied_minimum_distinct_placements"]
        == verify["distinct_exact_tied_placements"],
        minimum,
    )
    z = x["checked_physical_protection_and_loss"]
    for digest, num in sorted(groups.items(), key=lambda r: r[1]):
        o = i.option(f"pingyan-zero-heat-four-tie-{num}", "pingyan", x["minimum_C_m2"], minimum)
        i.map(
            o,
            maps,
            {"placement_number": [num]},
            links={
                "original_minimum_file_sha256": minimum,
                "independent_minimum_file_sha256": audit,
                "eight_scenario_coverage_file_sha256": coverage,
            },
            expected_action=digest,
        )
        i.claim(
            o,
            "all_modelled_exterior_ignitions",
            [minimum, coverage],
            zero_external_core_heat=not (
                z["external_core_contact_edges"] or z["external_core_radiation_edges"]
            ),
        )
        i.claim(
            o,
            "pingyan_four_registered_zero_heat_design_sources",
            [minimum, audit],
            families=x["design_families"],
            certified_A_upper_m2=z["worst_A_upper_m2"],
            certified_original_N_upper=z["worst_original_N_upper"],
        )
        rec = next(r for r in c["placements"] if r["frozen_action_sha256"] == digest)
        i.claim(
            o,
            "other_four_already_disclosed_static_coverage",
            [coverage],
            families=rec["other_disclosed_four"]["families"],
            certified_under_original_caps=rec["other_disclosed_four"][
                "certified_on_actual_physical_policy_source"
            ],
            remaining_families="undetermined_by_this_sufficient_rule",
        )
        if digest == rep:
            full_zero(i, o, "pingyan-zero-heat-minimum-native-eight-endpoints")
    recent = "pingyan-eight-known-zero-heat-and-design-loss"
    pair = "pingyan-two-matched-1m-full-native-pair-audit"
    d, p = i.receipt(recent), i.receipt(pair)
    o = i.option("pingyan-zero-heat-eight-known", "pingyan", d["C_m2"], recent)
    path = record_path(recent, ".geojson")
    check(file_sha256(path) == d["GIS_sha256"], recent)
    i.map(o, path, expected_action=actions(d["model_action"]))
    i.claim(
        o,
        "all_modelled_exterior_ignitions",
        [recent],
        zero_external_core_heat=d["all_external_core_heat_zero"],
        protected_count=d["protected_heritage_count"],
    )
    i.claim(
        o,
        "eight_known_families_used_for_design",
        [recent],
        families=d["sources_frozen"],
        fresh_independent_families=d["new_independent_validation_scenarios"],
        **d["all_eight_design_capacities"],
    )
    check(
        p["all_footprints_initial_masses_and_energies_matched"]
        and p["all_common_mesh_and_integration_hashes_matched"],
        pair,
    )
    same_area(p["new"]["construction_C_m2"], o["C_m2"])
    i.claim(
        o,
        "two_already_disclosed_exactly_matched_1m_cases",
        [pair],
        fine_mesh_runs=p["new_fine_scale_runs"],
        **pick(
            p["new"],
            "closed_strict_heritage_safe",
            "protected_inventory_per_case",
            "heritage_ignitions",
            "maximum_core_temperature_rise_K",
            "worst_whole_domain_burned_A_m2",
            "worst_burned_original_N",
        ),
    )
    trade = "pingyan-two-zero-heat-casewise-and-worst-loss-tradeoffs"
    t = i.receipt(trade)
    return dict(
        receipts=[minimum, audit, trade],
        finite_search_scope=x["scope"],
        initial_binary_selections=x["candidate_binary_selections"],
        distinct_admissible_combinations=x["cap_admissible_distinct_compartment_combinations"],
        tied_option_ids=[f"pingyan-zero-heat-four-tie-{n}" for n in sorted(groups.values())],
        native_representative=f"pingyan-zero-heat-four-tie-{groups[rep]}",
        previous_option="pingyan-zero-heat-four-previous",
        measured_comparison_previous_vs_representative=pick(
            t,
            "ranked_by_C_worst_A_worst_Nupper",
            "ranked_by_C_and_separate_A_Nupper_for_every_tested_source_and_scale",
        ),
    )


def joint_and_batuan(i):
    for v in ["pingyan", "batuan"]:
        name = "total-protection-and-loss-" + v
        native = "joint-loss-" + v + "-complete-native-audit"
        d, x = i.receipt(name), i.receipt(native)
        chosen = next(
            r for r in d["optimized_total_construction"] if r["core_energy_ceiling"] == 0.99
        )
        o = i.option(
            JOINT if v == "pingyan" else "batuan-joint-core-and-loss", v, chosen["C_m2"], name
        )
        same_area(x["C_m2"], o["C_m2"])
        i.map(
            o,
            OUT / (v + "-combined-clearance.geojson"),
            links={"certificate_file_sha256": name, "verified_native_receipt_file_sha256": native},
            expected_action=actions(chosen),
        )
        i.claim(
            o,
            "all_modelled_exterior_ignitions",
            [name],
            core_nonignition_certified=True,
            ignition_scope=d["core_protection_scope"],
        )
        i.claim(
            o,
            "plan_specific_already_known_registered_families",
            [name],
            families=d["fixed_25m2_source_budget_count"],
            certified_A_upper_m2=d["worst_registered_A_upper_m2"],
            certified_original_N_upper=d["worst_registered_N_upper"],
        )
        i.claim(
            o,
            "plan_specific_registered_complete_native_two_meshes",
            [native],
            families=x["registered_families"],
            strict_terminal_checks=len(x["checked_native_cases"]),
            **pick(
                x,
                "strict_heritage_no_ignition_all",
                "cases_with_any_positive_core_incoming_fire_heat",
                "maximum_measured_core_temperature_K",
                "worst_measured_A_m2",
                "worst_measured_N",
            ),
        )
    design = "batuan-direct-D9-G0-complete8-audit"
    independent = "batuan-independent-D9-G0-native-strict"
    old, n = i.receipt(design), i.receipt(independent)
    v = old["summary"]
    o = i.option(D9, "batuan", v["C_m2"], design)
    i.map(o, OUT / "batuan-economic-D9.geojson", links={"certificate_file_sha256": independent})
    i.claim(
        o,
        "batuan_original_four_two_meshes",
        [design],
        **pick(
            v,
            "complete8_strict_passed_and_closed",
            "strict_passed_cases",
            "worst_A_burned_out_m2",
            "worst_N_burned_out_bounds",
        ),
        peak_core_K=old["maximum_core_temperature_K"],
    )
    w = n["archive"]["fully_evaluated_feasible_plans"]
    check(
        len(w) == 1
        and w[0]["plan_id"] == D9
        and all(
            r["terminal_complete"] and not r["core_ignition_observed"] and not r["invalid_reasons"]
            for r in n["native_checks"]
        ),
        independent,
    )
    i.claim(
        o,
        "batuan_independently_drawn_four_two_meshes",
        [independent],
        strict_terminal_no_ignition_checks=len(n["native_checks"]),
        **pick(w[0]["worst_case"], "A_burned_out_m2", "N_burned_out"),
    )
    zero = "no-external-core-heat-with-fixed-loss-bounds"
    r = next(r for r in i.receipt(zero)["villages"] if r["village"] == "batuan")
    check(r["fixed_protocol_rule_feasible"] and r["source_footprints_unchanged"], zero)
    o = i.option("batuan-joint-zero-heat-and-loss", "batuan", r["C_m2"], zero)
    i.map(
        o,
        OUT / "batuan-combined-zero-heat.geojson",
        links={"certificate_file_sha256": zero},
        expected_action=actions(r),
    )
    i.claim(o, "all_modelled_exterior_ignitions", [zero], zero_external_core_heat=True)
    cert = "total-protection-and-loss-batuan"
    i.claim(
        o,
        "eight_already_known_registered_families",
        [zero, cert],
        families=i.receipt(cert)["fixed_25m2_source_budget_count"],
        certified_A_upper_m2=r["registered_A_upper_m2"],
        certified_original_N_upper=r["registered_N_upper"],
    )


def independent_group(i):
    name = "pingyan-independent-confirmatory-pareto"
    d = i.receipt(name)
    check(
        i.evidence[name]["file_sha256"]
        == "0cb8eb96069a264fe9eae5115a7dd5aab5c93724fc5b1b9f4c4fae19b2602203",
        "independently archived final digest changed",
    )
    m = d["strict_full_and_matched_source_frontiers"]
    summary = read(OUT / i.evidence[name]["verified_native_summary"]["path"])
    result = dict(
        status="complete",
        receipts=[name],
        **pick(
            d,
            "declared_candidates",
            "independent_draw_families",
            "roof_scales_per_family",
            "completed_native",
            "not_run_due_to_strict_core_failure",
        ),
        frontiers={},
    )
    for plan, rec in m["protective_action_maps"].items():
        rows = [r for r in summary["checks"] if r["plan_id"] == plan]
        passed = sum(r["status"] == "passed" and r["valid_terminal_fire_observation"] for r in rows)
        failed = sum(r["status"] == "failed" and r["valid_terminal_fire_observation"] for r in rows)
        skipped = len(m["archived_native_skip_causes"].get(plan, []))
        check(
            passed + failed == len(rows)
            and passed + failed + skipped
            == d["independent_draw_families"] * d["roof_scales_per_family"],
            plan,
        )
        i.claim(
            i.options[plan],
            "complete_preregistered_independent_strict_decision",
            [name],
            strict_passed=passed,
            strict_protection_failed=failed,
            unissued_after_verified_failure=skipped,
            eligible=plan in m["all_strict_original_families"]["eligible"],
        )
        check(
            any(
                g["file_sha256"] == rec["sha256"]
                and (OUT / g["path"]).resolve() == (ROOT / rec["workspace_path"]).resolve()
                for g in i.options[plan]["maps"]
            ),
            f"independent protective map changed: {plan}",
        )
        same_area(rec["canonical_C_m2"], i.options[plan]["C_m2"])
    for key in ("all_strict_original_families", "common_actual_physical_source_families"):
        f = m[key]
        vectors = {plan: r["objective_vector_C_maxA_maxNupper"] for plan, r in f["results"].items()}
        front = [
            plan
            for plan, v in vectors.items()
            if not any(
                all(a <= b for a, b in zip(w, v, strict=True))
                and any(a < b for a, b in zip(w, v, strict=True))
                for w in vectors.values()
            )
        ]
        check(
            set(front) == set(f["exact_non_dominated_plan_ids"]), f"dominance check failed: {key}"
        )
        result["frontiers"][key] = dict(
            eligible=f["eligible"],
            strictly_nondominated=f["exact_non_dominated_plan_ids"],
            compared_mesh_cases=len(f["observed_mesh_cases"]),
        )
        for plan, r in f["results"].items():
            i.claim(
                i.options[plan],
                "preregistered_independent_" + key,
                [name],
                **pick(
                    r,
                    "number_of_mesh_observations",
                    "max_total_burned_A_m2",
                    "observed_protected_ignitions_across_eligible_cases",
                    "maximum_observed_protected_surface_temperature_K",
                    "worst_burned_original_building_owner_bounds",
                    "max_roof_burned_m2",
                    "max_nonroof_burned_m2",
                    "strictly_dominated_by",
                ),
                nondominated=plan in front,
                families=len(r["families"]),
            )
    return result


def build():
    i = Inventory()
    local = local_options(i)
    zero = zero_options(i)
    joint_and_batuan(i)
    independent = independent_group(i)
    doc = dict(
        schema="heritage-protective-options-index-1",
        path_base="directory_containing_this_index",
        generator={"path": rel(Path(__file__).resolve()), "file_sha256": file_sha256(__file__)},
        definitions={
            "C_m2": "模型清理屋面并集面积加整格地面清理面积。单位为平方米。",
            "A_m2": "模型计算域内烧尽燃料覆盖的物理面积。单位为平方米。",
            "original_N": "按原始建筑物归属记录计算的烧尽建筑数量区间。火损目标采用上界。",
            "worst": "各指标分别取所列情景和网格中的最大值。极值可来自不同火程。",
            "heritage": "遗产未引燃与零外来热输入分别记录。后者的普适范围为模型内外部起火。",
            "certificate_scope": (
                "最小或非支配结论限于相应候选、机制和情景。尚无真实村落火灾校准记录。"
            ),
            "missing": "null 表示完整情景集的相应数值或资格未被核定。",
            "width_map_filter": "筛选条件同时适用。空对象选取全部施工要素。",
        },
        options=i.options,
        comparisons=dict(
            pingyan_six_original_design_candidates=local,
            pingyan_four_design_zero_heat=zero,
            pingyan_preregistered_independent_three_arms=independent,
        ),
        evidence=i.evidence,
    )
    doc["index_content_sha256"] = content_hash(doc)
    return doc


def verify_links(doc):
    for key, option in doc["options"].items():
        check(option["maps"] and option["evidence_by_scope"], key)
        refs = [
            option["cost_receipt"],
            *(name for c in option["evidence_by_scope"] for name in c["receipts"]),
        ]
        check(all(r in doc["evidence"] for r in refs), key)
        for row in option["maps"]:
            check(file_sha256(OUT / row["path"]) == row["file_sha256"], row["path"])
    for row in [doc["generator"], *doc["evidence"].values()]:
        check(file_sha256(OUT / row["path"]) == row["file_sha256"], row["path"])
    check(
        content_hash({k: v for k, v in doc.items() if k != "index_content_sha256"})
        == doc["index_content_sha256"],
        "index integrity",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="rebuild in memory and compare with existing index"
    )
    args = parser.parse_args()
    doc = build()
    verify_links(doc)
    if args.check:
        check(read(TARGET) == doc, "index out of date")
    else:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=OUT, prefix=".verified-options-", delete=False
        ) as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.chmod(f.name, 0o644)
        os.replace(f.name, TARGET)
    print(
        json.dumps(
            dict(
                options=len(doc["options"]),
                receipts=len(doc["evidence"]),
                sha256=file_sha256(TARGET),
                independent=doc["comparisons"]["pingyan_preregistered_independent_three_arms"][
                    "status"
                ],
            )
        )
    )


if __name__ == "__main__":
    main()
