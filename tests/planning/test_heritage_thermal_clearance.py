"""The protection MIP must price shared roof atoms and match small exhaustive searches."""

import itertools
from pathlib import Path

import numpy as np
import pytest


def test_energy_and_contact_constraints_match_all_possible_actions(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from optimize_heritage_thermal_clearance import optimize_groups

    costs = {("A",): 8, ("B",): 4, ("A", "B"): 17, ("G1",): 25, ("G2",): 25}
    energy = np.array([[1, 1, 15, 4, 2], [2, 7, 20, 4, 0]], dtype=float)
    capacity = np.array([6, 10], dtype=float)
    contact = {("G2",)}
    selected, result = optimize_groups(costs, energy, contact, capacity, 10)
    alternatives = []
    for bits in itertools.product([False, True], repeat=4):
        chosen = {a for a, flag in zip(["A", "B", "G1", "G2"], bits, strict=True) if flag}
        clear = np.array([set(group) <= chosen for group in costs])
        if chosen >= {"G2"} and np.all(energy[:, ~clear].sum(axis=1) <= capacity):
            alternatives.append(
                sum(cost for cost, f in zip(costs.values(), clear, strict=True) if f)
            )
    assert selected == {"A", "B", "G2"}
    assert result["construction_area_m2"] == min(alternatives) == 54
    assert result["dual_area_bound_m2"] == pytest.approx(min(alternatives))


def test_total_area_prices_shared_roofs_and_keeps_locked_fuel(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from optimize_heritage_protection_and_loss import solve_remaining

    required, retained = {"B0"}, {"B3"}
    costs = {("B0",): 1, ("B1",): 2, ("B0", "B1"): 3, ("B2",): 8, ("B3",): 3, ("G1",): 25}
    row = dict(zip(costs, [10, 0.2, 0.6, 0.4, 0.2, 0.05], strict=True))
    selected, result = solve_remaining([row], set(costs), required, retained, costs, 0.99)
    alternatives = []
    for bits in itertools.product([False, True], repeat=3):
        removed = required | {i for i, flag in zip(["B1", "B2", "G1"], bits, strict=True) if flag}
        if sum(amount for g, amount in row.items() if not set(g) <= removed) <= 0.99:
            alternatives.append(sum(cost for g, cost in costs.items() if set(g) <= removed))
    assert selected == {"B0", "B1"}
    assert result["C_m2"] == min(alternatives) == 6
    assert result["cost_lower_bound_m2"] == pytest.approx(6)
    assert result["max_core_absorbed_energy_fraction"] == pytest.approx(0.65)
