"""Local ignition selection preserves the component model and configuration."""

import http.client
import http.server
import importlib.util
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.component_curves_v3 import load_component_curve_configuration
from pyfireca.village.component_outputs_v3 import write_component_viewer
from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.interactive_components_v3 import (
    load_component_interactive_context,
    run_component_selection,
)
from pyfireca.village.interactive_v3 import InteractiveV3State, _handler

DIRECTORY = Path(__file__).parents[2] / "examples/village/vegetation_v3"
spec = importlib.util.spec_from_file_location(
    "interactive_component_fixture", DIRECTORY / "audit_spread.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def initial(tmp_path):
    resolved = resolve_components(
        audit.make_request(
            ("grass", "tree_equivalent", "building", "bare_or_road"), end_time_s=120
        ),
        load_component_configuration(DIRECTORY / "components.synthetic.yml"),
        contact=load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml"),
        internal_radiation=load_internal_radiation_parameters(
            DIRECTORY / "internal-radiation.synthetic.yml"
        ),
        curves=load_component_curve_configuration(DIRECTORY / "component-curves.synthetic.yml"),
    )
    result = run_component_simulation(resolved, retain_snapshots=True)
    root = write_component_run(resolved, result, tmp_path / "initial")
    viewer = write_component_viewer(
        resolved,
        result,
        root,
        material_labels={0: "草地"},
        scenario_metadata={"scenario_id": "component-api", "crop_rows": [0, 1]},
    )
    # A supplied original polygon ID must survive the self-contained spatial copy.
    (viewer / "building_cells.json").write_text(json.dumps({"buildings": {"B-real": [2]}}))
    return root


def test_selection_preserves_configuration_mass_labels_and_building_identity(initial, tmp_path):
    context = load_component_interactive_context(initial)
    output = run_component_selection(context, tmp_path / "selected", row=0, col=2, end_time_s=120)
    before = json.loads((initial / "manifest.json").read_text())
    after = json.loads((output / "manifest.json").read_text())
    for key in (
        "component_configuration",
        "component_curves",
        "flame_contact",
        "internal_radiation",
        "receiver_surfaces",
        "finite_radiation",
    ):
        assert before[key] == after[key]
    manifest = json.loads((output / "viewer/manifest.json").read_text())
    assert manifest["model"] == "component_effective_exposure_ca_v3"
    assert manifest["material"]["labels"]["0"] == "草地"
    assert manifest["scenario"]["crop_rows"] == [0, 1]
    assert manifest["scenario"]["ignition_events"] == [{"row": 0, "col": 2, "time_s": 0}]
    assert manifest["frame_count"] <= 101
    assert (output / "viewer/building_cells.json").read_bytes() == context.building_cells_bytes
    np.testing.assert_array_equal(
        np.fromfile(initial / "viewer/component_initial_fuel.bin", dtype="<f8"),
        np.fromfile(output / "viewer/component_initial_fuel.bin", dtype="<f8"),
    )


@pytest.mark.parametrize(
    "filename",
    [
        "component-curves.resolved.yml",
        "flame-contact.resolved.yml",
        "internal-radiation.resolved.yml",
    ],
)
def test_missing_config_fails_without_silent_model_change(initial, filename):
    (initial / filename).unlink()
    with pytest.raises(FileNotFoundError):
        load_component_interactive_context(initial)


def test_changed_config_and_resource_limits_rejected(initial, tmp_path):
    with pytest.raises(ValueError, match="RESOURCE_LIMIT"):
        load_component_interactive_context(initial, max_nodes=1)
    context = load_component_interactive_context(initial)
    for row, col, end in ((0, 3, 60), (0, 0, float("nan")), (0, 0, True), (-1, 0, 60)):
        with pytest.raises(ValueError):
            run_component_selection(context, tmp_path / "invalid", row=row, col=col, end_time_s=end)
    assert not (tmp_path / "invalid").exists()
    config = initial / "components.resolved.yml"
    config.write_text(
        config.read_text().replace("synthetic:component_partition", "synthetic:altered_partition")
    )
    with pytest.raises(ValueError, match="match run"):
        load_component_interactive_context(initial)


def test_http_submission_returns_component_replay_and_model_capability(initial, tmp_path):
    context = load_component_interactive_context(initial)
    request = context.bundle.request
    state = InteractiveV3State(
        initial / "base-case",
        initial / "viewer",
        tmp_path / "jobs",
        request.burnable_mask,
        asdict(request.grid),
        asdict(request.scenario.ignitions[0]),
        120,
        component_context=context,
        model="component_effective_exposure_ca_v3",
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)

    def get(path):
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        status, config = get("/api/config")
        assert status == 200 and config["component_ignition_selection"]
        assert config["model"] == "component_effective_exposure_ca_v3"
        connection.request(
            "POST",
            "/api/run",
            json.dumps({"row": 0, "col": 1, "end_time_s": 60}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        job = json.loads(response.read())
        assert response.status == 202
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, result = get(f"/api/jobs/{job['job_id']}")
            if result["status"] != "running":
                break
            time.sleep(0.02)
        assert result["status"] == "completed", result
        status, viewer = get(result["url"] + "manifest.json")
        assert status == 200 and viewer["schema_version"] == "village-component-viewer-1"
        assert viewer["scenario"]["ignition_events"] == [{"row": 0, "col": 1, "time_s": 0}]
        connection.request("POST", "/api/run", json.dumps({"row": 0, "col": 3, "end_time_s": 60}))
        response = connection.getresponse()
        response.read()
        assert response.status == 400
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_complete_finite_graph_and_receiver_surfaces_survive_selection(initial, tmp_path):
    from pyfireca.village.component_surfaces_v3 import load_receiver_surface_configuration
    from pyfireca.village.finite_radiation_v3 import load_finite_radiation_configuration

    original = load_component_interactive_context(initial)
    resolved = resolve_components(
        original.bundle.request,
        original.configuration,
        contact=original.options["contact"],
        curves=original.options["curves"],
        surfaces=load_receiver_surface_configuration(DIRECTORY / "receiver-surfaces.synthetic.yml"),
        finite_radiation=load_finite_radiation_configuration(
            DIRECTORY / "finite-radiation.synthetic.yml"
        ),
    )
    result = run_component_simulation(resolved, retain_snapshots=True)
    run = write_component_run(resolved, result, tmp_path / "finite")
    write_component_viewer(resolved, result, run)
    context = load_component_interactive_context(run)
    selected = run_component_selection(
        context, tmp_path / "finite-selected", row=0, col=1, end_time_s=30
    )
    before = json.loads((run / "manifest.json").read_text())
    after = json.loads((selected / "manifest.json").read_text())
    assert before["finite_radiation"] == after["finite_radiation"]
    assert before["receiver_surfaces"] == after["receiver_surfaces"]
    assert after["internal_radiation"] is None
    with (
        np.load(run / "finite_radiation.graph.npz") as a,
        np.load(selected / "finite_radiation.graph.npz") as b,
    ):
        for key in ("source", "target", "weight"):
            np.testing.assert_array_equal(a[key], b[key])
