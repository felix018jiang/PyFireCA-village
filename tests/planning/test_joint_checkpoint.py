"""Exercise automatic version/archive work in a disposable fake repository."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "checkpoint_fixture", ROOT / "scripts/complete_batuan_joint_checkpoint.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(module, "ROOT", repo)
    for args in [
        ("init",),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "placeholder.txt").write_text("fixture")
    subprocess.run(["git", "add", "placeholder.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    head = module.git("rev-parse", "HEAD")
    (repo / "docs/results").mkdir(parents=True)
    out = repo / "runs/research/pilot"
    (out / "review").mkdir(parents=True)
    selection = {"fixture": True}
    rows = []
    for label in [
        "baseline",
        "outer_proxy",
        "inner_proxy",
        "joint",
        "independent_sweep",
        "historical_combined",
    ]:
        for role, count in [("training", 8), ("held_out", 4)]:
            record = {
                "diagnostics": {"reached_requested_horizon": True, "simulated_until_s": 5400},
                "metrics": {
                    "burning_reached_domain_edge": False,
                    "ignited_structure_count": 0,
                    "ignited_house_count": 0,
                    "ignited_bridge_count": 0,
                },
                "intervention": {
                    "cleared_area_m2": 0,
                    "zone_areas_m2": {"outer_proxy": 0, "inner_proxy": 0},
                },
            }
            rows.append(
                {
                    "label": label,
                    "role": role,
                    "full_grid": True,
                    "records": [{**record, "scenario_id": f"{role}-{i}"} for i in range(count)],
                }
            )
    module.dump(out / "selection.json", selection)
    module.dump(out / "protocol.json", {"fixture": True})
    module.dump(out / "report.json", {"selection": selection, "confirmation": rows})
    for mode in ["outer_proxy", "inner_proxy", "joint"]:
        module.dump(out / (mode + "-search.json"), {"records": [None] * 16})
    module.dump(
        out / "audit.json",
        {
            "all_protected_counts_match": True,
            "new_physical_solves_during_recovery": 0,
            "training_prefetch_only": True,
            "actual_ca_solves": 0,
        },
    )
    (out / "review/index.html").write_text("fixture report")
    os.utime(out / "review/index.html", (2_000_000_000, 2_000_000_000))
    channels = repo / "runs/research/batuan-pair-channels-20261017-r1"
    channels.mkdir()
    module.dump(channels / "report.json", {"fixture": True})
    return repo, out, head


def test_checkpoint_commits_only_results_and_verifies_archive(tmp_path, monkeypatch):
    repo, out, head = fixture(tmp_path, monkeypatch)
    # Preserve a user's staged, unrelated edit.
    (repo / "user.txt").write_text("user change")
    subprocess.run(["git", "add", "user.txt"], cwd=repo, check=True)
    module.finish(out, head, os.getpid())
    complete = json.loads((out / "completion.json").read_text())
    assert complete["archive_verified"]
    assert module.git("rev-parse", "HEAD") == module.git("rev-parse", complete["tag"] + "^{}")
    assert "user.txt" not in module.git("show", "--name-only", "--format=", "HEAD")
    assert "user.txt" in module.git("diff", "--cached", "--name-only")
    backup = Path(complete["backup"])
    proof = json.loads((backup / "backup-validation.json").read_text())
    assert proof["verified_files"] > 0
    assert module.sha(backup / "joint-and-channel-experiments.tar.gz") == proof["archive_sha256"]


def test_changed_head_preserves_results_without_automatic_commit(tmp_path, monkeypatch):
    repo, out, head = fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="HEAD changed"):
        module.finish(out, "different-head", os.getpid())
    assert module.git("rev-parse", "HEAD") == head
    assert (repo / "docs/results/batuan-joint-pilot-20261017.json").exists()
    assert not (out / "completion.json").exists()
    assert not module.git("tag", "--list", "batuan-joint-pilot-20261017")


def test_completion_tag_is_present_in_verified_bundle(tmp_path, monkeypatch):
    _repo, out, head = fixture(tmp_path, monkeypatch)
    module.finish(out, head, os.getpid())
    complete = json.loads((out / "completion.json").read_text())
    bundle = Path(complete["backup"]) / "repository.bundle"
    refs = subprocess.check_output(["git", "bundle", "list-heads", str(bundle)], text=True)
    assert "refs/tags/" + complete["tag"] in refs
