"""Pair controls must preserve real source physics and separate initial ignition."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.planning.building_pairs import isolate_pair_request
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import V3CaseBundle

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "pair_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_pair_control_preserves_parameters_and_excludes_third_structure():
    req = fixture.make_request(("building", "grass", "building", "building"), end_time_s=30)
    req = replace(req, building_index=np.array([[10, -1, 20, 30]]))
    bundle = V3CaseBundle(req, {}, {}, {})
    before = request_fingerprint(req)
    params = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)
    pair = {"owner_ids": [10, 20]}
    a = isolate_pair_request(bundle, pair, 10, params, ground_enabled=False)
    b = isolate_pair_request(bundle, pair, 10, params, ground_enabled=True)
    assert a.burnable_mask.tolist() == [[True, False, True, False]]
    assert b.burnable_mask.tolist() == [[True, True, True, False]]
    assert a.scenario.ignitions[0].col == 0
    assert len(a.scenario.ignitions) == 1
    assert a.scenario.end_time_s == 5400
    assert np.any((a.exposure_edges.source == 0) & (a.exposure_edges.target == 2))
    for name in req.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(getattr(a.parameters, name), getattr(req.parameters, name))
    assert request_fingerprint(req) == before


def test_pair_reverse_ignition_and_time_step_are_explicit():
    req = fixture.make_request(("building", "grass", "building"), end_time_s=30)
    req = replace(req, building_index=np.array([[10, -1, 20]]))
    a = isolate_pair_request(
        V3CaseBundle(req, {}, {}, {}),
        {"owner_ids": [10, 20]},
        20,
        V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
        ground_enabled=False,
        dt_s=0.5,
    )
    assert a.scenario.ignitions[0].col == 2
    assert a.scenario.max_dt_s == 0.5
