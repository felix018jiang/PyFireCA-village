"""Bind village inputs to a prepared physical problem and export a strategy."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from math import fsum
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.study_io import load_inventory
from pyfireca.village.physics_v3 import V3CellPhysicsArrays, resolve_vegetation_physics
from pyfireca.village.vegetation_config import VegetationCase
from pyfireca.village.vegetation_materials import load_vegetation_profiles
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_material_binding(bundle, binding, library_path):
    """Re-resolve all 33 fields for comparison only; never replace case arrays."""
    if binding.get("schema_version") != "heritage-material-binding-1":
        raise ValueError("unsupported material binding schema")
    request = bundle.request
    library = load_vegetation_profiles(library_path)
    assignments = {int(k): v for k, v in binding["material_profiles"].items()}
    active_codes = set(int(x) for x in np.unique(request.material_index[request.known_mask]))
    if set(assignments) != active_codes or not set(assignments.values()) <= library.by_id.keys():
        raise ValueError("material bindings must cover exactly the known case material codes")
    profile_ids = np.full(request.grid.shape, "", dtype=object)
    for code, identifier in assignments.items():
        profile_ids[request.material_index == code] = identifier
    roof = request.building_index >= 0
    if set(profile_ids[roof]) != {binding["roof_profile_id"]}:
        raise ValueError("roof profile differs from the declared equivalent-building material")
    expected_burnable = np.zeros(request.grid.shape, dtype=bool)
    for profile in library.profiles:
        if profile.burnable:
            expected_burnable |= profile_ids == profile.id
    if not np.array_equal(expected_burnable, request.burnable_mask):
        raise ValueError("material combustibility differs from the prepared physical case")
    # The existing resolver reads spatial/profile fields only. Empty scenario
    # dictionaries do not choose physics or execute a simulation.
    case = VegetationCase(
        path=Path(library_path),
        case_id="strategy-material-comparison",
        evidence=library.evidence,
        prepared_dir=Path(library_path).parent,
        grid=request.grid,
        profiles=library,
        profile_id_by_cell=profile_ids,
        landcover_index=request.landcover_index,
        domain_mask=request.domain_mask,
        known_mask=request.known_mask,
        # Older prepared cases may omit the optional evidence raster. The
        # resolver copies it only into its unused evidence output; zero denotes
        # unknown provenance here and is never written into the physical case.
        evidence_index=request.material_evidence_index
        if request.material_evidence_index is not None
        else np.zeros(request.grid.shape, dtype=np.int16),
        burnable_mask=request.burnable_mask,
        coverage_fraction=request.parameters.coverage_fraction,
        weather={},
        scenario={},
        transport={},
        scope={},
        resources={},
        ignitions=(),
        spatial_budgets=(),
    )
    resolved = resolve_vegetation_physics(case).arrays
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        if not np.allclose(
            getattr(resolved, name), getattr(request.parameters, name), rtol=1e-12, atol=1e-7
        ):
            raise ValueError(f"material input differs from frozen physics: {name}")
    return {
        "all_physical_parameter_fields_match": True,
        "physical_parameter_field_count": len(V3CellPhysicsArrays.__dataclass_fields__),
        "library_id": library.library_id,
        "evidence": library.evidence,
        "production_ready": library.production_ready,
        "case_material_evidence_raster_present": request.material_evidence_index is not None,
        "roof_profile_id": binding["roof_profile_id"],
        "material_profiles": {str(k): v for k, v in assignments.items()},
    }


@dataclass
class StrategyInputs:
    manifest: dict
    paths: dict
    bundle: object
    inventory: object
    experiment: dict
    costs: dict | None
    receipt: dict


def load_strategy_inputs(path):
    path = Path(path).resolve()
    config = read_json(path)
    if config.get("schema_version") != "heritage-strategy-input-1":
        raise ValueError("unsupported unified strategy input schema")
    paths = {k: (path.parent / v).resolve() for k, v in config["inputs"].items()}
    required = {
        "imagery_tiff",
        "image_reference",
        "annotations",
        "footprints",
        "materials",
        "heritage",
        "prepared_case",
    }
    if not required <= paths.keys():
        raise ValueError(f"missing required village inputs: {sorted(required - paths.keys())}")
    bundle = load_v3_case_bundle(paths["prepared_case"])
    crs = CRS.from_user_input(config["spatial_crs"])
    if (
        not crs.is_projected
        or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2])
        or crs != CRS.from_user_input(bundle.request.grid.crs)
    ):
        raise ValueError("footprints and prepared case must use the declared projected metre CRS")
    identities = read_json(paths["heritage"])
    if identities.get("schema_version") != "heritage-inventory-1":
        raise ValueError("unsupported heritage list schema")
    inventory = load_inventory(
        bundle,
        paths["footprints"],
        identities,
        annotations=paths["annotations"],
        reference=paths["image_reference"],
    )
    if not inventory.targets:
        raise ValueError("at least one identified heritage target is required")
    reference = read_json(paths["image_reference"])
    with rasterio.open(paths["imagery_tiff"]) as image:
        if image.crs is None or CRS.from_user_input(image.crs) != crs:
            raise ValueError("prepare the imagery in the declared metre CRS before binding")
        if (image.width, image.height) != (
            reference["width_px"],
            reference["height_px"],
        ) or not np.allclose(
            tuple(image.transform)[:6], reference["affine_pixel_to_world"], rtol=0, atol=1e-8
        ):
            raise ValueError("TIFF and annotation image reference are not registered")
        extent = box(*image.bounds)
        if any(b.geometry.difference(extent).area > 1e-7 for b in inventory.buildings):
            raise ValueError("building footprint falls outside the registered TIFF")
        image_info = {
            "crs": image.crs.to_string(),
            "width": image.width,
            "height": image.height,
            "pixel_size_m": list(image.res),
            "bounds_m": list(image.bounds),
        }
    material = read_json(paths["materials"])
    paths["profile_library"] = (paths["materials"].parent / material["profile_library"]).resolve()
    material_report = validate_material_binding(bundle, material, paths["profile_library"])
    hashes = {k: file_sha256(v) for k, v in paths.items() if k != "prepared_case"}
    sources = set(bundle.metadata.get("sources_sha256", {}).values())
    for name in ("imagery_tiff", "profile_library"):
        if hashes[name] not in sources:
            raise ValueError(f"{name} is not a recorded source of the prepared physical case")
    search = config["search"]
    if search["mode"] != "saved_physical_checks":
        raise ValueError("this integration entry point only reuses saved physical checks")
    paths["physical_root"] = (path.parent / search["physical_root"]).resolve()
    if search.get("additional_root"):
        paths["additional_root"] = (path.parent / search["additional_root"]).resolve()
    experiment = read_json(paths["physical_root"] / "experiment.json")
    problem = experiment["problem"]
    physical_sha = request_fingerprint(bundle.request)
    if (
        content_hash(problem) != experiment["problem_sha256"]
        or physical_sha != problem["case_sha256"]
        or inventory.sha256 != problem["inventory_sha256"]
        or bundle.metadata["transport_parameters"] != problem["transport_parameters"]
    ):
        raise ValueError("unified inputs do not match the saved physical problem")
    if sorted(search["candidate_ids"]) != problem["candidate_ids"]:
        raise ValueError("candidate scope must match saved physical checks; no implicit expansion")
    if (
        not isinstance(search["max_saved_check_lookups"], int)
        or search["max_saved_check_lookups"] <= 0
    ):
        raise ValueError("a positive saved-check lookup budget is required")
    costs = None
    if config.get("costs") is not None:
        paths["costs"] = (path.parent / config["costs"]).resolve()
        costs = read_json(paths["costs"])
        if not costs.get("evidence"):
            raise ValueError("declared costs require explicit source/estimate evidence")
        hashes["costs"] = file_sha256(paths["costs"])
    receipt = {
        "schema_version": "heritage-strategy-input-receipt-1",
        "village_id": config["village_id"],
        "manifest_sha256": file_sha256(path),
        "input_file_sha256": hashes,
        "case_request_sha256": physical_sha,
        "inventory_sha256": inventory.sha256,
        "physical_problem_sha256": experiment["problem_sha256"],
        "building_count": len(inventory.buildings),
        "eligible_building_count": len(inventory.candidate_ids),
        "search_candidate_count": len(problem["candidate_ids"]),
        "heritage_targets": [asdict(t) for t in inventory.targets],
        "imagery": image_info,
        "materials": material_report,
        "frozen_experiment": {
            k: problem[k]
            for k in (
                "horizon_s",
                "scales_m",
                "quadrature_spacing_m",
                "source_policy",
                "overlap_policy",
            )
        },
        "prepared_case_is_required": True,
        "economic_cost_status": "missing" if costs is None else "declared_with_evidence",
        "pending_data": config["pending_data"],
    }
    return StrategyInputs(config, paths, bundle, inventory, experiment, costs, receipt)


def demolition_geometry(inventory, identifiers):
    selected = set(identifiers)
    if not selected <= set(inventory.candidate_ids):
        raise ValueError("export cannot demolish ineligible or protected buildings")
    removed = unary_union([inventory.by_id[i].geometry for i in sorted(selected)])
    retained = unary_union(
        [b.geometry for b in inventory.buildings if b.building_id not in selected]
    )
    cleared, shared = removed.difference(retained), removed.intersection(retained)
    allocated, rows = cleared.difference(cleared), []
    for identifier in sorted(selected):
        footprint = inventory.by_id[identifier].geometry
        part = footprint.intersection(cleared).difference(allocated)
        rows.append(
            {
                "building_id": identifier,
                "action": "whole_building_demolition",
                "nominal_footprint_area_m2": float(footprint.area),
                "cleared_area_allocated_m2": float(part.area),
                "shared_with_retained_area_m2": float(footprint.intersection(retained).area),
            }
        )
        allocated = allocated.union(part)
    ledger = {
        "demolished_count": len(selected),
        "nominal_footprint_area_m2": fsum(r["nominal_footprint_area_m2"] for r in rows),
        "removed_footprint_union_m2": float(removed.area),
        "cleared_roof_union_area_m2": float(cleared.area),
        "retained_shared_roof_area_m2": float(shared.area),
        "cleared_allocation_rule": "sorted_building_id_nonoverlapping_partition_not_ownership",
    }
    return rows, ledger, cleared, shared


def export_strategy(inputs, replay, output):
    """Export GIS coordinates, exact area bookkeeping and explicit missing costs."""
    output = Path(output)
    strategy = replay["strategy_result"]
    selected = strategy["best_ids"]
    if selected is None:
        raise ValueError("no verified feasible strategy is available for map/list export")
    rows, ledger, cleared, shared = demolition_geometry(inputs.inventory, selected)
    costs = inputs.costs
    for row in rows:
        row.update(
            declared_cost=None if costs is None else costs["values"][row["building_id"]],
            cost_unit=None if costs is None else costs["unit"],
            cost_status="pending" if costs is None else "declared_with_evidence",
            eligibility_evidence=inputs.inventory.candidate_evidence,
            implementation_feasibility_verified=False,
        )
    with (output / "demolition-list.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]) if rows else ["building_id", "action"]
        )
        writer.writeheader()
        writer.writerows(rows)
    cost_report = {
        **ledger,
        "objective_policy": strategy["objective_policy"],
        "declared_economic_cost": strategy["best_declared_cost"],
        "cost_unit": None if costs is None else costs["unit"],
        "economic_cost_status": "missing" if costs is None else "declared_with_evidence",
        "cost_evidence": None if costs is None else costs["evidence"],
        "household_count": None,
        "implementation_feasibility_verified": False,
        "search_candidate_ids": strategy["candidate_ids"],
        "optimality_proven_within_declared_search_problem": strategy["optimality_proven"],
        "whole_village_optimality_claimed": False,
        "pending_data": inputs.manifest["pending_data"],
    }
    write_json(output / "cost-summary.json", cost_report)
    project = Transformer.from_crs(inputs.manifest["spatial_crs"], "EPSG:4326", always_xy=True)

    def feature(geometry, properties):
        return {
            "type": "Feature",
            "geometry": mapping(transform(project.transform, geometry)),
            "properties": properties,
        }

    targets = {t.building_id: t for t in inputs.inventory.targets}
    features = []
    for b in inputs.inventory.buildings:
        features.append(
            feature(
                b.geometry,
                {
                    "building_id": b.building_id,
                    "action": "protect"
                    if b.building_id in targets
                    else "demolish"
                    if b.building_id in selected
                    else "retain",
                    "heritage_role": targets[b.building_id].role
                    if b.building_id in targets
                    else None,
                    "in_search_candidates": b.building_id in strategy["candidate_ids"],
                    "nominal_area_m2": float(b.geometry.area),
                    "roof_profile_id": inputs.receipt["materials"]["roof_profile_id"],
                },
            )
        )
    for name, items in (
        ("strategy-buildings.geojson", features),
        (
            "clearance.geojson",
            [
                feature(g, {"action": action, "area_m2": float(g.area)})
                for g, action in ((cleared, "clear_exclusive_roof"), (shared, "retain_shared_roof"))
                if not g.is_empty
            ],
        ),
    ):
        write_json(output / name, {"type": "FeatureCollection", "features": items})
    write_json(output / "input-receipt.json", inputs.receipt)
    write_json(output / "strategy-result.json", replay)
    write_json(
        output / "spatial-reference.json",
        {
            "input_crs": inputs.manifest["spatial_crs"],
            "geojson_crs": "EPSG:4326",
            "geojson_axis_order": "longitude_latitude",
            "area_calculation_crs": inputs.manifest["spatial_crs"],
            "clearance_is_vector_geometry_not_a_fire_safety_distance": True,
        },
    )
    return cost_report
