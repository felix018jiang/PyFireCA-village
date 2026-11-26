"""Finite higher-cost catalogs retain exact sources on the reference mesh."""

from dataclasses import replace

import pytest
from test_joint_comparison import inputs as inputs
from test_joint_comparison import runner as runner
from test_joint_master_reference import reference_setup

from pyfireca.planning.heritage import content_hash


def configured(inputs, runner):
    path, config, anchor, _ = reference_setup(inputs, runner, source_area_limit_m2=10)
    config["source_coverage_policy"] = "finite_declared_plans"
    config["plans"] = [
        {"plan_id": "larger-roof-cost", "demolished_ids": ["U"], "cleared_physical_cell_ids": []}
    ]
    return path, config, anchor


def test_finite_catalog_above_reference_cap_is_realized_without_changing_mesh(inputs, runner):
    path, config, anchor = configured(inputs, runner)
    oracle, _ = runner.make_oracle(path, config, "finite-catalog-code")
    assert oracle.source_area_function(("U",)) == pytest.approx(25)
    assert config["source_area_limit_m2"] == 10
    assert content_hash(oracle.problem["fires"]) == content_hash(anchor.problem["fires"])
    proof = oracle.problem["finite_declared_source_catalog"]
    assert proof["reference_area_cap_is_not_an_admissibility_bound_for_this_catalog"]
    assert proof["rows"][0]["actual_source_id"] == "M"
    check, record, _, result = oracle.evaluate("larger-roof-cost", next(iter(oracle.scenarios)))
    assert record.metrics["actual_source_ids"] == ["M"]
    assert record.intervention["cleared_roof_union_area_m2"] == pytest.approx(25)
    assert result.final_state.time_s == config["horizon_s"]
    assert check.evidence["source_id"] == "M"
    # The default bounded mode still rejects exactly this plan.
    config.pop("source_coverage_policy")
    with pytest.raises(ValueError, match="source-coverage area cap"):
        runner.make_oracle(path, config, "bounded")


def test_source_outside_prepared_prefix_is_rejected_before_simulation(inputs, runner):
    path, config, _ = configured(inputs, runner)
    config["plans"][0]["demolished_ids"] = ["M"]
    with pytest.raises(ValueError, match="outside the frozen reference prefix"):
        runner.make_oracle(path, config, "finite-catalog-code")


def test_finite_catalog_cannot_be_mutated_after_its_source_proof(inputs, runner):
    path, config, _ = configured(inputs, runner)
    oracle, _ = runner.make_oracle(path, config, "finite-catalog-code")
    plan = oracle.plans["larger-roof-cost"]
    oracle.plans[plan.plan_id] = replace(plan, cleared_physical_cell_ids=(1,))
    with pytest.raises(ValueError, match="changed after it was frozen"):
        oracle.validate_plan(plan.plan_id)


def test_certificate_roundtrips_serialized_fire_definitions(inputs, runner):
    path, config, _ = configured(inputs, runner)
    oracle, _ = runner.make_oracle(path, config, "finite-catalog-code")
    proof = runner.finite_source_catalog_certificate(
        oracle.plans.values(),
        oracle.problem["fires"],
        inventory_sha256=oracle.inventory.sha256,
        reference_problem_sha256=oracle.reference_master["problem_sha256"],
    )
    assert proof == oracle.finite_source_certificate
    missing = {label: {**fire, "footprints": {}} for label, fire in oracle.problem["fires"].items()}
    with pytest.raises(ValueError, match="outside the frozen reference prefix"):
        runner.finite_source_catalog_certificate(
            oracle.plans.values(),
            missing,
            inventory_sha256=oracle.inventory.sha256,
            reference_problem_sha256=oracle.reference_master["problem_sha256"],
        )


def test_source_selection_follows_priority_even_if_prepared_mapping_is_reordered(inputs, runner):
    path, config, _, _ = reference_setup(inputs, runner)
    config["source_coverage_policy"] = "finite_declared_plans"
    oracle, _ = runner.make_oracle(path, config, "finite-catalog-code")
    scenario = next(iter(oracle.scenarios))
    fire = oracle.fires[oracle.scenarios[scenario][0]]
    expected = oracle.actual_source_for("outside-fixed-refinement", scenario)
    fire["footprints"] = dict(reversed(list(fire["footprints"].items())))
    assert oracle.actual_source_for("outside-fixed-refinement", scenario) == expected == "M"


def test_finite_policy_requires_an_explicit_reference(inputs, runner):
    _, config, *_ = inputs
    config["source_coverage_policy"] = "finite_declared_plans"
    with pytest.raises(ValueError, match="frozen master mesh reference"):
        runner.validate_config(config)


def test_catalog_provenance_is_verified_before_a_simulation(inputs, runner):
    path, config, _ = configured(inputs, runner)
    catalog = path.parent / "frozen-catalog.json"
    catalog.write_text("original catalog")
    config["additional_frozen_inputs_sha256"] = {str(catalog): "0" * 64}
    with pytest.raises(ValueError, match="additional frozen catalog/provenance input changed"):
        runner.make_oracle(path, config, "finite-catalog-code")
