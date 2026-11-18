"""Physical source-area matching, local support and intervention comparison guards."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    fixed_area_ignition,
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
    unambiguous_roof,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout, roof_contact_operator
from pyfireca.village.flame_contact_v3 import FlameContactParameters

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "scale_synthetic", ROOT / "examples/research/heritage/synthetic_case.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def inputs():
    bundle, transport, inventory, _ = fixture.make_case()
    bundle = replace(
        bundle,
        request=replace(
            bundle.request,
            flame_contact=FlameContactParameters(
                1100,
                20,
                0.5,
                5,
                "synthetic",
                "roof support control",
                terrain_reference="shared_face",
            ),
        ),
    )
    return bundle, transport, inventory, resolve_roof_layout(bundle.request, inventory)


def refined(size=1.0, area=1.0, phase=(0.0, 0.0)):
    bundle, transport, inventory, base = inputs()
    footprint = fixed_area_ignition(inventory, "S", (5.0, 9.0), area)
    layout = refine_roof_layout(
        base,
        inventory,
        cell_size_m=size,
        refined_ids=("S", "G"),
        footprints=(footprint,),
        phase_fraction=phase,
    )
    return bundle, transport, inventory, layout, footprint


def seed_budget(layout):
    mask = np.asarray([i is not None for i in layout.ignition_owner])
    return {
        name: float(getattr(layout.thermal.parameters, name)[0, mask].sum())
        for name in (
            "covered_area_m2",
            "initial_dry_fuel_mass_kg",
            "water_mass_kg",
            "available_energy_J",
            "peak_hrr_W",
        )
    }


def test_fixed_area_is_nested_and_not_a_cell_count():
    _, _, inventory, _ = inputs()
    small = fixed_area_ignition(inventory, "S", (5.0, 9.0), 1.0)
    large = fixed_area_ignition(inventory, "S", (5.0, 9.0), 2.0)
    assert small.geometry.area == pytest.approx(1, abs=1e-8)
    assert large.geometry.area == pytest.approx(2, abs=1e-8)
    assert small.geometry.difference(large.geometry).area < 1e-9
    assert small.geometry.difference(unambiguous_roof(inventory, "S")).area == 0
    assert small.sha256 != large.sha256


def test_same_source_polygon_and_budget_across_three_scales_and_phase():
    reference = None
    for size, phase in ((2.0, (0.0, 0.0)), (1.0, (0.0, 0.0)), (0.5, (0.0, 0.0)), (0.5, (0.5, 0.5))):
        _, _, _, layout, footprint = refined(size=size, phase=phase)
        budget = seed_budget(layout)
        if reference is None:
            reference = budget
        for name in budget:
            assert budget[name] == pytest.approx(reference[name], rel=1e-9, abs=1e-7)
        union = unary_union(
            [
                p
                for p, direct in zip(layout.geometry, layout.ignition_owner, strict=True)
                if direct is not None
            ]
        )
        assert union.symmetric_difference(footprint.geometry).area < 1e-8
        assert budget["covered_area_m2"] == pytest.approx(1.0)
        assert budget["initial_dry_fuel_mass_kg"] == pytest.approx(18.0)
        assert not any(
            direct is not None and ids != ("S",)
            for direct, ids in zip(layout.ignition_owner, layout.owners, strict=True)
        )


def test_refinement_conserves_parent_fuel_and_does_not_refine_background():
    bundle, _, _, layout, _ = refined(size=0.5)
    for name in (
        "covered_area_m2",
        "water_mass_kg",
        "initial_dry_fuel_mass_kg",
        "available_energy_J",
    ):
        restored = np.bincount(
            layout.cell_id,
            weights=getattr(layout.thermal.parameters, name).ravel(),
            minlength=bundle.request.grid.height * bundle.request.grid.width,
        )
        np.testing.assert_allclose(
            restored, getattr(bundle.request.parameters, name).ravel(), rtol=1e-9, atol=1e-6
        )
    assert len(layout.nodes("M")) == 1
    assert len(layout.nodes("G")) == 16
    assert layout.resolution_protocol["parent_ground_grid_m"] == 2
    assert layout.resolution_protocol["background_scene"] == "full_parent_domain_no_crop"


def test_contact_uses_local_physical_support_and_never_crosses_a_gap():
    _, _, _, layout, _ = refined(size=1.0, area="all")
    contact = refined_contact_operator(layout)
    sources = set(layout.nodes("S"))
    cores = set(layout.nodes("G"))
    internal = [
        (s, slot) for s in sources for slot, t in enumerate(contact.neighbors[s]) if t in sources
    ]
    assert internal
    assert all(contact.geometry_fraction[s, slot] == pytest.approx(1) for s, slot in internal)
    assert not any(t in cores for s in sources for t in contact.neighbors[s])


def test_unrefined_contact_graph_matches_previous_closure():
    _, _, inventory, base = inputs()
    layout = refine_roof_layout(
        base, inventory, cell_size_m=2.0, refined_ids=("S", "G"), footprints=()
    )
    old, new = roof_contact_operator(base), refined_contact_operator(layout)

    def edges(operator):
        return {
            (s, int(t)): operator.geometry_fraction[s, k]
            for s, targets in enumerate(operator.neighbors)
            for k, t in enumerate(targets)
            if t >= 0
        }

    assert edges(old) == edges(new)


def test_split_ignition_only_forces_polygon_and_paired_demolition_keeps_source_budget():
    bundle, transport, inventory, layout, _ = refined(size=1.0, area=1.0)
    engine = RoofHeritageEvaluationEngine(
        bundle,
        transport,
        inventory,
        source_policy="technical_probe",
        prepared_layout=layout,
        contact_builder=refined_contact_operator,
    )
    scenario = EvaluationScenario("prescribed-area", footprint_ignitions(layout), 20.0)
    baseline, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("base"))
    changed, _, _ = engine.prepare(
        scenario,
        BuildingDemolitionPlan(
            "remove-M",
            ("M",),
            ground_evidence="synthetic_clean_ground",
        ),
    )
    direct = [e.col for e in baseline.node_request.scenario.ignitions]
    assert all(layout.ignition_owner[i] == "S" for i in direct)
    for name in baseline.node_request.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(baseline.node_request.parameters, name)[0, direct],
            getattr(changed.node_request.parameters, name)[0, direct],
        )
    result, _, _ = engine.simulate(scenario, BuildingDemolitionPlan("base"))
    assert result.diagnostics["source_verified"]
    assert result.diagnostics["roof_resolution_protocol"]["cell_size_m"] == 1.0


@pytest.mark.parametrize("area", [0.0, -1.0, 5.0, float("nan")])
def test_invalid_source_area_rejected(area):
    _, _, inventory, _ = inputs()
    with pytest.raises(ValueError):
        fixed_area_ignition(inventory, "S", (5.0, 9.0), area)


def test_core_source_and_outside_or_ambiguous_footprint_rejected():
    _, _, inventory, base = inputs()
    with pytest.raises(ValueError, match="ordinary"):
        fixed_area_ignition(inventory, "G", (9, 9), 1.0)
    footprint = RoofIgnitionFootprint("S", box(3, 8, 5, 10), "outside control")
    with pytest.raises(ValueError, match="beyond"):
        refine_roof_layout(
            base, inventory, cell_size_m=1.0, refined_ids=("S",), footprints=(footprint,)
        )


def test_node_budget_and_foreign_layout_binding_rejected():
    bundle, transport, inventory, base = inputs()
    with pytest.raises(ValueError, match="node limit"):
        refine_roof_layout(
            base, inventory, cell_size_m=0.5, refined_ids=("S",), footprints=(), max_nodes=2
        )
    with pytest.raises(ValueError, match="match"):
        RoofHeritageEvaluationEngine(
            bundle,
            transport,
            inventory,
            source_policy="technical_probe",
            prepared_layout=replace(base, inventory_sha256="wrong"),
        )
