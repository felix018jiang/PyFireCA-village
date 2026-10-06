"""Check that the runnable experiment preserves its scientific record."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely", minversion="2")


def test_study_reproducible_and_never_overwrites_existing_output(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/planning/single_line.py"
    spec = importlib.util.spec_from_file_location("planning_single_line", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, second = tmp_path / "first", tmp_path / "second"
    module.run_study(first)
    module.run_study(second)
    for path in first.iterdir():
        if path.name != "provenance.json":
            assert path.read_bytes() == (second / path.name).read_bytes()
    records = {r["plan_id"]: r for r in json.loads((first / "evaluations.json").read_text())}
    assert records["ideal-w0.02"]["target_arrival_s"] is None
    assert records["ideal-w0.02"]["target_graph_unreachable"] is True
    assert records["slow-w2"]["target_arrival_s"] == pytest.approx(106)
    study = json.loads((first / "study.resolved.json").read_text())
    assert study["evidence_level"] == "D0_synthetic"
    assert study["capabilities"]["supports_spotting"] is False
    before = {p.name: p.read_bytes() for p in first.iterdir()}
    with pytest.raises(FileExistsError):
        module.run_study(first)
    assert before == {p.name: p.read_bytes() for p in first.iterdir()}
