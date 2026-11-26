"""Reuse source-independent full-domain geometry when resuming the frozen experiment."""

import argparse
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from time import monotonic

import numpy as np
import run_heritage_geometry_validation as geometry
import run_heritage_wind_strategies as wind
import run_heritage_wind_validation as validation
from shapely.geometry import shape

from pyfireca.planning.roof_ignition import RoofIgnitionFootprint, refine_roof_layout
from pyfireca.village.ignition import VillageIgnitionEvent

PREPARE = validation.prepare_domain
STUDIES = {}


@lru_cache(maxsize=1)
def full_baseline(key, window, overlap):
    bundle, inventory, anchor = STUDIES[key]
    domain = wind.crop_wind_domain(bundle, inventory, anchor, window)
    grid = bundle.request.grid
    if domain.window != (0, grid.height, 0, grid.width):
        raise ValueError("Geometry reuse is valid only for the complete physical grid")
    layout = wind.resolve_roof_layout(
        domain.bundle.request, domain.inventory, max_nodes=600_000, core_overlap_policy=overlap
    )
    return domain, layout


def prepare(bundle, inventory, fire, core_ids, window, *, scale, overlap):
    key = id(bundle), id(inventory)
    if key not in STUDIES:
        STUDIES.clear()
        STUDIES[key] = (bundle, inventory, fire["anchor_m"])
    domain, baseline = full_baseline(key, window, overlap)
    physical = domain.bundle.request
    row, col = physical.grid.locate(*fire["anchor_m"])
    scenario = replace(physical.scenario, ignitions=(VillageIgnitionEvent(0, row, col),))
    physical = replace(physical, scenario=scenario)
    domain = replace(domain, bundle=replace(domain.bundle, request=physical))
    footprint = RoofIgnitionFootprint(
        fire["source_id"], shape(fire["geometry"]), fire["provenance"]
    )
    if footprint.sha256 != fire["footprint_sha256"]:
        raise ValueError("Altered registered ignition footprint")
    ids = sorted({fire["source_id"], *core_ids})
    layout = refine_roof_layout(
        replace(baseline, physical_request=physical),
        domain.inventory,
        cell_size_m=scale,
        refined_ids=ids,
        footprints=(footprint,),
        max_nodes=650_000,
    )
    return domain, layout, ids


def verify(root, reg):
    receipts = []
    for village, cfg in reg["villages"].items():
        bundle, _, inventory = wind.study(cfg["study_path"])
        grid = bundle.request.grid
        window = 2 * max(grid.width, grid.height) * grid.cell_size_m
        kwargs = {"scale": reg["primary"]["roof_scale_m"], "overlap": cfg["core_overlap_policy"]}
        args = (bundle, inventory, cfg["fires"][0], cfg["core_ids"], window)
        prepare(*args, **kwargs)
        # Check reuse across a different roof, independent of the first cached anchor.
        fire = cfg["fires"][-1]
        args = (bundle, inventory, fire, cfg["core_ids"], window)
        start = monotonic()
        original, base, base_ids = PREPARE(*args, **kwargs)
        original_s = monotonic() - start
        start = monotonic()
        reused, same, same_ids = prepare(*args, **kwargs)
        reused_s = monotonic() - start
        if (
            base_ids != same_ids
            or base.sha256 != same.sha256
            or base.ignition_owner != same.ignition_owner
            or original.window != reused.window
            or original.bundle.request.scenario != reused.bundle.request.scenario
        ):
            raise ValueError(f"{village}: cached spatial inputs differ")
        np.testing.assert_array_equal(
            original.physical_boundary_mask, reused.physical_boundary_mask
        )
        for name in ("cell_id", "xy_m", "area_fraction", "support_area_m2"):
            np.testing.assert_array_equal(getattr(base, name), getattr(same, name))
        for name in base.node_inputs.parameters.__dataclass_fields__:
            np.testing.assert_array_equal(
                getattr(base.node_inputs.parameters, name),
                getattr(same.node_inputs.parameters, name),
            )
        for name in base.node_inputs.__dataclass_fields__:
            if isinstance(field := getattr(base.node_inputs, name), np.ndarray):
                np.testing.assert_array_equal(field, getattr(same.node_inputs, name))
        receipts.append(
            {
                "village": village,
                "independent_test_roof": fire["source_id"],
                "both_layout_sha256": base.sha256,
                "original_setup_wall_s": original_s,
                "reused_setup_wall_s": reused_s,
                "identical_arrays_and_ignition": True,
            }
        )
        print(
            village,
            "exact geometry replay",
            round(original_s, 1),
            "→",
            round(reused_s, 1),
            flush=True,
        )
    return {
        "registration_sha256": wind.digest(root / "registration.json"),
        "scheduler_sha256": wind.digest(Path(__file__)),
        "cases": receipts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--output", type=Path, default=geometry.DEST)
    parser.add_argument("--verify-only", action="store_true")
    args, _ = parser.parse_known_args()
    root = args.output.resolve()
    reg = geometry.verify_registration(root)
    path = root / "geometry-reuse-verification.json"
    if args.verify_only:
        output = verify(root, reg)
        output["verification_sha256"] = wind.record_hash(output)
        wind.atomic_json(path, output)
        return
    receipt = geometry.read_json(path)
    if (
        receipt.pop("verification_sha256") != wind.record_hash(receipt)
        or receipt["registration_sha256"] != wind.digest(root / "registration.json")
        or receipt["scheduler_sha256"] != wind.digest(Path(__file__))
        or {r["village"] for r in receipt["cases"] if r["identical_arrays_and_ignition"]}
        != set(reg["villages"])
    ):
        raise ValueError(
            "Source-independent geometry reuse requires the exact verification receipt"
        )
    validation.prepare_domain = prepare
    geometry.main()


if __name__ == "__main__":
    main()
