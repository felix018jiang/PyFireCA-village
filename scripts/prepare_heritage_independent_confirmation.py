"""Preregister independent fire confirmation, then bind already frozen plans.

Neither command runs CA, builds a roof mesh or constructs a radiation graph.
Registration commits the RNG keys and geometry-only sampling rules without
revealing new source priorities. Binding requires an immutable final-plan file;
only then are four full-village retained-source priorities and common-layout
requirements materialized. Confirmation failures return to design; they never
trigger replacement draws. A later redesigned plan requires a new confirmation.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol, realize_sources
from pyfireca.planning.retained_sources import with_global_fallback
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_inventory

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_SCHEMA = "heritage-independent-confirmation-preregistration-1"
FINAL_SCHEMA = "heritage-frozen-final-candidates-1"


def immutable_json(path, value):
    path = Path(path)
    if path.exists():
        if content_hash(read_json(path)) != content_hash(value):
            raise ValueError(f"immutable registration or confirmation already differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)


def metadata_inventory(study_path):
    """Load identities and polygons without loading parent simulation arrays."""
    path = Path(study_path).resolve()
    study = read_json(path)
    if study.get("schema_version") != "heritage-p1-study-1":
        raise ValueError("frozen heritage study required")
    case = (path.parent / study["case_dir"]).resolve()
    manifest = read_json(case / "manifest.json")
    metadata = manifest["metadata"]
    structures = (
        (path.parent / study["structures_path"]).resolve()
        if study.get("structures_path")
        else Path(metadata["spatial_assets_dir"]) / "buildings.json"
    )
    annotation = (
        (path.parent / study["labelme_annotations"]).resolve()
        if study.get("labelme_annotations")
        else None
    )
    reference = (
        (path.parent / study["image_reference"]).resolve() if study.get("image_reference") else None
    )
    inventory = load_inventory(
        SimpleNamespace(metadata=metadata),
        structures,
        study,
        annotations=annotation,
        reference=reference,
    )
    paths = {
        path,
        structures,
        *(p for p in case.iterdir() if p.is_file() and p.suffix in {".json", ".npy", ".npz"}),
    }
    paths.update(p for p in (annotation, reference) if p is not None)
    return inventory, {str(p): file_sha256(p) for p in sorted(paths)}


def registration(village, study_path):
    if village not in {"batuan", "pingyan"}:
        raise ValueError("one of the two declared village identities required")
    inventory, inputs = metadata_inventory(study_path)
    cores = sorted(t.building_id for t in inventory.targets)
    expected = 2 if village == "batuan" else 3
    if len(cores) != expected:
        raise ValueError("core inventory differs from the preregistered stratification")
    seed = 202611101101 if village == "batuan" else 202611101102
    allocation = [{"kind": "near_core", "core_id": c, "draw_number": 1} for c in cores]
    allocation.extend({"kind": "background", "draw_number": n + 1} for n in range(4 - len(cores)))
    value = {
        "schema_version": REGISTRATION_SCHEMA,
        "village": village,
        "study_path": str(Path(study_path).resolve()),
        "inventory_sha256": inventory.sha256,
        "input_files_sha256": inputs,
        "generator_source_files_sha256": {
            str(path): file_sha256(path)
            for path in (
                Path(__file__).resolve(),
                ROOT / "src/pyfireca/planning/random_sources.py",
                ROOT / "src/pyfireca/planning/retained_sources.py",
                ROOT / "src/pyfireca/planning/roof_ignition.py",
                ROOT / "src/pyfireca/planning/heritage.py",
            )
        },
        "seed": seed,
        "rng": "numpy-PCG64;SHA256-keyed-per-draw-and-building",
        "rng_namespace": f"independent-confirmation-20261110-v1:{village}",
        "allocation": allocation,
        "ignition_area_m2": 25.0,
        "near_distance_m": 15.0,
        "empty_near_pool_policy": "within_60m_targeted_pressure_pool;otherwise_stop_before_CA",
        "pressure_distance_m": 60.0,
        "preferred_pool_policy": "nearest_core_disjoint_15m_strata;targeted60m_only_for_empty_core",
        "fallback_policy": (
            "entire_qualified_ordinary_village_pool;first_retained;never_no_fire_success"
        ),
        "anchor_policy": "uniform_in_unambiguous_roof;fixed_25m2_disk_intersection",
        "independent_draw_count": 4,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1.0,
        "reference_horizon_s": 3600.0,
        "terminal_horizon_cap_s": 21600.0,
        "terminal_fire_verification": True,
        "confirmation_failure_rule": (
            "retain_all_draws_and_failures;return_to_design;new_final_requires_new_confirmation"
        ),
        "resampling_rule": "none;source_or_polygon_repeats_are_disclosed_not_replaced",
        "weight_policy": (
            "stratified_confirmatory_constraints;not_population_failure_rate_or_annual_risk"
        ),
        "common_mesh_policy": (
            "per_fire_all_frozen_plans_share_source_union_and_all_refined_action_union"
        ),
        "coverage_scope": "finite_frozen_joint_plan_union;not_entire_demolition_powerset",
        "source_priorities_materialized": False,
        "final_candidate_manifest_sha256": None,
        "new_CA_checks": 0,
    }
    value["registration_sha256"] = content_hash(value)
    return value


def _checked_registration(path):
    value = read_json(path)
    unhashed = {k: v for k, v in value.items() if k != "registration_sha256"}
    if value.get("schema_version") != REGISTRATION_SCHEMA or content_hash(unhashed) != value.get(
        "registration_sha256"
    ):
        raise ValueError("preregistration fingerprint differs")
    if any(file_sha256(p) != digest for p, digest in value["input_files_sha256"].items()):
        raise ValueError("registered physical input bytes changed")
    if any(
        file_sha256(p) != digest for p, digest in value["generator_source_files_sha256"].items()
    ):
        raise ValueError("registered source-generation code changed")
    return value


def freeze_four_protocols(registration_value, inventory):
    """Use the existing retained-source constructor without any fire outcomes."""
    r = registration_value
    if inventory.sha256 != r["inventory_sha256"]:
        raise ValueError("registered inventory differs")
    base = freeze_source_protocol(
        inventory,
        seed=r["seed"],
        draws_per_stratum=2,
        area_m2=25,
        near_distance_m=r["near_distance_m"],
    )
    by_key = {(d["stratum"], int(d["draw_id"].split("-")[-1])): d for d in base["draws"]}
    outputs = []
    for number, allocated in enumerate(r["allocation"], 1):
        preferred = (
            "background" if allocated["kind"] == "background" else f"near-{allocated['core_id']}"
        )
        key = preferred, allocated["draw_number"]
        if key in by_key:
            draw = by_key[key]
            pool_type = (
                "nearest_core_disjoint_15m" if allocated["kind"] == "near_core" else "background"
            )
        elif allocated["kind"] == "near_core":
            core = inventory.by_id[allocated["core_id"]].geometry
            pool = sorted(
                i
                for i in base["members"]
                if inventory.by_id[i].geometry.distance(core) <= r["pressure_distance_m"]
            )
            if not pool:
                raise ValueError("empty preregistered 60m pressure pool;amend before confirmation")
            draw_id = f"{preferred}-targeted60-001"
            digest = content_hash([r["rng_namespace"], r["seed"], draw_id, "priority"])
            rng = np.random.Generator(np.random.PCG64(int(digest[:16], 16)))
            ids = rng.permutation(pool).tolist()
            draw = {
                "draw_id": draw_id,
                "stratum": preferred,
                "priority_ids": ids,
                "baseline_source_id": ids[0],
            }
            pool_type = "empty_15m_core_targeted_within60m;not_population_stratum"
        else:
            raise ValueError("empty preregistered background pool;amend before confirmation")
        original = {**base, "draws": [draw], "draws_per_stratum": 1}
        original.pop("protocol_sha256")
        original["protocol_sha256"] = content_hash(original)
        protocol = with_global_fallback(
            original, inventory, fallback_seed=r["seed"] + 1000 + number
        )
        protocol.update(
            independent_confirmation_registration_sha256=r["registration_sha256"],
            confirmatory_pool_type=pool_type,
            new_independent_draw=True,
            weight_policy=r["weight_policy"],
        )
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        outputs.append(protocol)
    if len(outputs) != 4:
        raise ValueError("exactly four confirmatory priorities required")
    return outputs


def bind(registration_path, final_candidates_path, output):
    r = _checked_registration(registration_path)
    final_path = Path(final_candidates_path).resolve()
    final = read_json(final_path)
    if (
        final.get("schema_version") != FINAL_SCHEMA
        or final.get("frozen") is not True
        or final.get("village") != r["village"]
        or final.get("inventory_sha256") != r["inventory_sha256"]
        or final.get("confirmation_registration_sha256") != r["registration_sha256"]
    ):
        raise ValueError("final candidate manifest must explicitly freeze the registered village")
    plans = final["plans"]
    if not 1 <= len(plans) <= 5 or len({p["plan_id"] for p in plans}) != len(plans):
        raise ValueError("one to five unique frozen plans required for the 40-check cap")
    inventory, references = metadata_inventory(r["study_path"])
    interventions, physical_keys = set(), set()
    for plan in plans:
        ids, cells = plan["demolished_ids"], plan["cleared_physical_cell_ids"]
        if len(set(ids)) != len(ids) or not set(ids) <= set(inventory.candidate_ids):
            raise ValueError("frozen plan has duplicate or ineligible demolition actions")
        if len(set(cells)) != len(cells) or any(
            isinstance(c, bool) or not isinstance(c, int) or c < 0 for c in cells
        ):
            raise ValueError("ground actions require unique nonnegative physical cell IDs")
        physical = tuple(sorted(ids)), tuple(sorted(cells))
        if physical in physical_keys:
            raise ValueError("duplicate physical plans must be consolidated before final freeze")
        physical_keys.add(physical)
        interventions.update(ids)
    refinements = set(final["refinement_ids"]) | interventions
    if not refinements <= inventory.by_id.keys():
        raise ValueError("final common refinement contains unknown identities")
    protocols = freeze_four_protocols(r, inventory)
    output = Path(output).resolve()
    rows, fires = [], []
    for index, protocol in enumerate(protocols, 1):
        path = output / f"source-{index}.json"
        immutable_json(path, protocol)
        draw = protocol["draws"][0]
        sources, footprints, per_plan = set(), {}, []
        for plan in plans:
            realized = realize_sources(protocol, inventory, demolished_ids=plan["demolished_ids"])[
                0
            ]
            if realized["status"] != "fire":
                raise ValueError(
                    "a frozen final plan leaves no source;confirmation remains unknown"
                )
            source = realized["source_id"]
            sources.add(source)
            footprints[source] = realized["footprint"].to_mapping()
            per_plan.append(
                {
                    "plan_id": plan["plan_id"],
                    "source_id": source,
                    "source_replaced": realized["source_replaced"],
                    "footprint_sha256": realized["footprint"].sha256,
                }
            )
        fire_id = f"independent-{index}-{draw['draw_id']}"
        fires.append({"fire_id": fire_id, "protocol_path": str(path), "draw_id": draw["draw_id"]})
        # This exact finite-plan union is the minimal source realization scope.
        # The existing joint runner additionally realizes a conservative bounded
        # prefix using the finite action union; it does not enumerate all 2^N.
        prefix = []
        maximum = max(len(p["demolished_ids"]) for p in plans)
        for identifier in draw["priority_ids"]:
            prefix.append(identifier)
            if identifier not in interventions or len(prefix) > maximum:
                break
        rows.append(
            {
                "fire_id": fire_id,
                "per_plan": per_plan,
                "actual_source_union": sorted(sources),
                "actual_source_footprints": footprints,
                "minimum_common_refined_ids": sorted(
                    refinements | sources | {t.building_id for t in inventory.targets}
                ),
                "existing_joint_runner_conservative_prefix": prefix,
            }
        )
    config = {
        "schema_version": "heritage-joint-comparison-1",
        "study_path": r["study_path"],
        "source_policy": "retained_sources",
        "source_fires": fires,
        "plans": plans,
        "refinement_ids": sorted(refinements),
        "horizon_s": 3600.0,
        "checkpoints_s": [1200.0, 2400.0, 3600.0],
        "terminal_fire_verification": True,
        "terminal_horizon_cap_s": 21600.0,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1.0,
        "core_overlap_policy": final.get("core_overlap_policy", "heritage_priority"),
        "max_nodes": 500000,
        "max_radiation_edges": 60000000,
        "max_new_checks": 8 * len(plans),
        "max_total_checks": 40,
        "max_wall_time_s": 28800.0,
        "minimum_free_disk_GB": 5.0,
        "new_independent_fire_draw_count": 4,
        "confirmation_registration_sha256": r["registration_sha256"],
        "final_candidates_path": str(final_path),
        "final_candidates_sha256": file_sha256(final_path),
        "scope": (
            "independent_frozen_candidate_confirmation;no_reselection_or_resampling;"
            "not_global_fire_coverage"
        ),
    }
    immutable_json(output / "confirmation-config.json", config)
    receipt = {
        "schema_version": "heritage-independent-confirmation-binding-1",
        "registration_sha256": r["registration_sha256"],
        "final_candidates_path": str(final_path),
        "final_candidates_sha256": file_sha256(final_path),
        "input_files_sha256": references,
        "config_sha256": file_sha256(output / "confirmation-config.json"),
        "source_protocol_files_sha256": {
            f["protocol_path"]: file_sha256(f["protocol_path"]) for f in fires
        },
        "new_independent_draws": 4,
        "planned_CA_checks": 8 * len(plans),
        "common_source_and_refinement_requirements": rows,
        "priority_and_polygon_repeats_not_resampled": True,
        "mesh_or_radiation_graph_constructed": False,
        "new_CA_checks": 0,
        "requires_serial_capacity_preflight_before_CA": True,
        "minimum_common_union_not_entire_optimization_space": True,
        "failure_action": r["confirmation_failure_rule"],
    }
    immutable_json(output / "binding-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_subparsers(dest="mode", required=True)
    register = mode.add_parser("register")
    register.add_argument("--village", choices=("batuan", "pingyan"), required=True)
    register.add_argument("--study", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    binder = mode.add_parser("bind")
    binder.add_argument("--registration", type=Path, required=True)
    binder.add_argument("--final-candidates", type=Path, required=True)
    binder.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "register":
        value = registration(args.village, args.study)
        immutable_json(args.output, value)
    else:
        value = bind(args.registration, args.final_candidates, args.output)
    print({"mode": args.mode, "new_CA_checks": 0, "output": str(args.output)})


if __name__ == "__main__":
    main()
