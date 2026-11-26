"""A finite-catalog draft binds inputs without freezing or running a producer."""

import importlib.util
import json
from pathlib import Path

import pytest

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


@pytest.fixture
def preparation(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "balanced_catalog_test", scripts / "prepare_heritage_balanced_catalog.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def save(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path

    source_files = [save(f"source{i}.json", {"source": i}) for i in range(4)]
    study = save("study.json", {"input": "unchanged"})
    master_config = save("master-config.json", {})
    seed_receipt = save("seed.json", {"seed": ["R"]})
    seed_search = save("seed-search.json", {"proof": "historical-phase-only"})
    old_proposal = save("old-proposals.json", {"unused": "old-five-arm-design"})
    fires = {}
    source_rows = []
    actual = {}
    for i, path in enumerate(source_files):
        label, identifier = f"family{i}", f"S{i}"
        actual[label] = identifier
        fires[label] = {
            "protocol": {
                "inventory_sha256": "inventory",
                "protocol_sha256": f"proto{i}",
                "draws": [{"draw_id": f"draw{i}", "priority_ids": [identifier, "R"]}],
            },
            "footprints": {identifier: {"source_id": identifier, "area_m2": 25}},
        }
        source_rows.append({"fire_id": label, "draw_id": f"draw{i}", "protocol_path": str(path)})
    problem = {"inventory_sha256": "inventory", "fires": fires}
    ref = save(
        "reference.json",
        {
            "problem": problem,
            "problem_sha256": content_hash(problem),
            "references_sha256": {str(p): file_sha256(p) for p in [*source_files, study]},
        },
    )
    template = {
        "schema_version": "heritage-joint-comparison-1",
        "village_id": "synthetic",
        "study_path": str(study),
        "source_policy": "retained_sources",
        "source_inventory_sha256": "inventory",
        "source_fires": source_rows,
        "candidate_ids": ["R", *[f"S{i}" for i in range(4)]],
        "reference_master_experiment_path": str(ref),
        "reference_master_experiment_sha256": file_sha256(ref),
        "reference_master_config_path": str(master_config),
        "reference_master_config_sha256": file_sha256(master_config),
        "roof_seed_receipt_path": str(seed_receipt),
        "roof_seed_receipt_sha256": file_sha256(seed_receipt),
        "roof_seed_search_snapshot_path": str(seed_search),
        "roof_seed_search_sha256": file_sha256(seed_search),
        "vegetation_proposals_path": str(old_proposal),
        "vegetation_proposals_sha256": file_sha256(old_proposal),
        "refinement_ids": ["R", "S0", "S1", "S2", "S3"],
        "roof_scales_m": [1.0, 0.5],
        "horizon_s": 3600.0,
        "quadrature_spacing_m": 0.5,
        "max_dt_s": 1.0,
        "terminal_fire_verification": True,
        "terminal_horizon_cap_s": 172800.0,
        "max_wall_time_s": 172800.0,
        "max_new_checks": 40,
        "max_total_checks": 40,
        "max_nodes": 1000,
        "max_radiation_edges": 100000,
        "minimum_free_disk_GB": 1,
        "new_independent_fire_draw_count": 0,
        "source_area_limit_m2": 443.0,
        "source_area_tolerance_m2": 1e-6,
        "core_overlap_policy": "reject",
        "checkpoints_s": [1200.0, 2400.0, 3600.0],
        "plans": [{"plan_id": "old", "demolished_ids": ["R"], "cleared_physical_cell_ids": []}],
        "roof_replacement_rule": {"stale": "must disappear"},
    }
    template_path = save("template.json", template)
    experiment = save(
        "template-experiment.json",
        {
            "config": template,
            "config_sha256": file_sha256(template_path),
        },
    )
    rows = []
    for i, offset in enumerate([10, 20, 30]):
        rows.append(
            {
                "plan_id": f"compact{i}",
                "inner_hull_buffer_m": offset,
                "demolished_ids": ["R"],
                "cleared_physical_cell_ids": [i + 1],
                "finite_catalog_actual_sources": actual,
                "first_retained_sources_match_original_seed": True,
                "protected_original_roof_node_cleared_count": 0,
                "core_and_actual_source_geometries_inside_inner": True,
                "nominal_ring_outside_parent_rectangle_area_m2": 0,
                "remaining_potential_node_positive_ring_overlap_count": 0,
                "cleared_vegetation_ground_footprint_area_m2": 25,
                "conservative_cut_evidence": {
                    "certified_no_potential_radiation_or_contact_edge": True,
                    "potential_unordered_inner_outer_pairs_within_radiation_support": 0,
                    "minimum_inner_outer_physical_polygon_distance_m": 21,
                    "radiation_support_radius_m": 15,
                },
            }
        )
    catalog = save(
        "catalog.json",
        {
            "schema_version": "heritage-balanced-core-source-ring-catalog-1",
            "reference_master_problem_sha256": content_hash(problem),
            "reference_master_experiment_sha256": file_sha256(ref),
            "inventory_sha256": "inventory",
            "new_CA_checks": 0,
            "outcome_files_read_for_design": [],
            "original_seed_ids": ["R"],
            "core_ids": ["C"],
            "finite_source_receipt": {"actual_sources": actual},
            "plans": rows,
        },
    )
    args = [template_path, catalog, tmp_path / "draft.json", tmp_path / "receipt.json"]
    return module, args, experiment


def test_draft_is_immutable_24_checks_and_preserves_reference_physics_without_producer(preparation):
    module, args, experiment = preparation
    originals = {str(p): p.read_bytes() for p in args[0].parent.iterdir() if p.is_file()}
    receipt = module.prepare(*args, template_experiment_path=experiment)
    config = json.loads(args[2].read_text())
    old = json.loads(args[0].read_text())
    assert config["source_coverage_policy"] == "finite_declared_plans"
    assert config["max_new_checks"] == config["max_total_checks"] == 24
    assert config["terminal_horizon_cap_s"] == config["max_wall_time_s"] == 172800
    assert config["source_area_limit_m2"] == 443
    assert "roof_replacement_rule" not in config
    assert all(config.get(k) == old.get(k) for k in module.PHYSICAL_KEYS)
    assert receipt["new_CA_checks"] == 0 and not receipt["producer_snapshot_frozen"]
    assert len(receipt["finite_first_retained_matrix"]) == 12
    assert receipt == module.prepare(*args, template_experiment_path=experiment)
    assert originals == {p: Path(p).read_bytes() for p in originals}
    assert not any(p.is_dir() for p in args[0].parent.iterdir())


def test_changed_frozen_input_is_rejected_before_any_draft_write(preparation):
    module, args, experiment = preparation
    (args[0].parent / "source0.json").write_text('{"source":"changed"}')
    with pytest.raises(ValueError, match="frozen input"):
        module.prepare(*args, template_experiment_path=experiment)
    assert not args[2].exists() and not args[3].exists()


@pytest.mark.parametrize("change", ["cut", "source"])
def test_incomplete_geometry_or_changed_source_scope_cannot_enter_finite_draft(preparation, change):
    module, args, experiment = preparation
    catalog = json.loads(args[1].read_text())
    if change == "cut":
        catalog["plans"][0]["conservative_cut_evidence"][
            "certified_no_potential_radiation_or_contact_edge"
        ] = False
    else:
        catalog["finite_source_receipt"]["actual_sources"]["family0"] = "S1"
    args[1].write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        module.prepare(*args, template_experiment_path=experiment)
    assert not args[2].exists() and not args[3].exists()


def test_changed_existing_output_cannot_be_overwritten_or_partially_replaced(preparation):
    module, args, experiment = preparation
    module.prepare(*args, template_experiment_path=experiment)
    args[2].write_text('{"tampered":"preserve for inspection"}')
    existing = {str(p): p.read_bytes() for p in args[0].parent.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="immutable balanced draft"):
        module.prepare(*args, template_experiment_path=experiment)
    assert existing == {p: Path(p).read_bytes() for p in existing}
