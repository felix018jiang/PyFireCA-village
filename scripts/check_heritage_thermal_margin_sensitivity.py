"""Re-solve the same protective energy inequalities at three prescribed margins."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from audit_heritage_thermal_optimum import material_rows, verify_seal
from optimize_heritage_thermal_clearance import optimize_groups

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def run(cert_path, published_path):
    parent = verify_seal(cert_path)
    published = verify_seal(published_path)
    if published["geometric_certificate_sha256"] != parent["certificate_sha256"]:
        raise ValueError("inconsistent reference certificates")
    original = read_json(Path(parent["native_reference_run"]) / "experiment.json")["config"]
    bundle, transport, inventory = load_study(Path(original["study_path"]))
    if (
        request_fingerprint(bundle.request) != parent["native_case_sha256"]
        or inventory.sha256 != parent["inventory_sha256"]
    ):
        raise ValueError("case or inventory changed")
    layout = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=original["max_nodes"],
        core_overlap_policy=original["core_overlap_policy"],
    )
    receptors, _, contacts = material_rows(bundle.request, layout, transport, inventory)
    roofs = set(parent["roof_actions"])
    cost = defaultdict(float)
    for owners, shape in zip(layout.owners, layout.geometry, strict=True):
        if owners and set(owners) <= roofs:
            cost[tuple(owners)] += shape.area
    for cell in parent["ground_parent_actions"]:
        cost[(cell,)] = bundle.request.grid.cell_area_m2

    # Map the two action types to unique strings for the original optimizer.
    def key(group):
        return tuple(f"G{i}" if isinstance(i, int) else i for i in group)

    group_costs = {key(group): value for group, value in cost.items()}
    doses = np.array([[row.get(group, 0.0) for group in cost] for row in receptors])
    records = []
    for limit in (0.90, 0.99, 1.0):
        selected, result = optimize_groups(
            group_costs, doses, {key(g) for g in contacts}, np.full(len(receptors), limit), 120.0
        )
        peak = result["realized_max_fraction_of_reserved_energy_capacity"] * limit
        if peak >= 1:
            raise ValueError("the closed relaxation did not yield a strictly protective witness")
        if (
            limit == 0.99
            and abs(result["construction_area_m2"] - published["construction_area_m2"]) > 1e-6
        ):
            raise ValueError("sensitivity did not reproduce the registered 1 percent result")
        records.append(
            {
                "allowed_fraction": limit,
                "max_recomputed_fraction": peak,
                "cost_m2": result["construction_area_m2"],
                "dual_lower_bound_m2": result["dual_area_bound_m2"],
                "solver_status": result["solver_status"],
                "roof_actions": sorted(selected & roofs),
                "ground_parent_actions": sorted(
                    i for i in parent["ground_parent_actions"] if f"G{i}" in selected
                ),
            }
        )
    return {
        "village": parent["village"],
        "parent_sha256": file_sha256(cert_path),
        "registered_energy_sha256": file_sha256(published_path),
        "margins": records,
        "case_observations_used_for_selection": 0,
        "fresh_native_fires": 0,
        "optimality_scope": "integer actions and thermal inequalities, floating-point MILP",
        "registered_fire_comparison_arms_unchanged": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--separation", type=Path, required=True)
    parser.add_argument("--energy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.separation, args.energy)
    result["method_sha256"] = file_sha256(Path(__file__))
    result["receipt_sha256"] = content_hash(result)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
