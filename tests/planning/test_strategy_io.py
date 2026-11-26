"""Village input binding and spatial/economic output, without fire solves."""

import csv
import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, shape
from shapely.ops import transform

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import (
    BuildingInventory,
    BuildingObject,
    HeritageTarget,
    content_hash,
)
from pyfireca.planning.strategy_io import (
    demolition_geometry,
    export_strategy,
    file_sha256,
    load_strategy_inputs,
    read_json,
    validate_material_binding,
    write_json,
)
from pyfireca.village.workflow_v3 import write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "strategy_input_fixture", ROOT / "examples/research/heritage/synthetic_case.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
PROFILES = ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml"


def binding():
    return {
        "schema_version": "heritage-material-binding-1",
        "profile_library": str(PROFILES),
        "material_profiles": {"0": "inert_bare_or_road", "5": "synthetic_building"},
        "roof_profile_id": "synthetic_building",
    }


@pytest.fixture
def prepared_inputs(tmp_path):
    bundle, _, inventory, _ = fixture.make_case()
    grid = replace(bundle.request.grid, coordinate_mode="projected", crs="EPSG:32649")
    bundle = replace(bundle, request=replace(bundle.request, grid=grid))
    tiff = tmp_path / "imagery.tif"
    with rasterio.open(
        tiff,
        "w",
        driver="GTiff",
        count=3,
        dtype="uint8",
        width=grid.width,
        height=grid.height,
        crs=grid.crs,
        transform=from_origin(0, 18, 2, 2),
    ) as image:
        image.write(np.full((3, grid.height, grid.width), 200, np.uint8))
    metadata = {
        **bundle.metadata,
        "sources_sha256": {
            str(tiff): file_sha256(tiff),
            str(PROFILES): file_sha256(PROFILES),
        },
    }
    write_v3_case_bundle(
        bundle.request,
        tmp_path / "case",
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata=metadata,
    )
    records, shapes = [], []
    for b in inventory.buildings:
        row = b.record()
        row["properties"]["source_shape_index"] = b.object_id
        records.append(row)
        shapes.append(
            {
                "label": "gulou" if b.building_id == "G" else b.source_label,
                "shape_type": "polygon",
                "points": [[x / 2, (18 - y) / 2] for x, y in b.geometry.exterior.coords],
            }
        )
    write_json(tmp_path / "buildings.json", {"buildings": records})
    write_json(
        tmp_path / "annotation.json",
        {
            "imageWidth": grid.width,
            "imageHeight": grid.height,
            "shapes": shapes,
        },
    )
    write_json(
        tmp_path / "image_ref.json",
        {
            "width_px": grid.width,
            "height_px": grid.height,
            "affine_pixel_to_world": [2, 0, 0, 0, -2, 18],
        },
    )
    write_json(
        tmp_path / "heritage.json",
        {
            "schema_version": "heritage-inventory-1",
            "targets": [asdict(t) for t in inventory.targets],
            "fixed_source_ids": inventory.fixed_source_ids,
            "candidate_ids": inventory.candidate_ids,
            "candidate_evidence": inventory.candidate_evidence,
        },
    )
    write_json(tmp_path / "materials.json", binding())
    problem = {
        "case_sha256": request_fingerprint(bundle.request),
        "inventory_sha256": inventory.sha256,
        "transport_parameters": bundle.metadata["transport_parameters"],
        "candidate_ids": sorted(inventory.candidate_ids),
        "horizon_s": 1200,
        "scales_m": [1, 0.5],
        "quadrature_spacing_m": 0.5,
        "source_policy": "synthetic",
        "overlap_policy": "retain_shared_roof",
    }
    (tmp_path / "physical").mkdir()
    write_json(
        tmp_path / "physical/experiment.json",
        {
            "problem": problem,
            "problem_sha256": content_hash(problem),
        },
    )
    manifest = {
        "schema_version": "heritage-strategy-input-1",
        "village_id": "second-synthetic-name",
        "place_name": "Synthetic village",
        "spatial_crs": grid.crs,
        "inputs": {
            "prepared_case": "case",
            "imagery_tiff": "imagery.tif",
            "footprints": "buildings.json",
            "materials": "materials.json",
            "heritage": "heritage.json",
            "annotations": "annotation.json",
            "image_reference": "image_ref.json",
        },
        "search": {
            "mode": "saved_physical_checks",
            "physical_root": "physical",
            "candidate_ids": sorted(inventory.candidate_ids),
            "max_saved_check_lookups": 64,
        },
        "costs": None,
        "pending_data": [{"id": "economic_costs", "status": "pending"}],
    }
    path = tmp_path / "input.json"
    write_json(path, manifest)
    return path, inventory


def test_unified_input_preserves_case_inventory_and_all_33_material_fields(prepared_inputs):
    path, original_inventory = prepared_inputs
    loaded = load_strategy_inputs(path)
    assert loaded.inventory.sha256 == original_inventory.sha256
    assert loaded.receipt["materials"]["physical_parameter_field_count"] == 33
    assert loaded.receipt["materials"]["all_physical_parameter_fields_match"]
    assert loaded.receipt["economic_cost_status"] == "missing" and loaded.costs is None
    assert loaded.receipt["case_request_sha256"] == request_fingerprint(loaded.bundle.request)


@pytest.mark.parametrize(
    "fault, message",
    [
        ("crs", "metre CRS"),
        ("registration", "image dimensions"),
        ("heritage", "core identities"),
        ("candidates", "candidate scope"),
    ],
)
def test_mismatched_inputs_stop_before_search(prepared_inputs, fault, message):
    path, _ = prepared_inputs
    manifest = read_json(path)
    if fault == "crs":
        manifest["spatial_crs"] = "EPSG:4326"
    elif fault == "registration":
        reference = read_json(path.parent / "image_ref.json")
        reference["width_px"] += 1
        write_json(path.parent / "image_ref.json", reference)
    elif fault == "heritage":
        identities = read_json(path.parent / "heritage.json")
        identities["targets"] = identities["targets"][:1]
        write_json(path.parent / "heritage.json", identities)
    else:
        manifest["search"]["candidate_ids"] = ["M"]
    write_json(path, manifest)
    with pytest.raises(ValueError, match=message):
        load_strategy_inputs(path)


def test_material_change_or_missing_assignment_is_not_silently_accepted(prepared_inputs):
    path, _ = prepared_inputs
    loaded = load_strategy_inputs(path)
    before = request_fingerprint(loaded.bundle.request)
    changed = replace(
        loaded.bundle,
        request=replace(
            loaded.bundle.request,
            parameters=replace(
                loaded.bundle.request.parameters,
                specific_heat_J_kg_K=loaded.bundle.request.parameters.specific_heat_J_kg_K * 2,
            ),
        ),
    )
    with pytest.raises(ValueError, match="specific_heat_J_kg_K"):
        validate_material_binding(changed, binding(), PROFILES)
    incomplete = binding()
    del incomplete["material_profiles"]["5"]
    with pytest.raises(ValueError, match="cover exactly"):
        validate_material_binding(loaded.bundle, incomplete, PROFILES)
    assert request_fingerprint(loaded.bundle.request) == before


def test_geojson_csv_and_cost_export_retain_shared_roof_and_unknown_money(tmp_path):
    x, y = 500000, 2800000
    inventory = BuildingInventory(
        tuple(
            BuildingObject(i, n, "building", geometry)
            for n, (i, geometry) in enumerate(
                [
                    ("A", box(x, y, x + 2, y + 2)),
                    ("B", box(x + 1, y, x + 3, y + 2)),
                    ("R", box(x + 2, y, x + 4, y + 2)),
                    ("G", box(x + 8, y, x + 9, y + 1)),
                ]
            )
        ),
        (HeritageTarget("G", "gulou", "synthetic", "synthetic"),),
        candidate_ids=("A", "B"),
        candidate_evidence="synthetic",
    )
    rows, ledger, cleared, shared = demolition_geometry(inventory, ("A", "B"))
    assert ledger["nominal_footprint_area_m2"] == 8
    assert ledger["removed_footprint_union_m2"] == 6
    assert cleared.area == 4 and shared.area == 2
    assert sum(r["cleared_area_allocated_m2"] for r in rows) == 4
    inputs = SimpleNamespace(
        inventory=inventory,
        costs=None,
        manifest={"spatial_crs": "EPSG:32649", "pending_data": []},
        receipt={"materials": {"roof_profile_id": "synthetic_building"}},
    )
    replay = {
        "best_ids": ["A", "B"],
        "strategy_result": {
            "best_ids": ["A", "B"],
            "candidate_ids": ["A", "B"],
            "objective_policy": "count_then_area",
            "best_declared_cost": None,
            "optimality_proven": True,
        },
    }
    exported = export_strategy(inputs, replay, tmp_path)
    assert exported["declared_economic_cost"] is None and exported["cost_unit"] is None
    with (tmp_path / "demolition-list.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert all(r["declared_cost"] == "" and r["cost_status"] == "pending" for r in rows)
    features = json.loads((tmp_path / "strategy-buildings.geojson").read_text())["features"]
    assert {
        f["properties"]["building_id"] for f in features if f["properties"]["action"] == "demolish"
    } == {"A", "B"}
    back = Transformer.from_crs("EPSG:4326", "EPSG:32649", always_xy=True)
    for feature in read_json(tmp_path / "clearance.geojson")["features"]:
        geometry = transform(back.transform, shape(feature["geometry"]))
        assert geometry.area == pytest.approx(feature["properties"]["area_m2"], abs=1e-7)
    with pytest.raises(ValueError, match="protected"):
        demolition_geometry(inventory, ("G",))
    inputs.costs = {"values": {"A": 100, "B": 250}, "unit": "CNY", "evidence": "test estimate"}
    replay["strategy_result"]["best_declared_cost"] = 350
    replay["strategy_result"]["objective_policy"] = "count_then_declared_cost"
    priced = tmp_path / "priced"
    priced.mkdir()
    cost_report = export_strategy(inputs, replay, priced)
    assert cost_report["declared_economic_cost"] == 350 and cost_report["cost_unit"] == "CNY"
    assert cost_report["economic_cost_status"] == "declared_with_evidence"
