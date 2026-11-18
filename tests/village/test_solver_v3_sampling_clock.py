"""A rounded tolerance addition must not turn a future sample into an overrun."""

import importlib.util
import inspect
import math
import struct
from dataclasses import fields, is_dataclass
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village import solver_v3 as solver
from pyfireca.village.ignition import VillageIgnitionEvent

SPEC = importlib.util.spec_from_file_location(
    "sampling_clock_native_fixture", Path(__file__).with_name("test_solver_v3.py")
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def legacy_solver():
    """Execute the same native kernel with only the former error guard restored."""
    source = inspect.getsource(solver._run_resolved_v3_simulation)
    added_guard = "            if state.time_s < due:\n                return\n"
    assert source.count(added_guard) == 1
    namespace = dict(solver.__dict__)
    exec(compile(source.replace(added_guard, ""), "<legacy-sample-guard>", "exec"), namespace)
    return namespace["_run_resolved_v3_simulation"]


def assert_bitwise_equal(left, right):
    assert type(left) is type(right)
    if isinstance(left, np.ndarray):
        assert left.dtype == right.dtype and left.shape == right.shape
        assert left.tobytes() == right.tobytes()
    elif is_dataclass(left):
        for field in fields(left):
            assert_bitwise_equal(getattr(left, field.name), getattr(right, field.name))
    elif isinstance(left, float):
        assert struct.pack("!d", left) == struct.pack("!d", right)
    elif isinstance(left, (list, tuple)):
        assert len(left) == len(right)
        for a, b in zip(left, right, strict=True):
            assert_bitwise_equal(a, b)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_bitwise_equal(left[key], right[key])
    else:
        assert left == right


def boundary_request():
    due, before = 43140.0, 43139.9999999999
    return FIXTURE._request(
        ("grass",),
        max_dt_s=due,
        end_time_s=due + 1,
        events=(VillageIgnitionEvent(before, 0, 0),),
        sample_interval_s=due,
    )


def test_real_one_cell_clock_reproduces_old_error_and_retains_every_exact_sample():
    request = boundary_request()
    due, before, tolerance = 43140.0, request.scenario.ignitions[0].time_s, 1e-10
    assert due - before == 1.0186340659856796e-10
    assert due - before > tolerance
    assert before + tolerance == due
    with pytest.raises(RuntimeError, match="stepped past an exact sample time"):
        legacy_solver()(request, contact=None)

    received = []

    class Sink:
        def append(self, snapshot):
            received.append(snapshot.time_s)

    result = solver.run_v3_simulation(request, snapshot_sink=Sink())
    assert result.final_state.time_s == 43141.0
    assert result.final_state.step_index == 3
    assert result.final_state.ignition_time_s[0, 0] == before
    assert received == [0.0, due, 43141.0]
    assert [snapshot.time_s for snapshot in result.snapshots] == received
    assert result.snapshots[1].time_s == due
    assert result.snapshots[1].fuel_remaining_kg[0, 0] <= result.initial_fuel_kg[0, 0]
    assert result.energy_budget.released_J == pytest.approx(
        result.consumed_fuel_kg * request.parameters.heat_of_combustion_J_kg_dry[0, 0]
    )


@pytest.mark.parametrize("case", ["sampling", "propagation", "delayed", "near", "none", "stop"])
def test_all_old_successful_native_trajectories_and_samples_are_bitwise_unchanged(case):
    keywords = {}
    if case == "sampling":
        request = FIXTURE._request(("grass",), max_dt_s=0.25, end_time_s=2.3, sample_interval_s=0.7)
    elif case == "propagation":
        request = FIXTURE._request(("grass", "shrub", "tree_equivalent"), edge_weight=0.08)
    elif case == "delayed":
        request = FIXTURE._request(
            ("grass", "grass", "shrub"),
            edge_weight=0.005,
            events=(VillageIgnitionEvent(0, 0, 0), VillageIgnitionEvent(5, 0, 2)),
        )
    elif case == "near":
        # Unlike the counterexample, this old path legitimately captured within
        # tolerance. Keep its existing state clock and samples exactly intact.
        due = 43140.0
        request = FIXTURE._request(
            ("grass",),
            max_dt_s=due,
            end_time_s=due + 1,
            sample_interval_s=due,
            events=(VillageIgnitionEvent(math.nextafter(due, -math.inf), 0, 0),),
        )
    elif case == "none":
        request = FIXTURE._request(("grass", "shrub"), edge_weight=0.08, sample_interval_s=None)
    else:
        request = FIXTURE._request(("grass", "shrub"), edge_weight=0.08)
        keywords["stop_when"] = lambda state: bool(np.isfinite(state.burnout_time_s).any())
    original = legacy_solver()(request, contact=None, **keywords)
    updated = solver._run_resolved_v3_simulation(request, contact=None, **keywords)
    assert_bitwise_equal(original, updated)


def test_genuine_sample_overrun_still_raises(monkeypatch):
    initialize = solver._initial_state

    def already_past_initial_sample(request):
        state = initialize(request)
        state.time_s = 1e-9
        return state

    monkeypatch.setattr(solver, "_initial_state", already_past_initial_sample)
    request = FIXTURE._request(("grass",))
    with pytest.raises(RuntimeError, match="stepped past an exact sample time"):
        solver.run_v3_simulation(request)
