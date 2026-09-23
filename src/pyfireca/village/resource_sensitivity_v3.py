"""Resource-only sensitivity reports for a V3 village case.

This module deliberately stops before graph construction and simulation.  It
uses the exact source/target masks and the same candidate-pair counter as the
V3 transport builder so a full-domain RGB-hypothesis case can be sized without
changing fire thresholds or consuming memory for an edge graph.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    estimate_v3_transport_resources,
)


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


def _frame_count(end_time_s: float, sample_interval_s: float) -> int:
    if end_time_s < 0.0 or sample_interval_s <= 0.0:
        raise ValueError("end_time_s must be non-negative and sample_interval_s must be positive")
    return ceil(end_time_s / sample_interval_s - 1e-12) + 1


def _load_physics(physics_dir: Path) -> V3CellPhysicsArrays:
    arrays = physics_dir / "arrays"
    values = {
        name: np.load(arrays / f"{name}.npy", allow_pickle=False)
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    return V3CellPhysicsArrays(**values)


def _load_transport(case_snapshot: Path) -> tuple[V3RadiativeTransportParameters, dict[str, Any]]:
    case = yaml.safe_load(case_snapshot.read_text(encoding="utf-8"))
    transport_data = case["transport"]
    transport = V3RadiativeTransportParameters(
        exposure_radius_m=_value(transport_data, "exposure_radius_m"),
        near_field_regularization_m=_value(transport_data, "near_field_regularization_m"),
        atmospheric_extinction_m_inv=_value(transport_data, "atmospheric_extinction_m_inv"),
        wind_speed_m_s=_value(case["weather"], "wind_speed_m_s"),
        wind_from_deg=_value(case["weather"], "wind_from_deg"),
        flame_tilt_coefficient_s_m=_value(transport_data, "flame_tilt_coefficient_s_m"),
    )
    return transport, case


def _scenario_report(
    grid: VillageGridSpec,
    source_mask: np.ndarray,
    target_mask: np.ndarray,
    transport: V3RadiativeTransportParameters,
    *,
    replay_frame_count: int,
    max_edge_count: int,
    max_memory_mb: int,
) -> dict[str, Any]:
    estimate = estimate_v3_transport_resources(
        grid,
        source_mask,
        target_mask,
        transport,
        replay_frame_count=replay_frame_count,
    )
    values = estimate.as_dict()
    construction_ok = estimate.construction_peak_bytes_estimate <= max_memory_mb * 1_000_000
    combined_ok = estimate.construction_plus_replay_bytes_estimate <= max_memory_mb * 1_000_000
    edge_ok = estimate.candidate_edge_upper_bound <= max_edge_count
    status = "ready_for_resource_gate"
    if not edge_ok or not construction_ok or not combined_ok:
        status = "blocked_by_configured_resource_gate"
    return {
        "radius_m": transport.exposure_radius_m,
        "transport": {
            "near_field_regularization_m": transport.near_field_regularization_m,
            "atmospheric_extinction_m_inv": transport.atmospheric_extinction_m_inv,
            "wind_speed_m_s": transport.wind_speed_m_s,
            "wind_from_deg": transport.wind_from_deg,
            "flame_tilt_coefficient_s_m": transport.flame_tilt_coefficient_s_m,
        },
        "estimate": values,
        "configured_limits": {
            "max_edge_count": max_edge_count,
            "max_memory_mb": max_memory_mb,
        },
        "checks": {
            "edge_count_within_limit": edge_ok,
            "construction_peak_within_limit": construction_ok,
            "construction_plus_replay_within_limit": combined_ok,
        },
        "minimum_limits_if_this_radius_is_selected": {
            "max_edge_count": estimate.candidate_edge_upper_bound,
            "max_memory_mb_for_construction": ceil(
                estimate.construction_peak_bytes_estimate / 1_000_000
            ),
            "max_memory_mb_for_construction_plus_replay": ceil(
                estimate.construction_plus_replay_bytes_estimate / 1_000_000
            ),
        },
        "status": status,
    }


def build_resource_sensitivity_report(
    spatial_dir: str | Path,
    physics_dir: str | Path,
    case_snapshot: str | Path,
    *,
    output: str | Path | None = None,
    radii_m: Iterable[float] = (4.0, 6.0, 8.0, 10.0),
    end_time_s: float = 30_000.0,
    sample_interval_s: float = 300.0,
) -> dict[str, Any]:
    """Return a deterministic resource report without building transport edges.

    ``spatial_dir`` should be the copied RGB-completed V01 package.  Its
    ``known_mask`` is the receiver mask for the hypothetical full-domain
    scenario; the V03 physics package still provides the independently frozen
    burnable source quantities.  Radius entries are diagnostic alternatives,
    not changes to the active physical case.
    """

    spatial = Path(spatial_dir).resolve()
    physics_path = Path(physics_dir).resolve()
    snapshot = Path(case_snapshot).resolve()
    manifest = json.loads((spatial / "manifest.json").read_text(encoding="utf-8"))
    grid = VillageGridSpec(**manifest["grid"])
    domain = np.load(spatial / "arrays/domain_mask.npy", allow_pickle=False)
    known = np.load(spatial / "arrays/known_mask.npy", allow_pickle=False)
    if domain.shape != grid.shape or known.shape != grid.shape:
        raise ValueError("spatial masks do not match the manifest grid")
    if domain.dtype != np.bool_ or known.dtype != np.bool_:
        raise TypeError("domain_mask and known_mask must be boolean arrays")
    if np.any(known & ~domain):
        raise ValueError("known cells must be inside the domain")
    physics = _load_physics(physics_path)
    parameters = physics.validated(grid.shape, grid.cell_area_m2)
    source_mask = parameters.pyrolysable_dry_fuel_mass_kg > 0.0
    target_mask = domain & known
    if np.any(source_mask & ~target_mask):
        raise ValueError("V03 burnable sources must be known cells in the completed domain")
    base_transport, case = _load_transport(snapshot)
    max_edge_count = int(case["resources"]["max_edge_count"])
    max_memory_mb = int(case["resources"]["max_memory_mb"])
    replay_frame_count = _frame_count(end_time_s, sample_interval_s)
    radii = tuple(float(radius) for radius in radii_m)
    if not radii or any(not np.isfinite(radius) or radius <= 0.0 for radius in radii):
        raise ValueError("radii_m must contain positive finite values")
    scenarios = [
        _scenario_report(
            grid,
            source_mask,
            target_mask,
            replace(base_transport, exposure_radius_m=radius),
            replay_frame_count=replay_frame_count,
            max_edge_count=max_edge_count,
            max_memory_mb=max_memory_mb,
        )
        for radius in radii
    ]
    current = next(
        (
            scenario
            for scenario in scenarios
            if scenario["radius_m"] == base_transport.exposure_radius_m
        ),
        None,
    )
    if current is None:
        current = _scenario_report(
            grid,
            source_mask,
            target_mask,
            base_transport,
            replay_frame_count=replay_frame_count,
            max_edge_count=max_edge_count,
            max_memory_mb=max_memory_mb,
        )
    report: dict[str, Any] = {
        "schema_version": "village-v3-resource-sensitivity-1",
        "status": current["status"],
        "simulation_executed": False,
        "graph_construction_executed": False,
        "purpose": "size_full_domain_rgb_hypothesis_before_dynamic_run",
        "inputs": {
            "spatial_dir": str(spatial),
            "physics_dir": str(physics_path),
            "case_snapshot": str(snapshot),
            "spatial_manifest_sha256": _sha256(spatial / "manifest.json"),
            "physics_manifest_sha256": _sha256(physics_path / "manifest.json"),
            "case_snapshot_sha256": _sha256(snapshot),
        },
        "grid": {
            "height": grid.height,
            "width": grid.width,
            "cell_count": grid.height * grid.width,
            "cell_size_m": grid.cell_size_m,
            "crs": grid.crs,
        },
        "classification": {
            "domain_cell_count": int(np.count_nonzero(domain)),
            "known_receiver_cell_count": int(np.count_nonzero(target_mask)),
            "outside_nodata_cell_count": int(np.count_nonzero(~domain)),
            "source_burnable_cell_count": int(np.count_nonzero(source_mask)),
            "inert_receiver_cell_count": int(np.count_nonzero(target_mask & ~source_mask)),
            "target_policy": "all_known_cells_in_completed_domain",
        },
        "replay": {
            "end_time_s": end_time_s,
            "sample_interval_s": sample_interval_s,
            "frame_count": replay_frame_count,
        },
        "active_case": {
            "radius_m": base_transport.exposure_radius_m,
            "scenario": "current_case_snapshot; alternatives below are diagnostic only",
            "estimate": current,
        },
        "radius_scenarios": scenarios,
        "recommendation": {
            "current_case_is_blocked_only_if": current["status"]
            == "blocked_by_configured_resource_gate",
            "minimum_max_edge_count_at_current_radius": current[
                "minimum_limits_if_this_radius_is_selected"
            ]["max_edge_count"],
            "current_configured_max_edge_count": max_edge_count,
            "minimum_combined_memory_mb_at_current_radius": current[
                "minimum_limits_if_this_radius_is_selected"
            ]["max_memory_mb_for_construction_plus_replay"],
            "current_configured_max_memory_mb": max_memory_mb,
            "next_action": (
                "raise_max_edge_count_after_review; memory estimate remains within the configured "
                "combined budget"
                if current["checks"]["edge_count_within_limit"] is False
                and current["checks"]["construction_plus_replay_within_limit"]
                else "review report and select a documented resource strategy"
            ),
            "thresholds_modified": False,
        },
    }
    if output is not None:
        destination = Path(output).resolve()
        if destination.exists():
            raise FileExistsError(f"resource sensitivity output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


__all__ = ["build_resource_sensitivity_report"]
