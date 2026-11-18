"""Area-bounded coverage of retained-source priority permutations.

If the first k qualified sources are all removed, the actual demolition plan
contains that entire prefix. Nonnegative AND-owned physical patch costs imply
that its cleared area is at least the cost of the prefix. A prefix whose cost
strictly exceeds the declared search-area cap therefore contains every possible
first retained source. This is a source-capacity proof, not an optimality proof.
No simulation, arbitrary monotonicity assumption, or cardinality bound is used.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from math import fsum, isfinite, nextafter

from shapely.ops import unary_union
from shapely.strtree import STRtree

from pyfireca.planning.area_candidates import ClearedAreaFunction
from pyfireca.planning.heritage import content_hash

_QUALIFICATION_CACHE = OrderedDict()


def _qualification_context(inventory):
    """Cache only deterministic, locally computed witnesses bound to inventory SHA."""
    fingerprint = inventory.sha256
    if fingerprint in _QUALIFICATION_CACHE:
        _QUALIFICATION_CACHE.move_to_end(fingerprint)
        return _QUALIFICATION_CACHE[fingerprint]
    buildings = inventory.buildings
    context = {
        "inventory_sha256": fingerprint,
        "buildings": buildings,
        "by_id": inventory.by_id,
        "core_ids": frozenset(t.building_id for t in inventory.targets),
        "tree": STRtree([b.geometry for b in buildings]),
        "witnesses": {},
    }
    _QUALIFICATION_CACHE[fingerprint] = context
    while len(_QUALIFICATION_CACHE) > 4:
        _QUALIFICATION_CACHE.popitem(last=False)
    return context


def _qualified_witness(context, identifier):
    if identifier in context["witnesses"]:
        return context["witnesses"][identifier]
    obj = context["by_id"][identifier]
    if identifier in context["core_ids"] or obj.source_label != "building":
        raise ValueError("qualified source must be an ordinary noncore building")
    # Same intersections and original inventory ordering as unambiguous_roof,
    # with an index replacing a full inventory scan for each source.
    hits = sorted(context["tree"].query(obj.geometry, predicate="intersects").tolist())
    others = [
        context["buildings"][i].geometry
        for i in hits
        if context["buildings"][i].building_id != identifier
    ]
    roof = obj.geometry.difference(unary_union(others)) if others else obj.geometry
    if roof.is_empty or roof.area <= 0:
        raise ValueError("qualified source has no unambiguous roof area")
    witness = identifier, float(roof.area), content_hash(roof.wkb_hex)
    context["witnesses"][identifier] = witness
    return witness


def _ids(values, name):
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a collection of unique IDs")
    values = tuple(values)
    if any(not isinstance(i, str) or not i for i in values) or len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique nonempty IDs")
    return values


def _nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and nonnegative")
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _validated_area(area, known_ids):
    # The dataclass can be constructed directly, so isinstance alone is not a
    # monotonicity witness. Validate every coefficient and ownership literal.
    if not isinstance(area, ClearedAreaFunction):
        raise TypeError("source coverage requires a physical-patch ClearedAreaFunction")
    if not isinstance(area.area_basis, str) or not area.area_basis.strip():
        raise ValueError("an explicit physical-patch area basis is required")
    if (
        isinstance(area.physical_patch_count, bool)
        or not isinstance(area.physical_patch_count, int)
        or area.physical_patch_count < len(area.owner_groups)
    ):
        raise ValueError("physical patch count cannot be smaller than its owner groups")
    groups, seen = [], set()
    for raw_owners, raw_area in area.owner_groups:
        owners = frozenset(_ids(raw_owners, "physical patch owners"))
        if not owners or not owners <= known_ids or owners in seen:
            raise ValueError("physical owner groups must be nonempty, known and unique")
        seen.add(owners)
        groups.append((owners, _nonnegative(raw_area, "physical patch area")))
    try:
        total = fsum(value for _, value in groups)
    except OverflowError as error:
        raise ValueError("physical patch cost overflows") from error
    if not isfinite(total):
        raise ValueError("physical patch cost must be finite")
    return ClearedAreaFunction(tuple(groups), area.area_basis, area.physical_patch_count)


def _area_mapping(area):
    return {
        "area_basis": area.area_basis,
        "physical_patch_count": area.physical_patch_count,
        "owner_groups": [
            {"owners": sorted(owners), "area_m2": value}
            for owners, value in sorted(area.owner_groups, key=lambda group: sorted(group[0]))
        ],
    }


def _strict_lower_area(area, removed):
    """Outward lower bound on the sum of the stored physical coefficients.

    Sum float coefficients as exact binary rationals, round down if necessary,
    and reserve one additional ULP. Borderline values remain unpruned. The CA
    guard and prefix comparison share the identical stored effective area cap.
    This is not a bound on uncertainty in surveyed geometry or construction.
    """
    exact = sum(
        (Fraction.from_float(value) for owners, value in area.owner_groups if owners <= removed),
        Fraction(0),
    )
    value = float(exact)
    if Fraction.from_float(value) > exact:
        value = nextafter(value, float("-inf"))
    return max(0.0, nextafter(value, float("-inf")))


@dataclass(frozen=True)
class SourceAreaCoverage:
    source_ids: tuple[str, ...]
    pre_removed_areas: tuple[float, ...]
    coverage_reason: str
    maximum_cleared_area: float
    area_bound_tolerance_m2: float
    admissible_area_limit_m2: float
    priority_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    inventory_sha256: str
    draw_sha256: str
    area: ClearedAreaFunction
    minimum_source_area_m2: float
    source_geometry_witnesses: tuple[tuple[str, float, str], ...]
    stopping_prefix_area_m2: float | None
    stopping_prefix_lower_bound_m2: float | None
    stopping_removed_prefix_ids: tuple[str, ...]

    @property
    def area_function_sha256(self):
        return content_hash(_area_mapping(self.area))

    def to_mapping(self):
        value = {
            "schema_version": "retained-source-area-coverage-1",
            "source_ids": list(self.source_ids),
            "pre_removed_areas": list(self.pre_removed_areas),
            "coverage_reason": self.coverage_reason,
            "maximum_cleared_area": self.maximum_cleared_area,
            "area_bound_tolerance_m2": self.area_bound_tolerance_m2,
            "admissible_area_limit_m2": self.admissible_area_limit_m2,
            "priority_ids": list(self.priority_ids),
            "candidate_ids": list(self.candidate_ids),
            "inventory_sha256": self.inventory_sha256,
            "draw_sha256": self.draw_sha256,
            "area_function_sha256": self.area_function_sha256,
            "monotonicity_witness": {
                "form": "sum_nonnegative_disjoint_patch_areas_times_AND_removed_owners",
                "physical_partition": _area_mapping(self.area),
                "physical_partition_geometry_validation": "caller_supplied_partition_contract",
            },
            "minimum_source_area_m2": self.minimum_source_area_m2,
            "source_geometry_witnesses": [
                {"source_id": i, "unambiguous_roof_area_m2": a, "geometry_sha256": digest}
                for i, a, digest in self.source_geometry_witnesses
            ],
            "stopping_prefix_area_m2": self.stopping_prefix_area_m2,
            "stopping_prefix_lower_bound_m2": self.stopping_prefix_lower_bound_m2,
            "stopping_removed_prefix_ids": list(self.stopping_removed_prefix_ids),
            "no_retained_source_outcome_possible": (
                set(self.priority_ids) <= set(self.candidate_ids)
                and self.area(self.priority_ids) <= self.admissible_area_limit_m2
            ),
            "no_source_policy": "unknown;never_successful_no_fire",
            "upper_bound_is_optimality_proof": False,
            "scope": "only_same_inventory_draw_action_set_and_admissible_area_cap",
        }
        value["coverage_sha256"] = content_hash(value)
        return value

    def validate_plan(self, demolished_ids, *, cleared_area=None):
        """Reject every plan outside the very cap used in the prefix proof.

        Call this before an oracle that only prepared ``source_ids``. Returning
        source_id=None explicitly requires an unknown/no-retained-source result.
        A supplied current area function must match the frozen cost witness.
        """
        ids = tuple(sorted(_ids(demolished_ids, "demolished IDs")))
        if not set(ids) <= set(self.candidate_ids):
            raise ValueError("plan contains an action outside the covered candidate set")
        if cleared_area is not None:
            checked = _validated_area(cleared_area, self.area.owner_ids)
            if content_hash(_area_mapping(checked)) != self.area_function_sha256:
                raise ValueError("plan guard uses a changed physical area function")
        value = self.area(ids)
        if value > self.admissible_area_limit_m2:
            raise ValueError(
                "plan exceeds the source-coverage area cap including its frozen tolerance"
            )
        source = next((i for i in self.priority_ids if i not in ids), None)
        if source is not None and source not in self.source_ids:
            raise ValueError("retained source is outside its area-coverage certificate")
        return {
            "source_id": source,
            "status": "covered_source" if source is not None else "no_retained_source_unknown",
            "cleared_area_m2": value,
            "admissible_area_limit_m2": self.admissible_area_limit_m2,
            "coverage_sha256": self.to_mapping()["coverage_sha256"],
        }


def cover_source_priority_by_area(
    draw,
    inventory,
    candidate_ids,
    cleared_area,
    maximum_cleared_area,
    *,
    area_bound_tolerance_m2=0.0,
    minimum_source_area_m2=25.0,
):
    """Bound all first-retained sources for plans costing at most UB+tolerance.

    A verified feasible plan may supply UB, but its feasibility and optimality
    are not inferred here. No prefix cap is based on demolition count. Source
    exclusive geometry is validated and recorded, but no additional exclusive
    area bound is substituted for the actual physical-patch cost.
    """
    if not isinstance(draw, dict) or not draw.get("priority_ids"):
        raise ValueError("a complete nonempty frozen priority draw is required")
    priority = _ids(draw["priority_ids"], "source priority")
    if draw.get("baseline_source_id") != priority[0]:
        raise ValueError("baseline source must be first in the frozen priority")
    candidates = tuple(sorted(_ids(candidate_ids, "candidate IDs")))
    known = set(inventory.by_id)
    forbidden = set(inventory.protected_ids) | set(inventory.fixed_source_ids)
    forbidden |= {t.building_id for t in inventory.targets}
    if not set(candidates) <= set(inventory.candidate_ids) or set(candidates) & forbidden:
        raise ValueError(
            "candidate actions must be eligible and cannot remove protected/fixed objects"
        )
    if any(inventory.by_id[i].source_label != "building" for i in candidates):
        raise ValueError("candidate actions must be ordinary buildings")
    if not set(priority) <= known:
        raise ValueError("source priority references an unknown inventory object")
    maximum = _nonnegative(maximum_cleared_area, "maximum cleared area")
    tolerance = _nonnegative(area_bound_tolerance_m2, "area bound tolerance")
    try:
        limit = fsum((maximum, tolerance))
    except OverflowError as error:
        raise ValueError("effective source-coverage area cap overflows") from error
    if not isfinite(limit):
        raise ValueError("effective source-coverage area cap must be finite")
    minimum = _nonnegative(minimum_source_area_m2, "minimum source area")
    if minimum <= 0:
        raise ValueError("minimum source area must be positive")
    area = _validated_area(cleared_area, known)
    witnesses = []
    context = _qualification_context(inventory)
    for identifier in priority:
        witness = _qualified_witness(context, identifier)
        if witness[1] < minimum:
            raise ValueError("qualified source has insufficient unambiguous roof area")
        witnesses.append(witness)
    covered, pre_removed, removed = [], [], set()
    reason = "full_priority_without_area_closure"
    stopping_area = stopping_lower = None
    for identifier in priority:
        covered.append(identifier)
        pre_removed.append(area(removed))
        if identifier not in candidates:
            reason = "fixed_retained_source_in_prefix"
            break
        removed.add(identifier)
        stopping_area = area(removed)
        stopping_lower = _strict_lower_area(area, removed)
        if stopping_lower > limit:
            reason = "strict_prefix_area_exceeds_admissible_cap"
            break
    return SourceAreaCoverage(
        tuple(covered),
        tuple(pre_removed),
        reason,
        maximum,
        tolerance,
        limit,
        priority,
        candidates,
        context["inventory_sha256"],
        content_hash(draw),
        area,
        minimum,
        tuple(witnesses),
        stopping_area,
        stopping_lower,
        tuple(i for i in priority if i in removed),
    )
