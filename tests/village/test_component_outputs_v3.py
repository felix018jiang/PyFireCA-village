"""Component replay preserves solver nodes on the original physical grid."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.component_outputs_v3 import (
    check_component_viewer_memory,
    write_component_viewer,
)
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
)
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.workflow_v3 import write_v3_case_bundle

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location("replay_audit_fixture", DIRECTORY / "audit_spread.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def resolved():
    request = audit.make_request(
        ("grass", "tree_equivalent", "building", "bare_or_road"), end_time_s=125
    )
    return resolve_components(
        request,
        load_component_configuration(DIRECTORY / "components.synthetic.yml"),
        contact=load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml"),
    )


def read_array(viewer, specification, dtype):
    if "chunks" in specification:
        return np.concatenate(
            [np.fromfile(viewer / chunk["path"], dtype=dtype) for chunk in specification["chunks"]]
        )
    return np.fromfile(viewer / specification["path"], dtype=dtype)


def test_replay_chunks_are_exact_solver_samples_and_grid_is_physical(resolved, tmp_path):
    result = run_component_simulation(resolved, retain_snapshots=True)
    viewer = write_component_viewer(resolved, result, tmp_path, chunk_frames=2)
    manifest = json.loads((viewer / "manifest.json").read_text())
    grid = json.loads((viewer / "grid.json").read_text())
    assert (grid["height"], grid["width"]) == (1, 4)
    assert manifest["components"]["node_count"] == 6
    assert manifest["sample_times_s"] == [0, 60, 120, 125]
    assert (viewer / "assets/component-view.mjs").is_file()
    for key, attribute, dtype in (
        ("state", "state", "u1"),
        ("temperature", "surface_temperature_K", "<f4"),
        ("temperature_valid", "temperature_valid_mask", "u1"),
        ("hrr", "interval_mean_hrr_W", "<f4"),
        ("surface_water", "heated_water_remaining_kg", "<f4"),
    ):
        expected = np.concatenate([getattr(s, attribute).ravel() for s in result.snapshots])
        np.testing.assert_array_equal(
            read_array(viewer, manifest[key], dtype), expected.astype(dtype)
        )
    node = manifest["components"]
    np.testing.assert_array_equal(read_array(viewer, node["cell_id"], "<i4"), [0, 1, 1, 2, 2, 3])
    moisture = read_array(viewer, node["initial_moisture"], "<f4")
    dry = read_array(viewer, node["initial_dry"], "<f8")
    assert moisture[1] == pytest.approx(0.15)
    assert np.average(moisture[1:3], weights=dry[1:3]) == pytest.approx(0.6375)
    physical = read_array(viewer, manifest["initial_moisture"], "<f4")
    assert physical[1] == pytest.approx(0.6375)
    assert np.isnan(physical[3])
    groups = json.loads((viewer / "building_cells.json").read_text())
    assert groups["identity_basis"] == "building_index_not_polygon_id"
    assert list(groups["buildings"].values()) == [[2]]


def test_missing_snapshots_and_bad_shapes_fail_before_writing(resolved, tmp_path):
    result = run_component_simulation(resolved)
    with pytest.raises(ValueError, match="sampled"):
        write_component_viewer(resolved, result, tmp_path)
    result = run_component_simulation(resolved, retain_snapshots=True)
    bad = replace(result.snapshots[0], surface_temperature_K=np.zeros((2, 3)))
    with pytest.raises(ValueError, match="indexing"):
        write_component_viewer(
            resolved, replace(result, snapshots=(bad, *result.snapshots[1:])), tmp_path
        )
    bad = replace(result.snapshots[1], time_s=0)
    with pytest.raises(ValueError, match="ordered"):
        write_component_viewer(
            resolved,
            replace(result, snapshots=(result.snapshots[0], bad, *result.snapshots[2:])),
            tmp_path,
        )
    assert not (tmp_path / "viewer").exists()


def test_excluded_fuel_cell_retains_unknown_state_without_requiring_moisture(tmp_path):
    request = audit.make_request(("grass", "tree_equivalent"), end_time_s=1)
    included = np.asarray([[True, False]])
    edges = request.exposure_edges
    keep = included.ravel()[edges.source] & included.ravel()[edges.target]
    request = replace(
        request,
        known_mask=included,
        burnable_mask=included,
        source_mask=included,
        target_mask=included,
        piloted_ignition_mask=included,
        exposure_edges=ExposureEdges(edges.source[keep], edges.target[keep], edges.weight[keep], 2),
        unknown_policy="exclude",
    )
    resolved = resolve_components(
        request, load_component_configuration(DIRECTORY / "components.synthetic.yml")
    )
    result = run_component_simulation(resolved, retain_snapshots=True)
    viewer = write_component_viewer(resolved, result, tmp_path)
    manifest = json.loads((viewer / "manifest.json").read_text())
    node = manifest["components"]

    # Masking a cell need not erase the original material's physical parameters.
    assert read_array(viewer, node["initial_dry"], "<f8")[1] == pytest.approx(32)
    np.testing.assert_array_equal(read_array(viewer, node["burnable"], "u1"), [1, 0])
    assert read_array(viewer, node["initial_fuel"], "<f8")[1] == 0
    assert np.isnan(read_array(viewer, node["initial_moisture"], "<f4")[1])
    states = read_array(viewer, manifest["state"], "u1").reshape(-1, node["node_count"])
    np.testing.assert_array_equal(states[:, 1], 254)


def test_mismatched_initial_fuel_is_rejected_before_creating_viewer(resolved, tmp_path):
    result = run_component_simulation(resolved, retain_snapshots=True)
    mismatched = replace(result, initial_fuel_kg=result.initial_fuel_kg * 2)
    with pytest.raises(ValueError, match="resolved initial fuel"):
        write_component_viewer(resolved, mismatched, tmp_path)
    assert not (tmp_path / "viewer").exists()


def test_replay_memory_gate_includes_physical_aggregation(resolved):
    assert check_component_viewer_memory(resolved)["frame_upper_bound"] == 4
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            check_component_viewer_memory(resolved, bad)
    dense = replace(
        resolved, physical_request=replace(resolved.physical_request, sample_interval_s=1e-5)
    )
    with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
        check_component_viewer_memory(dense, 1)


def test_cli_opt_in_replay_uses_labels_and_records_default_sample_interval(resolved, tmp_path):
    from pyfireca.cli import main

    request = replace(resolved.physical_request, sample_interval_s=None)
    case = write_v3_case_bundle(
        request,
        tmp_path / "case",
        material_labels={0: "test material"},
        metadata={"scenario_id": "replay-test"},
    )
    output = tmp_path / "run"
    assert (
        main(
            [
                "village",
                "run-components-v3",
                str(case),
                "--components-config",
                str(DIRECTORY / "components.synthetic.yml"),
                "--output",
                str(output),
                "--viewer",
                "--viewer-chunk-frames",
                "2",
            ]
        )
        == 0
    )
    manifest = json.loads((output / "viewer/manifest.json").read_text())
    assert manifest["scenario"]["scenario_id"] == "replay-test"
    assert manifest["scenario"]["sample_interval_s"] == 60
    assert manifest["material"]["labels"]["0"] == "test material"
