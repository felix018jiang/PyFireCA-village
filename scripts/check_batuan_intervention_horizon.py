"""Evaluate already-selected plans at a longer horizon without reselecting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_batuan_intervention_study import dump, engine_for
from shapely.geometry import shape

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPlan, ClearingStrip
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--horizon-s", type=float, default=5400)
    args = parser.parse_args()
    output = args.study.resolve()
    report = json.loads((output / "report.json").read_text())
    if report.get("status") != "completed":
        raise ValueError("wait until the main study and full-domain validation have completed")
    if "extended_horizon_validation" in report:
        raise FileExistsError("this study already has a horizon validation")
    protocol = report["protocol"]
    s = protocol["held_out_scenario"]
    if args.horizon_s <= s["horizon_s"]:
        raise ValueError("validation horizon must exceed the search horizon")
    scenario = EvaluationScenario(
        s["scenario_id"] + "-longer",
        tuple(Ignition(tuple(i["point_m"]), i["time_s"]) for i in s["ignitions"]),
        args.horizon_s,
    )
    bundle = load_v3_case_bundle(output / "search-case")
    structures = json.loads(
        (Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json").read_text()
    )["buildings"]
    labels = {item["building_id"]: item["properties"]["source_label"] for item in structures}
    types = {int(k): labels[v] for k, v in bundle.metadata["building_labels"].items()}
    zone = json.loads((output / "search-zones.geojson").read_text())
    near = shape(zone.get("projected_geometry", zone["geometry"]))
    engine = engine_for(bundle, near, types, protocol["code"])
    ids = ["no-clearing", *[item["representative"] for item in report["selected"].values()]]
    records = {}
    for plan_id in ids:
        raw = next(p for p in protocol["plans"] if p["plan_id"] == plan_id)
        plan = ClearingPlan(
            plan_id,
            tuple(
                ClearingStrip(tuple(s["start_m"]), tuple(s["end_m"]), s["width_m"])
                for s in raw["strips"]
            ),
            raw["zone_id"],
        )
        result = engine.evaluate(scenario, plan)
        if result.metrics["burning_reached_domain_edge"]:
            raise AssertionError("extended-horizon fire reached the crop edge")
        records[plan_id] = result.to_mapping()
        print(
            json.dumps({"plan": plan_id, "horizon_s": args.horizon_s, "metrics": result.metrics}),
            flush=True,
        )
    dump(output / "horizon-validation.json", records)
    report["extended_horizon_validation"] = records
    dump(output / "report.json", report)


if __name__ == "__main__":
    main()
