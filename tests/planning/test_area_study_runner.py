"""Validate the research runner's identity, complete-observation and budget gates."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, write_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "area_study_under_test", ROOT / "scripts/run_heritage_area_study.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config():
    return {
        "schema_version": "heritage-area-study-1",
        "max_new_checks": 4,
        "max_wall_time_s": 60,
        "minimum_free_disk_GB": 1,
        "jobs": [
            {
                "job_id": "village",
                "study_path": "study.json",
                "horizon_s": 3600,
                "max_dt_s": 1,
                "roof_scales_m": [1.0],
                "quadrature_spacing_m": 0.5,
                "max_nodes": 100,
                "max_radiation_edges": 100,
                "checkpoints_s": [1200, 3600],
                "source_fires": [
                    {"fire_id": "source", "protocol_path": "source.json", "draw_id": "one"}
                ],
                "plans": [
                    {"plan_id": "baseline", "demolished_ids": []},
                    {"plan_id": "selected", "demolished_ids": ["M"]},
                ],
            }
        ],
    }


def create_experiment(output, value):
    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "experiment.json",
        {
            "config": value,
            "jobs": [{"job_id": "village", "problem_sha256": "problem", "checks": 2}],
        },
    )


def completed_artifact(output, *, ids=(), status="passed", final_time=3600, invalid=False):
    folder = output / "village/evaluations" / content_hash([list(ids), "source-h1"])
    folder.mkdir(parents=True)
    plan = "selected" if ids else "baseline"
    write_json(
        folder / "check.json",
        {
            "scenario_id": "source-h1",
            "demolished_ids": list(ids),
            "status": status,
            "evidence": {"reached_horizon": final_time >= 3600, "simulated_until_s": final_time},
        },
    )
    write_json(
        folder / "evaluation.json",
        {
            "diagnostics": {"invalid_reasons": ["domain_edge"] if invalid else []},
            "intervention": {"cleared_roof_union_area_m2": 2 if ids else 0},
        },
    )
    write_json(
        folder / "curves.json",
        {
            "final_time_s": final_time,
            "checkpoints": [
                {"time_s": 3600, "valid_observation": final_time >= 3600 and not invalid}
            ],
        },
    )
    write_json(folder / "fire-loss.json", {"saved_final_time_s": final_time})
    np.savez_compressed(folder / "final-state.npz", time_s=final_time)
    np.savez_compressed(folder / "layout.npz", covered_area_m2=[1])
    np.savez_compressed(folder / "loss-basis.npz", baseline_covered_area_m2=[1])
    manifest = {
        "problem_sha256": "problem",
        "plan_id": plan,
        "scenario_id": "source-h1",
        "demolished_ids": list(ids),
        "files_sha256": {p.name: file_sha256(p) for p in folder.iterdir()},
    }
    write_json(folder / "complete.json", manifest)
    return folder


def rewrite_manifest(folder, **updates):
    manifest = json.loads((folder / "complete.json").read_text())
    manifest.update(updates)
    write_json(folder / "complete.json", manifest)


def test_cache_requires_complete_required_artifact_set(runner, tmp_path):
    folder = completed_artifact(tmp_path)
    manifest = json.loads((folder / "complete.json").read_text())
    manifest["files_sha256"].pop("final-state.npz")
    rewrite_manifest(folder, **manifest)
    with pytest.raises(ValueError, match="required artifact"):
        runner.read_complete(folder, "problem")


@pytest.mark.parametrize(
    ("field", "value"),
    [("plan_id", "other"), ("scenario_id", "other-h1"), ("demolished_ids", ["wrong"])],
)
def test_summary_rejects_wrong_declared_identity_even_with_same_physics(
    runner, tmp_path, field, value
):
    create_experiment(tmp_path, config())
    folder = completed_artifact(tmp_path)
    rewrite_manifest(folder, **{field: value})
    with pytest.raises(ValueError, match="identity"):
        runner.summarize(tmp_path)


@pytest.mark.parametrize(
    "artifact_kwargs",
    [{"status": "unknown"}, {"status": "failed", "final_time": 900}, {"invalid": True}],
)
def test_all_saved_files_do_not_claim_complete_valid_full_horizon(
    runner, tmp_path, artifact_kwargs
):
    create_experiment(tmp_path, config())
    completed_artifact(tmp_path, **artifact_kwargs)
    completed_artifact(tmp_path, ids=("M",))
    summary = runner.summarize(tmp_path)
    assert summary["completed_checks"] == 2
    assert not summary["complete"]
    assert summary["valid_completed_checks"] == 1


def test_failed_core_with_complete_valid_horizon_is_a_completed_observation(runner, tmp_path):
    create_experiment(tmp_path, config())
    completed_artifact(tmp_path, status="failed")
    completed_artifact(tmp_path, ids=("M",))
    summary = runner.summarize(tmp_path)
    assert summary["complete"]
    assert summary["valid_completed_checks"] == 2


def test_pending_duplicate_does_not_fill_a_missing_arm(runner, tmp_path):
    create_experiment(tmp_path, config())
    folder = completed_artifact(tmp_path)
    import shutil

    shutil.copytree(folder, folder.with_name(folder.name + ".pending"))
    summary = runner.summarize(tmp_path)
    assert summary["completed_checks"] == 1
    assert not summary["complete"]


@pytest.mark.parametrize(
    "change",
    [
        {"horizon_s": float("nan")},
        {"max_dt_s": float("inf")},
        {"checkpoints_s": [1200]},
        {"checkpoints_s": [3600, 1200]},
    ],
)
def test_config_requires_finite_numerics_and_a_final_horizon_checkpoint(runner, change):
    value = config()
    value["jobs"][0].update(change)
    with pytest.raises(ValueError):
        runner.validate_config(value)


@pytest.mark.parametrize("budget", [True, 1.5, float("nan")])
def test_check_budget_is_an_integer(runner, budget):
    value = config()
    value["max_new_checks"] = budget
    with pytest.raises(ValueError):
        runner.validate_config(value)


def resumable_environment(runner, monkeypatch, tmp_path, *, budget_limit, started=0):
    value = config()
    value["max_new_checks"] = budget_limit
    output = tmp_path / "output"
    create_experiment(output, value)
    config_path = tmp_path / "config.json"
    write_json(config_path, value)
    snapshot = output / "source-snapshot/scripts/run_heritage_area_study.py"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("frozen runner")
    monkeypatch.setattr(runner, "__file__", str(snapshot))
    monkeypatch.setattr(runner, "ROOT", output / "source-snapshot")
    monkeypatch.setattr(runner, "source_paths", lambda: [snapshot])
    experiment = json.loads((output / "experiment.json").read_text())
    experiment.update(
        config_sha256=file_sha256(config_path),
        references_sha256={},
        source_sha256={"scripts/run_heritage_area_study.py": file_sha256(snapshot)},
    )
    write_json(output / "experiment.json", experiment)
    write_json(
        output / "execution-budget.json",
        {"elapsed_s": 2.0, "active": None, "started_checks": started},
    )
    return config_path, output


def test_completed_checks_and_elapsed_budget_survive_resume(runner, monkeypatch, tmp_path):
    config_path, output = resumable_environment(runner, monkeypatch, tmp_path, budget_limit=1)
    completed_artifact(output)
    oracle = SimpleNamespace(problem_sha256="problem", scenarios={"source-h1": None})
    monkeypatch.setattr(runner, "make_oracle", lambda *args: (oracle, {}))
    summary = runner.resume(config_path, output)
    assert summary["completed_checks"] == 1
    assert not summary["complete"]
    budget = json.loads((output / "execution-budget.json").read_text())
    assert budget["elapsed_s"] >= 2
    assert budget["active"] is None
    assert budget["started_checks"] >= 1


def test_unpersisted_failed_attempt_consumes_declared_check_budget(runner, monkeypatch, tmp_path):
    config_path, output = resumable_environment(runner, monkeypatch, tmp_path, budget_limit=1)

    class FailedOracle:
        problem_sha256 = "problem"

        def __init__(self):
            self.scenarios = {"source-h1": None}

        def __call__(self, ids, scenario):
            raise RuntimeError("interrupted solver before persistence")

    monkeypatch.setattr(runner, "make_oracle", lambda *args: (FailedOracle(), {}))
    with pytest.raises(RuntimeError, match="interrupted solver"):
        runner.resume(config_path, output)
    budget = json.loads((output / "execution-budget.json").read_text())
    assert budget["started_checks"] == 1
    assert budget["active"] is None
    # Recovery does not reset an attempted solve just because no complete file exists.
    summary = runner.resume(config_path, output)
    assert summary["completed_checks"] == 0
    assert not summary["complete"]


def preparation_environment(runner, monkeypatch, tmp_path, *, scales):
    workspace = tmp_path / "workspace"
    code = workspace / "src/code.py"
    code.parent.mkdir(parents=True)
    code.write_text("frozen_code = True\n")
    monkeypatch.setattr(runner, "ROOT", workspace)
    source_calls = []

    def captured_paths():
        source_calls.append(1)
        return [code]

    monkeypatch.setattr(runner, "source_paths", captured_paths)
    value = config()
    value["jobs"][0]["roof_scales_m"] = scales
    config_path = tmp_path / "config.json"
    write_json(config_path, value)
    write_json(tmp_path / "study.json", {"frozen_study": True})
    oracle = SimpleNamespace(
        problem_sha256="problem",
        problem={"physical": "frozen"},
        scenarios={f"source-h{s:g}": None for s in scales},
        fires={
            "source": {
                "refined_ids": ["S"],
                "footprints": {"S": SimpleNamespace(to_mapping=lambda: {"polygon": "fixed"})},
            }
        },
        scales_m=scales,
        phase_fraction=(0.0, 0.0),
        cell_size_by_id={},
        max_nodes=100,
        max_radiation_edges=100,
        base=None,
        inventory=None,
        transport=None,
    )
    monkeypatch.setattr(runner, "make_oracle", lambda *args: (oracle, {}))
    monkeypatch.setattr(
        runner,
        "edge_bounds",
        lambda *args: {"raw_point_edges": 1, "integrated_edges_upper_bound": 2},
    )
    return config_path, tmp_path / "output", code, source_calls


def test_preparation_captures_source_list_once_before_geometry_work(runner, monkeypatch, tmp_path):
    config_path, output, _, source_calls = preparation_environment(
        runner, monkeypatch, tmp_path, scales=[1.0]
    )

    def geometry(*args, **kwargs):
        assert (output / "source-snapshot/src/code.py").read_text() == "frozen_code = True\n"
        # An unrelated module may appear during preflight; the frozen list does not grow.
        (runner.ROOT / "src/later_module.py").write_text("created_later = True\n")
        return SimpleNamespace(owners=[("S",)])

    monkeypatch.setattr(runner, "refine_roof_layout", geometry)
    runner.prepare(config_path, output)
    assert len(source_calls) == 1
    experiment = json.loads((output / "experiment.json").read_text())
    assert list(experiment["source_sha256"]) == ["src/code.py"]


def test_interrupted_preflight_reuses_only_matching_persisted_geometry(
    runner, monkeypatch, tmp_path
):
    config_path, output, _, _ = preparation_environment(
        runner, monkeypatch, tmp_path, scales=[1.0, 0.5]
    )
    geometry_calls = []

    def geometry(*args, **kwargs):
        geometry_calls.append(kwargs["cell_size_m"])
        if len(geometry_calls) == 2:
            raise RuntimeError("interrupted second preflight")
        return SimpleNamespace(owners=[("S",)])

    monkeypatch.setattr(runner, "refine_roof_layout", geometry)
    with pytest.raises(RuntimeError, match="interrupted second preflight"):
        runner.prepare(config_path, output)
    assert not (output / "experiment.json").exists()
    cache = json.loads((output / "preflight-cache.json").read_text())
    assert len(cache["geometry"]) == 1
    runner.prepare(config_path, output)
    assert geometry_calls == [1.0, 0.5, 0.5]
    assert (output / "experiment.json").exists()
