"""Basic replay compatibility, short reruns and recovery after job failure."""

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

from pyfireca.village import interactive_v3
from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle


@pytest.fixture
def state(tmp_path):
    path = Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py"
    spec = importlib.util.spec_from_file_location("interactive_core_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    request = fixture.make_request(("grass", "bare_or_road"), end_time_s=1)
    case = write_v3_case_bundle(request, tmp_path / "case")
    _, run = run_v3_case_bundle(case, tmp_path / "initial")
    return interactive_v3.InteractiveV3State(
        case,
        run / "viewer",
        tmp_path / "jobs",
        request.burnable_mask,
        asdict(request.grid),
        asdict(request.scenario.ignitions[0]),
        1,
    )


def test_http_legacy_module_short_run_and_invalid_submissions(state):
    # Emulate a saved viewer from before the shared statistics module existed.
    (state.initial_viewer / "assets/core-metrics.mjs").unlink()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), interactive_v3._handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)

    def fetch(method, path, payload=None):
        body = None if payload is None else json.dumps(payload)
        connection.request(method, path, body)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")

    try:
        status, content, mime = fetch("GET", "/assets/core-metrics.mjs")
        assert status == 200 and "text/javascript" in mime
        assert b"eventOccurredBy" in content
        for payload in (
            {"row": 0, "col": 1, "end_time_s": 1},  # Inert target.
            {"row": True, "col": 0, "end_time_s": 1},
            {"row": 0, "col": 0, "end_time_s": 0},
            {"row": 0, "col": 0, "end_time_s": 1, "moisture": 0},
        ):
            assert fetch("POST", "/api/run", payload)[0] == 400
        assert not state.jobs
        status, body, _ = fetch("POST", "/api/run", {"row": 0, "col": 0, "end_time_s": 1})
        assert status == 202
        job = json.loads(body)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, body, _ = fetch("GET", f"/api/jobs/{job['job_id']}")
            result = json.loads(body)
            if result["status"] != "running":
                break
            time.sleep(0.02)
        assert result["status"] == "completed", result
        status, body, _ = fetch("GET", result["url"] + "manifest.json")
        manifest = json.loads(body)
        assert status == 200 and manifest["sample_times_s"] == [0, 1]
        assert manifest["model"] == "effective_exposure_ca_v3"
        assert manifest["initial_fuel"]["unit"] == "kg"
        assert fetch("GET", result["url"] + "assets/core-metrics.mjs")[0] == 200
        assert fetch("GET", "/api/jobs/missing")[0] == 404
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_failed_job_releases_run_slot_and_next_run_preserves_original_arrays(state, monkeypatch):
    original = interactive_v3.run_v3_case_bundle

    def fail(*args, **kwargs):
        raise RuntimeError("injected output failure")

    monkeypatch.setattr(interactive_v3, "run_v3_case_bundle", fail)
    state.active_job = "failed"
    interactive_v3._run_job(state, "failed", 0, 0, 1)
    assert state.active_job is None
    assert state.jobs["failed"]["status"] == "failed"
    monkeypatch.setattr(interactive_v3, "run_v3_case_bundle", original)
    state.active_job = "recovered"
    interactive_v3._run_job(state, "recovered", 0, 0, 1)
    assert state.active_job is None
    assert state.jobs["recovered"]["status"] == "completed"
    for array in state.case_dir.glob("*.npy"):
        np.testing.assert_array_equal(
            np.load(array), np.load(state.output_root / "recovered/case" / array.name)
        )


def test_rerun_inherits_case_flame_contact_without_server_override(state, tmp_path):
    from dataclasses import replace

    from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    config = (
        Path(__file__).parents[2]
        / "examples/village/vegetation_v3/flame-contact-batuan-slope-5m.synthetic.yml"
    )
    contact = load_flame_contact_parameters(config)
    old = load_v3_case_bundle(state.case_dir)
    state.case_dir = write_v3_case_bundle(
        replace(old.request, flame_contact=contact), tmp_path / "enabled-case"
    )
    assert state.flame_contact is None  # The server must retain the bundle's configuration.
    interactive_v3._run_job(state, "contact-enabled", 0, 0, 1)
    assert state.jobs["contact-enabled"]["status"] == "completed"
    saved = load_v3_case_bundle(state.output_root / "contact-enabled/case")
    assert saved.request.flame_contact == contact
    manifest = json.loads(
        (state.output_root / "contact-enabled/run/viewer/manifest.json").read_text()
    )
    assert manifest["flame_contact"]["heat_transfer_coefficient_W_m2_K"] == 50
    assert manifest["flame_contact"]["terrain_reference"] == "shared_face"


def test_progress_callback_does_not_change_solver_results(state):
    from pyfireca.village.solver_v3 import run_v3_simulation
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    request = load_v3_case_bundle(state.case_dir).request
    baseline = run_v3_simulation(request)
    events = []
    observed = run_v3_simulation(request, progress_callback=lambda t, end: events.append((t, end)))
    assert events[0] == (0, 1) and events[-1] == (1, 1)
    assert observed.energy_budget == baseline.energy_budget
    for field in baseline.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(observed.final_state, field), getattr(baseline.final_state, field)
        )


def test_saved_runs_survive_restart_and_ignore_partial_or_other_maps(state):
    import shutil

    job_id = "a" * 32
    interactive_v3._run_job(state, job_id, 0, 0, 1)
    state.jobs.clear()  # Emulate a fresh server process retaining only disk results.
    runs = interactive_v3._saved_runs(state)
    assert [run["id"] for run in runs] == ["initial", job_id]
    assert runs[1]["url"] == f"/result/{job_id}/"
    assert (runs[1]["row"], runs[1]["col"], runs[1]["end_time_s"]) == (0, 0, 1)

    for prefix, mutation in (
        ("b", lambda manifest: manifest.update(run_status="running")),
        ("c", lambda manifest: manifest.update(model="different_model")),
    ):
        viewer = state.output_root / (prefix * 32) / "run/viewer"
        shutil.copytree(state.initial_viewer, viewer)
        path = viewer / "manifest.json"
        manifest = json.loads(path.read_text())
        mutation(manifest)
        path.write_text(json.dumps(manifest))
    wrong_grid = state.output_root / ("d" * 32) / "run/viewer"
    shutil.copytree(state.initial_viewer, wrong_grid)
    path = wrong_grid / "grid.json"
    grid = json.loads(path.read_text())
    grid["x_min_m"] += 100
    path.write_text(json.dumps(grid))
    corrupted = state.output_root / ("e" * 32) / "run/viewer"
    corrupted.mkdir(parents=True)
    (corrupted / "manifest.json").write_text("{broken")
    assert [run["id"] for run in interactive_v3._saved_runs(state)] == ["initial", job_id]
    state.jobs[job_id] = {"status": "running"}
    assert [run["id"] for run in interactive_v3._saved_runs(state)] == ["initial"]
    state.jobs.clear()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), interactive_v3._handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request("GET", "/api/runs")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["runs"][1]["id"] == job_id
        connection.request("GET", f"/result/{job_id}/manifest.json")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_material_draft_persists_undo_and_never_changes_solver_case(state):
    from pyfireca.village.material_draft_v3 import MaterialDraft

    before = {p.name: p.read_bytes() for p in state.case_dir.iterdir() if p.is_file()}
    draft = MaterialDraft(state.case_dir, state.output_root)
    original = int(draft.material[0, 1])
    target = int(draft.material[0, 0])
    proposal = dict(action="set", revision=0, row=0, col=1, material=target, reason="试验假设")
    first = draft.change(proposal)
    assert first["edits"][0]["before"] == original
    assert first["edits"][0]["after"] == target
    assert first["applied_to_simulation"] is False
    with pytest.raises(ValueError, match="刷新"):
        draft.change(proposal)
    restarted = MaterialDraft(state.case_dir, state.output_root)
    assert restarted.view() == first
    restored = restarted.change(dict(action="undo", revision=1))
    assert restored["revision"] == 2 and not restored["edits"]
    assert not restored["can_undo"]
    assert len(restored["events"]) == 2  # Undo preserves the full audit trail.
    assert before == {p.name: p.read_bytes() for p in state.case_dir.iterdir() if p.is_file()}
    for change in ({"row": True}, {"col": -1}, {"material": 999}, {"reason": " "}):
        with pytest.raises(ValueError):
            restarted.change({**proposal, "revision": 2, **change})
    assert restarted.view() == restored
    # A different baseline cannot inherit these proposals even in the same output root.
    np.save(state.case_dir / "material_index.npy", draft.material + 1)
    assert MaterialDraft(state.case_dir, state.output_root).view()["revision"] == 0


def test_material_draft_http_origin_running_job_and_save(state):
    from pyfireca.village.material_draft_v3 import MaterialDraft

    state.material_draft = MaterialDraft(state.case_dir, state.output_root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), interactive_v3._handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    payload = dict(
        action="set",
        revision=0,
        row=0,
        col=1,
        material=int(state.material_draft.material[0, 0]),
        reason="照片复核建议",
    )

    def post(headers=None):
        connection.request("POST", "/api/material-draft", json.dumps(payload), headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        assert post({"Origin": "https://example.com"})[0] == 403
        state.active_job = "running"
        assert post()[0] == 409
        state.active_job = None
        status, body = post()
        assert status == 200 and body["revision"] == 1
        assert post()[0] == 400
        connection.request("GET", "/api/material-draft")
        response = connection.getresponse()
        assert json.loads(response.read())["edits"] == body["edits"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_material_draft_application_rebuilds_graph_fuel_and_records_independent_case(state):
    from pyfireca.village.material_draft_v3 import MaterialDraft
    from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    state.material_draft = MaterialDraft(state.case_dir, state.output_root)
    state.material_transport = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)
    original = load_v3_case_bundle(state.case_dir).request
    draft = state.material_draft.change(
        dict(
            action="set",
            revision=0,
            row=0,
            col=1,
            material=int(original.material_index[0, 0]),
            reason="试验假设, 不是实测",
        )
    )
    state.jobs["corrected"] = {"status": "running"}
    state.active_job = "corrected"
    interactive_v3._run_job(state, "corrected", 0, 1, 1, draft)
    assert state.jobs["corrected"]["status"] == "completed", state.jobs["corrected"]
    corrected = load_v3_case_bundle(state.output_root / "corrected/case")
    request = corrected.request
    assert request.burnable_mask[0, 1] and request.source_mask[0, 1]
    assert (
        request.parameters.initial_dry_fuel_mass_kg[0, 1]
        == original.parameters.initial_dry_fuel_mass_kg[0, 0]
    )
    assert (
        request.parameters.moisture_content_dry_basis[0, 1]
        == original.parameters.moisture_content_dry_basis[0, 0]
    )
    assert request.material_evidence_index[0, 1] == 3
    assert np.any(request.exposure_edges.source == 1)  # New fuel must emit, not just change colour.
    assert corrected.metadata["material_corrections"]["applied_to_simulation"]
    assert corrected.metadata["material_corrections"]["events"][0]["reason"] == "试验假设, 不是实测"
    assert not load_v3_case_bundle(state.case_dir).request.burnable_mask[0, 1]
    viewer = json.loads((state.output_root / "corrected/run/viewer/manifest.json").read_text())
    assert viewer["scenario"]["material_corrections"]["applied_to_simulation"]
    assert "未实测" in viewer["material_evidence"]["labels"]["3"]


def test_material_correction_rejects_different_transport(state):
    from pyfireca.village.material_draft_v3 import apply_material_edits
    from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    request = load_v3_case_bundle(state.case_dir).request
    with pytest.raises(ValueError, match="传热连接"):
        apply_material_edits(request, [], V3RadiativeTransportParameters(8, 1.5, 0, 0, 0, 0))
