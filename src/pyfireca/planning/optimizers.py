"""Bounded discrete enumeration; no claim of continuous/real-world optimality."""

from dataclasses import dataclass
from itertools import product
from math import prod

from pyfireca.planning.contracts import (
    Plan,
    PointXY,
    Treatment,
    require_id,
    require_number,
    require_point,
)


@dataclass(frozen=True, slots=True)
class CandidateSegment:
    candidate_id: str
    start_m: PointXY
    end_m: PointXY
    width_choices_m: tuple[float, ...]
    profile_id: str

    def __post_init__(self):
        require_id("candidate_id", self.candidate_id)
        require_id("profile_id", self.profile_id)
        require_point(self.start_m)
        require_point(self.end_m)
        if self.start_m == self.end_m:
            raise ValueError("candidate must have positive length")
        if not isinstance(self.width_choices_m, tuple) or len(self.width_choices_m) < 2:
            raise ValueError("width choices must be a tuple including zero and a positive width")
        for width in self.width_choices_m:
            require_number("candidate width", width)
        if self.width_choices_m[0] != 0 or any(
            a >= b for a, b in zip(self.width_choices_m[:-1], self.width_choices_m[1:], strict=True)
        ):
            raise ValueError("width choices must increase strictly from zero (not built)")


@dataclass(frozen=True, slots=True)
class CandidateLayout:
    plan: Plan
    widths_m: tuple[float, ...]


def enumerate_layouts(candidates: tuple[CandidateSegment, ...], *, max_plans: int = 729):
    """Enumerate all choices in stable order or fail before yielding any plan."""
    if (
        not isinstance(candidates, tuple)
        or not candidates
        or not all(isinstance(c, CandidateSegment) for c in candidates)
    ):
        raise ValueError("candidates must be a nonempty tuple of CandidateSegment")
    if len({c.candidate_id for c in candidates}) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    if isinstance(max_plans, bool) or not isinstance(max_plans, int) or max_plans < 1:
        raise ValueError("max_plans must be a positive integer")
    if prod(len(c.width_choices_m) for c in candidates) > max_plans:
        raise ValueError("enumeration plan budget exceeded")
    layouts = []
    for indices in product(*(range(len(c.width_choices_m)) for c in candidates)):
        widths = tuple(c.width_choices_m[i] for c, i in zip(candidates, indices, strict=True))
        treatments = tuple(
            Treatment(c.candidate_id, c.start_m, c.end_m, width, c.profile_id)
            for c, width in zip(candidates, widths, strict=True)
            if width > 0
        )
        layouts.append(
            CandidateLayout(Plan("layout-" + "-".join(map(str, indices)), treatments), widths)
        )
    return tuple(layouts)
