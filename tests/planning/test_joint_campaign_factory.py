"""The joint factory cannot promote fixed-source or incomplete seeds to production."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "joint_factory_under_test", ROOT / "scripts/prepare_heritage_joint_area_campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def minimal_master(tmp_path, *, policy="area_bounded_retained_sources", best=None, feasible=()):
    master = {
        "source_policy": policy,
        "study_path": "study.json",
        "refinement_ids": ["B"],
        "source_fires": [
            {"fire_id": f"family-{i}", "protocol_path": f"source-{i}.json", "draw_id": "one"}
            for i in range(4)
        ],
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "horizon_s": 3600.0,
        "max_dt_s": 1.0,
        "max_nodes": 100,
        "max_radiation_edges": 100,
        "minimum_free_disk_GB": 1.0,
        "source_area_limit_m2": 30.0,
        "source_area_tolerance_m2": 1e-6,
        "core_overlap_policy": "reject",
    }
    master_path = tmp_path / "master.json"
    master_path.write_text(json.dumps(master))
    problem = {"inventory_sha256": "inventory", "case_sha256": "case"}
    experiment = {
        "config": master,
        "config_sha256": file_sha256(master_path),
        "problem": problem,
        "problem_sha256": content_hash(problem),
        "candidate_ids": ["A", "B", "C"],
    }
    (tmp_path / "experiment.json").write_text(json.dumps(experiment))
    search = {
        "problem_sha256": experiment["problem_sha256"],
        "source_policy": policy,
        "best_ids": best,
        "verified_feasible_plans": list(feasible),
        "scenario_ids": [f"family-{i}-h{scale:g}" for i in range(4) for scale in (1.0, 0.5)],
    }
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(search))
    return master_path, search_path, master, experiment, search


def test_fixed_source_upper_bound_is_not_a_retained_seed(factory, tmp_path):
    master, search, *_ = minimal_master(
        tmp_path, policy="fixed", best=["A"], feasible=[{"demolished_ids": ["A"]}]
    )
    with pytest.raises(ValueError, match="never fixed-source UB"):
        factory.validated_seed(master, search)


def test_no_verified_retained_seed_writes_no_production_config(factory, tmp_path):
    master, search, *_ = minimal_master(tmp_path)
    destination = tmp_path / "joint.json"
    with pytest.raises(ValueError, match="no fully validated seed"):
        factory.prepare("batuan", master_path=master, search_path=search, destination=destination)
    assert not destination.exists()
    assert not destination.with_suffix(".seed-receipt.json").exists()


def test_complete_search_row_still_requires_native_full_horizon_checks(
    factory, monkeypatch, tmp_path
):
    best = ["A"]
    master, search, *_ = minimal_master(tmp_path, best=best, feasible=[{"demolished_ids": best}])
    monkeypatch.setattr(
        factory, "read_complete", lambda *a, **k: SimpleNamespace(status="passed", evidence={})
    )
    monkeypatch.setattr(factory, "observation_status", lambda *a, **k: (False, False, {}, {}))
    with pytest.raises(ValueError, match="fully protected and observed"):
        factory.validated_seed(master, search)


@pytest.mark.parametrize("terminal", [False, True])
def test_factory_freezes_exact_search_bytes_receipt_and_reference_mesh(
    factory, monkeypatch, tmp_path, terminal
):
    terminal_args = {
        "terminal_fire_verification": terminal,
        "terminal_horizon_cap_s": 21600 if terminal else None,
    }
    best = ["A", "B"]
    feasible = {"demolished_ids": best, "cleared_area_m2": 30.0, "loss_metrics_complete": True}
    master_path, search_path, master, experiment, search = minimal_master(
        tmp_path, best=best, feasible=[feasible]
    )
    captured = search_path.read_bytes()
    checks = [
        {"scenario_id": scenario, "status": "passed", "complete_manifest_sha256": "proof"}
        for scenario in search["scenario_ids"]
    ]
    monkeypatch.setattr(
        factory,
        "validated_seed",
        lambda *_: (master, experiment, search, captured, feasible, checks),
    )
    monkeypatch.setattr(factory, "read_master_reference", lambda *_: (experiment, {}))
    inventory = SimpleNamespace(sha256="inventory")
    monkeypatch.setattr(
        factory, "load_study", lambda _: (SimpleNamespace(request=object()), None, inventory)
    )
    monkeypatch.setattr(
        factory,
        "resolve_roof_layout",
        lambda *a, **k: SimpleNamespace(
            owners=(("A",), ("B",)), geometry=(box(0, 0, 4, 5), box(4, 0, 6, 5))
        ),
    )
    proposals = tmp_path / "proposals.json"
    proposals.write_text(
        json.dumps(
            {
                "villages": [
                    {
                        "village_id": "batuan",
                        "plans": [
                            {"plan_id": "connection", "cleared_physical_cell_ids": [101]},
                            {"plan_id": "core-gap", "cleared_physical_cell_ids": [102]},
                            {"plan_id": "area-control", "cleared_physical_cell_ids": [103]},
                        ],
                    }
                ]
            }
        )
    )
    destination = tmp_path / "joint.json"
    report = factory.prepare(
        "batuan",
        master_path=master_path,
        search_path=search_path,
        proposals_path=proposals,
        destination=destination,
        **terminal_args,
    )
    config = json.loads(destination.read_text())
    receipt = json.loads(destination.with_suffix(".seed-receipt.json").read_text())
    assert report["planned_CA_checks"] == 40 and report["new_CA_checks"] == 0
    assert config["source_policy"] == "retained_sources"
    assert len(config["plans"]) == 5 and len(config["source_fires"]) == 4
    assert config["max_total_checks"] == config["max_new_checks"] == 40
    if terminal:
        assert config["terminal_fire_verification"]
        assert config["terminal_horizon_cap_s"] == 21600
        assert config["max_wall_time_s"] == 28800
    else:
        assert "terminal_fire_verification" not in config
        assert "terminal_horizon_cap_s" not in config
        assert config["max_wall_time_s"] == 14400
    assert config["refinement_ids"] == ["B"]  # A is selected, but must not force new meshing.
    assert config["candidate_ids"] == ["A", "B", "C"]
    assert config["roof_replacement_rule"]["removed_id"] == "A"
    assert config["roof_replacement_rule"]["saved_roof_geometry_area_m2"] == 20
    assert destination.with_suffix(".seed-search.json").read_bytes() == captured
    assert receipt["verified_feasible_row"] == feasible
    assert len(receipt["all_eight_retained_checks"]) == 8
    assert (
        file_sha256(destination.with_suffix(".seed-receipt.json"))
        == config["roof_seed_receipt_sha256"]
    )
    assert not receipt["global_joint_optimality_proven"]
    before = destination.read_bytes()
    factory.prepare(
        "batuan",
        master_path=master_path,
        search_path=search_path,
        proposals_path=proposals,
        destination=destination,
        **terminal_args,
    )
    assert destination.read_bytes() == before
    proposals.write_text(proposals.read_text() + "\n")
    with pytest.raises(ValueError, match="immutable"):
        factory.prepare(
            "batuan",
            master_path=master_path,
            search_path=search_path,
            proposals_path=proposals,
            destination=destination,
            **terminal_args,
        )
    assert destination.read_bytes() == before


@pytest.mark.parametrize(
    "flag,cap", [(True, None), (True, 1200), (False, 21600), (True, float("nan")), (True, True)]
)
def test_terminal_factory_requires_explicit_valid_temporal_protocol(factory, flag, cap):
    with pytest.raises(ValueError, match="terminal"):
        factory.prepare("batuan", terminal_fire_verification=flag, terminal_horizon_cap_s=cap)
