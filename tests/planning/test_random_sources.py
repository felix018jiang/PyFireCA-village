"""Paired conditional random fires, correct weighting and no fabricated successes."""

import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import (
    freeze_source_protocol,
    realize_sources,
    summarize_protection,
)

ROOT = Path(__file__).resolve().parents[2]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def inventory():
    fixture = module("random_fixture", "examples/research/heritage/synthetic_case.py")
    _, _, inv, _ = fixture.make_case()
    return replace(inv, fixed_source_ids=(), candidate_ids=("S", "M", "U"))


def protocol(inv, **kwargs):
    return freeze_source_protocol(
        inv, seed=20261023, draws_per_stratum=2, area_m2=1.0, near_distance_m=2.0, **kwargs
    )


def test_frozen_draws_and_polygons_reproduce_and_cores_are_never_sources():
    inv = inventory()
    frozen = protocol(inv)
    assert frozen == protocol(inv)
    a, b = realize_sources(frozen, inv), realize_sources(frozen, inv)
    assert sum(row["weight"] for row in a) == pytest.approx(1)
    assert set(frozen["members"]) == {"S", "M", "U"}
    for first, second in zip(a, b, strict=True):
        assert first["source_id"] == second["source_id"]
        assert first["anchor_m"] == second["anchor_m"]
        assert first["footprint"].sha256 == second["footprint"].sha256
        assert first["footprint"].geometry.area == pytest.approx(1)


def test_demolished_source_is_replaced_by_a_real_retained_fire_without_banning_candidates():
    inv = inventory()
    frozen = protocol(inv)
    before = realize_sources(frozen, inv)
    chosen = next(row for row in before if row["stratum"] != "background")
    removed = chosen["source_id"]
    after = realize_sources(frozen, inv, demolished_ids=(removed,))
    replacement = next(row for row in after if row["draw_id"] == chosen["draw_id"])
    assert replacement["source_id"] != removed
    assert replacement["source_replaced"]
    assert replacement["footprint"].geometry.area == pytest.approx(1)
    assert sum(row["weight"] for row in after) == pytest.approx(1)
    for a, b in zip(before, after, strict=True):
        if a["source_id"] != removed:
            assert a["footprint"].sha256 == b["footprint"].sha256
    assert removed in inv.candidate_ids


def test_empty_stratum_has_zero_mass_and_not_a_success_and_all_sources_removed_fails():
    inv = inventory()
    frozen = protocol(inv)
    rows = realize_sources(frozen, inv, demolished_ids=("U",))
    empty = [row for row in rows if row["stratum"] == "background"]
    assert all(row["status"] == "empty_stratum" and row["weight"] == 0 for row in empty)
    assert all(row["footprint"] is None for row in empty)
    assert sum(row["weight"] for row in rows) == pytest.approx(1)
    with pytest.raises(ValueError, match="retained eligible source"):
        realize_sources(frozen, inv, demolished_ids=("S", "M", "U"))


def test_protocol_tampering_and_wrong_inventory_are_rejected():
    inv = inventory()
    frozen = protocol(inv)
    with pytest.raises(ValueError, match="inventory mismatch"):
        realize_sources(frozen, replace(inv, candidate_ids=("M",)))
    frozen["seed"] += 1
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        realize_sources(frozen, inv)


def test_scale_failures_are_unioned_and_missing_runs_never_count_as_passed():
    inv = inventory()
    frozen = protocol(inv)
    sources = realize_sources(frozen, inv)
    runs = []
    for draw in sources:
        for scale in (1.0, 0.5):
            failed = draw == sources[0] and scale == 0.5
            runs.append(
                {
                    "plan_id": "base",
                    "draw_id": draw["draw_id"],
                    "scale_m": scale,
                    "complete": True,
                    "protection_status": "failed" if failed else "passed",
                    "core_outcomes": [
                        {"building_id": "G", "ignited": failed},
                        {"building_id": "F", "ignited": False},
                    ],
                }
            )
    result = summarize_protection(
        frozen, sources, runs, plan_id="base", scales_m=(1.0, 0.5), core_ids=("G", "F")
    )
    assert result["weighted_joint_failure_fraction"] == pytest.approx(sources[0]["weight"])
    assert result["weighted_core_failure_fractions"]["F"] == 0
    missing = summarize_protection(
        frozen, sources, runs[:-1], plan_id="base", scales_m=(1.0, 0.5), core_ids=("G", "F")
    )
    assert missing["weighted_joint_failure_fraction"] is None
    assert not missing["all_evaluations_complete"]


def test_random_pilot_runs_both_plans_and_scales_and_restores_one_case(tmp_path):
    controls = module("random_controls", "examples/research/heritage/roof_controls.py")
    study_path = Path(controls.create_controls(tmp_path / "inputs")[1]["study"])
    study = json.loads(study_path.read_text())
    study.update(fixed_source_ids=[], candidate_ids=["S", "M", "U"])
    study_path.write_text(json.dumps(study))
    config = {
        "schema_version": "heritage-random-source-pilot-1",
        "study_path": str(study_path),
        "seed": 20261023,
        "draws_per_stratum": 1,
        "ignition_area_m2": 1.0,
        "near_distance_m": 2.0,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.25,
        "horizon_s": 20,
        "plans": [
            {"plan_id": "base", "demolished_ids": []},
            {"plan_id": "remove", "demolished_ids": ["M"], "ground_evidence": "synthetic"},
        ],
        "max_nodes": 1000,
        "max_radiation_edges": 100000,
        "minimum_free_disk_GB": 0,
        "archive_native_bundle": True,
    }
    config_path = tmp_path / "pilot.json"
    config_path.write_text(json.dumps(config))
    runner = module("random_runner", "scripts/run_heritage_random_sources.py")
    output = tmp_path / "output"
    result = runner.run(config_path, output)
    assert result["complete"] and len(result["runs"]) == 8
    assert result["parent_case_unchanged"] and result["inventory_unchanged"]
    assert all(r["complete"] and r["source_verified"] for r in result["runs"])
    assert all(r["energy_error_J"] <= r["energy_tolerance_J"] for r in result["runs"])
    for row in result["runs"]:
        assert row["source_id"] not in {"G", "F"}
        np.testing.assert_allclose(row["source_budget"]["covered_area_m2"], 1)
    assert runner.run(config_path, output, resume=True)["runs"] == result["runs"]
    saved = next(output.glob("*/base"))
    assert not (saved / "node-storage-not-map").exists()
    assert (saved / "selected-trace.json.gz").exists()
    shutil.unpack_archive(saved / "node-storage-not-map.zip", saved)
    assert runner.load_runner().restore_simulation(saved)["all_final_state_fields_equal"]
    reporter = module("random_reporter", "scripts/summarize_heritage_random_sources.py")
    checked = reporter.summarize(output)
    assert checked["validation"]["all_energy_checks_passed"]
    assert checked["validation"]["all_source_polygons_equal_when_same_source"]
    assert len(checked["paired_core_outcomes"]) == 4
    manual = module("manual_bridge_runner", "scripts/run_heritage_bridge_manual.py")
    frozen = json.loads((output / "source-protocol.json").read_text())
    selected = next(r["draw_id"] for r in frozen["draws"] if r["stratum"] != "background")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"source": "synthetic"}))
    manual_config = {
        "schema_version": "heritage-bridge-manual-plans-1",
        "study_path": str(study_path),
        "reference_dir": str(output),
        "evidence_path": str(evidence),
        "archive_native_bundle": True,
        "archive_layout": True,
        "plans": [
            {
                "plan_id": "manual-M",
                "demolished_ids": ["M"],
                "overlap_policy": "retain_shared_roof",
                "ground_evidence": "synthetic",
                "draw_ids": [selected],
            }
        ],
        **{
            key: config[key]
            for key in (
                "roof_scales_m",
                "quadrature_spacing_m",
                "horizon_s",
                "max_nodes",
                "max_radiation_edges",
                "minimum_free_disk_GB",
            )
        },
    }
    manual_path = tmp_path / "manual.json"
    manual_path.write_text(json.dumps(manual_config))
    manual_result = manual.run(manual_path, tmp_path / "manual-output")
    assert len(manual_result) == 2 and all(r["complete"] for r in manual_result)
    assert all(r["source_verified"] for r in manual_result)
    assert all(r["intervention"]["overlap_policy"] == "retain_shared_roof" for r in manual_result)
    archived_manual = next((tmp_path / "manual-output").glob("*/manual-M"))
    assert not (archived_manual / "layout.json").exists()
    shutil.unpack_archive(archived_manual / "node-storage-not-map.zip", archived_manual)
    assert manual.load_runner().restore_simulation(archived_manual)["all_final_state_fields_equal"]
    manual_reporter = module("manual_reporter", "scripts/summarize_heritage_bridge_manual.py")
    checked = manual_reporter.summarize([tmp_path / "manual-output"], output)
    assert checked["new_simulations"] == 2
    assert checked["unique_saved_baselines_reused"] == 2
    assert checked["validation"]["clearance_and_shared_areas_match_vector_geometry"]
    saved_summary = tmp_path / "manual-output" / "summary.json"
    interrupted = json.loads(saved_summary.read_text())
    interrupted["complete"] = False
    saved_summary.write_text(json.dumps(interrupted))
    with pytest.raises(ValueError, match="incomplete manual matrix"):
        manual_reporter.summarize([tmp_path / "manual-output"], output)

    declarations, validation_roots = [], []
    for label, seed in (("a", 17), ("b", 29)):
        cohort_config = {
            **config,
            "seed": seed,
            "validation_cohort": label,
            "selection_result_sha256": content_hash(checked),
        }
        cohort_path = tmp_path / f"cohort-{label}.json"
        cohort_path.write_text(json.dumps(cohort_config))
        cohort_root = tmp_path / f"cohort-{label}"
        runner.run(cohort_path, cohort_root)
        declarations.append(
            {"file": cohort_path.name, "seed": seed, "config_sha256": content_hash(cohort_config)}
        )
        validation_roots.append(cohort_root)
    manifest = {
        "cohorts": declarations,
        "fixed_plan": config["plans"][1],
        "development_result_sha256": content_hash(checked),
        "new_simulations": 16,
        "draws_total": 4,
        "draws_per_stratum_total": 2,
        "strata": [s for s, ids in frozen["strata"].items() if ids],
    }
    manifest_path = tmp_path / "validation-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    confirmation = module("confirmation", "scripts/summarize_heritage_fixed_validation.py")
    combined = confirmation.summarize(manifest_path, validation_roots, output)
    assert combined["complete"] and combined["new_simulations"] == 16
    assert combined["validation"]["new_unique_baseline_source_polygons"] == 4
    assert all(p["draws"] == 4 for p in combined["plans"])
    assert len(combined["paired_draws"]) == 4  # Paired draws, not doubled roof scales.
    assert all("same_source" in pair for pair in combined["paired_draws"])
    # The reference layout includes both the removed original source and its
    # retained replacement. Reconstruct both polygons when reusing that layout.
    replacement_config = {
        **manual_config,
        "reference_dir": str(validation_roots[0]),
        "comparison_plan_id": "remove",
        "required_demolished_ids": ["M"],
        "archive_native_bundle": True,
    }
    replacement_path = tmp_path / "manual-replacement.json"
    replacement_path.write_text(json.dumps(replacement_config))
    replacement_root = tmp_path / "manual-replacement"
    replacement_rows = manual.run(replacement_path, replacement_root)
    assert all(r["source_replaced"] and r["same_source_as_comparison"] for r in replacement_rows)
    for row in replacement_rows:
        assert row["core_outcomes"] == row["comparison_core_outcomes"]
        run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
        before = json.loads(
            (validation_roots[0] / run_id / "remove" / "evaluation.json").read_text()
        )
        after = json.loads((replacement_root / run_id / "manual-M" / "evaluation.json").read_text())
        assert (
            before["diagnostics"]["physical_request_sha256"]
            == after["diagnostics"]["physical_request_sha256"]
        )
        assert (replacement_root / run_id / "manual-M" / "node-storage-not-map.zip").exists()
    replacement_config["required_demolished_ids"] = ["U"]
    replacement_path.write_text(json.dumps(replacement_config))
    with pytest.raises(ValueError, match="required demolition"):
        manual.run(replacement_path, tmp_path / "rejected-manual")


def test_three_explicit_arms_multibuilding_and_empty_stratum_archive_restore(tmp_path):
    controls = module("multi_controls", "examples/research/heritage/roof_controls.py")
    study_path = Path(controls.create_controls(tmp_path / "inputs")[1]["study"])
    study = json.loads(study_path.read_text())
    study.update(fixed_source_ids=[], candidate_ids=["S", "M", "U"])
    study_path.write_text(json.dumps(study))
    config = {
        "schema_version": "heritage-random-source-pilot-1",
        "study_path": str(study_path),
        "seed": 17,
        "draws_per_stratum": 1,
        "ignition_area_m2": 1.0,
        "near_distance_m": 2.0,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.25,
        "horizon_s": 20,
        "plans": [
            {"plan_id": "base", "demolished_ids": []},
            {"plan_id": "single", "demolished_ids": ["M"], "ground_evidence": "synthetic"},
            {"plan_id": "multi", "demolished_ids": ["M", "U"], "ground_evidence": "synthetic"},
        ],
        "max_nodes": 1000,
        "max_radiation_edges": 100000,
        "minimum_free_disk_GB": 0,
        "archive_native_bundle": True,
        "archive_layout": True,
    }
    path = tmp_path / "three-arms.json"
    path.write_text(json.dumps(config))
    runner = module("multi_runner", "scripts/run_heritage_random_sources.py")
    output = tmp_path / "output"
    result = runner.run(path, output)
    assert result["complete"] and result["planned_runs"] == len(result["runs"]) == 10
    sources = json.loads((output / "realized-sources.json").read_text())
    assert any(s["status"] == "empty_stratum" and s["weight"] == 0 for s in sources["multi"])
    assert sum(s["weight"] for s in sources["multi"]) == pytest.approx(1)
    assert all(
        r["source_id"] == "S" and r["demolished_count"] == 2
        for r in result["runs"]
        if r["plan_id"] == "multi"
    )
    assert runner.run(path, output, resume=True)["runs"] == result["runs"]
    reporter = module("multi_reporter", "scripts/summarize_heritage_random_sources.py")
    checked = reporter.summarize(output)
    assert len(checked["paired_core_outcomes"]) == 12
    empty = [
        r
        for r in checked["paired_core_outcomes"]
        if r["comparison_status"] == "zero_weight_empty_stratum"
    ]
    assert len(empty) == 4 and all(r["treated_failed_ids"] is None for r in empty)
    saved = next(output.glob("*/multi"))
    assert not (saved / "layout.json").exists()
    shutil.unpack_archive(saved / "node-storage-not-map.zip", saved)
    assert (saved / "layout.json").exists()
    assert runner.load_runner().restore_simulation(saved)["all_final_state_fields_equal"]
    # Increasing a capacity guard must reproduce the original physical request
    # and final states, while keeping the frozen experiment/configuration intact.
    last = result["runs"][-1]
    run_id = f"{last['draw_id']}-h{last['scale_m']}".replace(".", "p")
    case = output / run_id / last["plan_id"]
    before = json.loads((case / "evaluation.json").read_text())
    with np.load(case / "final-state.npz", allow_pickle=False) as saved_state:
        before_state = {k: saved_state[k].copy() for k in saved_state.files}
    experiment_before = (output / "experiment.json").read_bytes()
    shutil.rmtree(case)
    capacity = module("capacity_resume", "scripts/resume_heritage_resource_capacity.py")
    capacity.resume(path, output, max_radiation_edges=200000)
    after = json.loads((case / "evaluation.json").read_text())
    assert after["evaluation_sha256"] == before["evaluation_sha256"]
    assert after["metrics"] == before["metrics"]
    assert (
        after["diagnostics"]["physical_request_sha256"]
        == before["diagnostics"]["physical_request_sha256"]
    )
    assert after["diagnostics"]["effective_max_radiation_edges"] == 200000
    with np.load(case / "final-state.npz", allow_pickle=False) as saved_state:
        for key, value in before_state.items():
            np.testing.assert_array_equal(saved_state[key], value)
    assert (output / "experiment.json").read_bytes() == experiment_before
    assert json.loads(path.read_text()) == config
    assert reporter.summarize(output)["validation"]["execution_resource_override_verified"]
    config["plans"][-1]["demolished_ids"] = ["S", "M", "U"]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="retained eligible source"):
        runner.run(path, tmp_path / "no-retained-source")


def test_targeted_source_population_keeps_full_village_and_frozen_sampler(tmp_path):
    controls = module("target_controls", "examples/research/heritage/roof_controls.py")
    study_path = Path(controls.create_controls(tmp_path / "inputs")[1]["study"])
    study = json.loads(study_path.read_text())
    study.update(fixed_source_ids=[], candidate_ids=["S", "M", "U"])
    study_path.write_text(json.dumps(study))
    config = {
        "schema_version": "heritage-random-source-pilot-1",
        "study_path": str(study_path),
        "seed": 39,
        "draws_per_stratum": 2,
        "target_source_ids": ["S"],
        "ignition_area_m2": 1.0,
        "near_distance_m": 2.0,
        "roof_scales_m": [1.0, 0.5],
        "quadrature_spacing_m": 0.25,
        "horizon_s": 20,
        "plans": [
            {"plan_id": "base", "demolished_ids": []},
            {"plan_id": "remove-M", "demolished_ids": ["M"], "ground_evidence": "synthetic"},
        ],
        "max_nodes": 1000,
        "max_radiation_edges": 100000,
        "minimum_free_disk_GB": 0,
        "archive_native_bundle": True,
        "archive_layout": True,
    }
    path = tmp_path / "target.json"
    path.write_text(json.dumps(config))
    target = module("target_runner", "scripts/run_heritage_targeted_sources.py")
    output = tmp_path / "target-output"
    frozen = target.run(path, output, prepare_only=True)
    assert set(frozen["members"]) == {"S"} and len(frozen["draws"]) == 2
    assert (output / "targeted-source-snapshot.py").is_file()
    result = target.run(path, output, resume=True)
    assert result["complete"] and len(result["runs"]) == 8
    assert all(r["source_id"] == "S" for r in result["runs"])
    saved_inventory = json.loads((output / "inventory.json").read_text())
    assert {b["building_id"] for b in saved_inventory["buildings"]} >= {"S", "M", "U"}
    reporter = module("target_reporter", "scripts/summarize_heritage_random_sources.py")
    assert reporter.summarize(output)["validation"]["all_planned_runs_complete_valid"]
    prescribed = module("prescribed_runner", "scripts/run_heritage_prescribed_sources.py")
    prescribed_protocol = json.loads((output / "source-protocol.json").read_text())
    prescribed_protocol.pop("targeted_sampler_source_sha256")
    prescribed_protocol.pop("protocol_sha256")
    prescribed_protocol["protocol_sha256"] = content_hash(prescribed_protocol)
    protocol_path = tmp_path / "prescribed-protocol.json"
    protocol_path.write_text(json.dumps(prescribed_protocol))
    prescribed_config = {
        **config,
        "source_protocol_path": protocol_path.name,
        "source_protocol_sha256": content_hash(prescribed_protocol),
    }
    prescribed_path = tmp_path / "prescribed.json"
    prescribed_path.write_text(json.dumps(prescribed_config))
    replay = prescribed.run(prescribed_path, tmp_path / "prescribed-output")
    assert [r["footprint_sha256"] for r in replay["runs"]] == [
        r["footprint_sha256"] for r in result["runs"]
    ]
    assert reporter.summarize(tmp_path / "prescribed-output")["validation"][
        "all_planned_runs_complete_valid"
    ]
    (output / "targeted-source-snapshot.py").write_text("changed sampler")
    with pytest.raises(ValueError, match="sampler changed"):
        target.run(path, output, resume=True)
