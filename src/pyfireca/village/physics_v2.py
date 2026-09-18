"""Reduced-order thermal ignition and fuel-budget physics for village CA v2."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges
from pyfireca.village.exposure import target_power_W

STEFAN_BOLTZMANN_W_M2_K4 = 5.670374419e-8
WATER_BOILING_TEMPERATURE_K = 373.15
WATER_SPECIFIC_HEAT_J_KG_K = 4180.0
WATER_VAPORIZATION_ENTHALPY_J_KG = 2_256_400.0


@dataclass(frozen=True, slots=True)
class V2CellPhysicsArrays:
    """Resolved material and construction parameters for the v2 numerical kernel."""

    fuel_load_kg_m2: NDArray[np.float64]
    moisture_content_dry_basis: NDArray[np.float64]
    heat_of_combustion_J_kg: NDArray[np.float64]
    ignition_temperature_K: NDArray[np.float64]
    peak_hrrpua_W_m2: NDArray[np.float64]
    radiative_fraction: NDArray[np.float64]
    absorptivity: NDArray[np.float64]
    growth_time_s: NDArray[np.float64]
    steady_time_s: NDArray[np.float64]
    decay_time_s: NDArray[np.float64]
    effective_heated_dry_mass_kg_m2: NDArray[np.float64]
    specific_heat_J_kg_K: NDArray[np.float64]
    emissivity: NDArray[np.float64]
    ambient_heat_transfer_coefficient_W_m2_K: NDArray[np.float64]
    backing_conductance_W_m2_K: NDArray[np.float64]
    receiver_area_m2: NDArray[np.float64]
    active_surface_area_m2: NDArray[np.float64]
    pyrolysable_dry_mass_fraction: NDArray[np.float64]

    def validated(
        self,
        shape: tuple[int, ...],
        burnable: NDArray[np.bool_],
        *,
        cell_area_m2: float,
    ) -> V2CellPhysicsArrays:
        mask = np.asarray(burnable)
        if mask.shape != shape or mask.dtype != np.bool_:
            raise TypeError("burnable must be a boolean array matching the v2 parameter shape")
        if not np.isfinite(cell_area_m2) or cell_area_m2 <= 0.0:
            raise ValueError("cell_area_m2 must be finite and positive")
        values: dict[str, NDArray[np.float64]] = {}
        for name in self.__dataclass_fields__:
            value = np.asarray(getattr(self, name), dtype=np.float64)
            if value.shape != shape:
                raise ValueError(f"v2 parameter {name} must match shape {shape}")
            if np.any(~np.isfinite(value)) or np.any(value < 0.0):
                raise ValueError(f"v2 parameter {name} must be finite and non-negative")
            values[name] = value.copy()
        for name in (
            "radiative_fraction",
            "absorptivity",
            "emissivity",
            "pyrolysable_dry_mass_fraction",
        ):
            if np.any(values[name] > 1.0):
                raise ValueError(f"v2 parameter {name} must lie in [0, 1]")
        positive = (
            "fuel_load_kg_m2",
            "heat_of_combustion_J_kg",
            "ignition_temperature_K",
            "peak_hrrpua_W_m2",
            "growth_time_s",
            "decay_time_s",
            "effective_heated_dry_mass_kg_m2",
            "specific_heat_J_kg_K",
            "receiver_area_m2",
            "active_surface_area_m2",
            "pyrolysable_dry_mass_fraction",
        )
        for name in positive:
            if np.any(values[name][mask] <= 0.0):
                raise ValueError(f"v2 parameter {name} must be positive in burnable cells")
        fuel_energy = (
            values["fuel_load_kg_m2"]
            * cell_area_m2
            * values["pyrolysable_dry_mass_fraction"]
            * values["heat_of_combustion_J_kg"]
        )
        curve_energy = (
            values["peak_hrrpua_W_m2"]
            * values["active_surface_area_m2"]
            * (
                values["growth_time_s"] / 3.0
                + values["steady_time_s"]
                + values["decay_time_s"] / 2.0
            )
        )
        if not np.allclose(curve_energy[mask], fuel_energy[mask], rtol=1e-6, atol=1e-6):
            raise ValueError("v2 burn-curve energy does not match the dry fuel budget")
        return V2CellPhysicsArrays(**values)


def moisture_dry_to_wet_basis(moisture_dry_basis: float) -> float:
    if not np.isfinite(moisture_dry_basis) or moisture_dry_basis < 0.0:
        raise ValueError("dry-basis moisture must be finite and non-negative")
    return moisture_dry_basis / (1.0 + moisture_dry_basis)


def moisture_wet_to_dry_basis(moisture_wet_basis: float) -> float:
    if not np.isfinite(moisture_wet_basis) or not 0.0 <= moisture_wet_basis < 1.0:
        raise ValueError("wet-basis moisture must lie in [0, 1)")
    return moisture_wet_basis / (1.0 - moisture_wet_basis)


def energy_consistent_steady_time_s(
    *,
    fuel_mass_kg: float,
    heat_of_combustion_J_kg: float,
    peak_hrr_W: float,
    growth_time_s: float,
    decay_time_s: float,
) -> float:
    values = (
        fuel_mass_kg,
        heat_of_combustion_J_kg,
        peak_hrr_W,
        growth_time_s,
        decay_time_s,
    )
    if not all(np.isfinite(value) for value in values):
        raise ValueError("burn-curve inputs must be finite")
    if fuel_mass_kg <= 0.0 or heat_of_combustion_J_kg <= 0.0 or peak_hrr_W <= 0.0:
        raise ValueError("fuel mass, combustion heat, and peak HRR must be positive")
    if growth_time_s <= 0.0 or decay_time_s <= 0.0:
        raise ValueError("growth and decay times must be positive")
    steady = (
        fuel_mass_kg * heat_of_combustion_J_kg / peak_hrr_W
        - growth_time_s / 3.0
        - decay_time_s / 2.0
    )
    if steady < 0.0:
        raise ValueError("peak HRR and stage times exceed the available dry-fuel energy")
    return steady


def _burn_curve_antiderivative(
    age_s: NDArray[np.floating],
    growth_time_s: NDArray[np.floating],
    steady_time_s: NDArray[np.floating],
    decay_time_s: NDArray[np.floating],
) -> NDArray[np.float64]:
    age = np.maximum(np.asarray(age_s, dtype=np.float64), 0.0)
    growth = np.asarray(growth_time_s, dtype=np.float64)
    steady = np.asarray(steady_time_s, dtype=np.float64)
    decay = np.asarray(decay_time_s, dtype=np.float64)
    result = np.zeros_like(age)
    in_growth = age < growth
    result[in_growth] = age[in_growth] ** 3 / (3.0 * growth[in_growth] ** 2)
    after_growth = ~in_growth
    in_steady = after_growth & (age < growth + steady)
    result[in_steady] = growth[in_steady] / 3.0 + age[in_steady] - growth[in_steady]
    in_decay = after_growth & ~in_steady & (age < growth + steady + decay)
    decay_age = age[in_decay] - growth[in_decay] - steady[in_decay]
    result[in_decay] = (
        growth[in_decay] / 3.0
        + steady[in_decay]
        + decay_age
        - decay_age**2 / (2.0 * decay[in_decay])
    )
    finished = after_growth & ~in_steady & ~in_decay
    result[finished] = growth[finished] / 3.0 + steady[finished] + decay[finished] / 2.0
    return result


def burn_interval_energy_and_hrr(
    burning: NDArray[np.bool_],
    burn_age_s: NDArray[np.floating],
    fuel_remaining_kg: NDArray[np.floating],
    peak_hrr_W: NDArray[np.floating],
    growth_time_s: NDArray[np.floating],
    steady_time_s: NDArray[np.floating],
    decay_time_s: NDArray[np.floating],
    heat_of_combustion_J_kg: NDArray[np.floating],
    dt_s: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Integrate the v2 piecewise burn curve and enforce the fuel-energy budget."""

    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    arrays = [
        np.asarray(value, dtype=np.float64)
        for value in (
            burn_age_s,
            fuel_remaining_kg,
            peak_hrr_W,
            growth_time_s,
            steady_time_s,
            decay_time_s,
            heat_of_combustion_J_kg,
        )
    ]
    active = np.asarray(burning)
    if active.dtype != np.bool_ or any(value.shape != active.shape for value in arrays):
        raise ValueError("v2 burn arrays must have equal shape and a boolean burning mask")
    if any(np.any(~np.isfinite(value)) or np.any(value < 0.0) for value in arrays):
        raise ValueError("v2 burn arrays must be finite and non-negative")
    age, fuel, peak, growth, steady, decay, heat = arrays
    before = _burn_curve_antiderivative(age, growth, steady, decay)
    after = _burn_curve_antiderivative(age + dt_s, growth, steady, decay)
    curve_energy = peak * np.maximum(after - before, 0.0)
    available_energy = fuel * heat
    released = np.where(active, np.minimum(curve_energy, available_energy), 0.0)
    mass_loss = np.divide(released, heat, out=np.zeros_like(released), where=heat > 0.0)
    return released, mass_loss, released / dt_s


def radiant_flux_from_edges(
    hrr_W: NDArray[np.floating],
    radiative_fraction: NDArray[np.floating],
    receiver_area_m2: NDArray[np.floating],
    edges: ExposureEdges,
) -> NDArray[np.float64]:
    """Distribute the radiative HRR channel and divide by receptor area."""

    hrr = np.asarray(hrr_W, dtype=np.float64)
    fraction = np.asarray(radiative_fraction, dtype=np.float64)
    area = np.asarray(receiver_area_m2, dtype=np.float64)
    if not (hrr.shape == fraction.shape == area.shape):
        raise ValueError("v2 radiation arrays must have equal shape")
    if np.any(area <= 0.0) or np.any(fraction < 0.0) or np.any(fraction > 1.0):
        raise ValueError("v2 receiver area and radiation fractions are invalid")
    received = target_power_W((hrr * fraction).reshape(-1), edges).reshape(hrr.shape)
    return received / area


def net_surface_heat_flux_W_m2(
    temperature_K: NDArray[np.floating],
    radiant_flux_W_m2: NDArray[np.floating],
    convective_gain_W_m2: NDArray[np.floating],
    absorptivity: NDArray[np.floating],
    emissivity: NDArray[np.floating],
    ambient_heat_transfer_coefficient_W_m2_K: NDArray[np.floating],
    backing_conductance_W_m2_K: NDArray[np.floating],
    ambient_temperature_K: float,
) -> NDArray[np.float64]:
    """Evaluate the v2 reduced surface heat balance."""

    if not np.isfinite(ambient_temperature_K) or ambient_temperature_K <= 0.0:
        raise ValueError("ambient temperature must be finite and positive")
    arrays = [
        np.asarray(value, dtype=np.float64)
        for value in (
            temperature_K,
            radiant_flux_W_m2,
            convective_gain_W_m2,
            absorptivity,
            emissivity,
            ambient_heat_transfer_coefficient_W_m2_K,
            backing_conductance_W_m2_K,
        )
    ]
    if any(value.shape != arrays[0].shape for value in arrays):
        raise ValueError("v2 heat-balance arrays must have equal shape")
    temperature, radiant, convective, alpha, epsilon, heat_transfer, backing = arrays
    if any(np.any(~np.isfinite(value)) for value in arrays):
        raise ValueError("v2 heat-balance arrays must be finite")
    if np.any(temperature <= 0.0) or np.any(radiant < 0.0) or np.any(convective < 0.0):
        raise ValueError("v2 temperatures and heat gains are invalid")
    if np.any(alpha < 0.0) or np.any(alpha > 1.0) or np.any(epsilon < 0.0) or np.any(epsilon > 1.0):
        raise ValueError("v2 optical fractions must lie in [0, 1]")
    delta_temperature = temperature - ambient_temperature_K
    return (
        alpha * radiant
        + convective
        - heat_transfer * delta_temperature
        - epsilon * STEFAN_BOLTZMANN_W_M2_K4 * (temperature**4 - ambient_temperature_K**4)
        - backing * delta_temperature
    )


def advance_surface_heating_and_evaporation(
    temperature_K: NDArray[np.floating],
    water_mass_kg_m2: NDArray[np.floating],
    effective_heated_dry_mass_kg_m2: NDArray[np.floating],
    specific_heat_J_kg_K: NDArray[np.floating],
    net_flux_W_m2: NDArray[np.floating],
    dt_s: float,
    *,
    ambient_temperature_K: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Advance temperature and irreversible water loss with event-split energy use."""

    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    arrays = [
        np.asarray(value, dtype=np.float64)
        for value in (
            temperature_K,
            water_mass_kg_m2,
            effective_heated_dry_mass_kg_m2,
            specific_heat_J_kg_K,
            net_flux_W_m2,
        )
    ]
    if any(value.shape != arrays[0].shape for value in arrays):
        raise ValueError("v2 thermal arrays must have equal shape")
    temperature, water, dry_mass, dry_heat, flux = arrays
    if any(np.any(~np.isfinite(value)) for value in arrays):
        raise ValueError("v2 thermal arrays must be finite")
    if (
        np.any(temperature <= 0.0)
        or np.any(water < 0.0)
        or np.any(dry_mass <= 0.0)
        or np.any(dry_heat <= 0.0)
    ):
        raise ValueError("v2 thermal state or material properties are invalid")
    if not np.isfinite(ambient_temperature_K) or ambient_temperature_K <= 0.0:
        raise ValueError("ambient temperature must be finite and positive")
    next_temperature = temperature.copy()
    next_water = water.copy()
    energy = flux * dt_s
    for index in np.ndindex(temperature.shape):
        available = float(energy[index])
        dry_capacity = float(dry_mass[index] * dry_heat[index])
        if available <= 0.0:
            total_capacity = dry_capacity + float(water[index]) * WATER_SPECIFIC_HEAT_J_KG_K
            next_temperature[index] = max(
                ambient_temperature_K,
                float(temperature[index]) + available / total_capacity,
            )
            continue
        current_temperature = float(temperature[index])
        current_water = float(water[index])
        if current_water > 0.0 and current_temperature < WATER_BOILING_TEMPERATURE_K:
            total_capacity = dry_capacity + current_water * WATER_SPECIFIC_HEAT_J_KG_K
            energy_to_boiling = total_capacity * (WATER_BOILING_TEMPERATURE_K - current_temperature)
            used = min(available, energy_to_boiling)
            current_temperature += used / total_capacity
            available -= used
        if (
            current_water > 0.0
            and current_temperature >= WATER_BOILING_TEMPERATURE_K - 1e-12
            and available > 0.0
        ):
            evaporated = min(current_water, available / WATER_VAPORIZATION_ENTHALPY_J_KG)
            current_water -= evaporated
            available -= evaporated * WATER_VAPORIZATION_ENTHALPY_J_KG
            current_temperature = WATER_BOILING_TEMPERATURE_K
        if current_water <= 1e-15 and available > 0.0:
            current_water = 0.0
            current_temperature += available / dry_capacity
        next_temperature[index] = current_temperature
        next_water[index] = current_water
    return next_temperature, next_water


def monotone_thermal_timestep_limit_s(
    temperature_K: NDArray[np.floating],
    effective_heated_dry_mass_kg_m2: NDArray[np.floating],
    specific_heat_J_kg_K: NDArray[np.floating],
    emissivity: NDArray[np.floating],
    ambient_heat_transfer_coefficient_W_m2_K: NDArray[np.floating],
    backing_conductance_W_m2_K: NDArray[np.floating],
) -> float:
    """Return the strict local explicit cooling limit from the v2 specification."""

    temperature = np.asarray(temperature_K, dtype=np.float64)
    capacity = np.asarray(effective_heated_dry_mass_kg_m2, dtype=np.float64) * np.asarray(
        specific_heat_J_kg_K, dtype=np.float64
    )
    epsilon = np.asarray(emissivity, dtype=np.float64)
    heat_transfer = np.asarray(ambient_heat_transfer_coefficient_W_m2_K, dtype=np.float64)
    backing = np.asarray(backing_conductance_W_m2_K, dtype=np.float64)
    if not (
        temperature.shape == capacity.shape == epsilon.shape == heat_transfer.shape == backing.shape
    ):
        raise ValueError("v2 timestep arrays must have equal shape")
    radiative_slope = 4.0 * epsilon * STEFAN_BOLTZMANN_W_M2_K4 * temperature**3
    denominator = heat_transfer + backing + radiative_slope
    valid = denominator > 0.0
    if not np.any(valid):
        return np.inf
    return float(np.min(capacity[valid] / denominator[valid]))


__all__ = [
    "STEFAN_BOLTZMANN_W_M2_K4",
    "WATER_BOILING_TEMPERATURE_K",
    "WATER_SPECIFIC_HEAT_J_KG_K",
    "WATER_VAPORIZATION_ENTHALPY_J_KG",
    "V2CellPhysicsArrays",
    "advance_surface_heating_and_evaporation",
    "burn_interval_energy_and_hrr",
    "energy_consistent_steady_time_s",
    "moisture_dry_to_wet_basis",
    "moisture_wet_to_dry_basis",
    "monotone_thermal_timestep_limit_s",
    "net_surface_heat_flux_W_m2",
    "radiant_flux_from_edges",
]
