from __future__ import annotations

import pytest

from pyfireca.village.domain import VillageGridSpec


def test_grid_coordinate_contract_matches_project_plan_fixture() -> None:
    grid = VillageGridSpec(
        height=20,
        width=30,
        cell_size_m=2.0,
        x_min_m=0.0,
        y_max_m=40.0,
    )
    assert grid.locate(3.0, 35.0) == (2, 1)
    assert grid.center(2, 1) == (3.0, 35.0)
    assert grid.cell_id(2, 1) == 61
    assert grid.row_col(61) == (2, 1)


def test_right_and_bottom_boundaries_are_outside() -> None:
    grid = VillageGridSpec(2, 3, 2.0, 0.0, 4.0)
    with pytest.raises(IndexError):
        grid.locate(6.0, 3.0)
    with pytest.raises(IndexError):
        grid.locate(1.0, 0.0)


def test_projected_grid_requires_crs() -> None:
    with pytest.raises(ValueError, match=r"require.*crs"):
        VillageGridSpec(2, 2, 1.0, 0.0, 2.0, coordinate_mode="projected")


def test_local_grid_cannot_claim_a_crs() -> None:
    with pytest.raises(ValueError, match="must not claim"):
        VillageGridSpec(2, 2, 1.0, 0.0, 2.0, crs="EPSG:32648")
