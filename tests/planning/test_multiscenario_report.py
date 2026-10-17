"""Published comparisons expose bridge failures and cannot change selection."""

import importlib
import json
from pathlib import Path

import pytest


def test_report_distinguishes_design_and_held_out_bridge_failures(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    module = importlib.import_module("render_batuan_multiscenario_report")
    monkeypatch.setattr(
        module,
        "maps",
        lambda *_args: {
            label: label + ".jpg"
            for label in (
                "outer_proxy",
                "inner_proxy",
                "previous_outer_proxy",
                "previous_inner_proxy",
            )
        },
    )
    env = {"wind_speed_m_s": 2, "wind_from_deg": 135, "ignition_rise_factor": 0.95}
    protocol = {
        "environments": {"env": env},
        "training": [{"scenario_id": "t", "environment_id": "env", "parent_cell": [1, 2]}],
        "held_out": [{"scenario_id": "v", "environment_id": "env", "parent_cell": [3, 4]}],
    }
    selection = {"protocol_sha256": module.digest(protocol), "chosen": {}}
    labels = (
        "baseline",
        "previous_outer_proxy",
        "outer_proxy",
        "previous_inner_proxy",
        "inner_proxy",
    )
    report = {
        "selection_sha256": module.digest(selection),
        "plans": {label: {} for label in labels},
        "evaluations": {},
    }
    for label in labels:
        report["evaluations"][label] = {}
        for suite, sid in (("training", "t"), ("held_out", "v")):
            record = {
                "scenario_id": sid,
                "intervention": {"cleared_area_m2": 50},
                "metrics": {
                    "ignited_house_count": 0,
                    "ignited_bridge_count": int(label == "baseline"),
                },
            }
            report["evaluations"][label][suite] = {
                "records": [record],
                "screen": {"expected_scenario_ids": [sid]},
            }
    for name, data in (
        ("protocol.json", protocol),
        ("selection.json", selection),
        ("report.json", report),
    ):
        (tmp_path / name).write_text(json.dumps(data))
    module.render(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert '<td class="fail">0 / 1</td>' in page
    assert '<td class="pass">0 / 0</td>' in page
    assert "8项未见情景" in page and "不用于重选" in page
    selection["chosen"]["outer_proxy"] = {"changed": True}
    (tmp_path / "selection.json").write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="checksum"):
        module.render(tmp_path)
