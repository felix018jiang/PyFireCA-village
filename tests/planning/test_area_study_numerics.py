"""Numerical diagnostic controls preserve fixed source geometry and budgets."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol
from pyfireca.planning.roof_ignition import refine_roof_layout

ROOT = Path(__file__).resolve().parents[2]


def oracle(**kwargs):
    from shapely.geometry import box

    spec = importlib.util.spec_from_file_location(
        "area_diagnostic_fixture", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, transport, inventory, _ = fixture.make_case(
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 9, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
            "U": box(16, 12, 18, 14),
        }
    )
    protocol = freeze_source_protocol(
        inventory, seed=20261110, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    protocol["draws"] = [protocol["draws"][0]]
    protocol.pop("protocol_sha256")
    protocol["protocol_sha256"] = content_hash(protocol)
    return RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=("M",),
        refinement_ids=("M", "S"),
        fires=[{"fire_id": "source", "protocol": protocol}],
        code_version="numeric-controls-test",
        **kwargs,
    )


def test_legacy_numerical_rules_require_explicit_diagnostic_opt_in():
    with pytest.raises(ValueError, match="frozen dual-scale"):
        oracle(scales_m=(0.25,))
    with pytest.raises(ValueError, match="frozen dual-scale"):
        oracle(phase_fraction=(0.5, 0.5))
    with pytest.raises(ValueError, match="invalid explicitly"):
        oracle(experimental_numerics=True, scales_m=(0.0,))


def test_per_building_refinement_preserves_source_area_energy_and_other_roofs():
    coarse = oracle(experimental_numerics=True, scales_m=(0.5,))
    fine = oracle(experimental_numerics=True, scales_m=(0.5,), cell_size_by_id={"S": 0.25})
    assert coarse.problem_sha256 != fine.problem_sha256
    layouts = []
    for instance in (coarse, fine):
        fire = instance.fires["source"]
        layouts.append(
            refine_roof_layout(
                instance.base,
                instance.inventory,
                cell_size_m=0.5,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                cell_size_by_id=instance.cell_size_by_id,
            )
        )
    left, right = layouts
    assert len(right.nodes("S")) > len(left.nodes("S"))
    assert len(right.nodes("M")) == len(left.nodes("M"))
    for layout in layouts:
        source = np.array([i == "S" for i in layout.ignition_owner])
        assert layout.node_inputs.parameters.covered_area_m2.ravel()[source].sum() == pytest.approx(
            25
        )
        for identifier in ("S", "M", "G", "F"):
            assert sum(layout.geometry[i].area for i in layout.nodes(identifier)) == pytest.approx(
                coarse.inventory.by_id[identifier].geometry.area
            )
    for name in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J"):
        assert getattr(left.node_inputs.parameters, name).sum() == pytest.approx(
            getattr(right.node_inputs.parameters, name).sum()
        )


def test_trace_uses_actual_burning_enum(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "area_study", ROOT / "scripts/run_heritage_area_study.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    layout = SimpleNamespace(
        nodes=lambda _: np.array([0, 1]),
        node_inputs=SimpleNamespace(parameters=SimpleNamespace(covered_area_m2=np.array([[4, 7]]))),
    )
    trace = runner.CompactTrace(layout, ["S"])
    trace.append(
        SimpleNamespace(
            time_s=12,
            temperature_valid_mask=np.array([[True, False]]),
            state=np.array([[2, 3]]),
            surface_temperature_K=np.array([[400, 0]]),
            interval_mean_hrr_W=np.array([[0, 50]]),
            fuel_remaining_kg=np.array([[4, 3]]),
        )
    )
    assert trace.rows[0]["burning_area_m2"] == 7
    assert trace.rows[0]["maximum_valid_temperature_K"] == 400
