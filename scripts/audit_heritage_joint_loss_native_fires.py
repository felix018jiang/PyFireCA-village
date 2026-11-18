"""Compare complete native fire observations with the precomputed protective bounds."""

import argparse
import json
import math
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from check_heritage_compartment_first_escape import load_bound_inputs
from check_heritage_registered_roof_compartment import footprint_budget
from summarize_heritage_joint_pareto import summarize

from pyfireca.planning.fire_loss import recover_original_node_owners
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def require(value, err):
    if not value:
        raise ValueError(err)


def audit(village):
    root = Path(__file__).resolve().parents[1]
    res = root / "docs/results"
    config = (
        root / f"examples/research/heritage/area-20261110/{village}-joint-loss-native-20261111.json"
    )
    run = root / f"runs/research/heritage-joint-loss-native-{village}-20261111"
    signed_path = res / f"heritage-total-protection-and-loss-{village}-20261111.json"
    theory = read_json(signed_path)
    independent = res / f"heritage-total-protection-and-loss-{village}-independent-20261111.json"
    static = read_json(independent)
    require(
        theory.pop("receipt_sha256") == content_hash(theory)
        and static.pop("receipt_sha256") == content_hash(static)
        and static["joint_candidate_sha256"] == file_sha256(signed_path),
        "joint certificate changed",
    )
    phase_runs = [
        ("design", root / f"runs/research/heritage-thermal-and-geometric-pareto-{village}-20261111")
    ]
    if village == "batuan":
        phase_runs.append(
            (
                "previously_disclosed_confirmation",
                root / "runs/research/heritage-batuan-thermal-vs-D9-revealed-sources-20261111",
            )
        )
    by_phase = {
        (r["campaign"], r["declared_family_ordinal"]): r
        for r in theory["registered_family_loss_bounds"]
    }
    refs = {}
    for phase, ref_run in phase_runs:
        x = read_json(ref_run / "experiment.json")
        plan = x["config"]["plans"][0]
        for j, (family, fire) in enumerate(x["problem"]["fires"].items(), start=1):
            priority = fire["protocol"]["draws"][0]["priority_ids"]
            src = next(i for i in priority if i not in plan["demolished_ids"])
            refs[family] = (src, content_hash(fire["footprints"][src]), by_phase[phase, j])
    anchors = {v[0] for v in refs.values()}
    exp = read_json(run / "experiment.json")
    require(set(refs) == set(exp["problem"]["fires"]), "source family mismatch")
    choice = next(
        r for r in theory["optimized_total_construction"] if r["core_energy_ceiling"] == 0.99
    )
    (plan,) = exp["config"]["plans"]
    D = set(choice["demolished_ids"])
    require(
        plan["demolished_ids"] == choice["demolished_ids"]
        and plan["cleared_physical_cell_ids"] == choice["cleared_physical_cell_ids"],
        "arm differs from optimized candidate",
    )
    with tempfile.TemporaryDirectory(prefix="heritage-joint-loss-endpoint-") as temp:
        reports = {
            m: summarize(run, config, Path(temp) / f"{m}.json", temporal_mode=m)
            for m in ("strict", "finite_domain_closed")
        }
    strict = reports["strict"]
    conditional = reports["finite_domain_closed"]
    entries = [row for row in strict["native_checks"] if row.get("manifest")]
    require(len(entries) == len(refs) * 2, "native campaign incomplete")
    _, _, layout, inv, _, _, _, original_draws, _ = load_bound_inputs(village)
    polys = np.asarray(layout.geometry, dtype=object)
    tree = shapely.STRtree(polys)
    base_budgets = {
        (source, footprint.sha256): footprint_budget(
            layout, polys, tree, source, footprint, D, set(choice["cleared_physical_cell_ids"])
        )[0]
        for _, _, source, footprint in original_draws
    }
    original_area = layout.node_inputs.parameters.covered_area_m2.ravel()
    original_unrepresented = {b.building_id for b in inv.buildings} - {
        owner
        for group, area in zip(layout.owners, original_area, strict=True)
        if area > 0
        for owner in group
    }
    conditional_by = {
        (r["plan_id"], r["scenario_id"]): r for r in conditional["finite_domain_checks"]
    }
    outcomes = []
    source_budgets = {}
    checked = 0
    for c in entries:
        path = Path(c["manifest"])
        m = read_json(path)
        p = path.parent
        require(
            file_sha256(path) == c["manifest_sha256"]
            and m["problem_sha256"] == exp["problem_sha256"],
            "manifest changed",
        )
        for fn, digest in m["files_sha256"].items():
            require(file_sha256(p / fn) == digest, "native artifact changed")
            checked += 1
        v = read_json(p / "evaluation.json")
        raw = read_json(p / "check.json")
        fl = read_json(p / "fire-loss.json")
        ev = raw["evidence"]
        src, foot, bound = refs[ev["fire_id"]]
        require(
            ev["source_id"] == src
            and ev["source_footprint_sha256"] == foot
            and v["metrics"]["actual_source_ids"] == [src]
            and v["diagnostics"]["source_verified"],
            "source/footprint mismatch",
        )
        physical = ev["source_budget"]
        vals = [
            physical[k]
            for k in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        ]
        require(abs(vals[0] - 25) < 1e-6 and min(vals) > 0, "source physics invalid")
        require(
            np.allclose(base_budgets[src, foot], vals, atol=1e-7, rtol=1e-9),
            "native source material budget differs from original physical intersections",
        )
        if ev["fire_id"] in source_budgets:
            require(
                np.allclose(source_budgets[ev["fire_id"]], vals, atol=1e-7, rtol=1e-9),
                "support-dependent direct area or mass",
            )
        source_budgets[ev["fire_id"]] = vals
        with np.load(p / "final-state.npz") as st:
            end = float(st["time_s"])
            burn = st["burnout_time_s"].ravel()
            ignite = st["ignition_time_s"].ravel()
            spent = np.flatnonzero(np.isfinite(burn) & (burn <= end))
            ever = np.flatnonzero(np.isfinite(ignite) & (ignite <= end))
            require(np.isin(spent, ever).all(), "burnout lacks an ignition record")
        with np.load(p / "loss-basis.npz") as cb:
            after = cb["post_intervention_covered_area_m2"].ravel()
            require(np.all(after[ever] > 0), "cleared support ignited")
            A = math.fsum(after[spent])
            active_area = math.fsum(after[np.setdiff1d(ever, spent)])
        with np.load(p / "layout.npz") as lay:
            offset = lay["owner_offsets"]
            ids = lay["owner_ids"]
            original, _ = recover_original_node_owners(
                SimpleNamespace(
                    owners=tuple(tuple(ids[offset[j] : offset[j + 1]]) for j in ever),
                    component_id=lay["component_id"][ever],
                    cell_id=lay["physical_cell_id"][ever],
                    xy_m=lay["xy_m"][ever],
                ),
                inv,
                demolished_ids=D,
            )
        outside_burning = sum(not anchors.intersection(g) for g in original)
        spent_owners = [original[i] for i in np.searchsorted(ever, spent)]
        num = fl["physical_remaining_roof_annotation_counts"]
        require(
            set(num["unrepresented_original_ids"]) == original_unrepresented,
            "unrepresented original owners differ from the unsplit physical inventory",
        )
        lo = len({g[0] for g in spent_owners if len(g) == 1})
        hi = len(original_unrepresented.union(*(set(g) for g in spent_owners)))
        require(
            num["burned_out_annotation_attribution_count"] == {"lower": lo, "upper": hi},
            "saved original-owner N differs",
        )
        require(
            abs(A - fl["physical"]["all"]["cumulative_burned_out_covered_area_m2"]) < 1e-7,
            "recomputed native A differs",
        )
        cond = conditional_by[c["plan_id"], c["scenario_id"]]
        cores = v["metrics"]["core_outcomes"]
        safe = not any(b["ignited"] for b in cores)
        admiss = bool(c.get("terminal_complete") and c["status"] == "passed")
        finite = bool(cond.get("finite_domain_closed") and cond["conditional_status"] == "passed")
        if admiss or finite:
            require(active_area == 0 and safe, "terminal safe record inconsistent")
        outcomes.append(
            dict(
                campaign=bound["campaign"],
                ordinal=bound["declared_family_ordinal"],
                support_m=ev["scale_m"],
                strict_status=c["status"],
                finite_status=cond["conditional_status"],
                native_validity_reasons=c.get("invalid_reasons"),
                registry_direct_area_m2=vals[0],
                full_closed_and_core_safe=admiss,
                finite_closed_and_core_safe=finite,
                model_stop_time_s=end,
                active_area_m2=active_area,
                whole_domain_A_m2=A,
                n_original_owner_bounds=[lo, hi],
                registered_N_upper=bound["single_roof_N_owner_upper"],
                registered_A_upper_m2=bound["single_roof_A_upper_m2"],
                core_max_K=max(b["maximum_valid_surface_temperature_K"] for b in cores),
                model_protected_roofs_ignited=sum(b["ignited"] for b in cores),
                ignited_nodes_outside_nominated_group=outside_burning,
                within_declared_area_and_owner_envelopes=bound["single_roof_A_upper_m2"] >= A
                and hi <= bound["single_roof_N_owner_upper"],
                manifest_sha256=c["manifest_sha256"],
            )
        )
    outcomes.sort(key=lambda r: (r["campaign"], r["ordinal"], -r["support_m"]))
    full = len(outcomes)
    summaries = {}
    for mode, field in [
        ("strict", "full_closed_and_core_safe"),
        ("finite_domain_closed", "finite_closed_and_core_safe"),
    ]:
        keep = [x for x in outcomes if x[field]]
        valid = len(keep) == full
        summaries[mode] = {
            "closed_core_safe_count": len(keep),
            "fully_closed_feasible": valid,
            "worst_A_m2": max(x["whole_domain_A_m2"] for x in keep) if valid else None,
            "worst_N_original_owner_bounds": [
                max(x["n_original_owner_bounds"][i] for x in keep) for i in range(2)
            ]
            if valid
            else None,
        }
    cst = strict["canonical_construction_area_ledger"][0]
    require(abs(cst["canonical_C_m2"] - choice["C_m2"]) < 1e-6, "canonical area mismatch")
    return dict(
        schema_version="native-joint-protection-loss-endpoint-1",
        village=village,
        independent_theory_file_sha256=file_sha256(independent),
        source_config_sha256=file_sha256(config),
        native_problem_sha256=exp["problem_sha256"],
        native_snapshot_digest=content_hash(exp["source_sha256"]),
        verified_manifests=full,
        verified_native_artifact_hashes=checked,
        origin_footprints_retained=len(source_budgets),
        cleared_area_m2=cst["canonical_C_m2"],
        roof_union_m2=cst["roof_geometry_union_m2"],
        whole_ground_footprint_m2=cst["whole_ground_footprints_m2"],
        structural_max_registered_A_m2=theory["worst_registered_A_upper_m2"],
        structural_max_registered_N=theory["worst_registered_N_upper"],
        native_observed_maxima=summaries,
        per_case_observations=outcomes,
        all_saved_ignitions_confined_to_group=all(
            x["ignited_nodes_outside_nominated_group"] == 0 for x in outcomes
        ),
        all_observations_within_structural_bound=all(
            x["within_declared_area_and_owner_envelopes"] for x in outcomes
        ),
        counts_by_original_native_status=dict(Counter(x["strict_status"] for x in outcomes)),
        new_CA_checks=0,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("village", choices=["batuan", "pingyan"])
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    d = audit(a.village)
    d["method_sha256"] = file_sha256(__file__)
    d["receipt_sha256"] = content_hash(d)
    with a.output.open("x") as f:
        json.dump(d, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
