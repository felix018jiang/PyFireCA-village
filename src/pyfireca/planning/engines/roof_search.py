"""Frozen common-layout, retained-source CA oracle for exact building search."""

from __future__ import annotations

from dataclasses import asdict, replace
from functools import partial
from math import isfinite
from time import perf_counter

import numpy as np

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.roof_ignition import (
    footprint_ignitions,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import (
    resolve_roof_layout,
    roof_area_integrated_radiation_graph,
)
from pyfireca.planning.source_area_coverage import cover_source_priority_by_area


class RoofDemolitionOracle:
    """Keep candidate/source/core refinement fixed before comparing any plans.

    Each fire input is a single frozen source-protocol draw. All sources which
    could become first retained under candidate removal are pre-realized.
    Empty source strata are unknown, never successful no-fire evaluations.
    Only one prepared engine is retained to bound resident graph storage.
    """

    def __init__(
        self,
        bundle,
        transport,
        inventory,
        *,
        candidate_ids,
        fires,
        code_version,
        horizon_s=1200.0,
        scales_m=(1.0, 0.5),
        quadrature_spacing_m=0.5,
        max_nodes=200_000,
        max_radiation_edges=20_000_000,
        on_evaluation=None,
        refinement_ids=None,
        maximum_demolitions=None,
        core_overlap_policy="reject",
        stop_at_first_core_ignition=True,
        experimental_numerics=False,
        phase_fraction=(0.0, 0.0),
        cell_size_by_id=None,
        snapshot_sink_factory=None,
        freeze_integration_membership=False,
        source_area_limit_m2=None,
        source_area_tolerance_m2=0.0,
        enable_physical_group_cuts=False,
        stop_at_extinction=False,
    ):
        self.bundle, self.transport, self.inventory = bundle, transport, inventory
        self.candidate_ids = tuple(sorted(candidate_ids))
        self.refinement_ids = tuple(
            sorted(self.candidate_ids if refinement_ids is None else refinement_ids)
        )
        if not set(self.refinement_ids) <= inventory.by_id.keys():
            raise ValueError("refinement IDs must be inventory buildings")
        if maximum_demolitions is not None and (
            isinstance(maximum_demolitions, bool)
            or not isinstance(maximum_demolitions, int)
            or maximum_demolitions < 0
        ):
            raise ValueError("maximum demolitions must be a nonnegative integer")
        self.maximum_demolitions = maximum_demolitions
        if not isinstance(stop_at_extinction, bool):
            raise TypeError("stop_at_extinction must be an explicit boolean")
        self.stop_at_extinction = stop_at_extinction
        if (
            isinstance(source_area_tolerance_m2, bool)
            or not isfinite(source_area_tolerance_m2)
            or source_area_tolerance_m2 < 0
        ):
            raise ValueError("source area tolerance must be finite and nonnegative")
        if source_area_limit_m2 is None:
            if source_area_tolerance_m2 != 0:
                raise ValueError("a source area tolerance requires an explicit source area limit")
        elif (
            isinstance(source_area_limit_m2, bool)
            or not isfinite(source_area_limit_m2)
            or source_area_limit_m2 < 0
        ):
            raise ValueError("source area limit must be finite and nonnegative")
        self.source_area_limit_m2 = source_area_limit_m2
        self.source_area_tolerance_m2 = source_area_tolerance_m2
        if not isinstance(freeze_integration_membership, bool):
            raise TypeError("freeze_integration_membership must be an explicit boolean")
        self.freeze_integration_membership = freeze_integration_membership
        if not isinstance(enable_physical_group_cuts, bool):
            raise TypeError("enable_physical_group_cuts must be an explicit boolean")
        if enable_physical_group_cuts and not freeze_integration_membership:
            raise ValueError("physical-group cuts require frozen radiation integration membership")
        self.enable_physical_group_cuts = enable_physical_group_cuts
        if enable_physical_group_cuts:
            self.enable_failure_certificates = True
        self.stop_at_first_core_ignition = stop_at_first_core_ignition
        if len(set(candidate_ids)) != len(candidate_ids) or not set(candidate_ids) <= set(
            inventory.candidate_ids
        ):
            raise ValueError("search candidates must be unique eligible inventory IDs")
        if not experimental_numerics and (
            tuple(scales_m) != (1.0, 0.5)
            or quadrature_spacing_m != 0.5
            or tuple(phase_fraction) != (0.0, 0.0)
            or cell_size_by_id
        ):
            raise ValueError("current search oracle requires the frozen dual-scale rules")
        if (
            not scales_m
            or len(set(scales_m)) != len(scales_m)
            or any(
                not isfinite(s) or s <= 0 or s > bundle.request.grid.cell_size_m for s in scales_m
            )
            or not isfinite(quadrature_spacing_m)
            or quadrature_spacing_m <= 0
            or len(phase_fraction) != 2
            or any(not isfinite(x) or not 0 <= x < 1 for x in phase_fraction)
        ):
            raise ValueError("invalid explicitly declared numerical protocol")
        self.phase_fraction = tuple(phase_fraction)
        self.cell_size_by_id = {} if cell_size_by_id is None else dict(cell_size_by_id)
        if not set(self.cell_size_by_id) <= set(self.refinement_ids) or any(
            not isfinite(value) or value <= 0 or value > bundle.request.grid.cell_size_m
            for value in self.cell_size_by_id.values()
        ):
            raise ValueError("per-building roof scales require explicitly refined IDs")
        self.scales_m, self.horizon_s = tuple(scales_m), horizon_s
        self.quadrature_spacing_m = quadrature_spacing_m
        self.max_nodes, self.max_radiation_edges = max_nodes, max_radiation_edges
        self.on_evaluation = on_evaluation
        self.snapshot_sink_factory = snapshot_sink_factory
        self.base = resolve_roof_layout(
            bundle.request, inventory, max_nodes=max_nodes, core_overlap_policy=core_overlap_policy
        )
        self.source_area_function = None
        self.source_area_coverages = {}
        if source_area_limit_m2 is not None:
            self.source_area_function = build_cleared_area_function(
                self.base.owners,
                [geometry.area for geometry in self.base.geometry],
                area_basis="disjoint_physical_roof_geometry;clear_only_if_all_owners_removed",
            )
        self.fires, self.scenarios = {}, {}
        for fire in fires:
            label, protocol = fire["fire_id"], fire["protocol"]
            if label in self.fires or len(protocol["draws"]) != 1:
                raise ValueError("unique fire IDs and one prescribed draw per input required")
            draw = protocol["draws"][0]
            footprints, removed = {}, []
            priority = draw["priority_ids"]
            if source_area_limit_m2 is not None:
                coverage = cover_source_priority_by_area(
                    draw,
                    inventory,
                    self.candidate_ids,
                    self.source_area_function,
                    source_area_limit_m2,
                    area_bound_tolerance_m2=source_area_tolerance_m2,
                    minimum_source_area_m2=protocol["ignition_area_m2"],
                )
                self.source_area_coverages[label] = coverage
                priority = coverage.source_ids
            for identifier in priority:
                row = realize_sources(protocol, inventory, demolished_ids=tuple(removed))[0]
                if row["status"] != "fire" or row["source_id"] != identifier:
                    raise ValueError("source priority cannot be realized exactly")
                footprints[identifier] = row["footprint"]
                if identifier not in self.candidate_ids or (
                    source_area_limit_m2 is None
                    and maximum_demolitions is not None
                    and len(removed) >= maximum_demolitions
                ):
                    break
                removed.append(identifier)
            refined_ids = tuple(
                sorted(
                    set(self.refinement_ids)
                    | set(footprints)
                    | {t.building_id for t in inventory.targets}
                )
            )
            self.fires[label] = {
                "protocol": protocol,
                "footprints": footprints,
                "refined_ids": refined_ids,
            }
            for scale in self.scales_m:
                key = f"{label}-h{scale:g}"
                self.scenarios[key] = (label, scale)
        if not self.scenarios:
            raise ValueError("at least one frozen fire required")
        self.code_version = code_version
        self.problem = {
            "core_overlap_policy": core_overlap_policy,
            "schema_version": "heritage-roof-search-problem-1",
            "case_sha256": request_fingerprint(bundle.request),
            "inventory_sha256": inventory.sha256,
            "transport_parameters": asdict(transport),
            "candidate_ids": self.candidate_ids,
            "refinement_seed_ids": self.refinement_ids,
            "maximum_demolitions": maximum_demolitions,
            "horizon_s": horizon_s,
            "scales_m": self.scales_m,
            "quadrature_spacing_m": quadrature_spacing_m,
            "code_version": code_version,
            "source_policy": "sampled_retained_sources",
            "early_stop": "first_valid_core_ignition"
            if stop_at_first_core_ignition
            else "full_horizon",
            "overlap_policy": "retain_shared_roof",
            "fires": {
                label: {
                    "protocol": data["protocol"],
                    "refined_ids": data["refined_ids"],
                    "footprints": {i: f.to_mapping() for i, f in data["footprints"].items()},
                }
                for label, data in self.fires.items()
            },
        }
        if freeze_integration_membership:
            # Omit the default-false field to preserve legacy problem hashes.
            self.problem["freeze_integration_membership"] = True
        if enable_physical_group_cuts:
            self.problem["enable_physical_group_cuts"] = True
        if stop_at_extinction:
            self.problem["stop_at_extinction"] = True
        if source_area_limit_m2 is not None:
            # No area fields are added in the legacy/default problem. A new
            # area cap freezes both source coverage and its mandatory guard.
            self.problem["source_area_coverage"] = {
                "maximum_cleared_area_m2": float(source_area_limit_m2),
                "area_bound_tolerance_m2": float(source_area_tolerance_m2),
                "fires": {
                    label: coverage.to_mapping()
                    for label, coverage in self.source_area_coverages.items()
                },
                "guard_policy": "before_CA;identical_effective_cap_as_prefix_proof",
                "upper_bound_is_optimality_proof": False,
            }
        self.problem_sha256 = content_hash(self.problem)
        if experimental_numerics:
            self.problem["experimental_numerics"] = {
                "phase_fraction": self.phase_fraction,
                "cell_size_by_id": self.cell_size_by_id,
                "scope": "numerical_diagnostic_not_legacy_optimality_certificate",
            }
            self.problem_sha256 = content_hash(self.problem)
        self._engine_key = self._engine = None

    def proven_dominated_candidates(self):
        """Prove identity-only removals, without assuming monotone fire response.

        Every affected owner group must retain a noncandidate owner. Removal
        must not alter source selection or point/area integration membership.
        Then masks, all thermal arrays, graph weights, contact and time steps
        are identical. Numeric building identity is metadata in this solver.
        Tiny positive exclusive patches deliberately prevent certification.
        """
        groups = set(self.base.owners) - {()}
        candidates, proofs = set(self.candidate_ids), {}
        for identifier in self.candidate_ids:
            affected = [g for g in groups if identifier in g]
            if any(identifier in f["footprints"] for f in self.fires.values()):
                continue
            if any(not set(g).difference(candidates) for g in affected):
                continue
            # Keep the graph builder's selected-ID input valid as well as its
            # integration masks: a wholly shared annotation disappears from
            # the available owners when removed.
            if any(identifier in f["refined_ids"] for f in self.fires.values()):
                continue
            proofs[identifier] = {
                "problem_sha256": self.problem_sha256,
                "type": "identity_only_removal_strict_count_dominance",
                "affected_owner_groups": [list(g) for g in sorted(affected)],
                "mandatory_surviving_owners": sorted(
                    set().union(*(set(g).difference(candidates) for g in affected))
                ),
                "possible_source": False,
                "thermal_masks_arrays_integration_and_contact_unchanged": True,
            }
        return proofs

    def __call__(self, demolished_ids, scenario_id):
        if self.maximum_demolitions is not None and len(demolished_ids) > self.maximum_demolitions:
            raise ValueError("plan exceeds the frozen source/refinement coverage bound")
        started = perf_counter()
        label, scale = self.scenarios[scenario_id]
        fire = self.fires[label]
        if self.source_area_limit_m2 is not None:
            covered = self.source_area_coverages[label].validate_plan(
                demolished_ids, cleared_area=self.source_area_function
            )
        else:
            covered = None
        source_id = next((i for i in fire["footprints"] if i not in demolished_ids), None)
        if covered is not None and covered["source_id"] != source_id:
            raise ValueError("actual retained source is outside the frozen area source coverage")
        if source_id is None:
            return ProtectionCheck("unknown", {"reason": "no_retained_source_in_frozen_draw"})
        if self._engine_key != scenario_id:
            self._engine = None
            layout = refine_roof_layout(
                self.base,
                self.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=self.max_nodes,
                phase_fraction=self.phase_fraction,
                cell_size_by_id=self.cell_size_by_id,
            )
            integration_options = {}
            if self.freeze_integration_membership:
                selected = frozenset(fire["refined_ids"])
                mask = np.asarray([bool(selected.intersection(ids)) for ids in layout.owners])
                mask.setflags(write=False)
                integration_options["frozen_integrated_node_mask"] = mask
            self._engine = RoofHeritageEvaluationEngine(
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
                    **integration_options,
                ),
                max_radiation_edges=self.max_radiation_edges,
            )
            self._engine_key = scenario_id
        engine = self._engine
        ignition_layout = replace(
            engine.layout,
            ignition_owner=tuple(
                i if i == source_id else None for i in engine.layout.ignition_owner
            ),
        )
        scenario = EvaluationScenario(
            scenario_id,
            footprint_ignitions(ignition_layout),
            self.horizon_s,
        )
        plan = BuildingDemolitionPlan(
            "demolition-" + ("-".join(demolished_ids) or "none"),
            tuple(demolished_ids),
            ground_evidence="hypothetical_exclusive_roof_clearance;retain_shared_fuel;not_surveyed",
            overlap_policy="retain_shared_roof",
        )
        prepared_s = perf_counter() - started
        solve_started = perf_counter()
        self.last_snapshot_sink = (
            None
            if self.snapshot_sink_factory is None
            else self.snapshot_sink_factory(engine.layout)
        )
        record, layout, result = engine.simulate(
            scenario,
            plan,
            stop_at_first_core_ignition=self.stop_at_first_core_ignition,
            snapshot_sink=self.last_snapshot_sink,
            stop_at_extinction=self.stop_at_extinction,
        )
        solve_s = perf_counter() - solve_started
        direct = [e.col for e in layout.node_request.scenario.ignitions]
        budget = {
            name: float(getattr(layout.thermal.parameters, name).ravel()[direct].sum())
            for name in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        }
        raw_status = record.metrics["protection_status"]
        status = "unknown" if raw_status == "invalid" else raw_status
        check = ProtectionCheck(
            status,
            {
                "evaluation_sha256": record.evaluation_sha256,
                "source_id": source_id,
                "source_replaced": source_id != fire["protocol"]["draws"][0]["baseline_source_id"],
                "source_budget": budget,
                "scale_m": scale,
                "core_outcomes": record.metrics["core_outcomes"],
                "invalid_reasons": record.diagnostics["invalid_reasons"],
                "reached_horizon": record.diagnostics["reached_requested_horizon"],
                "simulated_until_s": record.diagnostics["simulated_until_s"],
                "prepared_s": prepared_s,
                "solve_s": solve_s,
                "elapsed_s": perf_counter() - started,
                "nodes": layout.node_request.grid.width,
                "radiation_edges": record.diagnostics["radiation_edge_count"],
            },
        )
        if self.stop_at_extinction:
            check.evidence.update(
                protection_horizon_closed=record.diagnostics["protection_horizon_closed"],
                effective_goal_closed=record.diagnostics["effective_goal_closed"],
                extinction_closed=record.diagnostics["extinction_closed"],
                temporal_closure_certificate=record.diagnostics["temporal_closure_certificate"],
            )
        if status == "failed" and getattr(self, "enable_failure_certificates", False):
            from pyfireca.planning.failure_cuts import cold_removal_certificate

            certificate = cold_removal_certificate(
                layout,
                result,
                engine.last_contact,
                self.inventory,
                self.transport,
                demolished_ids=demolished_ids,
                scenario_id=scenario_id,
                problem_sha256=self.problem_sha256,
                source_id=source_id,
                frozen_thermal_membership=self.enable_physical_group_cuts,
            )
            if certificate is not None:
                check.evidence["failure_certificate"] = certificate
        if self.on_evaluation is not None:
            self.on_evaluation(demolished_ids, scenario_id, check, record, layout, result)
        return check
