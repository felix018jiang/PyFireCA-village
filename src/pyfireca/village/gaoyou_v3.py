"""Build a traceable V3 request from the frozen Gaoyou V01--V04 packages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import V3Scenario, V3SimulationRequest
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.workflow_v3 import write_v3_case_bundle

LANDCOVER_LABELS = {
    0: "裸地/道路(当前未解析)",
    1: "草地",
    2: "灌木",
    3: "林下枯落物(当前未解析)",
    4: "树木等效燃料",
    5: "建筑等效燃料",
}
RGB_HYPOTHESIS_LANDCOVER_LABELS = {
    0: "裸地/道路(RGB推测, 不燃)",
    1: "草地(RGB推测)",
    2: "灌木(RGB推测)",
    3: "林下枯落物(当前未解析)",
    4: "树木等效燃料(RGB推测)",
    5: "建筑等效燃料(建筑标注)",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(mapping: dict[str, Any], name: str) -> float:
    value = mapping[name]
    if isinstance(value, dict):
        value = value["value"]
    return float(value)


def _load_parameters(v03_dir: Path) -> V3CellPhysicsArrays:
    arrays = v03_dir / "arrays"
    values = {
        name: np.load(arrays / f"{name}.npy", allow_pickle=False)
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    return V3CellPhysicsArrays(**values)


def _complete_unknown_as_inert_receivers(
    parameters: V3CellPhysicsArrays,
    unknown_mask: np.ndarray,
    cell_area_m2: float,
) -> V3CellPhysicsArrays:
    """Give RGB-completed inert cells receiver quantities without fuel."""

    fields = {
        name: getattr(parameters, name).copy() for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    for name in fields:
        if name not in {
            "coverage_fraction",
            "covered_area_m2",
            "receiver_area_ratio",
            "receiver_area_m2",
        }:
            fields[name][unknown_mask] = 0.0
    fields["coverage_fraction"][unknown_mask] = 1.0
    fields["covered_area_m2"][unknown_mask] = cell_area_m2
    fields["receiver_area_ratio"][unknown_mask] = 1.0
    fields["receiver_area_m2"][unknown_mask] = cell_area_m2
    return V3CellPhysicsArrays(**fields).validated(parameters.coverage_fraction.shape, cell_area_m2)


def _building_index(
    prepared_dir: Path, shape: tuple[int, int]
) -> tuple[np.ndarray, dict[int, str]]:
    import csv

    values = np.full(shape, -1, dtype=np.int32)
    identifiers: dict[str, int] = {}
    with (prepared_dir / "building_overlap.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for building_id in sorted({row["building_id"] for row in rows}):
        identifiers[building_id] = len(identifiers)
    for row in rows:
        cell_row, cell_col = int(row["row"]), int(row["col"])
        candidate = identifiers[row["building_id"]]
        current = values[cell_row, cell_col]
        values[cell_row, cell_col] = candidate if current < 0 else min(current, candidate)
    return values, {index: building_id for building_id, index in identifiers.items()}


def build_gaoyou_v3_case(
    prepared_dir: str | Path,
    physics_dir: str | Path,
    transport_report_dir: str | Path,
    output_dir: str | Path,
    *,
    case_snapshot: str | Path | None = None,
    end_time_s: float = 30_000.0,
    sample_interval_s: float = 300.0,
    unknown_policy: str = "exclude",
    fill_unknown_as_bare_or_road: bool = False,
    max_edge_count: int | None = None,
    max_memory_mb: int | None = None,
    blocker_assumption: str = (
        "V04 synthetic path transmissivity 1.0; field blocker calibration pending"
    ),
) -> Path:
    """Create a classified-area Gaoyou V3 bundle without running the solver."""

    prepared = Path(prepared_dir).resolve()
    physics = Path(physics_dir).resolve()
    transport_report = Path(transport_report_dir).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Gaoyou V3 case output already exists: {output}")
    prepared_manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    resource_report = json.loads(
        (transport_report / "resource_report.json").read_text(encoding="utf-8")
    )
    grid_data = prepared_manifest["grid"]
    grid = VillageGridSpec(**grid_data)
    domain = np.load(prepared / "arrays/domain_mask.npy", allow_pickle=False)
    known = np.load(prepared / "arrays/known_mask.npy", allow_pickle=False)
    landcover = np.load(prepared / "arrays/landcover_index.npy", allow_pickle=False).astype(
        np.int32, copy=False
    )
    parameters = _load_parameters(physics)
    if domain.shape != grid.shape or known.shape != grid.shape or landcover.shape != grid.shape:
        raise ValueError("Gaoyou V01/V03 arrays do not share the grid shape")
    original_unknown = domain & ~known
    original_unknown_count = int(np.count_nonzero(original_unknown))
    if fill_unknown_as_bare_or_road:
        landcover = landcover.copy()
        landcover[original_unknown] = 0
        known = domain.copy()
        inert_completion = original_unknown | (
            known & (landcover == 0) & (parameters.receiver_area_m2 <= 0.0) & domain
        )
        parameters = _complete_unknown_as_inert_receivers(
            parameters, inert_completion, grid.cell_area_m2
        )
        unknown_policy = "error"
    else:
        inert_completion = np.zeros(domain.shape, dtype=np.bool_)
    burnable = parameters.pyrolysable_dry_fuel_mass_kg > 0.0
    if np.any(burnable & ~known) or np.any(burnable & ~domain):
        raise ValueError("V03 burnable cells must be known cells inside the V01 domain")
    building, building_labels = _building_index(prepared, grid.shape)

    case_path = Path(case_snapshot) if case_snapshot else physics / "case.snapshot.yml"
    case_data = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    configured_max_edge_count = int(case_data["resources"]["max_edge_count"])
    configured_max_memory_mb = int(case_data["resources"]["max_memory_mb"])
    for name, value in (
        ("max_edge_count", max_edge_count),
        ("max_memory_mb", max_memory_mb),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(f"{name} override must be a positive integer")
    effective_max_edge_count = (
        configured_max_edge_count if max_edge_count is None else max_edge_count
    )
    effective_max_memory_mb = configured_max_memory_mb if max_memory_mb is None else max_memory_mb
    transport_data = case_data["transport"]
    transport = V3RadiativeTransportParameters(
        exposure_radius_m=_value(transport_data, "exposure_radius_m"),
        near_field_regularization_m=_value(transport_data, "near_field_regularization_m"),
        atmospheric_extinction_m_inv=_value(transport_data, "atmospheric_extinction_m_inv"),
        wind_speed_m_s=_value(case_data["weather"], "wind_speed_m_s"),
        wind_from_deg=_value(case_data["weather"], "wind_from_deg"),
        flame_tilt_coefficient_s_m=_value(transport_data, "flame_tilt_coefficient_s_m"),
    )
    blocker_transmissivity = np.ones(grid.shape, dtype=np.float64)
    edges = build_v3_radiative_transport_edges(
        grid,
        burnable,
        known,
        parameters.receiver_area_m2,
        parameters.source_height_m,
        parameters.receiver_height_m,
        blocker_transmissivity,
        transport,
        max_edge_count=effective_max_edge_count,
        max_memory_mb=effective_max_memory_mb,
        profile_id=np.load(physics / "arrays/profile_id.npy", allow_pickle=False),
    )
    ignition = json.loads((prepared / "ignition.json").read_text(encoding="utf-8"))
    scenario_data = case_data["scenario"]
    scenario = V3Scenario(
        max_dt_s=_value(scenario_data, "max_dt_s"),
        end_time_s=end_time_s,
        ambient_temperature_K=_value(scenario_data, "ambient_temperature_K"),
        heating_temperature_delta_K=0.01,
        ignitions=(VillageIgnitionEvent(0.0, int(ignition["row"]), int(ignition["col"])),),
    )
    request = V3SimulationRequest(
        grid=grid,
        domain_mask=domain,
        known_mask=known,
        burnable_mask=burnable,
        source_mask=burnable.copy(),
        target_mask=(known.copy() if fill_unknown_as_bare_or_road else burnable.copy()),
        piloted_ignition_mask=burnable.copy(),
        material_index=landcover.copy(),
        landcover_index=landcover.copy(),
        building_index=building,
        parameters=parameters,
        exposure_edges=edges,
        scenario=scenario,
        unknown_policy=unknown_policy,
        sample_interval_s=sample_interval_s,
    )
    sources = {
        "prepared_manifest_sha256": _sha256(prepared / "manifest.json"),
        "physics_manifest_sha256": _sha256(physics / "manifest.json"),
        "transport_resource_report_sha256": _sha256(transport_report / "resource_report.json"),
        "case_snapshot_sha256": _sha256(case_path),
    }
    metadata = {
        "scenario_id": (
            "gaoyou-rgb-hypothesis-full-domain-v3"
            if fill_unknown_as_bare_or_road
            else "gaoyou-v07-classified-area-v3"
        ),
        "evidence": (
            "synthetic_profiles_with_rgb_hypothesis_inert_completion"
            if fill_unknown_as_bare_or_road
            else "synthetic_profiles_with_observed_building_and_rgb_hypothesis_spatial_masks"
        ),
        "assumptions": True,
        "simulation_scope": (
            "hypothetical_full_domain" if fill_unknown_as_bare_or_road else "classified_area_only"
        ),
        "unknown_policy": unknown_policy,
        "unknown_domain_cell_count": original_unknown_count,
        "hypothetically_filled_unknown_cell_count": (
            int(np.count_nonzero(inert_completion)) if fill_unknown_as_bare_or_road else 0
        ),
        "outside_domain_cell_count": int(np.count_nonzero(~domain)),
        "classified_burnable_cell_count": int(np.count_nonzero(burnable)),
        "building_label_count": len(building_labels),
        "ignition_source": ignition,
        "blocker_assumption": blocker_assumption,
        "spatial_assets_dir": str(prepared),
        "resource_limits": {
            "case_snapshot_max_edge_count": configured_max_edge_count,
            "case_snapshot_max_memory_mb": configured_max_memory_mb,
            "effective_max_edge_count": effective_max_edge_count,
            "effective_max_memory_mb": effective_max_memory_mb,
            "overrides_applied": max_edge_count is not None or max_memory_mb is not None,
        },
        "v04_reference_edge_count": resource_report.get("graph_edge_count"),
        "rebuilt_edge_count": len(edges.source),
        "sources_sha256": sources,
    }
    result = write_v3_case_bundle(
        request,
        output,
        material_labels=(
            RGB_HYPOTHESIS_LANDCOVER_LABELS if fill_unknown_as_bare_or_road else LANDCOVER_LABELS
        ),
        landcover_labels=(
            RGB_HYPOTHESIS_LANDCOVER_LABELS if fill_unknown_as_bare_or_road else LANDCOVER_LABELS
        ),
        metadata=metadata,
    )
    (result / "classification_report.snapshot.json").write_text(
        (prepared / "classification_report.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (result / "source_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


__all__ = [
    "LANDCOVER_LABELS",
    "RGB_HYPOTHESIS_LANDCOVER_LABELS",
    "build_gaoyou_v3_case",
]
