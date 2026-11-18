"""Fixed heritage identities and explicit, survey-aware building inventories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

from pyfireca.planning.contracts import require_id
from pyfireca.planning.observations import build_object_observations
from pyfireca.village.vegetation_prepare import geometry_to_polygons


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class HeritageTarget:
    building_id: str
    role: str
    identity_evidence: str
    identity_status: str = "pending"

    def __post_init__(self):
        require_id("building_id", self.building_id)
        require_id("identity_evidence", self.identity_evidence)
        if self.role not in {"gulou", "fengyuqiao"}:
            raise ValueError("heritage role must be gulou or fengyuqiao")
        if self.identity_status not in {"pending", "annotation_only", "surveyed", "synthetic"}:
            raise ValueError("invalid heritage identity status")


@dataclass(frozen=True, slots=True)
class BuildingObject:
    building_id: str
    object_id: int
    source_label: str
    geometry: object

    def __post_init__(self):
        require_id("building_id", self.building_id)
        if isinstance(self.object_id, bool) or not isinstance(self.object_id, int):
            raise ValueError("object_id must be an integer")
        if self.object_id < 0 or self.source_label not in {"building", "fengyuqiao"}:
            raise ValueError("invalid building identity or physical type")
        g = self.geometry
        if g.geom_type not in {"Polygon", "MultiPolygon"} or not g.is_valid or g.is_empty:
            raise ValueError("building requires valid positive polygon geometry")
        if not np.isfinite(g.area) or g.area <= 0:
            raise ValueError("building requires finite positive area")

    def record(self):
        return {
            "building_id": self.building_id,
            "polygons": geometry_to_polygons(self.geometry),
            "properties": {"source_label": self.source_label},
        }


@dataclass(frozen=True, slots=True)
class BuildingInventory:
    buildings: tuple[BuildingObject, ...]
    targets: tuple[HeritageTarget, ...] = ()
    protected_ids: tuple[str, ...] = ()
    fixed_source_ids: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    candidate_evidence: str = "pending_survey_no_candidates_declared"

    def __post_init__(self):
        for name, cls in (("buildings", BuildingObject), ("targets", HeritageTarget)):
            items = getattr(self, name)
            if not isinstance(items, tuple) or any(not isinstance(x, cls) for x in items):
                raise TypeError(f"{name} must be a typed tuple")
        ids = [b.building_id for b in self.buildings]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("building identities must be nonempty and unique")
        if len({b.object_id for b in self.buildings}) != len(ids):
            raise ValueError("numeric building identities must be unique")
        target_ids = [t.building_id for t in self.targets]
        if len(set(target_ids)) != len(target_ids) or not set(target_ids) <= set(ids):
            raise ValueError("target IDs must be unique members of the inventory")
        for name in ("protected_ids", "fixed_source_ids", "candidate_ids"):
            values = getattr(self, name)
            if (
                not isinstance(values, tuple)
                or any(not isinstance(v, str) for v in values)
                or len(set(values)) != len(values)
                or not set(values) <= set(ids)
            ):
                raise ValueError(f"{name} must be unique inventory IDs")
        forbidden = set(self.protected_ids) | set(target_ids) | set(self.fixed_source_ids)
        if forbidden & set(self.candidate_ids):
            raise ValueError("demolition candidates conflict with protected targets or sources")
        if set(target_ids) & set(self.fixed_source_ids):
            raise ValueError("core self-ignition is outside the transmission-protection task")
        if any(self.by_id[i].source_label != "building" for i in self.candidate_ids):
            raise ValueError("only declared ordinary buildings may be demolition candidates")
        require_id("candidate_evidence", self.candidate_evidence)
        if (
            self.candidate_ids
            and self.candidate_evidence == "pending_survey_no_candidates_declared"
        ):
            raise ValueError(
                "declared candidates require explicit eligibility or hypothetical evidence"
            )

    @property
    def by_id(self):
        return {b.building_id: b for b in self.buildings}

    @property
    def labels(self):
        return {str(b.object_id): b.building_id for b in self.buildings}

    @property
    def structures(self):
        return tuple(b.record() for b in self.buildings)

    def to_mapping(self):
        return {
            "buildings": [{**b.record(), "object_id": b.object_id} for b in self.buildings],
            "targets": [asdict(t) for t in self.targets],
            "protected_ids": self.protected_ids,
            "fixed_source_ids": self.fixed_source_ids,
            "candidate_ids": self.candidate_ids,
            "candidate_evidence": self.candidate_evidence,
        }

    @property
    def sha256(self):
        return content_hash(self.to_mapping())


def inventory_from_records(structures, labels, **kwargs):
    """Preserve original IDs, including IDs that lost raster ownership."""
    owners = {value: int(key) for key, value in labels.items()}
    if len(owners) != len(labels) or len(structures) != len(labels):
        raise ValueError("annotation and numeric identity maps must match exactly")
    buildings = []
    for row in structures:
        identifier = row["building_id"]
        if identifier not in owners:
            raise ValueError("annotation has no declared numeric identity")
        geometry = unary_union([Polygon(p["exterior"], p["holes"]) for p in row["polygons"]])
        buildings.append(
            BuildingObject(
                identifier, owners[identifier], row["properties"]["source_label"], geometry
            )
        )
    return BuildingInventory(tuple(sorted(buildings, key=lambda b: b.object_id)), **kwargs)


def bind_labelme_targets(base_structures, updated_structures, *, identity_evidence):
    """Bind new role labels to stable physical IDs and disclose stale geometry.

    Source indices are accepted only with an exact geometry match for targets.
    Other changed outlines block simulation until the case is rebuilt.
    """
    base = {row["properties"]["source_shape_index"]: row for row in base_structures}
    updated = {row["properties"]["source_shape_index"]: row for row in updated_structures}
    if len(base) != len(base_structures) or len(updated) != len(updated_structures):
        raise ValueError("annotation source indices must be unique")
    if set(base) != set(updated):
        raise ValueError("annotation identities changed; prepare and reconcile a new case first")
    targets, differences = [], []
    for index, current in sorted(updated.items()):
        original = base[index]
        a, b = (
            unary_union([Polygon(p["exterior"], p["holes"]) for p in row["polygons"]])
            for row in (original, current)
        )
        matches = a.equals_exact(b, 1e-7)
        if not matches:
            differences.append(
                {
                    "building_id": original["building_id"],
                    "source_shape_index": index,
                    "old_area_m2": float(a.area),
                    "updated_area_m2": float(b.area),
                    "symmetric_difference_m2": float(a.symmetric_difference(b).area),
                    "old_centroid_m": [a.centroid.x, a.centroid.y],
                    "updated_centroid_m": [b.centroid.x, b.centroid.y],
                }
            )
        role = current["properties"]["source_label"]
        if role in {"gulou", "fengyuqiao"}:
            if not matches:
                raise ValueError("heritage outline changed; rebuild its physical geometry first")
            targets.append(
                HeritageTarget(original["building_id"], role, identity_evidence, "annotation_only")
            )
        elif role != original["properties"]["source_label"]:
            raise ValueError("physical annotation class changed; prepare a new case first")
    return tuple(targets), {
        "role_bindings": [asdict(t) for t in targets],
        "geometry_changes": differences,
        "physical_case_matches_updated_annotations": not differences,
        "physical_geometry_policy": "preserve_parent_geometry_for_audit_only_if_stale",
        "identity_binding": "source_index_and_exact_target_geometry_not_new_label_order",
    }


def audit_inventory(request, inventory):
    """Audit physical representation without inventing heritage/household data."""
    observer = build_object_observations(request, inventory.structures, inventory.labels)
    geometries = [b.geometry for b in inventory.buildings]
    tree = STRtree(geometries)
    overlaps = []
    for i, geometry in enumerate(geometries):
        for j in sorted(map(int, tree.query(geometry))):
            if j <= i:
                continue
            area = float(geometry.intersection(geometries[j]).area)
            if area > 1e-6:
                overlaps.append(
                    {
                        "left": inventory.buildings[i].building_id,
                        "right": inventory.buildings[j].building_id,
                        "overlap_m2": area,
                    }
                )
    counts = np.zeros(request.grid.height * request.grid.width, dtype=np.int32)
    for obj in observer.objects:
        counts[obj.cell_ids] += 1
    represented = set(map(int, np.unique(request.building_index))) - {-1}
    targets = []
    objects = {obj.label: obj for obj in observer.objects}
    for target in inventory.targets:
        obj = objects[target.building_id]
        geometry = inventory.by_id[target.building_id].geometry
        nearest = sorted(
            (b for b in inventory.buildings if b.building_id != target.building_id),
            key=lambda b: (geometry.distance(b.geometry), b.building_id),
        )[:20]
        targets.append(
            {
                **asdict(target),
                "vector_area_m2": float(geometry.area),
                "shared_temperature_cells": int((counts[obj.cell_ids] > 1).sum()),
                "nearest_objects": [
                    {
                        "building_id": b.building_id,
                        "gap_m": float(geometry.distance(b.geometry)),
                        "candidate_status": "declared"
                        if b.building_id in inventory.candidate_ids
                        else "not_declared",
                    }
                    for b in nearest
                ],
            }
        )
    missing_roles = sorted({"gulou", "fengyuqiao"} - {t.role for t in inventory.targets})
    missing = [
        {
            "field": "heritage_identity",
            "role": role,
            "status": "pending",
            "impact": "blocks_real_dual_heritage_claim",
        }
        for role in missing_roles
    ]
    # Source annotations carry geometry only; these survey facts are not implied.
    missing.extend(
        {"field": field, "status": "not_supplied_to_this_inventory", "impact": impact}
        for field, impact in (
            ("demolition_eligibility", "blocks_implementation_feasibility_claim"),
            ("household_identity_and_occupancy", "household_impact_unknown_not_zero"),
            ("floors_and_gross_floor_area", "use_footprint_area_only"),
            ("structural_independence_and_eaves", "blocks_structural_feasibility_claim"),
            ("cultural_activity_and_access_spaces", "blocks_cultural_space_feasibility_claim"),
            ("post_demolition_ground_and_debris", "requires_explicit_physical_assumption"),
            ("measured_timber_and_moisture", "equivalent_fuel_assumption_requires_validation"),
        )
    )
    if not inventory.fixed_source_ids:
        missing.append(
            {
                "field": "fixed_source_ids",
                "status": "not_frozen",
                "impact": "required_before_internal_fire_evaluation",
            }
        )
    return {
        "schema_version": "heritage-inventory-audit-1",
        "inventory_sha256": inventory.sha256,
        "annotation_count": len(inventory.buildings),
        "ordinary_annotation_count": sum(
            b.source_label == "building"
            and b.building_id not in {t.building_id for t in inventory.targets}
            for b in inventory.buildings
        ),
        "physical_building_annotation_count": sum(
            b.source_label == "building" for b in inventory.buildings
        ),
        "bridge_annotation_count": sum(b.source_label == "fengyuqiao" for b in inventory.buildings),
        "raster_owner_count": len(represented),
        "unrepresented_owner_ids": [
            b.building_id for b in inventory.buildings if b.object_id not in represented
        ],
        "positive_area_overlaps": overlaps,
        "observation": observer.summary(),
        "targets": targets,
        "missing_data": missing,
        "candidate_ids": inventory.candidate_ids,
        "candidate_evidence": inventory.candidate_evidence,
        "dual_target_geometry_ready": not missing_roles
        and not overlaps
        and not any(t["shared_temperature_cells"] for t in targets),
        "real_dual_heritage_ready": not missing_roles
        and all(t.identity_status == "surveyed" for t in inventory.targets)
        and not overlaps
        and not any(t["shared_temperature_cells"] for t in targets),
        "implementation_feasibility_verified": False,
        "building_features": [
            {
                "type": "Feature",
                "geometry": mapping(b.geometry),
                "properties": {
                    "building_id": b.building_id,
                    "object_id": b.object_id,
                    "footprint_area_m2": float(b.geometry.area),
                    "household_count": None,
                    "demolition_eligibility": "pending",
                    "source_label": b.source_label,
                    "heritage_role": next(
                        (t.role for t in inventory.targets if t.building_id == b.building_id), None
                    ),
                    "fixed_source": b.building_id in inventory.fixed_source_ids,
                    "candidate_declared": b.building_id in inventory.candidate_ids,
                },
            }
            for b in inventory.buildings
        ],
    }
