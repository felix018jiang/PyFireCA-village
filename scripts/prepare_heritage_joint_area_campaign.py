"""Freeze five joint arms only after the four-family retained master validates a seed.

Selection reads immutable physical checks and geometry, never joint outcomes.
The roof area proof remains scoped to roof actions; these finite joint proposals
supply candidates to a C/N/A tradeoff archive, not a single declared winner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from math import isfinite
from pathlib import Path

from run_heritage_area_master import read_complete
from run_heritage_area_study import observation_status
from run_heritage_joint_comparison import read_master_reference, validate_config

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def immutable_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"immutable joint selection artifact already differs: {path}")
        return
    with path.open("xb") as stream:
        stream.write(data)


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def validated_seed(master_path, search_path):
    master = read_json(master_path)
    experiment_path = search_path.with_name("experiment.json")
    experiment = read_json(experiment_path)
    if master.get("source_policy") != "area_bounded_retained_sources":
        raise ValueError(
            "production joint seed requires the retained-source master, never fixed-source UB"
        )
    if experiment.get("config") != master or experiment.get("config_sha256") != file_sha256(
        master_path
    ):
        raise ValueError("master configuration differs from its frozen experiment")
    if content_hash(experiment["problem"]) != experiment["problem_sha256"]:
        raise ValueError("master problem fingerprint mismatch")
    if not search_path.is_file():
        raise ValueError(
            "retained master has no fully validated seed yet;production joint not prepared"
        )
    search_bytes = search_path.read_bytes()
    search = json.loads(search_bytes)
    if (
        search.get("problem_sha256") != experiment["problem_sha256"]
        or search.get("source_policy") != master["source_policy"]
    ):
        raise ValueError("search belongs to a different retained master problem")
    best = search.get("best_ids")
    matches = [
        row for row in search.get("verified_feasible_plans", []) if row["demolished_ids"] == best
    ]
    if best is None or len(matches) != 1:
        raise ValueError(
            "retained master has no fully validated seed yet;production joint not prepared"
        )
    if not best:
        raise ValueError(
            "five-arm roof-replacement campaign requires a nonempty validated roof seed"
        )
    scenarios = [
        f"{fire['fire_id']}-h{scale:g}"
        for fire in master["source_fires"]
        for scale in master["roof_scales_m"]
    ]
    if (
        len(master["source_fires"]) != 4
        or tuple(master["roof_scales_m"]) != (1.0, 0.5)
        or master["horizon_s"] != 3600
    ):
        raise ValueError(
            "production joint campaign requires four retained families and 3600s dual support"
        )
    if set(search.get("scenario_ids", [])) != set(scenarios):
        raise ValueError("retained feasible seed lacks the complete eight-scenario declaration")
    checks = []
    for scenario in scenarios:
        folder = search_path.parent / "evaluations" / content_hash([best, scenario])
        check = read_complete(folder, experiment["problem_sha256"], ids=best, scenario=scenario)
        valid, reached, _, record = observation_status(
            folder, {"status": check.status, "evidence": check.evidence}, 3600
        )
        if (
            check.status != "passed"
            or not valid
            or not reached
            or record.get("metrics", {}).get("protection_status") != "passed"
            or record.get("metrics", {}).get("core_joint_failure_observed") is not False
        ):
            raise ValueError(
                "roof seed is not fully protected and observed under every retained scenario"
            )
        checks.append(
            {
                "scenario_id": scenario,
                "status": check.status,
                "complete_manifest_sha256": file_sha256(folder / "complete.json"),
                "evidence": check.evidence,
            }
        )
    return master, experiment, search, search_bytes, matches[0], checks


def prepare(
    village,
    *,
    master_path=None,
    search_path=None,
    proposals_path=None,
    destination=None,
    terminal_fire_verification=False,
    terminal_horizon_cap_s=None,
):
    if not isinstance(terminal_fire_verification, bool):
        raise ValueError("terminal_fire_verification must be an explicit boolean")
    if terminal_fire_verification:
        if (
            isinstance(terminal_horizon_cap_s, bool)
            or not isinstance(terminal_horizon_cap_s, (int, float))
            or not isfinite(terminal_horizon_cap_s)
            or terminal_horizon_cap_s < 3600
        ):
            raise ValueError("terminal verification requires an explicit finite cap >= 3600 s")
    elif terminal_horizon_cap_s is not None:
        raise ValueError("terminal cap requires terminal_fire_verification")
    master_path = Path(
        master_path
        or ROOT / f"examples/research/heritage/area-20261110/{village}-retained-area-master.json"
    ).resolve()
    search_path = Path(
        search_path or ROOT / f"runs/research/heritage-retained-area-{village}-20261110/search.json"
    ).resolve()
    proposals_path = Path(
        proposals_path or ROOT / "docs/results/heritage-joint-proposals-20261110.json"
    ).resolve()
    destination = Path(
        destination
        or ROOT / f"examples/research/heritage/area-20261110/{village}-joint-comparison.json"
    ).resolve()
    master, experiment, search, search_bytes, feasible, checks = validated_seed(
        master_path, search_path
    )
    best = list(search["best_ids"])
    study = (master_path.parent / master["study_path"]).resolve()
    bundle, _, inventory = load_study(study)
    if inventory.sha256 != experiment["problem"]["inventory_sha256"]:
        raise ValueError("joint seed geometry inventory differs from the retained master")
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=master["max_nodes"],
        core_overlap_policy=master["core_overlap_policy"],
    )
    area = build_cleared_area_function(
        base.owners, [g.area for g in base.geometry], area_basis="physical_roof_geometry_m2"
    )
    if abs(area(best) - feasible["cleared_area_m2"]) > 1e-6:
        raise ValueError("retained feasible row uses a different physical roof cost")
    largest = min(best, key=lambda i: (-(area(best) - area(set(best) - {i})), i))
    proposal_bytes = proposals_path.read_bytes()
    proposal = next(v for v in json.loads(proposal_bytes)["villages"] if v["village_id"] == village)
    ordered = proposal["plans"]
    if len(ordered) != 3:
        raise ValueError("exactly three declared vegetation proposals are required")
    plans = [{"plan_id": "roof-seed", "demolished_ids": best, "cleared_physical_cell_ids": []}]
    plans.extend(
        {
            "plan_id": row["plan_id"],
            "demolished_ids": best,
            "cleared_physical_cell_ids": row["cleared_physical_cell_ids"],
        }
        for row in ordered
    )
    connection = next(row for row in ordered if "connection" in row["plan_id"])
    plans.append(
        {
            "plan_id": "replace-largest-roof-by-vegetation-connection",
            "demolished_ids": sorted(set(best) - {largest}),
            "cleared_physical_cell_ids": connection["cleared_physical_cell_ids"],
        }
    )
    search_copy = destination.with_suffix(".seed-search.json")
    proposal_copy = destination.with_suffix(".vegetation-proposals.json")
    receipt_path = destination.with_suffix(".seed-receipt.json")
    receipt = {
        "schema_version": "joint-retained-roof-seed-receipt-1",
        "village_id": village,
        "master_config_path": str(master_path),
        "master_config_sha256": file_sha256(master_path),
        "master_experiment_path": str(search_path.with_name("experiment.json")),
        "master_experiment_sha256": file_sha256(search_path.with_name("experiment.json")),
        "master_problem_sha256": experiment["problem_sha256"],
        "source_search_path": str(search_path),
        "search_snapshot_path": str(search_copy),
        "search_bytes_sha256": hashlib.sha256(search_bytes).hexdigest(),
        "verified_feasible_row": feasible,
        "all_eight_retained_checks": checks,
        "seed_ids": best,
        "joint_outcomes_read": False,
        "new_CA_checks": 0,
        "selection": "best_fully_validated_retained_roof_seed_at_captured_search_bytes",
        "roof_area_optimality_proven": search.get("global_minimum_area_proven", False),
        "global_joint_optimality_proven": False,
    }
    config = {
        k: master[k]
        for k in (
            "roof_scales_m",
            "quadrature_spacing_m",
            "horizon_s",
            "max_dt_s",
            "max_nodes",
            "max_radiation_edges",
            "minimum_free_disk_GB",
            "core_overlap_policy",
        )
    }
    config.update(
        schema_version="heritage-joint-comparison-1",
        village_id=village,
        study_path=str(study),
        source_policy="retained_sources",
        source_fires=[
            {**f, "protocol_path": str((master_path.parent / f["protocol_path"]).resolve())}
            for f in master["source_fires"]
        ],
        source_area_limit_m2=master["source_area_limit_m2"],
        source_area_tolerance_m2=master.get("source_area_tolerance_m2", 0),
        candidate_ids=experiment["candidate_ids"],
        refinement_ids=list(master["refinement_ids"]),
        reference_master_experiment_path=str(search_path.with_name("experiment.json")),
        reference_master_experiment_sha256=receipt["master_experiment_sha256"],
        reference_master_config_path=str(master_path),
        reference_master_config_sha256=receipt["master_config_sha256"],
        roof_seed_receipt_path=str(receipt_path),
        roof_seed_receipt_sha256=hashlib.sha256(json_bytes(receipt)).hexdigest(),
        roof_seed_search_snapshot_path=str(search_copy),
        roof_seed_search_sha256=receipt["search_bytes_sha256"],
        vegetation_proposals_path=str(proposal_copy),
        vegetation_proposals_sha256=hashlib.sha256(proposal_bytes).hexdigest(),
        source_inventory_sha256=inventory.sha256,
        plans=plans,
        checkpoints_s=[1200.0, 2400.0, 3600.0],
        max_new_checks=40,
        max_total_checks=40,
        max_wall_time_s=14400.0,
        new_independent_fire_draw_count=0,
        roof_replacement_rule={
            "removed_id": largest,
            "saved_roof_geometry_area_m2": area(best) - area(set(best) - {largest}),
            "selection": "largest_marginal_physical_roof_geometry;ID_tie_break",
        },
        seed_reverification="same_master_mesh_roof_only_arm_rechecked;no_new_refinement_for_selected_building",
        scope="finite_C_N_A_tradeoff_candidates;roof_area_anchor_scope_not_joint_global_certificate",
    )
    if terminal_fire_verification:
        config.update(
            terminal_fire_verification=True,
            terminal_horizon_cap_s=float(terminal_horizon_cap_s),
            max_wall_time_s=28800.0,
        )
    validate_config(config)
    read_master_reference(destination, config)
    immutable_bytes(search_copy, search_bytes)
    immutable_bytes(proposal_copy, proposal_bytes)
    immutable_bytes(receipt_path, json_bytes(receipt))
    immutable_bytes(destination, json_bytes(config))
    return {
        "config": str(destination),
        "roof_seed": best,
        "replaced_roof": largest,
        "seed_receipt": str(receipt_path),
        "planned_CA_checks": 40,
        "new_CA_checks": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--village", choices=("batuan", "pingyan"), required=True)
    parser.add_argument("--master-config", type=Path)
    parser.add_argument("--search", type=Path)
    parser.add_argument("--proposals", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--terminal-fire-verification", action="store_true")
    parser.add_argument("--terminal-horizon-cap-s", type=float)
    args = parser.parse_args()
    print(
        prepare(
            args.village,
            master_path=args.master_config,
            search_path=args.search,
            proposals_path=args.proposals,
            destination=args.output,
            terminal_fire_verification=args.terminal_fire_verification,
            terminal_horizon_cap_s=args.terminal_horizon_cap_s,
        )
    )


if __name__ == "__main__":
    main()
