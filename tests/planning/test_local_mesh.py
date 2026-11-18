"""Physical invariants for local mesh controls, without external village data."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import shapely

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.solver_v3 import run_v3_simulation

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_batuan_local_mesh as local  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "mesh_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_bilinear_preserves_linear_terrain_without_new_extrema():
    values = np.array([[10.0, 12.0], [13.0, 15.0]])
    np.testing.assert_allclose(local.bilinear(values, np.array([0.25]), np.array([0.5])), [11.75])
    np.testing.assert_allclose(
        local.bilinear(values, np.array([-1.0, 3.0]), np.array([0.0, 2.0])), [10, 15]
    )


def test_real_geometry_area_and_gap_survive_one_metre_mesh():
    a = shapely.box(5.2, 5.1, 10.2, 10.1)
    b = shapely.box(13.7, 5.1, 18.7, 10.1)
    grid = VillageGridSpec(20, 25, 1, 0, 20)
    boxes = local.geometry_grid(grid)
    aa = shapely.area(shapely.intersection(boxes, a))
    bb = shapely.area(shapely.intersection(boxes, b))
    assert aa.sum() == pytest.approx(a.area)
    assert bb.sum() == pytest.approx(b.area)
    assert not np.any((aa[:, :-1] > 0) & (bb[:, 1:] > 0))
    assert not np.any((aa > 0) & (bb > 0))


def test_clearing_preserves_building_energy_and_cross_gap_radiation():
    original = fixture.make_request(("building", "grass", "building"), end_time_s=4)
    original = replace(original, building_index=np.array([[10, -1, 20]]))
    cleared = local.clear_local_ground(original)
    assert cleared.burnable_mask.tolist() == [[True, False, True]]
    for name in original.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(cleared.parameters, name)[:, [0, 2]],
            getattr(original.parameters, name)[:, [0, 2]],
        )
        assert getattr(cleared.parameters, name)[0, 1] == 0
    edges = cleared.exposure_edges
    assert np.any((edges.source == 0) & (edges.target == 2))
    assert not np.any((edges.source == 1) | (edges.target == 1))
    result = run_v3_simulation(cleared, retain_snapshots=False)
    assert result.final_state.absorbed_radiation_J[0, 2] > 0
    assert result.initial_fuel_kg[0, 1] == 0


def test_density_rejects_unreported_assembly_mixing():
    request = fixture.make_request(("building", "grass"), end_time_s=4)
    with pytest.raises(ValueError, match="nonuniform reference assembly"):
        local.density_fields(request, request.burnable_mask)
