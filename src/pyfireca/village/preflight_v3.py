"""Whole-domain V3 input, evidence and resource preflight checks."""

from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np

from pyfireca.village.workflow_v3 import load_v3_case_bundle

VIEWER_DYNAMIC_BYTES_PER_CELL_FRAME = 18
SOLVER_REPLAY_BYTES_PER_CELL_FRAME = 33
EDGE_BYTES = 16
CONSTRUCTION_BYTES_PER_EDGE = 64


def _frame_count(end_time_s: float, sample_interval_s: float | None) -> int:
    if sample_interval_s is None or sample_interval_s <= 0.0:
        return 0
    return ceil(end_time_s / sample_interval_s - 1e-12) + 1


def _material_counts(case: Any) -> dict[str, dict[str, float | int]]:
    labels = case.material_labels
    values = case.request.material_index
    area = case.request.parameters.covered_area_m2
    initial = case.request.parameters.initial_dry_fuel_mass_kg
    result: dict[str, dict[str, float | int]] = {}
    for index in np.unique(values):
        if int(index) < 0:
            continue
        mask = values == index
        key = labels.get(int(index), f"material_{int(index)}")
        result[key] = {
            "material_index": int(index),
            "cell_count": int(np.count_nonzero(mask)),
            "covered_area_m2": float(np.sum(area[mask])),
            "initial_dry_fuel_kg": float(np.sum(initial[mask])),
        }
    return result


def preflight_v3_case(
    case_dir: str | Path,
    *,
    output: str | Path | None = None,
    max_edge_count: int = 5_000_000,
    max_memory_mb: int = 2_048,
) -> dict[str, Any]:
    """Audit a serialized V3 case without constructing or running a simulation."""

    if max_edge_count <= 0 or max_memory_mb <= 0:
        raise ValueError("resource limits must be positive")
    case = load_v3_case_bundle(case_dir)
    request = case.request
    cells = request.grid.height * request.grid.width
    domain = np.asarray(request.domain_mask, dtype=bool)
    known = np.asarray(request.known_mask, dtype=bool)
    burnable = np.asarray(request.burnable_mask, dtype=bool)
    unknown = domain & ~known
    outside = ~domain
    edge_count = len(request.exposure_edges.source)
    frame_count = _frame_count(request.scenario.end_time_s, request.sample_interval_s)
    edge_bytes = edge_count * EDGE_BYTES
    construction_bytes = edge_count * CONSTRUCTION_BYTES_PER_EDGE
    solver_replay_bytes = cells * frame_count * SOLVER_REPLAY_BYTES_PER_CELL_FRAME
    viewer_dynamic_bytes = cells * frame_count * VIEWER_DYNAMIC_BYTES_PER_CELL_FRAME
    # Four int32 neighbors plus four float64 geometry fractions per cell;
    # transfer temporaries and full target/source accumulation arrays also
    # matter for a dense front. This is a conservative estimate, not RSS.
    contact_storage_bytes = cells * 48 if request.flame_contact else 0
    contact_working_bytes = cells * 512 if request.flame_contact else 0
    combined_peak_bytes = (
        construction_bytes + solver_replay_bytes + contact_working_bytes + cells * 8
    )

    reasons: list[str] = []
    metadata = case.metadata
    if int(np.count_nonzero(unknown)) > 0:
        reasons.append(f"unknown_cells={int(np.count_nonzero(unknown))}")
    if metadata.get("assumptions", False):
        reasons.append("scenario_contains_synthetic_assumptions")
    if metadata.get("simulation_scope") != "full_village":
        reasons.append(f"simulation_scope={metadata.get('simulation_scope', 'unspecified')}")
    if case.request.unknown_policy != "error":
        reasons.append(f"unknown_policy={case.request.unknown_policy}")
    if request.flame_contact is not None:
        reasons.append("effective_flame_contact_requires_independent_calibration")
    resource_reasons: list[str] = []
    if edge_count > max_edge_count:
        resource_reasons.append(f"edge_count={edge_count} exceeds {max_edge_count}")
    if combined_peak_bytes > max_memory_mb * 1_000_000:
        resource_reasons.append(
            f"construction_plus_solver_replay_mb={combined_peak_bytes / 1_000_000:.1f} "
            f"exceeds {max_memory_mb}"
        )
    report = {
        "schema_version": "village-v3-preflight-1",
        "case": str(Path(case_dir).resolve()),
        "status": "blocked" if reasons or resource_reasons else "ready",
        "research_full_village_ready": not reasons and not resource_reasons,
        "research_gate_reasons": reasons,
        "resource_gate_reasons": resource_reasons,
        "grid": {
            "height": request.grid.height,
            "width": request.grid.width,
            "cell_count": cells,
            "cell_size_m": request.grid.cell_size_m,
            "crs": request.grid.crs,
        },
        "classification": {
            "domain_cell_count": int(np.count_nonzero(domain)),
            "known_cell_count": int(np.count_nonzero(domain & known)),
            "unknown_cell_count": int(np.count_nonzero(unknown)),
            "outside_nodata_cell_count": int(np.count_nonzero(outside)),
            "burnable_cell_count": int(np.count_nonzero(burnable)),
            "nonbuilding_burnable_cell_count": int(
                np.count_nonzero(burnable & (request.building_index < 0))
            ),
            "source_cell_count": int(np.count_nonzero(request.source_mask)),
            "target_cell_count": int(np.count_nonzero(request.target_mask)),
            "by_material": _material_counts(case),
        },
        "flame_contact": (request.flame_contact.to_mapping() if request.flame_contact else None),
        "transport": {
            "contact_operator_storage_bytes_estimate": contact_storage_bytes,
            "contact_dense_working_bytes_estimate": contact_working_bytes,
            "edge_count": edge_count,
            "edge_bytes": edge_bytes,
            "construction_peak_bytes_estimate": construction_bytes,
            "construction_plus_solver_replay_bytes_estimate": combined_peak_bytes,
            "max_edge_count": max_edge_count,
            "max_memory_mb": max_memory_mb,
        },
        "replay": {
            "frame_count_estimate": frame_count,
            "end_time_s": request.scenario.end_time_s,
            "sample_interval_s": request.sample_interval_s,
            "solver_replay_bytes_estimate": solver_replay_bytes,
            "viewer_dynamic_bytes_estimate": viewer_dynamic_bytes,
            "viewer_dynamic_bytes_per_cell_frame": VIEWER_DYNAMIC_BYTES_PER_CELL_FRAME,
        },
        "assumptions": metadata,
    }
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


__all__ = ["preflight_v3_case"]
