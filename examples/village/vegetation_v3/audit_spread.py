"""Mechanism ablation using unchanged synthetic vegetation profiles.

python examples/village/vegetation_v3/audit_spread.py --output runs/vegetation-spread-new
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from math import isfinite, pi
from pathlib import Path
from time import perf_counter

import numpy as np

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import STEFAN_BOLTZMANN_W_M2_K4
from pyfireca.village.physics_v3 import resolve_vegetation_physics
from pyfireca.village.solver_v3 import V3Scenario, V3SimulationRequest, run_v3_simulation
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.vegetation_config import VegetationCase
from pyfireca.village.vegetation_materials import load_vegetation_profiles
from pyfireca.village.vegetation_prepare import LANDCOVER_IDS
from pyfireca.village.workflow_v3 import write_v3_case_bundle

DIRECTORY = Path(__file__).resolve().parent


def make_request(
    labels: tuple[str, ...],
    *,
    max_dt_s: float = 0.5,
    end_time_s: float = 1200,
    low_building_receiver: bool = False,
    wet: bool = False,
) -> V3SimulationRequest:
    """Resolve the original profiles; only explicitly named controls alter them."""
    grid = VillageGridSpec(1, len(labels), 2, 0, 2)
    shape = grid.shape
    library = load_vegetation_profiles(DIRECTORY / "profiles.synthetic.yml")
    indices = np.asarray([[LANDCOVER_IDS.index(label) for label in labels]], dtype=np.int16)
    profile_ids = np.asarray([[library.by_landcover[label].id for label in labels]])
    mask = np.ones(shape, dtype=bool)
    burnable = np.asarray([[label != "bare_or_road" for label in labels]], dtype=bool)
    case = VegetationCase(
        path=DIRECTORY / "profiles.synthetic.yml",
        case_id="near_field_mechanism_ablation",
        evidence="synthetic",
        prepared_dir=DIRECTORY,
        grid=grid,
        profiles=library,
        profile_id_by_cell=profile_ids,
        landcover_index=indices,
        domain_mask=mask,
        known_mask=mask,
        evidence_index=np.zeros(shape, dtype=np.int16),
        burnable_mask=burnable,
        coverage_fraction=np.ones(shape),
        weather={},
        scenario={},
        transport={},
        scope={},
        resources={},
        ignitions=(),
        spatial_budgets=(),
    )
    physics = resolve_vegetation_physics(case).arrays
    # Inert cells can intercept radiation; they supply no fuel or hot contact.
    physics = replace(
        physics,
        receiver_area_m2=np.where(burnable, physics.receiver_area_m2, grid.cell_area_m2),
        receiver_area_ratio=np.where(burnable, physics.receiver_area_ratio, 1),
        receiver_height_m=np.where(burnable, physics.receiver_height_m, 0.1),
    )
    if low_building_receiver:
        height = physics.receiver_height_m.copy()
        height[indices == LANDCOVER_IDS.index("building")] = 0.2
        physics = replace(physics, receiver_height_m=height)
    if wet:
        moisture = np.where(burnable, 5.0, 0.0)
        live_water = physics.live_dry_fuel_mass_kg * moisture
        dead_water = physics.dead_dry_fuel_mass_kg * moisture
        physics = replace(
            physics,
            live_water_mass_kg=live_water,
            dead_water_mass_kg=dead_water,
            water_mass_kg=live_water + dead_water,
            moisture_content_dry_basis=moisture,
        )
    edges = build_v3_radiative_transport_edges(
        grid,
        source_mask=burnable,
        target_mask=mask,
        source_height_m=physics.source_height_m,
        receiver_height_m=physics.receiver_height_m,
        receiver_area_m2=physics.receiver_area_m2,
        blocker_transmissivity=np.ones(shape),
        parameters=V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0),
    )
    return V3SimulationRequest(
        grid=grid,
        domain_mask=mask,
        known_mask=mask,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=mask,
        piloted_ignition_mask=burnable,
        material_index=indices,
        landcover_index=indices,
        building_index=np.where(indices == LANDCOVER_IDS.index("building"), 0, -1),
        parameters=physics,
        exposure_edges=edges,
        scenario=V3Scenario(max_dt_s, end_time_s, 300, 0.01, (VillageIgnitionEvent(0, 0, 0),)),
        sample_interval_s=60,
    )


def peak_radiation_audit() -> list[dict]:
    """Independent upper-bound equilibrium: one neighbor at 2 m, no wind."""
    records = []
    library = load_vegetation_profiles(DIRECTORY / "profiles.synthetic.yml")
    for profile in library.profiles:
        if not profile.burnable:
            continue
        value = profile.value
        peak = value("peak_hrrpua_W_m2") * value("active_surface_area_ratio") * 4
        distance2 = 4 + (value("receiver_height_m") - value("source_height_m") / 2) ** 2
        absorbed = (
            value("absorptivity")
            * value("radiative_fraction")
            * peak
            * value("receiver_area_ratio")
            / (4 * pi * (distance2 + 0.25))
        )

        def loss(temperature, value=value):
            return (
                value("ambient_heat_transfer_coefficient_W_m2_K")
                + value("backing_conductance_W_m2_K")
            ) * (temperature - 300) + value("emissivity") * STEFAN_BOLTZMANN_W_M2_K4 * (
                temperature**4 - 300**4
            )

        lower, upper = 300, 3000
        for _ in range(80):
            middle = (lower + upper) / 2
            if loss(middle) < absorbed:
                lower = middle
            else:
                upper = middle
        ignition = value("ignition_temperature_K")
        records.append(
            {
                "profile_id": profile.id,
                "absorbed_peak_W_m2": absorbed,
                "loss_at_ignition_W_m2": loss(ignition),
                "equilibrium_upper_bound_K": (lower + upper) / 2,
                "ignition_temperature_K": ignition,
                "single_neighbor_peak_can_reach_threshold": absorbed >= loss(ignition),
                "scope": "original synthetic profile, 2m centers, 0.5m regularization, one source",
            }
        )
    return records


def run_audit(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact.synthetic.yml")
    specs = (
        ("grass_chain", ("grass",) * 5, False, False),
        ("shrub_chain", ("shrub",) * 5, False, False),
        ("floor_chain", ("forest_floor",) * 5, False, False),
        ("tree_chain", ("tree_equivalent",) * 5, False, False),
        ("grass_to_building_at_2m_height", ("grass", "building"), False, False),
        ("grass_to_exposed_building_base", ("grass", "building"), True, False),
        ("shrub_to_exposed_building_base", ("shrub", "building"), True, False),
        ("tree_to_building", ("tree_equivalent", "building"), False, False),
        ("road_gap_grass", ("grass", "bare_or_road", "grass"), False, False),
        ("very_wet_grass_control", ("grass",) * 5, False, True),
    )
    study = {
        "evidence": "synthetic_mechanism_ablation_not_field_validation",
        "contact": contact.to_mapping(),
        "profiles": "unchanged profiles.synthetic.yml except explicitly named controls",
        "scenarios": [
            {"id": sid, "labels": labels, "low_receiver": low, "wet": wet}
            for sid, labels, low, wet in specs
        ],
        "max_dt_s": [0.5, 0.25],
        "end_time_s": 1200,
        "cell_size_m": 2,
        "radiation": "same graph in both mechanism variants",
        "limitations": [
            "single-layer equivalents",
            "static still-air effective flame contact",
            "no embers or crown transition",
            "not calibrated spread rate",
        ],
    }
    (output / "study.resolved.json").write_text(json.dumps(study, indent=2) + "\n")
    rows, timings = [], []
    for sid, labels, low, wet in specs:
        request = make_request(labels, low_building_receiver=low, wet=wet)
        for enabled in (False, True):
            for step in (0.5, 0.25):
                variant = replace(
                    request,
                    flame_contact=contact if enabled else None,
                    scenario=replace(request.scenario, max_dt_s=step),
                )
                started = perf_counter()
                result = run_v3_simulation(variant, retain_snapshots=False)
                timings.append(
                    {
                        "scenario": sid,
                        "contact": enabled,
                        "dt": step,
                        "wall_s": perf_counter() - started,
                    }
                )
                final = result.final_state
                rows.append(
                    {
                        "scenario": sid,
                        "contact": enabled,
                        "dt_s": step,
                        "ignited_cells": int(np.count_nonzero(np.isfinite(final.ignition_time_s))),
                        "ignition_times_s": [
                            float(t) if isfinite(t) else None for t in final.ignition_time_s.ravel()
                        ],
                        "final_temperature_K": final.surface_temperature_K.ravel().tolist(),
                        "convection_received_J": final.received_convection_J.ravel().tolist(),
                        "energy_budget": asdict(result.energy_budget),
                        "consumed_fuel_kg": result.consumed_fuel_kg,
                    }
                )
                if enabled and step == 0.5 and sid == "grass_chain":
                    write_v3_case_bundle(variant, output / "grass-contact-case")
    audit = peak_radiation_audit()
    records = {f"{r['scenario']}-{r['contact']}-{r['dt_s']}": r for r in rows}
    changes = []
    for sid, _, _, _ in specs:
        for enabled in (False, True):
            coarse = records[f"{sid}-{enabled}-0.5"]
            fine = records[f"{sid}-{enabled}-0.25"]
            pairs = zip(coarse["ignition_times_s"], fine["ignition_times_s"], strict=True)
            changes.extend(abs(a - b) for a, b in pairs if a is not None and b is not None)
    summary = {
        "runs": len(rows),
        "energy_partitions_close": all(
            abs(
                r["energy_budget"]["non_radiative_released_J"]
                - r["energy_budget"]["convective_transferred_J"]
                - r["energy_budget"]["nonradiative_untransferred_J"]
            )
            <= max(1e-6, r["energy_budget"]["released_J"] * 1e-10)
            for r in rows
        ),
        "dt_ignition_count_consistent": all(
            records[f"{sid}-{enabled}-0.5"]["ignited_cells"]
            == records[f"{sid}-{enabled}-0.25"]["ignited_cells"]
            for sid, _, _, _ in specs
            for enabled in (False, True)
        ),
        "max_ignition_time_change_s": max(changes),
        "field_validation": False,
    }
    for name, value in (
        ("results.json", rows),
        ("radiation_peak_audit.json", audit),
        ("timings.json", timings),
        ("validation.json", summary),
    ):
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    root = DIRECTORY.parents[2]
    paths = [
        Path(__file__),
        DIRECTORY / "profiles.synthetic.yml",
        DIRECTORY / "flame-contact.synthetic.yml",
        *(root / "src/pyfireca/village").glob("*.py"),
    ]
    hashes = {}
    for path in paths:
        relative = path.relative_to(root)
        data = path.read_bytes()
        snapshot = output / "source_snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    (output / "source_sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_audit(args.output), indent=2))
