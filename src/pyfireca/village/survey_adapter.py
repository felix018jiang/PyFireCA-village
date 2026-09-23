"""M03 adapter from resolved survey records to V03/V04 spatial inputs.

The adapter is intentionally a small-domain bridge.  It accepts an explicit
cell-to-geometry reference raster and coverage fraction, applies each active
survey record once, and returns validated V03 arrays plus the masks and
transmissivity array expected by V04.  It does not infer geometry from a
material label and does not run a solver.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.material_resolution import ResolvedSurveyCase
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.survey import MATERIAL_CLASS_IDS


def _array(name: str, value: Any, shape: tuple[int, int]) -> NDArray:
    array = np.asarray(value)
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    return array


def _float_array(name: str, value: Any, shape: tuple[int, int]) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    if np.any(~np.isfinite(array)) or np.any(array < 0.0):
        raise ValueError(f"{name} must be finite and non-negative")
    return array.copy()


def _number(value: float | None, name: str) -> float:
    if value is None or not np.isfinite(value):
        raise ValueError(f"resolved record is missing finite {name}")
    return float(value)


@dataclass(frozen=True, slots=True)
class M03SpatialAdapterResult:
    arrays: V3CellPhysicsArrays
    profile_id: NDArray[np.str_]
    landcover_index: NDArray[np.int16]
    evidence_index: NDArray[np.int16]
    transport_source_mask: NDArray[np.bool_]
    transport_target_mask: NDArray[np.bool_]
    blocker_transmissivity: NDArray[np.float64]
    adapter_sha256: str


def _hash_adapter_inputs(
    resolved: ResolvedSurveyCase,
    coverage: NDArray[np.float64],
    geometry_refs: NDArray,
    transmissivity: NDArray[np.float64],
) -> str:
    digest = hashlib.sha256()
    metadata = {
        "resolved_configuration_sha256": resolved.configuration_sha256,
        "coverage_dtype": str(coverage.dtype),
        "geometry_refs_dtype": str(geometry_refs.dtype),
        "transmissivity_dtype": str(transmissivity.dtype),
        "shape": list(coverage.shape),
    }
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(coverage.tobytes(order="C"))
    digest.update(
        json.dumps(
            geometry_refs.astype(str).tolist(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )
    digest.update(transmissivity.tobytes(order="C"))
    return digest.hexdigest()


def adapt_resolved_survey_to_v03(
    grid: VillageGridSpec,
    resolved: ResolvedSurveyCase,
    *,
    geometry_ref_by_cell: NDArray[Any],
    coverage_fraction: NDArray[np.floating],
    blocker_transmissivity: NDArray[np.floating],
) -> M03SpatialAdapterResult:
    """Build V03 arrays and explicit V04 masks from a resolved small domain.

    ``geometry_ref_by_cell`` and ``coverage_fraction`` are spatial inputs from
    a prior geometry step.  A positive coverage cell must match exactly one
    active resolved record.  The record's total area must close against the
    covered cell areas before mass is distributed, preventing double counting.
    """

    if not isinstance(grid, VillageGridSpec):
        raise TypeError("grid must be VillageGridSpec")
    if not isinstance(resolved, ResolvedSurveyCase):
        raise TypeError("resolved must be ResolvedSurveyCase")
    if not resolved.can_run:
        messages = "; ".join(
            issue.message for issue in resolved.issues if issue.severity == "error"
        )
        raise ValueError(f"resolved survey is not runnable: {messages}")
    shape = grid.shape
    refs = _array("geometry_ref_by_cell", geometry_ref_by_cell, shape)
    if refs.dtype.kind not in {"O", "U", "S"}:
        raise TypeError("geometry_ref_by_cell must contain strings or None")
    coverage = _float_array("coverage_fraction", coverage_fraction, shape)
    if np.any(coverage > 1.0):
        raise ValueError("coverage_fraction must lie in [0, 1]")
    transmission = _float_array("blocker_transmissivity", blocker_transmissivity, shape)
    if np.any(transmission > 1.0):
        raise ValueError("blocker_transmissivity must lie in [0, 1]")

    fields = {
        name: np.zeros(shape, dtype=np.float64) for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    fields["coverage_fraction"] = coverage.copy()
    fields["covered_area_m2"] = coverage * grid.cell_area_m2
    profile_id = np.full(shape, "", dtype="U64")
    landcover_index = np.full(shape, -1, dtype=np.int16)
    evidence_index = np.full(shape, -1, dtype=np.int16)
    assigned = np.zeros(shape, dtype=np.bool_)
    class_index = {material_class: index for index, material_class in enumerate(MATERIAL_CLASS_IDS)}

    for record_index, record in enumerate(resolved.records):
        cell_mask = (refs.astype(str) == record.geometry_ref) & (coverage > 0.0)
        if not record.active:
            continue
        if not np.any(cell_mask):
            raise ValueError(
                f"active observation {record.observation_id!r} has no covered cells for "
                f"geometry {record.geometry_ref!r}"
            )
        if np.any(assigned & cell_mask):
            raise ValueError(
                f"active survey geometries overlap cells for observation {record.observation_id!r}"
            )
        cell_area = fields["covered_area_m2"][cell_mask]
        actual_area = float(np.sum(cell_area))
        expected_area = _number(record.covered_area_m2, "covered_area_m2")
        if not np.isclose(actual_area, expected_area, rtol=1e-9, atol=1e-9):
            raise ValueError(
                f"coverage area closure failed for {record.observation_id!r}: "
                f"cells={actual_area:.12g}, resolved={expected_area:.12g}"
            )
        assigned[cell_mask] = True
        profile_id[cell_mask] = record.card_id or "unknown"
        landcover_index[cell_mask] = np.int16(class_index.get(record.material_class, -1))
        evidence_index[cell_mask] = np.int16(record_index)
        area = cell_area
        fuel_load = _number(record.fuel_load_kg_m2, "fuel_load_kg_m2")
        live_fraction = _number(record.live_dry_mass_fraction, "live_dry_mass_fraction")
        dead_fraction = _number(record.dead_dry_mass_fraction, "dead_dry_mass_fraction")
        pyro_fraction = _number(
            record.pyrolysable_dry_mass_fraction, "pyrolysable_dry_mass_fraction"
        )
        dry = area * fuel_load
        live = dry * live_fraction
        dead = dry * dead_fraction
        water_ratio = _number(record.moisture_content_dry_basis, "moisture_content_dry_basis")
        live_water = live * water_ratio
        dead_water = dead * water_ratio
        active_ratio = _number(record.active_surface_area_ratio, "active_surface_area_ratio")
        receiver_ratio = _number(record.receiver_area_ratio, "receiver_area_ratio")
        active_area = area * active_ratio
        receiver_area = area * receiver_ratio
        peak_hrrpua = _number(record.peak_hrrpua_W_m2_active, "peak_hrrpua_W_m2_active")
        peak = active_area * peak_hrrpua
        pyro = dry * pyro_fraction
        heat = _number(record.heat_of_combustion_J_kg_dry, "heat_of_combustion_J_kg_dry")
        available = pyro * heat
        growth = _number(record.growth_time_s, "growth_time_s")
        steady = _number(record.steady_time_s, "steady_time_s")
        decay = _number(record.decay_time_s, "decay_time_s")
        curve = peak * (growth / 3.0 + steady + decay / 2.0)
        assignments = {
            "initial_dry_fuel_mass_kg": dry,
            "live_dry_fuel_mass_kg": live,
            "dead_dry_fuel_mass_kg": dead,
            "pyrolysable_dry_fuel_mass_kg": pyro,
            "live_water_mass_kg": live_water,
            "dead_water_mass_kg": dead_water,
            "water_mass_kg": live_water + dead_water,
            "moisture_content_dry_basis": np.full_like(area, water_ratio),
            "heat_of_combustion_J_kg_dry": np.full_like(area, heat),
            "ignition_temperature_K": np.full_like(
                area, _number(record.ignition_temperature_K, "ignition_temperature_K")
            ),
            "peak_hrrpua_W_m2_active": np.full_like(area, peak_hrrpua),
            "peak_hrr_W": peak,
            "radiative_fraction": np.full_like(
                area, _number(record.radiative_fraction, "radiative_fraction")
            ),
            "absorptivity": np.full_like(area, _number(record.absorptivity, "absorptivity")),
            "growth_time_s": np.full_like(area, growth),
            "steady_time_s": np.full_like(area, steady),
            "decay_time_s": np.full_like(area, decay),
            "effective_heated_dry_mass_kg": area
            * _number(record.effective_heated_dry_mass_kg, "effective_heated_dry_mass_kg")
            / max(expected_area, 1e-12),
            "specific_heat_J_kg_K": np.full_like(
                area, _number(record.specific_heat_J_kg_K, "specific_heat_J_kg_K")
            ),
            "emissivity": np.full_like(area, _number(record.emissivity, "emissivity")),
            "ambient_heat_transfer_coefficient_W_m2_K": np.full_like(
                area,
                _number(
                    record.ambient_heat_transfer_coefficient_W_m2_K,
                    "ambient_heat_transfer_coefficient_W_m2_K",
                ),
            ),
            "backing_conductance_W_m2_K": np.full_like(
                area,
                _number(record.backing_conductance_W_m2_K, "backing_conductance_W_m2_K"),
            ),
            "receiver_area_ratio": np.full_like(area, receiver_ratio),
            "receiver_area_m2": receiver_area,
            "active_surface_area_ratio": np.full_like(area, active_ratio),
            "active_surface_area_m2": active_area,
            "pyrolysable_dry_mass_fraction": np.full_like(area, pyro_fraction),
            "source_height_m": np.full_like(
                area, _number(record.source_height_m, "source_height_m")
            ),
            "receiver_height_m": np.full_like(
                area, _number(record.receiver_height_m, "receiver_height_m")
            ),
            "available_energy_J": available,
            "configured_curve_energy_J": curve,
        }
        for name, values in assignments.items():
            fields[name][cell_mask] = values

    if np.any((coverage > 0.0) & ~assigned):
        missing_refs = sorted(set(refs[(coverage > 0.0) & ~assigned].astype(str)))
        raise ValueError(f"covered cells have no active resolved observation: {missing_refs}")
    arrays = V3CellPhysicsArrays(**fields).validated(shape, grid.cell_area_m2)
    source_mask = arrays.pyrolysable_dry_fuel_mass_kg > 0.0
    source_mask &= arrays.peak_hrr_W > 0.0
    target_mask = (arrays.covered_area_m2 > 0.0) & (arrays.receiver_area_m2 > 0.0)
    adapter_hash = _hash_adapter_inputs(resolved, coverage, refs, transmission)
    return M03SpatialAdapterResult(
        arrays=arrays,
        profile_id=profile_id,
        landcover_index=landcover_index,
        evidence_index=evidence_index,
        transport_source_mask=source_mask,
        transport_target_mask=target_mask,
        blocker_transmissivity=transmission,
        adapter_sha256=adapter_hash,
    )


__all__ = ["M03SpatialAdapterResult", "adapt_resolved_survey_to_v03"]
