"""Read prepared heritage identities without altering physical inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pyfireca.planning.heritage import (
    HeritageTarget,
    bind_labelme_targets,
    inventory_from_records,
)
from pyfireca.village.annotations import load_labelme_buildings
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def resolve_candidate_ids(config, records):
    """An explicit list or a declared provisional policy, never inferred permission."""
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


def load_inventory(bundle, structures_path, identities, *, annotations=None, reference=None):
    records = json.loads(Path(structures_path).read_text())["buildings"]
    targets = tuple(HeritageTarget(**t) for t in identities.get("targets", ()))
    if annotations is not None:
        raw = Path(annotations)
        latest = load_labelme_buildings(
            raw,
            json.loads(Path(reference).read_text()),
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
    return inventory_from_records(
        records,
        bundle.metadata["building_labels"],
        targets=targets,
        protected_ids=tuple(identities.get("protected_ids", ())),
        fixed_source_ids=tuple(identities.get("fixed_source_ids", ())),
        candidate_ids=resolve_candidate_ids(identities, records),
        candidate_evidence=identities.get(
            "candidate_evidence", "pending_survey_no_candidates_declared"
        ),
    )


def load_study(path):
    """Preserve the existing P1 study contract and its inventory fingerprint."""
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if config.get("schema_version") != "heritage-p1-study-1":
        raise ValueError("unsupported study schema")
    bundle = load_v3_case_bundle(path.parent / config["case_dir"])
    structures = config.get("structures_path")
    structures = (
        path.parent / structures
        if structures
        else (Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json")
    )
    inventory = load_inventory(
        bundle,
        structures,
        config,
        annotations=path.parent / config["labelme_annotations"]
        if config.get("labelme_annotations")
        else None,
        reference=path.parent / config["image_reference"]
        if config.get("image_reference")
        else None,
    )
    return (
        bundle,
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        inventory,
    )
