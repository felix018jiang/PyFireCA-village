"""Isolated, explicit static-wind and ignition-rise stress-test environments."""

from dataclasses import asdict, dataclass, replace

import numpy as np

from pyfireca.planning.contracts import require_id, require_number
from pyfireca.planning.sensitivity import ignition_rise_sensitivity
from pyfireca.village.transport_v3 import (
    DEFAULT_MAX_V3_TRANSPORT_EDGES,
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)


@dataclass(frozen=True, slots=True)
class ProtectionEnvironment:
    environment_id: str
    ignition_rise_factor: float
    wind_speed_m_s: float
    wind_from_deg: float
    flame_tilt_coefficient_s_m: float
    evidence: str = "synthetic_stress_test_not_calibrated"

    def __post_init__(self):
        require_id("environment_id", self.environment_id)
        require_number("ignition_rise_factor", self.ignition_rise_factor, positive=True)
        if self.ignition_rise_factor > 1:
            raise ValueError("ignition rise factor must be <= 1")
        for name in ("wind_speed_m_s", "wind_from_deg", "flame_tilt_coefficient_s_m"):
            require_number(name, getattr(self, name))
        if not 0 <= self.wind_from_deg < 360:
            raise ValueError("wind direction must lie in [0, 360)")
        if self.wind_speed_m_s and not self.flame_tilt_coefficient_s_m:
            raise ValueError("nonzero wind requires a nonzero tilt coefficient")
        if self.evidence != "synthetic_stress_test_not_calibrated":
            raise ValueError("these environments must explicitly declare synthetic evidence")


def apply_environment(bundle, environment):
    """Rebuild radiation geometry, without changing contact rules or the parent.

    Meteorological FROM direction is relative to grid north. The legacy V3
    closure shifts and lowers the radiative point at half flame height by
    theta=atan(coefficient*speed). No wind-driven convection or embers are added.
    """
    if not isinstance(environment, ProtectionEnvironment):
        raise TypeError("environment must be ProtectionEnvironment")
    changed = ignition_rise_sensitivity(bundle, environment.ignition_rise_factor)
    changed, _ = _rebuild_wind_transport(changed, environment, DEFAULT_MAX_V3_TRANSPORT_EDGES)
    return changed


def apply_static_wind(
    bundle,
    *,
    wind_speed_m_s,
    wind_from_deg,
    flame_tilt_coefficient_s_m,
    max_edge_count=DEFAULT_MAX_V3_TRANSPORT_EDGES,
):
    """Apply one uniform background wind to the transparent V3 thermal model.

    Direction is the meteorological FROM angle clockwise from grid north. Return
    both the rebuilt parent case and the matching transport to be used when
    constructing any refined roof/ground graph for that case.
    """
    if bundle.request.firebrands is not None:
        raise ValueError("static-wind-only comparisons require firebrands disabled")
    environment = ProtectionEnvironment(
        "static_wind", 1.0, wind_speed_m_s, wind_from_deg, flame_tilt_coefficient_s_m
    )
    request = bundle.request
    baseline = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    expected = _transparent_edges(request, baseline, max_edge_count)
    if any(
        not np.array_equal(getattr(expected, field), getattr(request.exposure_edges, field))
        for field in ("source", "target", "weight")
    ):
        raise ValueError("static wind requires a verified transparent parent graph")
    del expected
    return _rebuild_wind_transport(bundle, environment, max_edge_count)


def _transparent_edges(request, transport, max_edge_count):
    return build_v3_radiative_transport_edges(
        request.grid,
        request.source_mask,
        request.target_mask,
        request.parameters.receiver_area_m2,
        request.parameters.source_height_m,
        request.parameters.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
        max_edge_count=max_edge_count,
    )


def _rebuild_wind_transport(bundle, environment, max_edge_count):
    request = bundle.request
    transport = replace(
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        wind_speed_m_s=environment.wind_speed_m_s,
        wind_from_deg=environment.wind_from_deg,
        flame_tilt_coefficient_s_m=environment.flame_tilt_coefficient_s_m,
    )
    edges = _transparent_edges(request, transport, max_edge_count)
    metadata = dict(bundle.metadata)
    metadata.update(
        protection_environment=asdict(environment),
        transport_parameters=asdict(transport),
        wind_scope="static_radiative_flame_tilt_only_no_ember_or_wind_contact",
    )
    brands = request.firebrands
    if brands is not None:
        brands = replace(
            brands,
            wind_speed_m_s=environment.wind_speed_m_s,
            wind_from_deg=environment.wind_from_deg,
        )
        metadata["wind_scope"] = "static_radiative_tilt_and_prescribed_terminal_firebrands"
    return (
        replace(
            bundle,
            request=replace(request, exposure_edges=edges, firebrands=brands),
            metadata=metadata,
        ),
        transport,
    )
