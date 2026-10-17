"""Protection certificates require all scenarios; wind alters real transport."""

import importlib.util
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.environments import ProtectionEnvironment, apply_environment
from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.robust import ProtectionScreen, screen_protection
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle


def record(sid, *, houses=0, bridges=0, edge=False):
    return EvaluationResult(
        "test",
        "case",
        sid,
        "ca",
        sid,
        "plan",
        5400,
        {
            "ignited_house_count": houses,
            "ignited_bridge_count": bridges,
            "burning_reached_domain_edge": edge,
        },
        {},
        {},
        {},
    )


def test_safe_prefix_cannot_be_misreported_as_a_certificate():
    with pytest.raises(ValueError, match="incomplete"):
        ProtectionScreen(("a", "b"), (record("a"),))
    assert not ProtectionScreen(("a", "b"), (record("a", bridges=1),)).passed
    assert ProtectionScreen(("a", "b"), (record("a"), record("b"))).passed


def test_first_bridge_failure_stops_solves_but_full_control_keeps_all():
    called = []

    def evaluate(sid):
        called.append(sid)
        return record(sid, bridges=int(sid == "b"))

    result = screen_protection(("a", "b", "c"), evaluate)
    assert called == ["a", "b"]
    assert not result.passed and not result.complete
    assert result.to_mapping()["losses_are_lower_bounds"]
    called.clear()
    full = screen_protection(("a", "b", "c"), evaluate, stop_at_failure=False)
    assert called == ["a", "b", "c"]
    assert full.complete and not full.passed


@pytest.mark.parametrize("kind", ["ids", "edge", "horizon", "empty"])
def test_bad_screens_cannot_pass(kind):
    records = (record("a"), record("b"))
    if kind == "ids":
        records = records[::-1]
    elif kind == "edge":
        records = (record("a", edge=True), record("b"))
    elif kind == "horizon":
        records = (replace(record("a"), horizon_s=3600), record("b"))
    else:
        records = ()
    with pytest.raises(ValueError):
        ProtectionScreen(("a", "b"), records)


def bundle():
    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "robust_fixture", root / "examples/village/vegetation_v3/audit_spread.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = module.make_request(("tree_equivalent",) * 3, end_time_s=600)
    transport = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)
    return V3CaseBundle(request, {}, {}, {"transport_parameters": asdict(transport)})


def test_calm_is_exact_and_parent_survives_wind_and_material_clones():
    original = bundle()
    before = request_fingerprint(original.request)
    calm = apply_environment(original, ProtectionEnvironment("calm", 1, 0, 0, 0.2))
    assert request_fingerprint(calm.request) == before
    wind = apply_environment(original, ProtectionEnvironment("wind", 0.95, 4, 225, 0.2))
    assert request_fingerprint(wind.request) != before
    assert request_fingerprint(original.request) == before
    ambient = original.request.scenario.ambient_temperature_K
    np.testing.assert_allclose(
        wind.request.parameters.ignition_temperature_K,
        ambient + 0.95 * (original.request.parameters.ignition_temperature_K - ambient),
    )
    for name in original.request.parameters.__dataclass_fields__:
        if name != "ignition_temperature_K":
            np.testing.assert_array_equal(
                getattr(wind.request.parameters, name), getattr(original.request.parameters, name)
            )


def test_wind_from_convention_changes_actual_directed_radiation():
    original = bundle()
    east = apply_environment(original, ProtectionEnvironment("to_east", 1, 4, 270, 0.2))
    west = apply_environment(original, ProtectionEnvironment("to_west", 1, 4, 90, 0.2))

    def weight(b):
        edges = b.request.exposure_edges
        return edges.weight[(edges.source == 0) & (edges.target == 1)][0]

    assert weight(east) > weight(west)
    assert not np.array_equal(
        east.request.exposure_edges.weight, west.request.exposure_edges.weight
    )


@pytest.mark.parametrize(
    "values",
    [
        (1, 2, 0, 0),
        (0, 0, 0, 0.2),
        (1.01, 0, 0, 0.2),
        (1, -1, 0, 0.2),
        (1, 2, 360, 0.2),
        (1, True, 0, 0.2),
        (1, 2, 0, float("nan")),
    ],
)
def test_bad_environment_rejected(values):
    with pytest.raises(ValueError):
        ProtectionEnvironment("bad", *values)


def test_zero_loss_partial_physical_time_cannot_certify():
    short = replace(record("a"), diagnostics={"reached_requested_horizon": False})
    with pytest.raises(ValueError, match="physically truncated"):
        ProtectionScreen(("a",), (short,))


def test_failure_stopping_preserves_first_ignition_and_default_full_run():
    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "early_stop_fixture", root / "tests/planning/test_village_evaluation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.make_engine(("grass", "building", "grass"))
    plan = module.ClearingPlan("none")
    sc = module.scenario(engine, end=600)
    sc = replace(
        sc, ignitions=(*sc.ignitions, module.Ignition(engine.bundle.request.grid.center(0, 1), 120))
    )
    full = engine.evaluate(sc, plan)
    early = engine.evaluate(sc, plan, stop_at_first_structure_ignition=True)
    first = full.metrics["building_first_ignitions"][0]["time_s"]
    assert early.diagnostics["simulated_until_s"] == first
    assert not early.diagnostics["reached_requested_horizon"]
    assert early.metrics["ignited_house_count"] == 1
    assert early.evaluation_sha256 != full.evaluation_sha256
    assert full.diagnostics["simulated_until_s"] == 600
    assert full.diagnostics["reached_requested_horizon"]
    assert early.metrics["building_first_ignitions"] == full.metrics["building_first_ignitions"]
    # Safe runs cannot stop early; final physical outcomes remain identical.
    safe = module.make_engine(("grass",) * 3)
    a = safe.evaluate(module.scenario(safe), plan)
    b = safe.evaluate(module.scenario(safe), plan, stop_at_first_structure_ignition=True)
    assert b.diagnostics["reached_requested_horizon"]
    assert a.metrics == b.metrics
