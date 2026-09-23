from __future__ import annotations

import numpy as np
import pytest

from pyfireca.village.domain import CellParameterArrays, PreparedVillageDomain, VillageGridSpec
from pyfireca.village.exposure import (
    SourceIndexedExposure,
    build_exposure_edges,
    estimate_exposure_resources,
    integrate_exposure_dose,
    target_power_W,
)


def _parameters(shape: tuple[int, int]) -> CellParameterArrays:
    ones = np.ones(shape, dtype=np.float64)
    return CellParameterArrays(
        fuel_load_kg_m2=2 * ones,
        heat_of_combustion_J_kg=1_000_000 * ones,
        mass_loss_flux_kg_m2_s=0.1 * ones,
        active_area_ratio=ones,
        emission_fraction=0.2 * ones,
        absorptivity=0.8 * ones,
        ignition_dose_J_m2=5000 * ones,
        exposure_decay_s=10 * ones,
    )


def _domain(shape: tuple[int, int] = (3, 3)) -> PreparedVillageDomain:
    mask = np.ones(shape, dtype=np.bool_)
    return PreparedVillageDomain(
        grid=VillageGridSpec(*shape, 1.0, 0.0, float(shape[0])),
        material_index=np.zeros(shape, dtype=np.int32),
        domain_mask=mask,
        known_mask=mask,
        burnable_mask=mask,
        parameters=_parameters(shape),
    )


def test_constant_flux_matches_analytical_solution() -> None:
    dose = integrate_exposure_dose(np.array([0.0]), np.array([1000.0]), np.array([10.0]), 7.0)
    assert dose[0] == pytest.approx(10_000.0 * (1.0 - np.exp(-0.7)))


def test_source_free_decay_matches_analytical_solution() -> None:
    dose = integrate_exposure_dose(np.array([10_000.0]), np.array([0.0]), np.array([10.0]), 10.0)
    assert dose[0] == pytest.approx(3678.7944117)


def test_edge_weights_are_deterministic_and_budget_limited() -> None:
    first = build_exposure_edges(_domain(), exposure_radius_m=1.5, decay_length_m=5.0)
    second = build_exposure_edges(_domain(), exposure_radius_m=1.5, decay_length_m=5.0)
    np.testing.assert_array_equal(first.source, second.source)
    np.testing.assert_array_equal(first.target, second.target)
    np.testing.assert_allclose(first.weight, second.weight)
    outgoing = np.bincount(first.source, weights=first.weight, minlength=9)
    assert np.all(outgoing <= 1.0 + 1e-12)


def test_westerly_wind_gives_larger_eastward_weight() -> None:
    edges = build_exposure_edges(
        _domain((1, 3)),
        exposure_radius_m=1.1,
        decay_length_m=5.0,
        wind_speed_m_s=2.0,
        wind_from_deg=270.0,
        wind_beta_s_m=1.0,
    )
    center = 1
    outgoing = {
        int(target): float(weight)
        for source, target, weight in zip(edges.source, edges.target, edges.weight, strict=True)
        if source == center
    }
    assert outgoing[2] > outgoing[0]


def test_resource_estimate_and_limit_are_checked_before_graph_allocation() -> None:
    domain = _domain((10, 10))
    estimate = estimate_exposure_resources(domain, exposure_radius_m=2.1)
    assert estimate.cell_count == 100
    assert estimate.neighbor_offset_count == 12
    assert estimate.exposure_edge_upper_bound == 1200
    assert estimate.exposure_arrays_upper_bound_bytes == 1200 * 16

    with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
        build_exposure_edges(
            domain,
            exposure_radius_m=2.1,
            decay_length_m=5.0,
            max_edge_count=1199,
        )


@pytest.mark.parametrize("active_sources", [(), (0,), (0, 4), tuple(range(9))])
def test_source_indexed_exposure_matches_dense_distribution(
    active_sources: tuple[int, ...],
) -> None:
    edges = build_exposure_edges(_domain(), exposure_radius_m=1.5, decay_length_m=5.0)
    indexed = SourceIndexedExposure.from_edges(edges)
    power = np.zeros(edges.cell_count, dtype=np.float64)
    power[list(active_sources)] = np.arange(1, len(active_sources) + 1) * 1000.0

    np.testing.assert_allclose(
        indexed.target_power_W(power), target_power_W(power, edges), rtol=0.0, atol=1e-12
    )


def test_source_indexed_exposure_preserves_duplicate_target_contributions() -> None:
    from pyfireca.village.domain import ExposureEdges

    edges = ExposureEdges(
        source=np.asarray([0, 0, 2], dtype=np.int32),
        target=np.asarray([1, 1, 1], dtype=np.int32),
        weight=np.asarray([0.2, 0.3, 0.4]),
        cell_count=3,
    )
    power = np.asarray([10.0, 0.0, 0.0])
    indexed = SourceIndexedExposure.from_edges(edges, dense_edge_fraction=1.0)

    np.testing.assert_allclose(indexed.target_power_W(power), target_power_W(power, edges))
