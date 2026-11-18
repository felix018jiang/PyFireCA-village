"""Compare calm, east and west winds on three synthetic roofs on level ground.

Run from the checkout with python -m examples.research.heritage.compare_roof_wind.
All three roofs have the same material. Only the middle roof starts burning.
"""

from dataclasses import replace
from functools import partial

from examples.research.heritage.synthetic_case import make_case
from shapely.geometry import box

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.environments import ProtectionEnvironment, apply_environment
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.roof_thermal import roof_area_integrated_radiation_graph
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters


def compare_winds():
    roofs = {
        "G": box(8, 8, 10, 10),  # west receiver
        "S": box(12, 8, 14, 10),  # source
        "F": box(16, 8, 18, 10),  # east receiver
    }
    bundle, _, inventory, scenario = make_case(radius=15, geometries=roofs, candidate_ids=())
    scenario = replace(scenario, horizon_s=120)
    plan = BuildingDemolitionPlan("baseline")
    integrate_roofs = partial(
        roof_area_integrated_radiation_graph, integrated_ids=tuple(roofs), quadrature_spacing_m=0.5
    )

    rows = []
    for name, speed, from_deg in (("calm", 0, 0), ("west", 2, 270), ("east", 2, 90)):
        environment = ProtectionEnvironment(
            environment_id=name,
            ignition_rise_factor=1.0,
            wind_speed_m_s=speed,
            wind_from_deg=from_deg,
            flame_tilt_coefficient_s_m=0.2,  # synthetic, common to all three runs
        )
        windy_bundle = apply_environment(bundle, environment)
        transport = V3RadiativeTransportParameters(**windy_bundle.metadata["transport_parameters"])
        engine = RoofHeritageEvaluationEngine(
            windy_bundle,
            transport,
            inventory,
            source_policy="declared_fixed_sources",
            radiation_builder=integrate_roofs,
        )
        record, _, _ = engine.simulate(scenario, plan)
        by_roof = {
            roof["building_id"]: roof["absorbed_radiation_J"] / 1000
            for roof in record.metrics["core_outcomes"]
        }
        rows.append((name, by_roof["G"], by_roof["F"]))
    return rows


if __name__ == "__main__":
    print("wind_from  west_roof_kJ  east_roof_kJ  (synthetic, 120 s)")
    for name, west, east in compare_winds():
        print(f"{name:9}  {west:12.3f}  {east:12.3f}")
