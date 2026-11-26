"""Real roof-oracle coverage and pre-solve guards for retained technical sources."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import box

import pyfireca.planning.engines.roof_search as oracle_module
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol

ROOT = Path(__file__).resolve().parents[2]


def inputs(*, fixed_middle=False):
    spec = importlib.util.spec_from_file_location(
        "source_area_synthetic", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _ = fixture.make_case(
        radius=2,
        geometries={
            "S": box(2, 8, 4, 10),
            "M": box(6, 8, 8, 10),
            "G": box(10, 8, 12, 10),
            "F": box(20, 2, 22, 4),
            "U": box(16, 12, 18, 14),
        },
    )
    inventory = replace(
        inventory,
        fixed_source_ids=("M",) if fixed_middle else (),
        candidate_ids=("S", "U") if fixed_middle else ("S", "M", "U"),
    )
    protocol = freeze_source_protocol(
        inventory, seed=0, draws_per_stratum=1, area_m2=4, near_distance_m=30
    )
    draw = next(d for d in protocol["draws"] if "S" in d["priority_ids"])
    assert set(draw["priority_ids"]) == {"S", "M", "U"}
    draw["priority_ids"], draw["baseline_source_id"] = ["S", "M", "U"], "S"
    protocol["draws"] = [draw]
    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    return bundle, transport, inventory, [{"fire_id": "fire", "protocol": protocol}]


def oracle(*, limit=None, tolerance=0, maximum=None, fixed_middle=False, callback=None):
    bundle, transport, inventory, fires = inputs(fixed_middle=fixed_middle)
    return RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=inventory.candidate_ids,
        refinement_ids=("M",),
        fires=fires,
        code_version="source-area-test",
        source_area_limit_m2=limit,
        source_area_tolerance_m2=tolerance,
        maximum_demolitions=maximum,
        on_evaluation=callback,
        stop_at_first_core_ignition=False,
        freeze_integration_membership=True,
    )


def test_area_prefix_is_not_truncated_by_a_count_bound_and_default_hash_is_unchanged():
    area_bounded = oracle(limit=4, maximum=0)
    legacy = oracle(maximum=0)
    explicit_default = oracle(limit=None, tolerance=0, maximum=0)
    assert list(area_bounded.fires["fire"]["footprints"]) == ["S", "M"]
    assert list(legacy.fires["fire"]["footprints"]) == ["S"]
    assert "source_area_coverage" not in legacy.problem
    assert legacy.problem_sha256 == explicit_default.problem_sha256
    assert area_bounded.problem_sha256 != legacy.problem_sha256
    assert area_bounded.problem["source_area_coverage"]["fires"]["fire"]["source_ids"] == ["S", "M"]
    with pytest.raises(ValueError, match=r"plan exceeds.*bound"):
        area_bounded(("S",), "fire-h1")


def test_over_area_plan_is_rejected_before_any_graph_or_ca(monkeypatch):
    bounded = oracle(limit=4)

    def forbidden(*args, **kwargs):
        raise AssertionError("out-of-area plans must not prepare an engine")

    monkeypatch.setattr(oracle_module, "RoofHeritageEvaluationEngine", forbidden)
    with pytest.raises(ValueError, match="source-coverage area cap"):
        bounded(("S", "M"), "fire-h1")
    assert bounded._engine is None


def test_same_tolerance_admits_backup_source_and_is_hashed():
    unbuffered = oracle(limit=3)
    buffered = oracle(limit=3, tolerance=1)
    assert list(unbuffered.fires["fire"]["footprints"]) == ["S"]
    assert list(buffered.fires["fire"]["footprints"]) == ["S", "M"]
    assert buffered.source_area_coverages["fire"].validate_plan(("S",))["source_id"] == "M"
    assert buffered.problem_sha256 != unbuffered.problem_sha256
    with pytest.raises(ValueError, match="source-coverage area cap"):
        unbuffered(("S",), "fire-h1")


def test_full_priority_empty_source_returns_unknown_without_solving(monkeypatch):
    bounded = oracle(limit=20)
    assert list(bounded.fires["fire"]["footprints"]) == ["S", "M", "U"]

    def forbidden(*args, **kwargs):
        raise AssertionError("empty retained sources must not create a no-fire simulation")

    monkeypatch.setattr(oracle_module, "RoofHeritageEvaluationEngine", forbidden)
    value = bounded(("S", "M", "U"), "fire-h1")
    assert value.status == "unknown"
    assert value.evidence["reason"] == "no_retained_source_in_frozen_draw"


def test_fixed_middle_source_ends_area_coverage():
    bounded = oracle(limit=100, fixed_middle=True)
    assert list(bounded.fires["fire"]["footprints"]) == ["S", "M"]
    assert (
        bounded.source_area_coverages["fire"].coverage_reason == "fixed_retained_source_in_prefix"
    )
    assert bounded.source_area_coverages["fire"].validate_plan(("S", "U"))["source_id"] == "M"


def test_real_dual_support_simulation_uses_retained_backup_on_the_same_mesh():
    saved = []

    def capture(ids, scenario, check, record, layout, result):
        saved.append((ids, scenario, check, record, layout, result))

    bounded = oracle(limit=4, callback=capture)
    for scenario in bounded.scenarios:
        first = bounded((), scenario)
        backup = bounded(("S",), scenario)
        assert first.status in {"passed", "failed"}
        assert backup.status in {"passed", "failed"}
        assert first.evidence["source_id"] == "S"
        assert backup.evidence["source_id"] == "M"
        assert backup.evidence["source_replaced"]
        assert first.evidence["reached_horizon"] and backup.evidence["reached_horizon"]
        assert first.evidence["source_budget"]["covered_area_m2"] == pytest.approx(4)
        assert backup.evidence["source_budget"]["covered_area_m2"] == pytest.approx(4)
        prior, after = saved[-2:]
        assert prior[3].diagnostics["layout_sha256"] == after[3].diagnostics["layout_sha256"]
        assert prior[4].component_id == after[4].component_id
        assert prior[5].final_state.time_s == after[5].final_state.time_s == 1200
    assert len(saved) == 4


@pytest.mark.parametrize(
    "limit,tolerance", [(None, 1), (-1, 0), (float("inf"), 0), (4, -1), (4, float("nan"))]
)
def test_invalid_area_domain_is_rejected(limit, tolerance):
    with pytest.raises(ValueError, match="source area"):
        oracle(limit=limit, tolerance=tolerance)
