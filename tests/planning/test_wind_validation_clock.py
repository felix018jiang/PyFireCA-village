"""Cross-check the final sparse clock at transient and extinction boundaries."""

from pathlib import Path
from shutil import which

import numpy as np
import pytest
from test_wind_strategy_native_equivalence import source, windy_joint_case  # noqa: F401

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation


@pytest.mark.skipif(which("c++") is None, reason="C++ clock needs a compiler")
def test_validation_clock_transients_and_native_extinction(request, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from heritage_wind_terminal_engine import TerminalWindClock
    from heritage_wind_validation_fast import FastTerminalWindClock
    from verify_heritage_wind_validation_clock import physical_match

    runner, bundle, wind, inventory, (_, grass) = request.getfixturevalue("windy_joint_case")
    cores = [t.building_id for t in inventory.targets]
    domain, layout, ids = runner.prepare_domain(
        bundle, inventory, source(inventory, "S"), cores, 100
    )
    graph = runner.WindRadiationGeometry(layout, ids, 0.5).graph(wind)
    plan = {"demolished_ids": ["M"], "cleared_physical_cell_ids": [grass]}
    base, keep, mapping, _ = runner.retained_inputs(domain, layout, plan, 4000)
    req, contact = runner.resolve_operators(
        keep, mapping, base, graph, runner.refined_contact_operator(layout)
    )
    reference = TerminalWindClock(req, contact)
    candidate = FastTerminalWindClock(req, contact)
    for target in [31.63, 149.17, 221.42, 4000.0]:
        for clock in [reference, candidate]:
            clock.advance(target, exact=False)
        physical_match(reference.result(), candidate.result())
        np.testing.assert_array_equal(reference.peak_K, candidate.peak_K)
    out = candidate.result()
    assert check_final(req, out)["burning_cells"] == 0
    assert certify_extinction_v3(
        req, out.final_state, post_apply_events_boundary=True, contact=contact
    )["closed"]
    native = _run_resolved_v3_simulation(
        req,
        contact=contact,
        stop_when=lambda state: not np.any(state.state == 3),
        retain_snapshots=False,
    )
    for field in out.final_state.__dataclass_fields__:
        np.testing.assert_allclose(
            getattr(native.final_state, field),
            getattr(out.final_state, field),
            rtol=2e-10,
            atol=1e-7,
            equal_nan=True,
            err_msg=field,
        )
    np.testing.assert_array_equal(native.ignition_cause, out.ignition_cause)
    for field in out.energy_budget.__dataclass_fields__:
        np.testing.assert_allclose(
            getattr(native.energy_budget, field),
            getattr(out.energy_budget, field),
            rtol=2e-10,
            atol=1e-6,
            err_msg=field,
        )
