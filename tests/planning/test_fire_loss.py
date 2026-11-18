"""Guard physical accounting, uncertain ownership, and event-time reconstruction."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.fire_loss import fire_loss_from_result, summarize_fire_loss


def report(**updates):
    values = {
        "covered_area_m2": [2, 7, 3, 5, 4],
        "node_owners": [("A",), ("A", "B"), ("B",), (), ("C",)],
        "ignition_time_s": [0, 10, 30, 20, np.nan],
        "burnout_time_s": [5, np.nan, 40, 25, np.nan],
        "observation_time_s": 20,
        "final_time_s": 40,
        "inventory_ids": ["A", "B", "C", "missing"],
    }
    return summarize_fire_loss(**(values | updates))


def test_shared_roof_area_is_physical_once_but_owner_attribution_is_bounded():
    value = report()
    physical = value["physical"]
    assert physical["all"]["cumulative_ignited_covered_area_m2"] == 14
    assert physical["roof"]["cumulative_ignited_covered_area_m2"] == 9
    assert physical["nonroof"]["cumulative_ignited_covered_area_m2"] == 5
    assert physical["all"]["active_burning_covered_area_m2"] == 12
    assert physical["all"]["cumulative_burned_out_covered_area_m2"] == 2
    by_id = {b["building_id"]: b for b in value["per_building"]}
    assert by_id["A"]["cumulative_ignited_area_m2"] == {"lower": 2, "upper": 9}
    assert by_id["B"]["cumulative_ignited_area_m2"] == {"lower": 0, "upper": 7}
    assert by_id["A"]["ignited_fraction_of_modeled_roof"] == {"lower": 2 / 9, "upper": 1}
    assert by_id["A"]["whole_building_destroyed"] is None
    assert value["real_destroyed_building_count"] is None
    assert value["structural_damage_area_m2"] is None
    # A and B can both be intersected by the same 7 m2 patch, without inventing
    # two independent physical patches or two definite whole-building losses.
    assert value["annotation_counts"]["intersected_annotation_count"] == 2
    assert value["annotation_counts"]["ignited_annotation_attribution_count"] == {
        "lower": 1,
        "upper": 3,
    }
    assert value["annotation_counts"]["unrepresented_retained_ids"] == ["missing"]
    assert by_id["missing"]["cumulative_ignited_area_m2"]["upper"] is None
    json.dumps(value, allow_nan=False)


def test_checkpoint_events_include_exact_ignition_and_exclude_exact_burnout():
    at_ten = report(observation_time_s=10)
    at_twenty_five = report(observation_time_s=25)
    at_forty = report(observation_time_s=40)
    assert at_ten["physical"]["all"]["active_burning_covered_area_m2"] == 7
    assert at_twenty_five["physical"]["all"]["active_burning_covered_area_m2"] == 7
    assert at_forty["physical"]["all"]["cumulative_burned_out_covered_area_m2"] == 10
    # Missing burnout still means active, even at the final observation.
    assert at_forty["physical"]["all"]["active_burning_covered_area_m2"] == 7
    assert not at_forty["fire_extinguished_at_observation"]


def test_demolished_ids_do_not_reenter_fire_counts_via_retained_shared_roof():
    value = report(
        demolished_ids=["A"],
        ignition_time_s=[np.nan, 10, 30, 20, np.nan],
        burnout_time_s=[np.nan, np.nan, 40, 25, np.nan],
        common_retained_ids=["B", "C"],
        source_ids=["B"],
    )
    area = value["physical"]["all"]
    assert area["cleared_covered_area_m2"] == 2
    assert area["cumulative_ignited_covered_area_m2"] == 12
    assert area["clearing_plus_ignited_union_area_m2"] == 14
    assert value["demolished_annotation_count"] == 1
    assert value["annotation_counts"]["retained_inventory_count"] == 3
    assert value["common_retained_annotation_counts"]["retained_inventory_count"] == 2
    assert value["common_retained_annotation_counts"]["ignited_annotation_attribution_count"] == {
        "lower": 0,
        "upper": 1,
    }
    assert value["secondary_annotation_counts"]["ignited_annotation_attribution_count"] == {
        "lower": 0,
        "upper": 1,  # The missing inventory object remains unknown.
    }
    removed = next(b for b in value["per_building"] if b["building_id"] == "A")
    assert removed["cumulative_ignited_area_m2"] == {"lower": 0, "upper": 0}


def test_clearing_two_owners_removes_their_shared_physical_patch_only_once():
    value = report(
        demolished_ids=["A", "B"],
        ignition_time_s=[np.nan, np.nan, np.nan, 20, np.nan],
        burnout_time_s=[np.nan, np.nan, np.nan, 25, np.nan],
    )
    assert value["physical"]["roof"]["cleared_covered_area_m2"] == 12
    assert value["physical"]["all"]["clearing_plus_ignited_union_area_m2"] == 17
    assert value["demolished_annotation_count"] == 2
    assert value["annotation_counts"]["clearing_plus_ignited_annotation_count"] == {
        "lower": 2,
        "upper": 3,  # Only the unrepresented inventory object is unresolved.
    }


def test_fuel_depletion_uses_pyrolysable_mass_and_clearing_is_counted_separately():
    initial = [0, 70, 30, 50, 40]
    remaining = [0, 20, 0, 0, 40]
    kwargs = {
        "demolished_ids": ["A"],
        "ignition_time_s": [np.nan, 10, 30, 20, np.nan],
        "burnout_time_s": [np.nan, np.nan, 40, 25, np.nan],
        "initial_fuel_kg": initial,
        "final_fuel_remaining_kg": remaining,
        "baseline_initial_fuel_kg": [20, 70, 30, 50, 40],
    }
    earlier = report(**kwargs)
    assert not earlier["fuel"]["available"]
    value = report(observation_time_s=40, **kwargs)
    fuel = value["fuel"]
    assert fuel["fire_depleted_kg"] == 130
    assert fuel["roof_fire_depleted_kg"] == 80
    assert fuel["nonroof_fire_depleted_kg"] == 50
    assert fuel["planned_removed_kg"] == 20
    assert fuel["clearing_plus_fire_depleted_kg"] == 150
    assert fuel["actively_burning_remaining_kg"] == 20


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"node_owners": [("A", "A")] * 5}, "node_owners"),
        ({"covered_area_m2": [1, -1, 1, 1, 1]}, "nonnegative"),
        ({"observation_time_s": 41}, "observation time"),
        ({"burnout_time_s": [np.nan, 9, 40, 25, np.nan]}, "burnout must follow"),
        (
            {
                "ignition_time_s": [0, 10, 41, 20, np.nan],
                "burnout_time_s": [5, np.nan, np.nan, 25, np.nan],
            },
            "exceeds the saved",
        ),
        ({"demolished_ids": ["A"]}, "cleared nodes cannot ignite"),
        ({"demolished_ids": ["A"], "common_retained_ids": ["A"]}, "retained inventory"),
        ({"initial_fuel_kg": [1] * 5}, "provided together"),
        (
            {"initial_fuel_kg": [1] * 5, "final_fuel_remaining_kg": [2] * 5},
            "remaining fuel exceeds",
        ),
    ],
)
def test_invalid_physical_or_time_inputs_are_not_silent(updates, message):
    with pytest.raises(ValueError, match=message):
        report(**updates)


def adapter_case():
    from shapely.geometry import box

    objects = (
        SimpleNamespace(building_id="A", geometry=box(0, 0, 3, 1)),
        SimpleNamespace(building_id="B", geometry=box(2, 0, 5, 1)),
    )
    inventory = SimpleNamespace(
        buildings=objects, targets=(), by_id={b.building_id: b for b in objects}
    )
    baseline = SimpleNamespace(
        covered_area_m2=np.array([[2, 1, 2]]),
        pyrolysable_dry_fuel_mass_kg=np.array([[20, 10, 20]]),
    )
    layout = SimpleNamespace(
        node_inputs=SimpleNamespace(
            parameters=baseline,
            burnable_mask=np.array([[True, True, True]]),
            known_mask=np.array([[True, True, True]]),
        ),
        node_request=SimpleNamespace(parameters=SimpleNamespace(covered_area_m2=[[0, 1, 2]])),
        # The surviving shared patch lost A from its *current* solver owners.
        owners=(("A",), ("B",), ("B",)),
        xy_m=np.array([[1, 0.5], [2.5, 0.5], [4, 0.5]]),
    )
    state = SimpleNamespace(
        time_s=30,
        ignition_time_s=[[np.nan, 10, np.nan]],
        burnout_time_s=[[np.nan, np.nan, np.nan]],
        fuel_remaining_kg=[[0, 5, 20]],
    )
    result = SimpleNamespace(final_state=state, initial_fuel_kg=[[0, 10, 20]])
    return layout, result, inventory


def test_result_adapter_recovers_original_shared_ownership_after_demolition():
    layout, result, inventory = adapter_case()
    value = fire_loss_from_result(layout, result, inventory, demolished_ids=["A"])
    assert value["physical"]["all"]["clearing_plus_ignited_union_area_m2"] == 3
    b = next(b for b in value["per_building"] if b["building_id"] == "B")
    assert b["cumulative_ignited_area_m2"] == {"lower": 0, "upper": 1}
    assert b["shared_roof_covered_area_m2"] == 1
    assert value["fuel"]["planned_removed_kg"] == 20
    assert value["fuel"]["fire_depleted_kg"] == 5


def test_immutable_component_owners_resolve_slivers_without_point_membership_guessing():
    layout, result, inventory = adapter_case()
    layout.component_id = ("cell7:A:0/local0", "cell8:A+B:1/local2/local0", "cell9:B:0")
    layout.cell_id = np.array([7, 8, 9])
    # Floating-point sliver representative points can fall outside their source
    # polygon after archival rounding. Immutable topology remains authoritative.
    layout.xy_m[:] = 1e30
    value = fire_loss_from_result(layout, result, inventory, demolished_ids=["A"])
    b = next(b for b in value["per_building"] if b["building_id"] == "B")
    assert b["cumulative_ignited_area_m2"] == {"lower": 0, "upper": 1}
    assert value["ownership_reconstruction"]["methods_node_count"]["immutable_component_id"] == 3
    assert value["ownership_reconstruction"]["methods_node_count"]["geometry"] == 0


@pytest.mark.parametrize(
    ("component", "message"),
    [
        ("cell99:A+B:0", "physical parent cell"),
        ("cell8:A+unknown:0", "invalid roof owners"),
        ("cell8:A+A:0", "invalid roof owners"),
        ("cell8:A:0", "retained owners conflict"),
    ],
)
def test_recognized_component_corruption_is_not_repaired_with_geometry(component, message):
    layout, result, inventory = adapter_case()
    layout.component_id = ("cell7:A:0", component, "cell9:B:0")
    layout.cell_id = [7, 8, 9]
    with pytest.raises(ValueError, match=message):
        fire_loss_from_result(layout, result, inventory, demolished_ids=["A"])


def test_unknown_component_schema_uses_verified_geometry_fallback():
    layout, result, inventory = adapter_case()
    layout.component_id = ("legacy-node-a", "legacy-node-b", "legacy-node-c")
    layout.cell_id = [7, 8, 9]
    value = fire_loss_from_result(layout, result, inventory, demolished_ids=["A"])
    assert value["ownership_reconstruction"]["methods_node_count"]["geometry"] == 3


def test_unrecognized_component_and_unresolvable_point_remain_an_error():
    layout, result, inventory = adapter_case()
    layout.component_id = ("external-a", "external-b", "external-c")
    layout.cell_id = [7, 8, 9]
    layout.xy_m[:] = 1e30
    with pytest.raises(ValueError, match="cannot be recovered"):
        fire_loss_from_result(layout, result, inventory, demolished_ids=["A"])


def test_no_active_fire_is_a_time_specific_observation_not_structural_safety():
    value = report(burnout_time_s=[5, 15, 40, 25, np.nan], observation_time_s=40)
    assert value["fire_extinguished_at_observation"]
    assert value["real_destroyed_building_count"] is None
    assert value["common_retained_annotation_counts"] is None


def test_actual_roof_engine_result_uses_preserved_baseline_and_native_fuel_budget():
    from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
    from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan

    path = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("fire_loss_synthetic", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, scenario = fixture.make_case()
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    record, layout, result = engine.simulate(
        scenario,
        BuildingDemolitionPlan("remove-M", ("M",), ground_evidence="synthetic_inert_ground"),
    )
    value = fire_loss_from_result(
        layout,
        result,
        inventory,
        demolished_ids=("M",),
        source_ids=record.metrics["actual_source_ids"],
    )
    assert value["physical"]["roof"]["cleared_covered_area_m2"] == pytest.approx(4)
    assert value["fuel"]["fire_depleted_kg"] == pytest.approx(result.consumed_fuel_kg)
    assert value["fuel"]["planned_removed_kg"] > 0
    assert value["saved_final_time_s"] == 1200
    assert value["real_destroyed_building_count"] is None
    json.dumps(value, allow_nan=False)


def shared_original_loss(
    removed=(), *, areas=(10,), owners=(("A", "B"),), cleared=None, ignition=(0,), burnout=(5,)
):
    return summarize_fire_loss(
        covered_area_m2=areas,
        node_owners=owners,
        ignition_time_s=ignition,
        burnout_time_s=burnout,
        observation_time_s=10,
        final_time_s=10,
        inventory_ids=("A", "B"),
        demolished_ids=removed,
        cleared_node_mask=cleared,
    )


def test_zero_area_shared_label_removal_cannot_improve_original_physical_fire_count():
    before = shared_original_loss()
    after = shared_original_loss(("A",))
    for result in (before, after):
        counts = result["physical_remaining_roof_annotation_counts"]
        assert counts["ignited_annotation_attribution_count"] == {"lower": 0, "upper": 2}
        assert counts["burned_out_annotation_attribution_count"] == {"lower": 0, "upper": 2}
        assert counts["remaining_original_owner_count"] == 2
        assert counts["fully_cleared_ids"] == []
        assert counts["partially_cleared_ids"] == []
    assert after["annotation_counts"]["retained_inventory_count"] == 1
    assert after["demolished_annotation_count"] == 1
    assert before["physical"] == after["physical"]


def test_clearing_exclusive_roof_does_not_erase_burned_shared_roof_attribution():
    result = shared_original_loss(
        ("A",),
        areas=(4, 6),
        owners=(("A",), ("A", "B")),
        cleared=(True, False),
        ignition=(np.nan, 0),
        burnout=(np.nan, 5),
    )
    counts = result["physical_remaining_roof_annotation_counts"]
    assert counts["partially_cleared_ids"] == ["A"]
    assert counts["fully_cleared_ids"] == []
    assert counts["burned_out_annotation_attribution_count"] == {"lower": 0, "upper": 2}
    a = next(row for row in counts["per_original_owner"] if row["building_id"] == "A")
    assert a["original_covered_area_m2"] == 10
    assert a["remaining_covered_area_m2"] == 6
    assert a["burned_out_area_m2"] == {"lower": 0, "upper": 6}


def test_only_complete_physical_clearance_removes_original_owners_from_fire_denominator():
    result = shared_original_loss(
        ("A", "B"), cleared=(True,), ignition=(np.nan,), burnout=(np.nan,)
    )
    counts = result["physical_remaining_roof_annotation_counts"]
    assert counts["fully_cleared_ids"] == ["A", "B"]
    assert counts["remaining_original_owner_count"] == 0
    assert counts["ignited_annotation_attribution_count"] == {"lower": 0, "upper": 0}
    assert counts["burned_out_annotation_attribution_count"] == {"lower": 0, "upper": 0}


def test_original_owner_counts_keep_ignition_and_burnout_distinct():
    result = shared_original_loss(burnout=(np.nan,))
    counts = result["physical_remaining_roof_annotation_counts"]
    assert counts["ignited_annotation_attribution_count"]["upper"] == 2
    assert counts["burned_out_annotation_attribution_count"]["upper"] == 0
    assert not result["fire_extinguished_at_observation"]
