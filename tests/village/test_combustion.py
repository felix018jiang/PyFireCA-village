from __future__ import annotations

import numpy as np
import pytest

from pyfireca.village.combustion import (
    burning_mass_loss_and_hrr,
    maximum_fuel_limited_step_s,
)


def test_combustion_clips_the_final_mass_loss_interval() -> None:
    burning = np.array([True])
    remaining = np.array([0.2])
    rate = np.array([0.1])
    loss, hrr = burning_mass_loss_and_hrr(burning, remaining, rate, np.array([1_000_000.0]), 3.0)
    assert loss[0] == pytest.approx(0.2)
    assert hrr[0] == pytest.approx(0.2 / 3.0 * 1_000_000.0)
    assert maximum_fuel_limited_step_s(burning, remaining, rate) == pytest.approx(2.0)
