"""Replay frozen straight/multiple layouts at V1 without feedback to selection."""

from __future__ import annotations

# ruff: noqa: RUF001
import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from run_batuan_adaptive_search import digest, dump, save_checked_replay
from run_batuan_intervention_study import engine_for
from run_batuan_protection_search import context, scenarios_from

from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def export(study, include_ablation=False):
    study = Path(study).resolve()
    report = json.loads((study / "report.json").read_text())
    protocol = report["protocol"]
    selected = json.loads((study / "selection.json").read_text())
    if digest(selected["plans"]) != selected["sha256"]:
        raise ValueError("frozen selection checksum mismatch")
    for name, sha in protocol["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise ValueError("replay evaluator differs from frozen physics")
    bundle = load_v3_case_bundle(study / "search-case")
    metadata = dict(bundle.metadata)
    metadata.update(
        simulation_scope="局部81×81元胞（405m×405m）V1独立验证域，非全村求解",
        place_name="岜团村 · V1局部验证",
    )
    bundle = replace(bundle, metadata=metadata)
    near, types, sha = context(bundle)
    if sha != protocol["annotated_structures_sha256"]:
        raise ValueError("annotation changed")
    engine = engine_for(bundle, near, types, protocol["code"])
    scenario = scenarios_from(protocol, "held_out")[0]
    result = {
        "schema": "batuan-frozen-validation-replays-1",
        "selection_sha256": selected["sha256"],
        "domain": "local",
        "window": protocol["window"],
        "scenario": asdict(scenario),
        "selection_feedback": False,
        "comparisons": {},
    }
    targets = [
        (
            label,
            selected["plans"][label],
            report["validation"][label][0],
            "frozen_geometry_family_representative",
        )
        for label in ("outer_proxy_straight", "outer_proxy_multiple")
    ]
    if include_ablation:
        ablation = json.loads((study / "component-ablation.json").read_text())
        if ablation["protocol"]["main_selection_sha256"] != selected["sha256"]:
            raise ValueError("ablation selection mismatch")
        main = ablation["components"][0]
        targets.append(
            (
                "outer_proxy_main_only",
                main["plan"],
                main["base"][2],
                "post_selection_component_ablation",
            )
        )
    for label, raw_plan, reference, role in targets:
        plan = clearing_plan_from_mapping(raw_plan)
        evaluation = EvaluationResult(**reference)
        if (
            evaluation.case_sha256 != engine.case_sha256
            or evaluation.scenario_id != scenario.scenario_id
        ):
            raise ValueError("reference evaluation input mismatch")
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
        signature.update(engine.zones[plan.zone_id].tobytes())
        if signature.hexdigest() != evaluation.evaluation_sha256:
            raise ValueError("reference evaluation signature mismatch")
        if evaluation.metrics["burning_reached_domain_edge"]:
            raise ValueError("invalid replay crop")
        relative = "validation-replays/" + label
        viewer = save_checked_replay(engine, scenario, plan, study / relative, evaluation)
        result["comparisons"][label] = {
            "evaluation": evaluation.to_mapping(),
            "root_relative": relative,
            "study_role": role,
            "viewer_relative": str(Path(viewer).relative_to(study)),
        }
        print("Verified V1 replay " + label, flush=True)
    snapshot = study / "validation-replay-source" / Path(__file__).name
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    result["source_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    dump(study / "validation-replays.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--include-ablation", action="store_true")
    args = parser.parse_args()
    export(args.study, args.include_ablation)
