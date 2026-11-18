"""Real CA oracle, frozen timber parameters, 25 m2 fire, and both roof scales."""

import importlib.util
import itertools
import json
from dataclasses import asdict, replace
from math import fsum
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.planning.building_search import exact_demolition_search
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.random_sources import freeze_source_protocol, realize_sources
from pyfireca.planning.roof_ignition import refine_roof_layout
from pyfireca.village.workflow_v3 import write_v3_case_bundle


def synthetic_oracle(**kwargs):
    path = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("exact_synthetic", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _ = fixture.make_case(
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 9, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    protocol = freeze_source_protocol(
        inventory,
        seed=20261024,
        draws_per_stratum=1,
        area_m2=25,
        near_distance_m=15,
    )
    return RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=("M", "U"),
        fires=[{"fire_id": "synthetic", "protocol": protocol}],
        code_version="test-physical",
        **kwargs,
    )


def test_physical_exact_search_matches_all_subsets_and_scales():
    oracle = synthetic_oracle()
    areas = {i: oracle.inventory.by_id[i].geometry.area for i in oracle.candidate_ids}
    checks = {}

    def evaluate(ids, scenario):
        check = oracle(ids, scenario)
        checks[ids, scenario] = check
        return check

    search = exact_demolition_search(
        areas,
        tuple(oracle.scenarios),
        evaluate,
        problem_sha256=oracle.problem_sha256,
        initial_ids=("M", "U"),
    )
    table = []
    for count in range(3):
        for ids in itertools.combinations(sorted(areas), count):
            rows = [
                checks.get((ids, scenario)) or evaluate(ids, scenario)
                for scenario in oracle.scenarios
            ]
            assert all(check.status in {"passed", "failed"} for check in rows)
            table.append((ids, all(check.status == "passed" for check in rows)))
    expected = min(
        (ids for ids, passed in table if passed),
        key=lambda ids: (len(ids), fsum(areas[i] for i in ids)),
    )
    assert expected == ("M",)
    assert search["optimality_proven"] and search["best_ids"] == ["M"]
    for ids in ((), ("U",)):
        assert all(checks[ids, scenario].status == "failed" for scenario in oracle.scenarios)
    for check in checks.values():
        assert check.evidence["source_budget"]["covered_area_m2"] == pytest.approx(25)
        if check.status == "passed":
            assert check.evidence["reached_horizon"]
    assert {c.evidence["scale_m"] for c in checks.values()} == {1.0, 0.5}


def test_saved_entry_resumes_without_ca_and_rejects_changed_problem(tmp_path):
    oracle = synthetic_oracle()
    write_v3_case_bundle(
        oracle.bundle.request,
        tmp_path / "case",
        material_labels=oracle.bundle.material_labels,
        landcover_labels=oracle.bundle.landcover_labels,
        metadata=oracle.bundle.metadata,
    )

    def save(name, value):
        (tmp_path / name).write_text(json.dumps(value))

    save("structures.json", {"buildings": oracle.inventory.structures})
    save(
        "study.json",
        {
            "schema_version": "heritage-p1-study-1",
            "case_dir": "case",
            "structures_path": "structures.json",
            "targets": [asdict(t) for t in oracle.inventory.targets],
            "fixed_source_ids": oracle.inventory.fixed_source_ids,
            "candidate_ids": oracle.inventory.candidate_ids,
            "candidate_evidence": oracle.inventory.candidate_evidence,
        },
    )
    protocol = oracle.fires["synthetic"]["protocol"]
    save("source.json", protocol)
    config = {
        "schema_version": "heritage-exact-search-config-1",
        "study_path": "study.json",
        "candidate_ids": ["M", "U"],
        "initial_ids": ["M", "U"],
        "horizon_s": 1200,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.5,
        "max_nodes": 200000,
        "max_radiation_edges": 20000000,
        "minimum_free_disk_GB": 0,
        "max_new_checks": 8,
        "fires": [
            {
                "fire_id": "synthetic",
                "protocol_path": "source.json",
                "draw_id": protocol["draws"][0]["draw_id"],
            }
        ],
    }
    save("search.json", config)
    path = Path(__file__).resolve().parents[2] / "scripts/run_heritage_exact_search.py"
    spec = importlib.util.spec_from_file_location("exact_entry", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    first = runner.run(tmp_path / "search.json", tmp_path / "run")
    resumed = runner.run(tmp_path / "search.json", tmp_path / "run", resume=True)
    assert first["optimality_proven"] and first["new_checks"] > 0
    assert resumed["new_checks"] == 0 and resumed["best_ids"] == first["best_ids"]
    save("search.json", {**config, "horizon_s": 1201})
    with pytest.raises(ValueError, match="cannot resume"):
        runner.run(tmp_path / "search.json", tmp_path / "run", resume=True)
    save("search.json", {**config, "max_total_checks": 3})
    bounded = runner.run(tmp_path / "search.json", tmp_path / "bounded")
    again = runner.run(tmp_path / "search.json", tmp_path / "bounded", resume=True)
    assert bounded["status"] == again["status"] == "incomplete"
    assert bounded["new_checks"] == 3 and again["new_checks"] == 0
    assert len(list((tmp_path / "bounded/evaluations").glob("*/check.json"))) == 3


def test_problem_signature_binds_transport_parameters():
    oracle = synthetic_oracle()
    changed = RoofDemolitionOracle(
        oracle.bundle,
        replace(oracle.transport, atmospheric_extinction_m_inv=0.01),
        oracle.inventory,
        candidate_ids=oracle.candidate_ids,
        fires=[{"fire_id": "synthetic", "protocol": oracle.fires["synthetic"]["protocol"]}],
        code_version=oracle.code_version,
    )
    assert changed.problem_sha256 != oracle.problem_sha256


def test_explicit_refinement_does_not_grow_with_irrelevant_candidate_eligibility():
    oracle = synthetic_oracle(refinement_ids=("M",), maximum_demolitions=1)
    other = RoofDemolitionOracle(
        oracle.bundle,
        oracle.transport,
        oracle.inventory,
        candidate_ids=("M",),
        fires=[{"fire_id": "synthetic", "protocol": oracle.fires["synthetic"]["protocol"]}],
        code_version=oracle.code_version,
        refinement_ids=("M",),
        maximum_demolitions=1,
    )
    for scale in oracle.scales_m:
        layouts = [
            refine_roof_layout(
                o.base,
                o.inventory,
                cell_size_m=scale,
                refined_ids=o.fires["synthetic"]["refined_ids"],
                footprints=tuple(o.fires["synthetic"]["footprints"].values()),
            )
            for o in (oracle, other)
        ]
        assert layouts[0].owners == layouts[1].owners
        np.testing.assert_array_equal(layouts[0].xy_m, layouts[1].xy_m)
    with pytest.raises(ValueError, match="coverage bound"):
        oracle(("M", "U"), next(iter(oracle.scenarios)))


def test_strict_identity_proof_matches_physics_and_rejects_positive_exclusive_patch():
    path = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("identity_synthetic", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)

    def make(exclusive_width):
        bundle, transport, inventory, _ = fixture.make_case(
            geometries={
                "S": box(2, 8, 7, 13),
                "M": box(7, 8, 9, 10),
                "G": box(9, 8, 11, 10),
                "F": box(9, 6, 11, 8),
                "U": box(14, 12, 16, 14),
                "duplicate": box(14, 12, 16 + exclusive_width, 14),
            },
            candidate_ids=("M", "duplicate"),
        )
        protocol = freeze_source_protocol(
            inventory, seed=20261024, draws_per_stratum=1, area_m2=25, near_distance_m=15
        )
        return RoofDemolitionOracle(
            bundle,
            transport,
            inventory,
            candidate_ids=("M", "duplicate"),
            fires=[{"fire_id": "synthetic", "protocol": protocol}],
            refinement_ids=("M",),
            maximum_demolitions=2,
            code_version="identity-test",
        )

    oracle = make(0)
    assert set(oracle.proven_dominated_candidates()) == {"duplicate"}
    captures = []
    oracle.on_evaluation = lambda *args: captures.append(args)
    for scale in oracle.scales_m:
        scenario = f"synthetic-h{scale:g}"
        keep = oracle(("M",), scenario)
        remove = oracle(("M", "duplicate"), scenario)
        assert keep.status == remove.status
        assert keep.evidence["core_outcomes"] == remove.evidence["core_outcomes"]
        a, b = captures[-2:]
        for field in a[4].thermal.parameters.__dataclass_fields__:
            np.testing.assert_array_equal(
                getattr(a[4].thermal.parameters, field), getattr(b[4].thermal.parameters, field)
            )
        for field in ("source", "target", "weight"):
            np.testing.assert_array_equal(
                getattr(a[4].thermal.exposure_edges, field),
                getattr(b[4].thermal.exposure_edges, field),
            )
        for field in a[5].final_state.__dataclass_fields__:
            np.testing.assert_equal(
                getattr(a[5].final_state, field), getattr(b[5].final_state, field)
            )
    # No tolerance is allowed to discard a genuinely positive exclusive area.
    assert "duplicate" not in make(1e-4).proven_dominated_candidates()


def test_bounded_possible_sources_cover_every_admissible_plan():
    oracle = synthetic_oracle()
    # Put both removable roofs before the fixed source; at most one removal
    # means only the first two can become the actual source.
    protocol = json.loads(json.dumps(oracle.fires["synthetic"]["protocol"]))
    draw = protocol["draws"][0]
    draw["priority_ids"] = ["M", "U", "S"]
    draw["baseline_source_id"] = "M"
    # Each roof is large enough for the tiny geometry-only source used here.
    protocol["ignition_area_m2"] = 1
    from pyfireca.planning.heritage import content_hash

    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    bounded = RoofDemolitionOracle(
        oracle.bundle,
        oracle.transport,
        oracle.inventory,
        candidate_ids=("M", "U"),
        fires=[{"fire_id": "bounded", "protocol": protocol}],
        refinement_ids=(),
        maximum_demolitions=1,
        code_version="bounded-test",
    )
    assert set(bounded.fires["bounded"]["footprints"]) == {"M", "U"}
    for ids in ((), ("M",), ("U",)):
        row = realize_sources(protocol, oracle.inventory, demolished_ids=ids)[0]
        assert row["source_id"] in bounded.fires["bounded"]["footprints"]
