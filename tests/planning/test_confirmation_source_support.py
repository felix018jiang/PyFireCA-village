"""Geometry-only validation must reject lost area and misassigned physical source nodes."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box


@pytest.fixture
def verifier(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "confirmation_source_support", scripts / "verify_heritage_confirmation_source_support.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_native_source_must_keep_full_owned_site_and_budget(verifier):
    site = box(0, 0, 5, 5)
    budget = [25, 450, 90_000]
    masks = {name: np.array([True]) for name in ("domain_mask", "known_mask", "burnable_mask")}
    params = SimpleNamespace(
        **{name: np.array([value]) for name, value in zip(verifier.FIELDS, budget, strict=True)}
    )
    layout = SimpleNamespace(
        geometry=[site],
        owners=[("roof",)],
        ignition_owner=["roof"],
        node_inputs=SimpleNamespace(parameters=params, **masks),
    )
    result = verifier.check_support(layout, "roof", site, set(), budget)
    assert result["native_unique_retained_known_burnable_support"]
    with pytest.raises(ValueError, match="retained roof"):
        verifier.check_support(layout, "roof", site, {"roof"}, budget)
    layout.owners = [("roof", "another")]
    with pytest.raises(ValueError, match="uniquely owned"):
        verifier.check_support(layout, "roof", site, set(), budget)
    layout.owners = [("roof",)]
    layout.node_inputs.known_mask[0] = False
    with pytest.raises(ValueError, match="known"):
        verifier.check_support(layout, "roof", site, set(), budget)
    layout.node_inputs.known_mask[0] = True
    with pytest.raises(ValueError, match="physical footprint"):
        verifier.check_support(layout, "roof", box(0, 0, 5, 4), set(), budget)
    with pytest.raises(ValueError, match="base integration"):
        verifier.check_support(layout, "roof", site, set(), [25, 449, 90_000])
