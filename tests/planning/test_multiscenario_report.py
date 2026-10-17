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


def test_control_prefetch_does_not_overwrite_optimizer_screen(tmp_path, monkeypatch):
    import importlib

    from pyfireca.planning.evaluation import EvaluationResult

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    module = importlib.import_module("prefetch_batuan_multiscenario_controls")

    class FakeOracle:
        def __init__(self, *_args):
            self.protocol = {"training": [{"scenario_id": "a"}, {"scenario_id": "b"}]}

        def preflight(self, _plan):
            pass

        def evaluate(self, sid, plan, *, complete):
            assert complete
            return EvaluationResult(
                "test",
                "case",
                sid,
                "ca",
                sid,
                plan.plan_id,
                5400,
                {
                    "ignited_house_count": 0,
                    "ignited_bridge_count": 0,
                    "burning_reached_domain_edge": False,
                },
                {},
                {"reached_requested_horizon": True},
                {},
            )

        def screen(self, *_args, **_kwargs):
            raise AssertionError("control prefetch must not touch search screening ledger")

    monkeypatch.setattr(module, "ScenarioOracle", FakeOracle)
    path = tmp_path / "screens/training/none.json"
    path.parent.mkdir(parents=True)
    path.write_text("unchanged-search-prefix")
    module.control_task(tmp_path, "control", {"plan_id": "none", "zone_id": "vegetation"})
    assert path.read_text() == "unchanged-search-prefix"
    output = json.loads((tmp_path / "final-evaluations/control-training.json").read_text())
    assert len(output["records"]) == 2
    assert output["screen"]["protection_goal_met"]


def test_candidate_prefetch_excludes_held_out_and_failed_plans(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    module = importlib.import_module("prefetch_batuan_design_candidates")
    directory = tmp_path / "evaluation-cache"
    directory.mkdir()

    def save(plan, sid, area, bridge=0):
        record = {
            "scenario_id": sid,
            "intervention_id": plan,
            "diagnostics": {
                "stop_at_first_structure_ignition": True,
                "reached_requested_horizon": not bridge,
            },
            "metrics": {"ignited_house_count": 0, "ignited_bridge_count": bridge},
            "intervention": {"plan_id": plan, "zone_id": "z", "cleared_area_m2": area},
        }
        (directory / (plan + sid + ".json")).write_text(
            json.dumps({"record": record, "record_sha256": module.digest(record)})
        )

    save("certified", "a", 10)
    save("certified", "b", 10)
    save("promising", "a", 5)
    save("failed", "a", 3, bridge=1)
    save("heldout", "unseen", 1)
    save("too_large", "a", 20)
    result = module.pending(tmp_path, ["a", "b"])
    assert len(result) == 1
    assert result[0][2]["plan_id"] == "promising"
    assert result[0][3] == ["b"]


def test_replay_export_serializes_same_plan(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import validate_batuan_multiscenario as validator

    active = 0
    maximum = 0
    lock = threading.Lock()

    def export(*_args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return "checked"

    monkeypatch.setattr(validator, "_replay_task", export)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(validator.replay_task, tmp_path, "outer_proxy", {}, "design")
            for _ in range(2)
        ]
        assert [task.result() for task in tasks] == ["checked", "checked"]
    assert maximum == 1
