"""Shared-owner metadata must not change retained physical radiation kernels."""

import importlib.util
from functools import partial
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.roof_thermal import (
    resolve_roof_layout,
    roof_area_integrated_radiation_graph,
)


def case(*, exclusive=False):
    path = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("frozen_shared_integration", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.make_case(
        radius=8,
        ignition_temperature_K=350,
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 9, 10),
            "U": box(8, 8, 9, 10) if exclusive else box(7, 8, 9, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
        },
    )


def engine_case(*, exclusive=False, frozen=True):
    bundle, transport, inventory, scenario = case(exclusive=exclusive)
    base = resolve_roof_layout(bundle.request, inventory)
    selected = frozenset(("S", "M", "G", "F"))
    options = {}
    if frozen:
        options["frozen_integrated_node_mask"] = np.asarray(
            [bool(selected.intersection(ids)) for ids in base.owners]
        )
    engine = RoofHeritageEvaluationEngine(
        bundle,
        transport,
        inventory,
        source_policy="declared_fixed_sources",
        prepared_layout=base,
        radiation_builder=partial(
            roof_area_integrated_radiation_graph,
            integrated_ids=selected,
            quadrature_spacing_m=0.5,
            **options,
        ),
    )
    return engine, scenario


def remove_m():
    return BuildingDemolitionPlan(
        "remove-shared-label",
        ("M",),
        overlap_policy="retain_shared_roof",
        ground_evidence="synthetic",
    )


def test_wholly_shared_selected_owner_removal_preserves_graph_fuel_and_thermal_trajectory():
    engine, scenario = engine_case()
    base_layout, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("none"))
    removed_layout, _, ledger = engine.prepare(scenario, remove_m())
    assert ledger["cleared_roof_union_area_m2"] == 0
    assert ledger["removed_dry_fuel_kg"] == 0
    assert all("M" not in labels for labels in removed_layout.owners)
    for name in base_layout.thermal.parameters.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(base_layout.thermal.parameters, name),
            getattr(removed_layout.thermal.parameters, name),
        )
    for name in ("source", "target", "weight"):
        np.testing.assert_array_equal(
            getattr(base_layout.thermal.exposure_edges, name),
            getattr(removed_layout.thermal.exposure_edges, name),
        )
    base_record, _, baseline = engine.simulate(scenario, BuildingDemolitionPlan("none"))
    removed_record, _, removed = engine.simulate(scenario, remove_m())
    assert base_record.metrics["core_outcomes"] == removed_record.metrics["core_outcomes"]
    assert any(row["ignited"] for row in base_record.metrics["core_outcomes"])
    for name in baseline.final_state.__dataclass_fields__:
        left, right = getattr(baseline.final_state, name), getattr(removed.final_state, name)
        if isinstance(left, np.ndarray):
            np.testing.assert_array_equal(left, right)
        else:
            assert left == right
    np.testing.assert_array_equal(baseline.ignition_cause, removed.ignition_cause)


def test_exclusive_fuel_removal_does_not_switch_shared_retained_patch_kernel():
    engine, scenario = engine_case(exclusive=True)
    baseline, _, _ = engine.prepare(scenario, BuildingDemolitionPlan("none"))
    removed, _, ledger = engine.prepare(scenario, remove_m())
    assert ledger["cleared_roof_union_area_m2"] > 0
    mask = removed.thermal.burnable_mask.ravel()

    def retained_edges(graph):
        return {
            (int(source), int(target)): float(weight)
            for source, target, weight in zip(graph.source, graph.target, graph.weight, strict=True)
            if mask[source] and mask[target]
        }

    expected = retained_edges(baseline.thermal.exposure_edges)
    assert retained_edges(removed.thermal.exposure_edges) == expected
    # The historical owner-dependent builder remains opt-in unchanged, and
    # exposes the prior shared-node integration switch on this exact geometry.
    historical = roof_area_integrated_radiation_graph(
        removed, engine.transport, integrated_ids=("S", "M", "G", "F"), quadrature_spacing_m=0.5
    )
    assert retained_edges(historical) != expected


def test_default_builder_keeps_historical_selected_identity_validation():
    engine, scenario = engine_case(frozen=False)
    with pytest.raises(ValueError, match="known selected roof IDs"):
        engine.prepare(scenario, remove_m())


@pytest.mark.parametrize("kind", ["integer", "shape"])
def test_frozen_mask_requires_exact_boolean_node_membership(kind):
    engine, _ = engine_case()
    count = len(engine.layout.owners)
    bad = np.ones(count, dtype=int) if kind == "integer" else np.ones(count + 1, dtype=bool)
    with pytest.raises(ValueError, match="boolean and match physical nodes"):
        roof_area_integrated_radiation_graph(
            engine.layout,
            engine.transport,
            integrated_ids=("M",),
            frozen_integrated_node_mask=bad,
        )


def test_oracle_option_is_hashed_and_freezes_the_unmodified_engine_layout():
    bundle, transport, inventory, _ = case()
    protocol = freeze_source_protocol(
        inventory, seed=20261110, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    arguments = {
        "candidate_ids": ("M", "U"),
        "refinement_ids": ("M",),
        "fires": [{"fire_id": "fixed", "protocol": protocol}],
        "code_version": "frozen-membership-test",
        "horizon_s": 1,
    }
    historical = RoofDemolitionOracle(bundle, transport, inventory, **arguments)
    explicit_false = RoofDemolitionOracle(
        bundle, transport, inventory, **arguments, freeze_integration_membership=False
    )
    frozen = RoofDemolitionOracle(
        bundle, transport, inventory, **arguments, freeze_integration_membership=True
    )
    assert historical.problem_sha256 == explicit_false.problem_sha256
    assert "freeze_integration_membership" not in historical.problem
    assert frozen.problem_sha256 != historical.problem_sha256
    assert frozen.problem["freeze_integration_membership"]
    check = frozen(("M",), "fixed-h1")
    assert check.status in {"passed", "failed"}
    engine = frozen._engine
    mask = engine.radiation_builder.keywords["frozen_integrated_node_mask"]
    selected = frozenset(frozen.fires["fixed"]["refined_ids"])
    expected = np.asarray([bool(selected.intersection(ids)) for ids in engine.layout.owners])
    np.testing.assert_array_equal(mask, expected)
    assert not mask.flags.writeable
