"""Guard paired-source attribution, time reconstruction and durable results."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.strategy_io import file_sha256

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "fixed_comparison_under_test", ROOT / "scripts/run_heritage_fixed_comparison.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config():
    return {
        "schema_version": "heritage-fixed-comparison-1",
        "horizon_s": 3600.0,
        "checkpoints_s": [1200, 2400, 3600],
        "roof_scales_m": [1.0, 0.5],
        "max_nodes": 10000,
        "max_radiation_edges": 10000,
        "max_total_checks": 4,
        "max_wall_time_s": 60,
        "minimum_free_disk_GB": 1,
        "plans": [
            {"plan_id": "baseline", "demolished_ids": []},
            {"plan_id": "selected", "demolished_ids": ["M"]},
        ],
        "source_fires": [{"fire_id": "source", "protocol_path": "protocol.json", "draw_id": "one"}],
    }


def test_duplicate_physical_arms_and_source_deletion_are_rejected(runner, tmp_path):
    value = config()
    assert runner.validate_config(value) == ("M",)
    value["plans"].append({"plan_id": "nearest", "demolished_ids": ["M"]})
    with pytest.raises(ValueError, match="duplicate physical arms"):
        runner.validate_config(value)
    value = config()
    protocol = {
        "draws": [{"draw_id": "one", "baseline_source_id": "M", "priority_ids": ["M", "S"]}]
    }
    protocol["protocol_sha256"] = content_hash(protocol)
    (tmp_path / "protocol.json").write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="cannot remove a source"):
        runner.load_fires(tmp_path / "config.json", value, ("M",))


def test_time_curves_distinguish_burnout_and_count_shared_nodes_once(runner):
    # One 7 m2 physical roof patch can represent two owners; it remains one entry.
    rows = runner.checkpoint_metrics(
        [0, 1300, 2500, np.inf],
        [500, 3000, np.nan, np.nan],
        [25, 7, 3, 9],
        [True, True, False, True],
        [1200, 2400, 3600],
    )
    assert [r["cumulative_ignited_covered_area_m2"] for r in rows] == [25, 32, 35]
    assert [r["active_burning_covered_area_m2"] for r in rows] == [0, 7, 3]
    assert [r["cumulative_ignited_roof_covered_area_m2"] for r in rows] == [25, 32, 32]
    assert [r["active_burning_node_count"] for r in rows] == [0, 1, 1]


def test_unrun_budget_limited_batch_does_not_claim_full_observation(runner, tmp_path):
    oracle = SimpleNamespace(
        scenarios={"source-h1": ("source", 1.0)},
        fires={"source": {}},
        problem_sha256="frozen",
    )
    summary = runner.export_summary(tmp_path, config(), oracle)
    assert summary["planned_CA_checks"] == 2
    assert summary["persisted_CA_checks"] == 0
    assert summary["full_horizon_required"]
    assert not summary["complete"]
    assert not summary["full_horizon_observation"]
    assert not summary["all_evaluations_reached_horizon"]


def test_cache_requires_matching_identity_and_unchanged_complete_artifacts(runner, tmp_path):
    (tmp_path / "check.json").write_text(json.dumps({"status": "passed", "evidence": {}}))
    (tmp_path / "curves.json").write_text("{}")
    for name in ("evaluation.json", "final-state.npz", "layout.npz"):
        (tmp_path / name).write_text("test artifact")
    manifest = {
        "problem_sha256": "frozen",
        "demolished_ids": ["M"],
        "scenario_id": "source-h1",
        "files_sha256": {
            "check.json": file_sha256(tmp_path / "check.json"),
            "curves.json": file_sha256(tmp_path / "curves.json"),
            **{
                name: file_sha256(tmp_path / name)
                for name in ("evaluation.json", "final-state.npz", "layout.npz")
            },
        },
    }
    (tmp_path / "complete.json").write_text(json.dumps(manifest))
    assert runner.verify_result(tmp_path, "frozen", ("M",), "source-h1")["status"] == "passed"
    with pytest.raises(ValueError, match="identity mismatch"):
        runner.verify_result(tmp_path, "other", ("M",), "source-h1")
    (tmp_path / "curves.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed cached result"):
        runner.verify_result(tmp_path, "frozen", ("M",), "source-h1")


def test_real_solver_runs_full_horizon_for_failed_core_and_preserves_source(runner, tmp_path):
    from shapely.geometry import box

    from pyfireca.planning.engines.roof_search import RoofDemolitionOracle

    spec = importlib.util.spec_from_file_location(
        "comparison_synthetic", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _ = fixture.make_case(
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 9, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    protocol = freeze_source_protocol(
        inventory, seed=20261024, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    saved = []

    def capture(ids, scenario, check, record, layout, result):
        curves = runner.result_curves(record, layout, result, [600, 1200])
        runner.write_compact_arrays(tmp_path, layout, result)
        saved.append((ids, check, curves))

    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=("M",),
        refinement_ids=("M",),
        fires=[{"fire_id": "synthetic", "protocol": protocol}],
        code_version="test",
        horizon_s=1200,
        maximum_demolitions=1,
        stop_at_first_core_ignition=False,
        on_evaluation=capture,
    )
    for scenario in oracle.scenarios:
        for ids in ((), ("M",)):
            check = oracle(ids, scenario)
            assert check.evidence["reached_horizon"]
            assert check.evidence["source_id"] == "S"
            assert not check.evidence["source_replaced"]
    assert any(check.status == "failed" for ids, check, _ in saved if not ids)
    assert all(check.status == "passed" for ids, check, _ in saved if ids)
    assert all(curves["final_time_s"] == 1200 for _, _, curves in saved)
    assert all(
        curves["checkpoints"][-1]["active_burning_node_count"]
        == curves["final_active_burning_node_count"]
        for _, _, curves in saved
    )
    with np.load(tmp_path / "layout.npz", allow_pickle=False) as layout:
        assert layout["owner_offsets"][-1] == len(layout["owner_ids"])
    with np.load(tmp_path / "final-state.npz", allow_pickle=False) as state:
        assert state["time_s"] == 1200
