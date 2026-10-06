"""The user's basic simulator contract, using existing material defaults."""

import importlib.util
from pathlib import Path

import pytest

from pyfireca.village.solver_v3 import run_v3_simulation

path = Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_core.py"
spec = importlib.util.spec_from_file_location("core_acceptance_fixture", path)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.mark.parametrize("name", list(core.CASES))
def test_basic_fire_ca_contract(name):
    request = core.make_core_request(name)
    result = run_v3_simulation(request)
    report = core.check_core_result(name, request, result)
    assert report["ignition_time_s"][0] == 0
    assert request.flame_contact is not None
    assert request.parameters.moisture_content_dry_basis.shape == request.grid.shape
