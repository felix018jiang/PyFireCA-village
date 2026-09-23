from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.resource_sensitivity_v3 import build_resource_sensitivity_report


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    spatial = root / "spatial"
    physics = root / "physics"
    (spatial / "arrays").mkdir(parents=True)
    (physics / "arrays").mkdir(parents=True)
    manifest = {
        "schema_version": "prepared-village-vegetation-spatial-1",
        "grid": {
            "height": 1,
            "width": 3,
            "cell_size_m": 1.0,
            "x_min_m": 0.0,
            "y_max_m": 1.0,
            "coordinate_mode": "projected",
            "crs": "EPSG:32649",
        },
    }
    (spatial / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (physics / "manifest.json").write_text(json.dumps({"stage": "V03"}), encoding="utf-8")
    np.save(spatial / "arrays/domain_mask.npy", np.ones((1, 3), dtype=bool))
    np.save(spatial / "arrays/known_mask.npy", np.ones((1, 3), dtype=bool))
    shape = (1, 3)
    source = np.asarray([[True, False, False]])
    area = np.ones(shape)
    dry = np.where(source, 1.0, 0.0)
    pyro = dry.copy()
    peak_hrrpua = np.where(source, 1.0, 0.0)
    peak_hrr = peak_hrrpua.copy()
    available = pyro * 1_000.0
    curve = peak_hrr * (1.0 / 3.0 + 1.0 + 0.5)
    fields = {
        name: np.zeros(shape, dtype=np.float64) for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    fields.update(
        coverage_fraction=area,
        covered_area_m2=area,
        initial_dry_fuel_mass_kg=dry,
        live_dry_fuel_mass_kg=dry / 2.0,
        dead_dry_fuel_mass_kg=dry / 2.0,
        pyrolysable_dry_fuel_mass_kg=pyro,
        heat_of_combustion_J_kg_dry=np.where(source, 1_000.0, 0.0),
        peak_hrrpua_W_m2_active=peak_hrrpua,
        peak_hrr_W=peak_hrr,
        growth_time_s=np.where(source, 1.0, 0.0),
        steady_time_s=np.where(source, 1.0, 0.0),
        decay_time_s=np.where(source, 1.0, 0.0),
        active_surface_area_ratio=source.astype(float),
        active_surface_area_m2=np.where(source, 1.0, 0.0),
        pyrolysable_dry_mass_fraction=source.astype(float),
        receiver_area_ratio=area,
        receiver_area_m2=area,
        source_height_m=np.where(source, 1.0, 0.0),
        receiver_height_m=area,
        available_energy_J=available,
        configured_curve_energy_J=curve,
    )
    for name, value in fields.items():
        np.save(physics / "arrays" / f"{name}.npy", value)
    snapshot = root / "case.snapshot.yml"
    snapshot.write_text(
        yaml.safe_dump(
            {
                "transport": {
                    "exposure_radius_m": {"value": 1.0},
                    "near_field_regularization_m": {"value": 0.5},
                    "atmospheric_extinction_m_inv": {"value": 0.0},
                    "flame_tilt_coefficient_s_m": {"value": 0.0},
                },
                "weather": {
                    "wind_speed_m_s": {"value": 0.0},
                    "wind_from_deg": {"value": 0.0},
                },
                "resources": {"max_edge_count": 1, "max_memory_mb": 2},
            }
        ),
        encoding="utf-8",
    )
    return spatial, physics, snapshot


def test_resource_sensitivity_counts_receivers_without_building_edges(tmp_path: Path) -> None:
    spatial, physics, snapshot = _write_fixture(tmp_path)
    report = build_resource_sensitivity_report(
        spatial,
        physics,
        snapshot,
        output=tmp_path / "report.json",
        radii_m=(1.0,),
        end_time_s=10.0,
        sample_interval_s=5.0,
    )

    assert report["graph_construction_executed"] is False
    assert report["classification"]["source_burnable_cell_count"] == 1
    assert report["classification"]["inert_receiver_cell_count"] == 2
    assert report["active_case"]["estimate"]["estimate"]["candidate_edge_upper_bound"] == 1
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "ready_for_resource_gate"
