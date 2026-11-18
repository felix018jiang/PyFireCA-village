"""Local bands preserve gaps between protected roofs instead of filling a hull."""

import importlib.util
from pathlib import Path

import pytest
import shapely
from shapely.geometry import shape

from pyfireca.planning.roof_thermal import resolve_roof_layout


@pytest.fixture
def inputs(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "local_union_joint_fixture", Path(__file__).with_name("test_roof_joint.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.joint_case.__wrapped__()


def test_inner_geometry_is_exact_protected_union_and_leaves_intervening_space_out(inputs):
    from prepare_heritage_protected_union_bands import generate_protected_union_bands

    bundle, transport, inventory, _, _ = inputs
    base = resolve_roof_layout(bundle.request, inventory)
    plans, _, inner_mapping = generate_protected_union_bands(
        bundle.request,
        base,
        inventory,
        seed_ids=("M",),
        actual_source_ids=("S",),
        radius_m=transport.exposure_radius_m,
        offsets=(0,),
        width_m=0.01,
    )
    protected = shapely.union_all([inventory.by_id[i].geometry for i in ("S", "G", "F")])
    inner = shape(inner_mapping)
    assert inner.equals(protected)
    assert inner.area < protected.convex_hull.area
    assert plans[0]["inner_geometry_basis"].endswith("no_convex_hull")
    assert plans[0]["protected_union_area_m2"] == protected.area


def test_width_sweep_never_demolishes_a_source_or_core_and_keeps_original_seed(inputs):
    from prepare_heritage_protected_union_bands import generate_protected_union_bands

    bundle, transport, inventory, _, _ = inputs
    base = resolve_roof_layout(bundle.request, inventory)
    for width in (0.01, 2.5, 5, 10, 15.01):
        plans, _, _ = generate_protected_union_bands(
            bundle.request,
            base,
            inventory,
            seed_ids=("M",),
            actual_source_ids=("S",),
            radius_m=transport.exposure_radius_m,
            offsets=(0,),
            width_m=width,
        )
        plan = plans[0]
        assert "M" in plan["demolished_ids"]
        assert not set(plan["demolished_ids"]) & {"S", "G", "F"}
        assert plan["protected_original_roof_node_cleared_count"] == 0
        assert plan["C_construction_action_area_m2"] == pytest.approx(
            plan["cleared_roof_geometry_union_area_m2"]
            + 25 * plan["cleared_vegetation_parent_cell_count"]
        )
