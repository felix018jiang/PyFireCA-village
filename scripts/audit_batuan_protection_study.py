"""Verify frozen sources, cached evaluation signatures, selection and baseline inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from run_batuan_adaptive_search import digest, dump
from run_batuan_intervention_study import engine_for
from run_batuan_protection_search import LockedOracle, context, scenarios_from

from pyfireca.planning.adaptive import canonical_plan, matches_family, rank_key
from pyfireca.planning.effectiveness import joint_protection_score
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def audit(study):
    study = Path(study).resolve()
    protocol = json.loads((study / "protocol.json").read_text())
    report = json.loads((study / "report.json").read_text())
    if report["status"] != "completed":
        raise ValueError("study is not complete")
    for name, expected in protocol["source_hashes"].items():
        actual = hashlib.sha256((study / "source_snapshot" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"source snapshot mismatch: {name}")
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise AssertionError(f"active source differs from executed source: {name}")
    base = load_v3_case_bundle(ROOT / "runs/batuan-slope-contact-20261016/case")
    if request_fingerprint(base.request) != protocol["case_sha256"]:
        raise AssertionError("parent case changed")
    bundle = load_v3_case_bundle(study / "search-case")
    near, types, annotation_sha = context(bundle)
    if annotation_sha != protocol["annotated_structures_sha256"]:
        raise AssertionError("annotation changed")
    engine = engine_for(bundle, near, types, protocol["code"])
    lock = json.loads((study / "input-lock.json").read_text())
    if engine.case_sha256 != lock["crop_case"]:
        raise AssertionError("crop changed")
    train = scenarios_from(protocol, "training")
    held = scenarios_from(protocol, "held_out")
    oracle = LockedOracle(
        engine,
        train,
        train + held,
        study / "evaluation-cache",
        digest({"protocol": protocol, "stage": "training"}),
    )
    checked, requests, rejected = 0, 0, 0
    for name, search in report["searches"].items():
        zone, family = name.split("/")
        saved = json.loads((study / f"{zone}-{family}-search.json").read_text())
        if digest(saved) != digest(search):
            raise AssertionError("search/report mismatch")
        if search["config"] != protocol["configs"][family]:
            raise AssertionError("config mismatch")
        if len(search["records"]) != search["config"]["max_evaluations"]:
            raise AssertionError("matched budget not exhausted")
        eligible = []
        identities = set()
        for record in search["records"]:
            requests += 1
            plan = clearing_plan_from_mapping(record["plan"])
            if canonical_plan(zone, plan.paths) != plan or plan.plan_id in identities:
                raise AssertionError("noncanonical or duplicate plan")
            identities.add(plan.plan_id)
            if record["status"] != "ok":
                rejected += 1
                continue
            key = digest({"context": oracle.context, "plan": plan.to_mapping()})
            entry = json.loads((oracle.directory / (key + ".json")).read_text())
            if entry["key"] != key or entry["payload_sha256"] != digest(entry["payload"]):
                raise AssertionError("cache corruption")
            evaluations = [EvaluationResult(**r) for r in entry["payload"]["evaluations"]]
            for scenario, evaluation in zip(train, evaluations, strict=True):
                signature = hashlib.sha256()
                for item in (
                    engine.case_sha256,
                    engine.code_version,
                    asdict(engine.transport),
                    asdict(scenario),
                    plan.to_mapping(),
                    engine.structure_types,
                ):
                    signature.update(json.dumps(item, sort_keys=True).encode())
                signature.update(engine.zones[zone].tobytes())
                if evaluation.evaluation_sha256 != signature.hexdigest():
                    raise AssertionError("evaluation signature mismatch")
                checked += 1
            score = joint_protection_score(evaluations)
            if list(score) != record["effectiveness"]:
                raise AssertionError("ranking mismatch")
            if [e.metrics for e in evaluations] != record["training_metrics"]:
                raise AssertionError("metric mismatch")
            if (
                abs(evaluations[0].intervention["cleared_area_m2"] - record["cleared_area_m2"])
                > 1e-8
            ):
                raise AssertionError("area mismatch")
            if matches_family(plan, family):
                eligible.append(record)
        best = min(
            eligible, key=lambda r: (rank_key(r, search["config"]["objective"]), r["plan_id"])
        )
        if best["plan_id"] != search["representative"]:
            raise AssertionError("selected representative is not the declared best")
        if digest(report["chosen_plans"][zone + "_" + family]) != digest(best["plan"]):
            raise AssertionError("chosen geometry mismatch")
    selected = json.loads((study / "selection.json").read_text())
    if selected["sha256"] != digest(report["chosen_plans"]):
        raise AssertionError("selection changed after freeze")
    if report["held_out_used_for_reselection"] or report["sensitivity_used_for_reselection"]:
        raise AssertionError("test feedback enabled")
    replay_checks = []
    for label, entry in report["full_domain_comparison"].items():
        root = study / "full" / entry["evaluation"]["intervention_id"]
        times = np.load(root / "run/outputs/ignition_time_s.npy")
        state = np.load(root / "run/outputs/state_final.npy")
        metrics = entry["evaluation"]["metrics"]
        if int(np.isfinite(times).sum()) != metrics["ignited_cells"]:
            raise AssertionError("replay ignited cell mismatch")
        if int((state == 4).sum()) != metrics["burned_out_cells"]:
            raise AssertionError("replay burned-out cell mismatch")
        ignited_ids = set(
            int(i) for i in np.unique(base.request.building_index[np.isfinite(times)]) if i >= 0
        )
        for category, key in (
            ("building", "ignited_house_count"),
            ("fengyuqiao", "ignited_bridge_count"),
        ):
            if sum(types.get(i, "building") == category for i in ignited_ids) != metrics[key]:
                raise AssertionError("replay structure count mismatch")
        local = report["controls"][label] + report["validation"][label]
        for whole, cropped in zip(entry["all_scenarios"], local, strict=True):
            for key in ("ignited_cells", "ignited_house_count", "ignited_bridge_count"):
                if whole["metrics"][key] != cropped["metrics"][key]:
                    raise AssertionError("full/crop validation mismatch")
        replay_checks.append(label)
    result = {
        "status": "passed",
        "source_files": len(protocol["source_hashes"]),
        "requests": requests,
        "rejected_requests": rejected,
        "verified_training_evaluation_signatures": checked,
        "case_unchanged": True,
        "annotation_unchanged": True,
        "selection_frozen": True,
        "full_grid_scenarios_checked": sum(
            len(e["all_scenarios"]) for e in report["full_domain_comparison"].values()
        ),
        "replays_checked": replay_checks,
        "report_sha256": digest(report),
    }
    dump(study / "completion-audit.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    audit(parser.parse_args().study)
