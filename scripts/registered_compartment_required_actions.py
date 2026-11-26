"""Return input constraints for joint clearance of fixed roof compartments and core protection."""

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components
from check_heritage_compartment_first_escape import load_bound_inputs, pair_conditional_doses
from check_heritage_registered_roof_compartment import verify


def required_actions(village, *, fraction_guard=1e-10):
    """Recompute first-escape constraints before subtracting any baseline action.

    The returned mandatory removals act on the original physical inventory.
    Priority removals maintain frozen first-retained draws under a new joint plan.
    No solver or thermal CA is run. The returned retained IDs are constraints for
    the caller, and no source coordinates, dosimetry map or ranking is produced.
    """
    verify(0 <= fraction_guard < 1, "invalid immunity margin")
    _, _, layout, inventory, transport, _, _, families, _ = load_bound_inputs(village)
    protected = {item.building_id for item in inventory.targets}
    retained = {f[2] for f in families}
    active = layout.node_inputs.burnable_mask.ravel()
    core = np.array([bool(protected.intersection(g)) for g in layout.owners])
    inside = np.array([bool(retained.intersection(g)) for g in layout.owners])
    nodes = np.flatnonzero(inside)
    verify(not np.any(inside & core) and active[nodes].all(), "invalid retained group")
    polygons = np.asarray(layout.geometry, dtype=object)
    # All nodes in this reduced potential-transfer graph are original roof polygons.
    a, b = shapely.STRtree(polygons[nodes]).query(
        polygons[nodes], predicate="dwithin", distance=transport.exposure_radius_m + 1e-8
    )
    selected = a < b
    local = np.column_stack((a[selected], b[selected])).astype(np.int32)
    labels = components(local, np.ones(len(nodes), dtype=bool))
    ssets = []
    earlier = set()
    for _, protocol, source, _ in families:
        indices = [k for k, j in enumerate(nodes) if source in layout.owners[j]]
        ssets.append(tuple(nodes[np.isin(labels, np.unique(labels[indices]))].tolist()))
        priority = protocol["draws"][0]["priority_ids"]
        earlier.update(priority[: priority.index(source)])
    verify(not earlier.intersection(retained), "cannot simultaneously preserve all declared draws")
    outside = np.flatnonzero(active & ~(inside | core))
    dose, contact, _ = pair_conditional_doses(layout, polygons, nodes, outside, ssets, transport)
    obliged = contact | {j for j, fraction in dose.items() if fraction >= 1 - fraction_guard}
    roof = {i for j in obliged for i in layout.owners[j]}
    ground = {int(layout.cell_id[j]) for j in obliged if not layout.owners[j]}
    verify(
        not roof.intersection(retained | protected)
        and set(inventory.candidate_ids).issuperset(earlier | roof),
        "unavailable required action",
    )
    return {
        "first_escape_mandatory_roof_ids": sorted(roof),
        "first_escape_mandatory_ground_parent_cells": sorted(ground),
        "source_priority_mandatory_removed_roof_ids": sorted(earlier),
        "must_retain_ordinary_roof_ids": sorted(retained),
        "fraction_guard": fraction_guard,
        "fixed_family_count": len(families),
    }
