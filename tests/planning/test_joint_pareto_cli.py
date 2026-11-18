"""Read-only terminal Pareto CLI verifies real frozen native artifacts."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256
from pyfireca.planning.study_io import load_study
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/summarize_heritage_joint_pareto.py"


def command(run_root, config, output, *, temporal_mode=None):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--run-root",
            str(run_root),
            "--config",
            str(config),
            "--output",
            str(output),
            *([] if temporal_mode is None else ["--temporal-mode", temporal_mode]),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )


@pytest.fixture(scope="module")
def native_campaign(tmp_path_factory):
    destination = tmp_path_factory.mktemp("joint-pareto-native")
    path = ROOT / "tests/planning/test_joint_terminal_comparison.py"
    spec = importlib.util.spec_from_file_location("pareto_cli_terminal_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with pytest.MonkeyPatch.context() as monkeypatch:
        _, _, inputs = fixture.configured.__wrapped__(monkeypatch, destination)
    config_path, config, bundle, transport, inventory, _ = inputs
    write_v3_case_bundle(
        bundle.request,
        destination / "native-case",
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata={**bundle.metadata, "transport_parameters": asdict(transport)},
    )
    (destination / "study.json").write_text(
        json.dumps(
            {
                "schema_version": "heritage-p1-study-1",
                "case_dir": "native-case",
                "structures_path": "structures.json",
                "targets": [asdict(target) for target in inventory.targets],
                "fixed_source_ids": list(inventory.fixed_source_ids),
                "candidate_ids": list(inventory.candidate_ids),
                "candidate_evidence": inventory.candidate_evidence,
            }
        )
    )
    config_path.write_text(json.dumps(config))
    run_root = destination / "native-run"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_comparison.py"),
        "--config",
        str(config_path),
        "--output",
        str(run_root),
    ]
    for mode in ("--prepare-only", "--resume"):
        result = subprocess.run(
            [*common, mode],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return run_root, config_path


def copy_campaign(native_campaign, tmp_path):
    source, config = native_campaign
    copied = tmp_path / "copied-run"
    shutil.copytree(source, copied)
    return copied, config


def test_native_cli_retains_only_complete_safe_plan_and_never_writes_original(
    native_campaign, tmp_path
):
    run_root, config = native_campaign
    before = {
        str(path.relative_to(run_root)): file_sha256(path)
        for path in run_root.rglob("*")
        if path.is_file()
    }
    output = tmp_path / "archive.json"
    result = command(run_root, config, output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    archive = report["archive"]
    assert report["comparison_problem_sha256"] == report["native_comparison_problem_sha256"]
    assert report["new_CA_checks"] == report["original_run_writes"] == 0
    assert report["native_saved_state_and_peak_certificates_verified"]
    assert archive["comparison_protocol"]["protected_core_ids"] == ["F", "G"]
    assert [row["plan_id"] for row in archive["non_dominated_archive"]] == ["joint"]
    assert [row["plan_id"] for row in archive["failed_plans"]] == ["baseline"]
    assert not archive["global_pareto_frontier_complete"]
    assert not report["source_and_area_anchor_scope"][
        "old_3600_area_optimality_transferred_to_terminal_or_joint"
    ]
    area = next(
        row for row in report["canonical_construction_area_ledger"] if row["plan_id"] == "joint"
    )
    assert area["canonical_C_m2"] == 50
    assert area["whole_ground_footprints_m2"] == 25
    assert area["roof_geometry_union_m2"] == 25
    assert all(row["terminal_complete"] for row in report["native_checks"])
    after = {
        str(path.relative_to(run_root)): file_sha256(path)
        for path in run_root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_missing_scenario_stays_unknown_without_a_frontier(native_campaign, tmp_path):
    run_root, config = copy_campaign(native_campaign, tmp_path)
    for manifest in (run_root / "evaluations").glob("*/complete.json"):
        if json.loads(manifest.read_text())["plan_id"] == "joint":
            shutil.rmtree(manifest.parent)
    output = tmp_path / "partial.json"
    result = command(run_root, config, output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert not report["archive"]["non_dominated_archive"]
    assert [row["plan_id"] for row in report["archive"]["unresolved_plans"]] == ["joint"]
    missing = next(row for row in report["native_checks"] if row["plan_id"] == "joint")
    assert missing["status"] == "not_evaluated"


@pytest.mark.parametrize("target", ["source", "reference", "artifact"])
def test_modified_source_reference_or_artifact_is_rejected(native_campaign, tmp_path, target):
    run_root, config = copy_campaign(native_campaign, tmp_path)
    experiment = json.loads((run_root / "experiment.json").read_text())
    if target == "source":
        changed = run_root / "source-snapshot/src/pyfireca/village/extinction_v3.py"
        changed.write_text(changed.read_text() + "\n# changed\n")
    elif target == "reference":
        # Bind an isolated reference path so the original source fixture stays immutable.
        changed = tmp_path / "copied-reference.json"
        original = next(iter(experiment["references_sha256"]))
        changed.write_bytes(Path(original).read_bytes() + b" ")
        experiment["references_sha256"][str(changed)] = experiment["references_sha256"][original]
        (run_root / "experiment.json").write_text(json.dumps(experiment))
    else:
        changed = next((run_root / "evaluations").glob("*/fire-loss.json"))
        changed.write_text(changed.read_text() + " ")
    result = command(run_root, config, tmp_path / "bad.json")
    assert result.returncode != 0
    assert "changed" in result.stderr


def test_output_cannot_be_written_into_original_archive(native_campaign):
    run_root, config = native_campaign
    result = command(run_root, config, run_root / "new-analysis.json")
    assert result.returncode != 0
    assert "outside" in result.stderr
    assert not (run_root / "new-analysis.json").exists()


def test_output_cannot_overwrite_original_configuration(native_campaign):
    run_root, config = native_campaign
    before = config.read_bytes()
    result = command(run_root, config, config)
    assert result.returncode != 0
    assert "overwrite an original" in result.stderr
    assert config.read_bytes() == before


def test_saved_cap_limited_fire_is_explicitly_unknown(native_campaign, tmp_path):
    _, original_config = native_campaign
    config = json.loads(original_config.read_text())
    config.update(
        horizon_s=20.0,
        terminal_horizon_cap_s=20.0,
        checkpoints_s=[10.0, 20.0],
        plans=[config["plans"][0]],
    )
    for key in ("study_path",):
        config[key] = str((original_config.parent / config[key]).resolve())
    config["source_fires"] = [
        row | {"protocol_path": str((original_config.parent / row["protocol_path"]).resolve())}
        for row in config["source_fires"]
    ]
    config_path = tmp_path / "short.json"
    config_path.write_text(json.dumps(config))
    run_root = tmp_path / "short-native"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_comparison.py"),
        "--config",
        str(config_path),
        "--output",
        str(run_root),
    ]
    for mode in ("--prepare-only", "--resume"):
        result = subprocess.run(
            [*common, mode],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "short-archive.json"
    result = command(run_root, config_path, output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert not report["archive"]["non_dominated_archive"]
    assert [row["plan_id"] for row in report["archive"]["unresolved_plans"]] == ["baseline"]
    observation = report["native_checks"][0]
    assert observation["status"] == "unknown" and not observation["terminal_complete"]
    assert observation["terminal_observation"] == "time_cap_reached_without_extinction_closure"
    assert "active_burning_nodes_remain" in observation["closure_rejection_reasons"]


@pytest.fixture(scope="module")
def boundary_campaign(native_campaign, tmp_path_factory):
    _, original_config = native_campaign
    destination = tmp_path_factory.mktemp("pareto-real-boundary-native")
    config = json.loads(original_config.read_text())
    bundle, transport, inventory = load_study(original_config.parent / config["study_path"])
    request = bundle.request
    parameters = replace(
        request.parameters,
        **{
            name: np.roll(getattr(request.parameters, name), -2, axis=1)
            for name in request.parameters.__dataclass_fields__
        },
    )
    fields = {
        name: np.roll(value, -2, axis=1)
        for name in request.__dataclass_fields__
        if isinstance(value := getattr(request, name), np.ndarray)
        and value.shape == request.grid.shape
    }
    edges = build_v3_radiative_transport_edges(
        request.grid,
        fields["source_mask"],
        fields["target_mask"],
        parameters.receiver_area_m2,
        parameters.source_height_m,
        parameters.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
    )
    bundle = replace(
        bundle, request=replace(request, parameters=parameters, exposure_edges=edges, **fields)
    )
    inventory = replace(
        inventory,
        buildings=tuple(
            replace(b, geometry=translate(b.geometry, xoff=-10)) for b in inventory.buildings
        ),
    )
    write_v3_case_bundle(
        bundle.request,
        destination / "native-case",
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata={**bundle.metadata, "transport_parameters": asdict(transport)},
    )
    (destination / "structures.json").write_text(json.dumps({"buildings": inventory.structures}))
    (destination / "study.json").write_text(
        json.dumps(
            {
                "schema_version": "heritage-p1-study-1",
                "case_dir": "native-case",
                "structures_path": "structures.json",
                "targets": [asdict(t) for t in inventory.targets],
                "fixed_source_ids": list(inventory.fixed_source_ids),
                "candidate_ids": list(inventory.candidate_ids),
                "candidate_evidence": inventory.candidate_evidence,
            }
        )
    )
    old_source = config["source_fires"][0]
    protocol = json.loads((original_config.parent / old_source["protocol_path"]).read_text())
    protocol["inventory_sha256"] = inventory.sha256
    protocol["protocol_sha256"] = content_hash(
        {k: v for k, v in protocol.items() if k != "protocol_sha256"}
    )
    (destination / "source.json").write_text(json.dumps(protocol))
    config["source_fires"] = [old_source | {"protocol_path": "source.json"}]
    for plan in config["plans"]:
        plan["cleared_physical_cell_ids"] = [
            (c // request.grid.width) * request.grid.width
            + (c % request.grid.width - 2) % request.grid.width
            for c in plan["cleared_physical_cell_ids"]
        ]
    config_path = destination / "comparison.json"
    config_path.write_text(json.dumps(config))
    run_root = destination / "native-run"
    common = [
        sys.executable,
        str(ROOT / "scripts/run_heritage_joint_comparison.py"),
        "--config",
        str(config_path),
        "--output",
        str(run_root),
    ]
    for mode in ("--prepare-only", "--resume"):
        result = subprocess.run(
            [*common, mode],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return run_root, config_path


def test_real_boundary_native_archives_get_separate_conditional_pool_without_rewriting(
    boundary_campaign, tmp_path
):
    root, config = boundary_campaign
    before = {str(p.relative_to(root)): file_sha256(p) for p in root.rglob("*") if p.is_file()}
    strict, conditional = tmp_path / "strict.json", tmp_path / "finite.json"
    for output, mode in ((strict, "strict"), (conditional, "finite_domain_closed")):
        result = command(root, config, output, temporal_mode=mode)
        assert result.returncode == 0, result.stdout + result.stderr
    first, second = json.loads(strict.read_text()), json.loads(conditional.read_text())
    assert not first["archive"]["non_dominated_archive"]
    assert [r["plan_id"] for r in second["archive"]["non_dominated_archive"]] == ["joint"]
    assert [r["plan_id"] for r in second["archive"]["failed_plans"]] == ["baseline"]
    assert second["analysis_problem_sha256"] != second["native_comparison_problem_sha256"]
    assert second["comparison_problem_sha256"] == second["analysis_problem_sha256"]
    assert all(c["status"] == "unknown" for c in second["native_checks"])
    assert all(
        c["native_invalid_reasons"] == ["fire_reached_physical_domain_edge"]
        for c in second["finite_domain_checks"]
    )
    assert second["new_CA_checks"] == second["original_run_writes"] == 0
    after = {str(p.relative_to(root)): file_sha256(p) for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_boundary_unknown_native_certificate_still_binds_actual_saved_state(
    boundary_campaign, tmp_path
):
    source, config = boundary_campaign
    root = tmp_path / "copied"
    shutil.copytree(source, root)
    manifest_path = next((root / "evaluations").glob("*/complete.json"))
    folder = manifest_path.parent
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        values = {key: saved[key].copy() for key in saved.files}
    values["fuel_remaining_kg"].ravel()[0] += 1e-6
    np.savez_compressed(folder / "final-state.npz", **values)
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"]["final-state.npz"] = file_sha256(folder / "final-state.npz")
    manifest_path.write_text(json.dumps(manifest))
    result = command(root, config, tmp_path / "bad.json", temporal_mode="finite_domain_closed")
    assert result.returncode != 0
    assert "state/peak" in result.stderr
