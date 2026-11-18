"""Independent prototype for roof AND-actions plus whole 5 m ground actions.

This module is not connected to a producer. Its certificates have a distinct
capability seal/schema and are never passed to the whole-building cut reader.
The action universe, physical partition and thermal integration membership
must be frozen before any plan is evaluated. Subset grass catalogs prove only
their registered catalog; they do not imply all-ground-cell optimality.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property, partial
from math import isfinite

import numpy as np
from shapely import STRtree

from pyfireca.planning.area_candidates import ClearedAreaFunction, build_cleared_area_function
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.clearing import EXTENSIVE
from pyfireca.planning.roof_ignition import refined_contact_operator
from pyfireca.planning.roof_thermal import roof_area_integrated_radiation_graph
from pyfireca.planning.source_area_coverage import cover_source_priority_by_area
from pyfireca.village.physics_v2 import monotone_thermal_timestep_limit_s

JOINT_CUT_CAPABILITIES = {
    "fixed_physical_partition": True,
    "frozen_radiation_integration_membership": True,
    "owner_labels_are_thermal_metadata": True,
    "action_types": ["whole_roof_owner_AND", "whole_known_burnable_nonroof_5m_parent_cell"],
    "potential_receivers_including_warm_nonburners_guarded": True,
    "cold_global_timestep_limiting_nodes_guarded": True,
    "contact_operator_presence_fixed_by_parent_parameters_even_when_edge_empty": True,
    "all_ignitions_at_zero_on_one_roof_source": True,
    "wind_speed_m_s": 0.0,
    "transparent_radiation_without_occlusion_or_renormalization": True,
    "firebrands": False,
    "source_guard": "superset_of_prior_actions_and_singleton_source_or_fixed_source",
}
CUT_SCHEMA = "heritage-joint-physical-node-removal-cut-1"


def _ids(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("action IDs must be a collection")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError("unique nonempty action IDs required")
    return tuple(sorted(values))


def _area_mapping(area):
    return [{"owners": sorted(g), "area_m2": a} for g, a in area.owner_groups]


@dataclass(frozen=True)
class JointActionUniverse:
    roof_action_ids: tuple[str, ...]
    grass_cell_ids: tuple[int, ...]
    node_groups: tuple[tuple[str, ...], ...]
    original_node_owners: tuple[tuple[str, ...], ...]
    physical_cell_ids: tuple[int, ...]
    component_ids: tuple[str, ...]
    geometry_sha256: str
    inventory_sha256: str
    roof_area: ClearedAreaFunction
    area: ClearedAreaFunction
    grass_scope: str
    eligible_grass_cell_count: int

    @cached_property
    def candidate_ids(self):
        return tuple(sorted((*self.roof_action_ids, *(f"G{i}" for i in self.grass_cell_ids))))

    @cached_property
    def candidate_set(self):
        return frozenset(self.candidate_ids)

    @cached_property
    def sha256(self):
        return content_hash(self.to_mapping())

    @cached_property
    def physical_partition_sha256(self):
        return content_hash(
            {
                "logical_joint_universe_sha256": self.sha256,
                "node_groups": self.node_groups,
                "original_node_owners": self.original_node_owners,
                "physical_cell_ids": self.physical_cell_ids,
                "component_ids": self.component_ids,
                "geometry_sha256": self.geometry_sha256,
            }
        )

    def to_mapping(self):
        return {
            "schema_version": "heritage-joint-action-universe-prototype-1",
            "roof_action_ids": list(self.roof_action_ids),
            "grass_cell_ids": list(self.grass_cell_ids),
            "inventory_sha256": self.inventory_sha256,
            "construction_area_groups": _area_mapping(self.area),
            "construction_area_basis": self.area.area_basis,
            "grass_scope": self.grass_scope,
            "eligible_grass_cell_count": self.eligible_grass_cell_count,
            "global_physical_optimality_proven": False,
        }

    def canonical_actions(self, actions):
        actions = _ids(actions)
        if not set(actions) <= self.candidate_set:
            raise ValueError("action is outside the frozen joint catalog")
        return actions

    def plan(self, actions, *, plan_id="prototype"):
        actions = self.canonical_actions(actions)
        roof = tuple(i for i in actions if i in self.roof_action_ids)
        grass = tuple(sorted(int(i[1:]) for i in actions if i not in self.roof_action_ids))
        return RoofJointPlan(
            plan_id,
            roof,
            ground_evidence="synthetic_or_registered_model_actions_only",
            overlap_policy="retain_shared_roof",
            cleared_physical_cell_ids=grass,
        )


def build_joint_action_universe(
    layout, inventory, *, grass_cell_ids=None, construction_layout=None
):
    """Register all roof actions and all, or an explicit subset of, eligible ground.

    Ground cost is the complete 25 m² parent footprint even if its fuel coverage
    is partial. Any parent cell intersecting a roof is excluded. The factory
    rejects future split/nonuniform ground representations instead of guessing.
    """
    parent = layout.physical_request
    construction_layout = layout if construction_layout is None else construction_layout
    if (
        construction_layout.physical_request is not parent
        or construction_layout.inventory_sha256 != inventory.sha256
        or construction_layout.node_request is not None
    ):
        raise ValueError("canonical construction layout must be unbound on the same parent request")
    if parent.grid.cell_size_m != 5:
        raise ValueError("joint ground universe requires the original 5m parent grid")
    owners = tuple(tuple(g) for g in layout.owners)
    cells = np.asarray(layout.cell_id)
    if layout.inventory_sha256 != inventory.sha256:
        raise ValueError("layout inventory differs")
    mask = (
        (
            parent.domain_mask
            & parent.known_mask
            & parent.burnable_mask
            & (parent.building_index < 0)
            & (parent.parameters.covered_area_m2 > 0)
        )
        .ravel()
        .copy()
    )
    mask[np.unique(cells[[bool(g) for g in owners]])] = False
    eligible = set(np.flatnonzero(mask).tolist())
    requested = None if grass_cell_ids is None else tuple(grass_cell_ids)
    if requested is not None and len(set(requested)) != len(requested):
        raise ValueError("duplicate grass cell IDs must not change the registered catalog")
    selected = eligible if requested is None else set(requested)
    if any(isinstance(i, bool) or not isinstance(i, (int, np.integer)) for i in selected):
        raise ValueError("ground actions require integer parent cell IDs")
    if not selected <= eligible:
        raise ValueError("ground action is not known burnable whole nonroof parent geometry")
    selected = tuple(sorted(int(i) for i in selected))
    selected_set = set(selected)
    counts = np.bincount(cells, minlength=parent.grid.height * parent.grid.width)
    if any(counts[i] != 1 for i in selected):
        raise ValueError("ground action must map to one unsplit complete parent node")
    grass_ids = {f"G{i}" for i in selected}
    if grass_ids & inventory.by_id.keys():
        raise ValueError("ground action namespace collides with inventory identities")
    groups = tuple(
        g if g else (f"G{int(c)}",) if int(c) in selected_set else ()
        for c, g in zip(cells, owners, strict=True)
    )
    roof = build_cleared_area_function(
        construction_layout.owners,
        [g.area for g in construction_layout.geometry],
        area_basis="disjoint_roof_geometry_AND",
    )
    area = ClearedAreaFunction(
        (*roof.owner_groups, *((frozenset((f"G{i}",)), 25.0) for i in selected)),
        "disjoint_roof_geometry_AND_plus_whole_5m_nonroof_footprints",
        roof.physical_patch_count + len(selected),
    )
    return JointActionUniverse(
        tuple(sorted(inventory.candidate_ids)),
        selected,
        groups,
        owners,
        tuple(int(i) for i in cells),
        tuple(layout.component_id),
        content_hash([g.wkb_hex for g in layout.geometry]),
        inventory.sha256,
        roof,
        area,
        "all_eligible_parent_cells" if grass_cell_ids is None else "explicit_subset",
        len(eligible),
    )


@dataclass(frozen=True)
class JointSourceCoverage:
    universe: JointActionUniverse
    roof_coverage: object

    def validate_actions(self, actions):
        actions = self.universe.canonical_actions(actions)
        total = self.universe.area(actions)
        if total > self.roof_coverage.admissible_area_limit_m2:
            raise ValueError("joint plan exceeds the same total-area source cap")
        roof_ids = tuple(i for i in actions if i in self.universe.roof_action_ids)
        value = self.roof_coverage.validate_plan(roof_ids, cleared_area=self.universe.roof_area)
        return {**value, "joint_construction_area_m2": total}

    def to_mapping(self):
        return {
            "schema_version": "heritage-joint-source-area-coverage-prototype-1",
            "joint_universe_sha256": self.universe.sha256,
            "roof_coverage": self.roof_coverage.to_mapping(),
            "proof": "roof_C<=joint_C;total_cap_guard_implies_same_roof_prefix_bound",
            "cap_is_verified_feasible_upper_bound": False,
        }


def joint_source_area_coverage(universe, draw, inventory, cap_m2, *, tolerance_m2=0):
    if universe.inventory_sha256 != inventory.sha256:
        raise ValueError("joint source proof inventory differs")
    proof = cover_source_priority_by_area(
        draw,
        inventory,
        universe.roof_action_ids,
        universe.roof_area,
        cap_m2,
        area_bound_tolerance_m2=tolerance_m2,
    )
    return JointSourceCoverage(universe, proof)


def joint_cold_removal_certificate(
    engine, universe, actions, record, layout, result, *, problem_sha256, source_id
):
    """Certify a failed joint trajectory without assuming fire monotonicity.

    Removing only cold non-influential nodes preserves retained trajectories.
    All geometrically possible receivers, including warm unignited grass, and
    cold nodes that can determine the shared thermal step are escape literals.
    The caller must use this same frozen engine/source/mesh for later plans.
    """
    if not isinstance(engine, RoofJointEvaluationEngine):
        raise TypeError("the independent joint engine is required")
    builder = engine.radiation_builder
    if (
        not isinstance(builder, partial)
        or builder.func is not roof_area_integrated_radiation_graph
        or engine.contact_builder is not refined_contact_operator
    ):
        return None
    membership = builder.keywords.get("frozen_integrated_node_mask")
    if (
        not isinstance(membership, np.ndarray)
        or membership.dtype != bool
        or membership.shape != (len(universe.node_groups),)
        or membership.flags.writeable
    ):
        return None
    request, state = layout.node_request, result.final_state
    actual_core_failures = sorted(
        t.building_id
        for t in engine.inventory.targets
        if np.isfinite(state.ignition_time_s.ravel()[layout.nodes(t.building_id)]).any()
    )
    if (
        record.case_sha256 != engine.case_sha256
        or record.horizon_s != request.scenario.end_time_s
        or record.diagnostics["simulated_until_s"] != state.time_s
        or sorted(record.metrics["ignited_core_ids"]) != actual_core_failures
        or record.metrics["actual_source_ids"] != [source_id]
    ):
        raise ValueError("joint certificate record/source/state identity differs")
    if (
        engine.transport.wind_speed_m_s != 0
        or request.firebrands is not None
        or any(e.time_s != 0 for e in request.scenario.ignitions)
        or record.diagnostics["invalid_reasons"]
        or not actual_core_failures
    ):
        return None
    if (
        tuple(layout.cell_id) != universe.physical_cell_ids
        or tuple(layout.component_id) != universe.component_ids
        or content_hash([g.wkb_hex for g in layout.geometry]) != universe.geometry_sha256
    ):
        raise ValueError("physical partition changed after joint registration")
    actions = universe.canonical_actions(actions)
    removed, candidates = set(actions), universe.candidate_set
    original = layout.node_inputs.parameters
    clear = np.asarray([bool(g) and set(g) <= removed for g in universe.node_groups])
    positive = original.covered_area_m2.ravel() > 0
    if not np.array_equal(
        (request.parameters.covered_area_m2.ravel() == 0) & positive, clear & positive
    ):
        raise ValueError("joint action-to-node clearance does not match the prepared request")
    for name in original.__dataclass_fields__:
        if not np.array_equal(
            getattr(original, name).ravel()[~clear],
            getattr(request.parameters, name).ravel()[~clear],
        ):
            raise ValueError("retained thermal parameters changed")
        if name in EXTENSIVE and np.any(getattr(request.parameters, name).ravel()[clear] != 0):
            raise ValueError("cleared physical nodes retain extensive fuel or area")
    for name in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask"):
        actual, expected = getattr(request, name).ravel(), getattr(layout.node_inputs, name).ravel()
        if not np.array_equal(actual[~clear], expected[~clear]) or np.any(actual[clear]):
            raise ValueError("joint interventions changed retained thermal masks")
    if any(
        not clear[node] and tuple(sorted(set(before) - removed)) != tuple(after)
        for node, (before, after) in enumerate(
            zip(universe.original_node_owners, layout.owners, strict=True)
        )
    ):
        raise ValueError("current roof owner metadata does not match prior actions")
    if source_id in removed or source_id not in engine.inventory.by_id:
        return None
    direct = [e.row * request.grid.width + e.col for e in request.scenario.ignitions]
    if not direct or not set(direct) <= set(layout.nodes(source_id)):
        return None
    burned = np.flatnonzero(np.isfinite(state.ignition_time_s.ravel()))
    zone = np.zeros(len(universe.node_groups), bool)
    zone[burned] = True
    tree = STRtree(layout.geometry)
    for start in range(0, len(burned), 256):
        hits = tree.query(
            [layout.geometry[i] for i in burned[start : start + 256]],
            predicate="dwithin",
            distance=engine.transport.exposure_radius_m,
        )
        zone[hits[1]] = True
    contact = engine.last_contact
    if contact is not None:
        targets = contact.neighbors[burned]
        zone[targets[targets >= 0]] = True
    remaining = [tuple(sorted(set(g) - removed)) for g in universe.node_groups]
    live = request.burnable_mask.ravel() & (request.parameters.covered_area_m2.ravel() > 0)
    cold = ~zone & live & np.asarray([bool(g) and set(g) <= candidates for g in remaining])
    for name in ("incident_radiation_J", "received_convection_J", "released_energy_J"):
        if np.any(getattr(state, name).ravel()[cold] != 0):
            raise ValueError("cold-node joint proof contradicted by heat")
    if np.any(state.surface_temperature_K.ravel()[cold] != request.scenario.ambient_temperature_K):
        raise ValueError("cold-node joint proof contradicted by warm temperatures")
    limit = None
    if cold.any():
        p = request.parameters

        def field(name):
            return getattr(p, name).ravel()[cold]

        limit = monotone_thermal_timestep_limit_s(
            np.full(int(cold.sum()), request.scenario.ambient_temperature_K),
            field("effective_heated_dry_mass_kg") / field("covered_area_m2"),
            field("specific_heat_J_kg_K"),
            field("emissivity"),
            field("ambient_heat_transfer_coefficient_W_m2_K")
            + (contact.parameters.heat_transfer_coefficient_W_m2_K if contact else 0),
            field("backing_conductance_W_m2_K"),
        )
    dt_guard = limit is not None and limit < request.scenario.max_dt_s
    guarded = (zone | (cold if dt_guard else False)) & live
    grouped_nodes = {}
    for node in np.flatnonzero(guarded).tolist():
        group = remaining[node]
        if group:
            grouped_nodes.setdefault(group, []).append(node)
    groups = sorted(grouped_nodes)
    if source_id in candidates and (source_id,) not in groups:
        return None
    value = {
        "schema_version": CUT_SCHEMA,
        "problem_sha256": problem_sha256,
        "joint_universe_sha256": universe.sha256,
        "physical_partition_sha256": universe.physical_partition_sha256,
        "scenario_id": record.scenario_id,
        "required_prior_action_ids": list(actions),
        "required_clearance_action_groups": [list(g) for g in groups],
        "physical_group_witnesses": [
            {
                "remaining_action_group": list(group),
                "positive_burnable_node_count": len(grouped_nodes[group]),
                "node_indices_sha256": content_hash(grouped_nodes[group]),
            }
            for group in groups
        ],
        "source_id": source_id,
        "native_case_sha256": record.case_sha256,
        "native_evaluation_sha256": record.evaluation_sha256,
        "native_physical_request_sha256": record.diagnostics["physical_request_sha256"],
        "failed_at_s": state.time_s,
        "physical_core_failure_observed": True,
        "frozen_integration_membership_sha256": content_hash(membership.tolist()),
        "potential_receiver_node_count": int(zone.sum()),
        "guarded_positive_burnable_node_count": int(guarded.sum()),
        "warm_unignited_receiver_node_count": int(
            (
                zone
                & live
                & ~np.isfinite(state.ignition_time_s.ravel())
                & (state.surface_temperature_K.ravel() > request.scenario.ambient_temperature_K)
            ).sum()
        ),
        "cold_node_thermal_limit_s": float(limit)
        if limit is not None and isfinite(limit)
        else None,
        "cold_global_timestep_nodes_guarded": bool(dt_guard),
        "capabilities": deepcopy(JOINT_CUT_CAPABILITIES),
        "capabilities_sha256": content_hash(JOINT_CUT_CAPABILITIES),
        "proof_scope": (
            "same_frozen_joint_engine_partition_source_and_clock;not_general_fire_monotonicity"
        ),
    }
    value["certificate_sha256"] = content_hash(value)
    return value


def validate_joint_cut(
    value, universe, *, problem_sha256, scenario_id, actions, physical_partition_sha256=None
):
    raw = {k: v for k, v in value.items() if k != "certificate_sha256"}
    actions = universe.canonical_actions(actions)
    groups = [tuple(_ids(g)) for g in value.get("required_clearance_action_groups", ())]
    known = set(universe.area.owner_ids)
    if (
        value.get("schema_version") != CUT_SCHEMA
        or value.get("certificate_sha256") != content_hash(raw)
        or value.get("problem_sha256") != problem_sha256
        or value.get("scenario_id") != scenario_id
        or value.get("joint_universe_sha256") != universe.sha256
        or value.get("physical_partition_sha256")
        != (
            universe.physical_partition_sha256
            if physical_partition_sha256 is None
            else physical_partition_sha256
        )
        or value.get("required_prior_action_ids") != list(actions)
        or value.get("capabilities") != JOINT_CUT_CAPABILITIES
        or value.get("capabilities_sha256") != content_hash(JOINT_CUT_CAPABILITIES)
        or value.get("physical_core_failure_observed") is not True
        or not groups
        or len(set(groups)) != len(groups)
        or any(not g or not set(g) <= known or set(g) & set(actions) for g in groups)
        or value.get("source_id") in actions
        or value.get("source_id") not in known
        or (
            value.get("source_id") in universe.candidate_ids and (value["source_id"],) not in groups
        )
    ):
        raise ValueError("joint cut does not bind its independent physical capability and failure")
    witnesses = value.get("physical_group_witnesses", ())
    if len(witnesses) != len(groups) or any(
        w.get("remaining_action_group") != list(g)
        or isinstance(w.get("positive_burnable_node_count"), bool)
        or not isinstance(w.get("positive_burnable_node_count"), int)
        or w["positive_burnable_node_count"] <= 0
        or not w.get("node_indices_sha256")
        for g, w in zip(groups, witnesses, strict=True)
    ):
        raise ValueError("joint cut lacks positive physical-node witnesses")
    for name in (
        "native_case_sha256",
        "native_evaluation_sha256",
        "native_physical_request_sha256",
        "frozen_integration_membership_sha256",
    ):
        digest = value.get(name)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("joint cut lacks native input/evaluation/integration fingerprints")
    return groups


def joint_cut_forbids(value, actions):
    """Logical predicate for an already validated certificate, including A subset."""
    actions = set(actions)
    return set(value["required_prior_action_ids"]) <= actions and not any(
        set(g) <= actions for g in value["required_clearance_action_groups"]
    )


def solve_joint_action_master(
    universe,
    scenario_ids,
    *,
    problem_sha256,
    cache=None,
    area_cap_m2=None,
    time_limit_s=None,
    scenario_partition_sha256s=None,
    max_matrix_nonzeros=5_000_000,
):
    """Small sparse MIP prototype; native CA alone establishes feasibility.

    A single t=sum(x) equality makes every exact failed-plan no-good sparse:
    t-2*sum(x_i for i in A)>=1-|A|. Known passed plans stay in the master;
    they can meet its lower bound instead of receiving dense exclusions.
    No caller may transfer a roof-only lower bound into this joint model.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import csc_matrix

    candidates, scenarios = universe.candidate_ids, _ids(scenario_ids)
    if not scenarios:
        raise ValueError("declared joint scenarios required")
    if not isinstance(problem_sha256, str) or not problem_sha256:
        raise ValueError("frozen joint problem fingerprint required")
    if time_limit_s is not None and (
        isinstance(time_limit_s, bool) or not isfinite(time_limit_s) or time_limit_s <= 0
    ):
        raise ValueError("positive finite MIP time limit required")
    if (
        isinstance(max_matrix_nonzeros, bool)
        or not isinstance(max_matrix_nonzeros, int)
        or max_matrix_nonzeros < 1
    ):
        raise ValueError("positive sparse matrix resource cap required")
    partitions = (
        dict.fromkeys(scenarios, universe.physical_partition_sha256)
        if scenario_partition_sha256s is None
        else dict(scenario_partition_sha256s)
    )
    if set(partitions) != set(scenarios) or any(
        not isinstance(v, str) or not v for v in partitions.values()
    ):
        raise ValueError("one frozen physical partition fingerprint per scenario required")
    if area_cap_m2 is not None and (
        isinstance(area_cap_m2, bool) or not isfinite(area_cap_m2) or area_cap_m2 < 0
    ):
        raise ValueError("finite nonnegative joint area cap required")
    index, costs, and_groups = {i: n for n, i in enumerate(candidates)}, [0.0] * len(candidates), {}
    for group, area in universe.area.owner_groups:
        if not group <= index.keys():
            continue
        if len(group) == 1:
            costs[index[next(iter(group))]] += area
        else:
            key = tuple(sorted(group))
            and_groups[key] = len(costs)
            costs.append(area)
    cuts, exact, grouped = [], set(), {}
    for (problem, ids, scenario), check in (cache or {}).items():
        if problem != problem_sha256:
            continue
        canonical = universe.canonical_actions(ids)
        if tuple(ids) != canonical:
            raise ValueError("cached joint action IDs must be canonical")
        ids = canonical
        if scenario not in scenarios or not isinstance(check, ProtectionCheck):
            raise ValueError("joint cache scenario or check type differs")
        if check.evidence.get("invalid_reasons"):
            raise ValueError("invalid physical checks cannot enter a joint master")
        if check.evidence.get("problem_sha256", problem_sha256) != problem_sha256:
            raise ValueError("cached check conflicts with the frozen joint problem")
        if area_cap_m2 is not None and universe.area(ids) > area_cap_m2:
            raise ValueError("cached plan lies outside the joint area cap")
        grouped.setdefault(ids, {})[scenario] = check
        if check.status != "failed":
            continue
        cert = check.evidence.get("failure_certificate")
        if cert is None:
            exact.add(ids)
            continue
        groups = validate_joint_cut(
            cert,
            universe,
            problem_sha256=problem_sha256,
            scenario_id=scenario,
            actions=ids,
            physical_partition_sha256=partitions[scenario],
        )
        if check.evidence.get("source_id", cert["source_id"]) != cert["source_id"]:
            raise ValueError("joint failed source differs")
        cuts.append(cert)
        for group in groups:
            if len(group) > 1 and set(group) <= index.keys() and group not in and_groups:
                and_groups[group] = len(costs)
                costs.append(0.0)
    passed = sorted(
        (universe.area(ids), ids)
        for ids, checks in grouped.items()
        if set(checks) == set(scenarios) and all(c.status == "passed" for c in checks.values())
    )
    if any(joint_cut_forbids(cert, ids) for _area, ids in passed for cert in cuts):
        raise ValueError("joint failure certificate contradicts a complete passed plan")
    row_ids, cols, data, lower, upper = [], [], [], [], []

    def add(values, lo, hi):
        if len(data) + sum(bool(v) for v in values.values()) > max_matrix_nonzeros:
            raise MemoryError(
                "joint MIP sparse matrix resource cap;physical status remains unknown"
            )
        row = len(lower)
        for col, val in values.items():
            if val:
                row_ids.append(row)
                cols.append(col)
                data.append(val)
        lower.append(lo)
        upper.append(hi)

    for group, y in and_groups.items():
        for i in group:
            add({y: 1, index[i]: -1}, -np.inf, 0)
        add({**{index[i]: 1 for i in group}, y: -1}, -np.inf, len(group) - 1)
    for cert in cuts:
        values = {index[i]: -1 for i in cert["required_prior_action_ids"]}
        for group in map(tuple, cert["required_clearance_action_groups"]):
            if not set(group) <= index.keys():
                continue
            y = index[group[0]] if len(group) == 1 else and_groups[group]
            values[y] = values.get(y, 0) + 1
        add(values, 1 - len(cert["required_prior_action_ids"]), np.inf)
    count_index = None
    if exact:
        count_index = len(costs)
        costs.append(0.0)
        add({**{index[i]: 1 for i in candidates}, count_index: -1}, 0, 0)
        for ids in sorted(exact):
            add({**{index[i]: -2 for i in ids}, count_index: 1}, 1 - len(ids), np.inf)
    if area_cap_m2 is not None:
        add(dict(enumerate(costs)), -np.inf, area_cap_m2)
    matrix = csc_matrix((data, (row_ids, cols)), shape=(len(lower), len(costs)))
    highs = np.ones(len(costs))
    integrality = np.ones(len(costs))
    if count_index is not None:
        highs[count_index] = len(candidates)
        integrality[count_index] = 0
    options = {"mip_rel_gap": 0.0}
    if time_limit_s is not None:
        options["time_limit"] = time_limit_s
    answer = milp(
        costs,
        integrality=integrality,
        bounds=Bounds(np.zeros(len(costs)), highs),
        constraints=LinearConstraint(matrix, lower, upper),
        options=options,
    )
    ids, area = None, None
    if answer.x is not None:
        rounded = np.rint(answer.x)
        binary = np.ones(len(costs), bool)
        if count_index is not None:
            binary[count_index] = False
        if np.max(abs(answer.x[binary] - rounded[binary])) > 1e-5:
            raise ValueError("noninteger joint proposal")
        if count_index is not None:
            rounded[count_index] = rounded[: len(candidates)].sum()
        logical = len(lower) - (area_cap_m2 is not None)
        values = matrix @ rounded
        if np.any(values[:logical] < np.asarray(lower[:logical])) or np.any(
            values[:logical] > np.asarray(upper[:logical])
        ):
            raise ValueError("rounded joint proposal violates logical constraints")
        ids = [i for i in candidates if rounded[index[i]] == 1]
        area = universe.area(ids)
        if area_cap_m2 is not None and area > area_cap_m2:
            raise ValueError("rounded joint proposal exceeds total C cap")
    dual = getattr(answer, "mip_dual_bound", None)
    return {
        "schema_version": "heritage-joint-area-master-prototype-1",
        "problem_sha256": problem_sha256,
        "joint_universe_sha256": universe.sha256,
        "scenario_partition_sha256s": partitions,
        "candidate_count": len(candidates),
        "binary_and_count": len(and_groups),
        "constraint_count": len(lower),
        "matrix_nonzero_count": len(data),
        "matrix_nonzero_cap": max_matrix_nonzeros,
        "failure_cut_count": len(cuts),
        "exact_failed_plan_exclusion_count": len(exact),
        "exact_no_good_policy": "one_global_count_equality_plus_sparse_prior_action_rows",
        "solver_status": int(answer.status),
        "solver_message": str(answer.message),
        "mip_gap": float(answer.mip_gap)
        if getattr(answer, "mip_gap", None) is not None and isfinite(answer.mip_gap)
        else None,
        "mip_node_count": int(getattr(answer, "mip_node_count", 0) or 0),
        "bound_type": "floating_point_HiGHS_MIP_bound;not_exact_arithmetic_certificate",
        "remaining_scope_empty": int(answer.status) == 2,
        "remaining_master_lower_bound_m2": float(dual)
        if dual is not None and isfinite(dual)
        else None,
        "next_ids": ids,
        "next_area_m2": area,
        "next_physical_protection_status": "unknown",
        "known_passed_best_area_m2": None if not passed else passed[0][0],
        "known_passed_best_ids": None if not passed else list(passed[0][1]),
        "area_cap_m2": area_cap_m2,
        "grass_scope": universe.grass_scope,
        "global_physical_optimality_proven": False,
        "global_pareto_frontier_complete": False,
    }
