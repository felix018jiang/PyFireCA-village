"""Independent intervention inputs, unchanged targets, radiation and budgets."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.effectiveness import effectiveness_score
from pyfireca.planning.engines.village import VillageEvaluationEngine, request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import (
    ClearingPath,
    ClearingPlan,
    ClearingStrip,
    InterventionConflict,
)
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "evaluation_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
TRANSPORT = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)


def make_engine(labels=("grass",) * 3, **kwargs):
    request = fixture.make_request(labels, end_time_s=600)
    contact = load_flame_contact_parameters(
        ROOT / "examples/village/vegetation_v3/flame-contact.synthetic.yml"
    )
    request = replace(request, flame_contact=contact)
    bundle = V3CaseBundle(request, {}, {}, {})
    return VillageEvaluationEngine(bundle, TRANSPORT, code_version="test", **kwargs)


def scenario(engine, *, end=600, col=0):
    return EvaluationScenario(
        "same-fire", (Ignition(engine.bundle.request.grid.center(0, col)),), end
    )


def strip(width=2):
    # Middle cell spans x=[2,4], y=[0,2].
    return ClearingStrip((3.0, 0.0), (3.0, 2.0), width)


def test_empty_plan_reproduces_original_state_and_fingerprint():
    engine = make_engine()
    original = engine.bundle.request
    request, fraction, record = engine.prepare(scenario(engine), ClearingPlan("none"))
    before = request_fingerprint(original)
    a = run_v3_simulation(original, retain_snapshots=False)
    b = run_v3_simulation(request, retain_snapshots=False)
    for name in a.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(getattr(a.final_state, name), getattr(b.final_state, name))
    assert not fraction.any()
    assert record["cleared_area_m2"] == 0
    assert request_fingerprint(original) == before
    result = engine.evaluate(scenario(engine), ClearingPlan("none"))
    assert result.metrics["ignited_cells"] == 3
    assert result.diagnostics["parent_case_unchanged"]
    assert not result.capabilities["spotting"]


def test_straight_path_reproduces_legacy_strip_physics():
    engine = make_engine()
    old = ClearingPlan("strip", (strip(),))
    new = ClearingPlan("path", paths=(ClearingPath(((3.0, 0.0), (3.0, 2.0)), 2),))
    a, fa, _ = engine.prepare(scenario(engine), old)
    b, fb, _ = engine.prepare(scenario(engine), new)
    np.testing.assert_array_equal(fa, fb)
    for name in a.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(getattr(a.parameters, name), getattr(b.parameters, name))
    assert (
        engine.evaluate(scenario(engine), old).metrics
        == engine.evaluate(scenario(engine), new).metrics
    )


def test_multiple_bent_paths_preserve_buildings_and_union_area():
    engine = make_engine(("tree_equivalent", "building", "tree_equivalent"))
    p = ClearingPath(((2.5, 0.5), (4.0, 1.0), (5.5, 1.0)), 0.5)
    plan = ClearingPlan("bent", paths=(p, p))
    _, fraction, summary = engine.prepare(scenario(engine), plan)
    assert fraction[0, 1] == 0
    assert fraction[0, 2] > 0
    _, one, single = engine.prepare(scenario(engine), replace(plan, paths=(p,)))
    np.testing.assert_array_equal(fraction, one)
    assert summary["cleared_area_m2"] == single["cleared_area_m2"]


def test_strip_removes_fuel_but_preserves_radiation_across_gap():
    engine = make_engine()
    request, fraction, record = engine.prepare(scenario(engine), ClearingPlan("strip", (strip(),)))
    assert fraction.tolist() == [[0, 1, 0]]
    assert record["cleared_area_m2"] == 4
    assert not request.burnable_mask[0, 1]
    assert not request.source_mask[0, 1]
    assert not request.target_mask[0, 1]
    assert request.parameters.pyrolysable_dry_fuel_mass_kg[0, 1] == 0
    edges = request.exposure_edges
    assert np.any((edges.source == 0) & (edges.target == 2) & (edges.weight > 0))
    result = engine.evaluate(scenario(engine), ClearingPlan("strip", (strip(),)))
    assert result.metrics["ignited_cells"] == 1
    assert result.diagnostics["energy_budget"]["released_J"] > 0


def test_fractional_coverage_scales_all_extensive_physics_and_preserves_curve():
    engine = make_engine()
    old = engine.bundle.request.parameters
    request, fraction, record = engine.prepare(
        scenario(engine), ClearingPlan("narrow", (strip(0.5),))
    )
    assert fraction[0, 1] == pytest.approx(0.25)
    assert record["cleared_area_m2"] == pytest.approx(1)
    assert request.burnable_mask[0, 1]  # A narrow band is not a whole inert cell.
    assert request.parameters.peak_hrr_W[0, 1] == pytest.approx(old.peak_hrr_W[0, 1] * 0.75)
    assert request.parameters.water_mass_kg[0, 1] == pytest.approx(old.water_mass_kg[0, 1] * 0.75)
    assert request.parameters.ignition_temperature_K[0, 1] == old.ignition_temperature_K[0, 1]
    np.testing.assert_allclose(
        request.parameters.available_energy_J, request.parameters.configured_curve_energy_J
    )
    engine.evaluate(scenario(engine), ClearingPlan("narrow", (strip(0.5),)))


def test_building_cells_protected_and_not_removed_from_denominator():
    engine = make_engine(("tree_equivalent", "building", "tree_equivalent"))
    old = engine.bundle.request
    request, fraction, record = engine.prepare(scenario(engine), ClearingPlan("strip", (strip(),)))
    assert not fraction.any()
    assert record["cleared_area_m2"] == 0
    np.testing.assert_array_equal(request.building_index, old.building_index)
    np.testing.assert_array_equal(
        request.parameters.initial_dry_fuel_mass_kg, old.parameters.initial_dry_fuel_mass_kg
    )
    result = engine.evaluate(scenario(engine), ClearingPlan("strip", (strip(),)))
    assert result.metrics["protected_structure_count"] == 1


def test_overlap_counted_once_and_construction_zone_respected():
    engine = make_engine(zones={"forbidden": np.zeros((1, 3), dtype=bool)})
    s = scenario(engine)
    _, _, record = engine.prepare(s, ClearingPlan("overlap", (strip(), strip())))
    assert record["cleared_area_m2"] == 4
    _, f, record = engine.prepare(s, ClearingPlan("zone", (strip(),), "forbidden"))
    assert not f.any() and record["cleared_area_m2"] == 0
    with pytest.raises(ValueError, match="unknown construction"):
        engine.prepare(s, ClearingPlan("zone", (), "missing"))


def test_ignition_and_outside_envelope_conflicts_fail_instead_of_zero_loss():
    engine = make_engine()
    with pytest.raises(InterventionConflict, match="ignition cell"):
        engine.evaluate(scenario(engine, col=1), ClearingPlan("strip", (strip(),)))
    with pytest.raises(InterventionConflict, match="outside"):
        engine.evaluate(scenario(engine), ClearingPlan("outside", (strip(10),)))


def test_fingerprint_includes_scenario_plan_and_zone_and_repeats():
    engine = make_engine()
    empty = engine.evaluate(scenario(engine), ClearingPlan("none"))
    repeat = engine.evaluate(scenario(engine), ClearingPlan("none"))
    changed = engine.evaluate(scenario(engine, end=300), ClearingPlan("none"))
    treated = engine.evaluate(scenario(engine), ClearingPlan("strip", (strip(),)))
    assert empty.evaluation_sha256 == repeat.evaluation_sha256
    assert empty.metrics == repeat.metrics
    assert len({empty.evaluation_sha256, changed.evaluation_sha256, treated.evaluation_sha256}) == 3
    assert empty.case_sha256 == treated.case_sha256


def test_unsupported_parent_graph_rejected():
    engine = make_engine()
    request = engine.bundle.request
    graph = replace(
        request,
        exposure_edges=ExposureEdges(
            request.exposure_edges.source,
            request.exposure_edges.target,
            request.exposure_edges.weight * 0.9,
            request.exposure_edges.cell_count,
        ),
    )
    with pytest.raises(ValueError, match="unsupported parent radiation"):
        VillageEvaluationEngine(replace(engine.bundle, request=graph), TRANSPORT)


@pytest.mark.parametrize("width", [0, -1, float("nan"), True])
def test_bad_width_rejected(width):
    with pytest.raises(ValueError):
        strip(width)


def test_unsupported_intervention_and_bad_scenarios():
    engine = make_engine()
    with pytest.raises(TypeError, match="only explicit"):
        engine.evaluate(scenario(engine), {"kind": "wall"})
    with pytest.raises(ValueError):
        EvaluationScenario("empty", (), 600)
    with pytest.raises(ValueError):
        EvaluationScenario("late", (Ignition((1.0, 1.0), 601),), 600)


def test_effectiveness_ordering_excludes_area_and_preserves_equal_effects():
    engine = make_engine()
    result = engine.evaluate(scenario(engine), ClearingPlan("none"))
    # The one-row fixture is entirely on the boundary; use an explicit interior
    # record here to isolate ordering, while separately testing edge rejection.
    result = replace(result, metrics={**result.metrics, "burning_reached_domain_edge": False})
    expensive = replace(result, intervention={"cleared_area_m2": 100000})
    assert effectiveness_score([result]) == effectiveness_score([expensive])
    improved = replace(
        result, metrics={**result.metrics, "ignited_house_count": 0, "ignited_building_area_m2": 0}
    )
    worse = replace(improved, metrics={**improved.metrics, "ignited_house_count": 1})
    assert effectiveness_score([improved]) < effectiveness_score([worse])
    with pytest.raises(ValueError, match="same horizon"):
        effectiveness_score([result, replace(result, horizon_s=300)])
    with pytest.raises(ValueError, match="crop-edge"):
        effectiveness_score(
            [replace(result, metrics={**result.metrics, "burning_reached_domain_edge": True})]
        )


def test_finite_strip_allows_endpoint_bypass_in_contact_only_mechanism():
    # Deliberately isolate contact: radiation radius is below one cell spacing.
    grid = VillageGridSpec(7, 9, 2, 0, 14)
    base = fixture.make_request(("grass",) * 63, end_time_s=1200)
    fields = {
        name: getattr(base.parameters, name).reshape(grid.shape)
        for name in base.parameters.__dataclass_fields__
    }
    physics = replace(base.parameters, **fields)
    arrays = {
        name: getattr(base, name).reshape(grid.shape)
        for name in (
            "domain_mask",
            "known_mask",
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
        )
    }
    from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

    transport = replace(TRANSPORT, exposure_radius_m=1)
    edges = build_v3_radiative_transport_edges(
        grid,
        arrays["source_mask"],
        arrays["target_mask"],
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        transport,
    )
    contact = load_flame_contact_parameters(
        ROOT / "examples/village/vegetation_v3/flame-contact.synthetic.yml"
    )
    request = replace(
        base,
        grid=grid,
        parameters=physics,
        **arrays,
        exposure_edges=edges,
        scenario=replace(base.scenario, ignitions=(VillageIgnitionEvent(0, 3, 0),)),
        flame_contact=contact,
    )
    engine = VillageEvaluationEngine(V3CaseBundle(request, {}, {}, {}), transport)
    s = EvaluationScenario("bypass", (Ignition(grid.center(3, 0)),), 1200)
    finite, _, _ = engine.prepare(
        s, ClearingPlan("finite", (ClearingStrip((9.0, 4.0), (9.0, 10.0), 2),))
    )
    complete, _, _ = engine.prepare(
        s, ClearingPlan("full", (ClearingStrip((9.0, 0.0), (9.0, 14.0), 2),))
    )
    a = run_v3_simulation(finite, retain_snapshots=False)
    b = run_v3_simulation(complete, retain_snapshots=False)
    assert np.isfinite(a.final_state.ignition_time_s[3, 8])
    assert not np.isfinite(b.final_state.ignition_time_s[3, 8])
    assert np.isfinite(a.final_state.ignition_time_s[1, 4])
