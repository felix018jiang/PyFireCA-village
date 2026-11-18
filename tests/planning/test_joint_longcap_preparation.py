"""A larger time cap is an immutable new protocol with identical fire sources."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def preparation(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "longcap_preparation", ROOT / "scripts/prepare_heritage_joint_longcap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    producer = tmp_path / "producer"
    (producer / "src").mkdir(parents=True)
    source = producer / "src/model.py"
    source.write_text("# unchanged physics\n")
    hashes = {"src/model.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    (producer / "producer-manifest.json").write_text(json.dumps({"source_files_sha256": hashes}))
    config = tmp_path / "old.json"
    value = {
        "terminal_fire_verification": True,
        "terminal_horizon_cap_s": 21600,
        "max_total_checks": 40,
        "max_new_checks": 40,
        "max_wall_time_s": 28800,
        "source_fires": [{"protocol_path": str(tmp_path / "source.json")}],
        "plans": [{"plan_id": "one", "demolished_ids": ["A"], "cleared_physical_cell_ids": [7]}],
        "roof_scales_m": [1, 0.5],
        "max_dt_s": 1,
        "quadrature_spacing_m": 0.5,
    }
    config.write_text(json.dumps(value))
    run = tmp_path / "old-run"
    run.mkdir()
    (run / "experiment.json").write_text(
        json.dumps(
            {
                "config": value,
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            }
        )
    )
    (run / "execution-budget.json").write_text(
        json.dumps(
            {
                "started_checks": 2,
                "elapsed_s": 533.3987848749966,
                "active": None,
            }
        )
    )
    args = [
        config,
        run,
        producer,
        tmp_path / "new.json",
        tmp_path / "new-producer",
        tmp_path / "receipt.json",
    ]
    return module, args


def test_preparation_preserves_all_physics_and_old_spent_budget(preparation):
    module, args = preparation
    before = {
        str(p): p.read_bytes()
        for root in (args[1], args[2])
        for p in root.rglob("*")
        if p.is_file()
    }
    receipt = module.prepare(*args)
    assert receipt["changed_configuration_keys"] == ["max_wall_time_s", "terminal_horizon_cap_s"]
    assert receipt["old_spent_budget_preserved"]["started_checks"] == 2
    assert receipt["five_arms_and_complete_source_scale_matrix_preserved"]
    assert (
        receipt["new_protocol_starts_at_zero"]
        and not receipt["old_prefix_or_optimality_transferred"]
    )
    assert receipt["new_CA_checks"] == 0
    assert (args[4] / "src/model.py").read_bytes() == (args[2] / "src/model.py").read_bytes()
    assert module.prepare(*args) == receipt
    assert before == {path: Path(path).read_bytes() for path in before}


def test_frozen_parent_source_change_prevents_valid_new_config(preparation):
    module, args = preparation
    (args[2] / "src/model.py").write_text("# changed physics\n")
    with pytest.raises(ValueError, match="producer changed"):
        module.prepare(*args)
    assert not args[3].exists()


@pytest.mark.parametrize("cap", [True, 21600, 0, float("inf")])
def test_longcap_must_be_an_explicit_larger_finite_horizon(preparation, cap):
    module, args = preparation
    with pytest.raises(ValueError):
        module.prepare(*args, cap_s=cap)


def test_new_producer_cannot_be_written_inside_old_snapshot(preparation):
    module, args = preparation
    args[4] = args[2] / "nested"
    with pytest.raises(ValueError, match="independent"):
        module.prepare(*args)
    assert not args[4].exists()
