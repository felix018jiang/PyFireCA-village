"""Confirm that uninterrupted and chunked terminal runs share the V3 physics."""

from pathlib import Path
from shutil import which

import numpy as np
import pytest
from test_wind_strategy_native_equivalence import source, windy_joint_case  # noqa: F401

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation


@pytest.mark.skipif(which("c++") is None, reason="compiled sparse clock requires a C++ compiler")
def test_compiled_clock_matches_native_states_budgets_and_extinction(request, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from heritage_wind_terminal_engine import TerminalWindClock

    runner, bundle, transport, inventory, (_, grass_cell) = request.getfixturevalue(
        "windy_joint_case"
    )
    cores = [t.building_id for t in inventory.targets]
    domain, layout, ids = runner.prepare_domain(
        bundle, inventory, source(inventory, "S"), cores, 100
    )
    graph = runner.WindRadiationGeometry(layout, ids, 0.5).graph(transport)
    plan = {"demolished_ids": ["M"], "cleared_physical_cell_ids": [grass_cell]}
    horizon = 4000.0
    base, keep, mapping, _ = runner.retained_inputs(domain, layout, plan, horizon)
    req, contact = runner.resolve_operators(
        keep, mapping, base, graph, runner.refined_contact_operator(layout)
    )
    native_peak = np.full(req.grid.shape, req.scenario.ambient_temperature_K)

    def extinction(state):
        np.maximum(
            native_peak,
            state.surface_temperature_K,
            where=state.temperature_valid_mask,
            out=native_peak,
        )
        return not np.any(state.state == 3)

    reference = _run_resolved_v3_simulation(
        req, contact=contact, stop_when=extinction, retain_snapshots=False
    )
    clock = TerminalWindClock(req, contact)
    assert clock.advance(31.63, exact=False) > 31.63
    assert clock.advance(149.17, exact=False) > 149.17
    clock.advance(horizon)
    result = clock.result()
    np.testing.assert_allclose(clock.peak_K, native_peak.ravel(), rtol=2e-10, atol=1e-7)
    for name in reference.final_state.__dataclass_fields__:
        np.testing.assert_allclose(
            getattr(result.final_state, name),
            getattr(reference.final_state, name),
            rtol=2e-10,
            atol=1e-7,
            err_msg=name,
            equal_nan=True,
        )
    np.testing.assert_array_equal(result.ignition_cause, reference.ignition_cause)
    for name in reference.energy_budget.__dataclass_fields__:
        np.testing.assert_allclose(
            getattr(result.energy_budget, name),
            getattr(reference.energy_budget, name),
            rtol=2e-10,
            atol=1e-6,
            err_msg=name,
        )
    assert result.final_state.time_s < horizon
    assert check_final(req, result)["burning_cells"] == 0
    assert certify_extinction_v3(
        req, result.final_state, post_apply_events_boundary=True, contact=contact
    )["closed"]
