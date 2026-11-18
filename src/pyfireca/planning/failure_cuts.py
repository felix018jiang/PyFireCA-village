"""Failure-preserving cold-removal certificates and exact prefix bounds."""

from __future__ import annotations

from math import fsum

import numpy as np
from shapely import STRtree

from pyfireca.planning.heritage import content_hash
from pyfireca.village.physics_v2 import monotone_thermal_timestep_limit_s

PHYSICAL_GROUP_CUT_CAPABILITIES = {
    "fixed_physical_partition": True,
    "frozen_radiation_integration_membership": True,
    "owner_labels_are_thermal_metadata": True,
    "whole_building_actions_only": True,
    "cold_timestep_limiting_nodes_guarded": True,
    "all_ignitions_at_zero": True,
    "wind_speed_m_s": 0.0,
    "firebrands": False,
    "source_guard": "superset_of_prior_removed_and_singleton_source_or_fixed_source",
}


def cold_removal_certificate(
    layout,
    result,
    contact,
    inventory,
    transport,
    *,
    demolished_ids,
    scenario_id,
    problem_sha256,
    source_id,
    frozen_thermal_membership=False,
):
    """Certify only extra removals that cannot change this failed trajectory.

    Every realised burner and every possible receiver (including still-cold
    receivers) is retained. Outside nodes have exactly zero incoming heat and
    cannot determine the timestep. This is not fire-response monotonicity.

    The opt-in physical-group form additionally requires the caller to freeze
    quadrature membership and every owner-independent thermal operator on the
    common mesh. It can then distinguish deleting an annotation from clearing
    an entire physical owner group. The default legacy certificate is unchanged.
    """
    if not isinstance(frozen_thermal_membership, bool):
        raise TypeError("frozen_thermal_membership must be an explicit boolean")
    request, state = layout.node_request, result.final_state
    if request.firebrands is not None or transport.wind_speed_m_s != 0:
        return None
    burned = np.flatnonzero(np.isfinite(state.ignition_time_s.ravel()))
    if not len(burned):
        return None
    if any(e.time_s != 0 for e in request.scenario.ignitions):
        return None
    # Geometry distances conservatively cover representative-point AND finite
    # quadrature kernels, including pairs whose current weight is exactly zero.
    tree = STRtree(layout.geometry)
    zone = np.zeros(len(layout.owners), dtype=bool)
    zone[burned] = True
    for start in range(0, len(burned), 256):
        indices = burned[start : start + 256]
        pairs = tree.query(
            [layout.geometry[i] for i in indices],
            predicate="dwithin",
            distance=transport.exposure_radius_m,
        )
        zone[pairs[1]] = True
    if contact is not None:
        targets = contact.neighbors[burned]
        zone[targets[targets >= 0]] = True
    removed = set(demolished_ids)
    influential = set().union(*(set(ids) for node, ids in enumerate(layout.owners) if zone[node]))
    candidates = set(inventory.candidate_ids) - removed
    cold = np.asarray([bool(set(ids) & candidates) for ids in layout.owners]) & ~zone
    # Exact zero checks, never a small-temperature or small-area threshold.
    for name in ("incident_radiation_J", "received_convection_J", "released_energy_J"):
        if np.any(getattr(state, name).ravel()[cold] != 0):
            raise ValueError("cold-removal proof contradicted by nonzero heat/energy")
    if np.any(state.surface_temperature_K.ravel()[cold] != request.scenario.ambient_temperature_K):
        raise ValueError("cold-removal proof contradicted by changed cold temperature")
    parameters = request.parameters
    live = cold & request.burnable_mask.ravel()
    limit = None
    if live.any():

        def field(name):
            return getattr(parameters, name).ravel()[live]

        limit = monotone_thermal_timestep_limit_s(
            np.full(int(live.sum()), request.scenario.ambient_temperature_K),
            field("effective_heated_dry_mass_kg") / field("covered_area_m2"),
            field("specific_heat_J_kg_K"),
            field("emissivity"),
            field("ambient_heat_transfer_coefficient_W_m2_K")
            + (contact.parameters.heat_transfer_coefficient_W_m2_K if contact else 0),
            field("backing_conductance_W_m2_K"),
        )
        if limit < request.scenario.max_dt_s:
            # These nodes may affect all nodes through the global time step.
            # Keep their owners; the resulting certificate remains sound.
            influential.update(i for node in np.flatnonzero(live) for i in layout.owners[node])
    retained = sorted(influential & candidates)
    if source_id not in retained and source_id in candidates:
        raise ValueError("failure certificate must preserve its actual source")
    value = {
        "schema_version": "heritage-cold-removal-cut-1",
        "problem_sha256": problem_sha256,
        "scenario_id": scenario_id,
        "required_demolished_ids": sorted(removed),
        "required_retained_ids": retained,
        "source_id": source_id,
        "failed_at_s": float(state.time_s),
        "burned_node_count": len(burned),
        "potential_receiver_node_count": int(zone.sum()),
        "extra_removal_eligible_ids": sorted(candidates - set(retained)),
        "cold_node_energy_exactly_zero": True,
        "cold_node_thermal_limit_s": limit if limit is None or np.isfinite(limit) else None,
        "maximum_step_s": request.scenario.max_dt_s,
        "timestep_limiting_cold_nodes_guarded": limit is not None
        and limit < request.scenario.max_dt_s,
        "kernel_scope": (
            "fixed_geometry_transparent_calm_radiation_and_bounded_contact_no_firebrands"
        ),
    }
    if frozen_thermal_membership:
        guarded = zone.copy()
        if limit is not None and limit < request.scenario.max_dt_s:
            guarded |= live
        guarded &= request.burnable_mask.ravel() & (parameters.covered_area_m2.ravel() > 0)
        groups = {}
        for node in np.flatnonzero(guarded):
            # Shared nodes have lost already-removed labels; subtract A as an
            # explicit safeguard. A surviving noncandidate owner makes a group
            # impossible to clear, which the master treats as a fixed literal.
            owners = tuple(sorted(set(layout.owners[node]) - removed))
            if owners:
                groups.setdefault(owners, []).append(int(node))
        if source_id in candidates and (source_id,) not in groups:
            # A changed first-retained source must be a physical-change escape
            # literal, never an unguarded identity-only change in ignition.
            return None
        core_failed = any(
            np.isfinite(state.ignition_time_s.ravel()[layout.nodes(t.building_id)]).any()
            for t in inventory.targets
        )
        if not core_failed:
            return None
        owner_groups = [list(owners) for owners in sorted(groups)]
        capabilities = dict(PHYSICAL_GROUP_CUT_CAPABILITIES)
        value.update(
            schema_version="heritage-physical-node-removal-cut-1",
            required_clearance_owner_groups=owner_groups,
            physical_group_witnesses=[
                {
                    "remaining_owner_ids": list(owners),
                    "positive_burnable_node_count": len(nodes),
                    "covered_area_m2": float(parameters.covered_area_m2.ravel()[nodes].sum()),
                    "physical_node_indices_sha256": content_hash(nodes),
                }
                for owners, nodes in sorted(groups.items())
            ],
            guarded_positive_burnable_node_count=int(guarded.sum()),
            physical_group_sha256=content_hash(owner_groups),
            physical_cut_capabilities=capabilities,
            physical_cut_capabilities_sha256=content_hash(capabilities),
            physical_core_failure_observed=True,
            interpretation=(
                "a_superset_of_prior_removed_can_escape_only_by_clearing_all_remaining_owners_"
                "of_a_guarded_physical_node;partial_shared_label_removal_is_not_an_escape"
            ),
        )
    value["certificate_sha256"] = content_hash(value)
    return value


class FailureCutStore:
    """A cut forbids D superset A with D disjoint H; neither side is guessed."""

    def __init__(self, problem_sha256, areas):
        self.problem_sha256, self.areas = problem_sha256, dict(areas)
        self.cuts = {}

    def learn(self, ids, scenario, check):
        value = check.evidence.get("failure_certificate")
        if value is None:
            return
        raw = {k: v for k, v in value.items() if k != "certificate_sha256"}
        if (
            check.status != "failed"
            or value["problem_sha256"] != self.problem_sha256
            or value["scenario_id"] != scenario
            or value["required_demolished_ids"] != list(ids)
            or content_hash(raw) != value["certificate_sha256"]
        ):
            raise ValueError("failure certificate does not bind its failed physical check")
        a, h = set(value["required_demolished_ids"]), set(value["required_retained_ids"])
        if a & h or not (a | h) <= self.areas.keys():
            raise ValueError("invalid failure-cut action sets")
        self.cuts[value["certificate_sha256"]] = value

    def bound(self, prefix, remaining, slots):
        """A disjoint set packing is a lower bound, not a heuristic exclusion."""
        chosen, available = set(prefix), set(remaining)
        needed = []
        for cut in self.cuts.values():
            a, h = set(cut["required_demolished_ids"]), set(cut["required_retained_ids"])
            if not a <= chosen or h & chosen:
                continue
            choices = h & available
            if not choices:
                return None
            needed.append(choices)
        packed, used = [], set()
        for group in sorted(needed, key=lambda g: (len(g), tuple(sorted(g)))):
            if not group & used:
                packed.append(group)
                used.update(group)
        if len(packed) > slots:
            return None
        # Chosen costs plus a mandatory item per disjoint group. The remaining
        # cheapest items may repeat those minima: a relaxation, hence a safe LB.
        cheapest = sorted(self.areas[i] for i in available)
        lower = fsum(
            [
                *(self.areas[i] for i in chosen),
                *(min(self.areas[i] for i in group) for group in packed),
                *cheapest[: slots - len(packed)],
            ]
        )
        return lower

    def to_mapping(self):
        return list(self.cuts.values())
