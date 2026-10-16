"""The published table must expose bridge losses and retain frozen selection."""

import importlib
import json
from pathlib import Path

import pytest


def test_report_exposes_bridge_failure_and_checks_selection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    module = importlib.import_module("render_batuan_protection_report")
    chosen = {"baseline": {"plan_id": "none", "paths": []}}
    metric = {
        "metrics": {"ignited_house_count": 0, "ignited_bridge_count": 1},
        "intervention": {"cleared_area_m2": 0},
    }
    report = {
        "status": "completed",
        "chosen_plans": chosen,
        "selection_sha256": module.digest(chosen),
        "controls": {"baseline": [metric, metric]},
        "validation": {"baseline": [metric, metric]},
        "sensitivity": {"baseline": [metric] * 4},
        "full_domain_comparison": {},
        "searches": {},
        "protocol": {
            "configs": {"straight": {"max_evaluations": 96}},
            "sensitivity": {"rise_factor": 0.95},
        },
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "selection.json").write_text(json.dumps({"sha256": module.digest(chosen)}))
    module.render(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert 'class="fail">0 / 1</td>' in page
    assert "风雨桥" in page and "不反馈训练" in page
    assert "降低温升5%不等于引燃概率增加5%" in page
    report["chosen_plans"]["baseline"]["paths"] = ["changed after test"]
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="checksum"):
        module.render(tmp_path)
