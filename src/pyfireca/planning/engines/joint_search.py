"""Opt-in full joint-action protection oracle; no legacy roof cuts are imported."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from functools import partial

import numpy as np

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.joint_action_prototype import (
    build_joint_action_universe,
    joint_cold_removal_certificate,
    joint_source_area_coverage,
)
from pyfireca.planning.roof_ignition import (
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import roof_area_integrated_radiation_graph
from pyfireca.planning.terminal_verification import audit_terminal_result
from pyfireca.village.extinction_v3 import extinction_observation_fingerprints


class JointProtectionOracle:
    """One active physical engine; all logical roof and registered grass actions.

    A core ignition is sufficient to reject protection, so failures stop early.
    Passing requires strict extinction, not merely reaching the time cap. Every
    failure certificate is newly generated under the joint capability schema.
    """

    def __init__(self, bundle, transport, inventory, *, config, fires, code_version):
        self.config, self.code_version = config, code_version
        self.terminal_validation_scope = config.get("terminal_validation_scope", "strict")
        if self.terminal_validation_scope not in {"strict", "finite_mask_conditioned"}:
            raise ValueError("unknown joint terminal validation scope")
        self.horizon_s = float(config["horizon_s"])
        self.roof = RoofDemolitionOracle(
            bundle,
            transport,
            inventory,
            candidate_ids=tuple(sorted(inventory.candidate_ids)),
            refinement_ids=tuple(config["refinement_ids"]),
            fires=fires,
            code_version=code_version,
            horizon_s=self.horizon_s,
            scales_m=tuple(config["roof_scales_m"]),
            quadrature_spacing_m=config["quadrature_spacing_m"],
            max_nodes=config["max_nodes"],
            max_radiation_edges=config["max_radiation_edges"],
            maximum_demolitions=None,
            core_overlap_policy=config.get("core_overlap_policy", "reject"),
            stop_at_first_core_ignition=True,
            stop_at_extinction=True,
            freeze_integration_membership=True,
            source_area_limit_m2=config["source_area_limit_m2"],
            source_area_tolerance_m2=config.get("source_area_tolerance_m2", 0),
            experimental_numerics=config.get("experimental_test_protocol", False),
        )
        self.bundle, self.transport, self.inventory = bundle, transport, inventory
        self.base, self.fires = self.roof.base, self.roof.fires
        self.logical_universe = build_joint_action_universe(
            self.base,
            inventory,
            grass_cell_ids=config.get("grass_cell_ids"),
            construction_layout=self.base,
        )
        self.candidate_ids = self.logical_universe.candidate_ids
        self.source_coverages = {
            label: joint_source_area_coverage(
                self.logical_universe,
                fire["protocol"]["draws"][0],
                inventory,
                config["source_area_limit_m2"],
                tolerance_m2=config.get("source_area_tolerance_m2", 0),
            )
            for label, fire in self.fires.items()
        }
        for label, proof in self.source_coverages.items():
            if tuple(self.fires[label]["footprints"]) != proof.roof_coverage.source_ids:
                raise ValueError(
                    "joint roof-projection source prefix differs from prepared footprints"
                )
        self.scenarios = {
            f"{label}-h{scale:g}": (label, scale)
            for scale in config["roof_scales_m"]
            for label in self.fires
        }
        self.problem = {
            "schema_version": "heritage-joint-action-protection-problem-1",
            "roof_preparation": self.roof.problem,
            "joint_action_universe": self.logical_universe.to_mapping(),
            "joint_universe_sha256": self.logical_universe.sha256,
            "source_coverages": {
                label: p.to_mapping() for label, p in self.source_coverages.items()
            },
            "scenario_order": list(self.scenarios),
            "evaluation_temporal_protocol": {
                "horizon_cap_s": self.horizon_s,
                "stop_at_first_core_ignition": True,
                "stop_at_extinction": True,
                "passed_requires_strict_extinction_closure": True,
                "failure_loss_can_be_incomplete": True,
                "old_phase_prefix_or_roof_cut_imported": False,
            },
            "code_version": code_version,
        }
        self.problem_sha256 = content_hash(self.problem)
        self.native_problem, self.native_problem_sha256 = self.problem, self.problem_sha256
        self.boundary_scope_problem = None
        if self.terminal_validation_scope == "finite_mask_conditioned":
            from pyfireca.planning.boundary_scope import make_boundary_scope_problem

            self.boundary_scope_problem = make_boundary_scope_problem(
                self.native_problem_sha256, self.bundle.request
            )
            # The independently registered criterion is the mathematical problem.
            # Its native physical predecessor is saved separately by the runner.
            self.problem = {
                key: value
                for key, value in self.boundary_scope_problem.items()
                if key != "analysis_problem_sha256"
            }
            self.problem_sha256 = content_hash(self.problem)
            if self.problem_sha256 != self.boundary_scope_problem["analysis_problem_sha256"]:
                raise ValueError("conditional protection problem fingerprint differs")
        self.expected_partitions = {}
        self._scenario = self._engine = self._universe = None
        self.on_evaluation = None

    def prepared_geometry(self, scenario_id):
        """Prepare a partition and logical witness without a radiative/contact graph."""
        label, scale = self.scenarios[scenario_id]
        fire = self.fires[label]
        layout = refine_roof_layout(
            self.base,
            self.inventory,
            cell_size_m=scale,
            refined_ids=fire["refined_ids"],
            footprints=tuple(fire["footprints"].values()),
            max_nodes=self.config["max_nodes"],
        )
        universe = build_joint_action_universe(
            layout,
            self.inventory,
            grass_cell_ids=self.config.get("grass_cell_ids"),
            construction_layout=self.base,
        )
        if universe.sha256 != self.logical_universe.sha256:
            raise ValueError("joint action costs or catalog changed with source/model support")
        expected = self.expected_partitions.get(scenario_id)
        if expected is not None and universe.physical_partition_sha256 != expected:
            raise ValueError("joint physical partition changed from the frozen preflight")
        return layout, universe

    def engine_for_scenario(self, scenario_id):
        if self._scenario != scenario_id:
            self._engine = self._universe = None
            label, _ = self.scenarios[scenario_id]
            fire = self.fires[label]
            layout, universe = self.prepared_geometry(scenario_id)
            selected = frozenset(fire["refined_ids"])
            integrated = np.asarray([bool(selected.intersection(group)) for group in layout.owners])
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
                    quadrature_spacing_m=self.config["quadrature_spacing_m"],
                    frozen_integrated_node_mask=integrated,
                ),
                max_radiation_edges=self.config["max_radiation_edges"],
            )
            self._universe, self._scenario = universe, scenario_id
        return self._engine

    def __call__(self, actions, scenario_id):
        actions = self.logical_universe.canonical_actions(actions)
        label, scale = self.scenarios[scenario_id]
        source = self.source_coverages[label].validate_actions(actions)["source_id"]
        if source is None:
            return ProtectionCheck(
                "unknown",
                {
                    "reason": "no_retained_source_in_declared_joint_domain",
                    "new_CA_checks": 0,
                    "problem_sha256": self.problem_sha256,
                    "invalid_reasons": [],
                },
            )
        engine = self.engine_for_scenario(scenario_id)
        ignition_layout = replace(
            engine.layout,
            ignition_owner=tuple(
                identifier if identifier == source else None
                for identifier in engine.layout.ignition_owner
            ),
        )
        scenario = EvaluationScenario(
            scenario_id, footprint_ignitions(ignition_layout), self.horizon_s
        )
        plan = self.logical_universe.plan(actions, plan_id="joint-" + content_hash(actions))
        record, layout, result = engine.simulate(
            scenario,
            plan,
            stop_at_first_core_ignition=True,
            stop_at_extinction=True,
        )
        direct = [event.col for event in layout.node_request.scenario.ignitions]
        baseline_source = self.fires[label]["protocol"]["draws"][0]["baseline_source_id"]
        source_evidence = {
            "source_id": source,
            "comparison_problem_sha256": self.native_problem_sha256,
            "source_policy": "joint_total_area_bounded_retained_sources",
            "fire_id": label,
            "scale_m": scale,
            "new_independent_fire_draw_count": 0,
        }
        conditional = None
        if self.boundary_scope_problem is not None:
            from pyfireca.planning.boundary_scope import audit_finite_domain_result

            conditional = audit_finite_domain_result(
                engine,
                scenario,
                plan,
                record,
                layout,
                result,
                contact=engine.last_contact,
                analysis_problem=self.boundary_scope_problem,
                source_evidence=source_evidence,
            )
            verification = conditional.strict_verification
        else:
            verification = audit_terminal_result(
                engine,
                scenario,
                plan,
                record,
                layout,
                result,
                contact=engine.last_contact,
                reference_problem_sha256=self.native_problem_sha256,
                source_evidence=source_evidence,
            )
        failed = bool(record.metrics["core_joint_failure_observed"])
        invalid = bool(record.diagnostics["invalid_reasons"])
        status = "unknown" if invalid else "failed" if failed else verification.check.status
        evidence = {
            "problem_sha256": self.native_problem_sha256,
            "source_id": source,
            "baseline_source_id": baseline_source,
            "source_replaced": source != baseline_source,
            "source_budget": {
                name: float(getattr(layout.node_request.parameters, name).ravel()[direct].sum())
                for name in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
            },
            "scale_m": scale,
            "core_outcomes": record.metrics["core_outcomes"],
            "invalid_reasons": record.diagnostics["invalid_reasons"],
            "reached_horizon": record.diagnostics["reached_requested_horizon"],
            "simulated_until_s": result.final_state.time_s,
            "terminal_verification": verification.summary,
            "terminal_loss_complete": verification.summary["terminal_complete"],
            "observation_fingerprints": extinction_observation_fingerprints(
                result.final_state,
                record.diagnostics["maximum_valid_surface_temperature_by_node_K"],
            ),
            "physical_partition_sha256": self._universe.physical_partition_sha256,
            "source_footprint_sha256": self.fires[label]["footprints"][source].sha256,
            "frozen_integrated_node_mask_sha256": self.integration_mask_sha256,
            "new_CA_checks": 1,
        }
        if status == "unknown":
            evidence["reason"] = (
                "invalid_physical_domain" if invalid else "terminal_time_cap_or_closure_unresolved"
            )
        if status == "failed" and conditional is None:
            certificate = joint_cold_removal_certificate(
                engine,
                self._universe,
                actions,
                record,
                layout,
                result,
                problem_sha256=self.problem_sha256,
                source_id=source,
            )
            if certificate is not None:
                evidence["failure_certificate"] = certificate
        physical, counts, fuel = (
            verification.fire_loss[name]
            for name in ("physical", "physical_remaining_roof_annotation_counts", "fuel")
        )
        evidence["loss_metrics"] = {
            "ignited_roof_area_m2": physical["roof"]["cumulative_ignited_covered_area_m2"],
            "ignited_nonroof_area_m2": physical["nonroof"]["cumulative_ignited_covered_area_m2"],
            "consumed_fuel_kg": fuel["fire_depleted_kg"],
            "roof_fire_depleted_kg": fuel["roof_fire_depleted_kg"],
            "nonroof_fire_depleted_kg": fuel["nonroof_fire_depleted_kg"],
            "active_roof_area_m2": physical["roof"]["active_burning_covered_area_m2"],
            "active_nonroof_area_m2": physical["nonroof"]["active_burning_covered_area_m2"],
            "clearing_plus_ignited_roof_area_m2": physical["roof"][
                "clearing_plus_ignited_union_area_m2"
            ],
            "clearing_plus_ignited_annotation_count_lower": counts[
                "ignited_annotation_attribution_count"
            ]["lower"],
            "clearing_plus_ignited_annotation_count_upper": counts[
                "ignited_annotation_attribution_count"
            ]["upper"],
        }
        evidence["loss_metrics_semantics"] = (
            "partial_for_failed_prefix;complete_only_with_terminal_loss_complete;"
            "legacy_count_key_names_hold_original_physical_owner_fire_counts"
        )
        check = ProtectionCheck(status, evidence)
        if conditional is not None:
            from pyfireca.planning.boundary_scope import (
                make_finite_domain_mip_check,
                validate_finite_domain_mip_check,
            )

            receipt = make_finite_domain_mip_check(conditional.summary)
            # This is a new mathematical query, never a rewritten native check.
            # Keep native invalidity and the complete original query in evidence.
            additions = {
                key: value
                for key, value in evidence.items()
                if key not in {"invalid_reasons", "problem_sha256", "reason"}
            }
            additions.update(
                terminal_validation_scope=self.terminal_validation_scope,
                native_protection_query={"status": check.status, "evidence": evidence},
                native_terminal_loss_complete=verification.summary["terminal_complete"],
                terminal_loss_complete=conditional.summary["fire_loss_complete"],
            )
            conditional_evidence = {**additions, **receipt.evidence}
            if receipt.status == "failed" and not invalid and "failure_certificate" not in evidence:
                certificate = joint_cold_removal_certificate(
                    engine,
                    self._universe,
                    actions,
                    record,
                    layout,
                    result,
                    problem_sha256=self.problem_sha256,
                    source_id=source,
                )
                if certificate is not None:
                    conditional_evidence["failure_certificate"] = certificate
            if receipt.status == "unknown":
                conditional_evidence["reason"] = "finite_mask_protection_query_unresolved"
            check = ProtectionCheck(receipt.status, conditional_evidence)
            validate_finite_domain_mip_check(check, analysis_problem_sha256=self.problem_sha256)
        if self.on_evaluation is not None:
            self.on_evaluation(
                actions, scenario_id, check, record, layout, result, verification.fire_loss
            )
        return check
