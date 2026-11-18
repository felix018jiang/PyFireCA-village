"""P0 inventory audit and P1 explicit plan evaluation; no optimization."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.heritage import HeritageEvaluationEngine, compare_retained_ordinary
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import (
    HeritageTarget,
    audit_inventory,
    bind_labelme_targets,
    inventory_from_records,
)
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.village.annotations import load_labelme_buildings
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_features(stem, grid, features):
    """Use WGS84 GeoJSON for GIS; keep local metric data explicitly identified."""
    if grid.coordinate_mode == "projected":
        transformer = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
        features = [
            {
                **feature,
                "geometry": mapping(transform(transformer.transform, shape(feature["geometry"]))),
            }
            for feature in features
        ]
        path = stem.with_suffix(".geojson")
        value = {"type": "FeatureCollection", "source_grid": asdict(grid), "features": features}
    else:
        path = stem.with_suffix(".local-metric.json")
        value = {
            "schema_version": "local-metric-features-1",
            "coordinate_units": "m",
            "source_grid": asdict(grid),
            "features": features,
        }
    dump(path, value)
    return path


def run(config_path, output, *, audit_only=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-p1-study-1":
        raise ValueError("unsupported heritage study schema")
    bundle = load_v3_case_bundle((config_path.parent / config["case_dir"]).resolve())
    annotation = config.get("structures_path")
    annotation = (
        (config_path.parent / annotation).resolve()
        if annotation
        else Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json"
    )
    structures = json.loads(annotation.read_text())["buildings"]
    targets = tuple(HeritageTarget(**t) for t in config.get("targets", []))
    reconciliation = None
    annotation_provenance = {}
    if config.get("labelme_annotations"):
        labelme = (config_path.parent / config["labelme_annotations"]).resolve()
        reference = (config_path.parent / config["image_reference"]).resolve()
        annotation_sha = hashlib.sha256(labelme.read_bytes()).hexdigest()
        updated = load_labelme_buildings(
            labelme,
            json.loads(reference.read_text()),
            labels=("building", "gulou", "fengyuqiao"),
            repair_invalid=True,
        )
        if hashlib.sha256(labelme.read_bytes()).hexdigest() != annotation_sha:
            raise ValueError("annotation changed while reading; retry against a frozen input")
        bound_targets, reconciliation = bind_labelme_targets(
            structures, updated, identity_evidence=f"updated_labelme_sha256:{annotation_sha}"
        )
        by_id = {t.building_id: t for t in targets}
        for target in bound_targets:
            if target.building_id in by_id and by_id[target.building_id].role != target.role:
                raise ValueError("configured target role conflicts with current annotation")
            by_id[target.building_id] = target
        targets = tuple(by_id.values())
        annotation_provenance = {
            "labelme_path": str(labelme),
            "labelme_sha256": annotation_sha,
            "image_reference_path": str(reference),
            "image_reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        }
    inventory = inventory_from_records(
        structures,
        bundle.metadata["building_labels"],
        targets=targets,
        protected_ids=tuple(config.get("protected_ids", [])),
        fixed_source_ids=tuple(config.get("fixed_source_ids", [])),
        candidate_ids=tuple(config.get("candidate_ids", [])),
        candidate_evidence=config.get(
            "candidate_evidence", "pending_survey_no_candidates_declared"
        ),
    )
    output.mkdir(parents=True, exist_ok=False)
    report = audit_inventory(bundle.request, inventory)
    if reconciliation is not None:
        report["annotation_reconciliation"] = reconciliation
        dump(output / "annotation-reconciliation.json", reconciliation)
        if not reconciliation["physical_case_matches_updated_annotations"]:
            report["missing_data"].append(
                {
                    "field": "prepared_case_geometry",
                    "status": "stale_against_updated_labelme",
                    "affected_ids": [
                        row["building_id"] for row in reconciliation["geometry_changes"]
                    ],
                    "impact": "rebuild_physical_case_before_real_simulation",
                }
            )
    features = report.pop("building_features")
    dump(output / "inventory-audit.json", report)
    write_features(output / "buildings", bundle.request.grid, features)
    dump(output / "inventory.json", inventory.to_mapping())
    dump(
        output / "protocol.json",
        {
            "schema_version": "heritage-p1-protocol-1",
            "config": config,
            "config_path": str(config_path),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "annotation_path": str(annotation),
            "annotation_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
            **annotation_provenance,
            "parent_case_sha256": request_fingerprint(bundle.request),
            "inventory_sha256": inventory.sha256,
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [Path(__file__), *sorted((ROOT / "src/pyfireca").rglob("*.py"))]
            },
            "mode": "audit_only" if audit_only else "explicit_plans_no_search",
        },
    )
    if audit_only:
        return {"audit": str(output / "inventory-audit.json"), "evaluations": 0}
    if reconciliation and not reconciliation["physical_case_matches_updated_annotations"]:
        raise ValueError("prepared case geometry is stale; rebuild it before real evaluation")
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    engine = HeritageEvaluationEngine(bundle, transport, inventory)
    scenarios = tuple(
        EvaluationScenario(
            raw["scenario_id"],
            tuple(Ignition(tuple(i["point_m"]), i.get("time_s", 0)) for i in raw["ignitions"]),
            raw["horizon_s"],
        )
        for raw in config.get("scenarios", [])
    )
    plans = tuple(
        BuildingDemolitionPlan(
            raw["plan_id"],
            tuple(raw.get("demolished_ids", [])),
            raw.get("ground_policy", "cleared_inert"),
            raw.get("ground_evidence", ""),
        )
        for raw in config.get("plans", [])
    )
    for items in (scenarios, plans):
        ids = [getattr(x, "scenario_id", getattr(x, "plan_id", None)) for x in items]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("explicit scenarios and plans require nonempty unique IDs")
        if any(Path(i).name != i or i in {".", ".."} for i in ids):
            raise ValueError("study IDs must be single safe path components")
    records, comparisons = [], []
    for s_index, scenario in enumerate(scenarios):
        baseline = None
        for p_index, plan in enumerate(plans):
            record, request, simulation = engine.simulate(scenario, plan)
            directory = output / f"scenario-{s_index:03d}" / f"plan-{p_index:03d}"
            directory.mkdir(parents=True)
            dump(directory / "evaluation.json", record.to_mapping())
            write_features(
                directory / "plan",
                request.grid,
                [
                    {
                        **feature,
                        "properties": {
                            **feature["properties"],
                            "action": "demolish"
                            if feature["properties"]["building_id"] in plan.demolished_ids
                            else "keep",
                        },
                    }
                    for feature in features
                ],
            )
            np.savez_compressed(
                directory / "final-state.npz",
                **{
                    name: getattr(simulation.final_state, name)
                    for name in simulation.final_state.__dataclass_fields__
                },
            )
            spatial = directory / "spatial"
            spatial.mkdir()
            retained = tuple(
                b.record() for b in inventory.buildings if b.building_id not in plan.demolished_ids
            )
            dump(
                spatial / "buildings.json",
                {
                    "schema_version": "prepared-buildings-1",
                    "coordinate_space": "world_m",
                    "buildings": retained,
                },
            )
            metadata = {
                **bundle.metadata,
                "spatial_assets_dir": str(spatial),
                "building_labels": {
                    k: v for k, v in inventory.labels.items() if v not in plan.demolished_ids
                },
                "heritage_targets": [asdict(t) for t in inventory.targets],
                "demolition": record.intervention,
            }
            write_v3_case_bundle(
                request,
                directory / "case",
                material_labels=bundle.material_labels,
                landcover_labels=bundle.landcover_labels,
                metadata=metadata,
            )
            records.append(record.to_mapping())
            if not plan.demolished_ids:
                baseline = record
            elif baseline is not None and all(
                r.metrics["protection_status"] != "invalid" for r in (baseline, record)
            ):
                comparisons.append(
                    {
                        "scenario_id": scenario.scenario_id,
                        "plan_id": plan.plan_id,
                        **compare_retained_ordinary(baseline, record),
                    }
                )
    summary = {
        "evaluations": len(records),
        "records": records,
        "retained_ordinary_comparisons": comparisons,
        "optimization_performed": False,
    }
    dump(output / "summary.json", summary)
    return {"summary": str(output / "summary.json"), "evaluations": len(records)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, audit_only=args.audit_only), ensure_ascii=False))
