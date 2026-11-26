"""Reconstruct the frozen zero-heat campaign's eight endpoints from its own native archive."""

import argparse
import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import verify
from summarize_heritage_joint_pareto import summarize

from pyfireca.planning.fire_loss import recover_original_node_owners
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def audit(root, *, extension=False):
    results = root / "docs/results"
    run = root / "runs/research/heritage-pingyan-zero-core-heat-and-design-loss-20261111"
    config = (
        root
        / "examples/research/heritage/area-20261110/pingyan-zero-core-heat-and-design-loss-native-20261111.json"
    )
    files = dict(
        geometry=results / "heritage-pingyan-zero-heat-and-design-loss-20261111.json",
        independent=results
        / "heritage-pingyan-zero-heat-and-design-loss-independent-20261111.json",
        source_and_stages=results
        / "heritage-pingyan-zero-heat-native-support-and-capacity-20261111.json",
        baseline_prefix=results
        / "heritage-pingyan-zero-core-only-coarse-control-witness-20261111.json",
        experiment=run / "experiment.json",
        native_preflight=run / "preflight.json",
        native_budget=run / "execution-budget.json",
        config=config,
    )
    if extension:
        run = root / "runs/research/heritage-pingyan-zero-heat-minimum-native-20261111"
        config = (
            root
            / "examples/research/heritage/area-20261110/pingyan-zero-heat-minimum-native-20261111.json"
        )
        files.update(
            geometry=results / "heritage-pingyan-zero-heat-first-extensions-minimum-20261111.json",
            experiment=run / "experiment.json",
            native_preflight=run / "preflight.json",
            native_budget=run / "execution-budget.json",
            config=config,
            roof_geometry_equivalence=run / "same-roof-capacity-qualification.json",
        )
    evidence = {k: read_json(v) for k, v in files.items()}
    receipt = evidence["geometry"]
    experiment = evidence["experiment"]
    witness = evidence["source_and_stages"]
    c = evidence["config"]
    (p,) = c["plans"]
    D = set(p["demolished_ids"])
    for sealed in [receipt, evidence["independent"], witness, evidence["baseline_prefix"]]:
        verify(
            sealed["receipt_sha256"]
            == content_hash({k: v for k, v in sealed.items() if k != "receipt_sha256"}),
            "sealed evidence changed",
        )
    verify(
        p["demolished_ids"] == receipt["model_action"]["demolished_ids"]
        and p["cleared_physical_cell_ids"] == receipt["model_action"]["cleared_physical_cell_ids"]
        and evidence["native_preflight"]["passed"]
        and experiment["problem_sha256"] == content_hash(experiment["problem"])
        and experiment["config_sha256"] == file_sha256(config)
        and experiment["config"] == c,
        "wrong question or preflight",
    )
    cost_target = receipt["minimum_C_m2"] if extension else receipt["C_m2"]
    if extension:
        caps = receipt["checked_physical_protection_and_loss"]
        area_cap = caps["worst_A_upper_m2"]
        owner_cap = caps["worst_original_N_upper"]
        seal = evidence["roof_geometry_equivalence"]
        verify(
            seal["receipt_sha256"]
            == content_hash({k: v for k, v in seal.items() if k != "receipt_sha256"})
            and seal["selected_problem_sha256"] == experiment["problem_sha256"],
            "shared roof-stage qualification changed",
        )
    else:
        area_cap = receipt["worst_registered_burned_area_upper_m2"]
        owner_cap = receipt["worst_registered_original_building_owner_count_upper"]
    budget = evidence["native_budget"]
    verify(
        budget["started_checks"] == 8 and budget["active"] is None,
        "eight distinct budgeted fires must be terminal",
    )
    with tempfile.TemporaryDirectory(prefix="pingyan-zero-heat-") as tmp:
        strict = summarize(run, config, Path(tmp) / "strict.json", temporal_mode="strict")
        finite = summarize(
            run, config, Path(tmp) / "finite.json", temporal_mode="finite_domain_closed"
        )
    _, _, base, inventory, _, _, _, _, _ = load_bound_inputs("pingyan")
    ambient = base.physical_request.scenario.ambient_temperature_K
    parent_area = base.node_inputs.parameters.covered_area_m2.ravel()
    extras = set(inventory.by_id) - {
        o for i, group in enumerate(base.owners) if parent_area[i] > 0 for o in group
    }
    target = {x.building_id for x in inventory.targets}
    cases = [r for r in strict["native_checks"] if r.get("manifest")]
    verify(
        len(cases) == 8 and len({r["scenario_id"] for r in cases}) == 8, "native archive incomplete"
    )
    finite_rows = {r["scenario_id"]: r for r in finite["finite_domain_checks"]}
    ords = {f["fire_id"]: i + 1 for i, f in enumerate(c["source_fires"])}
    stages = {(r["draw_ordinal"], r["scale_m"]): r for r in witness["rows"]}
    actual_legal = {
        next(s for s in f["protocol"]["draws"][0]["priority_ids"] if s not in D)
        for f in experiment["problem"]["fires"].values()
    }
    prefix = evidence["baseline_prefix"]["observables_for_first_valid_witness"]
    control_run = root / "runs/research/heritage-pingyan-zero-core-only-coarse-control-20261111"
    (baseline_manifest,) = (
        m
        for m in control_run.glob("evaluations/*/complete.json")
        if file_sha256(m) == prefix["native_observation_manifest_sha256"]
    )
    baseline = read_json(baseline_manifest.with_name("check.json"))["evidence"]
    matching = []
    all_inside_original = True
    counts = []
    hashes = []
    artifacts = 0
    for row in cases:
        mp = Path(row["manifest"])
        m = read_json(mp)
        folder = mp.parent
        verify(
            file_sha256(mp) == row["manifest_sha256"]
            and m["problem_sha256"] == experiment["problem_sha256"],
            "result manifest changed",
        )
        for name, h in m["files_sha256"].items():
            verify(file_sha256(folder / name) == h, "native artifact changed")
            artifacts += 1
        check = read_json(folder / "check.json")["evidence"]
        evalu = read_json(folder / "evaluation.json")
        loss = read_json(folder / "fire-loss.json")
        metrics = evalu["metrics"]
        fire = experiment["problem"]["fires"][check["fire_id"]]
        expected = next(s for s in fire["protocol"]["draws"][0]["priority_ids"] if s not in D)
        (support,) = stages[ords[check["fire_id"]], check["scale_m"]][
            "physical_support_by_plan"
        ].values()
        fields = ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        verify(
            check["source_id"] == expected
            and metrics["actual_source_ids"] == [expected]
            and check["source_footprint_sha256"]
            == support["footprint_sha256"]
            == content_hash(fire["footprints"][expected])
            and np.allclose(
                [check["source_budget"][k] for k in fields],
                [support["native_budget"][k] for k in fields],
                rtol=1e-9,
                atol=1e-7,
            ),
            "source moved or physical budget changed",
        )
        with (
            np.load(folder / "final-state.npz") as state,
            np.load(folder / "loss-basis.npz") as basis,
            np.load(folder / "layout.npz") as ls,
        ):
            T = float(state["time_s"])
            ign = np.flatnonzero(
                np.isfinite(state["ignition_time_s"].ravel())
                & (state["ignition_time_s"].ravel() <= T)
            )
            burned = np.flatnonzero(
                np.isfinite(state["burnout_time_s"].ravel())
                & (state["burnout_time_s"].ravel() <= T)
            )
            area = basis["post_intervention_covered_area_m2"].ravel()
            offsets = ls["owner_offsets"]
            ids = ls["owner_ids"]
            verify(np.isin(burned, ign).all() and np.all(area[ign] > 0), "impossible burn history")
            original, _ = recover_original_node_owners(
                SimpleNamespace(
                    owners=tuple(tuple(ids[offsets[j] : offsets[j + 1]]) for j in ign),
                    component_id=ls["component_id"][ign],
                    cell_id=ls["physical_cell_id"][ign],
                    xy_m=ls["xy_m"][ign],
                ),
                inventory,
                demolished_ids=D,
            )
            within_original_roofs = all(actual_legal.intersection(g) for g in original)
            all_inside_original &= within_original_roofs
            if not extension:
                verify(within_original_roofs, "observed fire leaves the certified compartment")
            spent = [original[i] for i in np.searchsorted(ign, burned)]
            Nlo = len({g[0] for g in spent if len(g) == 1})
            Nhi = len(extras.union(*(set(g) for g in spent)))
            A = math.fsum(area[burned])
            active = math.fsum(area[np.setdiff1d(ign, burned)])
            pos = np.flatnonzero(np.isin(ids, list(target)))
            core_i = np.unique(np.searchsorted(offsets[1:], pos, side="right"))
            verify(len(core_i) > 0 and set(ids[pos]) == target, "missing protected material")
            for name in (
                "incident_radiation_J",
                "absorbed_radiation_J",
                "received_convection_J",
                "released_energy_J",
            ):
                verify(
                    np.all(state[name].ravel()[core_i] == 0),
                    "protected material received or emitted heat",
                )
            verify(
                np.all(state["surface_temperature_K"].ravel()[core_i] == ambient)
                and not np.isin(core_i, ign).any(),
                "a protected material heated",
            )
        outcome = metrics["core_outcomes"]
        verify(
            len(outcome) == len(target)
            and all(
                not x["ignited"]
                and x["maximum_valid_surface_temperature_K"] == ambient
                and x["absorbed_radiation_J"] == x["received_convection_J"] == 0
                for x in outcome
            ),
            "native protected historical maxima differ",
        )
        verify(
            loss["physical_remaining_roof_annotation_counts"][
                "burned_out_annotation_attribution_count"
            ]
            == {"lower": Nlo, "upper": Nhi}
            and abs(loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"] - A) < 1e-7,
            "source/owner resolved loss differs",
        )
        valid = row["status"] == "passed" and row.get("terminal_complete", False)
        cond = finite_rows[row["scenario_id"]]
        closed = cond.get("conditional_status") == "passed" and cond.get(
            "finite_domain_closed", False
        )
        verify(not (valid or closed) or active == 0, "active burning at claimed extinction")
        verify(
            area_cap >= A and Nhi <= owner_cap,
            "observation violates certified ceiling",
        )
        if (check["fire_id"], check["scale_m"]) == (baseline["fire_id"], baseline["scale_m"]):
            physical_fields = (
                "source_id",
                "source_footprint_sha256",
                "source_budget",
                "common_layout_sha256",
                "frozen_integrated_node_mask_sha256",
            )
            verify(
                all(check[k] == baseline[k] for k in physical_fields),
                "control comparison does not share the complete physical source or mesh",
            )
            baseline_lower = prefix["observed_burned_area_lower_bound_m2"]
            matching.append(
                dict(
                    matched_source_budgets_layout_and_quadrature=True,
                    strict_combined_fire_closed=bool(valid),
                    zero_core_only_burned_m2_lower_bound=baseline_lower,
                    combined_full_burned_m2=A if valid else None,
                    observed_same_case_burned_area_improvement_at_least_m2=baseline_lower - A
                    if valid
                    else None,
                    original_baseline_manifest_sha256=file_sha256(baseline_manifest),
                    original_combined_manifest_sha256=file_sha256(mp),
                )
            )
        counts.append(
            dict(
                scale_m=check["scale_m"],
                strict_valid=bool(valid),
                finite_closed=bool(closed),
                A=A,
                Nlo=Nlo,
                Nhi=Nhi,
            )
        )
        hashes.append(row["manifest_sha256"])
    verify(len(matching) == 1, "same-source native baseline control missing or ambiguous")
    actual_cost = strict["canonical_construction_area_ledger"][0]["canonical_C_m2"]
    verify(abs(actual_cost - cost_target) < 1e-5, "construction cost differs")

    def summarize_set(pool):
        return dict(
            observations=len(pool),
            worst_A_m2=max(x["A"] for x in pool) if pool else None,
            worst_N_bounds=[max(x["Nlo"] for x in pool), max(x["Nhi"] for x in pool)]
            if pool
            else None,
        )

    report = dict(
        schema_version="zero-heat-pingyan-eight-full-fire-endpoint-1",
        C_m2=actual_cost,
        eight_design_checks=8,
        independent_source_draws=0,
        all_started_natively_and_artifact_hashes_verified=artifacts,
        strict_terminal=summarize_set([x for x in counts if x["strict_valid"]]),
        conditional_finite_domain=summarize_set([x for x in counts if x["finite_closed"]]),
        strict_by_mesh={
            str(x): summarize_set([v for v in counts if v["strict_valid"] and v["scale_m"] == x])
            for x in c["roof_scales_m"]
        },
        all_covered_by_theory=True,
        all_ignitions_remained_on_original_containment_roofs=all_inside_original,
        peak_heritage_temperature_K=ambient,
        peak_above_initial_ambient_K=0.0,
        cumulative_heritage_incident_radiation_absorption_convection_and_release_J=0.0,
        zero_core_heat_confirmed_for_all_heritage_and_8_native_checks=True,
        modeled_gis_and_physical_support_verified=True,
        physically_matched_zero_core_only_control=matching[0],
        native_budget_elapsed_s=budget["elapsed_s"],
        original_manifests_combined_sha256=content_hash(sorted(hashes)),
        source_file_sha256={k: file_sha256(v) for k, v in files.items()},
        method_sha256=file_sha256(__file__),
    )
    report["receipt_sha256"] = content_hash(report)
    result_name = (
        "heritage-pingyan-zero-heat-minimum-native-eight-endpoints-20261111.json"
        if extension
        else "heritage-pingyan-zero-heat-and-design-loss-native-eight-endpoints-20261111.json"
    )
    with (results / result_name).open("x") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print("aggregate native endpoint verification", report["strict_terminal"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", action="store_true")
    audit(Path(__file__).resolve().parents[1], extension=parser.parse_args().extension)
