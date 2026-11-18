"""Freeze three Pingyan treatments before calling the unchanged confirmatory binder."""

import argparse
import json
from pathlib import Path

import prepare_heritage_independent_confirmation as original

from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / "examples/research/heritage/area-20261110"
BASE = ROOT / "examples/research/heritage/confirmation-20261110"
OUT = BASE / "pingyan-three-fixed-plans-20261111"
DOC = ROOT / "docs/results"
REG = BASE / "pingyan-registration.json"
FINAL = OUT / "final-candidates.json"
AMEND = OUT / "execution-amendment.json"
IDS = [
    "fixed-core-and-first-escape-compartment",
    "roof-seed-protected-union-ring-b0-w2.5",
    "roof-seed-protected-union-ring-b0-w15.01",
]
PRODUCER = ROOT / "runs/research/heritage-hrr-error-recovery-producer-20261111"


def freeze():
    r = original._checked_registration(REG)
    if (OUT / "original-binding").exists():
        raise ValueError("freeze must precede binding")
    joint = DOC / "heritage-total-protection-and-loss-pingyan-20261111.json"
    escape = DOC / "heritage-first-escape-compartment-pingyan-20261111.json"
    local = AREA / "pingyan-protected-union-priority-longcap-comparison-20261111.json"
    catalog = DOC / "heritage-pingyan-protected-union-20261111-catalog.json"
    optimum = next(
        row
        for row in read_json(joint)["optimized_total_construction"]
        if row["core_energy_ceiling"] == 0.99
    )
    action = read_json(escape)["model_action"]
    if not all(
        set(action[k]) == set(optimum[k]) for k in ("demolished_ids", "cleared_physical_cell_ids")
    ):
        raise ValueError("fixed 0.99 design differs from audited treatment")
    local_plans = {row["plan_id"]: row for row in read_json(local)["plans"]}
    costs = {
        row["plan_id"]: row["C_construction_action_area_m2"] for row in read_json(catalog)["plans"]
    }
    plans = [{"plan_id": IDS[0], **action}, *(local_plans[n] for n in IDS[1:])]
    cores = sorted(a["core_id"] for a in r["allocation"] if "core_id" in a)
    sources = [
        REG,
        joint,
        escape,
        local,
        catalog,
        Path(__file__),
        DOC / "heritage-total-protection-and-loss-pingyan-independent-20261111.json",
        DOC / "heritage-pingyan-local-union-strict-complete8-20261111.json",
        DOC / "heritage-pingyan-w2.5-vs-w15.01-objective-bound-20261111.json",
        DOC / "v3-hrr-error-path-regression-20261111.json",
        DOC / "v3-hrr-error-path-transport-ledger-20261111.json",
        PRODUCER / "producer-manifest.json",
    ]
    final = {
        "schema_version": original.FINAL_SCHEMA,
        "village": "pingyan",
        "frozen": True,
        "inventory_sha256": r["inventory_sha256"],
        "confirmation_registration_sha256": r["registration_sha256"],
        "core_ids": cores,
        "core_overlap_policy": "heritage_priority",
        "refinement_ids": sorted(set(cores).union(*(p["demolished_ids"] for p in plans))),
        "plans": plans,
        "canonical_design_C_m2": [optimum["C_m2"], *(costs[n] for n in IDS[1:])],
        "design_evidence_before_unblinding": [
            "fixed objective with protected-core heat bound and original-family escape bound",
            "partial strict design evidence; cost and observed loss tradeoff",
            "8/8 complete valid design outcomes",
        ],
        "source_priorities_revealed_before_final_freeze": False,
        "three_fixed_choices_independent_of_confirmation_results": True,
        "evidence_and_implementation_sha256": {str(p): file_sha256(p) for p in sources},
    }
    original.immutable_json(FINAL, final)
    amendment = {
        "schema_version": "heritage-confirmation-pre-unblinding-execution-amendment-1",
        "registration_sha256": r["registration_sha256"],
        "final_sha256": file_sha256(FINAL),
        "execution_order": IDS,
        "eligible_arms": 3,
        "independent_draws": 4,
        "near_draws": 3,
        "background_draws": 1,
        "scales_per_draw": 2,
        "observations_per_arm": 8,
        "expected_observations": 24,
        "source": "same registered draw; first retained; same 25m2 footprint for same realized "
        "owner; distinct owners compared as policies and identified as unmatched",
        "mesh": "joint roof-action and heritage union plus original per-draw source prefix",
        "acceptance": "each arm requires all eight strict native no-core-ignition and valid "
        "extinction; track peak K and worst N/A; time, edge or capacity unknown stays unknown",
        "failure": "verified strict core ignition rejects that arm; skipped cases stay missing",
        "held_source_parameters": {
            k: r[k]
            for k in (
                "seed",
                "rng",
                "rng_namespace",
                "allocation",
                "ignition_area_m2",
                "near_distance_m",
                "empty_near_pool_policy",
                "pressure_distance_m",
                "preferred_pool_policy",
                "fallback_policy",
                "anchor_policy",
                "resampling_rule",
                "confirmation_failure_rule",
                "weight_policy",
                "roof_scales_m",
                "quadrature_spacing_m",
                "max_dt_s",
            )
        },
        "original_limits": {
            "max_new_checks": 24,
            "max_total_checks": 40,
            "max_wall_time_s": 28800.0,
            "terminal_horizon_cap_s": 21600.0,
        },
        "overrides": {
            "max_new_checks": 24,
            "max_total_checks": 24,
            "max_wall_time_s": 172800.0,
            "terminal_horizon_cap_s": 172800.0,
            "execution_case_order": "plans_first",
            "execution_priority_plan_ids": IDS,
            "stop_plan_after_protection_failure": True,
            "required_solver_source_sha256": file_sha256(
                PRODUCER / "src/pyfireca/village/solver_v3.py"
            ),
        },
        "unchanged_capacity_limits": {
            "max_nodes": 500000,
            "max_radiation_edges": 60000000,
            "minimum_free_disk_GB": 5.0,
        },
        "capacity_rule": "stop on a hard resource limit; no regrid, resample or inferred outcomes",
        "design_loss_or_optimality_transferred": False,
        "source_draws_or_CA_during_freeze": 0,
    }
    original.immutable_json(AMEND, amendment)


def bind():
    final, amendment = read_json(FINAL), read_json(AMEND)
    if amendment["final_sha256"] != file_sha256(FINAL) or not all(
        file_sha256(p) == h for p, h in final["evidence_and_implementation_sha256"].items()
    ):
        raise ValueError("pre-unblinding frozen files changed")
    dest = OUT / "original-binding"
    binding = original.bind(REG, FINAL, dest)
    base = dest / "confirmation-config.json"
    config = read_json(base)
    for k, v in {**amendment["original_limits"], **amendment["unchanged_capacity_limits"]}.items():
        if config[k] != v:
            raise ValueError("original binder differs from registered numeric field: " + k)
    config.update(amendment["overrides"])
    config["additional_frozen_inputs_sha256"] = {
        **final["evidence_and_implementation_sha256"],
        **{str(p): file_sha256(p) for p in (FINAL, AMEND, base, dest / "binding-receipt.json")},
        **binding["source_protocol_files_sha256"],
    }
    config["scope"] = "independent_three_frozen_plans;no_reselection;new_mesh;strict_extinction"
    original.immutable_json(OUT / "confirmation-longcap-config.json", config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "bind"))
    args = parser.parse_args()
    (freeze if args.action == "freeze" else bind)()
    print(
        json.dumps(
            {"action": args.action, "final": file_sha256(FINAL), "amendment": file_sha256(AMEND)}
        )
    )
