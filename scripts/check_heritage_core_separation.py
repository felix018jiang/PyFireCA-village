"""Minimum clearing for physical separation of protected roofs under the current finite model."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import shapely

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study
from pyfireca.village.physics_v2 import net_surface_heat_flux_W_m2


def minimum_separation(layout, inventory, radius):
    """Eliminate every combustible exterior parent patch at distance <= radius from a core.

    Whole owner sets and whole ground cells are the indivisible actions. A patch
    can be eliminated only by its full owner set, or its one ground action.
    This yields an inclusion-minimal action set contained in every feasible one.
    Actual separated geometry can have additional zero-cost equivalent actions.
    """
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("positive finite model support required")
    parent = layout.physical_request
    protected = {target.building_id for target in inventory.targets}
    if not protected or parent.firebrands is not None:
        raise ValueError("the certificate requires protected targets and disabled firebrands")
    core = np.array([bool(protected.intersection(ids)) for ids in layout.owners])
    if protected != {i for ids in layout.owners for i in ids if i in protected}:
        raise ValueError("a protected roof is absent from the physical layout")
    polygons = np.asarray(layout.geometry, dtype=object)
    union = shapely.union_all(polygons[core])
    burnable = layout.node_inputs.burnable_mask.ravel()
    external = np.flatnonzero(~core & burnable)
    # Direct distances to the union also include exterior patches sharing a boundary.
    distances = shapely.distance(polygons[external], union)
    required = external[distances <= radius]
    base_eligible = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    roof_cells = {int(c) for c, ids in zip(layout.cell_id, layout.owners, strict=True) if ids}
    candidates = set(inventory.candidate_ids)
    buildings, cells = set(), set()
    blocked = 0
    for node in required:
        ids = layout.owners[node]
        cell = int(layout.cell_id[node])
        if ids:
            if not set(ids) <= candidates:
                blocked += 1
            buildings.update(ids)
        elif not base_eligible[cell] or cell in roof_cells:
            blocked += 1
        else:
            cells.add(cell)
    if blocked:
        return {"minimum_separation_feasible": False, "unremovable_required_patch_count": blocked}
    roof_clear = np.array([bool(ids) and set(ids) <= buildings for ids in layout.owners])
    grass_clear = np.isin(layout.cell_id, sorted(cells))
    if np.any(core & (roof_clear | grass_clear)) or grass_clear.sum() != len(cells):
        raise ValueError("protected retention or whole unoccupied ground cell contract failed")
    retained = external[~(roof_clear | grass_clear)[external]]
    remaining_distance = None
    if len(retained):
        # Independent tree query over all retained geometries and individual core atoms.
        tree = shapely.STRtree(polygons[retained])
        crossings = tree.query(polygons[core], predicate="dwithin", distance=radius)
        if crossings.shape[1]:
            raise ValueError("construction has a residual possible cross-boundary support")
        _, nearest = tree.query_nearest(polygons[core], all_matches=False, return_distance=True)
        remaining_distance = float(nearest.min())
        if remaining_distance <= radius + 1e-8:
            raise ValueError("remaining physical gap cannot be separated at numerical tolerance")
    if len(required) and not np.all((roof_clear | grass_clear)[required]):
        raise ValueError("an obligatory patch was retained")
    area = build_cleared_area_function(
        layout.owners, shapely.area(polygons), area_basis="all_original_owners_removed"
    )
    roof_area = float(area(buildings))
    grass_area = len(cells) * parent.grid.cell_area_m2
    joint_geometry = shapely.union_all(polygons[roof_clear | grass_clear])
    if not np.isclose(joint_geometry.area, roof_area + grass_area, rtol=0, atol=1e-6):
        raise ValueError("geometry union and charged action area differ")
    physics = layout.node_inputs.parameters
    ambient = parent.scenario.ambient_temperature_K
    initial = np.full(core.sum(), ambient)
    zero = np.zeros_like(initial)
    flux = net_surface_heat_flux_W_m2(
        initial,
        zero,
        zero,
        physics.absorptivity.ravel()[core],
        physics.emissivity.ravel()[core],
        physics.ambient_heat_transfer_coefficient_W_m2_K.ravel()[core],
        physics.backing_conductance_W_m2_K.ravel()[core],
        ambient,
    )
    if np.any(flux > 0) or np.any(physics.ignition_temperature_K.ravel()[core] <= ambient):
        raise ValueError("ambient unignited protected state is not invariant")
    return {
        "minimum_separation_feasible": True,
        "minimum_construction_area_m2": roof_area + grass_area,
        "roof_union_m2": roof_area,
        "ground_footprint_m2": grass_area,
        "roof_actions": sorted(buildings),
        "ground_parent_actions": sorted(cells),
        "protected_building_count": len(protected),
        "necessary_external_parent_patch_count": len(required),
        "remaining_external_burnable_parent_patch_count": len(retained),
        "remaining_cross_support_pair_count": 0,
        "minimum_remaining_gap_m": remaining_distance,
        "model_radius_m": radius,
        "initial_ambient_K": ambient,
        "positive_ambient_heat_flux_without_external_input": False,
        "constraints": (
            "all retained burnable exterior polygons strictly farther than the model radius "
            "from every protected roof"
        ),
        "minimality": (
            "each required ground patch forces its whole cell; each required roof patch forces "
            "all its owners; cost is monotone under action inclusion"
        ),
        "protection_condition": (
            "all prescribed direct ignitions on exterior nodes; initial ambient state; "
            "no incoming firebrands or other heat sources; finite model"
        ),
        "whole_village_burned_count": None,
        "whole_village_burned_area_m2": None,
    }


def run(input_run):
    experiment = read_json(input_run / "experiment.json")
    config, problem = experiment["config"], experiment["problem"]
    bundle, transport, inventory = load_study(Path(config["study_path"]))
    if (
        request_fingerprint(bundle.request) != problem["case_sha256"]
        or inventory.sha256 != problem["inventory_sha256"]
        or json.loads(json.dumps(asdict(transport))) != problem["transport_parameters"]
    ):
        raise ValueError("model inputs differ from their frozen native fingerprints")
    root = Path(__file__).resolve().parents[1]
    for source, digest in experiment["source_sha256"].items():
        if source.startswith("src/") and file_sha256(root / source) != digest:
            raise ValueError("live physical code differs from the frozen source: " + source)
    layout = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    result = minimum_separation(layout, inventory, transport.exposure_radius_m)
    result.update(
        schema_version="physical_core_separation_certificate_1",
        village=config["village_id"],
        native_reference_run=str(input_run.resolve()),
        native_problem_sha256=experiment["problem_sha256"],
        native_case_sha256=problem["case_sha256"],
        inventory_sha256=inventory.sha256,
        numerical_support_policy=(
            "polygon separation encloses point/area radiation supports and direct boundary contact"
        ),
        method_sha256=file_sha256(Path(__file__)),
        native_CA_checks=0,
    )
    result["certificate_sha256"] = content_hash(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    certificate = run(args.run)
    with args.output.open("x") as handle:
        json.dump(certificate, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(
        certificate["village"],
        certificate.get("minimum_construction_area_m2"),
        certificate["minimum_separation_feasible"],
    )
