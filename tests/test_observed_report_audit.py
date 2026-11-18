"""Report audits must reject corrupted physical evidence even with a valid checksum."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def auditor(monkeypatch):
    folder = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(
        "observed_report_audit", folder / "render_batuan_observed_joint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence():
    plan = {"plan_id": "fixed", "zone_id": "joint", "components": []}
    protocol = {"horizon_s": 5400, "full_observation": {"observation_sha256": "geometry"}}
    declared = {"scenario_id": "external", "source_family": "external"}
    item = {
        **declared,
        "assessment": {"source_family": "external", "source_building_ids": []},
        "record": {
            "scenario_id": "external",
            "intervention_id": "fixed",
            "intervention": deepcopy(plan),
            "horizon_s": 5400,
            "diagnostics": {
                "simulated_until_s": 5400,
                "reached_requested_horizon": True,
                "energy_tolerance_J": 1,
                "energy_closure_errors_J": {"release": -0.1},
            },
            "metrics": {
                "burning_reached_domain_edge": False,
                "object_observation": {
                    "object_count": 333,
                    "observation_sha256": "geometry",
                    "unrepresented_labels": [],
                    "partly_unrepresented": [],
                    "fuel_duplicated": False,
                    "subcell_independent_temperatures": False,
                },
            },
        },
    }
    return item, declared, plan, protocol


def test_confirmation_matches_cache_and_signed_budget(auditor):
    item, declared, plan, protocol = evidence()
    auditor.validate_confirmation_record(
        item, declared, plan, protocol, {auditor.digest(item["record"])}
    )
    with pytest.raises(AssertionError, match="full-cache"):
        auditor.validate_confirmation_record(item, declared, plan, protocol, set())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("diagnostics", "energy_closure_errors_J"), {"release": -2}),
        (("diagnostics", "energy_closure_errors_J"), {"release": float("nan")}),
        (("diagnostics", "energy_closure_errors_J"), {}),
        (("diagnostics", "energy_tolerance_J"), float("inf")),
        (("diagnostics", "simulated_until_s"), float("nan")),
        (("diagnostics", "simulated_until_s"), 5399),
        (("horizon_s",), 5401),
        (("scenario_id",), "held-instead"),
        (("intervention", "zone_id"), "changed"),
        (("metrics", "burning_reached_domain_edge"), None),
        (("metrics", "object_observation", "observation_sha256"), "changed"),
        (("metrics", "object_observation", "fuel_duplicated"), True),
    ],
)
def test_checksum_valid_but_invalid_evidence_is_rejected(auditor, path, value):
    item, declared, plan, protocol = evidence()
    target = item["record"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(AssertionError):
        auditor.validate_confirmation_record(
            item, declared, plan, protocol, {auditor.digest(item["record"])}
        )


def test_jointly_mislabelled_source_family_is_rejected(auditor):
    item, declared, plan, protocol = evidence()
    item["source_family"] = item["assessment"]["source_family"] = "internal"
    with pytest.raises(AssertionError, match="identity"):
        auditor.validate_confirmation_record(
            item, declared, plan, protocol, {auditor.digest(item["record"])}
        )


def test_internal_probe_excludes_only_declared_source(auditor):
    item, declared, plan, protocol = evidence()
    declared.update(source_family="internal", source_owner=162)
    item["source_family"] = item["assessment"]["source_family"] = "internal"
    item["assessment"]["source_building_ids"] = [162]
    auditor.validate_confirmation_record(
        item, declared, plan, protocol, {auditor.digest(item["record"])}
    )
    item["assessment"]["source_building_ids"].append(163)
    with pytest.raises(AssertionError, match="source exclusion"):
        auditor.validate_confirmation_record(
            item, declared, plan, protocol, {auditor.digest(item["record"])}
        )


def test_generation_progress_does_not_reset_query_count(auditor, tmp_path):
    (tmp_path / "protocol.json").write_text(json.dumps({"candidate_budget_per_mode": 32}))
    (tmp_path / "outer_proxy-progress.json").write_text(json.dumps({"event": "generation"}))
    (tmp_path / "pipeline.log").write_text(
        '{"mode":"outer_proxy","query":29}\n{"mode":"outer_proxy","query":null}\npartial log line'
    )
    auditor.render(tmp_path)
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["search"][0]["queries"] == 29
    assert state["stage"] == "running"
    assert not (tmp_path / "audit.json").exists()
