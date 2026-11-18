"""Fixed heritage protection using independent polygon roof temperatures."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import ClassVar

import numpy as np

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.roof_thermal import (
    apply_roof_demolition,
    bind_roof_ignitions,
    resolve_roof_layout,
    roof_contact_operator,
    roof_radiation_graph,
)
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges


class RoofHeritageEvaluationEngine:
    capabilities: ClassVar[dict] = {
        "engine": "heritage_independent_roof_ca_v3_p1",
        "independent_subcell_temperatures": True,
        "independent_patch_fuel_water_and_burn_clocks": True,
        "whole_building_demolition": True,
        "fixed_core_targets": True,
        "radiation": "transparent_regularized_point_at_patch_representative_positions",
        "contact": "existing_bounded_closure_gated_by_positive_polygon_boundary_contact",
        "opaque_wall": False,
        "spotting": False,
        "optimization": False,
        "field_calibrated": False,
    }

    def __init__(
        self,
        bundle,
        transport,
        inventory,
        *,
        source_policy,
        code_version="heritage-roof-1",
        prepared_layout=None,
        contact_builder=None,
        radiation_builder=None,
        max_radiation_edges=5_000_000,
    ):
        if not inventory.targets:
            raise ValueError("fixed heritage targets required")
        if source_policy not in {
            "technical_probe",
            "declared_fixed_sources",
            "sampled_retained_sources",
        }:
            raise ValueError("source policy must be explicit")
        if source_policy == "declared_fixed_sources" and not inventory.fixed_source_ids:
            raise ValueError("fixed source IDs remain pending")
        self.bundle, self.transport, self.inventory = bundle, transport, inventory
        self.source_policy, self.code_version = source_policy, code_version
        self.contact_builder = contact_builder or roof_contact_operator
        self.radiation_builder = radiation_builder or roof_radiation_graph
        self.radiation_method = getattr(
            self.radiation_builder,
            "__name__",
            getattr(getattr(self.radiation_builder, "func", None), "__name__", "custom"),
        )
        self.max_radiation_edges = max_radiation_edges
        self.case_sha256, self.inventory_sha256 = (
            request_fingerprint(bundle.request),
            inventory.sha256,
        )
        parent = bundle.request
        graph = build_v3_radiative_transport_edges(
            parent.grid,
            parent.source_mask,
            parent.target_mask,
            parent.parameters.receiver_area_m2,
            parent.parameters.source_height_m,
            parent.parameters.receiver_height_m,
            np.ones(parent.grid.shape),
            transport,
            ground_elevation_m=parent.ground_elevation_m,
            max_edge_count=max_radiation_edges,
        )
        if any(
            not np.array_equal(getattr(graph, k), getattr(parent.exposure_edges, k))
            for k in ("source", "target", "weight")
        ):
            raise ValueError("unsupported parent graph: transparent-point match required")
        if prepared_layout is not None and (
            prepared_layout.inventory_sha256 != inventory.sha256
            or request_fingerprint(prepared_layout.physical_request) != self.case_sha256
            or prepared_layout.node_request is not None
        ):
            raise ValueError("prepared roof layout must match the unbound parent and inventory")
        self.layout = prepared_layout or resolve_roof_layout(parent, inventory)
        self.layout_sha256 = self.layout.sha256
        self.core_nodes = {
            t.building_id: self.layout.nodes(t.building_id) for t in inventory.targets
        }
        if any(not len(nodes) for nodes in self.core_nodes.values()):
            raise ValueError("every core must retain physical roof components")

    def prepare(self, scenario, plan):
        if not isinstance(scenario, EvaluationScenario) or not isinstance(
            plan, BuildingDemolitionPlan
        ):
            raise TypeError("typed scenario and building demolition plan required")
        events, source_ids = bind_roof_ignitions(
            self.layout, self.inventory, scenario, source_policy=self.source_policy
        )
        bound = self.layout.node_inputs.bind(
            replace(
                self.bundle.request.scenario, end_time_s=float(scenario.horizon_s), ignitions=events
            )
        )
        layout, ledger = apply_roof_demolition(
            replace(self.layout, node_request=bound), self.inventory, plan, source_ids=source_ids
        )
        request = layout.node_request
        graph = self.radiation_builder(layout, self.transport, max_edges=self.max_radiation_edges)
        layout = replace(layout, node_request=replace(request, exposure_edges=graph))
        ledger.update(
            actual_source_ids=list(source_ids),
            source_policy=self.source_policy,
            study_source_selection_pending=(
                self.source_policy == "technical_probe" and not self.inventory.fixed_source_ids
            ),
            radiation_method=self.radiation_method,
        )
        return layout, self.contact_builder(layout), ledger

    def simulate(
        self,
        scenario,
        plan,
        *,
        snapshot_sink=None,
        stop_at_first_core_ignition=False,
        progress_callback=None,
        stop_at_extinction=False,
    ):
        """Return evaluation, physical-to-node layout and native thermal result."""
        if not isinstance(stop_at_extinction, bool):
            raise TypeError("stop_at_extinction must be an explicit boolean")
        layout, contact, ledger = self.prepare(scenario, plan)
        self.last_contact = contact
        request = layout.node_request
        core_nodes = np.unique(np.concatenate(list(self.core_nodes.values())))
        max_temperature = np.full(
            request.grid.width, request.scenario.ambient_temperature_K, dtype=float
        )
        closure_certificate = None
        closure_refusal = None
        if stop_at_extinction:
            from pyfireca.village.extinction_v3 import certify_extinction_v3

        def observe(state):
            nonlocal closure_certificate, closure_refusal
            # Burning temperatures are undefined in V3; retain only meaningful
            # susceptible surface temperatures, not a fictitious flame value.
            valid = state.temperature_valid_mask.ravel()
            np.maximum(
                max_temperature,
                np.where(valid, state.surface_temperature_K.ravel(), 0),
                out=max_temperature,
            )
            core_failure = bool(
                stop_at_first_core_ignition
                and np.isfinite(state.ignition_time_s.ravel()[core_nodes]).any()
            )
            if core_failure:
                return True
            if stop_at_extinction:
                attempt = certify_extinction_v3(
                    request,
                    state,
                    contact=contact,
                    post_apply_events_boundary=True,
                    maximum_valid_surface_temperature_K=max_temperature,
                )
                closure_refusal = attempt["rejection_reasons"]
                if attempt["closed"]:
                    closure_certificate = attempt["certificate"]
                    return True
            return False

        result = _run_resolved_v3_simulation(
            request,
            contact=contact,
            snapshot_sink=snapshot_sink,
            retain_snapshots=False,
            stop_when=observe,
            progress_callback=progress_callback,
        )
        state = result.final_state
        if stop_at_extinction:
            # At a requested horizon the loop has already applied final events,
            # but need not have called its observer at that last boundary.
            observe(state)
        diagnostics = check_final(request, result)
        ignition_times = state.ignition_time_s.ravel()
        ignited = np.isfinite(ignition_times)
        times = {}
        for identifier in self.inventory.by_id:
            nodes = layout.nodes(identifier)
            values = ignition_times[nodes]
            finite = values[np.isfinite(values)]
            if len(finite):
                times[identifier] = float(finite.min())
        complete = bool(state.time_s >= scenario.horizon_s - 1e-8)
        goal_closed = complete or closure_certificate is not None
        grid = layout.physical_request.grid
        rows, cols = np.divmod(layout.cell_id[ignited], grid.width)
        boundary = bool(
            np.any((rows == 0) | (rows == grid.height - 1) | (cols == 0) | (cols == grid.width - 1))
        )
        source_verified = all(times.get(i) == 0 for i in ledger["actual_source_ids"])
        invalid = ["fire_reached_physical_domain_edge"] if boundary else []
        if not source_verified:
            invalid.append("source_not_ignited")
        target_ids = set(self.core_nodes)
        failed = sorted(target_ids.intersection(times))
        status = (
            "invalid" if invalid else "failed" if failed else "passed" if goal_closed else "unknown"
        )
        retained_annotations = sorted(
            i
            for i, b in self.inventory.by_id.items()
            if b.source_label == "building" and i not in target_ids and i not in plan.demolished_ids
        )
        retained = sorted(set(retained_annotations) - set(layout.ambiguous_ids))
        outcomes = []
        for target in self.inventory.targets:
            nodes = self.core_nodes[target.building_id]
            outcomes.append(
                {
                    **asdict(target),
                    "component_count": len(nodes),
                    "independent_temperature_states": True,
                    "shared_temperature_components": 0,
                    "ignited": target.building_id in times,
                    "first_ignition_s": times.get(target.building_id),
                    "ignited_component_count": int(ignited[nodes].sum()),
                    "maximum_valid_surface_temperature_K": float(max_temperature[nodes].max()),
                    "absorbed_radiation_J": float(state.absorbed_radiation_J.ravel()[nodes].sum()),
                    "received_convection_J": float(
                        state.received_convection_J.ravel()[nodes].sum()
                    ),
                }
            )
        metrics = {
            "protection_status": status,
            "protection_goal_met": status == "passed",
            "core_joint_failure_observed": bool(failed),
            "core_count": len(target_ids),
            "ignited_core_ids": failed,
            "core_outcomes": outcomes,
            "actual_source_ids": ledger["actual_source_ids"],
            "retained_ordinary_ids": retained,
            "retained_ordinary_annotation_ids": retained_annotations,
            "ordinary_comparison_policy": "exclude_ambiguous_overlapping_annotation_ids",
            "ignited_retained_ordinary_ids": sorted(set(retained).intersection(times)),
            "ignited_secondary_ordinary_ids": sorted(
                set(retained).intersection(times) - set(ledger["actual_source_ids"])
            ),
            "building_first_ignitions": times,
            "ambiguous_ordinary_annotation_ids": layout.ambiguous_ids,
            "consumed_fuel_kg": result.consumed_fuel_kg,
            "interpretation": (
                "any roof-patch ignition within this horizon; not building loss or annual risk"
            ),
        }
        source_conditions = {
            "scenario": asdict(scenario),
            "transport": asdict(self.transport),
            "parent": self.case_sha256,
            "layout": self.layout_sha256,
            "source_policy": self.source_policy,
            "radiation_method": self.radiation_method,
            "code": self.code_version,
        }
        if stop_at_extinction:
            source_conditions["stop_at_extinction"] = True
        diagnostics.update(
            simulated_until_s=float(state.time_s),
            reached_requested_horizon=complete,
            invalid_reasons=invalid,
            source_verified=source_verified,
            parent_case_unchanged=request_fingerprint(self.bundle.request) == self.case_sha256,
            physical_request_sha256=request_fingerprint(request),
            inventory_sha256=self.inventory_sha256,
            layout_sha256=self.layout_sha256,
            source_conditions_sha256=content_hash(source_conditions),
            source_policy=self.source_policy,
            ordinary_overlap_identity_unresolved=bool(layout.ambiguous_ids),
            stop_at_first_core_ignition=stop_at_first_core_ignition,
            partition_conservation_max_abs_error=layout.conservation,
            contact_enabled=contact is not None,
            contact_edge_count=0 if contact is None else int((contact.neighbors >= 0).sum()),
            radiation_edge_count=len(request.exposure_edges.source),
            radiation_method=self.radiation_method,
            roof_resolution_protocol=getattr(layout, "resolution_protocol", None),
            maximum_valid_surface_temperature_by_node_K=max_temperature.tolist(),
        )
        if stop_at_extinction:
            diagnostics.update(
                stop_at_extinction=True,
                protection_horizon_closed=bool(goal_closed and not invalid),
                effective_goal_closed=bool(goal_closed and not invalid),
                extinction_closed=closure_certificate is not None,
                temporal_closure_certificate=closure_certificate,
                temporal_closure_rejection_reasons=closure_refusal,
            )
        if not diagnostics["parent_case_unchanged"]:
            raise AssertionError("roof evaluation mutated parent case")
        signature = content_hash(
            {
                "conditions": diagnostics["source_conditions_sha256"],
                "plan": plan.to_mapping(),
                "request": diagnostics["physical_request_sha256"],
                "early_stop": stop_at_first_core_ignition,
            }
        )
        record = EvaluationResult(
            "heritage-roof-evaluation-1",
            self.case_sha256,
            signature,
            self.capabilities["engine"],
            scenario.scenario_id,
            plan.plan_id,
            scenario.horizon_s,
            metrics,
            ledger,
            diagnostics,
            dict(self.capabilities),
        )
        return record, layout, result

    def evaluate(self, scenario, plan, **kwargs):
        return self.simulate(scenario, plan, **kwargs)[0]
