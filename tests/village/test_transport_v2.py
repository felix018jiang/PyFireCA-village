from __future__ import annotations

import numpy as np
import pytest

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.transport_v2 import (
    V2RadiativeTransportParameters,
    build_v2_radiative_transport_edges,
)


def _parameters(**overrides: float) -> V2RadiativeTransportParameters:
    values = {
        "exposure_radius_m": 4.0,
        "effective_flame_height_m": 2.0,
        "effective_receiver_height_m": 1.0,
        "near_field_regularization_m": 0.0,
        "atmospheric_extinction_m_inv": 0.0,
        "wind_speed_m_s": 0.0,
        "wind_from_deg": 0.0,
        "flame_tilt_coefficient_s_m": 0.0,
    }
    values.update(overrides)
    return V2RadiativeTransportParameters(**values)


def _weights(
    *,
    shape: tuple[int, int] = (1, 4),
    transmission: np.ndarray | None = None,
    parameters: V2RadiativeTransportParameters | None = None,
) -> dict[int, float]:
    grid = VillageGridSpec(*shape, 1.0, 0.0, float(shape[0]))
    source = np.zeros(shape, dtype=np.bool_)
    source[0, 0] = True
    target = np.ones(shape, dtype=np.bool_)
    target[0, 0] = False
    edges = build_v2_radiative_transport_edges(
        grid,
        source,
        target,
        np.ones(shape),
        np.ones(shape) if transmission is None else transmission,
        parameters or _parameters(),
    )
    return {
        int(target_id): float(weight)
        for target_id, weight in zip(edges.target, edges.weight, strict=True)
    }


def test_point_source_weight_obeys_inverse_square_distance() -> None:
    weights = _weights()

    assert weights[1] / weights[2] == pytest.approx(4.0)
    assert weights[1] / weights[3] == pytest.approx(9.0)


def test_intermediate_blocker_multiplies_line_transmission() -> None:
    clear = _weights()
    transmission = np.ones((1, 4))
    transmission[0, 1] = 0.25
    blocked = _weights(transmission=transmission)

    assert blocked[1] == pytest.approx(clear[1])
    assert blocked[2] == pytest.approx(0.25 * clear[2])
    assert blocked[3] == pytest.approx(0.25 * clear[3])


def test_opaque_intermediate_cell_removes_shadowed_edges() -> None:
    transmission = np.ones((1, 4))
    transmission[0, 1] = 0.0

    weights = _weights(transmission=transmission)

    assert set(weights) == {1}


def test_wind_tilt_moves_effective_source_downwind() -> None:
    shape = (1, 3)
    grid = VillageGridSpec(*shape, 1.0, 0.0, 1.0)
    source = np.zeros(shape, dtype=np.bool_)
    source[0, 1] = True
    target = ~source
    edges = build_v2_radiative_transport_edges(
        grid,
        source,
        target,
        np.ones(shape),
        np.ones(shape),
        _parameters(
            wind_speed_m_s=2.0,
            wind_from_deg=270.0,
            flame_tilt_coefficient_s_m=0.5,
        ),
    )
    weights = {
        int(target_id): float(weight)
        for target_id, weight in zip(edges.target, edges.weight, strict=True)
    }

    assert weights[2] > weights[0]


def test_kernel_rejects_nonphysical_outgoing_energy_allocation() -> None:
    shape = (3, 3)
    grid = VillageGridSpec(*shape, 1.0, 0.0, 3.0)
    source = np.zeros(shape, dtype=np.bool_)
    source[1, 1] = True
    target = ~source
    with pytest.raises(ValueError, match="more than all radiative power"):
        build_v2_radiative_transport_edges(
            grid,
            source,
            target,
            np.full(shape, 100.0),
            np.ones(shape),
            _parameters(),
        )
