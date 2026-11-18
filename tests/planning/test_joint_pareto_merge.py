"""Physical catalog equivalence cannot be inferred from village or source names."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/merge_heritage_joint_pareto.py"


@pytest.fixture(scope="module")
def merger():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("joint_pareto_merge_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit(merger):
    protocol = {
        "parent_request_fields": {"domain_mask": "mask", "parameters": "material"},
        "transport_parameters": {"exposure_radius_m": 15},
        "inventory_sha256": "inventory",
        "protected_core_ids": ["core"],
        "native_physical_problem": {
            "fires": {
                "family": {
                    "protocol": {"draws": [{"priority_ids": ["S", "R"]}]},
                    "refined_ids": ["S", "R", "core"],
                    "footprints": {"S": "polygon", "R": "replacement"},
                }
            }
        },
        "numerics": {"roof_scales_m": [1.0], "quadrature_spacing_m": 0.5, "max_dt_s": 0.5},
        "source_policy": "retained_sources",
        "source_sha256": {"src/solver.py": "heat-code"},
        "runner_physical_leaves": {"engine_for_scenario": "same-builder"},
    }
    return {
        "protocol": protocol,
        "physical_protocol_sha256": merger.single.content_hash(protocol),
        "baseline_partitions": {"family-h1": {"layout": "same", "integration": "mask"}},
        "source_witnesses": {"family-h1:S": {"area": 25, "fuel": 450}},
    }


@pytest.mark.parametrize(
    "field",
    [
        "parent_request_fields",
        "transport_parameters",
        "inventory_sha256",
        "protected_core_ids",
        "native_physical_problem",
        "numerics",
        "source_policy",
        "source_sha256",
        "runner_physical_leaves",
    ],
)
def test_every_physical_leaf_mismatch_is_rejected(merger, field):
    first = audit(merger)
    second = deepcopy(first)
    second["protocol"][field] = "different"
    second["physical_protocol_sha256"] = merger.single.content_hash(second["protocol"])
    with pytest.raises(ValueError, match="not equivalent"):
        merger.verify_equivalence([first, second], expected_scenario_count=1)


def test_source_replacement_allowed_only_under_same_family_contract(merger):
    first, second = audit(merger), audit(merger)
    second["source_witnesses"] = {"family-h1:R": {"area": 25, "fuel": 450}}
    receipt = merger.verify_equivalence([first, second], expected_scenario_count=1)
    assert len(receipt["source_witnesses"]) == 2
    second["source_witnesses"] = {"family-h1:S": {"area": 25, "fuel": 500}}
    with pytest.raises(ValueError, match="budget or polygon"):
        merger.verify_equivalence([first, second], expected_scenario_count=1)


def test_integration_membership_and_missing_scenario_identity_cannot_be_hidden(merger):
    first, second = audit(merger), audit(merger)
    second["baseline_partitions"]["family-h1"]["integration"] = "switched"
    with pytest.raises(ValueError, match="integration membership"):
        merger.verify_equivalence([first, second], expected_scenario_count=1)
    with pytest.raises(ValueError, match="scenario count"):
        merger.verify_equivalence([first])


def test_only_explicit_administrative_fields_are_ignored(merger):
    first = audit(merger)["protocol"]["native_physical_problem"]
    second = deepcopy(first)
    first.update(joint_plans={"old": {}}, code_version="a", candidate_ids=["S"])
    second.update(joint_plans={"new": {}}, code_version="b", candidate_ids=["S", "R"])
    assert merger.physical_problem_projection(first) == merger.physical_problem_projection(second)
    second["new_heat_closure"] = 20
    assert merger.physical_problem_projection(first) != merger.physical_problem_projection(second)
    del second["new_heat_closure"]
    second["fires"]["family"]["footprints"] = {"R": "replacement", "S": "polygon"}
    assert merger.physical_problem_projection(first) != merger.physical_problem_projection(second)


def report(cost, count, area, *, mode="strict", problem="native"):
    feasible = {
        "plan_id": "same-name",
        "problem_sha256": problem,
        "demolished_ids": [],
        "cleared_physical_cell_ids": [],
        "cleared_area_m2": cost,
        "status": "passed",
        "scenario_observations": {"family-h1": {}},
        "objective_values": [cost, count, area],
        "worst_case": {
            "N_burned_out": {"lower": count - 1, "upper": count},
            "A_burned_out_m2": area,
            "A_active_m2": 0,
        },
    }
    return {
        "temporal_mode": mode,
        "run_root": "original-archive",
        "native_comparison_problem_sha256": problem,
        "analysis_problem_sha256": "analysis" if mode == "finite_domain_closed" else None,
        "archive": {
            "comparison_protocol": {"scenario_ids": ["family-h1"]},
            "unrepresented_original_ids": [],
            "objective_names": ["C", "N_upper", "A"],
            "fully_evaluated_feasible_plans": [feasible],
            "unresolved_plans": [],
            "failed_plans": [],
        },
    }


def test_merge_keeps_native_ids_and_recomputes_interval_dominance(merger):
    receipt = merger.verify_equivalence([audit(merger)], expected_scenario_count=1)
    first, second = report(25, 5, 100, problem="first"), report(50, 8, 200, problem="second")
    original = deepcopy([first, second])
    output = merger.merge_verified_archives([first, second], receipt)
    assert [r["plan_id"] for r in output["non_dominated_archive"]] == ["batch1::same-name"]
    assert output["fully_evaluated_feasible_plans"][1]["robustly_dominated_by"] == [
        "batch1::same-name"
    ]
    assert first == original[0] and second == original[1]
    assert [r["native_problem_sha256"] for r in output["lineage"]] == ["first", "second"]
    assert not output["global_pareto_frontier_complete"]


def test_merge_never_pools_strict_and_conditioned_or_missing_observations(merger):
    receipt = merger.verify_equivalence([audit(merger)], expected_scenario_count=1)
    with pytest.raises(ValueError, match="cannot be merged"):
        merger.merge_verified_archives(
            [report(25, 5, 100), report(25, 5, 100, mode="finite_domain_closed")], receipt
        )
    row = report(25, 5, 100)
    row["archive"]["fully_evaluated_feasible_plans"][0]["scenario_observations"] = {}
    with pytest.raises(ValueError, match="every closed"):
        merger.merge_verified_archives([row], receipt)


@pytest.fixture(scope="module")
def native_campaign(tmp_path_factory):
    path = ROOT / "tests/planning/test_joint_pareto_cli.py"
    spec = importlib.util.spec_from_file_location("merge_native_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.native_campaign.__wrapped__(tmp_path_factory)


def test_real_native_cli_audits_both_snapshots_and_never_writes_them(
    native_campaign, merger, tmp_path
):
    root, config = native_campaign
    before = {
        str(p.relative_to(root)): merger.single.file_sha256(p)
        for p in root.rglob("*")
        if p.is_file()
    }
    catalog = tmp_path / "catalogs.json"
    catalog.write_text(json.dumps([{"run_root": str(root), "config": str(config)}] * 2))
    output = tmp_path / "merged.json"
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--catalogs",
            str(catalog),
            "--output",
            str(output),
            "--expected-scenario-count",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert process.returncode == 0, process.stdout + process.stderr
    merged = json.loads(output.read_text())
    assert merged["new_CA_checks"] == merged["original_run_writes"] == 0
    assert merged["archive"]["complete_feasible_plan_count"] == 2
    assert len(merged["archive"]["non_dominated_archive"]) == 2
    assert merged["physical_equivalence_certificate"]["schema_version"] == merger.SCHEMA
    assert all(
        a["radiation_graphs_constructed"] == 0 for a in merged["native_partition_and_source_audits"]
    )
    after = {
        str(p.relative_to(root)): merger.single.file_sha256(p)
        for p in root.rglob("*")
        if p.is_file()
    }
    assert before == after


def test_native_worker_independently_rejects_integration_mask_switch(
    native_campaign, merger, tmp_path
):
    source, config = native_campaign
    copied = tmp_path / "copied"
    shutil.copytree(source, copied)
    manifest_path = next((copied / "evaluations").glob("*/complete.json"))
    check_path = manifest_path.parent / "check.json"
    check = json.loads(check_path.read_text())
    check["evidence"]["frozen_integrated_node_mask_sha256"] = "0" * 64
    check_path.write_text(json.dumps(check))
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"]["check.json"] = merger.single.file_sha256(check_path)
    manifest_path.write_text(json.dumps(manifest))
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--native-protocol-worker",
            "--run-root",
            str(copied),
            "--config",
            str(config),
            "--output",
            str(tmp_path / "bad.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert process.returncode != 0
    assert "integration membership" in process.stderr
