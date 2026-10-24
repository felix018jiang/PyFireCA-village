"""V3 evaluation of a declared demolition plan against fixed heritage objects."""

from __future__ import annotations

from dataclasses import asdict, replace
from time import perf_counter
from typing import ClassVar

import numpy as np
from shapely.geometry import Point

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import prepare_building_demolition
from pyfireca.planning.observations import build_object_observations
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges


class HeritageEvaluationEngine:
    capabilities: ClassVar[dict] = {
        "engine": "heritage_demolition_ca_v3_p1",
        "whole_building_demolition": True,
        "fixed_core_targets": True,
        "fixed_internal_sources": True,
        "post_demolition_ground": "explicit_cleared_inert_only",
        "independent_subcell_temperatures": False,
        "opaque_wall": False,
        "spotting": False,
        "dynamic_wind": False,
        "optimization": False,
    }

    def __init__(self, bundle, transport, inventory, *, code_version="heritage-p1-1"):
        if not inventory.targets or not inventory.fixed_source_ids:
            raise ValueError("fixed heritage targets and ordinary source IDs are required")
        if bundle.request.firebrands is not None:
            raise ValueError("P1 heritage demolition does not support firebrands")
        if any(inventory.by_id[i].source_label != "building" for i in inventory.fixed_source_ids):
            raise ValueError("fixed internal sources must be ordinary buildings")
        self.bundle, self.transport, self.inventory = bundle, transport, inventory
        self.code_version = code_version
        self.case_sha256 = request_fingerprint(bundle.request)
        self.inventory_sha256 = inventory.sha256
        self.observer = build_object_observations(
            bundle.request, inventory.structures, inventory.labels
        )
        request = bundle.request
        graph = build_v3_radiative_transport_edges(
            request.grid,
            request.source_mask,
            request.target_mask,
            request.parameters.receiver_area_m2,
            request.parameters.source_height_m,
            request.parameters.receiver_height_m,
            np.ones(request.grid.shape),
            transport,
            ground_elevation_m=request.ground_elevation_m,
        )
        if any(
            not np.array_equal(getattr(graph, key), getattr(request.exposure_edges, key))
            for key in ("source", "target", "weight")
        ):
            raise ValueError("unsupported parent graph: transparent-point match required")
        by_label = {obj.label: obj for obj in self.observer.objects}
        for target in inventory.targets:
            if not request.burnable_mask.ravel()[by_label[target.building_id].cell_ids].all():
                raise ValueError("core geometry must remain represented by burnable building cells")

    def prepare(self, scenario, plan):
        if not isinstance(scenario, EvaluationScenario):
            raise TypeError("scenario must be EvaluationScenario")
        if self.inventory.sha256 != self.inventory_sha256:
            raise ValueError("frozen inventory changed")
        parent = self.bundle.request
        if request_fingerprint(parent) != self.case_sha256:
            raise ValueError("parent case changed")
        events, source_ids = [], set()
        for ignition in scenario.ignitions:
            if ignition.time_s != 0:
                raise ValueError("P1 fixed internal sources must ignite at time zero")
            row, col = parent.grid.locate(*ignition.point_m)
            objects = self.observer.objects_at_cell(row * parent.grid.width + col)
            if len(objects) != 1:
                raise ValueError("source requires an unambiguous single-building cell")
            identifier = self.inventory.labels[str(objects[0])]
            if identifier not in self.inventory.fixed_source_ids:
                raise ValueError("ignition is not a declared fixed ordinary source")
            if not self.inventory.by_id[identifier].geometry.covers(Point(ignition.point_m)):
                raise ValueError("ignition point is outside the actual source outline")
            source_ids.add(identifier)
            events.append(VillageIgnitionEvent(0, row, col))
        request = replace(
            parent,
            scenario=replace(
                parent.scenario, end_time_s=float(scenario.horizon_s), ignitions=tuple(events)
            ),
        )
        changed, observer, ledger = prepare_building_demolition(
            request, plan, self.inventory, self.transport
        )
        return changed, observer, {**ledger, "actual_source_ids": sorted(source_ids)}

    def simulate(self, scenario, plan, *, stop_at_first_core_ignition=False):
        """Return evaluation, physical request and native result for audited exports."""
        started = perf_counter()
        request, observer, ledger = self.prepare(scenario, plan)
        target_ids = {t.building_id for t in self.inventory.targets}
        targets = [obj for obj in observer.objects if obj.label in target_ids]
        core_cells = np.unique(np.concatenate([obj.cell_ids for obj in targets]))
        counts = np.zeros(request.grid.height * request.grid.width, dtype=np.int32)
        for obj in observer.objects:
            counts[obj.cell_ids] += 1
        shared_targets = [obj.label for obj in targets if np.any(counts[obj.cell_ids] > 1)]

        def core_failed(state):
            return bool(np.isfinite(state.ignition_time_s.ravel()[core_cells]).any())

        result = run_v3_simulation(
            request,
            retain_snapshots=False,
            stop_when=core_failed if stop_at_first_core_ignition else None,
        )
        diagnostics = check_final(request, result)
        state = result.final_state
        first = observer.first_ignitions(state.ignition_time_s)
        times = {row["object_label"]: row["time_s"] for row in first}
        complete = bool(state.time_s >= scenario.horizon_s - 1e-8)
        ignited = np.isfinite(state.ignition_time_s)
        boundary = bool(ignited[[0, -1], :].any() or ignited[:, [0, -1]].any())
        actual_sources = ledger["actual_source_ids"]
        source_verified = all(times.get(identifier) == 0 for identifier in actual_sources)
        failed_ids = sorted(target_ids & times.keys())
        invalid = []
        if shared_targets:
            invalid.append("core_objects_share_temperature_cells")
        if boundary:
            invalid.append("fire_reached_domain_edge")
        if not source_verified:
            invalid.append("declared_source_not_ignited")
        status = (
            "invalid"
            if invalid
            else "failed"
            if failed_ids
            else "passed"
            if complete
            else "unknown"
        )
        retained_ordinary = sorted(
            obj.label
            for obj in observer.objects
            if obj.source_label == "building" and obj.label not in target_ids
        )
        affected_ordinary = sorted(set(retained_ordinary) & times.keys())
        metrics = {
            "protection_status": status,
            "protection_goal_met": status == "passed",
            "core_joint_failure_observed": bool(failed_ids),
            "core_count": len(target_ids),
            "ignited_core_ids": failed_ids,
            "core_outcomes": [
                {
                    **asdict(t),
                    "ignited": t.building_id in times,
                    "first_ignition_s": times.get(t.building_id),
                    "shared_temperature_cells": int(sum(counts[obj.cell_ids] > 1)),
                }
                for t in self.inventory.targets
                for obj in targets
                if obj.label == t.building_id
            ],
            "actual_source_ids": actual_sources,
            "retained_ordinary_ids": retained_ordinary,
            "ignited_retained_ordinary_ids": affected_ordinary,
            "ignited_secondary_ordinary_ids": sorted(set(affected_ordinary) - set(actual_sources)),
            "building_first_ignitions": first,
            "ignited_building_cell_area_m2": float(
                request.parameters.covered_area_m2[ignited & (request.building_index >= 0)].sum()
            ),
            "consumed_fuel_kg": result.consumed_fuel_kg,
            "burning_reached_domain_edge": boundary,
            "object_observation": observer.summary(),
            "interpretation": "fixed-source finite-horizon ignition; not building destruction",
        }
        diagnostics.update(
            simulated_until_s=float(state.time_s),
            reached_requested_horizon=complete,
            invalid_reasons=invalid,
            source_verified=source_verified,
            stop_at_first_core_ignition=stop_at_first_core_ignition,
            parent_case_unchanged=request_fingerprint(self.bundle.request) == self.case_sha256,
            physical_request_sha256=request_fingerprint(request),
            inventory_sha256=self.inventory_sha256,
            observation_sha256=observer.sha256,
            source_conditions_sha256=content_hash(
                {
                    "scenario": asdict(scenario),
                    "transport": asdict(self.transport),
                    "code_version": self.code_version,
                }
            ),
            code_version=self.code_version,
            elapsed_s=perf_counter() - started,
        )
        if not diagnostics["parent_case_unchanged"]:
            raise AssertionError("demolition evaluation mutated its parent")
        signature = content_hash(
            {
                "parent": self.case_sha256,
                "request": diagnostics["physical_request_sha256"],
                "inventory": self.inventory_sha256,
                "observer": observer.sha256,
                "scenario": asdict(scenario),
                "plan": plan.to_mapping(),
                "transport": asdict(self.transport),
                "code": self.code_version,
                "stop_at_first_core_ignition": stop_at_first_core_ignition,
            }
        )
        record = EvaluationResult(
            "heritage-evaluation-1",
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
        return record, request, result

    def evaluate(self, scenario, plan, *, stop_at_first_core_ignition=False):
        return self.simulate(
            scenario, plan, stop_at_first_core_ignition=stop_at_first_core_ignition
        )[0]


def compare_retained_ordinary(baseline, treated):
    """Compare the treated surviving cohort against the same baseline IDs."""
    if (
        (baseline.case_sha256, baseline.scenario_id, baseline.horizon_s)
        != (treated.case_sha256, treated.scenario_id, treated.horizon_s)
        or baseline.diagnostics["inventory_sha256"] != treated.diagnostics["inventory_sha256"]
        or baseline.metrics["actual_source_ids"] != treated.metrics["actual_source_ids"]
        or baseline.diagnostics["source_conditions_sha256"]
        != treated.diagnostics["source_conditions_sha256"]
        or baseline.intervention["demolished_count"] != 0
    ):
        raise ValueError("comparison requires matched no-demolition baseline and source conditions")
    for record in (baseline, treated):
        if (
            not record.diagnostics["reached_requested_horizon"]
            or record.metrics["protection_status"] == "invalid"
        ):
            raise ValueError("comparison requires full-horizon valid observations")
    cohort = set(treated.metrics["retained_ordinary_ids"])
    before = cohort & set(baseline.metrics["ignited_retained_ordinary_ids"])
    after = cohort & set(treated.metrics["ignited_retained_ordinary_ids"])
    return {
        "cohort_ids": sorted(cohort),
        "cohort_count": len(cohort),
        "baseline_ignited_ids": sorted(before),
        "treated_ignited_ids": sorted(after),
        "newly_ignited_ids": sorted(after - before),
        "avoided_ignited_ids": sorted(before - after),
        "planned_demolished_ids": treated.intervention["demolished_ids"],
        "affected_household_count": None,
    }
