"""A prefetcher must own the main cache lock until replay export is complete."""

import fcntl
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyfireca.planning.adaptive import canonical_plan


def test_full_prefetch_keeps_replay_under_main_plan_lock(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    module = importlib.import_module("prefetch_batuan_full_validation")
    (tmp_path / "protocol.json").write_text(json.dumps({"code": "frozen"}))
    plan = canonical_plan("outer_proxy", ())
    directory = tmp_path / "full-cache"
    directory.mkdir()
    key = module.digest({"context": "same-context", "plan": plan.to_mapping()})
    lock_path = directory / (key + ".lock")
    calls = []

    def assert_main_cannot_enter(stage):
        with lock_path.open("a") as competitor, pytest.raises(BlockingIOError):
            fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(stage)

    class Oracle:
        context = "same-context"
        solver_calls = cache_hits = 0

        def __init__(self, *args):
            self.directory = directory

        def __call__(self, plan):
            assert_main_cannot_enter("physics")
            return [SimpleNamespace(evaluation_sha256="original-signature")]

    def save(*args):
        assert_main_cannot_enter("replay")
        return "viewer"

    monkeypatch.setattr(module, "load_v3_case_bundle", lambda _: "base")
    monkeypatch.setattr(module, "context", lambda _: ("near", {}, "sha"))
    monkeypatch.setattr(module, "engine_for", lambda *_: "engine")
    monkeypatch.setattr(module, "scenarios_from", lambda *_: ("scenario",))
    monkeypatch.setattr(module, "TrainingOracle", Oracle)
    monkeypatch.setattr(module, "save_checked_replay", save)
    result = module.check_one(tmp_path, "baseline", plan.to_mapping())
    assert calls == ["physics", "replay"]
    assert result["replay_exported_under_plan_lock"]
    with lock_path.open("a") as competitor:
        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
