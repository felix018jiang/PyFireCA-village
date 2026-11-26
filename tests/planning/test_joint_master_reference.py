"""A roof-only joint arm must preserve its retained master physical request."""

import json
from dataclasses import replace

import numpy as np
import pytest
from test_joint_comparison import inputs as inputs
from test_joint_comparison import retained_setup
from test_joint_comparison import runner as runner

from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


def reference_setup(inputs, runner, *, source_area_limit_m2=25.0):
    path, config, _ = retained_setup(inputs)
    _, _, bundle, transport, inventory, cells = inputs
    config = config | {
        "refinement_ids": [],
        "source_area_limit_m2": source_area_limit_m2,
        "source_area_tolerance_m2": 1e-6,
    }
    fires, _ = runner.load_joint_fires(path, config, ("M", "U"), inventory)
    bundle = replace(
        bundle,
        request=replace(
            bundle.request, scenario=replace(bundle.request.scenario, max_dt_s=config["max_dt_s"])
        ),
    )
    results = []
    anchor = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=tuple(sorted(inventory.candidate_ids)),
        refinement_ids=(),
        maximum_demolitions=len(inventory.candidate_ids),
        fires=fires,
        code_version="frozen-master-code",
        horizon_s=config["horizon_s"],
        scales_m=tuple(config["roof_scales_m"]),
        quadrature_spacing_m=0.5,
        max_nodes=config["max_nodes"],
        max_radiation_edges=config["max_radiation_edges"],
        source_area_limit_m2=source_area_limit_m2,
        source_area_tolerance_m2=1e-6,
        freeze_integration_membership=True,
        experimental_numerics=True,
        on_evaluation=lambda *args: results.append(args),
    )
    master = config | {
        "schema_version": "heritage-area-master-config-1",
        "source_policy": "area_bounded_retained_sources",
    }
    master_path = path.parent / "master.json"
    master_path.write_text(json.dumps(master))
    experiment = {
        "config": master,
        "config_sha256": file_sha256(master_path),
        "problem": anchor.problem,
        "problem_sha256": anchor.problem_sha256,
        "candidate_ids": list(anchor.candidate_ids),
        "source_policy": "area_bounded_retained_sources",
        "references_sha256": {},
    }
    experiment_path = path.parent / "master-experiment.json"
    experiment_path.write_text(json.dumps(experiment))
    config.update(
        candidate_ids=list(anchor.candidate_ids),
        reference_master_experiment_path=str(experiment_path),
        reference_master_experiment_sha256=file_sha256(experiment_path),
        reference_master_config_path=str(master_path),
        reference_master_config_sha256=file_sha256(master_path),
        plans=[
            {"plan_id": "roof-only", "demolished_ids": ["M"], "cleared_physical_cell_ids": []},
            {"plan_id": "joint", "demolished_ids": ["M"], "cleared_physical_cell_ids": [cells[1]]},
            {
                "plan_id": "outside-fixed-refinement",
                "demolished_ids": ["U"],
                "cleared_physical_cell_ids": [],
            },
        ],
    )
    path.write_text(json.dumps(config))
    return path, config, anchor, results


def test_static_wind_cannot_reuse_a_calm_master(inputs, runner):
    path, config, _, _ = reference_setup(inputs, runner)
    config["static_wind"] = {
        "wind_speed_m_s": 2,
        "wind_from_deg": 270,
        "flame_tilt_coefficient_s_m": 0.2,
    }
    with pytest.raises(ValueError, match="physical mesh/source problem differs"):
        runner.make_oracle(path, config, "wind-producer")


def test_master_reference_roof_only_input_and_trajectory_are_identical(inputs, runner):
    path, config, anchor, captured = reference_setup(inputs, runner)
    oracle, _ = runner.make_oracle(path, config, "new-joint-producer")
    scenario = next(iter(anchor.scenarios))
    anchor(("M",), scenario)
    _, _, _, anchor_record, anchor_layout, anchor_result = captured[0]
    _, record, layout, result = oracle.evaluate("roof-only", scenario)
    assert oracle.maximum_demolitions is None
    assert oracle.candidate_ids == anchor.candidate_ids
    assert oracle.refinement_ids == anchor.refinement_ids == ()
    assert content_hash(oracle.problem["fires"]) == content_hash(anchor.problem["fires"])
    assert (
        record.diagnostics["physical_request_sha256"]
        == anchor_record.diagnostics["physical_request_sha256"]
    )
    assert layout.geometry == anchor_layout.geometry
    for field in anchor_result.final_state.__dataclass_fields__:
        np.testing.assert_array_equal(
            getattr(result.final_state, field), getattr(anchor_result.final_state, field)
        )
    assert oracle.validate_plan("outside-fixed-refinement").demolished_ids == ("U",)
    assert "U" not in oracle.refinement_ids
    # Grass construction raises C, while the retained-source cap guards roof cost only.
    _, joint, _, _ = oracle.evaluate("joint", scenario)
    assert joint.intervention["cleared_action_geometry_union_area_m2"] == 50
    assert joint.intervention["cleared_roof_geometry_union_area_m2"] == 25


def test_reference_rejects_out_of_roof_area_domain_before_any_solve(inputs, runner):
    path, config, _, _ = reference_setup(inputs, runner)
    config["plans"].append(
        {"plan_id": "outside-cap", "demolished_ids": ["M", "U"], "cleared_physical_cell_ids": []}
    )
    with pytest.raises(ValueError, match="source-coverage area cap"):
        runner.make_oracle(path, config, "joint-code")


@pytest.mark.parametrize(
    "field,value",
    [
        ("refinement_ids", ["M"]),
        ("max_dt_s", 1.0),
        ("candidate_ids", ["M"]),
        ("source_area_tolerance_m2", 0.1),
    ],
)
def test_reference_rejects_silent_mesh_numerical_or_candidate_changes(inputs, runner, field, value):
    path, config, _, _ = reference_setup(inputs, runner)
    config[field] = value
    with pytest.raises(ValueError):
        runner.make_oracle(path, config, "joint-code")


def test_reference_experiment_bytes_cannot_change_after_config_binding(inputs, runner):
    path, config, _, _ = reference_setup(inputs, runner)
    experiment = path.parent / "master-experiment.json"
    experiment.write_text(experiment.read_text() + "\n")
    with pytest.raises(ValueError, match="reference file hash"):
        runner.make_oracle(path, config, "joint-code")
