"""Source confirmation is preregistered before revealing draws and binding plans."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import box

from pyfireca.planning.heritage import BuildingInventory, BuildingObject, HeritageTarget
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def preparer(monkeypatch):
    path = ROOT / "scripts/prepare_heritage_independent_confirmation.py"
    spec = importlib.util.spec_from_file_location("independent_confirmation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buildings = tuple(
        BuildingObject(name, number, "building", geometry)
        for number, (name, geometry) in enumerate(
            {
                "S": box(0, 0, 10, 10),
                "M": box(15, 0, 25, 10),
                "U": box(200, 0, 210, 10),
                "G": box(0, 20, 10, 30),
                "F": box(30, 20, 40, 30),
            }.items()
        )
    )
    inventory = BuildingInventory(
        buildings,
        (
            HeritageTarget("G", "gulou", "synthetic", "synthetic"),
            HeritageTarget("F", "fengyuqiao", "synthetic", "synthetic"),
        ),
        candidate_ids=("S", "M", "U"),
        candidate_evidence="synthetic_test_only",
    )
    monkeypatch.setattr(module, "metadata_inventory", lambda path: (inventory, {}))
    return module, inventory


def test_registration_has_no_plan_or_revealed_source_and_is_reproducible(preparer, tmp_path):
    module, inventory = preparer
    r = module.registration("batuan", tmp_path / "study.json")
    assert r == module.registration("batuan", tmp_path / "study.json")
    assert not r["source_priorities_materialized"]
    assert r["final_candidate_manifest_sha256"] is None
    assert r["new_CA_checks"] == 0
    assert r["inventory_sha256"] == inventory.sha256
    assert [x["kind"] for x in r["allocation"]] == [
        "near_core",
        "near_core",
        "background",
        "background",
    ]
    assert "plans" not in r and "draws" not in r


def test_four_full_global_priorities_keep_all_qualified_sources_and_no_resampling(
    preparer, tmp_path
):
    module, inventory = preparer
    r = module.registration("batuan", tmp_path / "study.json")
    protocols = module.freeze_four_protocols(r, inventory)
    assert protocols == module.freeze_four_protocols(r, inventory)
    assert len(protocols) == 4
    for protocol in protocols:
        draw = protocol["draws"][0]
        assert set(draw["priority_ids"]) == {"S", "M", "U"}
        assert draw["stratum"] == "all_retained_sources"
        actual = realize_sources(protocol, inventory)[0]
        assert actual["footprint"].geometry.area == pytest.approx(25, abs=1e-6)
        assert actual["weight"] == 1
    # Both background draws may select U; independence does not forbid repeats.
    assert protocols[2]["draws"][0]["baseline_source_id"] == "U"
    assert protocols[3]["draws"][0]["baseline_source_id"] == "U"
    assert protocols[2]["draws"][0]["draw_id"] != protocols[3]["draws"][0]["draw_id"]


def frozen_manifest(module, inventory, r):
    return {
        "schema_version": module.FINAL_SCHEMA,
        "frozen": True,
        "village": "batuan",
        "inventory_sha256": inventory.sha256,
        "confirmation_registration_sha256": r["registration_sha256"],
        "plans": [
            {"plan_id": "baseline", "demolished_ids": [], "cleared_physical_cell_ids": []},
            {"plan_id": "candidate", "demolished_ids": ["S"], "cleared_physical_cell_ids": [12]},
        ],
        "refinement_ids": ["S"],
    }


def test_bind_requires_frozen_plans_and_generates_only_finite_union_no_mesh(preparer, tmp_path):
    module, inventory = preparer
    r = module.registration("batuan", tmp_path / "study.json")
    rp, fp, output = tmp_path / "registration.json", tmp_path / "final.json", tmp_path / "prepared"
    write_json(rp, r)
    final = frozen_manifest(module, inventory, r)
    write_json(fp, {**final, "frozen": False})
    with pytest.raises(ValueError, match="explicitly freeze"):
        module.bind(rp, fp, output)
    assert not output.exists()
    write_json(fp, final)
    receipt = module.bind(rp, fp, output)
    assert receipt["planned_CA_checks"] == 16
    assert not receipt["mesh_or_radiation_graph_constructed"]
    assert receipt["new_CA_checks"] == 0
    assert receipt == module.bind(rp, fp, output)
    config = read_json(output / "confirmation-config.json")
    assert config["terminal_fire_verification"]
    assert config["terminal_horizon_cap_s"] == 21600
    assert config["source_policy"] == "retained_sources"
    assert config["new_independent_fire_draw_count"] == 4
    assert "reference_master_experiment_path" not in config
    for fire in receipt["common_source_and_refinement_requirements"]:
        assert set(fire["actual_source_union"]) <= set(
            fire["existing_joint_runner_conservative_prefix"]
        )
        assert set(fire["minimum_common_refined_ids"]) >= {"S", "G", "F"}


def test_registered_source_code_and_physical_inputs_are_bound(preparer, tmp_path):
    module, inventory = preparer
    r = module.registration("batuan", tmp_path / "study.json")
    r["generator_source_files_sha256"][str(Path(module.__file__))] = "altered"
    from pyfireca.planning.heritage import content_hash

    r["registration_sha256"] = content_hash(
        {k: v for k, v in r.items() if k != "registration_sha256"}
    )
    rp, fp = tmp_path / "registration.json", tmp_path / "final.json"
    write_json(rp, r)
    write_json(fp, frozen_manifest(module, inventory, r))
    with pytest.raises(ValueError, match="source-generation code changed"):
        module.bind(rp, fp, tmp_path / "out")


def test_empty_core_uses_preregistered_60m_pool_or_stops_before_CA(preparer, tmp_path, monkeypatch):
    module, original = preparer
    for distance, should_stop in ((60, False), (1000, True)):
        inventory = replace(
            original,
            buildings=tuple(
                replace(b, geometry=box(distance, 20, distance + 10, 30))
                if b.building_id == "F"
                else b
                for b in original.buildings
            ),
        )
        monkeypatch.setattr(
            module, "metadata_inventory", lambda path, inventory=inventory: (inventory, {})
        )
        r = module.registration("batuan", tmp_path / "study.json")
        if should_stop:
            with pytest.raises(ValueError, match="empty preregistered 60m"):
                module.freeze_four_protocols(r, inventory)
        else:
            protocols = module.freeze_four_protocols(r, inventory)
            assert "empty_15m_core_targeted" in protocols[0]["confirmatory_pool_type"]
            assert all(set(p["draws"][0]["priority_ids"]) == {"S", "M", "U"} for p in protocols)
