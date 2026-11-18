"""Terrain datums must not be mistaken for relative flame lengths."""

from math import pi

import numpy as np
import pytest

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
    build_v3_radiative_transport_edges_reference,
)


def build(ground, *, reference=False, wind=0):
    grid = VillageGridSpec(1, 2, 5.0, 0.0, 5.0)
    fn = (
        build_v3_radiative_transport_edges_reference
        if reference
        else build_v3_radiative_transport_edges
    )
    return fn(
        grid,
        np.array([[True, False]]),
        np.array([[False, True]]),
        np.full((1, 2), 25.0),
        np.full((1, 2), 4.0),
        np.full((1, 2), 2.0),
        np.ones((1, 2)),
        V3RadiativeTransportParameters(6, 5, 0, wind, 270, 0.3),
        ground_elevation_m=ground,
    )


def test_flat_terrain_datum_does_not_change_source_receiver_distance():
    original = build(None)
    elevated = build(np.full((1, 2), 300.0))
    np.testing.assert_allclose(elevated.weight, original.weight, rtol=0, atol=0)
    assert elevated.weight[0] == pytest.approx(25 / (4 * pi * (25 + 25)))


@pytest.mark.parametrize("wind", [0, 4])
def test_slope_differences_and_wind_match_reference_and_are_datum_invariant(wind):
    ground = np.array([[-10.0, -9.0]])
    vector = build(ground, wind=wind)
    oracle = build(ground, wind=wind, reference=True)
    np.testing.assert_allclose(vector.weight, oracle.weight, rtol=1e-14)
    np.testing.assert_allclose(vector.weight, build(ground + 800, wind=wind).weight, rtol=1e-14)
    if wind == 0:
        assert vector.weight[0] == pytest.approx(25 / (4 * pi * (25 + 25 + 1)))


@pytest.mark.parametrize("ground", [np.zeros((2, 2)), np.array([[np.nan, 0.0]])])
def test_bad_terrain_is_rejected(ground):
    with pytest.raises(ValueError, match="ground elevation"):
        build(ground)
