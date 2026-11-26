"""Read-only audit of the frozen two-village follow-up; never runs the CA.

During execution, atomically completed evaluations are audited and absent ones
remain pending. --require-complete requires all 24 full-horizon evaluations.
Only one evaluation's clock/layout arrays are loaded at a time. Native replay
is a separate operation; this audit does not substitute for that receipt.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from math import fsum
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study
from pyfireca.village.state import VillageCellState

ROOT = Path(__file__).resolve().parents[1]
VILLAGES = ("batuan", "pingyan")
REQUIRED_FILES = {"evaluation.json", "check.json", "curves.json", "final-state.npz", "layout.npz"}
AREA_FIELDS = (
    "demolished_footprint_area_m2",
    "cleared_roof_union_area_m2",
    "retained_shared_roof_area_m2",
)
GEOMETRY_AREA_FIELDS = (
    "demolished_roof_union_area_m2",
    "internal_demolished_overlap_m2",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, *, atol=1e-6, rtol=1e-9):
    return bool(np.isclose(actual, expected, atol=atol, rtol=rtol))


def array_hash(value):
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str((array.shape, array.dtype.str)).encode())
    digest.update(array.tobytes())
    return digest.hexdigest()


def geometry_area_ledger(geometries, demolished_ids):
    """Use original roof polygons, independently of the solver's thermal ledger.

    Nominal cost sums building footprints. Physical removal uses their union;
    overlapping removed buildings therefore contribute a separate internal
    duplicate area. Pairwise overlaps are explanatory, not summed: three-way
    intersections would otherwise overcount the duplicate area.
    """
    ids = sorted(demolished_ids)
    require(len(ids) == len(set(ids)), "duplicate demolition IDs")
    require(set(ids) <= geometries.keys(), "demolition ID absent from geometry")
    demolished = unary_union([geometries[k] for k in ids])
    retained = unary_union([g for k, g in geometries.items() if k not in ids])
    nominal = fsum(geometries[k].area for k in ids)
    duplicate = nominal - demolished.area
    require(duplicate >= -1e-6, "demolition union exceeds summed footprints")
    area = {
        "demolished_footprint_area_m2": nominal,
        "demolished_roof_union_area_m2": float(demolished.area),
        "internal_demolished_overlap_m2": max(0.0, duplicate),
        "cleared_roof_union_area_m2": float(demolished.difference(retained).area),
        "retained_shared_roof_area_m2": float(demolished.intersection(retained).area),
    }
    require(
        close(
            area["demolished_roof_union_area_m2"],
            area["cleared_roof_union_area_m2"] + area["retained_shared_roof_area_m2"],
        ),
        "independent polygon union does not partition into cleared/shared area",
    )
    require(
        close(nominal, demolished.area + area["internal_demolished_overlap_m2"]),
        "independent nominal area does not equal union plus internal overlap",
    )
    pairs = []
    for left, right in combinations(ids, 2):
        overlap = float(geometries[left].intersection(geometries[right]).area)
        if overlap > 1e-6:
            pairs.append({"building_ids": [left, right], "overlap_area_m2": overlap})
    return {"area_m2": area, "overlapping_demolished_pairs": pairs}


def audit_geometry_fixture():
    """A removed pair overlaps while one retained roof shares part of its union."""
    geometries = {
        "A": box(0, 0, 2, 2),
        "B": box(1, 0, 3, 2),
        "retained": box(2, 1, 3, 3),
    }
    ledger = geometry_area_ledger(geometries, ["A", "B"])
    expected = {
        "demolished_footprint_area_m2": 8.0,
        "demolished_roof_union_area_m2": 6.0,
        "internal_demolished_overlap_m2": 2.0,
        "cleared_roof_union_area_m2": 5.0,
        "retained_shared_roof_area_m2": 1.0,
    }
    require(ledger["area_m2"] == expected, "overlapping-demolition geometry fixture failed")
    single = geometry_area_ledger(geometries, ["A"])["area_m2"]
    require(single["internal_demolished_overlap_m2"] == 0, "single-removal fixture failed")
    return {"status": "passed", "overlapping_demolition": ledger, "single_demolition": single}


def audit_inputs(village, run):
    experiment = read_json(run / "experiment.json")
    config_path = (
        ROOT / f"examples/research/heritage/followup-20261103/{village}-fixed-comparison.json"
    )
    config = read_json(config_path)
    problem = experiment["problem"]
    require(config == experiment["config"], "live configuration differs from frozen experiment")
    require(
        file_sha256(config_path) == experiment["config_sha256"], "configuration byte hash changed"
    )
    require(
        read_json(run / "input-snapshot/config.json") == config, "configuration snapshot changed"
    )
    require(content_hash(problem) == experiment["problem_sha256"], "physical problem hash mismatch")
    require(
        content_hash(experiment["source_sha256"]) == problem["code_version"],
        "producer fingerprint mismatch",
    )
    require(config["horizon_s"] == problem["horizon_s"] == 3600, "unexpected observation horizon")
    require(config["checkpoints_s"] == [1200, 2400, 3600], "unexpected observation checkpoints")
    require(config["roof_scales_m"] == problem["scales_m"] == [1.0, 0.5], "dual scales changed")
    require(problem["early_stop"] == "full_horizon", "core-ignition early stop enabled")
    require(
        len(config["source_fires"]) == 2 and len(config["plans"]) == 3,
        "expected two fires and three arms",
    )
    require(
        experiment["planned_CA_checks"] == config["max_total_checks"] == 12,
        "per-village check budget changed",
    )
    require(
        not experiment["optimization"] and not experiment["independent_new_fire_draws"],
        "experiment scope changed",
    )
    require(read_json(run / "preflight.json")["all_passed"], "capacity preflight not passed")
    selection = Path(config["position_selection_file"])
    require(
        file_sha256(selection) == config["position_selection_sha256"],
        "frozen geometry selection changed",
    )

    snapshot_matches = []
    root_matches = []
    for relative, digest in experiment["source_sha256"].items():
        require(
            file_sha256(run / "source-snapshot" / relative) == digest,
            f"source snapshot changed: {relative}",
        )
        snapshot_matches.append(relative)
        require(
            file_sha256(ROOT / relative) == digest,
            f"current producer differs from frozen snapshot: {relative}",
        )
        root_matches.append(relative)
    for path, digest in experiment["source_references_sha256"].items():
        require(file_sha256(path) == digest, f"frozen source input changed: {path}")

    study_path = (config_path.parent / config["study_path"]).resolve()
    require(read_json(study_path) == experiment["study"], "study input changed")
    require(file_sha256(study_path) == experiment["study_sha256"], "study byte hash changed")
    require(
        read_json(run / "input-snapshot/study.json") == experiment["study"],
        "study snapshot changed",
    )
    bundle, transport, inventory = load_study(study_path)
    require(
        request_fingerprint(bundle.request) == problem["case_sha256"],
        "parent physical case changed",
    )
    require(inventory.sha256 == problem["inventory_sha256"], "parent inventory changed")
    saved_inventory = read_json(run / "inventory.json")
    require(content_hash(saved_inventory) == inventory.sha256, "saved inventory changed")
    geometries = {
        item["building_id"]: unary_union(
            [Polygon(p["exterior"], p.get("holes", [])) for p in item["polygons"]]
        )
        for item in saved_inventory["buildings"]
    }
    geometry_ledgers = {
        plan["plan_id"]: geometry_area_ledger(geometries, plan["demolished_ids"])
        for plan in config["plans"]
    }
    for plan_id, ledger in geometry_ledgers.items():
        # These are independently measured facts about the frozen selection,
        # not tolerances used to reconcile the physical ledger.
        expected_overlap = (
            7.89635971347545 if village == "pingyan" and plan_id == "area-matched-position" else 0.0
        )
        require(
            close(ledger["area_m2"]["internal_demolished_overlap_m2"], expected_overlap),
            f"frozen removal geometry internal overlap changed: {plan_id}",
        )
    source_audits = {item["fire_id"]: item for item in config["source_audit"]}
    source_rows = []
    for fire_id, fire in problem["fires"].items():
        protocol = fire["protocol"]
        require(
            read_json(run / f"input-snapshot/{fire_id}.json") == protocol,
            "source protocol snapshot changed",
        )
        require(
            len(protocol["draws"]) == 1 and len(fire["footprints"]) == 1,
            "fixed fire is not singleton",
        )
        draw = protocol["draws"][0]
        source_id = draw["baseline_source_id"]
        require(draw["priority_ids"] == [source_id], "fallback source unexpectedly enabled")
        require(
            all(source_id not in p["demolished_ids"] for p in config["plans"]),
            "a comparison arm removes the source",
        )
        realization = realize_sources(protocol, inventory)[0]
        require(
            realization["status"] == "fire" and not realization["source_replaced"],
            "fixed source did not realize",
        )
        footprint_hash = realization["footprint"].sha256
        require(
            footprint_hash == content_hash(fire["footprints"][source_id]),
            "saved physical footprint differs",
        )
        require(
            footprint_hash == source_audits[fire_id]["footprint_sha256"],
            "historical footprint changed",
        )
        require(
            close(realization["footprint"].geometry.area, 25.0),
            "source polygon is not 25 square metres",
        )
        historical_path = ROOT / source_audits[fire_id]["protocol_path"]
        require(
            file_sha256(historical_path) == source_audits[fire_id]["parent_source_file_sha256"],
            "historical source archive changed",
        )
        source_rows.append(
            {"fire_id": fire_id, "source_id": source_id, "footprint_sha256": footprint_hash}
        )
    del bundle, transport, inventory, geometries, saved_inventory
    gc.collect()
    return experiment, {
        "status": "passed",
        "problem_sha256": experiment["problem_sha256"],
        "source_snapshot_files_checked": len(snapshot_matches),
        "current_root_files_match_snapshot": len(root_matches),
        "parent_physics_and_inventory_verified": True,
        "same_historical_polygons_verified": source_rows,
        "geometry_area_ledgers": geometry_ledgers,
    }


def verify_manifest(folder, expected_problem, ids, scenario):
    manifest = read_json(folder / "complete.json")
    require(manifest["problem_sha256"] == expected_problem, "evaluation physical problem differs")
    require(
        manifest["demolished_ids"] == ids and manifest["scenario_id"] == scenario,
        "evaluation identity differs",
    )
    files = manifest["files_sha256"]
    require(files.keys() >= REQUIRED_FILES, "completion manifest misses required artifacts")
    for relative, digest in files.items():
        path = folder / relative
        require(path.resolve().is_relative_to(folder.resolve()), "manifest contains escaping path")
        require(
            path.is_file() and file_sha256(path) == digest,
            f"artifact missing or changed: {relative}",
        )
    return len(files)


def audit_arrays(folder, record, curves, checkpoints):
    """Reconstruct clocks and area accounting independently of the runner."""
    with np.load(folder / "final-state.npz", allow_pickle=False) as state_file:
        ignition = state_file["ignition_time_s"].ravel()
        burnout = state_file["burnout_time_s"].ravel()
        state = state_file["state"].ravel()
        direct = state_file["ignition_cause"].ravel() == 1
        final_time = float(state_file["time_s"])
    with np.load(folder / "layout.npz", allow_pickle=False) as layout_file:
        area = layout_file["covered_area_m2"].ravel()
        offsets = layout_file["owner_offsets"]
        owners = layout_file["owner_ids"]
        xy = layout_file["xy_m"]
        geometry_hashes = {
            key: array_hash(layout_file[key])
            for key in ("physical_cell_id", "xy_m", "component_id", "area_fraction")
        }
    require(
        ignition.shape == burnout.shape == state.shape == area.shape,
        "physical node arrays disagree",
    )
    require(
        len(offsets) == len(state) + 1 and offsets[0] == 0 and offsets[-1] == len(owners),
        "owner offsets disagree",
    )
    require(np.all(np.diff(offsets) >= 0), "owner offsets not monotone")
    require(np.isfinite(area).all() and (area >= 0).all(), "invalid physical covered area")
    require(
        direct.any() and np.all(ignition[direct] == 0), "direct source clocks are not time zero"
    )
    require(close(final_time, 3600), "clock arrays are truncated")
    require(close(final_time, curves["final_time_s"]), "curve observation time differs")
    roofs = np.diff(offsets) > 0
    require(
        [r["time_s"] for r in curves["checkpoints"]] == checkpoints, "checkpoint sequence changed"
    )
    previous_count = -1
    previous_area = -1.0
    for row in curves["checkpoints"]:
        t = row["time_s"]
        ignited = np.isfinite(ignition) & (ignition <= t)
        active = ignited & (np.isnan(burnout) | np.isposinf(burnout) | (burnout > t))
        require(row["valid_observation"] and final_time >= t, "invalid or censored checkpoint")
        require(
            row["ignited_physical_node_count"] == int(ignited.sum()),
            "cumulative ignition count differs from clocks",
        )
        require(
            row["active_burning_node_count"] == int(active.sum()),
            "active combustion count differs from clocks",
        )
        require(
            close(row["cumulative_ignited_covered_area_m2"], area[ignited].sum()),
            "cumulative physical area differs",
        )
        require(
            close(row["cumulative_ignited_roof_covered_area_m2"], area[ignited & roofs].sum()),
            "roof area differs or owners counted twice",
        )
        require(
            close(row["active_burning_covered_area_m2"], area[active].sum()),
            "active combustion area differs",
        )
        require(
            row["ignited_physical_node_count"] >= previous_count,
            "cumulative ignition count decreased",
        )
        require(
            row["cumulative_ignited_covered_area_m2"] >= previous_area - 1e-6,
            "cumulative ignition area decreased",
        )
        previous_count = row["ignited_physical_node_count"]
        previous_area = row["cumulative_ignited_covered_area_m2"]
    terminal_active = np.isfinite(ignition) & (
        np.isnan(burnout) | np.isposinf(burnout) | (burnout > final_time)
    )
    terminal_state = state == int(VillageCellState.BURNING)
    require(
        np.array_equal(terminal_active, terminal_state),
        "terminal burnout-clock reconstruction differs from BURNING state",
    )
    require(
        int(terminal_state.sum()) == curves["final_active_burning_node_count"],
        "terminal burning summary differs",
    )

    owner_nodes = np.repeat(np.arange(len(state)), np.diff(offsets))
    first = {}
    for identifier, when in zip(owners, ignition[owner_nodes], strict=True):
        if np.isfinite(when):
            first[str(identifier)] = min(first.get(str(identifier), float("inf")), float(when))
    require(
        first
        == record["metrics"]["building_first_ignitions"]
        == curves["building_first_ignitions"],
        "building ignition clocks differ",
    )
    ordinary = set(record["metrics"]["retained_ordinary_ids"])
    actual_sources = set(record["metrics"]["actual_source_ids"])
    for row in curves["checkpoints"]:
        expected_ordinary = sorted(i for i in ordinary if i in first and first[i] <= row["time_s"])
        require(
            row["ignited_retained_ordinary_ids"] == expected_ordinary,
            "ordinary-building checkpoint differs",
        )
        require(
            row["ignited_secondary_ordinary_ids"]
            == sorted(set(expected_ordinary) - actual_sources),
            "secondary building checkpoint differs",
        )
        for outcome in row["core_outcomes"]:
            when = first.get(outcome["building_id"])
            ignited = when is not None and when <= row["time_s"]
            require(outcome["ignited"] == ignited, "core checkpoint differs from owner clocks")
            require(
                outcome["first_ignition_s"] == (when if ignited else None),
                "core ignition time differs",
            )
        require(
            row["core_joint_failure"] == any(o["ignited"] for o in row["core_outcomes"]),
            "joint-core event differs",
        )
    return {
        "physical_nodes": len(state),
        "final_active_burning_nodes": int(terminal_state.sum()),
        "geometry_hashes": geometry_hashes,
        "direct_source_covered_area_m2": float(area[direct].sum()),
        "scenario_mapping": {
            "scenario_id": record["scenario_id"],
            "ignitions": [{"point_m": p.tolist(), "time_s": 0.0} for p in xy[direct]],
            "horizon_s": record["horizon_s"],
        },
    }


def audit_evaluation(folder, experiment, fire_id, scale, plan, geometry_ledger):
    config, problem = experiment["config"], experiment["problem"]
    ids = sorted(plan["demolished_ids"])
    scenario = f"{fire_id}-h{scale:g}"
    file_count = verify_manifest(folder, experiment["problem_sha256"], ids, scenario)
    record = read_json(folder / "evaluation.json")
    check = read_json(folder / "check.json")
    curves = read_json(folder / "curves.json")
    diagnostics, evidence = record["diagnostics"], check["evidence"]
    intervention, metrics = record["intervention"], record["metrics"]
    expected_source = problem["fires"][fire_id]["protocol"]["draws"][0]["baseline_source_id"]
    require(
        check["status"] in {"passed", "failed"} and check["status"] == metrics["protection_status"],
        "evaluation is invalid or status disagrees",
    )
    require(record["case_sha256"] == problem["case_sha256"], "evaluation parent case changed")
    require(
        record["scenario_id"] == scenario and record["horizon_s"] == 3600,
        "scenario/horizon differs",
    )
    require(
        diagnostics["inventory_sha256"] == problem["inventory_sha256"],
        "evaluation inventory changed",
    )
    require(
        diagnostics["reached_requested_horizon"] and evidence["reached_horizon"],
        "evaluation did not reach full horizon",
    )
    require(
        close(diagnostics["simulated_until_s"], 3600)
        and close(evidence["simulated_until_s"], 3600),
        "evaluation observation truncated",
    )
    require(
        not diagnostics["invalid_reasons"] and not evidence["invalid_reasons"],
        "physical validity check failed",
    )
    require(
        diagnostics["source_verified"] and diagnostics["parent_case_unchanged"],
        "source or parent validation failed",
    )
    require(not diagnostics["stop_at_first_core_ignition"], "evaluation used first-core early stop")
    require(
        not evidence["source_replaced"] and evidence["source_id"] == expected_source,
        "source replaced or changed",
    )
    require(
        metrics["actual_source_ids"] == intervention["actual_source_ids"] == [expected_source],
        "source identity differs",
    )
    require(
        expected_source not in ids and intervention["demolished_ids"] == ids,
        "illegal source removal or changed action",
    )
    require(intervention["demolished_count"] == len(ids), "demolition count differs")
    require(
        intervention["retained_component_parameters_unchanged"],
        "retained material parameters changed",
    )
    require(evidence["scale_m"] == scale, "spatial scale differs")
    require(
        evidence["evaluation_sha256"] == record["evaluation_sha256"],
        "evaluation linkage hash differs",
    )
    arrays = audit_arrays(folder, record, curves, config["checkpoints_s"])
    require(
        close(
            arrays["direct_source_covered_area_m2"], evidence["source_budget"]["covered_area_m2"]
        ),
        "direct source physical area differs from energy budget",
    )
    conditions = content_hash(
        {
            "scenario": arrays.pop("scenario_mapping"),
            "transport": problem["transport_parameters"],
            "parent": problem["case_sha256"],
            "layout": diagnostics["layout_sha256"],
            "source_policy": problem["source_policy"],
            "radiation_method": diagnostics["radiation_method"],
            "code": problem["code_version"],
        }
    )
    require(
        conditions == diagnostics["source_conditions_sha256"],
        "evaluation source/physics/code fingerprint differs",
    )
    plan_mapping = {
        k: intervention[k]
        for k in ("plan_id", "demolished_ids", "ground_policy", "ground_evidence", "overlap_policy")
    }
    signature = content_hash(
        {
            "conditions": conditions,
            "plan": plan_mapping,
            "request": diagnostics["physical_request_sha256"],
            "early_stop": False,
        }
    )
    require(signature == record["evaluation_sha256"], "evaluation physical signature mismatch")
    resolution = diagnostics["roof_resolution_protocol"]
    require(
        resolution["refined_ids"] == problem["fires"][fire_id]["refined_ids"],
        "refinement set differs",
    )
    require(len(resolution["footprints"]) == 1, "multiple physical ignition polygons")
    require(
        content_hash(resolution["footprints"][0])
        == content_hash(problem["fires"][fire_id]["footprints"][expected_source]),
        "evaluation ignition polygon changed",
    )
    require(
        all(
            abs(value) <= diagnostics["energy_tolerance_J"]
            for value in diagnostics["energy_closure_errors_J"].values()
        ),
        "energy closure exceeded tolerance",
    )
    area = {k: float(intervention[k]) for k in AREA_FIELDS}
    require(all(np.isfinite(v) and v >= 0 for v in area.values()), "invalid demolition area")
    geometry_area = geometry_ledger["area_m2"]
    for key in AREA_FIELDS:
        require(close(area[key], geometry_area[key]), f"physical/polygon area differs: {key}")
    area.update({key: geometry_area[key] for key in GEOMETRY_AREA_FIELDS})
    require(
        close(
            area["demolished_roof_union_area_m2"],
            area["cleared_roof_union_area_m2"] + area["retained_shared_roof_area_m2"],
        ),
        "demolition polygon union does not equal cleared plus retained-shared area",
    )
    require(
        close(
            area["demolished_footprint_area_m2"],
            area["demolished_roof_union_area_m2"] + area["internal_demolished_overlap_m2"],
        ),
        "nominal area does not equal demolition union plus internal duplicate area",
    )
    return {
        "status": "passed",
        "fire_id": fire_id,
        "scale_m": scale,
        "plan_id": plan["plan_id"],
        "scenario_id": scenario,
        "demolished_ids": ids,
        "source_id": expected_source,
        "source_budget": evidence["source_budget"],
        "layout_sha256": diagnostics["layout_sha256"],
        "source_conditions_sha256": conditions,
        "intervention_area_m2": area,
        "result_status": check["status"],
        "core_ignited": {o["building_id"]: o["ignited"] for o in metrics["core_outcomes"]},
        "manifest_files_verified": file_count,
        **arrays,
    }


def audit_run(village, run):
    result = {
        "village": village,
        "run": str(run),
        "errors": [],
        "pending": [],
        "evaluations": [],
        "cross_arm_checks": [],
        "area_checks": [],
        "dual_scale_checks": [],
    }
    try:
        experiment, inputs = audit_inputs(village, run)
        result["inputs"] = inputs
    except Exception as exc:
        result["errors"].append({"stage": "inputs", "message": str(exc)})
        return result
    config = experiment["config"]
    # Snapshot completed names once: evaluations published later await the next audit.
    completed = {p.parent.name for p in (run / "evaluations").glob("*/complete.json")}
    expected_names = set()
    for fire_id in experiment["problem"]["fires"]:
        for scale in config["roof_scales_m"]:
            scenario = f"{fire_id}-h{scale:g}"
            for plan in config["plans"]:
                ids = sorted(plan["demolished_ids"])
                name = content_hash([ids, scenario])
                expected_names.add(name)
                if name not in completed:
                    result["pending"].append({"scenario_id": scenario, "plan_id": plan["plan_id"]})
                    continue
                try:
                    result["evaluations"].append(
                        audit_evaluation(
                            run / "evaluations" / name,
                            experiment,
                            fire_id,
                            scale,
                            plan,
                            inputs["geometry_area_ledgers"][plan["plan_id"]],
                        )
                    )
                except Exception as exc:
                    result["errors"].append(
                        {
                            "stage": "evaluation",
                            "scenario_id": scenario,
                            "plan_id": plan["plan_id"],
                            "message": str(exc),
                        }
                    )
    if completed - expected_names:
        result["errors"].append(
            {
                "stage": "coverage",
                "message": "unexpected completed evaluation folders",
                "folders": sorted(completed - expected_names),
            }
        )
    by_scenario, by_plan, by_fire_plan = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in result["evaluations"]:
        by_scenario[row["scenario_id"]].append(row)
        by_plan[row["plan_id"]].append(row)
        by_fire_plan[row["fire_id"], row["plan_id"]].append(row)
    for scenario, rows in by_scenario.items():
        try:
            first = rows[0]
            for row in rows[1:]:
                for key in (
                    "source_id",
                    "layout_sha256",
                    "source_conditions_sha256",
                    "geometry_hashes",
                ):
                    require(row[key] == first[key], f"cross-arm {key} differs")
                require(
                    row["source_budget"].keys() == first["source_budget"].keys(),
                    "source budget fields differ",
                )
                for key, value in row["source_budget"].items():
                    require(
                        close(value, first["source_budget"][key]),
                        f"cross-arm source budget differs: {key}",
                    )
            result["cross_arm_checks"].append(
                {
                    "scenario_id": scenario,
                    "status": "passed" if len(rows) == 3 else "pending",
                    "arms_checked": len(rows),
                }
            )
        except Exception as exc:
            result["errors"].append(
                {"stage": "cross_arm", "scenario_id": scenario, "message": str(exc)}
            )
    for plan, rows in by_plan.items():
        try:
            for row in rows[1:]:
                for key in AREA_FIELDS + GEOMETRY_AREA_FIELDS:
                    require(
                        close(
                            row["intervention_area_m2"][key], rows[0]["intervention_area_m2"][key]
                        ),
                        f"demolition area changes across fire/scale: {key}",
                    )
            result["area_checks"].append(
                {
                    "plan_id": plan,
                    "status": "passed" if len(rows) == 4 else "pending",
                    "scenarios_checked": len(rows),
                    "area_m2": rows[0]["intervention_area_m2"],
                }
            )
        except Exception as exc:
            result["errors"].append(
                {"stage": "area_consistency", "plan_id": plan, "message": str(exc)}
            )
    for (fire, plan), rows in by_fire_plan.items():
        scales = {row["scale_m"] for row in rows}
        result["dual_scale_checks"].append(
            {
                "fire_id": fire,
                "plan_id": plan,
                "status": "passed" if scales == {1.0, 0.5} else "pending",
                "scales_checked": sorted(scales),
                "joint_core_failure_over_scales": any(
                    any(row["core_ignited"].values()) for row in rows
                )
                if scales == {1.0, 0.5}
                else None,
                "independent_fire_draw_count": 1,
                "new_independent_fire_draw_count": 0,
            }
        )
    budget = read_json(run / "execution-budget.json")
    result["execution_budget"] = {
        "elapsed_s": budget["elapsed_s"],
        "active": budget["active"] is not None,
        "declared_soft_limit_s": config["max_wall_time_s"],
        "policy": experiment["wall_budget_policy"],
    }
    result["completed_artifacts_seen"] = len(completed)
    result["planned_CA_checks"] = 12
    result["checked_CA_evaluations"] = len(result["evaluations"])
    result["complete"] = (
        not result["pending"] and not result["errors"] and len(result["evaluations"]) == 12
    )
    result["all_available_checks_passed"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/results/heritage-followup-20261103/audit.json"
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    geometry_fixture = audit_geometry_fixture()
    villages = [
        audit_run(village, ROOT / f"runs/research/heritage-followup-{village}-20261103")
        for village in VILLAGES
    ]
    errors = [dict(village=row["village"], **error) for row in villages for error in row["errors"]]
    complete = all(row.get("complete", False) for row in villages)
    report = {
        "schema_version": "heritage-followup-readonly-audit-1",
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "status": "failed" if errors else "passed" if complete else "partial_passed",
        "audit_passed": complete and not errors,
        "all_available_checks_passed": not errors,
        "complete": complete,
        "planned_CA_checks": 24,
        "checked_CA_evaluations": sum(row.get("checked_CA_evaluations", 0) for row in villages),
        "pending_CA_evaluations": sum(len(row["pending"]) for row in villages),
        "errors": errors,
        "fire_scenarios": 4,
        "new_independent_fire_draws": 0,
        "dual_scales_are_independent_samples": False,
        "new_CA_runs_by_this_audit": 0,
        "native_restoration": (
            "separate_parent_receipt_required;not_performed_by_this_readonly_audit"
        ),
        "area_interpretation": (
            "cumulative ignited physical covered area is not destroyed building area; "
            "physical patches counted once; nominal demolition cost sums building "
            "footprints = demolished polygon union + internal duplicate area; "
            "demolished polygon union = cleared area + retained-shared area"
        ),
        "geometry_fixture": geometry_fixture,
        "villages": villages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    write_json(temporary, report)
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "complete",
                    "checked_CA_evaluations",
                    "pending_CA_evaluations",
                    "errors",
                )
            },
            ensure_ascii=False,
        )
    )
    if errors:
        raise SystemExit(1)
    if args.require_complete and not complete:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
