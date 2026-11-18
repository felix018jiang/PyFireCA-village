"""Reusable policy choices; these are not multi-village fire validation."""

import pytest
from shapely.geometry import box

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.heritage import BuildingInventory, BuildingObject, HeritageTarget
from pyfireca.planning.heritage_strategy import plan_heritage_strategy


def inventory(prefix):
    return BuildingInventory(
        tuple(
            BuildingObject(prefix + name, n, "building", geometry)
            for n, (name, geometry) in enumerate(
                (("small", box(0, 0, 2, 2)), ("large", box(4, 0, 7, 3)), ("core", box(8, 0, 9, 1)))
            )
        ),
        (HeritageTarget(prefix + "core", "gulou", "synthetic", "synthetic"),),
        candidate_ids=(prefix + "small", prefix + "large"),
        candidate_evidence="synthetic",
    )


@pytest.mark.parametrize("prefix", ["village_alpha_", "另一村落_"])
def test_same_algorithm_uses_configured_ids_and_distinguishes_area_from_money(prefix):
    inv = inventory(prefix)

    def evaluate(ids, _scenario):
        return ProtectionCheck("passed" if len(ids) == 1 else "failed", {})

    arguments = {"physical_problem_sha256": "frozen-physics-" + prefix, "max_new_checks": 32}
    area = plan_heritage_strategy(inv, ("scenario-1", "scenario-2"), evaluate, **arguments)
    money = plan_heritage_strategy(
        inv,
        ("scenario-1", "scenario-2"),
        evaluate,
        declared_cost_by_id={prefix + "small": 100, prefix + "large": 10},
        cost_unit="CNY",
        **arguments,
    )
    assert area["optimality_proven"] and money["optimality_proven"]
    assert area["best_ids"] == [prefix + "small"] and not area["declared_cost_provided"]
    assert area["best_nominal_area_m2"] == 4 and area["best_declared_cost"] is None
    assert money["best_ids"] == [prefix + "large"] and money["best_objective"] == [1, 10]
    assert money["best_nominal_area_m2"] == 9 and money["secondary_unit"] == "CNY"
    assert money["problem_sha256"] != area["problem_sha256"]


def test_missing_cost_or_unit_never_silently_uses_area_as_money():
    inv = inventory("")
    for costs, unit in (({"small": 1}, "CNY"), ({"small": 1, "large": 2}, None)):
        with pytest.raises(ValueError, match="every candidate"):
            plan_heritage_strategy(
                inv,
                ("s",),
                None,
                physical_problem_sha256="p",
                max_new_checks=32,
                declared_cost_by_id=costs,
                cost_unit=unit,
            )
    with pytest.raises(ValueError, match="explicit building costs"):
        plan_heritage_strategy(
            inv, ("s",), None, physical_problem_sha256="p", max_new_checks=32, cost_unit="CNY"
        )
    with pytest.raises(ValueError, match="eligible inventory"):
        plan_heritage_strategy(
            inv,
            ("s",),
            None,
            physical_problem_sha256="p",
            max_new_checks=32,
            candidate_ids=("core",),
        )
