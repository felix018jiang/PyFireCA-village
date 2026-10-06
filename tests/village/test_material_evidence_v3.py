"""Classification provenance travels with cases but cannot affect thermal physics."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

ROOT = Path(__file__).parents[2]


def fixture():
    spec = importlib.util.spec_from_file_location(
        "evidence_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_request(("grass", "building", "bare_or_road"), end_time_s=1)


def test_evidence_roundtrip_crop_replay_and_physics_invariance(tmp_path):
    original = fixture()
    raw = np.array([[1, 2, 0]], dtype=np.uint8)
    request = replace(original, material_evidence_index=raw)
    raw[:] = 255
    np.testing.assert_array_equal(request.material_evidence_index, [[1, 2, 0]])
    assert not request.material_evidence_index.flags.writeable
    baseline = run_v3_simulation(original)
    observed = run_v3_simulation(request)
    assert baseline.energy_budget == observed.energy_budget
    for name in baseline.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(baseline.final_state, name), getattr(observed.final_state, name)
        )
    case = write_v3_case_bundle(request, tmp_path / "case")
    loaded = load_v3_case_bundle(case).request
    np.testing.assert_array_equal(loaded.material_evidence_index, [[1, 2, 0]])
    _, run = run_v3_case_bundle(case, tmp_path / "run", chunk_frames=4)
    manifest = json.loads((run / "viewer/manifest.json").read_text())
    assert "未实测" in manifest["material_evidence"]["labels"]["2"]
    np.testing.assert_array_equal(
        np.fromfile(run / "viewer/material_evidence.bin", dtype=np.uint8), [1, 2, 0]
    )
    crop = crop_v3_case_bundle(
        case,
        tmp_path / "crop",
        row_start=0,
        row_stop=1,
        col_start=1,
        col_stop=3,
        end_time_s=1,
        sample_interval_s=1,
        ignition=VillageIgnitionEvent(0, 0, 0),
    )
    np.testing.assert_array_equal(
        load_v3_case_bundle(crop).request.material_evidence_index, [[2, 0]]
    )
    legacy = write_v3_case_bundle(original, tmp_path / "legacy")
    assert load_v3_case_bundle(legacy).request.material_evidence_index is None


@pytest.mark.parametrize(
    "value",
    [
        np.array([[42, 1, 2]], dtype=np.uint8),
        np.ones((2, 2), dtype=np.uint8),
        np.array([[0, 1, 2]], dtype=np.int32),
    ],
)
def test_bad_evidence_rejected(value):
    with pytest.raises(ValueError):
        replace(fixture(), material_evidence_index=value)


def test_slope_matrix_absolute_radiation_geometry_has_correct_distance():
    path = ROOT / "scripts/audit_receiver_matrix.py"
    spec = importlib.util.spec_from_file_location("receiver_matrix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = fixture()
    spec = importlib.util.spec_from_file_location(
        "pair_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
    )
    pair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pair)
    from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters

    contact = load_flame_contact_parameters(
        ROOT / "examples/village/vegetation_v3/flame-contact-low-building.synthetic.yml"
    )
    for angle in [-20, 0, 20]:
        request = module.make_pair(pair, ("grass", "building"), angle, 1, contact)
        edge = (request.exposure_edges.source == 0) & (request.exposure_edges.target == 1)
        dz = 2 * np.tan(np.deg2rad(angle))
        separation = (
            dz
            + request.parameters.receiver_height_m[0, 1]
            - request.parameters.source_height_m[0, 0] / 2
        )
        expected = request.parameters.receiver_area_m2[0, 1] / (
            4 * np.pi * (4 + separation**2 + 0.5**2)
        )
        assert request.exposure_edges.weight[edge][0] == pytest.approx(expected)
        assert request.parameters.source_height_m[0, 0] == raw.parameters.source_height_m[0, 0]
