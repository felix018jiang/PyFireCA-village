from __future__ import annotations

import numpy as np
import pytest

from pyfireca.village.domain import (
    CellParameterArrays,
    ExposureEdges,
    PreparedVillageDomain,
    VillageGridSpec,
)
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver import (
    VillageScenario,
    VillageSimulationRequest,
    run_village_simulation,
)
from pyfireca.village.state import VillageCellState


def _request(
    *,
    shape: tuple[int, int] = (1, 1),
    weights: tuple[list[int], list[int], list[float]] = ([], [], []),
    max_dt: float = 3.0,
    end: float = 20.0,
    events: tuple[VillageIgnitionEvent, ...] = (VillageIgnitionEvent(0.0, 0, 0),),
    fuel_load: float = 2.0,
    mass_loss: float = 0.1,
    heat: float = 1_000_000.0,
    emission: float = 0.2,
    ignition_dose: float = 5000.0,
) -> VillageSimulationRequest:
    ones = np.ones(shape)
    mask = np.ones(shape, dtype=np.bool_)
    domain = PreparedVillageDomain(
        grid=VillageGridSpec(*shape, 1.0, 0.0, float(shape[0])),
        material_index=np.zeros(shape, dtype=np.int32),
        domain_mask=mask,
        known_mask=mask,
        burnable_mask=mask,
        parameters=CellParameterArrays(
            fuel_load_kg_m2=fuel_load * ones,
            heat_of_combustion_J_kg=heat * ones,
            mass_loss_flux_kg_m2_s=mass_loss * ones,
            active_area_ratio=ones,
            emission_fraction=emission * ones,
            absorptivity=ones,
            ignition_dose_J_m2=ignition_dose * ones,
            exposure_decay_s=10 * ones,
        ),
    )
    source, target, weight = weights
    edges = ExposureEdges(
        source=np.asarray(source, dtype=np.int32),
        target=np.asarray(target, dtype=np.int32),
        weight=np.asarray(weight, dtype=np.float64),
        cell_count=shape[0] * shape[1],
    )
    return VillageSimulationRequest(
        domain=domain,
        scenario=VillageScenario(max_dt, end, 0.1, events),
        exposure_edges=edges,
    )


def test_single_cell_burns_out_at_exact_fuel_limited_time() -> None:
    result = run_village_simulation(_request())
    state = result.final_state
    assert state.burnout_time_s[0, 0] == pytest.approx(20.0)
    assert state.fuel_remaining_kg[0, 0] == pytest.approx(0.0)
    assert result.consumed_fuel_kg == pytest.approx(2.0)
    assert state.state[0, 0] == VillageCellState.BURNED_OUT


def test_newly_ignited_cell_does_not_emit_in_same_step() -> None:
    request = _request(
        shape=(1, 3),
        weights=([0, 1], [1, 2], [1.0, 1.0]),
        max_dt=1.0,
        end=1.0,
        fuel_load=10.0,
        mass_loss=1.0,
        heat=1000.0,
        emission=1.0,
        ignition_dose=50.0,
    )
    result = run_village_simulation(request)
    assert result.final_state.state[0, 1] == VillageCellState.BURNING
    assert result.final_state.state[0, 2] == VillageCellState.UNBURNED
    assert result.final_state.exposure_J_m2[0, 2] == 0.0


def test_delayed_ignition_prevents_early_stop_and_end_event_is_applied() -> None:
    request = _request(
        max_dt=3.0,
        end=10.0,
        events=(VillageIgnitionEvent(10.0, 0, 0),),
    )
    result = run_village_simulation(request)
    assert result.final_state.ignition_time_s[0, 0] == pytest.approx(10.0)
    assert result.final_state.state[0, 0] == VillageCellState.BURNING
    assert result.final_state.fuel_remaining_kg[0, 0] == pytest.approx(2.0)
    assert result.ignition_cause[0, 0] == 1


def test_external_event_wins_cause_tie_with_exposure() -> None:
    request = _request(
        shape=(1, 2),
        weights=([0], [1], [1.0]),
        max_dt=1.0,
        end=1.0,
        events=(VillageIgnitionEvent(0.0, 0, 0), VillageIgnitionEvent(1.0, 0, 1)),
        fuel_load=10.0,
        mass_loss=1.0,
        heat=1000.0,
        emission=1.0,
        ignition_dose=50.0,
    )
    result = run_village_simulation(request)
    assert result.final_state.ignition_time_s[0, 1] == pytest.approx(1.0)
    assert result.ignition_cause[0, 1] == 1


def test_non_burnable_ignition_is_rejected() -> None:
    request = _request()
    inert = request.domain.burnable_mask.copy()
    inert[0, 0] = False
    with pytest.raises(ValueError, match="not known burnable"):
        VillageSimulationRequest(
            domain=PreparedVillageDomain(
                grid=request.domain.grid,
                material_index=request.domain.material_index,
                domain_mask=request.domain.domain_mask,
                known_mask=request.domain.known_mask,
                burnable_mask=inert,
                parameters=request.domain.parameters,
            ),
            scenario=request.scenario,
            exposure_edges=request.exposure_edges,
        )
