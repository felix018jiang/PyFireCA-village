"""Execution revisions and the single final action precede independent-source reveal."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json


@pytest.fixture
def helper(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "final_batuan_confirmation", scripts / "prepare_heritage_batuan_final_confirmation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(helper):
    plan = {
        "plan_id": "direct-thin-D9-with-no-grass",
        "demolished_ids": [
            "B0060",
            "B0061",
            "B0064",
            "B0065",
            "B0068",
            "B0071",
            "B0074",
            "B0076",
            "B0077",
        ],
        "cleared_physical_cell_ids": [],
    }
    r = {
        "registration_sha256": helper.REGISTRATION_ID,
        "inventory_sha256": "0" * 64,
        "generator_source_files_sha256": {},
        **{
            k: "unchanged"
            for k in (
                "seed",
                "rng",
                "rng_namespace",
                "allocation",
                "ignition_area_m2",
                "near_distance_m",
                "empty_near_pool_policy",
                "pressure_distance_m",
                "preferred_pool_policy",
                "fallback_policy",
                "anchor_policy",
                "resampling_rule",
                "confirmation_failure_rule",
                "weight_policy",
            )
        },
    }
    acceptance = {
        "plan": plan,
        "summary": {"complete8_strict_passed_and_closed": True, "C_m2": 1089.0173624408872},
        "maximum_core_temperature_K": {"B0073": 416.6, "B0120": 389.8},
    }
    config = {
        "refinement_ids": [
            "B0061",
            "B0062",
            "B0063",
            "B0067",
            "B0071",
            "B0073",
            "B0076",
            "B0077",
            "B0120",
        ],
        "core_overlap_policy": "reject",
    }
    return r, acceptance, config


def test_unique_final_keeps_old_refinement_and_unions_D9_core(helper):
    r, accepted, design = inputs(helper)
    before = deepcopy((r, accepted, design))
    frozen = helper.final_manifest(r, accepted, design, {"mask": "original-physical-mask"})
    assert frozen["plans"] == [accepted["plan"]]
    assert len(frozen["refinement_ids"]) == 14
    assert set(design["refinement_ids"]) <= set(frozen["refinement_ids"])
    assert not frozen["source_priorities_revealed_before_this_final_freeze"]
    assert (r, accepted, design) == before
    accepted["plan"]["cleared_physical_cell_ids"] = [12]
    with pytest.raises(ValueError, match="only the complete-eight"):
        helper.final_manifest(r, accepted, design, {})


def test_amendment_is_source_blind_and_clone_cannot_change_registered_numerics(helper, tmp_path):
    r, accepted, design = inputs(helper)
    final, clock = tmp_path / "final.json", tmp_path / "clock.json"
    write_json(final, helper.final_manifest(r, accepted, design, {}))
    write_json(clock, {})
    amendment = helper.execution_amendment(r, final, clock)
    base = {
        **amendment["unchanged_numerics"],
        **{k: v["base"] for k, v in amendment["overrides"].items()},
        "source_fires": [{"protocol_path": "unrevealed-sources-remain-the-original-binder-output"}],
        "plans": [accepted["plan"]],
        "refinement_ids": read_json(final)["refinement_ids"],
    }
    before = deepcopy(base)
    config = helper.clone_execution_config(base, amendment, {"proof": "0" * 64})
    assert config["terminal_horizon_cap_s"] == config["max_wall_time_s"] == 172800
    assert config["max_total_checks"] == config["max_new_checks"] == 8
    assert config["required_solver_source_sha256"] == helper.SOLVER_SHA
    assert config["source_fires"] == base["source_fires"]
    assert config["plans"] == base["plans"]
    assert base == before
    base["quadrature_spacing_m"] = 1
    with pytest.raises(ValueError, match="fixed numerics"):
        helper.clone_execution_config(base, amendment, {})


def test_actual_original_bind_cannot_run_before_both_immutable_receipts(
    helper, tmp_path, monkeypatch
):
    r, acceptance, config = inputs(helper)
    directory = tmp_path / "prepared"
    registration, accepted, cfg = (
        tmp_path / n for n in ("reg.json", "accepted.json", "config.json")
    )
    catalog, clock_path = tmp_path / "catalog.json", tmp_path / "clock.json"
    write_json(catalog, {"plans": [acceptance["plan"]]})
    config.update(
        vegetation_proposals_path=str(catalog), vegetation_proposals_sha256=file_sha256(catalog)
    )
    write_json(cfg, config)
    run = tmp_path / "native-design"
    run.mkdir()
    experiment = run / "experiment.json"
    write_json(experiment, {"config": config, "config_sha256": file_sha256(cfg)})
    acceptance.update(run_directory=str(run), experiment_sha256=file_sha256(experiment))
    write_json(accepted, acceptance)
    write_json(registration, r)
    registration_before = file_sha256(registration)
    monkeypatch.setattr(helper, "ACCEPTANCE_SHA", file_sha256(accepted))
    monkeypatch.setattr(helper.original, "_checked_registration", lambda path: r)
    clock = {
        "schema_version": "v3-sample-clock-error-path-extension-audit-1",
        "new_solver_sha256": helper.SOLVER_SHA,
        "remove_exact_added_error_branch_restores_entire_old_file_byte_for_byte": True,
        "regression_validation": {"total_passed": 123},
    }
    clock["audit_sha256"] = content_hash(clock)
    write_json(clock_path, clock)

    class ReachedOriginalBinder(Exception):
        pass

    def bind(registration_path, final_path, base):
        amendment_path = directory / "execution-amendment.json"
        assert final_path.is_file() and amendment_path.is_file()
        amendment = read_json(amendment_path)
        assert amendment["final_candidates_sha256"] == file_sha256(final_path)
        assert amendment["overrides"]["terminal_horizon_cap_s"]["effective"] == 172800
        assert not list(base.glob("source-*.json"))
        raise ReachedOriginalBinder

    monkeypatch.setattr(helper.original, "bind", bind)
    with pytest.raises(ReachedOriginalBinder):
        helper.prepare(registration, accepted, cfg, directory, clock_path)
    assert file_sha256(registration) == registration_before
