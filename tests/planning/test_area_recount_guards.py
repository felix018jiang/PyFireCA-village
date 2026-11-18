"""Pure recount guards bind scope and physical ledgers without running village CA."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.fire_loss import summarize_fire_loss
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False))


@pytest.fixture
def recount(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "area_recount_under_test", ROOT / "scripts/recount_heritage_area_archives.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def frozen_metadata(tmp_path, *, relative_study=False):
    config_dir = tmp_path / "original-config"
    config_dir.mkdir()
    config = {
        "study_path": "study.json" if relative_study else str(config_dir / "study.json"),
        "source_policy": "fixed",
        "horizon_s": 3600,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1.0,
        "refinement_ids": ["A"],
        "core_overlap_policy": "reject",
        "source_fires": [{"fire_id": "source", "draw_id": "one", "protocol_path": "source.json"}],
    }
    problem = {
        "horizon_s": 3600,
        "scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "refinement_seed_ids": ["A"],
        "core_overlap_policy": "reject",
        "source_policy": "sampled_retained_sources",
        "fires": {"source": {"protocol": {"draws": [{"draw_id": "one", "priority_ids": ["A"]}]}}},
        "case_sha256": "not-loaded-in-scope-guard-test",
        "inventory_sha256": "not-loaded-in-scope-guard-test",
    }
    config_path = config_dir / "config.json"
    write_json(config_path, config)
    run = tmp_path / "archive"
    run.mkdir()
    experiment = {
        "schema_version": "heritage-area-master-config-1",
        "config": config,
        "config_sha256": file_sha256(config_path),
        "problem": problem,
        "problem_sha256": content_hash(problem),
        "references_sha256": {},
    }
    write_json(run / "experiment.json", experiment)
    return run, config_path, experiment


def forbid_data_loading(monkeypatch, recount):
    monkeypatch.setattr(
        recount, "load_study", lambda *_: pytest.fail("scope guard must run before loading data")
    )


def test_modified_problem_metadata_cannot_keep_its_original_hash(recount, monkeypatch, tmp_path):
    run, _, experiment = frozen_metadata(tmp_path)
    experiment["problem"]["horizon_s"] = 1200
    write_json(run / "experiment.json", experiment)
    forbid_data_loading(monkeypatch, recount)
    with pytest.raises(ValueError, match="metadata fingerprint"):
        recount.recount_master(run)


def test_changed_source_domain_or_draw_is_rejected_before_any_recount(
    recount, monkeypatch, tmp_path
):
    run, _, experiment = frozen_metadata(tmp_path)
    forbid_data_loading(monkeypatch, recount)
    experiment["config"].update(
        source_policy="area_bounded_retained_sources",
        source_area_limit_m2=25,
        source_area_tolerance_m2=0,
    )
    write_json(run / "experiment.json", experiment)
    with pytest.raises(ValueError, match="retained-source area domain"):
        recount.recount_master(run)
    experiment["config"]["source_policy"] = "fixed"
    experiment["config"]["source_fires"][0]["draw_id"] = "another-draw"
    write_json(run / "experiment.json", experiment)
    with pytest.raises(ValueError, match="source draw"):
        recount.recount_master(run)


def test_relative_study_requires_original_config_and_never_guesses_current_directory(
    recount, monkeypatch, tmp_path
):
    run, config_path, _ = frozen_metadata(tmp_path, relative_study=True)
    current = tmp_path / "unrelated-working-directory"
    current.mkdir()
    (current / "study.json").write_text('{"wrong": "must not load this file"}')
    monkeypatch.chdir(current)
    forbid_data_loading(monkeypatch, recount)
    with pytest.raises(ValueError, match="relative study_path requires --config"):
        recount.recount_master(run)
    resolved = []

    class ResolvedOriginalStudy(Exception):
        pass

    def inspect_original(path):
        resolved.append(path)
        raise ResolvedOriginalStudy

    monkeypatch.setattr(recount, "load_study", inspect_original)
    with pytest.raises(ResolvedOriginalStudy):
        recount.recount_master(run, config_path=config_path)
    assert resolved == [(config_path.parent / "study.json").resolve()]
    assert resolved[0] != current / "study.json"


def test_native_physical_loss_tampering_is_rejected_without_rewriting_raw_arrays(recount, tmp_path):
    folder = tmp_path / "complete-evaluation"
    folder.mkdir()
    area, post = np.array([2.0, 7.0, 5.0]), np.array([0.0, 7.0, 5.0])
    ignition, burnout = np.array([np.nan, 0.0, 0.0]), np.array([np.nan, 10.0, np.nan])
    inventory = SimpleNamespace(by_id={"A": object(), "B": object()}, targets=())
    parent = SimpleNamespace(known_mask=np.ones((1, 3), bool))
    np.savez_compressed(
        folder / "layout.npz",
        physical_cell_id=np.arange(3),
        owner_offsets=[0, 1, 2, 2],
        owner_ids=["A", "B"],
        component_id=["cell0:A:0", "cell1:A+B:0/local0", "cell2:ground:0"],
        xy_m=[[0, 0], [5, 0], [10, 0]],
        covered_area_m2=post,
    )
    np.savez_compressed(
        folder / "loss-basis.npz",
        baseline_covered_area_m2=area,
        post_intervention_covered_area_m2=post,
    )
    np.savez_compressed(
        folder / "final-state.npz",
        time_s=20.0,
        ignition_time_s=ignition,
        burnout_time_s=burnout,
    )
    write_json(
        folder / "evaluation.json",
        {
            "diagnostics": {"simulated_until_s": 20.0},
            "metrics": {"actual_source_ids": []},
        },
    )
    native = summarize_fire_loss(
        covered_area_m2=area,
        node_owners=(("A",), ("A", "B"), ()),
        ignition_time_s=ignition,
        burnout_time_s=burnout,
        observation_time_s=20.0,
        inventory_ids=("A", "B"),
        demolished_ids=("A",),
        cleared_node_mask=(True, False, False),
    )
    write_json(folder / "fire-loss.json", native)
    manifest = {"demolished_ids": ["A"]}
    arrays_before = {
        name: file_sha256(folder / name)
        for name in ("layout.npz", "loss-basis.npz", "final-state.npz")
    }
    _, accepted, audit = recount.enrich_saved_loss(folder, inventory, parent, manifest)
    assert audit["methods_node_count"]["immutable_component_id"] == 3
    assert (
        accepted["physical_remaining_roof_annotation_counts"][
            "burned_out_annotation_attribution_count"
        ]["upper"]
        == 2
    )
    native["physical"]["all"]["cumulative_burned_out_covered_area_m2"] += 1
    write_json(folder / "fire-loss.json", native)
    with pytest.raises(ValueError, match="recount changed native physical loss"):
        recount.enrich_saved_loss(folder, inventory, parent, manifest)
    assert arrays_before == {name: file_sha256(folder / name) for name in arrays_before}
