"""Geometry-only, budgeted proposals for reducing actual cleared roof area.

These proposals are not fire-protection findings. Every eligible ordinary
building remains in the action universe; ordering by area and distance does not
certify omitted combinations. Fire simulation and independent confirmation are
separate steps.
"""

from __future__ import annotations

import heapq
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import combinations
from math import fsum, isfinite


def _ids(values, name):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    return tuple(sorted(values))


def _positive_limit(value, name, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
        raise ValueError(f"{name} must be a {'nonnegative' if allow_zero else 'positive'} integer")
    return value


@dataclass(frozen=True)
class ClearedAreaFunction:
    """Sum disjoint physical patches only when every named owner is removed.

    ``owner_groups`` contains physical partition areas, not overlapping building
    footprints. Callers explicitly choose geometry or parameter coverage areas;
    this object does not silently switch between those definitions. Empty-owner
    background patches do not belong to building demolition actions.
    """

    owner_groups: tuple[tuple[frozenset[str], float], ...]
    area_basis: str
    physical_patch_count: int

    def __call__(self, demolished_ids):
        removed = set(_ids(demolished_ids, "demolished_ids"))
        return fsum(area for owners, area in self.owner_groups if owners <= removed)

    @property
    def owner_ids(self):
        return frozenset(i for owners, _ in self.owner_groups for i in owners)


def build_cleared_area_function(owners, patch_areas_m2, *, area_basis):
    """Build actual clearing cost from a frozen physical partition.

    For a roof layout use ``layout.owners`` with either ``[g.area for g in
    layout.geometry]`` and an explicit geometry basis, or the explicitly chosen
    covered-area array. The supplied patches must be disjoint physical atoms;
    supplying overlapping building footprints would double count shared space.
    """
    if not isinstance(area_basis, str) or not area_basis.strip():
        raise ValueError("an explicit physical area basis is required")
    owners, areas = tuple(owners), tuple(patch_areas_m2)
    if len(owners) != len(areas):
        raise ValueError("owners and physical patch areas must have equal lengths")
    grouped = defaultdict(list)
    for group, area in zip(owners, areas, strict=True):
        group = _ids(group, "physical patch owners")
        if isinstance(area, bool):
            raise ValueError("physical patch areas must be finite and nonnegative")
        area = float(area)
        if not isfinite(area) or area < 0:
            raise ValueError("physical patch areas must be finite and nonnegative")
        if group:
            grouped[group].append(area)
    return ClearedAreaFunction(
        tuple((frozenset(group), fsum(values)) for group, values in sorted(grouped.items())),
        area_basis,
        len(owners),
    )


def _small_combinations(items, weights, size):
    """Generate k-subsets by an additive ordering heuristic, never a true bound."""
    if len(items) < size:
        return
    initial = tuple(range(size))
    heap = [(fsum(weights[items[i]] for i in initial), initial)]
    seen = {initial}
    while heap:
        _, indices = heapq.heappop(heap)
        yield tuple(items[i] for i in indices)
        for position in range(size):
            upper = indices[position + 1] if position + 1 < size else len(items)
            if indices[position] + 1 >= upper:
                continue
            following = list(indices)
            following[position] += 1
            following = tuple(following)
            if following not in seen:
                seen.add(following)
                heapq.heappush(
                    heap,
                    (fsum(weights[items[i]] for i in following), following),
                )


def _round_robin(streams):
    queue = deque(iter(stream) for stream in streams)
    while queue:
        stream = queue.popleft()
        try:
            yield next(stream)
        except StopIteration:
            continue
        queue.append(stream)


def generate_area_candidates(
    inventory,
    seed_ids,
    cleared_area,
    *,
    fixed_source_ids=(),
    per_class_limits=None,
    max_generated=20_000,
    max_plans=12,
    max_bundle_size=3,
):
    """Return seed and diverse local proposals without reading fire outcomes.

    The seed is always retained. Classes are single add-back, one-for-one
    replacement, two-for-two exchange, and one-for-two/three bundle replacement.
    Within a class, complete plans are ranked by the supplied actual cleared
    area, then geometric distance, then IDs. Bundle traversal uses singleton
    areas as an ordering heuristic only: shared-area nonadditivity means the
    first visited bundles are not certified cheapest bundles.

    ``max_generated`` bounds attempted proposals including the seed. The
    remaining budget is divided across active classes so exchanges are not
    starved by the one-for-one stream. ``per_class_limits`` bounds retained
    proposals, while ``max_plans`` bounds final output. Final slots rotate
    across classes to preserve useful structural differences. No simulation,
    feasibility judgement, lower bound or optimality certificate is produced.
    """
    max_generated = _positive_limit(max_generated, "generation budget")
    max_plans = _positive_limit(max_plans, "plan limit")
    if (
        isinstance(max_bundle_size, bool)
        or not isinstance(max_bundle_size, int)
        or max_bundle_size not in {2, 3}
    ):
        raise ValueError("maximum bundle size must be 2 or 3")
    defaults = {
        "single_addback": 2,
        "single_replacement": 3,
        "double_exchange": 2,
        "small_bundle": 4,
    }
    limits = defaults | ({} if per_class_limits is None else dict(per_class_limits))
    if set(limits) != set(defaults):
        raise ValueError("unknown proposal class")
    for name, value in limits.items():
        _positive_limit(value, f"{name} retained limit", allow_zero=True)
    blocked = (
        set(inventory.protected_ids)
        | set(inventory.fixed_source_ids)
        | {target.building_id for target in inventory.targets}
        | set(_ids(fixed_source_ids, "fixed_source_ids"))
    )
    if not blocked <= inventory.by_id.keys():
        raise ValueError("protected/source identities must belong to the inventory")
    full_candidates = _ids(inventory.candidate_ids, "inventory candidates")
    eligible = tuple(
        i
        for i in full_candidates
        if i not in blocked and inventory.by_id[i].source_label == "building"
    )
    seed = _ids(seed_ids, "seed_ids")
    if not set(seed) <= set(eligible):
        raise ValueError("seed must contain only eligible non-source ordinary buildings")
    if isinstance(cleared_area, ClearedAreaFunction) and not cleared_area.owner_ids <= set(
        inventory.by_id
    ):
        raise ValueError("physical patch owners must belong to the inventory")

    area_cache = {}

    def area(ids):
        ids = tuple(sorted(ids))
        if ids not in area_cache:
            value = cleared_area(ids)
            if isinstance(value, bool) or not isfinite(value) or value < 0:
                raise ValueError("cleared area must be finite and nonnegative")
            area_cache[ids] = float(value)
        return area_cache[ids]

    seed_area = area(seed)
    addition_pool = tuple(i for i in eligible if i not in seed)
    singleton_areas = {i: area((i,)) for i in addition_pool}

    def additions_for(removed):
        return sorted(
            addition_pool,
            key=lambda i: (
                singleton_areas[i],
                inventory.by_id[i].geometry.area,
                min(
                    inventory.by_id[i].geometry.distance(inventory.by_id[r].geometry)
                    for r in removed
                ),
                i,
            ),
        )

    def replacements(removed, count):
        ordered = additions_for(removed)
        for added in _small_combinations(ordered, singleton_areas, count):
            yield removed, added

    streams = {
        "single_addback": ((tuple((i,)), ()) for i in seed),
        "single_replacement": _round_robin(replacements((i,), 1) for i in seed),
        "double_exchange": _round_robin(replacements(pair, 2) for pair in combinations(seed, 2)),
        "small_bundle": _round_robin(
            replacements((i,), count) for i in seed for count in range(2, max_bundle_size + 1)
        ),
    }
    active = [name for name in defaults if limits[name]]
    allowance = dict.fromkeys(defaults, 0)
    if active:
        share, remainder = divmod(max_generated - 1, len(active))
        for index, name in enumerate(active):
            allowance[name] = share + (index < remainder)
    seed_set = set(seed)
    proposals, statistics = {}, {}
    for name, stream in streams.items():
        rows = {}
        attempts = 0
        complete = False
        iterator = iter(stream)
        while attempts < allowance[name]:
            try:
                removed, added = next(iterator)
            except StopIteration:
                complete = True
                break
            attempts += 1
            ids = tuple(sorted((seed_set - set(removed)) | set(added)))
            if ids == seed:
                continue
            distance = fsum(
                min(
                    inventory.by_id[a].geometry.distance(inventory.by_id[r].geometry)
                    for r in removed
                )
                for a in added
            )
            rows[ids] = {
                "demolished_ids": list(ids),
                "demolished_count": len(ids),
                "cleared_area_m2": area(ids),
                "area_change_from_seed_m2": area(ids) - seed_area,
                "source": name,
                "removed_from_seed_ids": list(removed),
                "added_to_seed_ids": list(added),
                "replacement_distance_sum_m": distance,
            }
        ranked = sorted(
            rows.values(),
            key=lambda row: (
                row["cleared_area_m2"],
                row["replacement_distance_sum_m"],
                row["demolished_ids"],
            ),
        )
        proposals[name] = ranked[: limits[name]]
        statistics[name] = {
            "attempt_limit": allowance[name],
            "attempted": attempts,
            "distinct_generated": len(rows),
            "retained_before_global_limit": len(proposals[name]),
            "class_stream_exhausted": complete,
            "traversal_limited": not complete and limits[name] > 0,
        }

    selected = [
        {
            "demolished_ids": list(seed),
            "demolished_count": len(seed),
            "cleared_area_m2": seed_area,
            "area_change_from_seed_m2": 0.0,
            "source": "seed",
            "removed_from_seed_ids": [],
            "added_to_seed_ids": [],
            "replacement_distance_sum_m": 0.0,
        }
    ]
    seen = {seed}
    for row in _round_robin(proposals[name] for name in defaults):
        if len(selected) >= max_plans:
            break
        ids = tuple(row["demolished_ids"])
        if ids not in seen:
            selected.append(row)
            seen.add(ids)
    selected[1:] = sorted(
        selected[1:], key=lambda row: (row["cleared_area_m2"], row["demolished_ids"])
    )
    attempted = 1 + sum(row["attempted"] for row in statistics.values())
    return {
        "schema_version": "heritage-area-candidate-generation-1",
        "area_basis": getattr(cleared_area, "area_basis", "caller_supplied_set_function"),
        "simulation_used": False,
        "proposal_scope": "budgeted_seed_neighborhoods;not_full_powerset",
        "full_candidate_space_unresolved": True,
        "global_optimality_proven": False,
        "inventory_candidate_ids": list(full_candidates),
        "inventory_candidate_count": len(full_candidates),
        "eligible_candidate_ids": list(eligible),
        "eligible_candidate_count": len(eligible),
        "excluded_protected_or_source_ids": sorted(set(full_candidates) & blocked),
        "distance_policy": "ordering_only;no_distance_eligibility_cutoff",
        "bundle_ordering_policy": "singleton_area_heuristic;not_a_nonadditive_area_bound",
        "generation_budget": max_generated,
        "generated_attempts_including_seed": attempted,
        "unconsumed_generation_budget": max_generated - attempted,
        "generation_limited": any(row["traversal_limited"] for row in statistics.values()),
        "per_class_statistics": statistics,
        "returned_plan_count": len(selected),
        "plans": selected,
    }
