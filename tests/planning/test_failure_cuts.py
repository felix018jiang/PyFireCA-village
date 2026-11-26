"""Strict pruning: complete nonmonotone tables and native two-scale CA."""

import importlib.util
import itertools
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.building_search import ProtectionCheck, exact_demolition_search
from pyfireca.planning.failure_cuts import FailureCutStore, cold_removal_certificate
from pyfireca.planning.heritage import content_hash


def cut(problem, a, h, scenario="s"):
    c = {
        "schema_version": "heritage-cold-removal-cut-1",
        "problem_sha256": problem,
        "scenario_id": scenario,
        "required_demolished_ids": sorted(a),
        "required_retained_ids": sorted(h),
    }
    c["certificate_sha256"] = content_hash(c)
    return c


def test_cut_search_matches_complete_nonmonotone_tables_and_budget_bounds():
    for seed in range(20):
        rng = np.random.default_rng(seed)
        areas = {str(i): float(i + 1) for i in range(7)}
        table = {}
        for k in range(8):
            for ids in itertools.combinations(areas, k):
                table[ids] = bool(rng.integers(2))
        certificates = [cut("p", (), ("1", "3")), cut("p", ("1",), ("4", "6"))]
        for c in certificates:
            a, h = set(c["required_demolished_ids"]), set(c["required_retained_ids"])
            for ids in table:
                if a <= set(ids) and not h & set(ids):
                    table[ids] = False
        table[tuple(areas)] = True
        store = FailureCutStore("p", areas)
        for c in certificates:
            store.learn(
                tuple(c["required_demolished_ids"]),
                "s",
                ProtectionCheck("failed", {"failure_certificate": c}),
            )

        def evaluate(ids, _scenario, table=table):
            return ProtectionCheck("passed" if table[ids] else "failed", {})

        result = exact_demolition_search(
            areas, ("s",), evaluate, problem_sha256="p", failure_cuts=store
        )
        expected = min(
            (ids for ids, ok in table.items() if ok),
            key=lambda ids: (len(ids), sum(areas[i] for i in ids)),
        )
        assert result["best_ids"] == list(expected) and result["optimality_proven"]
        assert any(layer["failure_cut_pruned_combinations"] for layer in result["layers"])
        limited = exact_demolition_search(
            areas,
            ("s",),
            evaluate,
            problem_sha256="p",
            initial_ids=tuple(areas),
            max_new_checks=3,
            failure_cuts=store,
        )
        assert limited["minimum_count_lower_bound"] <= len(expected)
        if limited["optimality_proven"]:
            assert limited["best_ids"] == list(expected)


def physical_oracle(**kwargs):
    p = Path(__file__).with_name("test_roof_exact_search.py")
    spec = importlib.util.spec_from_file_location("cold_cut_fixture", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.synthetic_oracle(**kwargs)


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_every_certified_cold_superset_preserves_core_failure_and_retained_arrays(scale):
    from pyfireca.planning.evaluation import EvaluationScenario
    from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
    from pyfireca.planning.roof_ignition import footprint_ignitions

    oracle = physical_oracle()
    oracle.enable_failure_certificates = True
    scenario_id = f"synthetic-h{scale:g}"
    check = oracle((), scenario_id)
    assert check.status == "failed"
    cert = check.evidence["failure_certificate"]
    assert "M" in cert["required_retained_ids"] and "U" in cert["extra_removal_eligible_ids"]
    engine = oracle._engine
    ignition = replace(
        engine.layout,
        ignition_owner=tuple(
            i if i == check.evidence["source_id"] else None for i in engine.layout.ignition_owner
        ),
    )
    scenario = EvaluationScenario(scenario_id, footprint_ignitions(ignition), 1200)
    baseline, _, reference = engine.simulate(
        scenario, BuildingDemolitionPlan("none"), stop_at_first_core_ignition=True
    )
    extra = cert["extra_removal_eligible_ids"]
    for k in range(len(extra) + 1):
        for ids in itertools.combinations(extra, k):
            record, layout, result = engine.simulate(
                scenario,
                BuildingDemolitionPlan(
                    "cold", ids, overlap_policy="retain_shared_roof", ground_evidence="synthetic"
                ),
                stop_at_first_core_ignition=True,
            )
            assert record.metrics["core_outcomes"] == baseline.metrics["core_outcomes"]
            assert result.final_state.time_s == reference.final_state.time_s
            retained = np.asarray(
                [not (set(owners) and set(owners) <= set(ids)) for owners in layout.owners]
            )
            for name in reference.final_state.__dataclass_fields__:
                value, old = getattr(result.final_state, name), getattr(reference.final_state, name)
                if isinstance(value, np.ndarray):
                    np.testing.assert_array_equal(value.ravel()[retained], old.ravel()[retained])


def test_cold_nodes_that_limit_timestep_are_retained_in_certificate():
    oracle = physical_oracle()
    check = oracle((), "synthetic-h1")
    engine = oracle._engine
    from pyfireca.planning.evaluation import EvaluationScenario
    from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
    from pyfireca.planning.roof_ignition import footprint_ignitions

    ignition = replace(
        engine.layout,
        ignition_owner=tuple(
            i if i == check.evidence["source_id"] else None for i in engine.layout.ignition_owner
        ),
    )
    scenario = EvaluationScenario("synthetic-h1", footprint_ignitions(ignition), 1200)
    _, layout, result = engine.simulate(
        scenario, BuildingDemolitionPlan("none"), stop_at_first_core_ignition=True
    )
    physics = layout.node_request.parameters
    cp = physics.specific_heat_J_kg_K.copy()
    cp.ravel()[layout.nodes("U")] = 0.001
    proof_layout = replace(
        layout,
        node_request=replace(
            layout.node_request, parameters=replace(physics, specific_heat_J_kg_K=cp)
        ),
    )
    cert = cold_removal_certificate(
        proof_layout,
        result,
        engine.last_contact,
        oracle.inventory,
        oracle.transport,
        demolished_ids=(),
        scenario_id="synthetic-h1",
        problem_sha256=oracle.problem_sha256,
        source_id=check.evidence["source_id"],
    )
    assert "U" in cert["required_retained_ids"]
    assert cert["timestep_limiting_cold_nodes_guarded"]


def test_no_feasible_plan_inside_bound_is_not_global_infeasibility():
    result = exact_demolition_search(
        {"a": 1, "b": 2, "c": 3},
        ("s",),
        lambda ids, s: ProtectionCheck("passed" if len(ids) == 3 else "failed", {}),
        problem_sha256="p",
        maximum_demolitions=2,
    )
    assert result["status"] == "bounded_infeasible"
    assert result["minimum_count_lower_bound"] == 3 and not result["optimality_proven"]


def test_global_source_fallback_preserves_original_anchor_and_never_becomes_no_fire():
    from shapely.geometry import box

    from pyfireca.planning.heritage import BuildingInventory, BuildingObject, HeritageTarget
    from pyfireca.planning.random_sources import freeze_source_protocol, realize_sources
    from pyfireca.planning.retained_sources import with_global_fallback

    inv = BuildingInventory(
        tuple(
            BuildingObject(i, n, "building", g)
            for n, (i, g) in enumerate(
                [
                    ("S", box(0, 0, 5, 5)),
                    ("T", box(8, 0, 13, 5)),
                    ("U", box(20, 0, 25, 5)),
                    ("G", box(30, 0, 32, 2)),
                ]
            )
        ),
        (HeritageTarget("G", "gulou", "synthetic", "synthetic"),),
        candidate_ids=("S", "T", "U"),
        candidate_evidence="synthetic",
    )
    original = freeze_source_protocol(
        inv, seed=17, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    original.update(
        strata={"preferred": ["S"]},
        draws=[
            {
                "draw_id": "preferred-001",
                "stratum": "preferred",
                "priority_ids": ["S"],
                "baseline_source_id": "S",
            }
        ],
    )
    original.pop("protocol_sha256")
    original["protocol_sha256"] = content_hash(original)
    extended = with_global_fallback(original, inv, fallback_seed=99)
    a, b = realize_sources(original, inv)[0], realize_sources(extended, inv)[0]
    assert a["source_id"] == b["source_id"] == "S"
    assert a["footprint"].to_mapping() == b["footprint"].to_mapping()
    replacement = realize_sources(extended, inv, demolished_ids=("S",))[0]
    assert replacement["status"] == "fire" and replacement["source_id"] in {"T", "U"}
    assert replacement["footprint"].geometry.area == pytest.approx(25)


def test_explicit_heritage_priority_resolves_shared_atom_without_discarding_fuel():
    from shapely.geometry import box

    from pyfireca.planning.roof_thermal import resolve_roof_layout

    p = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("priority_fixture", p)
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
    bundle, _, inv, _ = f.make_case(
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 10, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    with pytest.raises(ValueError, match="semantic review"):
        resolve_roof_layout(bundle.request, inv)
    layout = resolve_roof_layout(bundle.request, inv, core_overlap_policy="heritage_priority")
    assert not set(layout.nodes("G")) & set(layout.nodes("M"))
    assert all(layout.owners[i] == ("G",) for i in layout.nodes("G"))
    for field in layout.conservation.values():
        assert field < 1e-5


def test_live_cut_preserves_area_order_and_every_unexcluded_combination():
    from math import fsum

    from pyfireca.planning.building_search import ordered_demolition_sets

    for seed in range(10):
        rng = np.random.default_rng(seed)
        areas = {str(i): float(v) for i, v in enumerate(rng.uniform(0.1, 100, 8))}
        store = FailureCutStore("p", areas)
        iterator = ordered_demolition_sets(areas, 3, branch_bound=store.bound)
        seen = [next(iterator)[0] for _ in range(5)]
        c = cut("p", (), ("1", "7"))
        store.learn((), "s", ProtectionCheck("failed", {"failure_certificate": c}))
        rest = list(iterator)
        expected = {
            tuple(p) for p in itertools.combinations(sorted(areas), 3) if set(p) & {"1", "7"}
        } - set(seen)
        assert {ids for ids, value in rest} == expected
        assert [value for ids, value in rest] == sorted(
            fsum(areas[i] for i in ids) for ids, value in rest
        )


def test_confirmation_observes_entire_horizon_even_after_core_failure():
    oracle = physical_oracle(stop_at_first_core_ignition=False)
    check = oracle((), "synthetic-h1")
    assert check.status == "failed" and check.evidence["reached_horizon"]
    assert check.evidence["simulated_until_s"] == 1200
    assert oracle.problem["early_stop"] == "full_horizon"


def test_parent_graph_uses_declared_engine_capacity(monkeypatch):
    from pyfireca.planning.engines import roof_heritage

    original = roof_heritage.build_v3_radiative_transport_edges
    observed = []

    def record_capacity(*args, **kwargs):
        observed.append(kwargs["max_edge_count"])
        return original(*args, **kwargs)

    monkeypatch.setattr(roof_heritage, "build_v3_radiative_transport_edges", record_capacity)
    oracle = physical_oracle(max_radiation_edges=60000000)
    check = oracle((), "synthetic-h1")
    assert check.status == "failed"
    assert observed == [60000000]
