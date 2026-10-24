"""Freeze three hand-specified roof controls; no search and no site recommendations."""

from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path

from shapely.geometry import box

from pyfireca.village.workflow_v3 import write_v3_case_bundle


def create_controls(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "heritage_synthetic", Path(__file__).with_name("synthetic_case.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    shared = {
        "S": box(6.05, 8.2, 6.45, 8.8),
        "M": box(6.5, 8.2, 6.9, 8.8),
        "G": box(7, 8.2, 7.4, 8.8),
        "F": box(7.5, 8.2, 7.9, 8.8),
        "U": box(16, 12, 18, 14),
    }
    inputs = []
    for name, kwargs in (
        ("shared-cell", {"geometries": shared, "ignition_temperature_K": 350}),
        ("transmission-chain", {}),
        ("cross-gap", {"radius": 8, "ignition_temperature_K": 350}),
    ):
        folder = output / name
        folder.mkdir()
        bundle, _transport, inventory, scenario = fixture.make_case(**kwargs)
        request = replace(bundle.request, sample_interval_s=15.0)
        write_v3_case_bundle(
            request,
            folder / "case",
            material_labels=bundle.material_labels,
            landcover_labels=bundle.landcover_labels,
            metadata=bundle.metadata,
        )

        def dump(filename, value, folder=folder):
            (folder / filename).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")

        dump("buildings.json", {"buildings": inventory.structures})
        dump(
            "study.json",
            {
                "schema_version": "heritage-p1-study-1",
                "case_dir": "case",
                "structures_path": "buildings.json",
                "targets": [asdict(t) for t in inventory.targets],
                "fixed_source_ids": inventory.fixed_source_ids,
                "candidate_ids": inventory.candidate_ids,
                "candidate_evidence": inventory.candidate_evidence,
            },
        )
        dump(
            "experiments.json",
            {
                "schema_version": "heritage-roof-experiments-1",
                "source_policy": "technical_probe",
                "evidence": "synthetic_control_not_calibrated_to_batuan_or_demolition_permission",
                "scenarios": [
                    {
                        "scenario_id": name,
                        "horizon_s": scenario.horizon_s,
                        "ignitions": [asdict(i) for i in scenario.ignitions],
                    }
                ],
                "plans": [
                    {"plan_id": "baseline"},
                    {
                        "plan_id": "remove-M",
                        "demolished_ids": ["M"],
                        "ground_evidence": "synthetic_cleared_inert_ground",
                    },
                ],
            },
        )
        inputs.append(
            {"study": str(folder / "study.json"), "experiments": str(folder / "experiments.json")}
        )
    return inputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(create_controls(args.output), indent=2))
