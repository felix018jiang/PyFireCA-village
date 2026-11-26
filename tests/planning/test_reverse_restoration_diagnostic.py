"""Reverse restoration screening never masquerades as a village certificate."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def diagnostic():
    spec = importlib.util.spec_from_file_location(
        "reverse_diagnostic_tests", ROOT / "scripts/audit_heritage_reverse_restoration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_prefix_restoration_cannot_change_the_actual_prescribed_source(diagnostic):
    priority = ["A", "S", "B", "C"]
    actual, blocked = diagnostic.source_restoration_guard(priority, ("A", "B"))
    assert actual == "S" and blocked == ["A"]
    assert diagnostic.source_restoration_guard(priority, ("A",))[0] == "S"
    assert diagnostic.source_restoration_guard(priority, ("B",))[0] == "A"
    with pytest.raises(ValueError, match="source-empty"):
        diagnostic.source_restoration_guard(priority, priority)


def test_owner_AND_restoration_changes_only_whole_previously_cleared_physical_groups(diagnostic):
    owners = [("A", "B"), ("B",), ("A", "C"), ()]
    before = diagnostic.roof_clear_mask(owners, {"A", "B"})
    after_a = diagnostic.roof_clear_mask(owners, {"B"})
    after_b = diagnostic.roof_clear_mask(owners, {"A"})
    assert before.tolist() == [True, True, False, False]
    assert (before & ~after_a).tolist() == [True, False, False, False]
    assert (before & ~after_b).tolist() == [True, True, False, False]
    assert not diagnostic.roof_clear_mask([("A", "C")], {"A"}).any()


def test_parent_box_bound_includes_exact_radius_boundary_and_refined_source_multiplicity(
    diagnostic,
):
    shape = (11, 11)
    cell = 5 * 11 + 5
    cells = np.r_[np.arange(121), cell]
    first = cells == cell
    zone, upper = diagnostic.parent_cell_zone(cells, first, shape, 15.0, 5.0)
    assert zone[5 * 11 + 9]
    assert not zone[5 * 11 + 10]
    assert upper == 2 * int(zone[cells].sum())


def test_real_closed_restoration_controls_preserve_kept_trajectories_but_not_global_budget(
    diagnostic,
):
    result = diagnostic.small_native_controls()
    assert result["native_CA_runs"] == 4
    for case in result["controls"]:
        assert case["old_native_status"] == case["new_native_status"] == "passed"
        assert case["old_native_invalid"] == case["new_native_invalid"] == []
        assert case["old_closed"] and case["new_closed"]
        assert case["every_kept_native_step_state_clock_and_radiation_path_bitwise_equal"]
        assert case["original_ignited_source_radiation_spans_exact"]
        assert case["contact_padding_width_old"] == case["contact_padding_width_new"]
        assert case["contact_original_ignited_rows_bytes_exact"]
        assert case["cold_CFL_guard"]
        assert case["cold_temperature_exact_stationary"]
        assert case["cold_water_storage_exact_stationary"]
        assert case["global_energy_budget_rounding_differences"]
        assert not case["strict_village_certificate_generated_by_this_control"]
    assert result["masked_reduction_zero_insertion_counterexample"]["bits_differ"]
