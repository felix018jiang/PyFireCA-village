"""A completed screen is reusable; a failed prefix never becomes a full run."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPlan

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from reuse_completed_batuan_runs import complete_from_screen  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "reuse_fixture", ROOT / "tests/planning/test_joint_corridors.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_reused_complete_record_matches_direct_run_and_rejects_prefix():
    e = fixture.engine()
    s = EvaluationScenario("short", (Ignition(e.bundle.request.grid.center(0, 0)),), 5)
    p = ClearingPlan("none", zone_id="joint")
    screen = e.evaluate(s, p, stop_at_first_structure_ignition=True)
    direct = e.evaluate(s, p, stop_at_first_structure_ignition=False)
    reused = complete_from_screen(e, s, p, screen)
    assert reused.evaluation_sha256 == direct.evaluation_sha256
    assert reused.metrics == direct.metrics
    assert reused.intervention == direct.intervention
    assert reused.diagnostics["simulated_until_s"] == direct.diagnostics["simulated_until_s"]
    assert reused.diagnostics["physical_run_reused"]
    incomplete = replace(
        screen, diagnostics={**screen.diagnostics, "reached_requested_horizon": False}
    )
    with pytest.raises(ValueError, match="physically complete"):
        complete_from_screen(e, s, p, incomplete)
    with pytest.raises(ValueError, match="identity"):
        complete_from_screen(e, replace(s, scenario_id="other"), p, screen)

    rounded = replace(screen, diagnostics={**screen.diagnostics, "simulated_until_s": 5 - 1e-12})
    assert complete_from_screen(e, s, p, rounded).diagnostics["physical_run_reused"]
    short = replace(screen, diagnostics={**screen.diagnostics, "simulated_until_s": 5 - 1e-4})
    with pytest.raises(ValueError, match="physically complete"):
        complete_from_screen(e, s, p, short)
