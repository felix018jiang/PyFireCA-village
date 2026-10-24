"""Separate an annotation trigger on a shared source cell from receiver ignition."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.village.domain import VillageGridSpec


@pytest.fixture
def bridge(monkeypatch):
    folder = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(
        "bridge_mesh", folder / "run_batuan_bridge_mesh.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_source_trigger_is_not_owned_receiver_ignition(bridge):
    q = SimpleNamespace(grid=VillageGridSpec(1, 2, 5, 0, 5), building_index=np.array([[60, 119]]))
    state = SimpleNamespace(
        ignition_time_s=np.array([[0, np.inf]]),
        absorbed_radiation_J=np.array([[0, 200]]),
        received_convection_J=np.array([[0, 10]]),
    )
    observed = bridge.first_trigger_cells(q, state, np.array([0, 1]), np.array([0.627, 20]))
    owned = bridge.first_trigger_cells(q, state, np.array([1]), np.array([20]))
    assert observed["time_s"] == 0
    assert observed["cells"][0]["fuel_owner"] == 60
    assert owned == {"time_s": None, "cells": []}
    state.ignition_time_s[0, 1] = 300
    assert bridge.first_trigger_cells(q, state, np.array([1]), np.array([20]))["time_s"] == 300


def test_trigger_cell_evidence_retains_all_first_ties_and_metric_positions(bridge):
    q = SimpleNamespace(
        grid=VillageGridSpec(1, 2, 2.5, 100, 200), building_index=np.array([[119, 119]])
    )
    state = SimpleNamespace(
        ignition_time_s=np.array([[10.0, 10.0]]),
        absorbed_radiation_J=np.array([[50, 60]]),
        received_convection_J=np.array([[0, 3]]),
    )
    trigger = bridge.first_trigger_cells(q, state, np.array([0, 1]), np.array([6.25, 0.03]))
    assert len(trigger["cells"]) == 2
    assert trigger["cells"][1]["point_m"] == [103.75, 198.75]
    assert trigger["cells"][1]["receiver_intersection_m2"] == 0.03
    assert trigger["cells"][1]["end_cumulative_absorbed_radiation_J"] == 60


def test_prespecified_matrix_does_not_duplicate_completed_house_pairs(bridge):
    tasks = bridge.controls()
    assert len(tasks) == len({t["key"] for t in tasks}) == 24
    assert {t["source_owner"] for t in tasks} == {60, 119}
    assert sum(t["cleared"] for t in tasks) == 6
    assert {t["ground_mode"] for t in tasks} == {"none", "mapped"}
    for spacing in (5, 2.5, 1):
        for source in (60, 119):
            primary = [
                t
                for t in tasks
                if t["spacing_m"] == spacing
                and t["source_owner"] == source
                and t["dt_s"] == 1
                and t["channel"] == "both"
            ]
            assert len(primary) == 3


def test_auditor_accepts_solver_nan_sentinel_and_rejects_invalid_actual_times(bridge):
    import audit_batuan_bridge_mesh as audit

    good = np.array([[np.nan, 0.0, 336.0]])
    audit.validate_ignition_times(good, (1, 3))
    assert audit.first_time(np.array([np.nan])) is None
    for invalid in [np.inf, -1.0, 5401.0]:
        with pytest.raises(AssertionError):
            audit.validate_ignition_times(np.array([[np.nan, 0.0, invalid]]), (1, 3))
    with pytest.raises(AssertionError):
        audit.validate_ignition_times(good, (3, 1))
