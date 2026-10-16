"""Explicit, isolated ignition-threshold sensitivity; never alters the source case."""

from dataclasses import replace

import numpy as np

from pyfireca.planning.contracts import require_number


def ignition_rise_sensitivity(bundle, rise_factor=0.95):
    """Scale the ignition temperature *rise above ambient*, not Kelvin values.

    Only modeled burnable material changes. Fuel mass, moisture, emitted energy,
    heat-transfer rules and noncombustibles are identical. This is a declared
    sensitivity hypothesis, not calibration or an empirically measured material.
    """
    require_number("rise_factor", rise_factor, positive=True)
    if rise_factor > 1:
        raise ValueError("this sensitivity requires rise_factor <= 1")
    request = bundle.request
    ambient = request.scenario.ambient_temperature_K
    temperature = np.array(request.parameters.ignition_temperature_K, copy=True)
    mask = request.burnable_mask
    if np.any(temperature[mask] <= ambient):
        raise ValueError("burnable ignition temperature must exceed ambient")
    temperature[mask] = ambient + rise_factor * (temperature[mask] - ambient)
    parameters = replace(request.parameters, ignition_temperature_K=temperature)
    metadata = dict(bundle.metadata)
    metadata["ignition_sensitivity"] = {
        "formula": "T_ign_new = T_ambient + factor * (T_ign_base - T_ambient)",
        "rise_factor": rise_factor,
        "affected_cells": int(mask.sum()),
        "empirically_calibrated": False,
        "purpose": "predeclared adverse sensitivity, evaluated for every compared plan",
    }
    return replace(bundle, request=replace(request, parameters=parameters), metadata=metadata)
