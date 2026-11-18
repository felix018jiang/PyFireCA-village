"""Build a small declared synthetic two-core P1 case; never a surveyed village."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from shapely.geometry import box

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import BuildingInventory, BuildingObject, HeritageTarget
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import resolve_vegetation_physics
from pyfireca.village.solver_v3 import V3Scenario, V3SimulationRequest
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.vegetation_config import VegetationCase
from pyfireca.village.vegetation_materials import load_vegetation_profiles
from pyfireca.village.vegetation_prepare import building_cell_intersections
from pyfireca.village.workflow_v3 import V3CaseBundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[3]


def make_case(
    *,
    radius=3.0,
    ignition_temperature_K=520.0,
    geometries=None,
    candidate_ids=("M", "U"),
    fixed_source_ids=("S",),
):
    grid = VillageGridSpec(9, 13, 2, 0, 18)
    geometries = geometries or {
        "S": box(4, 8, 6, 10),
        "M": box(6, 8, 8, 10),
        "G": box(8, 8, 10, 10),
        "F": box(8, 6, 10, 8),
        "U": box(16, 12, 18, 14),
    }
    buildings = tuple(
        BuildingObject(i, n, "fengyuqiao" if i == "F" else "building", g)
        for n, (i, g) in enumerate(geometries.items())
    )
    inventory = BuildingInventory(
        buildings,
        (
            HeritageTarget("G", "gulou", "synthetic_role_not_real_building", "synthetic"),
            HeritageTarget("F", "fengyuqiao", "synthetic_role_not_real_building", "synthetic"),
        ),
        fixed_source_ids=fixed_source_ids,
        candidate_ids=candidate_ids,
        candidate_evidence="synthetic_technical_candidates_not_surveyed_or_implementation_plans",
    )
    rows, coverage, _ = building_cell_intersections(inventory.structures, grid)
    occupied = coverage > 0
    owner, maximum = np.full(grid.shape, -1, np.int32), np.zeros(grid.shape)
    for identifier, row, col, area in rows:
        if area > maximum[row, col]:
            maximum[row, col] = area
            owner[row, col] = inventory.by_id[identifier].object_id
    profiles = ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml"
    library = load_vegetation_profiles(profiles)
    covers = np.where(occupied, 5, 0).astype(np.int16)
    profile_ids = np.where(
        occupied, library.by_landcover["building"].id, library.by_landcover["bare_or_road"].id
    )
    mask = np.ones(grid.shape, bool)
    case = VegetationCase(
        path=profiles,
        case_id="synthetic_heritage_p1",
        evidence="synthetic",
        prepared_dir=profiles.parent,
        grid=grid,
        profiles=library,
        profile_id_by_cell=profile_ids,
        landcover_index=covers,
        domain_mask=mask,
        known_mask=mask,
        evidence_index=np.zeros(grid.shape, np.int16),
        burnable_mask=occupied,
        coverage_fraction=np.where(occupied, coverage, 1).astype(np.float64),
        weather={},
        scenario={},
        transport={},
        scope={},
        resources={},
        ignitions=(),
        spatial_budgets=(),
    )
    physics = resolve_vegetation_physics(case).arrays
    physics = replace(physics, ignition_temperature_K=np.where(occupied, ignition_temperature_K, 0))
    transport = V3RadiativeTransportParameters(radius, 0.5, 0, 0, 0, 0)
    edges = build_v3_radiative_transport_edges(
        grid,
        occupied,
        occupied,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        transport,
    )
    events, ignitions = [], []
    for identifier in fixed_source_ids:
        point = inventory.by_id[identifier].geometry.representative_point()
        row, col = grid.locate(point.x, point.y)
        events.append(VillageIgnitionEvent(0, row, col))
        ignitions.append(Ignition((point.x, point.y)))
    request = V3SimulationRequest(
        grid,
        mask,
        mask,
        occupied,
        occupied,
        occupied,
        occupied,
        covers,
        covers,
        owner,
        physics,
        edges,
        V3Scenario(1.0, 1200.0, 300.0, 0.01, tuple(events)),
    )
    bundle = V3CaseBundle(
        request,
        {0: "inert", 5: "synthetic_equivalent_timber"},
        {0: "bare_or_road", 5: "building"},
        {
            "transport_parameters": asdict(transport),
            "building_labels": inventory.labels,
            "evidence": "synthetic_not_site_calibrated",
        },
    )
    return (
        bundle,
        transport,
        inventory,
        EvaluationScenario("internal-source", tuple(ignitions), 1200),
    )


def write_example(output, *, radius=3.0, ignition_temperature_K=520.0):
    output.mkdir(parents=True, exist_ok=False)
    bundle, _, inventory, scenario = make_case(
        radius=radius, ignition_temperature_K=ignition_temperature_K
    )
    spatial = output / "spatial"
    spatial.mkdir()
    (spatial / "buildings.json").write_text(json.dumps({"buildings": inventory.structures}))
    write_v3_case_bundle(
        bundle.request,
        output / "case",
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
        metadata={**bundle.metadata, "spatial_assets_dir": str(spatial.resolve())},
    )
    config = {
        "schema_version": "heritage-p1-study-1",
        "case_dir": "case",
        "synthetic_physical_assumptions": {
            "exposure_radius_m": radius,
            "ignition_temperature_K": ignition_temperature_K,
            "scope": "mechanism_control_not_measured_timber_or_safe_distance",
        },
        "targets": [asdict(t) for t in inventory.targets],
        "fixed_source_ids": inventory.fixed_source_ids,
        "candidate_ids": inventory.candidate_ids,
        "candidate_evidence": inventory.candidate_evidence,
        "scenarios": [asdict(scenario)],
        "plans": [
            {"plan_id": "baseline", "demolished_ids": []},
            {
                "plan_id": "remove-middle",
                "demolished_ids": ["M"],
                "ground_evidence": "synthetic_clear_all_debris_and_leave_inert_surface",
            },
        ],
    }
    (output / "study.json").write_text(json.dumps(config, indent=2) + "\n")
    return output / "study.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--radius-m", type=float, default=3.0)
    parser.add_argument("--ignition-temperature-k", type=float, default=520.0)
    args = parser.parse_args()
    print(
        write_example(
            args.output, radius=args.radius_m, ignition_temperature_K=args.ignition_temperature_k
        )
    )
