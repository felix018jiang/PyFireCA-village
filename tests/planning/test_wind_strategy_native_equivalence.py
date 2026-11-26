"""Compare compact wind-strategy runs with independently prepared V3 joint-intervention runs."""

import importlib.util
import json
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np
import pytest

from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.environments import apply_static_wind
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    footprint_ignitions,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import roof_area_integrated_radiation_graph
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def windy_joint_case(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import run_heritage_wind_strategies as runner

    spec = importlib.util.spec_from_file_location(
        "native_joint_fixture", Path(__file__).with_name("test_roof_joint.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle, _, inventory, _, grass = module.joint_case.__wrapped__()
    contact = FlameContactParameters(1100, 25, 0.1, 5, "synthetic", "native_wind_equivalence")
    bundle = replace(bundle, request=replace(bundle.request, flame_contact=contact))
    bundle, transport = apply_static_wind(
        bundle, wind_speed_m_s=2, wind_from_deg=240, flame_tilt_coefficient_s_m=0.2
    )
    return runner, bundle, transport, inventory, grass


def source(inventory, identifier):
    roof = inventory.by_id[identifier].geometry
    footprint = RoofIgnitionFootprint(identifier, roof, "synthetic whole 25 m² roof")
    assert footprint.geometry.area == pytest.approx(25.0)
    return {
        **footprint.to_mapping(),
        "anchor_m": list(roof.centroid.coords[0]),
        "footprint_sha256": footprint.sha256,
    }


def test_retained_node_states_match_native_roof_and_ground_intervention(
    windy_joint_case, record_property
):
    runner, bundle, transport, inventory, (_, sparse_grass) = windy_joint_case
    horizon = 350.0
    cores = [t.building_id for t in inventory.targets]
    domain, layout, integrated = runner.prepare_domain(
        bundle, inventory, source(inventory, "S"), cores, 100.0
    )
    plan = RoofJointPlan(
        "remove", ("M",), ground_evidence="synthetic", cleared_physical_cell_ids=(sparse_grass,)
    )
    # Native path rebuilds the graph, contact and extensive arrays *after* clearance.
    selected = np.array([bool(set(integrated).intersection(ids)) for ids in layout.owners])
    native = RoofJointEvaluationEngine(
        domain.bundle,
        transport,
        domain.inventory,
        source_policy="declared_fixed_sources",
        prepared_layout=layout,
        contact_builder=refined_contact_operator,
        radiation_builder=partial(
            roof_area_integrated_radiation_graph,
            integrated_ids=integrated,
            quadrature_spacing_m=0.5,
            frozen_integrated_node_mask=selected,
        ),
    )
    scenario = EvaluationScenario("same-source", footprint_ignitions(layout), horizon)
    native_layout, native_contact, _ = native.prepare(scenario, plan)
    reference = _run_resolved_v3_simulation(
        native_layout.node_request, contact=native_contact, retain_snapshots=False
    )
    # Compact path drops cleared nodes and filters precomputed radiation and contact edges.
    request, retained, indices, _ = runner.retained_inputs(
        domain, layout, plan.to_mapping(), horizon
    )
    graph = runner.WindRadiationGeometry(layout, integrated, 0.5).graph(transport)
    request, compact_contact = runner.resolve_operators(
        retained, indices, request, graph, refined_contact_operator(layout)
    )
    actual = _run_resolved_v3_simulation(request, contact=compact_contact, retain_snapshots=False)
    assert len(retained) < len(layout.owners)
    assert sparse_grass not in layout.cell_id[retained]
    assert set(layout.nodes("M")).isdisjoint(retained)
    assert actual.final_state.absorbed_radiation_J.sum() > 0
    assert actual.final_state.received_convection_J.sum() > 0
    differences = {}
    for key in reference.final_state.__dataclass_fields__:
        expected = getattr(reference.final_state, key)
        value = getattr(actual.final_state, key)
        if isinstance(expected, np.ndarray):
            np.testing.assert_allclose(
                value, expected[:, retained], rtol=1e-12, atol=1e-7, equal_nan=True, err_msg=key
            )
            target = expected[:, retained]
            finite = np.isfinite(target)
            differences[key] = float(
                np.max(
                    np.abs(value[finite].astype(float) - target[finite].astype(float)), initial=0
                )
            )
        else:
            assert value == pytest.approx(expected, rel=1e-12)
    np.testing.assert_array_equal(actual.ignition_cause, reference.ignition_cause[:, retained])
    record_property(
        "physics_evidence",
        json.dumps(
            {
                "retained_nodes": len(retained),
                "original_nodes": len(layout.owners),
                "horizon_s": horizon,
                "wind_speed_m_s": transport.wind_speed_m_s,
                "wind_from_deg": transport.wind_from_deg,
                "integrated_spacing_m": 0.5,
                "refined_roof_spacing_m": 1,
                "parent_spacing_m": domain.bundle.request.grid.cell_size_m,
                "total_absorbed_radiation_J": float(actual.final_state.absorbed_radiation_J.sum()),
                "total_received_convection_J": float(
                    actual.final_state.received_convection_J.sum()
                ),
                "max_abs_diff_on_finite_entries": differences,
                "ignition_causes_identical": True,
                "state_comparison_rtol": 1e-12,
                "state_comparison_atol": 1e-7,
            }
        ),
    )


def test_closed_cropped_run_reports_zero_exposure_for_outside_cores(
    windy_joint_case, record_property
):
    runner, bundle, transport, inventory, _ = windy_joint_case
    cores = [t.building_id for t in inventory.targets]
    fire = source(inventory, "U")
    transport = replace(transport, exposure_radius_m=3.0)
    plan = RoofJointPlan("baseline").to_mapping()
    metrics = []
    for radius_m in (12, 100):
        domain, layout, integrated = runner.prepare_domain(bundle, inventory, fire, cores, radius_m)
        if radius_m == 12:
            assert set(cores).isdisjoint(domain.inventory.by_id)
        else:
            assert set(cores) <= set(domain.inventory.by_id)
        graph = runner.WindRadiationGeometry(layout, integrated, 0.5).graph(transport)
        result = runner.run_case(
            domain,
            layout,
            plan,
            graph,
            refined_contact_operator(layout),
            cores,
            transport.exposure_radius_m,
            120.0,
        )
        assert result["boundary"]["closed"]
        metrics.append(result)
    local, full = metrics
    assert local["boundary"]["artificial_boundary_min_gap_m"] is not None
    assert full["boundary"]["artificial_boundary_min_gap_m"] is None
    assert (
        local["core_outcomes"]
        == full["core_outcomes"]
        == {
            key: {"first_ignition_s": None, "peak_rise_K": 0.0, "absorbed_radiation_J": 0.0}
            for key in cores
        }
    )
    for key in (
        "roof_ignited_m2",
        "ground_ignited_m2",
        "roof_burned_out_m2",
        "ground_burned_out_m2",
        "active_m2",
        "source_energy_J",
    ):
        assert local[key] == pytest.approx(full[key], rel=1e-12)
    record_property(
        "physics_evidence",
        json.dumps(
            {
                "radius_m": transport.exposure_radius_m,
                "horizon_s": 120,
                "local_window_radius_m": 12,
                "outside_core_count": len(cores),
                "outside_core_outcomes": local["core_outcomes"],
                "local_boundary": local["boundary"],
                "full_domain_boundary": full["boundary"],
                "local_roof_ignited_m2": local["roof_ignited_m2"],
                "full_domain_roof_ignited_m2": full["roof_ignited_m2"],
            }
        ),
    )
