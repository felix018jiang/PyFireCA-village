"""Physical fire exposure and fuel loss, without inferring destroyed houses.

Each entry is one disjoint physical node. Shared roof owners may each appear in
the annotation report, but never duplicate physical area or fuel. Attribution
bounds allocate shared patches to zero or all listed owners; they are conservative
bounds on annotation involvement, not a count of independently surveyed houses.
"""

from __future__ import annotations

import re

import numpy as np

_COMPONENT_ID = re.compile(r"cell([0-9]+):([^:\s]+):[0-9]+(?:/local[0-9]+)*")


def _vector(values, name, count=None):
    array = np.asarray(values, dtype=float).ravel()
    if count is not None and array.size != count:
        raise ValueError(f"{name} must match the physical node layout")
    return array


def _nonnegative(values, name, count=None):
    array = _vector(values, name, count)
    if not np.all(np.isfinite(array)) or np.any(array < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return array


def _bound(lower, upper):
    return {"lower": lower, "upper": upper}


def summarize_fire_loss(
    *,
    covered_area_m2,
    node_owners,
    ignition_time_s,
    burnout_time_s,
    observation_time_s,
    final_time_s=None,
    inventory_ids=None,
    demolished_ids=(),
    common_retained_ids=None,
    source_ids=(),
    cleared_node_mask=None,
    initial_fuel_kg=None,
    final_fuel_remaining_kg=None,
    baseline_initial_fuel_kg=None,
    known_node_mask=None,
):
    """Return JSON-compatible metrics on a pre-intervention physical area basis.

    ``node_owners`` must retain original overlap ownership, before demolition.
    ``covered_area_m2`` must also precede clearing; the mask identifies whole
    physical nodes actually cleared (a shared patch with a surviving owner is
    retained). Fuel arrays are pyrolysable fuel, matching ``V3SimulationResult``
    ``initial_fuel_kg``, rather than initial total dry timber mass. Fuel changes
    are available only at the saved final time; clocks can reconstruct earlier
    area checkpoints. NaN and +inf clocks mean no observed event.

    An inventory ID without physical representation remains unknown and can
    contribute to the count upper bound; it must not silently count as safe.
    The sum of per-owner area upper bounds is intentionally not physical area.
    """
    area = _nonnegative(covered_area_m2, "covered_area_m2")
    count = area.size
    if not count:
        raise ValueError("physical node layout must not be empty")
    owners = tuple(tuple(ids) for ids in node_owners)
    if len(owners) != count or any(
        len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids)
        for ids in owners
    ):
        raise ValueError("node_owners must be unique nonempty string IDs per physical node")
    observed_ids = {i for ids in owners for i in ids}
    ids = tuple(sorted(observed_ids if inventory_ids is None else inventory_ids))
    if (
        len(ids) != len(set(ids))
        or any(not isinstance(i, str) or not i for i in ids)
        or not observed_ids <= set(ids)
    ):
        raise ValueError("inventory_ids must uniquely include every node owner")
    removed, sources = set(demolished_ids), set(source_ids)
    if not (removed | sources) <= set(ids) or removed & sources:
        raise ValueError("demolished/source IDs must be disjoint inventory members")
    common = None if common_retained_ids is None else set(common_retained_ids)
    if common is not None and (not common <= set(ids) or common & removed):
        raise ValueError("common_retained_ids must be retained inventory members")
    time = float(observation_time_s)
    end = time if final_time_s is None else float(final_time_s)
    if not np.isfinite(time) or not np.isfinite(end) or not 0 <= time <= end:
        raise ValueError("observation time must be finite and within the saved final time")
    ignition = _vector(ignition_time_s, "ignition_time_s", count)
    burnout = _vector(burnout_time_s, "burnout_time_s", count)
    if any(np.any(np.isneginf(a)) or np.any(a[np.isfinite(a)] < 0) for a in (ignition, burnout)):
        raise ValueError("event clocks cannot be negative")
    finite_burnout = np.isfinite(burnout)
    if np.any(finite_burnout & (~np.isfinite(ignition) | (burnout < ignition))):
        raise ValueError("burnout must follow a finite ignition")
    if np.any(ignition[np.isfinite(ignition)] > end) or np.any(burnout[finite_burnout] > end):
        raise ValueError("event clock exceeds the saved final time")
    roofs = np.asarray([bool(o) for o in owners])
    removed_by_ownership = np.asarray([bool(o) and set(o) <= removed for o in owners])
    cleared = (
        removed_by_ownership
        if cleared_node_mask is None
        else np.asarray(cleared_node_mask, dtype=bool).ravel()
    )
    known = (
        np.ones(count, dtype=bool)
        if known_node_mask is None
        else np.asarray(known_node_mask, dtype=bool).ravel()
    )
    if cleared.size != count or known.size != count:
        raise ValueError("node masks must match the physical node layout")
    if np.any(removed_by_ownership & ~cleared & (area > 0)):
        raise ValueError("an exclusively removed roof must be marked cleared")
    ignited = np.isfinite(ignition) & (ignition <= time) & (area > 0)
    if np.any(ignited & cleared):
        raise ValueError("cleared nodes cannot ignite in the post-intervention fire")
    exhausted = ignited & finite_burnout & (burnout <= time)
    active = ignited & ~exhausted

    def physical(mask):
        retained = mask & ~cleared
        clear = mask & cleared
        return {
            "baseline_covered_area_m2": float(area[mask].sum()),
            "retained_covered_area_m2": float(area[retained].sum()),
            "cleared_covered_area_m2": float(area[clear].sum()),
            "cumulative_ignited_covered_area_m2": float(area[mask & ignited].sum()),
            "cumulative_burned_out_covered_area_m2": float(area[mask & exhausted].sum()),
            "active_burning_covered_area_m2": float(area[mask & active].sum()),
            "ignited_node_count": int((mask & ignited).sum()),
            "active_burning_node_count": int((mask & active).sum()),
            "clearing_plus_ignited_union_area_m2": float(area[mask & (cleared | ignited)].sum()),
            "clearing_plus_burned_out_union_area_m2": float(
                area[mask & (cleared | exhausted)].sum()
            ),
        }

    owner_nodes = {i: [] for i in ids}
    for node, labels in enumerate(owners):
        for label in labels:
            owner_nodes[label].append(node)
    buildings = []
    for identifier in ids:
        nodes = np.asarray(owner_nodes[identifier], dtype=int)
        usable = nodes[(area[nodes] > 0) & ~cleared[nodes]]
        exclusive = np.asarray([n for n in usable if len(owners[n]) == 1], dtype=int)
        represented = float(area[nodes].sum())
        shared = float(area[[n for n in nodes if len(owners[n]) > 1]].sum())
        removed_id = identifier in removed

        def event_area(
            event,
            removed_id=removed_id,
            exclusive=exclusive,
            usable=usable,
            represented=represented,
        ):
            if removed_id:
                return _bound(0.0, 0.0)
            return _bound(
                float(area[exclusive[event[exclusive]]].sum()),
                float(area[usable[event[usable]]].sum()) if represented > 0 else None,
            )

        fired = event_area(ignited)
        spent = event_area(exhausted)
        burning = event_area(active)

        def fraction(bounds, represented=represented, removed_id=removed_id):
            if represented > 0:
                return _bound(bounds["lower"] / represented, bounds["upper"] / represented)
            return _bound(0.0, 0.0 if removed_id else None)

        buildings.append(
            {
                "building_id": identifier,
                "demolished": removed_id,
                "is_source": identifier in sources,
                "identity_status": "unrepresented_annotation"
                if represented == 0
                else "overlapping_annotation"
                if shared > 0
                else "annotation_only",
                "modeled_roof_covered_area_m2": represented,
                "shared_roof_covered_area_m2": shared,
                "cumulative_ignited_area_m2": fired,
                "cumulative_burned_out_area_m2": spent,
                "active_burning_area_m2": burning,
                "ignited_fraction_of_modeled_roof": fraction(fired),
                "burned_out_fraction_of_modeled_roof": fraction(spent),
                "active_burning_fraction_of_modeled_roof": fraction(burning),
                "first_possible_ignition_s": float(ignition[usable[ignited[usable]]].min())
                if not removed_id and np.any(ignited[usable])
                else None,
                "first_exclusive_ignition_s": float(ignition[exclusive[ignited[exclusive]]].min())
                if not removed_id and np.any(ignited[exclusive])
                else None,
                "whole_building_destroyed": None,
            }
        )

    def counts(selected):
        rows = [b for b in buildings if b["building_id"] in selected and not b["demolished"]]
        lower = sum(b["cumulative_ignited_area_m2"]["lower"] > 0 for b in rows)
        upper = sum(
            b["cumulative_ignited_area_m2"]["upper"] is None
            or b["cumulative_ignited_area_m2"]["upper"] > 0
            for b in rows
        )
        return {
            "retained_inventory_count": len(rows),
            "unrepresented_retained_ids": [
                b["building_id"] for b in rows if b["modeled_roof_covered_area_m2"] == 0
            ],
            "ignited_annotation_attribution_count": _bound(lower, upper),
            "clearing_plus_ignited_annotation_count": _bound(
                lower + len(removed.intersection(selected)),
                upper + len(removed.intersection(selected)),
            ),
            "intersected_annotation_count": sum(
                (b["cumulative_ignited_area_m2"]["upper"] or 0) > 0 for b in rows
            ),
        }

    # A planned annotation deletion is not disappearance of its physical roof:
    # a shared patch remains fuel while any owner survives. Keep the original
    # attribution even for IDs listed in demolished_ids. This block supplies
    # the physical N objective; the historical retained-ID metrics stay intact.
    original_rows = []
    for identifier in ids:
        nodes = np.asarray(owner_nodes[identifier], dtype=int)
        positive = nodes[area[nodes] > 0]
        remaining = positive[~cleared[positive]]
        exclusive = np.asarray([node for node in remaining if len(owners[node]) == 1], dtype=int)
        original_area = float(area[positive].sum())
        remaining_area = float(area[remaining].sum())

        def original_event_area(
            event, remaining=remaining, exclusive=exclusive, original_area=original_area
        ):
            return _bound(
                float(area[exclusive[event[exclusive]]].sum()),
                float(area[remaining[event[remaining]]].sum()) if original_area > 0 else None,
            )

        original_rows.append(
            {
                "building_id": identifier,
                "planned_action_id": identifier in removed,
                "original_covered_area_m2": original_area,
                "remaining_covered_area_m2": remaining_area,
                "cleared_covered_area_m2": original_area - remaining_area,
                "fully_cleared": original_area > 0 and remaining_area == 0,
                "unrepresented": original_area == 0,
                "ignited_area_m2": original_event_area(ignited),
                "burned_out_area_m2": original_event_area(exhausted),
            }
        )

    def original_count(field):
        return _bound(
            sum(row[field]["lower"] > 0 for row in original_rows),
            sum(row[field]["upper"] is None or row[field]["upper"] > 0 for row in original_rows),
        )

    original_counts = {
        "schema_version": "physical-original-owner-fire-counts-1",
        "original_inventory_count": len(ids),
        "remaining_original_owner_count": sum(not row["fully_cleared"] for row in original_rows),
        "planned_action_annotation_count": len(removed),
        "planned_action_ids": sorted(removed),
        "fully_cleared_ids": [row["building_id"] for row in original_rows if row["fully_cleared"]],
        "partially_cleared_ids": [
            row["building_id"]
            for row in original_rows
            if row["cleared_covered_area_m2"] > 0 and row["remaining_covered_area_m2"] > 0
        ],
        "unrepresented_original_ids": [
            row["building_id"] for row in original_rows if row["unrepresented"]
        ],
        "ignited_annotation_attribution_count": original_count("ignited_area_m2"),
        "burned_out_annotation_attribution_count": original_count("burned_out_area_m2"),
        "per_original_owner": original_rows,
        "interpretation": (
            "original owner involvement on actually remaining physical roof patches;"
            "planned label removal alone cannot reduce these counts;"
            "exclusive-patch lower and shared-inclusive upper are attribution bounds;"
            "unrepresented owners remain unknown;not whole-building destruction"
        ),
        "real_destroyed_building_count": None,
    }

    fuel = {"available": False, "reason": "initial_and_final_pyrolysable_fuel_arrays_required"}
    if (initial_fuel_kg is None) != (final_fuel_remaining_kg is None):
        raise ValueError("initial and final fuel arrays must be provided together")
    if initial_fuel_kg is not None:
        initial = _nonnegative(initial_fuel_kg, "initial_fuel_kg", count)
        remaining = _nonnegative(final_fuel_remaining_kg, "final_fuel_remaining_kg", count)
        tolerance = np.maximum(1e-9, initial * 1e-9)
        if np.any(remaining > initial + tolerance):
            raise ValueError("remaining fuel exceeds the initial post-intervention fuel")
        if not np.isclose(time, end, rtol=0, atol=1e-9):
            fuel = {"available": False, "reason": "fuel_state_available_only_at_saved_final_time"}
        else:
            consumed = np.maximum(initial - remaining, 0)
            fuel = {
                "available": True,
                "basis": "pyrolysable_fuel;mass_depletion_not_structural_damage",
                "initial_post_intervention_kg": float(initial.sum()),
                "remaining_kg": float(remaining.sum()),
                "fire_depleted_kg": float(consumed.sum()),
                "roof_fire_depleted_kg": float(consumed[roofs].sum()),
                "nonroof_fire_depleted_kg": float(consumed[~roofs].sum()),
                "actively_burning_remaining_kg": float(remaining[active].sum()),
                "planned_removed_kg": None,
                "clearing_plus_fire_depleted_kg": None,
            }
            if baseline_initial_fuel_kg is not None:
                baseline = _nonnegative(baseline_initial_fuel_kg, "baseline_initial_fuel_kg", count)
                if np.any(initial > baseline + np.maximum(1e-9, baseline * 1e-9)):
                    raise ValueError("post-intervention fuel exceeds baseline fuel")
                if not np.allclose(initial[~cleared], baseline[~cleared], rtol=1e-9, atol=1e-9):
                    raise ValueError("fuel changed outside cleared nodes")
                planned = np.maximum(baseline - initial, 0)
                fuel["planned_removed_kg"] = float(planned.sum())
                fuel["clearing_plus_fire_depleted_kg"] = float((planned + consumed).sum())

    return {
        "schema_version": "physical-fire-loss-1",
        "observation_time_s": time,
        "saved_final_time_s": end,
        "interpretation": {
            "area": "disjoint_physical_covered_nodes;each_node_once",
            "building_bounds": "exclusive_patch_lower;inclusive_shared_patch_upper;not_tight",
            "identity": "annotation_objects;independent_household_identity_not_established",
            "damage": "ignition_and_fuel_exhaustion_are_not_whole_building_destruction",
            "total_loss": "planned_clearing_union_fire_exposure;not_monetary_loss",
        },
        "physical": {
            "all": physical(np.ones(count, dtype=bool)),
            "roof": physical(roofs),
            "nonroof": physical(~roofs),
            "unknown_baseline_covered_area_m2": float(area[~known].sum()),
        },
        "annotation_counts": counts(set(ids)),
        "physical_remaining_roof_annotation_counts": original_counts,
        "secondary_annotation_counts": counts(set(ids) - sources),
        "common_retained_annotation_counts": None if common is None else counts(common),
        "demolished_annotation_count": len(removed),
        "per_building": buildings,
        "fuel": fuel,
        "real_destroyed_building_count": None,
        "structural_damage_area_m2": None,
        "fire_extinguished_at_observation": not bool(active.any()),
    }


def recover_original_node_owners(
    layout, inventory, *, demolished_ids=(), original_node_owners=None
):
    """Recover immutable roof owners, without relying on tiny-polygon point tests.

    Canonical ``cellN:A+B:part/localK`` IDs bind original owners to the physical
    parent cell. Recognized IDs with conflicting cells/owners are errors, never
    silently repaired. Unknown component formats use the original geometric
    fallback, which must independently establish every current roof owner.
    """
    current = tuple(tuple(group) for group in layout.owners)
    count = len(current)
    inventory_ids = set(inventory.by_id)
    methods = {"supplied": 0, "immutable_component_id": 0, "geometry": 0, "nonroof": 0}
    if original_node_owners is not None:
        recovered = [tuple(group) for group in original_node_owners]
        methods["supplied"] = count
        if len(recovered) != count:
            raise ValueError("original owners must match the physical node layout")
    else:
        recovered = [None] * count
        components, cells = getattr(layout, "component_id", None), getattr(layout, "cell_id", None)
        if components is not None and cells is not None:
            if len(components) != count or len(cells) != count:
                raise ValueError("component identities must match the physical node layout")
            for node, component in enumerate(components):
                match = _COMPONENT_ID.fullmatch(str(component))
                if match is None:
                    continue
                if int(match[1]) != int(cells[node]):
                    raise ValueError("component identity conflicts with the physical parent cell")
                group = () if match[2] == "ground" else tuple(match[2].split("+"))
                if len(set(group)) != len(group) or not set(group) <= inventory_ids:
                    raise ValueError("immutable component identity contains invalid roof owners")
                recovered[node] = group
                methods["immutable_component_id"] += 1
        fallback = [node for node, group in enumerate(recovered) if group is None and current[node]]
        if fallback:
            from shapely import points
            from shapely.strtree import STRtree

            objects = inventory.buildings
            tree = STRtree([b.geometry for b in objects])
            hits = tree.query(points(np.asarray(layout.xy_m)[fallback]), predicate="covered_by")
            labels = {node: [] for node in fallback}
            for query, object_index in hits.T:
                labels[fallback[query]].append(objects[object_index].building_id)
            core_ids = {target.building_id for target in inventory.targets}
            for node in fallback:
                group = labels[node]
                core = core_ids.intersection(group)
                recovered[node] = tuple(sorted(core if core else group))
                if not recovered[node]:
                    raise ValueError("original roof ownership cannot be recovered from inventory")
                methods["geometry"] += 1
        for node, group in enumerate(recovered):
            if group is None:
                recovered[node] = ()
                methods["nonroof"] += 1
    removed = set(demolished_ids)
    for post, before in zip(current, recovered, strict=True):
        if len(set(before)) != len(before) or not set(before) <= inventory_ids:
            raise ValueError("original node owners must be unique inventory identities")
        if not set(post) <= set(before):
            raise ValueError("retained owners conflict with recovered original node identity")
        if not (set(before) - set(post)) <= removed:
            raise ValueError("original owner dropped without a declared demolition")
    return tuple(recovered), {
        "methods_node_count": methods,
        "retained_owner_subset_verified": True,
        "owner_drops_bound_to_declared_demolitions": True,
        "component_contract": (
            "cellN:owner+owner:part[/localK];known_inventory_ids;matching_parent_cell"
        ),
        "geometry_policy": "unrecognized_component_schema_only;failure_is_not_guessed",
    }


def fire_loss_from_result(
    layout,
    result,
    inventory,
    *,
    demolished_ids=(),
    common_retained_ids=None,
    source_ids=(),
    observation_time_s=None,
    original_node_owners=None,
):
    """Adapt an in-memory roof result, preserving pre-clearing physical budgets.

    Pass original owners when already available. Otherwise verified immutable
    component identities take priority; geometric membership is a strict fallback
    for layouts without the canonical component schema. Demolition may have
    removed shared-owner IDs from ``layout.owners``. No solver data is changed.
    """
    baseline = layout.node_inputs.parameters
    effective = layout.node_request.parameters
    area = np.asarray(baseline.covered_area_m2).ravel()
    retained_area = np.asarray(effective.covered_area_m2).ravel()
    cleared = (area > 0) & (retained_area == 0)
    if not np.allclose(area[~cleared], retained_area[~cleared], rtol=1e-9, atol=1e-8):
        raise ValueError("result must use whole-node clearing on the baseline physical layout")
    original_node_owners, ownership_audit = recover_original_node_owners(
        layout,
        inventory,
        demolished_ids=demolished_ids,
        original_node_owners=original_node_owners,
    )
    state = result.final_state
    loss = summarize_fire_loss(
        covered_area_m2=area,
        node_owners=original_node_owners,
        ignition_time_s=state.ignition_time_s,
        burnout_time_s=state.burnout_time_s,
        observation_time_s=state.time_s if observation_time_s is None else observation_time_s,
        final_time_s=state.time_s,
        inventory_ids=inventory.by_id.keys(),
        demolished_ids=demolished_ids,
        common_retained_ids=common_retained_ids,
        source_ids=source_ids,
        cleared_node_mask=cleared,
        initial_fuel_kg=result.initial_fuel_kg,
        final_fuel_remaining_kg=state.fuel_remaining_kg,
        baseline_initial_fuel_kg=np.where(
            layout.node_inputs.burnable_mask, baseline.pyrolysable_dry_fuel_mass_kg, 0
        ),
        known_node_mask=layout.node_inputs.known_mask,
    )
    loss["ownership_reconstruction"] = ownership_audit
    return loss
