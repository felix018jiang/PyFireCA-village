"""Frozen, budgeted full-horizon roof-plus-vegetation comparisons; no optimization."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
from dataclasses import replace
from functools import partial
from itertools import pairwise
from math import isfinite
from pathlib import Path

import numpy as np
from preflight_heritage_exact_expansion import edge_bounds
from run_heritage_area_study import observation_status
from run_heritage_area_study import read_complete as read_area_complete
from run_heritage_fixed_comparison import (
    atomic_json,
    load_fires,
    result_curves,
    write_compact_arrays,
)

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.environments import ProtectionEnvironment, apply_static_wind
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.fire_loss import fire_loss_from_result
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import (
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import roof_area_integrated_radiation_graph
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study
from pyfireca.planning.terminal_verification import (
    close_terminal_event_curves,
    evaluate_terminal_joint,
    terminal_observation_is_complete,
)
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heritage-joint-comparison-1"
REQUIRED_ARTIFACTS = frozenset(
    {
        "evaluation.json",
        "check.json",
        "curves.json",
        "fire-loss.json",
        "layout.npz",
        "final-state.npz",
        "loss-basis.npz",
        "initial-post-budget.json",
    }
)


def _positive(value, name, *, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
        or (integer and not isinstance(value, int))
    ):
        raise ValueError(f"{name} must be positive finite" + (" integer" if integer else ""))


def _safe(name):
    return (
        isinstance(name, str) and bool(name) and Path(name).name == name and name not in {".", ".."}
    )


def validate_config(config):
    if config.get("schema_version") != SCHEMA:
        raise ValueError("explicit joint-comparison schema required")
    if "static_wind" in config:
        wind = config["static_wind"]
        if not isinstance(wind, dict) or wind.keys() != {
            "wind_speed_m_s",
            "wind_from_deg",
            "flame_tilt_coefficient_s_m",
        }:
            raise ValueError("static_wind requires speed, FROM direction and tilt coefficient")
        ProtectionEnvironment("static_wind", 1.0, **wind)
    references = config.get("additional_frozen_inputs_sha256", {})
    if not isinstance(references, dict) or any(
        not isinstance(path, str)
        or not path
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        for path, digest in references.items()
    ):
        raise ValueError(
            "additional frozen provenance inputs require file paths and SHA256 digests"
        )
    for key in (
        "horizon_s",
        "max_dt_s",
        "quadrature_spacing_m",
        "max_wall_time_s",
        "minimum_free_disk_GB",
    ):
        _positive(config[key], key)
    for key in ("max_new_checks", "max_nodes", "max_radiation_edges"):
        _positive(config[key], key, integer=True)
    terminal = config.get("terminal_fire_verification", False)
    if not isinstance(terminal, bool):
        raise ValueError("terminal_fire_verification must be boolean")
    if terminal:
        if "terminal_horizon_cap_s" not in config:
            raise ValueError("terminal verification requires an explicit terminal_horizon_cap_s")
        _positive(config["terminal_horizon_cap_s"], "terminal_horizon_cap_s")
        if config["terminal_horizon_cap_s"] < config["horizon_s"]:
            raise ValueError("terminal horizon cap cannot precede the reference horizon")
    elif config.get("terminal_horizon_cap_s") is not None:
        raise ValueError("terminal horizon cap requires terminal_fire_verification=True")
    scales = config["roof_scales_m"]
    if not scales or len(set(scales)) != len(scales):
        raise ValueError("unique positive roof scales required")
    for scale in scales:
        _positive(scale, "roof scale")
    test_protocol = config.get("experimental_test_protocol", False)
    if not isinstance(test_protocol, bool):
        raise ValueError("experimental_test_protocol must be boolean")
    if not test_protocol and (
        config["horizon_s"] != 3600
        or tuple(scales) != (1.0, 0.5)
        or config["quadrature_spacing_m"] != 0.5
    ):
        raise ValueError(
            "production joint protocol requires 3600 s, dual scales and 0.5 m quadrature"
        )
    checkpoints = config.get("checkpoints_s", [config["horizon_s"]])
    if (
        not checkpoints
        or any(isinstance(t, bool) or not isfinite(t) or t < 0 for t in checkpoints)
        or any(a >= b for a, b in pairwise(checkpoints))
        or checkpoints[-1] != config["horizon_s"]
    ):
        raise ValueError("ordered checkpoints must end at the complete horizon")
    if not config.get("source_fires") or not config.get("plans"):
        raise ValueError("explicit fixed fires and joint plans required")
    fire_ids = [f["fire_id"] for f in config["source_fires"]]
    if not all(map(_safe, fire_ids)) or len(set(fire_ids)) != len(fire_ids):
        raise ValueError("unique safe fire IDs required")
    plan_ids = [p["plan_id"] for p in config["plans"]]
    if not all(map(_safe, plan_ids)) or len(set(plan_ids)) != len(plan_ids):
        raise ValueError("unique safe plan IDs required")
    physical = []
    interventions = set()
    for row in config["plans"]:
        plan = plan_from_mapping(row)
        interventions.update(plan.demolished_ids)
        physical.append((tuple(sorted(plan.demolished_ids)), plan.cleared_physical_cell_ids))
    if len(set(physical)) != len(physical):
        raise ValueError("duplicate physical joint arms must be consolidated")
    refinement = config["refinement_ids"]
    reference_mode = bool(config.get("reference_master_experiment_path"))
    coverage_policy = config.get("source_coverage_policy", "reference_area_bound")
    if coverage_policy not in {"reference_area_bound", "finite_declared_plans"}:
        raise ValueError("unsupported explicit source coverage policy")
    if coverage_policy == "finite_declared_plans" and not reference_mode:
        raise ValueError("finite declared source coverage requires a frozen master mesh reference")
    if len(set(refinement)) != len(refinement) or (
        not reference_mode and not interventions <= set(refinement)
    ):
        raise ValueError("common refinement must uniquely contain every intervention building")
    if any(not isinstance(i, str) or not i for i in refinement):
        raise ValueError("refinement IDs must be building identities")
    if config.get("source_policy", "fixed") not in {"fixed", "retained_sources"}:
        raise ValueError("source_policy must be fixed or retained_sources")
    if reference_mode:
        if config.get("source_policy") != "retained_sources":
            raise ValueError("master mesh reference requires retained_sources")
        if not config.get("reference_master_experiment_sha256") or "candidate_ids" not in config:
            raise ValueError("master reference requires its digest and complete candidate IDs")
        candidates = config["candidate_ids"]
        if len(candidates) != len(set(candidates)) or not interventions <= set(candidates):
            raise ValueError("joint interventions must belong to the full reference candidate set")
        for key in ("source_area_limit_m2", "source_area_tolerance_m2"):
            value = config.get(key, 0 if key.endswith("tolerance_m2") else None)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    "master reference requires a finite nonnegative roof source-area cap"
                )
    elif config.get("source_area_limit_m2") is not None:
        raise ValueError("roof source-area cap requires an explicit master reference")
    cap = config.get("max_total_checks", 20)
    _positive(cap, "max_total_checks", integer=True)
    if cap > 40:
        raise ValueError("joint comparison declaration cannot exceed 40 CA checks")
    planned = len(fire_ids) * len(scales) * len(plan_ids)
    if planned > cap or config["max_new_checks"] > cap:
        raise ValueError("joint comparison exceeds its explicit max_total_checks cap")
    if "new_independent_fire_draw_count" not in config:
        raise ValueError("declare new_independent_fire_draw_count explicitly, including zero")
    new_draws = config["new_independent_fire_draw_count"]
    if (
        isinstance(new_draws, bool)
        or not isinstance(new_draws, int)
        or not 0 <= new_draws <= len(fire_ids)
    ):
        raise ValueError(
            "new independent fire count must be explicit nonnegative within fixed fires"
        )
    # An execution subset never changes the full physical question or its
    # completeness requirement; omitted observations stay missing in summaries.
    tuple(execution_checks(config))
    return tuple(sorted(interventions))


def plan_from_mapping(row):
    return RoofJointPlan(
        row["plan_id"],
        tuple(row["demolished_ids"]),
        ground_evidence=row.get("ground_evidence", "hypothetical_joint_clearance;not_surveyed"),
        overlap_policy="retain_shared_roof",
        cleared_physical_cell_ids=tuple(row["cleared_physical_cell_ids"]),
    )


def read_master_reference(config_path, config):
    """Bind the joint catalog to one immutable retained-master physical mesh."""
    if not config.get("reference_master_experiment_path"):
        return None, {}
    references = {}

    def frozen_file(path_key, hash_key):
        path = (config_path.parent / config[path_key]).resolve()
        digest = config.get(hash_key)
        if not digest or file_sha256(path) != digest:
            raise ValueError(f"master reference file hash differs: {path_key}")
        references[str(path)] = digest
        return path

    path = frozen_file("reference_master_experiment_path", "reference_master_experiment_sha256")
    reference = read_json(path)
    master = reference["config"]
    if config.get("reference_master_config_path"):
        master_path = frozen_file("reference_master_config_path", "reference_master_config_sha256")
        if (
            read_json(master_path) != master
            or file_sha256(master_path) != reference["config_sha256"]
        ):
            raise ValueError("reference master config differs from the frozen experiment")
    else:
        master_path = path
        if any(
            not Path(value).is_absolute()
            for value in (
                master["study_path"],
                *(fire["protocol_path"] for fire in master["source_fires"]),
            )
        ):
            raise ValueError("relative master inputs require reference_master_config_path")
    if (
        master.get("source_policy") != "area_bounded_retained_sources"
        or reference.get("source_policy", master.get("source_policy"))
        != "area_bounded_retained_sources"
        or reference.get("problem_sha256") != content_hash(reference["problem"])
        or reference["problem"].get("freeze_integration_membership") is not True
    ):
        raise ValueError("reference must be a fingerprinted frozen-mesh retained-source master")
    if sorted(config["candidate_ids"]) != sorted(reference["problem"]["candidate_ids"]):
        raise ValueError("reference joint mode must preserve all master candidate actions")
    if (
        reference.get("candidate_ids", reference["problem"]["candidate_ids"])
        != reference["problem"]["candidate_ids"]
    ):
        raise ValueError("reference candidate declarations conflict")
    if sorted(config["refinement_ids"]) != sorted(master["refinement_ids"]):
        raise ValueError("joint reference mode cannot change the master refinement set")
    for key in (
        "horizon_s",
        "max_dt_s",
        "roof_scales_m",
        "quadrature_spacing_m",
        "source_area_limit_m2",
        "source_area_tolerance_m2",
    ):
        if config.get(key, 0 if key.endswith("tolerance_m2") else None) != master.get(
            key, 0 if key.endswith("tolerance_m2") else None
        ):
            raise ValueError(f"joint numerics/source coverage differs from reference master: {key}")
    if config.get("core_overlap_policy", "reject") != master.get("core_overlap_policy", "reject"):
        raise ValueError("joint reference core ownership policy differs")
    if (config_path.parent / config["study_path"]).resolve() != (
        master_path.parent / master["study_path"]
    ).resolve():
        raise ValueError("joint study path differs from reference master")

    def normalized_fires(fires, parent):
        return [
            (fire["fire_id"], str((parent / fire["protocol_path"]).resolve()), fire["draw_id"])
            for fire in fires
        ]

    if normalized_fires(config["source_fires"], config_path.parent) != normalized_fires(
        master["source_fires"], master_path.parent
    ):
        raise ValueError("joint retained-source protocols differ from reference master")
    for source_path, expected in reference.get("references_sha256", {}).items():
        if file_sha256(source_path) != expected:
            raise ValueError("reference master physical input bytes changed")
        references[source_path] = expected
    return reference, references


def verify_reference_problem(problem, reference):
    # Search-only early-stop/code-version/count-bound fields do not alter the
    # prepared physical request. The mesh, operators and area-prefix proof do.
    for field in (
        "case_sha256",
        "inventory_sha256",
        "transport_parameters",
        "candidate_ids",
        "refinement_seed_ids",
        "horizon_s",
        "scales_m",
        "quadrature_spacing_m",
        "core_overlap_policy",
        "fires",
        "freeze_integration_membership",
        "source_area_coverage",
        "experimental_numerics",
    ):
        if content_hash(problem.get(field)) != content_hash(reference.get(field)):
            raise ValueError(
                f"joint physical mesh/source problem differs from master reference: {field}"
            )


def finite_source_catalog_certificate(plans, fires, *, inventory_sha256, reference_problem_sha256):
    """Prove first-retained source coverage for exactly the declared finite plans.

    The reference area bound explains how the common mesh was prepared. It
    does not constrain roof cost in this opt-in catalog. Every actual source
    must independently belong to that immutable prepared prefix; no arbitrary
    outside-catalog plan or empty-source success is permitted.
    """
    rows = []
    for plan in sorted(plans, key=lambda p: p.plan_id):
        removed = set(plan.demolished_ids)
        for label, fire in sorted(fires.items()):
            protocol = fire["protocol"]
            if protocol["inventory_sha256"] != inventory_sha256 or len(protocol["draws"]) != 1:
                raise ValueError("finite source proof has a different inventory or draw set")
            draw = protocol["draws"][0]
            priority = draw["priority_ids"]
            actual = next(
                (identifier for identifier in priority if identifier not in removed), None
            )
            if actual is None:
                raise ValueError("finite catalog has no retained source")
            if actual not in fire["footprints"]:
                raise ValueError("finite catalog source is outside the frozen reference prefix")
            footprint = fire["footprints"][actual]
            footprint = footprint.to_mapping() if hasattr(footprint, "to_mapping") else footprint
            rows.append(
                {
                    "plan_id": plan.plan_id,
                    "demolished_ids": sorted(removed),
                    "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
                    "fire_id": label,
                    "draw_id": draw["draw_id"],
                    "protocol_sha256": protocol["protocol_sha256"],
                    "priority_sha256": content_hash(priority),
                    "removed_priority_prefix": list(priority[: priority.index(actual)]),
                    "actual_source_id": actual,
                    "prepared_prefix_ids": [i for i in priority if i in fire["footprints"]],
                    "source_footprint_sha256": content_hash(footprint),
                }
            )
    proof = {
        "schema_version": "finite-declared-retained-source-catalog-1",
        "inventory_sha256": inventory_sha256,
        "reference_master_problem_sha256": reference_problem_sha256,
        "reference_area_cap_is_not_an_admissibility_bound_for_this_catalog": True,
        "coverage_scope": "only_exact_declared_roof_and_ground_plans",
        "all_sources_in_immutable_reference_mesh_prefix": True,
        "rows": rows,
    }
    proof["certificate_sha256"] = content_hash(proof)
    return proof


class JointComparisonOracle(RoofDemolitionOracle):
    """All arms of one fixed fire share roof nodes and immutable quadrature membership."""

    def __init__(
        self, bundle, transport, inventory, *, config, fires, code_version, reference=None
    ):
        if "static_wind" in config:
            bundle, transport = apply_static_wind(
                bundle, **config["static_wind"], max_edge_count=config["max_radiation_edges"]
            )
        interventions = tuple(sorted({i for p in config["plans"] for i in p["demolished_ids"]}))
        reference_options = {}
        candidates = interventions
        maximum_demolitions = max(len(p["demolished_ids"]) for p in config["plans"])
        if reference is not None:
            candidates = tuple(sorted(config["candidate_ids"]))
            maximum_demolitions = None
            reference_options = {
                "source_area_limit_m2": config["source_area_limit_m2"],
                "source_area_tolerance_m2": config.get("source_area_tolerance_m2", 0),
            }
        super().__init__(
            bundle,
            transport,
            inventory,
            candidate_ids=candidates,
            refinement_ids=tuple(config["refinement_ids"]),
            fires=fires,
            code_version=code_version,
            horizon_s=config["horizon_s"],
            scales_m=tuple(config["roof_scales_m"]),
            quadrature_spacing_m=config["quadrature_spacing_m"],
            max_nodes=config["max_nodes"],
            max_radiation_edges=config["max_radiation_edges"],
            maximum_demolitions=maximum_demolitions,
            core_overlap_policy=config.get("core_overlap_policy", "reject"),
            stop_at_first_core_ignition=False,
            freeze_integration_membership=True,
            experimental_numerics=config.get("experimental_test_protocol", False),
            **reference_options,
        )
        self.reference_master = reference
        if reference is not None:
            verify_reference_problem(self.problem, reference["problem"])
            self.problem["reference_master_problem_sha256"] = reference["problem_sha256"]
            self.problem["reference_master_experiment_sha256"] = config[
                "reference_master_experiment_sha256"
            ]
        if "static_wind" in config:
            self.problem["static_wind"] = {
                **config["static_wind"],
                "evidence": bundle.metadata["protection_environment"]["evidence"],
                "direction_reference": "meteorological_from_grid_north",
                "scope": bundle.metadata["wind_scope"],
            }
        self.plans = {row["plan_id"]: plan_from_mapping(row) for row in config["plans"]}
        self.problem["joint_plans"] = {name: p.to_mapping() for name, p in self.plans.items()}
        self.problem["joint_action_cost_basis"] = (
            "roof_geometry_union_plus_whole_parent_ground_footprints"
        )
        self.problem["new_independent_fire_draw_count"] = config.get(
            "new_independent_fire_draw_count", 0
        )
        self.source_policy = config.get("source_policy", "fixed")
        self.problem["joint_source_policy"] = self.source_policy
        self.source_coverage_policy = config.get("source_coverage_policy", "reference_area_bound")
        self.finite_source_certificate = None
        if self.source_coverage_policy == "finite_declared_plans":
            if reference is None:
                raise ValueError("finite source catalog requires a verified reference mesh")
            self.finite_source_certificate = finite_source_catalog_certificate(
                self.plans.values(),
                self.fires,
                inventory_sha256=self.inventory.sha256,
                reference_problem_sha256=reference["problem_sha256"],
            )
            self.problem["finite_declared_source_catalog"] = self.finite_source_certificate
            self.problem["source_coverage_application"] = "finite_declared_plans"
        self.problem_sha256 = content_hash(self.problem)
        self.reference_problem_sha256 = self.problem_sha256
        self.terminal_fire_verification = config.get("terminal_fire_verification", False)
        self.terminal_horizon_cap_s = config.get("terminal_horizon_cap_s")
        if self.terminal_fire_verification:
            self.problem["evaluation_temporal_protocol"] = {
                "schema_version": "heritage-terminal-temporal-protocol-1",
                "reference_problem_sha256": self.reference_problem_sha256,
                "reference_horizon_s": self.horizon_s,
                "terminal_horizon_cap_s": float(self.terminal_horizon_cap_s),
                "stop_at_first_core_ignition": False,
                "stop_at_extinction": True,
                "closure_schema_version": "v3-extinction-time-closure-1",
                "actual_elapsed_time_is_preserved": True,
                "horizon_change_can_change_final_clipped_timestep": True,
                "previous_objective_values_or_optimality_transferred": False,
            }
            self.problem_sha256 = content_hash(self.problem)
        self.integration_mask_sha256 = None

    def validate_plan(self, plan):
        if isinstance(plan, str):
            plan = self.plans[plan]
        if not isinstance(plan, RoofJointPlan) or self.plans.get(plan.plan_id) != plan:
            raise ValueError("joint plan differs from frozen declarations")
        if self.source_coverage_policy == "finite_declared_plans":
            proof = finite_source_catalog_certificate(
                self.plans.values(),
                self.fires,
                inventory_sha256=self.inventory.sha256,
                reference_problem_sha256=self.reference_master["problem_sha256"],
            )
            if proof != self.finite_source_certificate:
                raise ValueError("finite source catalog changed after it was frozen")
        else:
            for coverage in self.source_area_coverages.values():
                coverage.validate_plan(plan.demolished_ids, cleared_area=self.source_area_function)
        return plan

    def engine_for_scenario(self, scenario_id):
        if self._engine_key != scenario_id:
            self._engine = None
            self._engine_key = None
            label, scale = self.scenarios[scenario_id]
            fire = self.fires[label]
            layout = refine_roof_layout(
                self.base,
                self.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=self.max_nodes,
            )
            selected = frozenset(fire["refined_ids"])
            integrated = np.asarray([bool(selected.intersection(ids)) for ids in layout.owners])
            integrated.setflags(write=False)
            self.integration_mask_sha256 = hashlib.sha256(integrated.tobytes()).hexdigest()
            self._engine = RoofJointEvaluationEngine(
                self.bundle,
                self.transport,
                self.inventory,
                source_policy="sampled_retained_sources",
                code_version=self.code_version,
                prepared_layout=layout,
                contact_builder=refined_contact_operator,
                radiation_builder=partial(
                    roof_area_integrated_radiation_graph,
                    integrated_ids=fire["refined_ids"],
                    quadrature_spacing_m=self.quadrature_spacing_m,
                    frozen_integrated_node_mask=integrated,
                ),
                max_radiation_edges=self.max_radiation_edges,
            )
            self._engine_key = scenario_id
        return self._engine

    def actual_source_for(self, plan, scenario_id):
        plan = self.validate_plan(plan)
        label, _ = self.scenarios[scenario_id]
        fire = self.fires[label]
        source = next(
            (
                i
                for i in fire["protocol"]["draws"][0]["priority_ids"]
                if i not in plan.demolished_ids
            ),
            None,
        )
        return source if source in fire["footprints"] else None

    def scenario_for(self, scenario_id, source_id=None):
        engine = self.engine_for_scenario(scenario_id)
        label, _ = self.scenarios[scenario_id]
        if source_id is None:
            source_id = self.fires[label]["protocol"]["draws"][0]["baseline_source_id"]
        if source_id not in self.fires[label]["footprints"]:
            raise ValueError("source is outside the frozen common-layout prefix")
        ignition_layout = replace(
            engine.layout,
            ignition_owner=tuple(
                i if i == source_id else None for i in engine.layout.ignition_owner
            ),
        )
        return EvaluationScenario(scenario_id, footprint_ignitions(ignition_layout), self.horizon_s)

    def evaluate(self, plan, scenario_id):
        started = time.monotonic()
        plan = self.validate_plan(plan)
        label, scale = self.scenarios[scenario_id]
        baseline_source = self.fires[label]["protocol"]["draws"][0]["baseline_source_id"]
        expected_source = self.actual_source_for(plan, scenario_id)
        if expected_source is None:
            return (
                ProtectionCheck(
                    "unknown",
                    {
                        "reason": "no_retained_source_in_frozen_prefix",
                        "source_id": None,
                        "baseline_source_id": baseline_source,
                        "source_policy": self.source_policy,
                        "fire_id": label,
                        "scale_m": scale,
                        "reached_horizon": False,
                        "source_replaced": None,
                        "simulated_until_s": None,
                        "new_CA_checks": 0,
                    },
                ),
                None,
                None,
                None,
            )
        engine = self.engine_for_scenario(scenario_id)
        scenario = self.scenario_for(scenario_id, expected_source)
        prepared = time.monotonic() - started
        terminal = None
        if self.terminal_fire_verification:
            terminal = evaluate_terminal_joint(
                self, plan, scenario_id, horizon_cap_s=self.terminal_horizon_cap_s
            )
            record, layout, result = terminal.record, terminal.layout, terminal.result
        else:
            record, layout, result = engine.simulate(
                scenario, plan, stop_at_first_core_ignition=False
            )
        direct = [event.col for event in layout.node_request.scenario.ignitions]
        budget = {
            name: float(getattr(layout.node_request.parameters, name).ravel()[direct].sum())
            for name in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        }
        if record.metrics["actual_source_ids"] != [expected_source]:
            raise ValueError("joint simulation differs from the frozen retained-source realization")
        status = record.metrics["protection_status"] if terminal is None else terminal.check.status
        check = ProtectionCheck(
            "unknown" if status == "invalid" else status,
            {
                "source_id": expected_source,
                "baseline_source_id": baseline_source,
                "fire_id": label,
                "source_policy": self.source_policy,
                "source_replaced": expected_source != baseline_source,
                "source_budget": budget,
                "source_footprint_sha256": self.fires[label]["footprints"][expected_source].sha256,
                "common_layout_sha256": engine.layout_sha256,
                "frozen_integrated_node_mask_sha256": self.integration_mask_sha256,
                "scale_m": scale,
                "core_outcomes": record.metrics["core_outcomes"],
                "invalid_reasons": record.diagnostics["invalid_reasons"],
                "reached_horizon": record.diagnostics["reached_requested_horizon"],
                "simulated_until_s": record.diagnostics["simulated_until_s"],
                "prepared_s": prepared,
                "solve_s": time.monotonic() - started - prepared,
            },
        )
        if terminal is not None:
            check.evidence["terminal_verification"] = terminal.summary
            check.evidence["evaluation_temporal_protocol"] = self.problem[
                "evaluation_temporal_protocol"
            ]
        if self.finite_source_certificate is not None:
            check.evidence["finite_source_catalog_certificate_sha256"] = (
                self.finite_source_certificate["certificate_sha256"]
            )
            check.evidence["reference_roof_cap_is_an_admissibility_constraint"] = False
        return check, record, layout, result


def load_joint_fires(config_path, config, interventions, inventory):
    if config.get("source_policy", "fixed") == "fixed":
        return load_fires(config_path, config, interventions)
    fires, references = [], {}
    for item in config["source_fires"]:
        path = (config_path.parent / item["protocol_path"]).resolve()
        protocol = read_json(path)
        unhashed = {k: v for k, v in protocol.items() if k != "protocol_sha256"}
        if content_hash(unhashed) != protocol.get("protocol_sha256"):
            raise ValueError("retained-source protocol fingerprint mismatch")
        if protocol.get("inventory_sha256") != inventory.sha256:
            raise ValueError("retained-source protocol inventory mismatch")
        draws = [d for d in protocol["draws"] if d["draw_id"] == item["draw_id"]]
        if len(draws) != 1:
            raise ValueError("exactly one frozen draw required per retained-source family")
        draw = draws[0]
        priority = draw.get("priority_ids", [])
        if (
            not priority
            or len(priority) != len(set(priority))
            or not set(priority) <= inventory.by_id.keys()
            or draw.get("baseline_source_id") != priority[0]
            or draw.get("stratum") != "all_retained_sources"
            or set(priority) != set(protocol.get("strata", {}).get("all_retained_sources", []))
        ):
            raise ValueError(
                "retained-source family requires the complete all_retained_sources priority"
            )
        protocol = {**protocol, "draws": draws}
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        fires.append({"fire_id": item["fire_id"], "protocol": protocol})
        references[str(path)] = file_sha256(path)
    return fires, references


def make_oracle(config_path, config, code_hash):
    interventions = validate_config(config)
    reference, reference_files = read_master_reference(config_path, config)
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study)
    if bundle.request.grid.cell_size_m != 5.0:
        raise ValueError("joint comparison requires the frozen original 5 m parent grid")
    if not set(interventions) <= set(inventory.candidate_ids):
        raise ValueError("ineligible building intervention")
    if reference is not None and tuple(sorted(config["candidate_ids"])) != tuple(
        sorted(inventory.candidate_ids)
    ):
        raise ValueError("reference joint candidates differ from the complete current inventory")
    bundle = replace(
        bundle,
        request=replace(
            bundle.request,
            scenario=replace(bundle.request.scenario, max_dt_s=float(config["max_dt_s"])),
        ),
    )
    fires, references = load_joint_fires(config_path, config, interventions, inventory)
    references.update(reference_files)
    references[str(study)] = file_sha256(study)
    study_config = read_json(study)
    case_dir = (study.parent / study_config["case_dir"]).resolve()
    # The numerical problem fingerprint is authoritative, while these hashes
    # also bind the actual input bytes used to reconstruct it after a restart.
    for path in case_dir.iterdir():
        if path.is_file() and path.suffix in {".json", ".npy", ".npz"}:
            references[str(path.resolve())] = file_sha256(path)
    structures = study_config.get("structures_path")
    structures_path = (
        study.parent / structures
        if structures
        else Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json"
    ).resolve()
    references[str(structures_path)] = file_sha256(structures_path)
    for key in ("labelme_annotations", "image_reference"):
        if study_config.get(key):
            path = (study.parent / study_config[key]).resolve()
            references[str(path)] = file_sha256(path)
    for path_key, hash_key in (
        ("roof_seed_receipt_path", "roof_seed_receipt_sha256"),
        ("roof_seed_search_snapshot_path", "roof_seed_search_sha256"),
        ("vegetation_proposals_path", "vegetation_proposals_sha256"),
    ):
        if config.get(path_key):
            path = (config_path.parent / config[path_key]).resolve()
            if file_sha256(path) != config.get(hash_key):
                raise ValueError(f"joint seed/proposal receipt changed: {path_key}")
            references[str(path)] = config[hash_key]
    for declared, digest in config.get("additional_frozen_inputs_sha256", {}).items():
        path = (config_path.parent / declared).resolve()
        if file_sha256(path) != digest:
            raise ValueError("additional frozen catalog/provenance input changed: " + str(path))
        references[str(path)] = digest
    for path, digest in reference_files.items():
        if references.get(path, digest) != digest or file_sha256(path) != digest:
            raise ValueError("reference master input bytes changed while preparing joint inputs")
        references[path] = digest
    oracle = JointComparisonOracle(
        bundle,
        transport,
        inventory,
        config=config,
        fires=fires,
        code_version=code_hash,
        reference=reference,
    )
    for plan in oracle.plans.values():
        oracle.validate_plan(plan)
    return oracle, references


def source_paths():
    """Freeze imported project modules and explicit script dependencies, once."""
    paths = set()
    for name, module in tuple(sys.modules.items()):
        if name == "pyfireca" or name.startswith("pyfireca."):
            path = getattr(module, "__file__", None)
            if path and Path(path).suffix == ".py":
                candidate = Path(path).resolve()
                if candidate.is_relative_to(ROOT):
                    paths.add(candidate)
    paths.update(
        ROOT / "scripts" / name
        for name in (
            "run_heritage_joint_comparison.py",
            "run_heritage_area_study.py",
            "run_heritage_fixed_comparison.py",
            "preflight_heritage_exact_expansion.py",
            "run_heritage_roof_study.py",
        )
    )
    return sorted(paths)


def prepare_geometry_preflight(oracle):
    rows = []
    for label, fire in oracle.fires.items():
        for scale in oracle.scales_m:
            layout = refine_roof_layout(
                oracle.base,
                oracle.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=oracle.max_nodes,
            )
            # Eligibility is geometry-only; no graph construction or CA is needed.
            checker = object.__new__(RoofJointEvaluationEngine)
            checker.bundle, checker.layout = oracle.bundle, layout
            scenario = EvaluationScenario(
                f"{label}-h{scale:g}", footprint_ignitions(layout), oracle.horizon_s
            )
            for plan in oracle.plans.values():
                checker._vegetation_nodes(scenario, plan)
            bounds = edge_bounds(layout, oracle.transport, fire["refined_ids"])
            row = {"fire_id": label, "scale_m": scale, "nodes": len(layout.owners), **bounds}
            row["passed"] = (
                row["nodes"] <= oracle.max_nodes
                and max(row["raw_point_edges"], row["integrated_edges_upper_bound"])
                <= oracle.max_radiation_edges
            )
            rows.append(row)
            if not row["passed"]:
                raise ValueError("joint geometry capacity preflight failed before any CA")
    return rows


def prepare(config_path, output):
    config = read_json(config_path)
    validate_config(config)
    output.mkdir(parents=True, exist_ok=False)
    paths = tuple(source_paths())
    hashes = {str(p.relative_to(ROOT)): file_sha256(p) for p in paths}
    for path in paths:
        target = output / "source-snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if file_sha256(target) != hashes[str(path.relative_to(ROOT))]:
            raise ValueError("source changed while freezing joint comparison")
    config_hash = file_sha256(config_path)
    oracle, references = make_oracle(config_path, config, content_hash(hashes))
    rows = prepare_geometry_preflight(oracle)
    if any(file_sha256(p) != hashes[str(p.relative_to(ROOT))] for p in paths):
        raise ValueError("frozen source changed during preparation")
    if file_sha256(config_path) != config_hash or any(
        file_sha256(p) != h for p, h in references.items()
    ):
        raise ValueError("input changed during preparation")
    write_json(
        output / "experiment.json",
        {
            "schema_version": SCHEMA,
            "config": config,
            "config_sha256": config_hash,
            "source_sha256": hashes,
            "references_sha256": references,
            "problem_sha256": oracle.problem_sha256,
            "problem": oracle.problem,
            "source_counts": source_counts(config),
            "source_realizations": [
                {
                    "plan_id": plan.plan_id,
                    "scenario_id": scenario,
                    "actual_source_id": oracle.actual_source_for(plan, scenario),
                    "baseline_source_id": oracle.fires[oracle.scenarios[scenario][0]]["protocol"][
                        "draws"
                    ][0]["baseline_source_id"],
                }
                for plan, scenario in expected_checks(config)
            ],
            "global_optimality_proven": False,
        },
    )
    write_json(output / "preflight.json", {"rows": rows, "passed": True})
    atomic_json(
        output / "execution-budget.json", {"elapsed_s": 0.0, "started_checks": 0, "active": None}
    )
    return summarize(output)


def source_counts(config):
    fires = len(config["source_fires"])
    new = config.get("new_independent_fire_draw_count", 0)
    return {
        "source_policy": config.get("source_policy", "fixed"),
        "source_family_count": fires,
        "fixed_source_fire_count": fires if config.get("source_policy", "fixed") == "fixed" else 0,
        "new_independent_fire_draw_count": new,
        "historical_source_family_replays": fires - new,
        "historical_fixed_source_replays": fires - new
        if config.get("source_policy", "fixed") == "fixed"
        else 0,
        "independent_draws_are_not_plan_or_scale_checks": True,
    }


def expected_checks(config):
    for fire in config["source_fires"]:
        for scale in config["roof_scales_m"]:
            scenario = f"{fire['fire_id']}-h{scale:g}"
            for plan in config["plans"]:
                yield plan_from_mapping(plan), scenario


def execution_checks(config):
    """Select declared calls without promoting omitted cases to completed ones."""
    expected = tuple(expected_checks(config))
    order = config.get("execution_case_order", "declared")
    if not isinstance(order, str) or order not in {"declared", "plans_first"}:
        raise ValueError("execution_case_order must be declared or plans_first")
    if not isinstance(config.get("stop_plan_after_protection_failure", False), bool):
        raise ValueError("stop_plan_after_protection_failure must be an explicit boolean")
    ordered = expected
    priority = config.get("execution_priority_plan_ids")
    if priority is not None and (
        order != "plans_first"
        or not isinstance(priority, list)
        or any(not isinstance(i, str) or not i for i in priority)
        or len(priority) != len(config["plans"])
        or len(set(priority)) != len(priority)
        or set(priority) != {row["plan_id"] for row in config["plans"]}
    ):
        raise ValueError("execution_priority_plan_ids requires an exact plans_first permutation")
    if order == "plans_first":
        ordered = tuple(
            (plan, scenario)
            for identifier in priority or [row["plan_id"] for row in config["plans"]]
            for plan, scenario in expected
            if plan.plan_id == identifier
        )
    selection = config.get("execution_case_selection")
    if selection is None:
        yield from ordered
        return
    if not isinstance(selection, list) or not selection:
        raise ValueError("execution_case_selection requires a nonempty explicit case list")
    requested = []
    for row in selection:
        if (
            not isinstance(row, dict)
            or set(row) != {"plan_id", "scenario_id"}
            or not all(isinstance(v, str) and v for v in row.values())
        ):
            raise ValueError("execution case requires exactly plan_id and scenario_id")
        requested.append((row["plan_id"], row["scenario_id"]))
    available = {(plan.plan_id, scenario) for plan, scenario in expected}
    if len(set(requested)) != len(requested) or not set(requested) <= available:
        raise ValueError("execution cases must be unique members of the full declared question")
    selected = set(requested)
    yield from (
        (plan, scenario) for plan, scenario in ordered if (plan.plan_id, scenario) in selected
    )


def native_protection_failure_witness(output, problem_hash, plan, scenario, *, terminal_required):
    """A verified strict native core failure can refute this plan's conjunction.

    Unknowns, including edge-invalid extinction records, never justify skipping
    another source. The actual retained core ignition is checked in saved state.
    """
    folder = result_folder(output, plan, scenario)
    if not (folder / "complete.json").is_file():
        return None
    check = read_complete(
        folder,
        problem_hash,
        plan_id=plan.plan_id,
        scenario_id=scenario,
        demolished_ids=plan.demolished_ids,
        cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
        terminal_required=terminal_required,
    )
    evidence = check["evidence"]
    if check["status"] != "failed" or evidence.get("invalid_reasons") != []:
        return None
    record = read_json(folder / "evaluation.json")
    metrics, diagnostics = record["metrics"], record["diagnostics"]
    if (
        diagnostics.get("invalid_reasons") != []
        or diagnostics.get("source_verified") is not True
        or diagnostics.get("parent_case_unchanged") is not True
        or metrics.get("protection_status") != "failed"
        or metrics.get("actual_source_ids") != [evidence.get("source_id")]
        or not evidence.get("source_id")
    ):
        raise ValueError("native failed protection record lacks valid source/physical evidence")
    core_rows = metrics["core_outcomes"]
    core_ids = sorted(row["building_id"] for row in core_rows)
    if not core_ids or len(set(core_ids)) != len(core_ids):
        raise ValueError("native failed record requires unique protected core identities")
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        offsets, names = saved["owner_offsets"], saved["owner_ids"]
        owners = tuple(tuple(names[offsets[n] : offsets[n + 1]]) for n in range(len(offsets) - 1))
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        ignition = saved["ignition_time_s"].ravel()
        actual_time = float(saved["time_s"])
    if len(owners) != len(ignition):
        raise ValueError("native core failure state and physical layout differ")
    actual = sorted(
        identifier
        for identifier in core_ids
        if any(
            identifier in group and np.isfinite(ignition[n]) and 0 <= ignition[n] <= actual_time
            for n, group in enumerate(owners)
        )
    )
    if (
        not actual
        or sorted(metrics.get("ignited_core_ids", ())) != actual
        or sorted(row["building_id"] for row in core_rows if row["ignited"]) != actual
        or sorted(row["building_id"] for row in evidence.get("core_outcomes", ()) if row["ignited"])
        != actual
    ):
        raise ValueError("native failed protection claim differs from actual core ignition state")
    terminal_complete = None
    if terminal_required:
        terminal_complete, _, _, _ = terminal_observation_status(folder, check)
        terminal = evidence["terminal_verification"]
        if (
            not terminal_complete
            or terminal["terminal_status"] != "failed"
            or terminal["protection_feasible"] is not False
            or terminal["ignited_core_ids"] != actual
        ):
            raise ValueError("native terminal failure is not a complete strict counterexample")
    return {
        "problem_sha256": problem_hash,
        "plan_id": plan.plan_id,
        "scenario_id": scenario,
        "manifest": str((folder / "complete.json").relative_to(output)),
        "manifest_sha256": file_sha256(folder / "complete.json"),
        "check_sha256": file_sha256(folder / "check.json"),
        "status": "failed",
        "native_invalid_reasons": [],
        "source_id": evidence["source_id"],
        "source_verified": True,
        "ignited_core_ids": actual,
        "terminal_required": terminal_required,
        "strict_terminal_complete": terminal_complete,
    }


def read_execution_skips(output, experiment):
    """Validate the separate execution ledger; it is never a native CA result."""
    path = output / "execution-skips.json"
    if not path.exists():
        return None
    config = experiment["config"]
    receipt = read_json(path)
    if (
        config.get("stop_plan_after_protection_failure", False) is not True
        or receipt.get("schema_version") != "heritage-joint-execution-skips-1"
        or receipt.get("problem_sha256") != experiment["problem_sha256"]
        or receipt.get("execution_case_order") != config.get("execution_case_order", "declared")
        or receipt.get("execution_priority_plan_ids") != config.get("execution_priority_plan_ids")
        or receipt.get("receipt_sha256")
        != content_hash({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    ):
        raise ValueError("execution skip receipt differs from frozen problem or policy")
    selected = {(plan.plan_id, scenario): plan for plan, scenario in execution_checks(config)}
    declared = {(plan.plan_id, scenario): plan for plan, scenario in expected_checks(config)}
    seen, verified = set(), {}
    for row in receipt["skipped_cases"]:
        key = row["plan_id"], row["scenario_id"]
        witness = row["failure_witness"]
        witness_key = witness["plan_id"], witness["scenario_id"]
        if (
            key not in selected
            or key in seen
            or witness_key not in declared
            or key == witness_key
            or witness["plan_id"] != row["plan_id"]
            or row.get("reason") != "verified_native_protection_counterexample"
        ):
            raise ValueError("execution skip is not a declared same-plan counterexample decision")
        seen.add(key)
        if witness_key not in verified:
            verified[witness_key] = native_protection_failure_witness(
                output,
                experiment["problem_sha256"],
                declared[witness_key],
                witness_key[1],
                terminal_required=config.get("terminal_fire_verification", False),
            )
        if verified[witness_key] is None or verified[witness_key] != witness:
            raise ValueError("execution skip counterexample is missing or changed")
    return receipt


def persist_execution_skip(output, experiment, plan, scenario, witness):
    receipt = read_execution_skips(output, experiment)
    if receipt is None:
        receipt = {
            "schema_version": "heritage-joint-execution-skips-1",
            "problem_sha256": experiment["problem_sha256"],
            "execution_case_order": experiment["config"].get("execution_case_order", "declared"),
            "execution_priority_plan_ids": experiment["config"].get("execution_priority_plan_ids"),
            "skipped_cases": [],
            "native_case_results_created": 0,
            "full_scenario_completeness_requirement_changed": False,
        }
    key = plan.plan_id, scenario
    if key not in {(r["plan_id"], r["scenario_id"]) for r in receipt["skipped_cases"]}:
        receipt["skipped_cases"].append(
            {
                "plan_id": plan.plan_id,
                "scenario_id": scenario,
                "reason": "verified_native_protection_counterexample",
                "failure_witness": witness,
            }
        )
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = content_hash(receipt)
    atomic_json(output / "execution-skips.json", receipt)


def result_folder(output, plan, scenario):
    return output / "evaluations" / content_hash([plan.plan_id, scenario])


def terminal_observation_status(folder, check):
    """Verify terminal metadata and bind closure to actual saved native arrays."""
    terminal = check.get("evidence", {}).get("terminal_verification")
    if not isinstance(terminal, dict):
        raise ValueError("terminal comparison cache requires its terminal verification")
    curves = read_json(folder / "curves.json")
    record = read_json(folder / "evaluation.json")
    loss = read_json(folder / "fire-loss.json")
    manifest = read_json(folder / "complete.json")
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        values = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
    values["time_s"], values["step_index"] = float(values["time_s"]), int(values["step_index"])
    state = V3State(**values)
    protocol, diagnostics = terminal["model_protocol"], record["diagnostics"]
    cap = protocol["terminal_horizon_cap_s"]
    reached = state.time_s >= cap - 1e-8
    if (
        any(
            terminal.get(key) != manifest.get(key)
            for key in ("plan_id", "scenario_id", "demolished_ids", "cleared_physical_cell_ids")
        )
        or terminal["terminal_status"] != check["status"]
        or protocol["source_evidence"]["comparison_problem_sha256"] != manifest["problem_sha256"]
        or record["horizon_s"] != cap
        or diagnostics["physical_request_sha256"] != protocol["physical_request_sha256"]
        or diagnostics.get("stop_at_extinction") is not True
        or diagnostics["stop_at_first_core_ignition"] is not False
        or diagnostics["simulated_until_s"] != state.time_s
        or diagnostics["reached_requested_horizon"] != reached
        or check["evidence"]["reached_horizon"] != reached
        or check["evidence"]["simulated_until_s"] != state.time_s
        or terminal["actual_stop_time_s"] != state.time_s
        or curves["final_time_s"] != state.time_s
        or loss["saved_final_time_s"] != state.time_s
        or diagnostics.get("temporal_closure_certificate")
        != terminal["temporal_closure_certificate"]
        or sorted(record["metrics"]["ignited_core_ids"]) != terminal["ignited_core_ids"]
    ):
        raise ValueError("terminal saved request, plan, source, clock or certificate differs")
    valid = terminal_observation_is_complete(
        terminal, state, diagnostics["maximum_valid_surface_temperature_by_node_K"]
    )
    counts, metrics = loss["physical_remaining_roof_annotation_counts"], terminal["metrics"]
    if (
        metrics["C_construction_action_area_m2"]
        != record["intervention"]["cleared_action_geometry_union_area_m2"]
        or metrics["N_ignited_annotation_attribution_count"]
        != counts["ignited_annotation_attribution_count"]
        or metrics["N_burned_out_annotation_attribution_count"]
        != counts["burned_out_annotation_attribution_count"]
        or metrics["A_ignited_physical_covered_area_m2"]
        != loss["physical"]["all"]["cumulative_ignited_covered_area_m2"]
        or metrics["burned_out_physical_covered_area_m2"]
        != loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
        or curves["terminal_endpoint"]["metrics"] != metrics
        or curves["terminal_endpoint"]["actual_stop_time_s"] != state.time_s
        or curves["terminal_endpoint"]["terminal_complete"] != valid
    ):
        raise ValueError("terminal audited C/N/A loss or endpoint differs from saved artifacts")
    for row in curves["checkpoints"]:
        future = row["time_s"] > state.time_s
        if (
            row.get("actual_time_reached") != (not future)
            or row.get("event_metrics_closed_by_extinction_certificate") != (future and valid)
            or row.get("future_temperature_fields_not_simulated") != future
            or row.get("event_metrics_valid")
            != bool(row["valid_observation"] or (future and valid))
        ):
            raise ValueError(
                "terminal checkpoint confuses actual observation and extinction closure"
            )
    return valid, reached, curves, record


def read_complete(
    folder,
    problem_hash,
    *,
    plan_id=None,
    scenario_id=None,
    demolished_ids=None,
    cleared_physical_cell_ids=None,
    terminal_required=False,
):
    check = read_area_complete(
        folder,
        problem_hash,
        plan_id=plan_id,
        demolished_ids=demolished_ids,
        scenario_id=scenario_id,
    )
    manifest = read_json(folder / "complete.json")
    if not manifest["files_sha256"].keys() >= REQUIRED_ARTIFACTS:
        raise ValueError("joint cache lacks required artifacts")
    cells = manifest.get("cleared_physical_cell_ids")
    if (
        cells is None
        or cells != check.get("cleared_physical_cell_ids")
        or (cleared_physical_cell_ids is not None and cells != list(cleared_physical_cell_ids))
    ):
        raise ValueError("cached joint cell identity differs")
    if terminal_required or "terminal_verification" in check.get("evidence", {}):
        terminal_observation_status(folder, check)
    return check


def persist_unknown_source(output, oracle, plan, scenario, check):
    folder = result_folder(output, plan, scenario)
    pending = folder.with_name(folder.name + ".pending")
    pending.mkdir(parents=True, exist_ok=False)
    payload = {
        "plan_id": plan.plan_id,
        "scenario_id": scenario,
        "demolished_ids": list(plan.demolished_ids),
        "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
        "status": check.status,
        "evidence": check.evidence,
    }
    write_json(pending / "unknown-source.json", payload)
    write_json(
        pending / "unknown-manifest.json",
        {
            "problem_sha256": oracle.problem_sha256,
            "files_sha256": {"unknown-source.json": file_sha256(pending / "unknown-source.json")},
        },
    )
    pending.rename(folder)


def read_unknown_source(folder, problem_hash, plan, scenario):
    manifest = read_json(folder / "unknown-manifest.json")
    if manifest.get("problem_sha256") != problem_hash or manifest.get("files_sha256") != {
        "unknown-source.json": file_sha256(folder / "unknown-source.json")
    }:
        raise ValueError("unknown-source artifact identity/hash mismatch")
    payload = read_json(folder / "unknown-source.json")
    if (
        any(
            payload.get(k) != v
            for k, v in (
                ("plan_id", plan.plan_id),
                ("scenario_id", scenario),
                ("demolished_ids", list(plan.demolished_ids)),
                ("cleared_physical_cell_ids", list(plan.cleared_physical_cell_ids)),
                ("status", "unknown"),
            )
        )
        or payload.get("evidence", {}).get("reason") != "no_retained_source_in_frozen_prefix"
    ):
        raise ValueError("unknown-source plan/scenario identity mismatch")
    if (
        payload["evidence"].get("source_id") is not None
        or payload["evidence"].get("new_CA_checks") != 0
    ):
        raise ValueError("empty source cannot claim a physical fire or protection success")
    return payload


def annotation_counts(loss, selected):
    rows = [row for row in loss["per_building"] if row["building_id"] in selected]
    retained = [row for row in rows if not row["demolished"]]
    lower = sum(row["cumulative_ignited_area_m2"]["lower"] > 0 for row in retained)
    upper = sum(
        row["cumulative_ignited_area_m2"]["upper"] is None
        or row["cumulative_ignited_area_m2"]["upper"] > 0
        for row in retained
    )
    removed = sum(row["demolished"] for row in rows)
    return {
        "inventory_annotation_count": len(rows),
        "retained_annotation_count": len(retained),
        "ignited_annotation_attribution_count": {"lower": lower, "upper": upper},
        "clearing_plus_ignited_annotation_count": {
            "lower": lower + removed,
            "upper": upper + removed,
        },
        "real_destroyed_building_count": None,
    }


def verify_pairing(checks):
    references = {}
    layouts = {}
    source_budgets = {}
    for row in checks:
        evidence = row["evidence"]
        if evidence.get("source_id") is None:
            continue
        layout_identity = content_hash(
            {
                key: evidence[key]
                for key in ("common_layout_sha256", "frozen_integrated_node_mask_sha256")
            }
        )
        previous_layout = layouts.setdefault(row["scenario_id"], layout_identity)
        if previous_layout != layout_identity:
            raise ValueError("joint arms changed common layout or frozen integration membership")
        fields = {
            key: evidence[key]
            for key in (
                "source_id",
                "source_footprint_sha256",
                "source_budget",
                "common_layout_sha256",
                "frozen_integrated_node_mask_sha256",
            )
        }
        identity = content_hash(fields)
        group = (row["scenario_id"], evidence["source_id"])
        previous = references.setdefault(group, identity)
        if previous != identity:
            raise ValueError(
                "joint arms changed source budget, layout or frozen integration membership"
            )
        source_key = (
            evidence.get("fire_id", row["scenario_id"].rsplit("-h", 1)[0]),
            evidence["source_id"],
            evidence["source_footprint_sha256"],
        )
        budget = np.asarray(
            [
                evidence["source_budget"][key]
                for key in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
            ],
            dtype=float,
        )
        previous_budget = source_budgets.setdefault(source_key, budget)
        if not np.allclose(previous_budget, budget, rtol=1e-9, atol=1e-7):
            raise ValueError("same source footprint changed its physical budget across roof scales")
    return True


def source_attribution(checks):
    groups = {}
    for row in checks:
        evidence = row["evidence"]
        if evidence.get("source_id") is None:
            continue
        key = (row["scenario_id"], evidence["source_id"])
        group = groups.setdefault(
            key,
            {
                "scenario_id": row["scenario_id"],
                "actual_source_id": evidence["source_id"],
                "baseline_source_id": evidence.get("baseline_source_id", evidence["source_id"]),
                "source_replaced": evidence["source_replaced"],
                "plan_ids": [],
            },
        )
        group["plan_ids"].append(row["plan_id"])
    return {
        "baseline_source_checks": sum(
            row["evidence"].get("source_replaced") is False for row in checks
        ),
        "replacement_source_checks": sum(
            row["evidence"].get("source_replaced") is True for row in checks
        ),
        "unknown_source_checks": sum(row["evidence"].get("source_id") is None for row in checks),
        "same_actual_source_comparison_groups": list(groups.values()),
        "interpretation": (
            "same-source isolation comparisons are confined to each actual-source group;"
            "source-replacement outcomes describe the conditional retained-source scenario"
        ),
    }


def summarize(output):
    experiment = read_json(output / "experiment.json")
    config, checks = experiment["config"], []
    terminal_mode = config.get("terminal_fire_verification", False)
    validity_key = (
        "valid_terminal_fire_observation" if terminal_mode else "valid_full_horizon_observation"
    )
    expected = list(expected_checks(config))
    for plan, scenario in expected:
        folder = result_folder(output, plan, scenario)
        if not (folder / "complete.json").is_file():
            if (folder / "unknown-manifest.json").is_file():
                unknown = read_unknown_source(folder, experiment["problem_sha256"], plan, scenario)
                checks.append(
                    {
                        **unknown,
                        "unknown_source": True,
                        "valid_full_horizon_observation": False,
                        **({"valid_terminal_fire_observation": False} if terminal_mode else {}),
                        "reached_horizon": False,
                        "folder": str(folder.relative_to(output)),
                    }
                )
            continue
        check = read_complete(
            folder,
            experiment["problem_sha256"],
            plan_id=plan.plan_id,
            scenario_id=scenario,
            demolished_ids=plan.demolished_ids,
            cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
            terminal_required=terminal_mode,
        )
        if terminal_mode:
            valid, reached, curves, record = terminal_observation_status(folder, check)
            if [row["time_s"] for row in curves["checkpoints"]] != config.get(
                "checkpoints_s", [config["horizon_s"]]
            ):
                raise ValueError("terminal reference checkpoints differ from frozen configuration")
            reference_valid = bool(curves["checkpoints"]) and all(
                row["event_metrics_valid"] for row in curves["checkpoints"]
            )
        else:
            valid, reached, curves, record = observation_status(folder, check, config["horizon_s"])
            reference_valid = valid
        loss = read_json(folder / "fire-loss.json")
        ledger = record["intervention"]
        checks.append(
            {
                **check,
                "plan_id": plan.plan_id,
                "valid_full_horizon_observation": reference_valid,
                **({"valid_terminal_fire_observation": valid} if terminal_mode else {}),
                "unknown_source": False,
                "reached_horizon": reached,
                "folder": str(folder.relative_to(output)),
                "construction_action_area_m2": ledger["cleared_action_geometry_union_area_m2"],
                "covered_fuel_cleared_area_m2": ledger["cleared_total_covered_area_m2"],
                "physical": loss["physical"],
                "physical_remaining_roof_annotation_counts": loss.get(
                    "physical_remaining_roof_annotation_counts"
                ),
                "fuel": loss["fuel"],
                "ordinary_annotation_counts": loss["ordinary_annotation_counts"],
                "core_annotation_counts": loss["core_annotation_counts"],
                "secondary_ordinary_annotation_counts": loss[
                    "secondary_ordinary_annotation_counts"
                ],
                "endpoint": curves["terminal_endpoint"]
                if terminal_mode
                else curves["checkpoints"][-1]
                if curves.get("checkpoints")
                else None,
                **({"reference_endpoint": curves["checkpoints"][-1]} if terminal_mode else {}),
            }
        )
    verify_pairing(checks)
    report = {
        "schema_version": "heritage-joint-comparison-summary-1",
        "planned_checks": len(expected),
        "completed_checks": sum(not row.get("unknown_source", False) for row in checks),
        "recorded_checks": len(checks),
        "unknown_source_checks": sum(row.get("unknown_source", False) for row in checks),
        "started_CA_checks": read_json(output / "execution-budget.json").get("started_checks", 0)
        if (output / "execution-budget.json").exists()
        else 0,
        "valid_completed_checks": sum(row[validity_key] for row in checks),
        "complete": len(checks) == len(expected) and all(row[validity_key] for row in checks),
        "all_evaluations_reached_horizon": len(checks) == len(expected)
        and all(row["reached_horizon"] for row in checks),
        "source_counts": source_counts(config),
        "source_attribution": source_attribution(checks),
        "source_policy": config.get("source_policy", "fixed"),
        "global_optimality_proven": False,
        "interpretation": (
            "finite joint plan comparison;annotation exposure not whole-building destruction"
        ),
        "checks": checks,
    }
    if terminal_mode:
        report.update(
            terminal_fire_verification=True,
            evaluation_temporal_protocol=experiment["problem"]["evaluation_temporal_protocol"],
            valid_reference_horizon_checks=sum(
                row["valid_full_horizon_observation"] for row in checks
            ),
            all_evaluations_reached_reference_horizon=len(checks) == len(expected)
            and all(
                row["evidence"].get("simulated_until_s", -1) is not None
                and row["evidence"].get("simulated_until_s", -1) >= config["horizon_s"]
                for row in checks
            ),
            terminal_complete_checks=sum(row[validity_key] for row in checks),
            interpretation=(
                "finite declared joint plans;final fire losses require strict extinction closure;"
                "reference checkpoints may be event metrics inferred through closure;"
                "horizon extension does not transfer earlier objective values or optimality"
            ),
        )
    if config.get("stop_plan_after_protection_failure", False):
        execution_receipt = read_execution_skips(output, experiment)
        skips = [] if execution_receipt is None else execution_receipt["skipped_cases"]
        failed_plans = {row["plan_id"] for row in skips}
        declared_plans = {plan.plan_id: plan for plan, _ in expected}
        for row in checks:
            if (
                row["plan_id"] not in failed_plans
                and row["status"] == "failed"
                and row["evidence"].get("invalid_reasons") == []
                and native_protection_failure_witness(
                    output,
                    experiment["problem_sha256"],
                    declared_plans[row["plan_id"]],
                    row["scenario_id"],
                    terminal_required=terminal_mode,
                )
                is not None
            ):
                failed_plans.add(row["plan_id"])
        report["execution_control"] = {
            "execution_case_order": config.get("execution_case_order", "declared"),
            "execution_priority_plan_ids": config.get("execution_priority_plan_ids"),
            "stop_plan_after_protection_failure": True,
            "skipped_case_count": len(skips),
            "skipped_cases_are_native_results": False,
            "remaining_fire_losses_are_complete": False if skips else None,
            "skip_receipt_sha256": None
            if execution_receipt is None
            else execution_receipt["receipt_sha256"],
            "plan_protection_query_status": {
                plan["plan_id"]: "failed"
                if plan["plan_id"] in failed_plans
                else "passed"
                if all(
                    any(
                        row["plan_id"] == plan["plan_id"]
                        and row["scenario_id"] == scenario
                        and row[validity_key]
                        and row["status"] == "passed"
                        for row in checks
                    )
                    for scenario in {s for _, s in expected}
                )
                else "unknown"
                for plan in config["plans"]
            },
        }
    atomic_json(output / "summary.json", report)
    return report


def persist(output, config, oracle, plan, scenario, check, record, layout, result):
    folder = result_folder(output, plan, scenario)
    pending = folder.with_name(folder.name + ".pending")
    pending.mkdir(parents=True, exist_ok=False)
    loss = fire_loss_from_result(
        layout,
        result,
        oracle.inventory,
        demolished_ids=plan.demolished_ids,
        source_ids=record.metrics["actual_source_ids"],
        original_node_owners=oracle._engine.layout.owners,
    )
    cores = {target.building_id for target in oracle.inventory.targets}
    ordinary = set(oracle.inventory.by_id) - cores
    loss["ordinary_annotation_counts"] = annotation_counts(loss, ordinary)
    loss["core_annotation_counts"] = annotation_counts(loss, cores)
    loss["secondary_ordinary_annotation_counts"] = annotation_counts(
        loss, ordinary - set(record.metrics["actual_source_ids"])
    )
    write_json(pending / "fire-loss.json", loss)
    write_json(pending / "evaluation.json", record.to_mapping())
    write_json(
        pending / "check.json",
        {
            "scenario_id": scenario,
            "demolished_ids": list(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "status": check.status,
            "evidence": check.evidence,
        },
    )
    curves = result_curves(
        record, layout, result, config.get("checkpoints_s", [config["horizon_s"]])
    )
    if config.get("terminal_fire_verification", False):
        curves = close_terminal_event_curves(curves, check.evidence["terminal_verification"])
    write_json(pending / "curves.json", curves)
    write_compact_arrays(pending, layout, result)
    base, post = layout.node_inputs.parameters, layout.node_request.parameters
    np.savez_compressed(
        pending / "loss-basis.npz",
        baseline_covered_area_m2=base.covered_area_m2,
        post_intervention_covered_area_m2=post.covered_area_m2,
        baseline_pyrolysable_fuel_kg=base.pyrolysable_dry_fuel_mass_kg,
        initial_fuel_kg=result.initial_fuel_kg,
        initial_total_dry_fuel_kg=result.initial_total_dry_fuel_kg,
        landcover_index=layout.node_request.landcover_index,
    )
    write_json(
        pending / "initial-post-budget.json",
        {
            "initial_post_pyrolysable_fuel_kg": float(result.initial_fuel_kg.sum()),
            "initial_post_total_dry_fuel_kg": float(result.initial_total_dry_fuel_kg.sum()),
            "initial_post_available_energy_J": float(post.available_energy_J.sum()),
            "baseline_available_energy_J": float(base.available_energy_J.sum()),
            "source_budget": check.evidence["source_budget"],
            "construction_action_area_m2": record.intervention[
                "cleared_action_geometry_union_area_m2"
            ],
            "covered_fuel_cleared_area_m2": record.intervention["cleared_total_covered_area_m2"],
        },
    )
    files = {path.name: file_sha256(path) for path in pending.iterdir() if path.is_file()}
    write_json(
        pending / "complete.json",
        {
            "problem_sha256": oracle.problem_sha256,
            "plan_id": plan.plan_id,
            "scenario_id": scenario,
            "demolished_ids": list(plan.demolished_ids),
            "cleared_physical_cell_ids": list(plan.cleared_physical_cell_ids),
            "files_sha256": files,
        },
    )
    pending.rename(folder)


def resume(config_path, output):
    snapshot = output / "source-snapshot/scripts" / Path(__file__).name
    if Path(__file__).resolve() != snapshot.resolve():
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(snapshot),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--resume",
            ],
            {**os.environ, "PYTHONPATH": str(output / "source-snapshot/src")},
        )
    experiment = read_json(output / "experiment.json")
    config = read_json(config_path)
    validate_config(config)
    if config != experiment["config"] or file_sha256(config_path) != experiment["config_sha256"]:
        raise ValueError("frozen joint configuration changed")
    for path, digest in experiment["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("frozen joint input changed")
    for relative, digest in experiment["source_sha256"].items():
        if file_sha256(output / "source-snapshot" / relative) != digest:
            raise ValueError("frozen joint source changed")
    budget = read_json(output / "execution-budget.json")
    if budget["active"] is not None:
        raise ValueError("unclosed previous execution; inspect budget before recovery")
    if not isfinite(budget["elapsed_s"]) or budget["elapsed_s"] < 0:
        raise ValueError("invalid cumulative joint wall time")
    before = summarize(output)
    saved = before["completed_checks"]
    known_failures = {}
    if config.get("stop_plan_after_protection_failure", False):
        plans = {plan.plan_id: plan for plan, _ in expected_checks(config)}
        for row in before["checks"]:
            if row["status"] == "failed" and row["evidence"].get("invalid_reasons") == []:
                witness = native_protection_failure_witness(
                    output,
                    experiment["problem_sha256"],
                    plans[row["plan_id"]],
                    row["scenario_id"],
                    terminal_required=config.get("terminal_fire_verification", False),
                )
                if witness is not None:
                    known_failures.setdefault(row["plan_id"], witness)
    attempts = budget.get("started_checks", saved)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("invalid cumulative joint check count")
    budget["started_checks"] = max(attempts, saved)
    previous, started = budget["elapsed_s"], time.monotonic()
    budget["active"] = {"pid": os.getpid()}

    def save_budget():
        budget["elapsed_s"] = previous + time.monotonic() - started
        atomic_json(output / "execution-budget.json", budget)

    save_budget()
    try:
        if (
            budget["started_checks"] >= config["max_new_checks"]
            or previous >= config["max_wall_time_s"]
        ):
            return summarize(output)
        oracle, _ = make_oracle(config_path, config, content_hash(experiment["source_sha256"]))
        if oracle.problem_sha256 != experiment["problem_sha256"]:
            raise ValueError("joint physical problem changed since preparation")
        for plan, scenario in execution_checks(config):
            folder = result_folder(output, plan, scenario)
            if folder.exists():
                if (folder / "unknown-manifest.json").is_file():
                    read_unknown_source(folder, oracle.problem_sha256, plan, scenario)
                    continue
                read_complete(
                    folder,
                    oracle.problem_sha256,
                    plan_id=plan.plan_id,
                    scenario_id=scenario,
                    demolished_ids=plan.demolished_ids,
                    cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
                    terminal_required=config.get("terminal_fire_verification", False),
                )
                continue
            if plan.plan_id in known_failures:
                persist_execution_skip(
                    output, experiment, plan, scenario, known_failures[plan.plan_id]
                )
                summarize(output)
                continue
            if oracle.actual_source_for(plan, scenario) is None:
                check, _, _, _ = oracle.evaluate(plan, scenario)
                persist_unknown_source(output, oracle, plan, scenario, check)
                summarize(output)
                continue
            save_budget()
            if (
                budget["started_checks"] >= config["max_new_checks"]
                or budget["elapsed_s"] >= config["max_wall_time_s"]
            ):
                return summarize(output)
            if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
                raise RuntimeError("joint declared disk budget reached")
            budget["started_checks"] += 1
            save_budget()
            check, record, layout, result = oracle.evaluate(plan, scenario)
            # Compare against previously persisted arms before accepting the new result.
            previous_checks = summarize(output)["checks"]
            verify_pairing(
                [*previous_checks, {"scenario_id": scenario, "evidence": check.evidence}]
            )
            persist(output, config, oracle, plan, scenario, check, record, layout, result)
            if config.get("stop_plan_after_protection_failure", False) and check.status == "failed":
                witness = native_protection_failure_witness(
                    output,
                    oracle.problem_sha256,
                    plan,
                    scenario,
                    terminal_required=config.get("terminal_fire_verification", False),
                )
                if witness is not None:
                    known_failures.setdefault(plan.plan_id, witness)
            save_budget()
            summarize(output)
            print(
                {
                    "scenario": scenario,
                    "plan": plan.plan_id,
                    "status": check.status,
                    "started_checks": budget["started_checks"],
                },
                flush=True,
            )
            del check, record, layout, result
    finally:
        save_budget()
        budget["active"] = None
        atomic_json(output / "execution-budget.json", budget)
    return summarize(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    config_path, output = args.config.resolve(), args.output.resolve()
    report = (
        prepare(config_path, output)
        if args.prepare_only
        else (summarize(output) if args.summarize else resume(config_path, output))
    )
    print(
        {key: report[key] for key in ("complete", "completed_checks", "planned_checks")}, flush=True
    )


if __name__ == "__main__":
    main()
