"""Physical-node cuts cannot be escaped by deleting shared annotation labels."""

import importlib.util
import itertools
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.area_master import solve_area_master
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.failure_cuts import cold_removal_certificate
from pyfireca.planning.heritage import BuildingObject, content_hash
from pyfireca.planning.random_sources import freeze_source_protocol


def oracle(*, enabled=True, source_removable=False, on_evaluation=None):
    path = Path(__file__).resolve().parents[2] / "examples/research/heritage/synthetic_case.py"
    spec = importlib.util.spec_from_file_location("physical_owner_group_fixture", path)
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
    inventory = replace(
        inventory,
        buildings=(
            *inventory.buildings,
            BuildingObject("V", 5, "building", inventory.by_id["M"].geometry),
            BuildingObject("W", 6, "building", inventory.by_id["M"].geometry),
        ),
        fixed_source_ids=() if source_removable else inventory.fixed_source_ids,
        candidate_ids=("M", "U", "V", "W", "S") if source_removable else ("M", "U", "V", "W"),
    )
    protocol = freeze_source_protocol(
        inventory, seed=20261112, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    return RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=inventory.candidate_ids,
        refinement_ids=("M",),
        fires=[{"fire_id": "one", "protocol": protocol}],
        code_version="physical-node-cut-regression",
        freeze_integration_membership=True,
        enable_physical_group_cuts=enabled,
        on_evaluation=on_evaluation,
    )


def subsets(items):
    values = sorted(items)
    return [
        group for count in range(len(values) + 1) for group in itertools.combinations(values, count)
    ]


def forbidden(certificate, ids):
    removed = set(ids)
    return set(certificate["required_demolished_ids"]) <= removed and not any(
        set(group) <= removed for group in certificate["required_clearance_owner_groups"]
    )


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_every_pruned_real_ca_subset_preserves_failure_and_retained_state(scale):
    captures = {}

    def remember(ids, scenario, check, record, layout, result):
        captures[tuple(ids)] = (record, layout, result)

    physical = oracle(on_evaluation=remember)
    scenario = f"one-h{scale:g}"
    baseline = physical((), scenario)
    assert baseline.status == "failed"
    cert = baseline.evidence["failure_certificate"]
    assert cert["schema_version"] == "heritage-physical-node-removal-cut-1"
    assert ["M", "V", "W"] in cert["required_clearance_owner_groups"]
    assert forbidden(cert, ("V",)) and forbidden(cert, ("M", "V"))
    assert not forbidden(cert, ("M", "V", "W"))
    reference_record, reference_layout, reference = captures[()]
    for ids in subsets(physical.candidate_ids):
        check = baseline if not ids else physical(ids, scenario)
        record, _layout, result = captures[ids]
        if not forbidden(cert, ids):
            continue
        assert check.status == "failed"
        assert record.metrics["core_outcomes"] == reference_record.metrics["core_outcomes"]
        assert result.final_state.time_s == reference.final_state.time_s
        retained = np.asarray(
            [not (owners and set(owners) <= set(ids)) for owners in reference_layout.owners]
        )
        for name in reference.final_state.__dataclass_fields__:
            value, expected = (
                getattr(result.final_state, name),
                getattr(reference.final_state, name),
            )
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(value.ravel()[retained], expected.ravel()[retained])


def test_failed_shared_label_plan_yields_remaining_group_and_zero_cost_and_auxiliary():
    pytest.importorskip("scipy.optimize")
    physical = oracle()
    scenario = "one-h1"
    check = physical(("M",), scenario)
    assert check.status == "failed"
    cert = check.evidence["failure_certificate"]
    assert ["V", "W"] in cert["required_clearance_owner_groups"]
    assert not any("M" in group for group in cert["required_clearance_owner_groups"])
    area = build_cleared_area_function(
        physical.base.owners,
        [g.area for g in physical.base.geometry],
        area_basis="physical_geometry",
    )
    cache = {(physical.problem_sha256, ("M",), scenario): check}
    # Every plan retaining M also retains the same complete physical bridge;
    # obtain real failure witnesses before excluding these exact alternatives.
    for ids in subsets(set(physical.candidate_ids) - {"M"}):
        value = physical(ids, scenario)
        assert value.status == "failed"
        cache[physical.problem_sha256, ids, scenario] = value
    master = solve_area_master(
        physical.candidate_ids,
        area,
        (scenario,),
        problem_sha256=physical.problem_sha256,
        fixed_retained_ids=sorted(area.owner_ids - set(physical.candidate_ids)),
        cache=cache,
    )
    assert master["physical_group_failure_cut_count"] > 0
    assert master["physical_cut_auxiliary_variable_count"] > 0
    assert master["next_ids"] == ["M", "V", "W"]
    assert master["next_area_m2"] == pytest.approx(4)
    assert physical(tuple(master["next_ids"]), scenario).status == "passed"


def test_cold_timestep_limiter_is_an_explicit_physical_group_guard():
    captures = {}
    physical = oracle(on_evaluation=lambda *args: captures.update(last=args))
    check = physical((), "one-h1")
    _, _, _, _, layout, result = captures["last"]
    physics = layout.node_request.parameters
    cp = physics.specific_heat_J_kg_K.copy()
    cp.ravel()[layout.nodes("U")] = 0.001
    proof_layout = replace(
        layout,
        node_request=replace(
            layout.node_request, parameters=replace(physics, specific_heat_J_kg_K=cp)
        ),
    )
    proof = cold_removal_certificate(
        proof_layout,
        result,
        physical._engine.last_contact,
        physical.inventory,
        physical.transport,
        demolished_ids=(),
        scenario_id="one-h1",
        problem_sha256=physical.problem_sha256,
        source_id=check.evidence["source_id"],
        frozen_thermal_membership=True,
    )
    assert proof["timestep_limiting_cold_nodes_guarded"]
    assert ["U"] in proof["required_clearance_owner_groups"]


def test_source_changes_cannot_escape_without_the_physical_source_singleton():
    physical = oracle(source_removable=True)
    check = physical((), "one-h1")
    assert check.status == "failed"
    cert = check.evidence["failure_certificate"]
    assert ["S"] in cert["required_clearance_owner_groups"]
    assert not forbidden(cert, ("S",))
    assert physical(("S",), "one-h1").status == "unknown"


def test_opt_in_is_hashed_and_requires_frozen_membership_while_legacy_cert_stays_legacy():
    legacy, current = oracle(enabled=False), oracle()
    assert "enable_physical_group_cuts" not in legacy.problem
    assert current.problem["enable_physical_group_cuts"]
    assert current.problem_sha256 != legacy.problem_sha256
    legacy.enable_failure_certificates = True
    old = legacy((), "one-h1").evidence["failure_certificate"]
    assert old["schema_version"] == "heritage-cold-removal-cut-1"
    assert "required_clearance_owner_groups" not in old
    with pytest.raises(ValueError, match="require frozen"):
        RoofDemolitionOracle(
            legacy.bundle,
            legacy.transport,
            legacy.inventory,
            candidate_ids=legacy.candidate_ids,
            fires=[{"fire_id": "one", "protocol": legacy.fires["one"]["protocol"]}],
            code_version="illegal",
            enable_physical_group_cuts=True,
        )


@pytest.mark.parametrize("mutation", ["seal", "group", "area", "source"])
def test_master_rejects_unsealed_unknown_or_nonphysical_groups(mutation):
    pytest.importorskip("scipy.optimize")
    physical = oracle()
    check = physical((), "one-h1")
    value = dict(check.evidence["failure_certificate"])
    if mutation == "seal":
        value["physical_cut_capabilities_sha256"] = "changed"
    elif mutation == "group":
        value["required_clearance_owner_groups"] = [["not_an_inventory_owner"]]
        value["physical_group_sha256"] = content_hash(value["required_clearance_owner_groups"])
    elif mutation == "area":
        value["physical_group_witnesses"] = [dict(w) for w in value["physical_group_witnesses"]]
        value["physical_group_witnesses"][0]["covered_area_m2"] = 0
    else:
        value["source_id"] = "M"
        value["required_clearance_owner_groups"] = [
            group for group in value["required_clearance_owner_groups"] if group != ["M"]
        ]
    value["certificate_sha256"] = content_hash(
        {key: item for key, item in value.items() if key != "certificate_sha256"}
    )
    area = build_cleared_area_function(
        physical.base.owners,
        [g.area for g in physical.base.geometry],
        area_basis="physical_geometry",
    )
    with pytest.raises(ValueError):
        solve_area_master(
            physical.candidate_ids,
            area,
            ("one-h1",),
            problem_sha256=physical.problem_sha256,
            fixed_retained_ids=sorted(area.owner_ids - set(physical.candidate_ids)),
            cache={
                (physical.problem_sha256, (), "one-h1"): ProtectionCheck(
                    "failed", {"failure_certificate": value}
                )
            },
        )
