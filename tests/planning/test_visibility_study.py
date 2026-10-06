"""End-to-end scientific record: failed old gate and scoped corrected gate."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely", minversion="2")


def test_visibility_study_reproduces_old_failure_and_preserves_corrected_evidence(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/planning/visibility_validation.py"
    spec = importlib.util.spec_from_file_location("visibility_study", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "study"
    summary = module.run_study(output)
    old = json.loads((output / "raster_baseline/validation.json").read_text())
    assert old["p03_continuum_planning_gate"] == "not_passed"
    assert summary["p03_scope_gate"] == "eligible_small_D0_isotropic_only"
    assert summary["evaluation_records"] == 78
    assert summary["visibility_solver_calls"] == 144
    assert summary["worst_relative_error"] <= 0.02
    study = json.loads((output / "study.resolved.json").read_text())
    assert study["capabilities"]["supports_anisotropic_ros"] is False
    assert study["capabilities"]["global_continuum_optimality_guaranteed"] is False
    provenance = json.loads((output / "provenance.json").read_text())
    for relative, digest in provenance["source_sha256"].items():
        assert (
            hashlib.sha256((output / "source_snapshot" / relative).read_bytes()).hexdigest()
            == digest
        )
    before = (output / "validation.json").read_bytes()
    with pytest.raises(FileExistsError):
        module.run_study(output)
    assert before == (output / "validation.json").read_bytes()
