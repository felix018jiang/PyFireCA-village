"""Automatic moisture selection, explicit overrides and replay-visible initial state."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from pyfireca.cli import main
from pyfireca.village.moisture import default_moisture_payload, load_default_moisture_initialization
from pyfireca.village.outputs_v3 import write_v3_run_outputs
from pyfireca.village.resolution_workflow_v3 import resolve_survey_v3
from pyfireca.village.solver_v3 import run_v3_simulation

ROOT = Path(__file__).parents[2]
DIRECTORY = ROOT / "examples/village/material_cards"
spec = importlib.util.spec_from_file_location(
    "moisture_runtime_fixture", ROOT / "examples/village/vegetation_v3/audit_spread.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _inputs(tmp_path):
    survey = yaml.safe_load((DIRECTORY / "survey.example.yml").read_text())
    survey["records"] = survey["records"][:1]
    survey["records"][0]["quantity_band"] = "standard"
    path = tmp_path / "survey.yml"
    path.write_text(yaml.safe_dump(survey))
    areas = tmp_path / "areas.yml"
    areas.write_text(
        yaml.safe_dump(
            {
                "schema_version": "village-geometry-areas-1",
                "source": "test",
                "areas": {survey["records"][0]["geometry_ref"]: {"area_m2": 10}},
            }
        )
    )
    return path, areas


def _kwargs(areas):
    return {
        "material_cards_path": DIRECTORY / "catalog.yml",
        "assemblies_path": DIRECTORY / "assemblies.synthetic.yml",
        "geometry_areas_path": areas,
    }


def test_defaults_are_packaged_and_match_explicit_existing_scenario_from_any_cwd(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert default_moisture_payload() == yaml.safe_load(
        (DIRECTORY / "moisture.initial.synthetic.yml").read_text()
    )
    assert (
        load_default_moisture_initialization()
        .by_material_class["wood_unspecified"]
        .moisture_content_dry_basis.value
        == 0.1
    )
    survey, areas = _inputs(tmp_path)
    automatic = resolve_survey_v3(survey, **_kwargs(areas), output=tmp_path / "automatic.json")
    explicit = resolve_survey_v3(
        survey, **_kwargs(areas), moisture_path=DIRECTORY / "moisture.initial.synthetic.yml"
    )
    assert automatic.can_run and automatic.as_dict() == explicit.as_dict()
    assert automatic.records[0].water_mass_kg == pytest.approx(15)
    saved = json.loads((tmp_path / "automatic.json").read_text())
    assert saved["moisture_selection"]["source"] == "builtin_material_defaults"
    assert not saved["moisture_selection"]["production_ready"]
    assert saved["moisture_selection"]["resolved_configuration"] == default_moisture_payload()


def test_cli_no_longer_requires_water_file(tmp_path):
    survey, areas = _inputs(tmp_path)
    out = tmp_path / "resolved.json"
    assert (
        main(
            [
                "village",
                "resolve-survey-v3",
                str(survey),
                "--material-cards",
                str(DIRECTORY / "catalog.yml"),
                "--assemblies",
                str(DIRECTORY / "assemblies.synthetic.yml"),
                "--geometry-areas",
                str(areas),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    assert json.loads(out.read_text())["resolution_status"] == "ready"


def test_explicit_water_override_retains_measured_source_and_does_not_change_fuel(tmp_path):
    survey, areas = _inputs(tmp_path)
    raw = default_moisture_payload()
    state = raw["states"][0]
    state["moisture_content_dry_basis"] = {
        "value": 0.4,
        "unit": "kg/kg_dry",
        "provenance": "test:measured",
    }
    state["status"] = "field_measured"
    path = tmp_path / "measured.yml"
    path.write_text(yaml.safe_dump(raw))
    explicit = resolve_survey_v3(
        survey, **_kwargs(areas), moisture_path=path, output=tmp_path / "out.json"
    )
    assert explicit.records[0].dry_fuel_mass_kg == pytest.approx(150)
    assert explicit.records[0].water_mass_kg == pytest.approx(60)
    saved = json.loads((tmp_path / "out.json").read_text())
    assert saved["moisture_selection"]["source"] == "explicit_file"
    assert saved["moisture_selection"]["resolved_configuration"] == raw


def test_defaults_do_not_become_field_evidence_or_resolve_unknown_identity(tmp_path):
    survey, areas = _inputs(tmp_path)
    checked = resolve_survey_v3(survey, **_kwargs(areas), mode="evidence_checked")
    assert not checked.can_run
    assert any(issue.code == "assumed_moisture_in_evidence_mode" for issue in checked.issues)
    raw = yaml.safe_load(survey.read_text())
    raw["records"][0]["material_class"] = "unknown"
    survey.write_text(yaml.safe_dump(raw))
    unresolved = resolve_survey_v3(survey, **_kwargs(areas))
    assert not unresolved.can_run
    assert unresolved.records[0].moisture_content_dry_basis is None


@pytest.mark.parametrize("chunks", [None, 2])
def test_viewer_exports_initial_moisture_separately_from_evaporating_water(tmp_path, chunks):
    request = audit.make_request(("grass", "tree_equivalent", "bare_or_road"), end_time_s=20)
    result = run_v3_simulation(request)
    out = write_v3_run_outputs(result, request, tmp_path / "run", chunk_frames=chunks)
    manifest = json.loads((out / "viewer/manifest.json").read_text())
    assert manifest["initial_moisture"]["basis"] == "dry_basis_ratio"
    mc = np.fromfile(out / "viewer/initial_moisture.bin", dtype="<f4")
    assert mc[:2] == pytest.approx([0.3, 0.6375])
    assert np.isnan(mc[2])
    assert len(mc) == request.domain_mask.size
