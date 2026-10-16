"""Verified transparent-point V3 adapter for hypothetical vegetation clearing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from time import perf_counter

import numpy as np

from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPlan, prepare_clearing
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

CAPABILITIES = {
    "engine": "effective_exposure_ca_v3",
    "thermal_state": True,
    "vegetation_clearance": True,
    "partial_cells": "homogeneous_fuel_scaling",
    "cross_strip_radiation": True,
    "opaque_wall": False,
    "spotting": False,
    "dynamic_wind": False,
    "construction_cost_model": False,
    "global_optimality_guaranteed": False,
}


def request_fingerprint(request):
    digest = hashlib.sha256()
    digest.update(json.dumps(asdict(request.grid), sort_keys=True).encode())
    digest.update(json.dumps(asdict(request.scenario), sort_keys=True).encode())
    digest.update(
        json.dumps(
            request.flame_contact.to_mapping() if request.flame_contact else None, sort_keys=True
        ).encode()
    )
    arrays = {
        name: getattr(request, name)
        for name in (
            "domain_mask",
            "known_mask",
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
            "ground_elevation_m",
            "material_evidence_index",
        )
    }
    arrays.update(
        {
            "physics_" + name: getattr(request.parameters, name)
            for name in request.parameters.__dataclass_fields__
        }
    )
    arrays.update(
        {
            "edge_" + name: getattr(request.exposure_edges, name)
            for name in ("source", "target", "weight")
        }
    )
    for name, value in arrays.items():
        digest.update(name.encode())
        if value is not None:
            array = np.ascontiguousarray(value)
            digest.update(str(array.dtype).encode())
            digest.update(str(array.shape).encode())
            digest.update(array.tobytes())
    digest.update(str(request.sample_interval_s).encode())
    digest.update(request.unknown_policy.encode())
    return digest.hexdigest()


class VillageEvaluationEngine:
    capabilities = CAPABILITIES

    def __init__(
        self, bundle, transport, *, zones=None, structure_types=None, code_version="unspecified"
    ):
        self.bundle, self.transport = bundle, transport
        self.zones = {"vegetation": np.ones(bundle.request.grid.shape, dtype=bool)}
        self.zones.update({key: np.array(value, copy=True) for key, value in (zones or {}).items()})
        for value in self.zones.values():
            if value.dtype != np.bool_ or value.shape != bundle.request.grid.shape:
                raise ValueError("construction zone must be boolean and match grid")
        self.structure_types = dict(structure_types or {})
        self.code_version = code_version
        self.case_sha256 = request_fingerprint(bundle.request)
        # This adapter supports precisely the verified parent graph. No silent
        # rebuilding of a finite-area, opaque or legacy incorrect graph.
        request = bundle.request
        verified = build_v3_radiative_transport_edges(
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
            not np.array_equal(getattr(verified, key), getattr(request.exposure_edges, key))
            for key in ("source", "target", "weight")
        ):
            raise ValueError("unsupported parent radiation graph: transparent-point match required")

    def prepare(self, scenario, intervention):
        if not isinstance(scenario, EvaluationScenario):
            raise TypeError("scenario must be EvaluationScenario")
        if not isinstance(intervention, ClearingPlan):
            raise TypeError("only explicit vegetation-clearing plans are supported")
        if intervention.zone_id not in self.zones:
            raise ValueError("unknown construction zone")
        parent = self.bundle.request
        events = tuple(
            VillageIgnitionEvent(i.time_s, *parent.grid.locate(*i.point_m))
            for i in scenario.ignitions
        )
        # Select the evaluation's ignition before applying an independent overlay.
        request = replace(
            parent,
            scenario=replace(parent.scenario, end_time_s=scenario.horizon_s, ignitions=events),
        )
        return prepare_clearing(
            request,
            intervention,
            self.transport,
            allowed_mask=self.zones[intervention.zone_id],
            ignitions=events,
        )

    def evaluate(self, scenario, intervention):
        started = perf_counter()
        request, _fraction, treatment = self.prepare(scenario, intervention)
        result = run_v3_simulation(request, retain_snapshots=False)
        diagnostics = check_final(request, result)
        parent = self.bundle.request
        state = result.final_state
        ignited = np.isfinite(state.ignition_time_s)
        buildings = parent.building_index >= 0
        structure_ids = np.unique(parent.building_index[buildings])
        affected, first = [], []
        for bid in structure_ids:
            mask = buildings & (parent.building_index == bid)
            if not ignited[mask].any():
                continue
            minimum = float(state.ignition_time_s[mask & ignited].min())
            category = self.structure_types.get(int(bid), "building")
            affected.append(category)
            first.append({"building_index": int(bid), "source_label": category, "time_s": minimum})
        first.sort(key=lambda item: item["time_s"])
        house_times = [item["time_s"] for item in first if item["source_label"] == "building"]
        metrics = {
            "protected_structure_count": int(structure_ids.size),
            "ignited_structure_count": len(first),
            "ignited_house_count": affected.count("building"),
            "ignited_bridge_count": affected.count("fengyuqiao"),
            "ignited_building_area_m2": float(
                parent.parameters.covered_area_m2[buildings & ignited].sum()
            ),
            "first_house_ignition_s": min(house_times) if house_times else None,
            "house_ignition_free_time_s": min(house_times) if house_times else scenario.horizon_s,
            "ignited_cells": int(ignited.sum()),
            "burned_out_cells": int((state.state == 4).sum()),
            "consumed_fuel_kg": result.consumed_fuel_kg,
            "building_first_ignitions": first,
            "burning_reached_domain_edge": bool(
                ignited[[0, -1], :].any() or ignited[:, [0, -1]].any()
            ),
        }
        digest = hashlib.sha256()
        for item in (
            self.case_sha256,
            self.code_version,
            asdict(self.transport),
            asdict(scenario),
            intervention.to_mapping(),
            self.structure_types,
        ):
            digest.update(json.dumps(item, sort_keys=True).encode())
        digest.update(self.zones[intervention.zone_id].tobytes())
        diagnostics.update(
            elapsed_s=perf_counter() - started,
            code_version=self.code_version,
            parent_case_unchanged=request_fingerprint(parent) == self.case_sha256,
        )
        if not diagnostics["parent_case_unchanged"]:
            raise AssertionError("evaluation mutated its parent case")
        return EvaluationResult(
            "intervention-evaluation-1",
            self.case_sha256,
            digest.hexdigest(),
            CAPABILITIES["engine"],
            scenario.scenario_id,
            intervention.plan_id,
            scenario.horizon_s,
            metrics,
            treatment,
            diagnostics,
            dict(CAPABILITIES),
        )
