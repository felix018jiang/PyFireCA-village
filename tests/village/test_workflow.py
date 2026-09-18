from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from pyfireca.village.prepare import load_prepared_village, prepare_village_case
from pyfireca.village.prepared_workflow_v2 import (
    load_prepared_v2_case,
    run_prepared_v2_case,
)
from pyfireca.village.workflow import run_village_config, validate_village_run


def _write_case(root: Path) -> tuple[Path, Path]:
    (root / "imagery").mkdir(parents=True)
    (root / "annotations").mkdir()
    (root / "materials").mkdir()
    Image.new("RGB", (6, 4), (73, 90, 65)).save(root / "imagery" / "village.png")
    (root / "imagery" / "image_ref.json").write_text(
        json.dumps(
            {
                "schema_version": "image-ref-1",
                "coordinate_mode": "local_metric",
                "width_px": 6,
                "height_px": 4,
                "affine_pixel_to_world": [1, 0, 0, 0, -1, 4],
                "world_unit": "m",
                "north_reference": "local_y_positive",
            }
        ),
        encoding="utf-8",
    )
    (root / "annotations" / "buildings.local.json").write_text(
        json.dumps(
            {
                "schema_version": "buildings-1",
                "coordinate_space": "image_pixels",
                "image_file": "../imagery/village.png",
                "pixel_coordinates": "edge_based",
                "buildings": [
                    {
                        "building_id": "B001",
                        "polygons": [
                            {
                                "exterior": [[0, 0], [6, 0], [6, 4], [0, 4], [0, 0]],
                                "holes": [],
                            }
                        ],
                        "properties": {"outline_type": "footprint", "height_m": None},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog = {
        "schema_version": "material-catalog-1",
        "library_id": "workflow-fixture-v1",
        "materials": [
            {
                "id": "demo_wood",
                "label": "Synthetic wood",
                "role": "building_fuel",
                "burnable": True,
                "evidence": "synthetic",
                "source_id": "workflow-fixture",
                "parameters": {
                    "fuel_load_kg_m2": 2.0,
                    "heat_of_combustion_J_kg": 1_000_000.0,
                    "mass_loss_flux_kg_m2_s": 0.1,
                    "active_area_ratio": 1.0,
                    "emission_fraction": 0.2,
                    "absorptivity": 0.8,
                    "ignition_dose_J_m2": 1000.0,
                    "exposure_decay_s": 10.0,
                },
            }
        ],
    }
    (root / "materials" / "material_catalog.yml").write_text(
        yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
    )
    with (root / "materials" / "material_grid.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["row", "col", "material_id"])
        for row in range(2):
            for col in range(3):
                writer.writerow([row, col, "demo_wood"])
    case = {
        "schema_version": "village-case-1",
        "case_id": "workflow-fixture",
        "mode": "synthetic_demo",
        "imagery": {"path": "imagery/village.png", "reference": "imagery/image_ref.json"},
        "buildings": {"path": "annotations/buildings.local.json", "format": "local_json"},
        "materials": {
            "source": "materials/material_grid.csv",
            "format": "grid_csv",
            "catalog": "materials/material_catalog.yml",
        },
        "spatial": {
            "coordinate_mode": "local_metric",
            "crs": None,
            "grid": {"x_min_m": 0, "y_max_m": 4, "height": 2, "width": 3, "cell_size_m": 2},
        },
    }
    scenario = {
        "schema_version": "village-scenario-1",
        "scenario_id": "workflow-run",
        "model": {
            "name": "effective_exposure_ca_v1",
            "exposure_radius_m": 3.0,
            "decay_length_m": 5.0,
            "wind_beta_s_m": 0.0,
            "coefficients_evidence": "synthetic",
            "coefficients_source_id": "workflow-fixture",
        },
        "weather": {"wind_speed_m_s": 0.0, "wind_from_deg": 0.0},
        "ignitions": [{"cell": {"row": 0, "col": 0}, "time_s": 0, "kind": "direct"}],
        "numerics": {"max_dt_s": 0.5, "end_time_s": 10.0},
        "outputs": {"sample_interval_s": 2.0},
        "visualization": {"heating_display_fraction": 0.1},
    }
    case_path = root / "case.yml"
    scenario_path = root / "scenario.yml"
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    scenario_path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
    return case_path, scenario_path


def test_prepare_validate_and_run_end_to_end(tmp_path: Path) -> None:
    case, scenario = _write_case(tmp_path / "source")
    prepared = prepare_village_case(case, tmp_path / "prepared")
    domain = load_prepared_village(prepared)
    assert domain.grid.shape == (2, 3)
    assert np.all(domain.burnable_mask)
    request, _ = validate_village_run(prepared, scenario)
    assert len(request.exposure_edges.source) > 0

    result, artifacts = run_village_config(prepared, scenario, tmp_path / "run")
    assert result.final_state.time_s == 10.0
    assert result.snapshots[0].time_s == 0.0
    assert result.snapshots[-1].time_s == 10.0
    run_record = json.loads((artifacts.directory / "run.json").read_text())
    assert run_record["status"] == "completed"
    assert (artifacts.outputs / "state_samples.npy").is_file()
    building_rows = list(
        csv.DictReader((artifacts.outputs / "buildings.csv").open(encoding="utf-8"))
    )
    assert building_rows[0]["building_id"] == "B001"
    assert float(building_rows[0]["mapped_area_m2"]) == 24.0
    viewer = artifacts.directory / "viewer"
    assert (viewer / "index.html").is_file()
    manifest = json.loads((viewer / "manifest.json").read_text())
    assert (viewer / "state.bin").stat().st_size == manifest["frame_count"] * 2 * 3


def test_prepared_grid_runs_v2_with_building_blockers_and_scalar_viewer(tmp_path: Path) -> None:
    case_path, _ = _write_case(tmp_path / "source")
    prepared = prepare_village_case(case_path, tmp_path / "prepared")
    v2_scenario = {
        "schema_version": "village-v2-prepared-scenario-1",
        "scenario_id": "prepared-v2-test",
        "model": "effective_exposure_ca_v2",
        "evidence": "synthetic_numerical_fixture_not_physical_calibration",
        "material_mappings": {
            "demo_wood": {
                "construction_id": "synthetic_exposed_wood",
                "burnable": True,
                "ignition_mode": "piloted",
                "material_parameters": {
                    "fuel_load_kg_m2": 1.0,
                    "moisture_content_dry_basis": 0.0,
                    "heat_of_combustion_J_kg": 1000.0,
                    "ignition_temperature_K": 300.1,
                    "peak_hrrpua_W_m2": 100.0,
                    "radiative_fraction": 1.0,
                    "absorptivity": 1.0,
                    "growth_time_s": 3.0,
                    "decay_time_s": 2.0,
                },
                "construction_parameters": {
                    "effective_heated_dry_mass_kg_m2": 0.01,
                    "specific_heat_J_kg_K": 100.0,
                    "emissivity": 0.0,
                    "ambient_heat_transfer_coefficient_W_m2_K": 0.0,
                    "backing_conductance_W_m2_K": 0.0,
                    "receiver_area_m2": 1.0,
                    "active_surface_area_m2": 1.0,
                    "pyrolysable_dry_mass_fraction": 1.0,
                },
            }
        },
        "blockers": {
            "background_transmissivity": 1.0,
            "building_transmissivity": 0.5,
        },
        "transport": {
            "calibration": "synthetic_geometry_test",
            "parameters": {
                "exposure_radius_m": 3.0,
                "effective_flame_height_m": 2.0,
                "effective_receiver_height_m": 1.0,
                "near_field_regularization_m": 0.25,
                "atmospheric_extinction_m_inv": 0.0,
                "wind_speed_m_s": 0.0,
                "wind_from_deg": 0.0,
                "flame_tilt_coefficient_s_m": 0.0,
            },
        },
        "scenario": {
            "ambient_temperature_K": 300.0,
            "max_dt_s": 1.0,
            "end_time_s": 4.0,
            "sample_interval_s": 1.0,
            "ignitions": [{"cell": {"row": 0, "col": 0}, "time_s": 0.0, "kind": "direct"}],
        },
    }
    scenario_path = tmp_path / "scenario-v2.synthetic.yml"
    scenario_path.write_text(yaml.safe_dump(v2_scenario, sort_keys=False), encoding="utf-8")

    resolved = load_prepared_v2_case(prepared, scenario_path)
    assert np.all(resolved.blocker_transmissivity == 0.5)
    assert len(resolved.request.exposure_edges.source) > 0

    result, artifacts = run_prepared_v2_case(prepared, scenario_path, tmp_path / "run-v2")
    assert np.count_nonzero(np.isfinite(result.final_state.ignition_time_s)) > 1
    viewer = artifacts.directory / "viewer"
    manifest = json.loads((viewer / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "village-viewer-2"
    assert manifest["time_display_unit"] == "min"
    expected_float_bytes = manifest["frame_count"] * 2 * 3 * 4
    assert (viewer / "temperature.bin").stat().st_size == expected_float_bytes
    assert (viewer / "hrr.bin").stat().st_size == expected_float_bytes
    building_cells = json.loads((viewer / "building_cells.json").read_text(encoding="utf-8"))
    assert building_cells["buildings"]["B001"] == [0, 1, 2, 3, 4, 5]
