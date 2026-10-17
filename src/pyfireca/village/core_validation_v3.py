"""Independent numerical acceptance checks, without asserting empirical accuracy."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np


def check_final(request, result):
    final = result.final_state
    initial = result.initial_fuel_kg
    remaining = final.fuel_remaining_kg
    if (
        not np.all(np.isfinite(remaining))
        or np.any(remaining < 0)
        or np.any(remaining > initial + 1e-9)
    ):
        raise AssertionError("fuel outside initial budget")
    if np.any(np.isfinite(final.ignition_time_s[~request.burnable_mask])) or np.any(
        remaining[~request.burnable_mask]
    ):
        raise AssertionError("non-fuel cell ignited or acquired fuel")
    if np.any(remaining[final.state == 4] != 0):
        raise AssertionError("burned-out cell retains combustible fuel")
    valid = final.temperature_valid_mask
    if np.any(~np.isfinite(final.surface_temperature_K[valid])) or np.any(
        final.surface_temperature_K[valid] < request.scenario.ambient_temperature_K - 1e-8
    ):
        raise AssertionError("invalid susceptible surface temperature")
    if np.any(final.heated_water_remaining_kg < 0) or np.any(
        final.heated_water_remaining_kg > result.initial_heated_water_kg + 1e-9
    ):
        raise AssertionError("heated water outside initial budget")
    late = np.isfinite(final.burnout_time_s) & (
        final.burnout_time_s < final.time_s - request.scenario.max_dt_s - 1e-8
    )
    if np.any(final.interval_mean_hrr_W[late] != 0):
        raise AssertionError("burned-out material still releases combustion heat")
    b = result.energy_budget
    diverted = (
        result.firebrands["final_budget"]["transferred_chemical_J"] if result.firebrands else 0.0
    )
    errors = {
        "fuel_release": abs(
            float(np.sum((initial - remaining) * request.parameters.heat_of_combustion_J_kg_dry))
            - b.released_J
            - diverted
        ),
        "release_split": abs(b.released_J - b.radiative_emitted_J - b.non_radiative_released_J),
        "radiation_split": abs(
            b.radiative_emitted_J - b.radiative_intercepted_J - b.radiative_unintercepted_J
        ),
        "contact_split": abs(
            b.non_radiative_released_J - b.convective_transferred_J - b.nonradiative_untransferred_J
        ),
        "susceptible_radiation_split": abs(
            b.susceptible_incident_J - b.susceptible_absorbed_J - b.susceptible_unabsorbed_J
        ),
    }
    tolerance = 1e-5 + 1e-9 * (b.released_J + diverted)
    if max(errors.values()) > tolerance:
        raise AssertionError(f"energy partition failed: {errors}, tolerance={tolerance}")
    return {
        "ignited_cells": int(np.isfinite(final.ignition_time_s).sum()),
        "burned_out_cells": int((final.state == 4).sum()),
        "burning_cells": int((final.state == 3).sum()),
        "heated_cells": int((final.state == 2).sum()),
        "consumed_fuel_kg": result.consumed_fuel_kg,
        "remaining_fuel_kg": float(remaining.sum()),
        "energy_budget": asdict(b),
        "energy_closure_errors_J": errors,
        "energy_tolerance_J": tolerance,
        **({"firebrand_budget": result.firebrands["final_budget"]} if result.firebrands else {}),
    }


def compare_steps(coarse, fine):
    a, b = coarse.final_state, fine.final_state
    left, right = np.isfinite(a.ignition_time_s), np.isfinite(b.ignition_time_s)
    matched = left & right
    union = int((left | right).sum())
    intersection = int(matched.sum())
    jaccard = intersection / union if union else 1.0
    differences = np.abs(a.ignition_time_s[matched] - b.ignition_time_s[matched])
    late = matched & (b.ignition_time_s > 0)
    relative = np.abs(a.ignition_time_s[late] - b.ignition_time_s[late]) / np.maximum(
        b.ignition_time_s[late], 1.0
    )
    consumption_difference = abs(coarse.consumed_fuel_kg - fine.consumed_fuel_kg) / max(
        fine.consumed_fuel_kg, 1e-9
    )
    p95 = float(np.quantile(relative, 0.95)) if relative.size else 0.0
    # Declared engineering tolerances, not claims of real-world model accuracy.
    outcome_pass = np.array_equal(left, right) if union < 50 else jaccard >= 0.98
    return {
        "same_ignited_set": bool(np.array_equal(left, right)),
        "ignited_set_jaccard": jaccard,
        "coarse_ignited": int(left.sum()),
        "fine_ignited": int(right.sum()),
        "max_matched_ignition_difference_s": float(differences.max()) if differences.size else 0.0,
        "p95_relative_ignition_difference": p95,
        "relative_consumed_fuel_difference": consumption_difference,
        "engineering_tolerance_pass": bool(
            outcome_pass and p95 <= 0.05 and consumption_difference <= 0.02
        ),
        "tolerances": {
            "small_ignited_set_exact_below": 50,
            "large_set_min_jaccard": 0.98,
            "p95_relative_ignition_difference": 0.05,
            "relative_consumed_fuel_difference": 0.02,
        },
    }
