"""Reassess accepted full runs with input-bound cohorts; no new CA solves."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import ROOT, dump, sha
from run_batuan_joint_search import Oracle

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.protection import (
    assess_protection,
    cohort_from_scenario,
    paired_interaction,
)


def audit(study, output):
    output.mkdir(parents=True, exist_ok=False)
    parent = json.loads((study / "report.json").read_text())
    proof = json.loads((study / "audit.json").read_text())
    if not proof["all_protected_counts_match"]:
        raise AssertionError("parent comparison did not pass")
    oracle = Oracle(study, full=True)
    represented = set(map(int, np.unique(oracle.bundle.request.building_index))) - {-1}
    absent = sorted(set(oracle.types) - represented)
    inventory_audit = {
        "annotated_object_count": len(oracle.types),
        "independent_grid_object_count": len(represented),
        "unrepresented_owner_ids": absent,
        "unrepresented_labels": [oracle.bundle.metadata["building_labels"][str(i)] for i in absent],
        "scope": "reassess legacy raster objects only; not complete annotation-level protection",
        "complete_annotation_level_claim_allowed": not absent,
    }
    cohort_cache = {}
    for row in oracle.rows.values():
        engine = oracle.engine(row["environment_id"])
        scenario = EvaluationScenario(row["scenario_id"], (Ignition(tuple(row["point_m"])),), 5400)
        cohort_cache[row["scenario_id"]] = cohort_from_scenario(
            engine.bundle,
            scenario,
            source_family="external",
            structure_types={i: engine.structure_types[i] for i in represented},
        )
    outcomes = []
    by_label = {}
    for block in parent["confirmation"]:
        if not block["full_grid"]:
            continue
        for raw in block["records"]:
            result = EvaluationResult(**raw)
            assessment = assess_protection(result, cohort_cache[result.scenario_id])
            if (
                assessment["ignited_house_count"] != result.metrics["ignited_house_count"]
                or assessment["ignited_bridge_count"] != result.metrics["ignited_bridge_count"]
            ):
                raise AssertionError("external cohort changed established counts")
            row = {
                "label": block["label"],
                "role": block["role"],
                "scenario_id": result.scenario_id,
                "assessment": assessment,
            }
            outcomes.append(row)
            by_label[block["label"], result.scenario_id] = row
    blocks = {b["label"]: b for b in parent["confirmation"] if b["full_grid"]}
    outer = blocks["outer_proxy"]["plan"]["plan_id"]
    inner = blocks["inner_proxy"]["plan"]["plan_id"]
    joint_ids = tuple(p["plan_id"] for p in blocks["historical_combined"]["plan"]["components"])
    if set(joint_ids) != {outer, inner}:
        raise AssertionError("fixed-measure interaction would use different single-side plans")
    interactions = []
    for sid in oracle.rows:
        rows = []
        for label, measures in [
            ("baseline", ()),
            ("outer_proxy", (outer,)),
            ("inner_proxy", (inner,)),
            ("historical_combined", joint_ids),
        ]:
            a = by_label[label, sid]["assessment"]
            rows.append(
                {
                    "scenario_id": sid,
                    "cohort_sha256": a["cohort_sha256"],
                    "complete": True,
                    "measure_ids": measures,
                    "loss": a["ignited_house_count"] + a["ignited_bridge_count"],
                }
            )
        interactions.append(
            paired_interaction(baseline=rows[0], outer=rows[1], inner=rows[2], joint=rows[3])
        )
    protocol = {
        "parent_report_sha256": sha(study / "report.json"),
        "parent_audit_sha256": sha(study / "audit.json"),
        "source_hashes": {
            str(p.relative_to(ROOT)): sha(p)
            for p in [Path(__file__), ROOT / "src/pyfireca/planning/protection.py"]
        },
        "policy": "fixed input-bound object protection; source families never pooled",
        "new_ca_solves": 0,
        "inventory_audit": inventory_audit,
    }
    dump(output / "protocol.json", protocol)
    dump(
        output / "report.json",
        {
            "protocol_sha256": sha(output / "protocol.json"),
            "outcomes": outcomes,
            "fixed_measure_interactions": interactions,
            "all_external_counts_unchanged": True,
            "new_ca_solves": 0,
        },
    )
    print(
        json.dumps(
            {
                "assessments": len(outcomes),
                "fixed_interactions": len(interactions),
                "counts_unchanged": True,
                "new_ca_solves": 0,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.study.resolve(), args.output.resolve())
