"""V03 per-cell fuel, water and energy arrays for the vegetation model.

The module resolves the V02 profile language into cell quantities.  It does
not advance temperature or construct a transport graph; those remain later
stages.  All areas are measured on the covered part of a cell exactly once.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.physics_v2 import (
    WATER_BOILING_TEMPERATURE_K,
    WATER_SPECIFIC_HEAT_J_KG_K,
    WATER_VAPORIZATION_ENTHALPY_J_KG,
    burn_interval_energy_and_hrr,
)
from pyfireca.village.vegetation_config import VegetationCase


def _array(name: str, value: Any, shape: tuple[int, int]) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape:
        raise ValueError(f"v3 array {name} shape {array.shape} does not match {shape}")
    if np.any(~np.isfinite(array)) or np.any(array < 0.0):
        raise ValueError(f"v3 array {name} must be finite and non-negative")
    return array.copy()


def burn_curve_energy_J(
    peak_hrr_W: float, growth_time_s: float, steady_time_s: float, decay_time_s: float
) -> float:
    """Integral of the quadratic-growth/steady/linear-decay HRR curve."""

    values = (peak_hrr_W, growth_time_s, steady_time_s, decay_time_s)
    if any(not isfinite(value) for value in values):
        raise ValueError("burn curve values must be finite")
    if peak_hrr_W < 0.0 or growth_time_s < 0.0 or steady_time_s < 0.0 or decay_time_s < 0.0:
        raise ValueError("burn curve values must be non-negative")
    return peak_hrr_W * (growth_time_s / 3.0 + steady_time_s + decay_time_s / 2.0)


def moisture_energy_demand_J(water_mass_kg: float, ambient_temperature_K: float) -> float:
    """Energy needed to heat water to boiling and evaporate it."""

    if not isfinite(water_mass_kg) or water_mass_kg < 0.0:
        raise ValueError("water_mass_kg must be finite and non-negative")
    if not isfinite(ambient_temperature_K) or ambient_temperature_K <= 0.0:
        raise ValueError("ambient_temperature_K must be finite and positive")
    sensible = (
        water_mass_kg
        * WATER_SPECIFIC_HEAT_J_KG_K
        * max(0.0, WATER_BOILING_TEMPERATURE_K - ambient_temperature_K)
    )
    return sensible + water_mass_kg * WATER_VAPORIZATION_ENTHALPY_J_KG


@dataclass(frozen=True, slots=True)
class V3CellPhysicsArrays:
    """Resolved quantities with cell-area and coverage already applied."""

    coverage_fraction: NDArray[np.float64]
    covered_area_m2: NDArray[np.float64]
    initial_dry_fuel_mass_kg: NDArray[np.float64]
    live_dry_fuel_mass_kg: NDArray[np.float64]
    dead_dry_fuel_mass_kg: NDArray[np.float64]
    pyrolysable_dry_fuel_mass_kg: NDArray[np.float64]
    live_water_mass_kg: NDArray[np.float64]
    dead_water_mass_kg: NDArray[np.float64]
    water_mass_kg: NDArray[np.float64]
    moisture_content_dry_basis: NDArray[np.float64]
    heat_of_combustion_J_kg_dry: NDArray[np.float64]
    ignition_temperature_K: NDArray[np.float64]
    peak_hrrpua_W_m2_active: NDArray[np.float64]
    peak_hrr_W: NDArray[np.float64]
    radiative_fraction: NDArray[np.float64]
    absorptivity: NDArray[np.float64]
    growth_time_s: NDArray[np.float64]
    steady_time_s: NDArray[np.float64]
    decay_time_s: NDArray[np.float64]
    effective_heated_dry_mass_kg: NDArray[np.float64]
    specific_heat_J_kg_K: NDArray[np.float64]
    emissivity: NDArray[np.float64]
    ambient_heat_transfer_coefficient_W_m2_K: NDArray[np.float64]
    backing_conductance_W_m2_K: NDArray[np.float64]
    receiver_area_ratio: NDArray[np.float64]
    receiver_area_m2: NDArray[np.float64]
    active_surface_area_ratio: NDArray[np.float64]
    active_surface_area_m2: NDArray[np.float64]
    pyrolysable_dry_mass_fraction: NDArray[np.float64]
    source_height_m: NDArray[np.float64]
    receiver_height_m: NDArray[np.float64]
    available_energy_J: NDArray[np.float64]
    configured_curve_energy_J: NDArray[np.float64]

    def validated(self, shape: tuple[int, int], cell_area_m2: float) -> V3CellPhysicsArrays:
        if not isfinite(cell_area_m2) or cell_area_m2 <= 0.0:
            raise ValueError("cell_area_m2 must be finite and positive")
        values = {
            name: _array(name, getattr(self, name), shape) for name in self.__dataclass_fields__
        }
        for name in (
            "coverage_fraction",
            "radiative_fraction",
            "absorptivity",
            "emissivity",
            "pyrolysable_dry_mass_fraction",
        ):
            if np.any(values[name] > 1.0):
                raise ValueError(f"v3 parameter {name} must lie in [0, 1]")
        if np.any(values["covered_area_m2"] > cell_area_m2 + 1e-9):
            raise ValueError("v3 covered area cannot exceed a cell area")
        if not np.allclose(
            values["covered_area_m2"], values["coverage_fraction"] * cell_area_m2, atol=1e-9
        ):
            raise ValueError("v3 covered area and coverage fraction disagree")
        checks = {
            "initial_dry_fuel_mass_kg": values["live_dry_fuel_mass_kg"]
            + values["dead_dry_fuel_mass_kg"],
            "water_mass_kg": values["live_water_mass_kg"] + values["dead_water_mass_kg"],
            "pyrolysable_dry_fuel_mass_kg": values["initial_dry_fuel_mass_kg"]
            * values["pyrolysable_dry_mass_fraction"],
            "peak_hrr_W": values["peak_hrrpua_W_m2_active"] * values["active_surface_area_m2"],
            "available_energy_J": values["pyrolysable_dry_fuel_mass_kg"]
            * values["heat_of_combustion_J_kg_dry"],
        }
        for name, expected in checks.items():
            if not np.allclose(values[name], expected, rtol=1e-10, atol=1e-6):
                raise ValueError(f"v3 conservation relationship failed for {name}")
        if not np.allclose(
            values["configured_curve_energy_J"],
            values["peak_hrr_W"]
            * (
                values["growth_time_s"] / 3.0
                + values["steady_time_s"]
                + values["decay_time_s"] / 2.0
            ),
            rtol=1e-10,
            atol=1e-6,
        ):
            raise ValueError("v3 configured curve energy does not match HRR fields")
        burnable = values["pyrolysable_dry_fuel_mass_kg"] > 0.0
        if np.any(
            values["configured_curve_energy_J"][burnable]
            > values["available_energy_J"][burnable] + 1e-6
        ):
            raise ValueError("v3 configured curve exceeds available cell fuel energy")
        return V3CellPhysicsArrays(**values)


@dataclass(frozen=True, slots=True)
class ResolvedV3Physics:
    arrays: V3CellPhysicsArrays
    profile_id: NDArray[np.str_]
    landcover_index: NDArray[np.int16]
    evidence_index: NDArray[np.int16]


def resolve_vegetation_physics(case: VegetationCase) -> ResolvedV3Physics:
    """Resolve profile values into cell quantities without modifying the case."""

    if not isinstance(case, VegetationCase):
        raise TypeError("case must be a VegetationCase")
    shape = case.grid.shape

    def zero() -> NDArray[np.float64]:
        return np.zeros(shape, dtype=np.float64)

    fields = {name: zero() for name in V3CellPhysicsArrays.__dataclass_fields__}
    fields["coverage_fraction"] = case.coverage_fraction.copy()
    fields["covered_area_m2"] = case.coverage_fraction * case.grid.cell_area_m2
    profile_ids = np.full(shape, "", dtype="U64")
    for profile in case.profiles.profiles:
        mask = case.profile_id_by_cell == profile.id
        profile_ids[mask] = profile.id
        if not profile.burnable:
            continue
        parameter = profile.value
        covered = fields["covered_area_m2"][mask]
        dry = covered * parameter("fuel_load_kg_m2")
        live = dry * parameter("live_dry_mass_fraction")
        dead = dry * parameter("dead_dry_mass_fraction")
        pyro = dry * parameter("pyrolysable_dry_mass_fraction")
        live_water = live * parameter("live_moisture_content_dry_basis")
        dead_water = dead * parameter("dead_moisture_content_dry_basis")
        active = covered * parameter("active_surface_area_ratio")
        receiver = covered * parameter("receiver_area_ratio")
        peak = active * parameter("peak_hrrpua_W_m2")
        available = pyro * parameter("heat_of_combustion_J_kg_dry")
        curve = peak * (
            parameter("growth_time_s") / 3.0
            + parameter("steady_time_s")
            + parameter("decay_time_s") / 2.0
        )
        assignments = {
            "initial_dry_fuel_mass_kg": dry,
            "live_dry_fuel_mass_kg": live,
            "dead_dry_fuel_mass_kg": dead,
            "pyrolysable_dry_fuel_mass_kg": pyro,
            "live_water_mass_kg": live_water,
            "dead_water_mass_kg": dead_water,
            "water_mass_kg": live_water + dead_water,
            "moisture_content_dry_basis": np.divide(
                live_water + dead_water,
                dry,
                out=np.zeros_like(dry),
                where=dry > 0.0,
            ),
            "heat_of_combustion_J_kg_dry": np.full_like(
                covered, parameter("heat_of_combustion_J_kg_dry")
            ),
            "ignition_temperature_K": np.full_like(covered, parameter("ignition_temperature_K")),
            "peak_hrrpua_W_m2_active": np.full_like(covered, parameter("peak_hrrpua_W_m2")),
            "peak_hrr_W": peak,
            "radiative_fraction": np.full_like(covered, parameter("radiative_fraction")),
            "absorptivity": np.full_like(covered, parameter("absorptivity")),
            "growth_time_s": np.full_like(covered, parameter("growth_time_s")),
            "steady_time_s": np.full_like(covered, parameter("steady_time_s")),
            "decay_time_s": np.full_like(covered, parameter("decay_time_s")),
            "effective_heated_dry_mass_kg": covered * parameter("effective_heated_dry_mass_kg_m2"),
            "specific_heat_J_kg_K": np.full_like(covered, parameter("specific_heat_J_kg_K")),
            "emissivity": np.full_like(covered, parameter("emissivity")),
            "ambient_heat_transfer_coefficient_W_m2_K": np.full_like(
                covered, parameter("ambient_heat_transfer_coefficient_W_m2_K")
            ),
            "backing_conductance_W_m2_K": np.full_like(
                covered, parameter("backing_conductance_W_m2_K")
            ),
            "receiver_area_ratio": np.full_like(covered, parameter("receiver_area_ratio")),
            "receiver_area_m2": receiver,
            "active_surface_area_ratio": np.full_like(
                covered, parameter("active_surface_area_ratio")
            ),
            "active_surface_area_m2": active,
            "pyrolysable_dry_mass_fraction": np.full_like(
                covered, parameter("pyrolysable_dry_mass_fraction")
            ),
            "source_height_m": np.full_like(covered, parameter("source_height_m")),
            "receiver_height_m": np.full_like(covered, parameter("receiver_height_m")),
            "available_energy_J": available,
            "configured_curve_energy_J": curve,
        }
        for name, value in assignments.items():
            fields[name][mask] = value
    arrays = V3CellPhysicsArrays(**fields).validated(shape, case.grid.cell_area_m2)
    return ResolvedV3Physics(
        arrays=arrays,
        profile_id=profile_ids,
        landcover_index=case.landcover_index.copy(),
        evidence_index=case.evidence_index.copy().astype(np.int16),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_v3_physics_package(
    case: VegetationCase, physics: ResolvedV3Physics, output_dir: str | Path
) -> Path:
    """Write the V03 static arrays and an auditable conservation report."""

    if not isinstance(physics, ResolvedV3Physics):
        raise TypeError("physics must be ResolvedV3Physics")
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"physics output directory already exists: {output}")
    output.mkdir(parents=True)
    try:
        arrays_dir = output / "arrays"
        arrays_dir.mkdir()
        for name in V3CellPhysicsArrays.__dataclass_fields__:
            np.save(
                arrays_dir / f"{name}.npy",
                getattr(physics.arrays, name),
                allow_pickle=False,
            )
        np.save(arrays_dir / "profile_id.npy", physics.profile_id, allow_pickle=False)
        np.save(arrays_dir / "landcover_index.npy", physics.landcover_index, allow_pickle=False)
        np.save(arrays_dir / "evidence_index.npy", physics.evidence_index, allow_pickle=False)
        shutil.copy2(case.path, output / "case.snapshot.yml")
        shutil.copy2(case.profiles.path, output / "profiles.snapshot.yml")
        report = {
            "schema_version": "village-vegetation-v03-physics-report-1",
            "case_id": case.case_id,
            "simulation_executed": False,
            "grid": {
                "height": case.grid.height,
                "width": case.grid.width,
                "cell_size_m": case.grid.cell_size_m,
                "cell_area_m2": case.grid.cell_area_m2,
                "crs": case.grid.crs,
            },
            "mass_totals_kg": {
                name: float(np.sum(getattr(physics.arrays, name)))
                for name in (
                    "initial_dry_fuel_mass_kg",
                    "live_dry_fuel_mass_kg",
                    "dead_dry_fuel_mass_kg",
                    "pyrolysable_dry_fuel_mass_kg",
                    "live_water_mass_kg",
                    "dead_water_mass_kg",
                    "water_mass_kg",
                )
            },
            "energy_totals_J": {
                "available": float(np.sum(physics.arrays.available_energy_J)),
                "configured_curve": float(np.sum(physics.arrays.configured_curve_energy_J)),
                "closure_error": float(
                    np.sum(physics.arrays.configured_curve_energy_J)
                    - np.sum(physics.arrays.available_energy_J)
                ),
            },
            "burnable_cell_count": int(np.count_nonzero(case.burnable_mask)),
            "unknown_domain_cell_count": int(np.count_nonzero(case.domain_mask & ~case.known_mask)),
        }
        (output / "physics_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema_version": "prepared-village-vegetation-physics-1",
            "stage": "V03",
            "case_sha256": _sha256(case.path),
            "profiles_sha256": _sha256(case.profiles.path),
            "prepared_manifest_sha256": _sha256(case.prepared_dir / "manifest.json"),
            "arrays": [
                *V3CellPhysicsArrays.__dataclass_fields__,
                "profile_id",
                "landcover_index",
                "evidence_index",
            ],
            "simulation_executed": False,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


__all__ = [
    "ResolvedV3Physics",
    "V3CellPhysicsArrays",
    "burn_curve_energy_J",
    "burn_interval_energy_and_hrr",
    "moisture_energy_demand_J",
    "resolve_vegetation_physics",
    "write_v3_physics_package",
]
