"""Run explicit roof-temperature experiments; no candidate selection or optimization.

Study identities/eligibility and experiment sources are separate inputs. A
technical probe does not fill the study's pending fixed-source or candidate IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Point

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.heritage import compare_retained_ordinary
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import (
    HeritageTarget,
    bind_labelme_targets,
    content_hash,
    inventory_from_records,
)
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.roof_thermal import roof_contact_operator
from pyfireca.village.annotations import load_labelme_buildings
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactOperator, FlameContactParameters
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def resolve_candidate_ids(config, records):
    """Resolve an explicit list or the user-authorized provisional all-ordinary policy."""
    explicit = tuple(config.get("candidate_ids", ()))
    policy = config.get("candidate_policy")
    if policy is None:
        return explicit
    if explicit:
        raise ValueError("candidate policy and explicit candidate IDs cannot be combined")
    if policy != "all_ordinary_except_protected_targets_and_sources":
        raise ValueError("unsupported demolition candidate policy")
    forbidden = (
        set(config.get("protected_ids", ()))
        | {target["building_id"] for target in config.get("targets", ())}
        | set(config.get("fixed_source_ids", ()))
    )
    return tuple(
        sorted(
            row["building_id"]
            for row in records
            if row["properties"]["source_label"] == "building"
            and row["building_id"] not in forbidden
        )
    )


def load_study(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if config.get("schema_version") != "heritage-p1-study-1":
        raise ValueError("unsupported study schema")
    bundle = load_v3_case_bundle(path.parent / config["case_dir"])
    structures_path = config.get("structures_path")
    structures_path = (
        path.parent / structures_path
        if structures_path
        else Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json"
    )
    records = json.loads(structures_path.read_text())["buildings"]
    candidate_ids = resolve_candidate_ids(config, records)
    targets = tuple(HeritageTarget(**t) for t in config.get("targets", []))
    if config.get("labelme_annotations"):
        raw = path.parent / config["labelme_annotations"]
        reference = path.parent / config["image_reference"]
        latest = load_labelme_buildings(
            raw,
            json.loads(reference.read_text()),
            labels=("building", "gulou", "fengyuqiao"),
            repair_invalid=True,
        )
        bound, reconciliation = bind_labelme_targets(
            records,
            latest,
            identity_evidence=f"labelme_sha256:{hashlib.sha256(raw.read_bytes()).hexdigest()}",
        )
        if not reconciliation["physical_case_matches_updated_annotations"]:
            raise ValueError("stale physical geometry; refresh before independent roof simulation")
        if {(t.building_id, t.role) for t in bound} != {(t.building_id, t.role) for t in targets}:
            raise ValueError("core identities do not match frozen annotation")
    inventory = inventory_from_records(
        records,
        bundle.metadata["building_labels"],
        targets=targets,
        protected_ids=tuple(config.get("protected_ids", [])),
        fixed_source_ids=tuple(config.get("fixed_source_ids", [])),
        candidate_ids=candidate_ids,
        candidate_evidence=config.get(
            "candidate_evidence", "pending_survey_no_candidates_declared"
        ),
    )
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    return bundle, transport, inventory


class SelectedTrace:
    """Save only selected roof states; never accumulate full-village replay frames."""

    def __init__(self, layout, identifiers):
        self.nodes = np.unique(np.concatenate([layout.nodes(i) for i in identifiers]))
        self.frames = []

    def append(self, snapshot):
        self.frames.append(
            {
                "time_s": snapshot.time_s,
                "surface_temperature_K": snapshot.surface_temperature_K.ravel()[
                    self.nodes
                ].tolist(),
                "temperature_valid_mask": snapshot.temperature_valid_mask.ravel()[
                    self.nodes
                ].tolist(),
                "state": snapshot.state.ravel()[self.nodes].tolist(),
                "heated_water_remaining_kg": snapshot.heated_water_remaining_kg.ravel()[
                    self.nodes
                ].tolist(),
                "fuel_remaining_kg": snapshot.fuel_remaining_kg.ravel()[self.nodes].tolist(),
            }
        )


def save_evaluation(output, record, layout, result, contact):
    """Persist arrays and physical contact graph; storage-grid adjacency is forbidden."""
    output.mkdir(parents=True, exist_ok=False)
    request = layout.node_request
    dump(output / "evaluation.json", record.to_mapping())
    dump(output / "layout.json", layout.to_mapping())
    np.savez_compressed(
        output / "layout.npz",
        physical_cell_id=layout.cell_id,
        xy_m=layout.xy_m,
        component_id=np.asarray(layout.component_id),
        area_fraction=layout.area_fraction,
    )
    np.savez_compressed(
        output / "final-state.npz",
        **{k: getattr(result.final_state, k) for k in result.final_state.__dataclass_fields__},
        ignition_cause=result.ignition_cause,
        initial_total_dry_fuel_kg=result.initial_total_dry_fuel_kg,
        initial_fuel_kg=result.initial_fuel_kg,
        initial_total_water_kg=result.initial_total_water_kg,
        initial_heated_water_kg=result.initial_heated_water_kg,
    )
    if contact is not None:
        np.savez_compressed(
            output / "contact.npz",
            neighbors=contact.neighbors,
            geometry_fraction=contact.geometry_fraction,
        )
        dump(output / "contact.json", contact.parameters.to_mapping())
    write_v3_case_bundle(
        request,
        output / "node-storage-not-map",
        material_labels={
            int(v): f"inherited_material_{v}" for v in np.unique(request.material_index)
        },
        landcover_labels={
            int(v): f"inherited_landcover_{v}" for v in np.unique(request.landcover_index)
        },
        metadata={
            "model": "heritage-independent-roofs-1",
            "storage_grid_is_geographic": False,
            "requires_saved_contact_topology": contact is not None,
            "physical_layout": "../layout.json",
            "evaluation_sha256": record.evaluation_sha256,
        },
    )


def restore_simulation(output):
    """Rerun saved arrays with the saved physical topology, not 1 x N adjacency."""
    output = Path(output)
    bundle = load_v3_case_bundle(output / "node-storage-not-map")
    if bundle.metadata.get("model") != "heritage-independent-roofs-1":
        raise ValueError("not a saved independent roof model")
    contact = None
    if bundle.metadata["requires_saved_contact_topology"]:
        parameters = FlameContactParameters.from_mapping(
            json.loads((output / "contact.json").read_text())
        )
        with np.load(output / "contact.npz", allow_pickle=False) as graph:
            contact = FlameContactOperator(
                graph["neighbors"],
                graph["geometry_fraction"],
                parameters,
                bundle.request.parameters,
            )
    expected = json.loads((output / "evaluation.json").read_text())["diagnostics"][
        "physical_request_sha256"
    ]
    if request_fingerprint(bundle.request) != expected:
        raise ValueError("saved thermal request fingerprint mismatch")
    result = _run_resolved_v3_simulation(bundle.request, contact=contact, retain_snapshots=False)
    check_final(bundle.request, result)
    with np.load(output / "final-state.npz", allow_pickle=False) as saved:
        for key in result.final_state.__dataclass_fields__:
            np.testing.assert_array_equal(getattr(result.final_state, key), saved[key])
        np.testing.assert_array_equal(result.ignition_cause, saved["ignition_cause"])
    return {"all_final_state_fields_equal": True, "saved_contact_used": contact is not None}


def run(study_path, experiment_path, output):
    study_path, experiment_path, output = map(
        lambda p: Path(p).resolve(), (study_path, experiment_path, output)
    )
    experiments = json.loads(experiment_path.read_text())
    if experiments.get("schema_version") != "heritage-roof-experiments-1":
        raise ValueError("unsupported experiment schema")
    bundle, transport, inventory = load_study(study_path)
    paths = [
        Path(__file__),
        *sorted((ROOT / "src/pyfireca").rglob("*.py")),
        *sorted((ROOT / "examples/research/heritage").glob("*.py")),
    ]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    engine = RoofHeritageEvaluationEngine(
        bundle,
        transport,
        inventory,
        source_policy=experiments["source_policy"],
        code_version=content_hash(hashes),
    )
    plans = tuple(
        BuildingDemolitionPlan(**{**p, "demolished_ids": tuple(p.get("demolished_ids", ()))})
        for p in experiments["plans"]
    )
    if not plans or plans[0].demolished_ids:
        raise ValueError("first experiment plan must be no-demolition baseline")
    if len({p.plan_id for p in plans}) != len(plans):
        raise ValueError("plan IDs must be unique")
    scenarios = tuple(
        EvaluationScenario(
            s["scenario_id"],
            tuple(Ignition(tuple(i["point_m"]), i.get("time_s", 0)) for i in s["ignitions"]),
            s["horizon_s"],
        )
        for s in experiments["scenarios"]
    )
    if not scenarios or len({s.scenario_id for s in scenarios}) != len(scenarios):
        raise ValueError("nonempty unique scenarios required")
    if any(
        Path(name).name != name or name in {".", ".."}
        for name in [*(s.scenario_id for s in scenarios), *(p.plan_id for p in plans)]
    ):
        raise ValueError("scenario and plan IDs must be safe directory names")
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "study-input.json", json.loads(study_path.read_text()))
    dump(output / "experiments.json", experiments)
    dump(output / "inventory.json", inventory.to_mapping())
    # Freeze producer code with the experiment, not just a mutable git revision.
    for path in paths:
        relative = path.relative_to(ROOT)
        destination = output / "source-snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        destination.write_bytes(data)
        if hashlib.sha256(data).hexdigest() != hashes[str(relative)]:
            raise ValueError("producer changed during snapshot; restart experiment")
    dump(
        output / "protocol.json",
        {
            "schema_version": "heritage-roof-protocol-1",
            "study_path": str(study_path),
            "study_sha256": hashlib.sha256(study_path.read_bytes()).hexdigest(),
            "experiments_sha256": hashlib.sha256(experiment_path.read_bytes()).hexdigest(),
            "parent_case_sha256": engine.case_sha256,
            "inventory_sha256": engine.inventory_sha256,
            "layout_sha256": engine.layout_sha256,
            "source_sha256": hashes,
            "source_policy": engine.source_policy,
            "candidate_eligibility_pending": not bool(inventory.candidate_ids),
            "fixed_study_source_pending": not bool(inventory.fixed_source_ids),
            "optimization": False,
        },
    )
    summaries = []
    for scenario in scenarios:
        baseline = None
        for plan in plans:
            print(f"Starting {scenario.scenario_id}/{plan.plan_id}", flush=True)
            identifiers = tuple(
                sorted(
                    {t.building_id for t in inventory.targets}
                    | {
                        i
                        for i, b in inventory.by_id.items()
                        if any(b.geometry.contains(Point(*p.point_m)) for p in scenario.ignitions)
                    }
                )
            )
            trace = SelectedTrace(engine.layout, identifiers)

            def progress(now, end):
                if now == 0 or now >= end:
                    print(f"  thermal clock {now:g}/{end:g} s", flush=True)

            record, layout, result = engine.simulate(
                scenario, plan, snapshot_sink=trace, progress_callback=progress
            )
            folder = output / scenario.scenario_id / plan.plan_id
            # Reconstructing contact uses unchanged static geometry plus the new burnable mask.
            save_evaluation(folder, record, layout, result, roof_contact_operator(layout))
            dump(
                folder / "selected-trace.json",
                {
                    "node_ids": trace.nodes.tolist(),
                    "identifiers": identifiers,
                    "frames": trace.frames,
                },
            )
            if baseline is None:
                baseline = record
            else:
                dump(
                    folder / "matched-ordinary-comparison.json",
                    compare_retained_ordinary(baseline, record),
                )
            summaries.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "plan_id": plan.plan_id,
                    "evaluation": str(folder / "evaluation.json"),
                    "protection_status": record.metrics["protection_status"],
                    "core_outcomes": record.metrics["core_outcomes"],
                    "demolished_count": record.intervention["demolished_count"],
                }
            )
            print(f"  complete: {record.metrics['protection_status']}", flush=True)
    summary = {
        "schema_version": "heritage-roof-summary-1",
        "runs": summaries,
        "fixed_study_source_ids": inventory.fixed_source_ids,
        "candidate_ids": inventory.candidate_ids,
        "parent_case_unchanged": request_fingerprint(bundle.request) == engine.case_sha256,
    }
    dump(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--experiments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.study, args.experiments, args.output)


if __name__ == "__main__":
    main()
