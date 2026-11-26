"""Measure how much of the original ordinary village roof fabric each placement retains."""

import argparse
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from check_heritage_compartment_first_escape import load_bound_inputs
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]


def cases(village):
    fixed = ROOT / "docs/results"
    ref = ROOT / "runs/research"
    files = {}

    def load(path):
        files[str(path.relative_to(ROOT))] = file_sha256(path)
        return read_json(path)

    combo = load(fixed / f"heritage-total-protection-and-loss-{village}-20261111.json")
    joint = next(
        p for p in combo["optimized_total_construction"] if p["core_energy_ceiling"] == 0.99
    )
    plans = [("design-compartment", joint, joint["C_m2"])]
    if village == "batuan":
        data = load(fixed / "heritage-batuan-direct-D9-G0-complete8-audit-20261111.json")
        plans.append(
            ("economic", data["plan"], data["scenario_rows"][0]["C_construction_action_area_m2"])
        )
        both = load(fixed / "heritage-no-external-core-heat-with-fixed-loss-bounds-20261111.json")
        zero = next(r for r in both["villages"] if r["village"] == village)
        plans.append(("zero-heat-compartment", zero, zero["C_m2"]))
    else:
        catalogue = load(ref / "heritage-protected-union-pingyan-20261111/experiment.json")
        costs = load(fixed / "heritage-pingyan-six-options-loss-capped-minimum-20261111.json")
        for row in catalogue["config"]["plans"]:
            if row["plan_id"].endswith(("w2.5", "w15.01")):
                price = next(
                    r["C_m2"] for r in costs["observations"] if r["policy"] == row["plan_id"]
                )
                plans.append((row["plan_id"], row, price))
        compact = load(
            fixed / "heritage-pingyan-observed-clearance-subset-registration-20261111.json"
        )
        plans.append(("local-compact", compact["action"], compact["C_m2"]))
        origin = load(
            ref / "heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
        )
        base = next(
            p
            for p in origin["config"]["plans"]
            if p["plan_id"] == "minimum-ground-centre-core-clearance"
        )
        for name, file in [
            ("zero-heat-original-four", "heritage-pingyan-zero-heat-and-design-loss"),
            ("zero-heat-enumerated-four", "heritage-pingyan-zero-heat-first-extensions-minimum"),
            ("zero-heat-known-eight", "heritage-pingyan-eight-known-zero-heat-and-design-loss"),
        ]:
            d = load(fixed / (file + "-20261111.json"))
            plans.append((name, d["model_action"], d["model_action"]["C_m2"]))
            if name == "zero-heat-original-four":
                plans.append(("core-only-zero-heat", base, d["base_zero_heat_construction_m2"]))
    return plans, files


def audit(village):
    plans, files = cases(village)
    _, _, layout, inv, _, _, _, _, _ = load_bound_inputs(village)
    protected = {c.building_id for c in inv.targets}
    groups = defaultdict(list)
    for i, owners in enumerate(layout.owners):
        if owners and protected.isdisjoint(owners):
            groups[tuple(owners)].append(layout.geometry[i].area)
    area = {g: math.fsum(a) for g, a in groups.items()}
    ordinary = set.union(*(set(g) for g in groups))
    if ordinary != set(inv.candidate_ids):
        raise ValueError("ordinary roof inventory differs from the positive modeled footprint")
    whole = math.fsum(area.values())
    result = []
    for name, plan, expected_cost in plans:
        D, G = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
        if not D.issubset(ordinary) or not protected.isdisjoint(D):
            raise ValueError("invalid roof clearing")
        active_ground = layout.physical_request.burnable_mask.ravel()
        if G and (
            np.any(layout.physical_request.building_index.ravel()[sorted(G)] >= 0)
            or not active_ground[sorted(G)].all()
        ):
            raise ValueError("invalid ground clearing")
        cleared, kept = set(), set()
        for g in groups:
            (cleared if set(g) <= D else kept).update(g)
        removed_area = math.fsum(a for g, a in area.items() if set(g) <= D)
        grass = len(G) * layout.physical_request.grid.cell_area_m2
        if not math.isclose(expected_cost, removed_area + grass, rel_tol=1e-10, abs_tol=1e-5):
            raise ValueError("physical construction area differs")
        result.append(
            dict(
                policy=name,
                C_m2=expected_cost,
                ordinary_roof_removed_m2=removed_area,
                ground_cleared_m2=grass,
                original_ordinary_roof_union_retained_fraction=(whole - removed_area) / whole,
                selected_ordinary_roof_labels=len(D),
                fully_physically_removed_original_ordinary_labels=len(cleared - kept),
                partly_physically_removed_original_ordinary_labels=len(cleared & kept),
                ordinary_original_labels_with_remaining_roof=len(kept),
            )
        )
    return dict(
        village=village,
        original_ordinary_roof_union_m2=whole,
        original_ordinary_labels=len(ordinary),
        policies=result,
        source_files_sha256=files,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    path = parser.parse_args().output
    result = dict(
        villages=[audit(v) for v in ("batuan", "pingyan")],
        basis="Original positive modeled ordinary roof union before fire, "
        "including shared physical roof retained until all its owners are selected.",
        new_CA_runs=0,
        method_sha256=file_sha256(__file__),
    )
    result["receipt_sha256"] = content_hash(result)
    immutable(path, encoded(result))
