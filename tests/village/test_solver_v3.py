from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
import yaml

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import (
    IGNITION_CAUSE_DIRECT,
    IGNITION_CAUSE_NONE,
    IGNITION_CAUSE_THERMAL,
    V3Scenario,
    V3SimulationRequest,
    run_v3_simulation,
)
from pyfireca.village.state import VillageCellState
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "examples/village/vegetation_v3/v05-small-domain.synthetic.yml"
FIXTURE_SHA256 = "26f3452d692793ebc683519756b74c1d1c653860e6e1accdc0fc5a2442b35ed6"


def _fixture() -> dict:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def _physics(
    burnable: np.ndarray,
    target: np.ndarray,
    *,
    coverage: np.ndarray | None = None,
    ignition_temperature: np.ndarray | None = None,
    heat_loss_W_m2_K: float = 0.0,
) -> V3CellPhysicsArrays:
    config = _fixture()
    values = config["physics"]
    shape = burnable.shape
    coverage_array = np.where(target, 1.0, 0.0) if coverage is None else coverage
    area = coverage_array * config["grid"]["cell_size_m"] ** 2
    dry = np.where(burnable, area * values["initial_dry_fuel_mass_kg"], 0.0)
    pyro = np.where(burnable, dry, 0.0)
    active_area = np.where(burnable, area, 0.0)
    peak = active_area * values["peak_hrr_W"]
    heat = np.where(burnable, values["heat_of_combustion_J_kg_dry"], 0.0)
    available = pyro * heat
    ignition = (
        np.where(burnable, config["scenario"]["ignition_temperature_K"], 0.0)
        if ignition_temperature is None
        else ignition_temperature
    )
    return V3CellPhysicsArrays(
        coverage_fraction=coverage_array,
        covered_area_m2=area,
        initial_dry_fuel_mass_kg=dry,
        live_dry_fuel_mass_kg=np.zeros(shape),
        dead_dry_fuel_mass_kg=dry,
        pyrolysable_dry_fuel_mass_kg=pyro,
        live_water_mass_kg=np.zeros(shape),
        dead_water_mass_kg=np.zeros(shape),
        water_mass_kg=np.zeros(shape),
        moisture_content_dry_basis=np.zeros(shape),
        heat_of_combustion_J_kg_dry=heat,
        ignition_temperature_K=ignition,
        peak_hrrpua_W_m2_active=np.where(burnable, values["peak_hrr_W"], 0.0),
        peak_hrr_W=peak,
        radiative_fraction=np.where(burnable, values["radiative_fraction"], 0.0),
        absorptivity=np.where(burnable, values["absorptivity"], 0.0),
        growth_time_s=np.where(burnable, values["growth_time_s"], 0.0),
        steady_time_s=np.where(burnable, values["steady_time_s"], 0.0),
        decay_time_s=np.where(burnable, values["decay_time_s"], 0.0),
        effective_heated_dry_mass_kg=np.where(
            burnable, area * values["effective_heated_dry_mass_kg"], 0.0
        ),
        specific_heat_J_kg_K=np.where(burnable, values["specific_heat_J_kg_K"], 0.0),
        emissivity=np.zeros(shape),
        ambient_heat_transfer_coefficient_W_m2_K=np.where(burnable, heat_loss_W_m2_K, 0.0),
        backing_conductance_W_m2_K=np.zeros(shape),
        receiver_area_ratio=np.where(target, 1.0, 0.0),
        receiver_area_m2=np.where(target, area, 0.0),
        active_surface_area_ratio=np.where(burnable, 1.0, 0.0),
        active_surface_area_m2=active_area,
        pyrolysable_dry_mass_fraction=np.where(burnable, 1.0, 0.0),
        source_height_m=np.where(burnable, values["source_height_m"], 0.0),
        receiver_height_m=np.where(target, values["receiver_height_m"], 0.0),
        available_energy_J=available,
        configured_curve_energy_J=available,
    )


def _request(
    labels: tuple[str, ...],
    *,
    edge_weight: float | None = None,
    max_dt_s: float | None = None,
    end_time_s: float | None = None,
    events: tuple[VillageIgnitionEvent, ...] | None = None,
    sample_interval_s: float | None = 0.5,
    reverse_edges: bool = False,
    heat_loss_W_m2_K: float = 0.0,
    ignition_temperature: np.ndarray | None = None,
    coverage: np.ndarray | None = None,
) -> V3SimulationRequest:
    config = _fixture()
    shape = (1, len(labels))
    grid = VillageGridSpec(1, len(labels), 1.0, 0.0, 1.0)
    known = np.ones(shape, dtype=np.bool_)
    domain = known.copy()
    burnable = np.asarray([[label != "concrete" for label in labels]], dtype=np.bool_)
    target = known.copy()
    source_ids: list[int] = []
    target_ids: list[int] = []
    weights: list[float] = []
    if edge_weight is not None:
        for col in range(len(labels) - 1):
            if burnable[0, col]:
                source_ids.append(col)
                target_ids.append(col + 1)
                weights.append(edge_weight)
    if reverse_edges:
        source_ids.reverse()
        target_ids.reverse()
        weights.reverse()
    edges = ExposureEdges(
        np.asarray(source_ids, dtype=np.int32),
        np.asarray(target_ids, dtype=np.int32),
        np.asarray(weights, dtype=np.float64),
        len(labels),
    )
    codes = config["material_codes"]
    material = np.asarray([[codes[label] for label in labels]], dtype=np.int32)
    building = np.asarray(
        [[col + 1 if label == "building_wood" else -1 for col, label in enumerate(labels)]],
        dtype=np.int32,
    )
    scenario_values = config["scenario"]
    scenario = V3Scenario(
        max_dt_s=max_dt_s or scenario_values["max_dt_s"],
        end_time_s=end_time_s or scenario_values["end_time_s"],
        ambient_temperature_K=scenario_values["ambient_temperature_K"],
        heating_temperature_delta_K=scenario_values["heating_temperature_delta_K"],
        ignitions=events or (VillageIgnitionEvent(0.0, 0, 0),),
    )
    return V3SimulationRequest(
        grid=grid,
        domain_mask=domain,
        known_mask=known,
        burnable_mask=burnable,
        source_mask=burnable.copy(),
        target_mask=target,
        piloted_ignition_mask=burnable.copy(),
        material_index=material,
        landcover_index=material.copy(),
        building_index=building,
        parameters=_physics(
            burnable,
            target,
            coverage=coverage,
            ignition_temperature=ignition_temperature,
            heat_loss_W_m2_K=heat_loss_W_m2_K,
        ),
        exposure_edges=edges,
        scenario=scenario,
        sample_interval_s=sample_interval_s,
    )


def test_v05_fixture_hash_and_energy_scale_are_frozen() -> None:
    assert sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256
    config = _fixture()
    physics = config["physics"]
    curve_energy = physics["peak_hrr_W"] * (
        physics["growth_time_s"] / 3 + physics["steady_time_s"] + physics["decay_time_s"] / 2
    )
    assert curve_energy == pytest.approx(
        physics["pyrolysable_dry_fuel_mass_kg"] * physics["heat_of_combustion_J_kg_dry"]
    )


def test_ot01_nonbuilding_grass_chain_heats_ignites_propagates_and_burns_out() -> None:
    request = _request(("grass", "grass", "grass"), edge_weight=0.08)
    result = run_v3_simulation(request)

    assert np.array_equal(
        result.final_state.state,
        np.full((1, 3), int(VillageCellState.BURNED_OUT), dtype=np.uint8),
    )
    assert result.ignition_cause[0, 0] == IGNITION_CAUSE_DIRECT
    assert np.all(result.ignition_cause[0, 1:] == IGNITION_CAUSE_THERMAL)
    assert 0.0 < result.final_state.ignition_time_s[0, 1] < result.final_state.ignition_time_s[0, 2]
    assert result.consumed_fuel_kg == pytest.approx(3.0)
    assert result.energy_budget.released_J == pytest.approx(3000.0)
    assert np.all(~result.final_state.temperature_valid_mask)


@pytest.mark.parametrize(
    ("source_label", "target_label"),
    [
        ("building_wood", "grass"),
        ("grass", "building_wood"),
        ("grass", "shrub"),
        ("shrub", "grass"),
        ("shrub", "tree_equivalent"),
        ("tree_equivalent", "shrub"),
        ("tree_equivalent", "tree_equivalent"),
        ("litter", "building_wood"),
    ],
)
def test_ot02_cross_material_pairs_share_strong_and_weak_solver_paths(
    source_label: str, target_label: str
) -> None:
    transport = _fixture()["transport"]
    strong = run_v3_simulation(
        _request((source_label, target_label), edge_weight=transport["strong_edge_weight"])
    )
    weak = run_v3_simulation(
        _request((source_label, target_label), edge_weight=transport["weak_edge_weight"])
    )

    assert strong.ignition_cause[0, 1] == IGNITION_CAUSE_THERMAL
    assert weak.ignition_cause[0, 1] == IGNITION_CAUSE_NONE
    assert weak.final_state.surface_temperature_K[0, 1] > 300.0
    assert weak.final_state.state[0, 1] == VillageCellState.HEATING


def test_ot03_object_membership_does_not_ignite_all_tree_cells() -> None:
    result = run_v3_simulation(
        _request(("tree_equivalent",) * 4, edge_weight=0.08, sample_interval_s=0.25)
    )

    initial = result.snapshots[0]
    assert np.count_nonzero(initial.state == VillageCellState.BURNING) == 1
    assert np.all(np.diff(result.final_state.ignition_time_s[0]) > 0.0)
    assert np.all(result.ignition_cause[0, 1:] == IGNITION_CAUSE_THERMAL)


def test_ot04_and_vt10_inert_road_receives_heat_and_radiation_crosses_it() -> None:
    request = _request(("grass", "concrete", "building_wood"), edge_weight=None)
    parameters = V3RadiativeTransportParameters(
        exposure_radius_m=_fixture()["transport"]["road_crossing_radius_m"],
        near_field_regularization_m=0.5,
        atmospheric_extinction_m_inv=0.0,
        wind_speed_m_s=0.0,
        wind_from_deg=0.0,
        flame_tilt_coefficient_s_m=0.0,
    )
    edges = build_v3_radiative_transport_edges(
        request.grid,
        request.source_mask,
        request.target_mask,
        request.parameters.receiver_area_m2,
        request.parameters.source_height_m,
        request.parameters.receiver_height_m,
        np.ones(request.grid.shape),
        parameters,
    )
    result = run_v3_simulation(replace(request, exposure_edges=edges))

    assert result.final_state.state[0, 1] == VillageCellState.INERT
    assert result.energy_budget.inert_received_J > 0.0
    assert result.ignition_cause[0, 2] == IGNITION_CAUSE_THERMAL
    assert (0, 2) in set(zip(edges.source, edges.target, strict=True))


def test_ot05_unknown_and_outside_are_distinct_and_excluded() -> None:
    base = _request(("grass", "grass", "grass"), edge_weight=None)
    domain = np.asarray([[True, True, False]])
    known = np.asarray([[True, False, False]])
    burnable = np.asarray([[True, False, False]])
    target = burnable.copy()
    material = np.asarray([[0, -1, -1]], dtype=np.int32)
    parameters = _physics(burnable, target)
    values = dict(
        domain_mask=domain,
        known_mask=known,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=target,
        piloted_ignition_mask=burnable,
        material_index=material,
        landcover_index=material,
        building_index=np.full((1, 3), -1, dtype=np.int32),
        parameters=parameters,
        exposure_edges=ExposureEdges(
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.int32),
            np.empty(0),
            3,
        ),
    )
    with pytest.raises(ValueError, match=r"UNKNOWN_CELLS.*first=\(0, 1\)"):
        replace(base, unknown_policy="error", **values)
    result = run_v3_simulation(replace(base, unknown_policy="exclude", **values))

    assert result.final_state.state[0, 1] == VillageCellState.UNKNOWN
    assert result.final_state.state[0, 2] == VillageCellState.OUTSIDE
    assert result.final_state.fuel_remaining_kg[0, 1:].sum() == 0.0


def test_ot06_partial_coverage_target_heats_then_cools_without_water_recovery() -> None:
    coverage = np.asarray([[1.0, 0.5]])
    ignition = np.asarray([[305.0, 400.0]])
    request = _request(
        ("grass", "grass"),
        edge_weight=0.02,
        end_time_s=8.0,
        sample_interval_s=0.25,
        heat_loss_W_m2_K=2.0,
        ignition_temperature=ignition,
        coverage=coverage,
    )
    result = run_v3_simulation(request)
    temperatures = np.asarray(
        [snapshot.surface_temperature_K[0, 1] for snapshot in result.snapshots]
    )

    assert request.parameters.covered_area_m2[0, 1] == pytest.approx(0.5)
    assert request.parameters.pyrolysable_dry_fuel_mass_kg[0, 1] == pytest.approx(0.5)
    assert np.max(temperatures) > temperatures[-1] > 300.0
    assert result.ignition_cause[0, 1] == IGNITION_CAUSE_NONE
    assert np.all(np.diff([s.heated_water_remaining_kg[0, 1] for s in result.snapshots]) <= 1e-12)


def test_ot07_building_index_changes_only_attribution() -> None:
    request = _request(("grass", "building_wood", "shrub"), edge_weight=0.08)
    changed = replace(request, building_index=np.asarray([[99, -1, 42]], dtype=np.int32))
    first = run_v3_simulation(request)
    second = run_v3_simulation(changed)

    assert np.array_equal(first.final_state.state, second.final_state.state)
    assert np.allclose(first.final_state.ignition_time_s, second.final_state.ignition_time_s)
    assert np.allclose(first.final_state.fuel_remaining_kg, second.final_state.fuel_remaining_kg)


def test_vt15_delayed_source_runs_after_first_source_burns_out() -> None:
    events = (VillageIgnitionEvent(0.0, 0, 0), VillageIgnitionEvent(5.0, 0, 2))
    result = run_v3_simulation(
        _request(("grass", "grass", "shrub"), edge_weight=0.005, events=events)
    )

    assert result.final_state.ignition_time_s[0, 2] == pytest.approx(5.0)
    assert result.ignition_cause[0, 2] == IGNITION_CAUSE_DIRECT
    assert result.final_state.state[0, 0] == VillageCellState.BURNED_OUT


def test_exact_sampling_never_steps_past_requested_clock() -> None:
    request = _request(
        ("grass",), edge_weight=None, max_dt_s=0.25, end_time_s=2.3, sample_interval_s=0.7
    )
    result = run_v3_simulation(request)

    assert [snapshot.time_s for snapshot in result.snapshots] == pytest.approx(
        [0.0, 0.7, 1.4, 2.1, 2.3]
    )


def test_ot10_vt11_vt17_timestep_and_edge_order_converge() -> None:
    results = [
        run_v3_simulation(
            _request(("grass", "shrub", "tree_equivalent"), edge_weight=0.08, max_dt_s=dt)
        )
        for dt in (0.25, 0.125, 0.0625)
    ]
    reversed_result = run_v3_simulation(
        _request(
            ("grass", "shrub", "tree_equivalent"),
            edge_weight=0.08,
            max_dt_s=0.125,
            reverse_edges=True,
        )
    )

    for result in results:
        assert result.consumed_fuel_kg == pytest.approx(3.0, rel=1e-10)
        assert np.array_equal(result.final_state.state, results[-1].final_state.state)
    assert (
        np.max(
            np.abs(
                results[-2].final_state.ignition_time_s - results[-1].final_state.ignition_time_s
            )
        )
        <= 2.0
    )
    assert np.array_equal(reversed_result.final_state.state, results[1].final_state.state)
    assert np.allclose(
        reversed_result.final_state.ignition_time_s,
        results[1].final_state.ignition_time_s,
    )


def test_energy_budget_partitions_released_and_intercepted_radiation() -> None:
    result = run_v3_simulation(_request(("grass", "grass"), edge_weight=0.08))
    budget = result.energy_budget

    assert budget.released_J == pytest.approx(
        budget.radiative_emitted_J + budget.non_radiative_released_J
    )
    assert budget.radiative_emitted_J == pytest.approx(
        budget.radiative_intercepted_J + budget.radiative_unintercepted_J
    )
    assert budget.radiative_intercepted_J == pytest.approx(
        budget.susceptible_incident_J
        + budget.inert_received_J
        + budget.inactive_combustible_received_J
    )


def test_v06_v3_bundle_round_trip_and_viewer_output(tmp_path: Path) -> None:
    from pyfireca.village.workflow_v3 import (
        load_v3_case_bundle,
        run_v3_case_bundle,
        write_v3_case_bundle,
    )

    request = _request(("grass", "shrub", "building_wood"), edge_weight=0.08)
    bundle_dir = write_v3_case_bundle(
        request,
        tmp_path / "case",
        material_labels={0: "草地", 1: "灌木", 2: "木质建筑"},
        landcover_labels={0: "grass", 1: "shrub", 2: "building"},
        metadata={"evidence": "synthetic", "scenario_id": "v05-test"},
    )
    loaded = load_v3_case_bundle(bundle_dir)
    assert loaded.request.grid == request.grid
    assert np.array_equal(loaded.request.burnable_mask, request.burnable_mask)
    assert np.array_equal(loaded.request.exposure_edges.source, request.exposure_edges.source)
    assert loaded.material_labels[0] == "草地"

    _, output = run_v3_case_bundle(bundle_dir, tmp_path / "run")
    manifest = json.loads((output / "viewer/manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "village-viewer-3"
    assert manifest["scenario"]["includes_nonbuilding_fuels"] is True
    assert manifest["landcover"]["labels"]["1"] == "shrub"
    cells = request.grid.height * request.grid.width
    assert (output / "viewer/state.bin").stat().st_size == manifest["frame_count"] * cells
    assert (output / "viewer/temperature_valid.bin").is_file()
    assert (output / "viewer/surface_water.bin").is_file()
    assert (output / "energy_budget.csv").is_file()
    assert json.loads((output / "material_outcomes.json").read_text(encoding="utf-8"))

    streamed_result, chunked_output = run_v3_case_bundle(
        bundle_dir, tmp_path / "chunked-run", chunk_frames=2
    )
    assert streamed_result.snapshots == ()
    chunked_manifest = json.loads(
        (chunked_output / "viewer/manifest.json").read_text(encoding="utf-8")
    )
    assert chunked_manifest["storage"] == {"mode": "frame_chunks", "chunk_frames": 2}
    assert "chunks" in chunked_manifest["state"]
    assert not (chunked_output / "viewer/state.bin").exists()
    np.testing.assert_array_equal(
        np.load(output / "outputs/state_samples.npy"),
        np.load(chunked_output / "outputs/state_samples.npy"),
    )
    np.testing.assert_allclose(
        np.load(output / "outputs/fuel_fraction_samples.npy"),
        np.load(chunked_output / "outputs/fuel_fraction_samples.npy"),
    )
    assert np.array_equal(
        np.load(output / "outputs/sample_times_s.npy"),
        np.load(chunked_output / "outputs/sample_times_s.npy"),
    )
    from pyfireca.village.inspection_v3 import inspect_v3_run

    chunked_report = inspect_v3_run(chunked_output)
    assert chunked_report["visual_acceptance"] is True


def test_v06_bundle_uses_declared_spatial_assets_for_viewer(tmp_path: Path) -> None:
    from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle

    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "manifest.json").write_text(
        json.dumps({"artifacts": {"background": "background.png"}}), encoding="utf-8"
    )
    (prepared / "background.png").write_bytes(b"png")
    (prepared / "buildings.json").write_text("{}", encoding="utf-8")
    (prepared / "image_ref.json").write_text(
        json.dumps({"affine_pixel_to_world": [1, 0, 0, 0, -1, 0]}), encoding="utf-8"
    )
    request = _request(("grass",))
    bundle = write_v3_case_bundle(
        request,
        tmp_path / "case",
        metadata={"spatial_assets_dir": str(prepared)},
    )

    _, output = run_v3_case_bundle(bundle, tmp_path / "run")
    viewer = output / "viewer"
    manifest = json.loads((viewer / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["background"] == "background.png"
    assert (viewer / "background.png").read_bytes() == b"png"
    assert json.loads((viewer / "image_ref.json").read_text(encoding="utf-8"))[
        "affine_pixel_to_world"
    ] == [1, 0, 0, 0, -1, 0]


def test_v08_crop_preserves_world_origin_and_remaps_edges(tmp_path: Path) -> None:
    from pyfireca.village.workflow_v3 import (
        crop_v3_case_bundle,
        load_v3_case_bundle,
        write_v3_case_bundle,
    )

    request = _request(("grass", "shrub", "tree_equivalent"), edge_weight=0.08)
    bundle = write_v3_case_bundle(request, tmp_path / "parent")
    cropped = crop_v3_case_bundle(
        bundle,
        tmp_path / "crop",
        row_start=0,
        row_stop=1,
        col_start=1,
        col_stop=3,
        end_time_s=5.0,
        sample_interval_s=1.0,
        ignition=VillageIgnitionEvent(0.0, 0, 0),
    )
    loaded = load_v3_case_bundle(cropped)
    assert loaded.request.grid.shape == (1, 2)
    assert loaded.request.grid.x_min_m == 1.0
    assert loaded.request.grid.y_max_m == 1.0
    assert np.array_equal(loaded.request.exposure_edges.source, np.asarray([0], dtype=np.int32))
    assert np.array_equal(loaded.request.exposure_edges.target, np.asarray([1], dtype=np.int32))


def test_v09_inspection_matches_first_middle_and_final_backend_frames(tmp_path: Path) -> None:
    from pyfireca.village.inspection_v3 import inspect_v3_run
    from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle

    request = _request(("grass", "shrub", "tree_equivalent"), edge_weight=0.08)
    bundle = write_v3_case_bundle(request, tmp_path / "case")
    _, output = run_v3_case_bundle(bundle, tmp_path / "run")
    report_path = tmp_path / "inspection.json"
    report = inspect_v3_run(output, output=report_path, cell_ids=[0, 2])

    assert report["visual_acceptance"] is True
    assert report["backend_checks"] == {
        "state_samples_match": True,
        "fuel_samples_match": True,
    }
    assert [item["frame"] for item in report["representative_frames"]] == [0, 8, 16]
    assert len(report["representative_frames"][0]["cells"]) == 2
    assert report_path.is_file()


def test_v09_prepared_assets_and_cropped_building_cells_use_nested_manifest(tmp_path: Path) -> None:
    from pyfireca.village.outputs_v3 import _copy_spatial_assets, _write_building_cells

    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "background.png").write_bytes(b"png")
    (prepared / "image_ref.json").write_text("{}", encoding="utf-8")
    (prepared / "buildings.json").write_text("{}", encoding="utf-8")
    (prepared / "manifest.json").write_text(
        json.dumps({"artifacts": {"background": "background.png"}}), encoding="utf-8"
    )
    (prepared / "building_overlap.csv").write_text(
        "building_id,row,col\nB0233,1,2\nB0999,0,0\n", encoding="utf-8"
    )
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    assert _copy_spatial_assets(prepared, viewer) == "background.png"
    assert (viewer / "background.png").read_bytes() == b"png"

    request = _request(("grass",))
    _write_building_cells(
        request,
        viewer,
        prepared,
        {"crop_rows": [1, 2], "crop_cols": [2, 3]},
    )
    cells = json.loads((viewer / "building_cells.json").read_text(encoding="utf-8"))
    assert cells["buildings"] == {"B0233": [0]}


def test_v10_preflight_reports_research_gate_and_resource_budget(tmp_path: Path) -> None:
    from pyfireca.village.preflight_v3 import preflight_v3_case
    from pyfireca.village.workflow_v3 import write_v3_case_bundle

    request = _request(("grass", "shrub", "tree_equivalent"), edge_weight=0.08)
    blocked_bundle = write_v3_case_bundle(
        request,
        tmp_path / "blocked",
        metadata={"assumptions": True, "simulation_scope": "classified_area_only"},
    )
    blocked = preflight_v3_case(blocked_bundle)
    assert blocked["status"] == "blocked"
    assert blocked["research_full_village_ready"] is False
    assert "scenario_contains_synthetic_assumptions" in blocked["research_gate_reasons"]
    assert blocked["classification"]["nonbuilding_burnable_cell_count"] == 3
    assert blocked["transport"]["edge_count"] == 2

    ready_bundle = write_v3_case_bundle(
        request,
        tmp_path / "ready",
        metadata={"assumptions": False, "simulation_scope": "full_village"},
    )
    ready = preflight_v3_case(ready_bundle, max_edge_count=2, max_memory_mb=1)
    assert ready["status"] == "ready"
    assert ready["research_full_village_ready"] is True


def test_v10_unknown_review_reports_components_without_assigning_materials(tmp_path: Path) -> None:
    from PIL import Image

    from pyfireca.village.unknown_review_v3 import review_unknown_v3
    from pyfireca.village.workflow_v3 import write_v3_case_bundle

    request = _request(("grass", "grass"))
    known = np.asarray([[True, False]], dtype=bool)
    burnable = request.burnable_mask.copy()
    burnable[0, 1] = False
    source = request.source_mask.copy()
    source[0, 1] = False
    target = request.target_mask.copy()
    target[0, 1] = False
    piloted = request.piloted_ignition_mask.copy()
    piloted[0, 1] = False
    reviewed_request = replace(
        request,
        known_mask=known,
        burnable_mask=burnable,
        source_mask=source,
        target_mask=target,
        piloted_ignition_mask=piloted,
        unknown_policy="exclude",
    )
    bundle = write_v3_case_bundle(reviewed_request, tmp_path / "case")
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "manifest.json").write_text("{}", encoding="utf-8")
    Image.fromarray(np.zeros((1, 2, 3), dtype=np.uint8)).save(prepared / "background.png")
    output = tmp_path / "review"

    report = review_unknown_v3(prepared, bundle, output_dir=output)

    assert report["status"] == "review_required"
    assert report["unknown_cell_count"] == 1
    assert report["component_count"] == 1
    assert report["largest_components"][0]["area_m2"] == pytest.approx(1.0)
    assert report["assumptions"]["no_case_arrays_modified"] is True
    assert (output / "unknown_review.json").is_file()
    assert (output / "unknown_components.csv").is_file()
    component_ids = np.load(output / "unknown_component_id.npy", allow_pickle=False)
    assert np.array_equal(component_ids, np.asarray([[0, 1]], dtype=np.int32))
    template = (output / "unknown_component_survey.template.yml").read_text(encoding="utf-8")
    assert "material_class: unknown" in template
    from pyfireca.village.survey import parse_field_survey

    survey = parse_field_survey(yaml.safe_load(template))
    assert len(survey.records) == 1
    assert survey.records[0].geometry_ref == "v3-unknown-component/C0001"
    assert np.array_equal(reviewed_request.known_mask, known)


def test_rgb_hypothesis_inert_completion_adds_receivers_without_fuel() -> None:
    from pyfireca.village.gaoyou_v3 import _complete_unknown_as_inert_receivers

    request = _request(("grass", "grass"))
    completed = _complete_unknown_as_inert_receivers(
        request.parameters,
        np.asarray([[False, True]], dtype=bool),
        request.grid.cell_area_m2,
    )

    assert completed.receiver_area_m2[0, 1] == pytest.approx(1.0)
    assert completed.covered_area_m2[0, 1] == pytest.approx(1.0)
    assert completed.initial_dry_fuel_mass_kg[0, 1] == pytest.approx(0.0)
    assert completed.peak_hrr_W[0, 1] == pytest.approx(0.0)


def test_w00_dem_roundtrip_crop_and_integrity(tmp_path: Path) -> None:
    from pyfireca.village.workflow_v3 import (
        crop_v3_case_bundle,
        load_v3_case_bundle,
        write_v3_case_bundle,
    )

    request = replace(
        _request(("grass", "grass", "grass"), edge_weight=0.08),
        ground_elevation_m=np.asarray([[500.0, 501.5, 499.0]]),
    )
    bundle = write_v3_case_bundle(request, tmp_path / "case")
    loaded = load_v3_case_bundle(bundle).request
    np.testing.assert_array_equal(loaded.ground_elevation_m, request.ground_elevation_m)
    np.testing.assert_array_equal(loaded.exposure_edges.weight, request.exposure_edges.weight)
    cropped = crop_v3_case_bundle(
        bundle,
        tmp_path / "crop",
        row_start=0,
        row_stop=1,
        col_start=1,
        col_stop=3,
        ignition=VillageIgnitionEvent(0.0, 0, 0),
    )
    crop = load_v3_case_bundle(cropped).request
    np.testing.assert_array_equal(crop.ground_elevation_m, [[501.5, 499.0]])
    assert crop.grid.x_min_m == request.grid.x_min_m + request.grid.cell_size_m
    np.testing.assert_array_equal(crop.exposure_edges.source, [0])
    np.testing.assert_array_equal(crop.exposure_edges.target, [1])
    np.testing.assert_array_equal(crop.exposure_edges.weight, [0.08])
    np.save(bundle / "ground_elevation_m.npy", np.asarray([[500.0, 501.5, 498.0]]))
    with pytest.raises(ValueError, match="elevation hash"):
        load_v3_case_bundle(bundle)
    (bundle / "ground_elevation_m.npy").unlink()
    with pytest.raises(FileNotFoundError, match="elevation array"):
        load_v3_case_bundle(bundle)


def test_w00_legacy_flat_bundle_and_future_schema(tmp_path: Path) -> None:
    from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

    request = _request(("grass", "grass"), edge_weight=0.08)
    bundle = write_v3_case_bundle(request, tmp_path / "case")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["ground_elevation"]
    manifest_path.write_text(json.dumps(manifest))
    legacy = load_v3_case_bundle(bundle).request
    assert legacy.ground_elevation_m is None
    expected, actual = run_v3_simulation(request), run_v3_simulation(legacy)
    for name in actual.final_state.__dataclass_fields__:
        np.testing.assert_equal(
            getattr(actual.final_state, name), getattr(expected.final_state, name)
        )
    manifest["schema_version"] = "village-v3-case-2"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported V3"):
        load_v3_case_bundle(bundle)


def test_w00_interactive_http_preserves_dem_and_applies_user_selection(tmp_path: Path) -> None:
    import http.server
    import threading
    import time
    import urllib.error
    import urllib.request

    from pyfireca.village.interactive_v3 import InteractiveV3State, _handler
    from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

    request = replace(
        _request(("grass", "grass", "concrete"), edge_weight=0.08),
        ground_elevation_m=np.asarray([[500.0, 502.0, 499.0]]),
    )
    bundle = write_v3_case_bundle(request, tmp_path / "case")
    original = {p.name: sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}
    state = InteractiveV3State(
        case_dir=bundle,
        initial_viewer=tmp_path,
        output_root=tmp_path / "jobs",
        burnable=request.burnable_mask,
        grid={"height": 1, "width": 3},
        ignition={"row": 0, "col": 0},
        end_time_s=request.scenario.end_time_s,
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path: str) -> dict:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return json.load(response)

    def post(payload: dict) -> dict:
        query = urllib.request.Request(
            base + "/api/run",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Origin": base},
        )
        with urllib.request.urlopen(query, timeout=5) as response:
            assert response.status == 202
            return json.load(response)

    try:
        assert get("/api/config")["max_end_time_s"] == 30000
        good = {"row": 0, "col": 1, "end_time_s": 2.5}
        for invalid in (
            {**good, "col": 2},
            {**good, "row": -1},
            {**good, "row": True},
            {**good, "end_time_s": True},
            {**good, "end_time_s": 30001},
            {**good, "end_time_s": float("nan")},
            {**good, "wind_speed_m_s": 5},
        ):
            with pytest.raises(urllib.error.HTTPError) as error:
                post(invalid)
            assert error.value.code == 400
        job_id = post(good)["job_id"]
        deadline = time.monotonic() + 15
        job = get(f"/api/jobs/{job_id}")
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            job = get(f"/api/jobs/{job_id}")
        assert job["status"] == "completed", job
        generated = load_v3_case_bundle(state.output_root / job_id / "case").request
        assert generated.scenario.ignitions == (VillageIgnitionEvent(0.0, 0, 1),)
        assert generated.scenario.end_time_s == 2.5
        np.testing.assert_array_equal(generated.ground_elevation_m, request.ground_elevation_m)
        np.testing.assert_array_equal(
            generated.exposure_edges.weight, request.exposure_edges.weight
        )
        outputs = state.output_root / job_id / "run" / "outputs"
        assert np.load(outputs / "sample_times_s.npy")[-1] == 2.5
        assert np.load(outputs / "ignition_cause.npy")[0, 1] == IGNITION_CAUSE_DIRECT
        viewer = get(f"/result/{job_id}/manifest.json")
        assert viewer["elevation"]["path"] == "elevation.bin"
        with urllib.request.urlopen(
            base + f"/result/{job_id}/elevation.bin", timeout=5
        ) as response:
            np.testing.assert_array_equal(
                np.frombuffer(response.read(), dtype="<f4"), request.ground_elevation_m.ravel()
            )
        assert {p.name: sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()} == original
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
