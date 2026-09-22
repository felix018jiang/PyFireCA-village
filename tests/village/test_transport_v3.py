from __future__ import annotations

from math import exp, pi

import numpy as np
import pytest

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
    build_v3_radiative_transport_edges_reference,
    enforce_v3_transport_resource_limits,
    estimate_v3_transport_resources,
)


def _parameters(**overrides: float) -> V3RadiativeTransportParameters:
    values = {
        "exposure_radius_m": 2.1,
        "near_field_regularization_m": 0.5,
        "atmospheric_extinction_m_inv": 0.0,
        "wind_speed_m_s": 0.0,
        "wind_from_deg": 0.0,
        "flame_tilt_coefficient_s_m": 0.0,
    }
    values.update(overrides)
    return V3RadiativeTransportParameters(**values)


def _edge_map(edges) -> dict[tuple[int, int], float]:
    return {
        (int(source), int(target)): float(weight)
        for source, target, weight in zip(edges.source, edges.target, edges.weight, strict=True)
    }


def test_vt09_building_and_vegetation_share_one_source_budget() -> None:
    grid = VillageGridSpec(1, 3, 1.0, 0.0, 1.0)
    source = np.array([[False, True, False]])
    one_target = np.array([[True, False, False]])
    two_targets = np.array([[True, False, True]])
    area = np.full(grid.shape, 0.2)
    source_height = np.full(grid.shape, 2.0)
    receiver_height = np.array([[0.1, 0.0, 2.0]])
    transmission = np.ones(grid.shape)
    profile = np.array([["grass", "building", "tree"]])

    first = build_v3_radiative_transport_edges(
        grid,
        source,
        one_target,
        area,
        source_height,
        receiver_height,
        transmission,
        _parameters(),
        profile_id=profile,
    )
    second = build_v3_radiative_transport_edges(
        grid,
        source,
        two_targets,
        area,
        source_height,
        receiver_height,
        transmission,
        _parameters(),
        profile_id=profile,
    )

    assert first.weight.sum() < second.weight.sum() <= 1.0
    with pytest.raises(ValueError, match=r"RADIATION_BUDGET_EXCEEDED.*building"):
        build_v3_radiative_transport_edges(
            grid,
            source,
            two_targets,
            np.full(grid.shape, 100.0),
            source_height,
            receiver_height,
            transmission,
            _parameters(),
            profile_id=profile,
        )


def test_vt10_inert_road_is_not_a_source_but_radiation_can_cross_it() -> None:
    grid = VillageGridSpec(1, 5, 1.0, 0.0, 1.0)
    source = np.array([[True, False, False, False, False]])
    target = np.array([[False, False, True, False, True]])
    edges = build_v3_radiative_transport_edges(
        grid,
        source,
        target,
        np.full(grid.shape, 0.2),
        np.full(grid.shape, 0.5),
        np.full(grid.shape, 0.1),
        np.ones(grid.shape),
        _parameters(exposure_radius_m=3.1),
    )

    assert _edge_map(edges).keys() == {(0, 2)}
    assert 1 not in edges.source
    assert 1 not in edges.target


def test_vt12_per_cell_heights_match_manual_three_dimensional_distance() -> None:
    grid = VillageGridSpec(1, 4, 2.0, 0.0, 2.0)
    source = np.array([[True, False, True, False]])
    target = np.array([[False, True, False, True]])
    area = np.full(grid.shape, 0.25)
    source_height = np.array([[0.4, 0.0, 6.0, 0.0]])
    receiver_height = np.array([[0.0, 0.1, 0.0, 2.0]])
    parameters = _parameters(
        exposure_radius_m=2.1,
        near_field_regularization_m=0.25,
        atmospheric_extinction_m_inv=0.03,
    )
    edges = build_v3_radiative_transport_edges(
        grid,
        source,
        target,
        area,
        source_height,
        receiver_height,
        np.ones(grid.shape),
        parameters,
    )
    weights = _edge_map(edges)

    grass_distance = np.sqrt(2.0**2 + (0.1 - 0.4 / 2.0) ** 2)
    building_distance = np.sqrt(2.0**2 + (2.0 - 6.0 / 2.0) ** 2)
    grass_expected = 0.25 / (4.0 * pi * (grass_distance**2 + 0.25**2)) * exp(-0.03 * grass_distance)
    building_expected = (
        0.25 / (4.0 * pi * (building_distance**2 + 0.25**2)) * exp(-0.03 * building_distance)
    )

    assert weights[(0, 1)] == pytest.approx(grass_expected)
    assert weights[(2, 3)] == pytest.approx(building_expected)
    assert weights[(0, 1)] != pytest.approx(weights[(2, 3)])


def test_vt14_vectorized_blocks_equal_direct_enumeration() -> None:
    grid = VillageGridSpec(5, 5, 1.0, 10.0, 20.0)
    source = np.array(
        [
            [True, True, False, False, False],
            [False, True, True, False, False],
            [False, False, True, True, False],
            [False, False, False, True, True],
            [False, False, False, False, True],
        ]
    )
    target = np.ones(grid.shape, dtype=np.bool_)
    target[0, 4] = False
    area = np.linspace(0.02, 0.08, 25).reshape(grid.shape)
    source_height = np.linspace(0.3, 6.0, 25).reshape(grid.shape)
    receiver_height = np.linspace(0.1, 2.0, 25).reshape(grid.shape)
    transmission = np.ones(grid.shape)
    transmission[2, 1] = 0.4
    transmission[1, 3] = 0.0
    parameters = _parameters(
        exposure_radius_m=2.25,
        wind_speed_m_s=2.0,
        wind_from_deg=225.0,
        flame_tilt_coefficient_s_m=0.2,
    )

    reference = build_v3_radiative_transport_edges_reference(
        grid,
        source,
        target,
        area,
        source_height,
        receiver_height,
        transmission,
        parameters,
    )
    block_one = build_v3_radiative_transport_edges(
        grid,
        source,
        target,
        area,
        source_height,
        receiver_height,
        transmission,
        parameters,
        block_rows=1,
    )
    block_three = build_v3_radiative_transport_edges(
        grid,
        source,
        target,
        area,
        source_height,
        receiver_height,
        transmission,
        parameters,
        block_rows=3,
    )

    for actual in (block_one, block_three):
        assert np.array_equal(actual.source, reference.source)
        assert np.array_equal(actual.target, reference.target)
        assert np.allclose(actual.weight, reference.weight, rtol=1e-14, atol=1e-15)
        assert len(set(zip(actual.source, actual.target, strict=True))) == len(actual.source)


def test_resource_estimate_counts_exact_candidates_and_fails_before_build() -> None:
    grid = VillageGridSpec(3, 3, 1.0, 0.0, 3.0)
    mask = np.ones(grid.shape, dtype=np.bool_)
    estimate = estimate_v3_transport_resources(
        grid, mask, mask, _parameters(exposure_radius_m=1.1), replay_frame_count=101
    )

    assert estimate.offset_count == 4
    assert estimate.candidate_edge_upper_bound == 24
    assert estimate.final_edge_bytes_upper_bound == 24 * 16
    assert estimate.replay_resident_bytes_estimate == 3 * 3 * 101 * 33
    with pytest.raises(ValueError, match=r"RESOURCE_LIMIT.*24.*23.*no edges were built"):
        enforce_v3_transport_resource_limits(estimate, max_edge_count=23, max_memory_mb=2048)
