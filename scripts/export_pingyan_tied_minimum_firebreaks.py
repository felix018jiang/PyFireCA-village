"""Export the three committed finite-family minima as defensive material layers."""

import math
from pathlib import Path

from audit_pingyan_fixed_actions_eight_coverage import (
    MIN,
    MIN2,
    RES,
    Experiment,
    sealed,
    tied_actions,
)
from check_heritage_registered_roof_compartment import verify
from export_heritage_joint_loss_actions import physical_features
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256


def run():
    small = sealed(MIN)
    independent = sealed(MIN2)
    archive = RES / "heritage-pingyan-four-fixed-placements-eight-coverage-20261111.json"
    coverage = sealed(archive)
    e = Experiment()
    targets = tied_actions(e, small)
    study = Path(e.cfg["study_path"])
    costs = {r["frozen_action_sha256"]: r["C_m2"] for r in coverage["placements"]}
    verify(
        set(targets) == set(small["canonical_tied_placements_sha256"])
        and len(targets) == independent["distinct_exact_tied_placements"] == 3,
        "geometry does not cover all three frozen placements",
    )
    features = []
    for number, (key, (D, G, C)) in enumerate(targets.items(), 1):
        action = dict(demolished_ids=sorted(D), cleared_physical_cell_ids=sorted(G), C_m2=C)
        parts, crs = physical_features(study, action)
        verify(
            {p["properties"]["material"] for p in parts} == {"roof_union", "whole_ground_cells"}
            and len(parts) == 2
            and abs(math.fsum(p["properties"]["actual_m2"] for p in parts) - costs[key]) < 1e-5,
            "physical material area differs",
        )
        ids = {p["properties"]["material"]: p["properties"]["action_ids"] for p in parts}
        verify(
            content_hash(
                dict(
                    demolished_ids=ids["roof_union"],
                    cleared_physical_cell_ids=ids["whole_ground_cells"],
                )
            )
            == key,
            "GIS actions do not reproduce the frozen placement",
        )
        for p in parts:
            p["properties"].update(
                placement_number=number,
                placement_action_sha256=key,
                placement_construction_union_m2=C,
            )
        features.extend(parts)
    data = dict(
        type="FeatureCollection",
        metric_crs=crs,
        name="pingyan_three_tied_defensive_firebreaks",
        original_minimum_file_sha256=file_sha256(MIN),
        independent_minimum_file_sha256=file_sha256(MIN2),
        eight_scenario_coverage_file_sha256=file_sha256(archive),
        export_method_sha256=file_sha256(__file__),
        features=features,
    )
    immutable(
        RES / "heritage-pingyan-three-tied-minimum-firebreaks-20261111.geojson", encoded(data)
    )


if __name__ == "__main__":
    run()
