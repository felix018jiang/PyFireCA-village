"""Two-arm preparation preserves controls and never invokes a physical graph."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from test_joint_comparison import inputs as inputs
from test_joint_comparison import runner as runner
from test_joint_finite_catalog import configured


@pytest.fixture
def preparation(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "optimized_balanced_preparation_test",
        scripts / "prepare_heritage_optimized_balanced_catalog.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(name, offset, width):
    return {
        "plan_id": name,
        "inner_hull_buffer_m": offset,
        "nominal_ring_width_m": width,
        "demolished_ids": ["R"],
        "cleared_physical_cell_ids": [1],
        "conservative_cut_evidence": {"certified_no_potential_radiation_or_contact_edge": True},
        "protected_original_roof_node_cleared_count": 0,
        "nominal_ring_outside_parent_rectangle_area_m2": 0,
        "remaining_potential_node_positive_ring_overlap_count": 0,
    }


def test_only_optimized_and_original_b10_are_selected_without_mutating_old_catalog(preparation):
    top = {"reference_master_problem_sha256": "reference", "plans": [plan("optimized", 0, 15.01)]}
    control = {
        "reference_master_problem_sha256": "reference",
        "plans": [plan("old10", 10, 20), plan("old20", 20, 20), plan("old30", 30, 20)],
    }
    before = deepcopy(control)
    selected = preparation.selected_plans(top, control)
    assert [p["plan_id"] for p in selected] == ["optimized", "old10"]
    selected[1]["demolished_ids"].append("another")
    assert control == before


def test_unverified_selected_cut_cannot_become_a_production_arm(preparation):
    top = {"reference_master_problem_sha256": "reference", "plans": [plan("optimized", 0, 15.01)]}
    control = {"reference_master_problem_sha256": "reference", "plans": [plan("old10", 10, 20)]}
    top["plans"][0]["conservative_cut_evidence"][
        "certified_no_potential_radiation_or_contact_edge"
    ] = False
    with pytest.raises(ValueError, match="cut witness"):
        preparation.selected_plans(top, control)


def test_real_synthetic_oracle_source_and_eligibility_audit_is_graph_free(
    preparation, inputs, runner, monkeypatch
):
    path, config, _ = configured(inputs, runner)
    oracle, _ = runner.make_oracle(path, config, "source-certificate-only")
    monkeypatch.setattr(preparation, "runner", runner)
    audit = preparation.zero_ca_validation(path, config, oracle.finite_source_certificate)
    assert audit["make_oracle_passed"]
    assert audit["graph_builds"] == audit["new_CA_checks"] == 0
    assert not audit["scenario_engine_instantiated"]
    assert audit["plan_source_grass_checks"][0]["actual_source_id"] == "M"
    assert audit["plan_source_grass_checks"][0][
        "prescribed_source_footprint_area_m2"
    ] == pytest.approx(25)
