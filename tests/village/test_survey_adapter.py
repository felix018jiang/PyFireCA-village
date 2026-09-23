from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pyfireca.village import (
    V3Scenario,
    V3SimulationRequest,
    VillageGridSpec,
    VillageIgnitionEvent,
    adapt_resolved_survey_to_v03,
    load_assembly_preset_library,
    load_material_card_library,
    load_moisture_initialization,
    resolve_material_selections,
    run_v3_simulation,
)
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.survey import parse_field_survey
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)

ROOT = Path(__file__).parents[2]
CARDS = ROOT / "examples/village/material_cards/catalog.yml"
ASSEMBLIES = ROOT / "examples/village/material_cards/assemblies.synthetic.yml"
MOISTURE = ROOT / "examples/village/material_cards/moisture.initial.synthetic.yml"


def _resolved_case():
    survey = parse_field_survey(
        {
            "schema_version": "village-field-survey-1",
            "survey_id": "m03-small-domain",
            "records": [
                {
                    "observation_id": "OBS-WOOD",
                    "object_id": "B001",
                    "component": "facade",
                    "geometry_ref": "geom-wood",
                    "material_class": "wood_unspecified",
                    "quantity_band": "standard",
                },
                {
                    "observation_id": "OBS-BRICK",
                    "object_id": "B002",
                    "component": "interior_wall",
                    "geometry_ref": "geom-brick",
                    "material_class": "masonry_brick",
                    "quantity_band": "standard",
                },
            ],
        }
    )
    cards = load_material_card_library(CARDS)
    assemblies = load_assembly_preset_library(ASSEMBLIES)
    moisture = load_moisture_initialization(MOISTURE)
    resolved = resolve_material_selections(
        survey,
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"geom-wood": 1.5, "geom-brick": 1.0},
        case_id="m03-small-domain",
    )
    assert resolved.can_run
    return resolved


def _spatial_inputs():
    grid = VillageGridSpec(
        height=2,
        width=2,
        cell_size_m=1.0,
        x_min_m=0.0,
        y_max_m=2.0,
        coordinate_mode="projected",
        crs="EPSG:32649",
    )
    refs = np.asarray(
        [["geom-wood", "geom-wood"], ["geom-brick", ""]],
        dtype="U16",
    )
    coverage = np.asarray([[1.0, 0.5], [1.0, 0.0]], dtype=np.float64)
    blockers = np.ones((2, 2), dtype=np.float64)
    return grid, refs, coverage, blockers


def test_m03_adapts_mass_once_and_exposes_v04_inputs() -> None:
    resolved = _resolved_case()
    grid, refs, coverage, blockers = _spatial_inputs()
    first = adapt_resolved_survey_to_v03(
        grid,
        resolved,
        geometry_ref_by_cell=refs,
        coverage_fraction=coverage,
        blocker_transmissivity=blockers,
    )
    second = adapt_resolved_survey_to_v03(
        grid,
        resolved,
        geometry_ref_by_cell=refs,
        coverage_fraction=coverage,
        blocker_transmissivity=blockers,
    )

    arrays = first.arrays
    assert first.adapter_sha256 == second.adapter_sha256
    assert np.sum(arrays.covered_area_m2) == pytest.approx(2.5)
    assert np.sum(arrays.initial_dry_fuel_mass_kg) == pytest.approx(22.5)
    assert np.sum(arrays.live_dry_fuel_mass_kg) == pytest.approx(0.0)
    assert np.sum(arrays.dead_dry_fuel_mass_kg) == pytest.approx(22.5)
    assert np.sum(arrays.available_energy_J) == pytest.approx(
        np.sum(arrays.configured_curve_energy_J)
    )
    assert np.array_equal(first.transport_source_mask, np.asarray([[True, True], [False, False]]))
    assert np.array_equal(first.transport_target_mask, np.asarray([[True, True], [True, False]]))
    assert first.profile_id[1, 0] == "masonry_brick"
    assert first.evidence_index[0, 0] == 0

    parameters = V3RadiativeTransportParameters(
        exposure_radius_m=1.5,
        near_field_regularization_m=0.5,
        atmospheric_extinction_m_inv=0.0,
        wind_speed_m_s=0.0,
        wind_from_deg=0.0,
        flame_tilt_coefficient_s_m=0.0,
    )
    edges = build_v3_radiative_transport_edges(
        grid,
        first.transport_source_mask,
        first.transport_target_mask,
        arrays.receiver_area_m2,
        arrays.source_height_m,
        arrays.receiver_height_m,
        first.blocker_transmissivity,
        parameters,
        profile_id=first.profile_id,
    )
    assert edges.source.size > 0
    assert np.all(np.isfinite(edges.weight))


def test_m03_requires_area_closure_and_resolved_coverage() -> None:
    resolved = _resolved_case()
    grid, refs, coverage, blockers = _spatial_inputs()
    with pytest.raises(ValueError, match="coverage area closure"):
        adapt_resolved_survey_to_v03(
            grid,
            resolved,
            geometry_ref_by_cell=refs,
            coverage_fraction=coverage * 0.9,
            blocker_transmissivity=blockers,
        )

    missing_refs = refs.copy()
    missing_refs[1, 0] = "unresolved"
    with pytest.raises(ValueError, match="no covered cells"):
        adapt_resolved_survey_to_v03(
            grid,
            resolved,
            geometry_ref_by_cell=missing_refs,
            coverage_fraction=coverage,
            blocker_transmissivity=blockers,
        )


def test_m03_rejects_nonzero_coverage_without_a_matching_record() -> None:
    resolved = _resolved_case()
    grid, refs, coverage, blockers = _spatial_inputs()
    refs[1, 1] = "unknown"
    coverage[1, 1] = 0.5
    with pytest.raises(ValueError, match="no active resolved observation"):
        adapt_resolved_survey_to_v03(
            grid,
            resolved,
            geometry_ref_by_cell=refs,
            coverage_fraction=coverage,
            blocker_transmissivity=blockers,
        )


def test_ot08_m03_adapter_and_equivalent_v03_arrays_use_the_same_solver() -> None:
    resolved = _resolved_case()
    grid, refs, coverage, blockers = _spatial_inputs()
    adapted = adapt_resolved_survey_to_v03(
        grid,
        resolved,
        geometry_ref_by_cell=refs,
        coverage_fraction=coverage,
        blocker_transmissivity=blockers,
    )
    transport = V3RadiativeTransportParameters(
        exposure_radius_m=1.5,
        near_field_regularization_m=0.5,
        atmospheric_extinction_m_inv=0.0,
        wind_speed_m_s=0.0,
        wind_from_deg=0.0,
        flame_tilt_coefficient_s_m=0.0,
    )
    edges = build_v3_radiative_transport_edges(
        grid,
        adapted.transport_source_mask,
        adapted.transport_target_mask,
        adapted.arrays.receiver_area_m2,
        adapted.arrays.source_height_m,
        adapted.arrays.receiver_height_m,
        adapted.blocker_transmissivity,
        transport,
        profile_id=adapted.profile_id,
    )
    known = coverage > 0.0
    indexes = np.where(known, adapted.landcover_index, -1).astype(np.int32)
    scenario = V3Scenario(
        max_dt_s=0.25,
        end_time_s=2.0,
        ambient_temperature_K=300.0,
        heating_temperature_delta_K=0.001,
        ignitions=(VillageIgnitionEvent(0.0, 0, 0),),
    )

    def request(arrays: V3CellPhysicsArrays) -> V3SimulationRequest:
        return V3SimulationRequest(
            grid=grid,
            domain_mask=known,
            known_mask=known,
            burnable_mask=adapted.transport_source_mask,
            source_mask=adapted.transport_source_mask,
            target_mask=adapted.transport_target_mask,
            piloted_ignition_mask=adapted.transport_source_mask,
            material_index=indexes,
            landcover_index=indexes,
            building_index=np.asarray([[1, 1], [2, -1]], dtype=np.int32),
            parameters=arrays,
            exposure_edges=edges,
            scenario=scenario,
            sample_interval_s=0.5,
        )

    copied = V3CellPhysicsArrays(
        **{
            name: getattr(adapted.arrays, name).copy()
            for name in adapted.arrays.__dataclass_fields__
        }
    )
    from_cards = run_v3_simulation(request(adapted.arrays))
    from_explicit_arrays = run_v3_simulation(request(copied))

    assert np.array_equal(from_cards.final_state.state, from_explicit_arrays.final_state.state)
    assert np.allclose(
        from_cards.final_state.fuel_remaining_kg,
        from_explicit_arrays.final_state.fuel_remaining_kg,
    )
    assert np.allclose(
        from_cards.final_state.released_energy_J,
        from_explicit_arrays.final_state.released_energy_J,
    )
