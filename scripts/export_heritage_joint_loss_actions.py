"""Export the combined physical clearing polygons as GIS layers."""

import argparse
import json
from pathlib import Path

import shapely
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import demolition_geometry, file_sha256, read_json
from pyfireca.planning.study_io import load_study


def physical_features(study_path, plan):
    bundle, _, inventory = load_study(study_path)
    request, grid = bundle.request, bundle.request.grid
    roofs, cells = plan["demolished_ids"], plan["cleared_physical_cell_ids"]
    _, _, cleared, _ = demolition_geometry(inventory, roofs)
    half = grid.cell_size_m / 2
    ground = []
    for cell in cells:
        i, j = divmod(cell, grid.width)
        if not (
            request.domain_mask[i, j]
            and request.known_mask[i, j]
            and request.burnable_mask[i, j]
            and request.building_index[i, j] < 0
            and request.parameters.covered_area_m2[i, j] > 0
        ):
            raise ValueError("ineligible ground cell in placement")
        x, y = grid.center(i, j)
        ground.append(box(x - half, y - half, x + half, y + half))
    grass = shapely.union_all(ground)
    all_roofs = shapely.union_all([b.geometry for b in inventory.buildings])
    if (
        grass.intersection(all_roofs).area > 1e-8
        or abs(grass.union(cleared).area - plan["C_m2"]) > 1e-5
    ):
        raise ValueError("physical union differs from the certified cost")
    forward = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    inverse = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    features = []
    for kind, geom, ids in (("roof_union", cleared, roofs), ("whole_ground_cells", grass, cells)):
        if geom.is_empty:
            if ids:
                raise ValueError("declared action has no physical footprint")
            continue
        projected = transform(forward.transform, geom)
        if not projected.is_valid:
            projected = shapely.make_valid(projected, method="structure", keep_collapsed=False)
        projected = shapely.orient_polygons(projected)
        if not projected.is_valid or projected.geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError("coordinate transformation did not produce a valid polygon layer")
        coordinates = mapping(projected)
        restored = transform(inverse.transform, shape(coordinates))
        restored_surface = shapely.make_valid(restored, method="structure", keep_collapsed=False)
        if (
            abs(restored_surface.area - geom.area) > 1e-4
            or restored_surface.symmetric_difference(geom).area > 1e-4
        ):
            raise ValueError("coordinate round trip changed the physical placement")
        features.append(
            {
                "type": "Feature",
                "properties": {"material": kind, "actual_m2": geom.area, "action_ids": ids},
                "geometry": coordinates,
            }
        )
    return features, str(grid.crs)


def export(result_path, output_path, plan_id=None):
    result = read_json(result_path)
    if plan_id is None:
        if result["receipt_sha256"] != content_hash(
            {k: v for k, v in result.items() if k != "receipt_sha256"}
        ):
            raise ValueError("combined placement digest mismatch")
        village = result["village"]
        folder = Path(__file__).resolve().parents[1] / "examples/research/heritage/area-20261110"
        config = read_json(folder / f"{village}-compartment-source-reference-20261111.json")
        plan = next(
            x for x in result["optimized_total_construction"] if x["core_energy_ceiling"] == 0.99
        )
        value = dict(
            name=f"{village}_combined_firebreak",
            registered_families=result["fixed_25m2_source_budget_count"],
            registered_burned_area_upper_m2=result["worst_registered_A_upper_m2"],
            registered_building_owner_upper=result["worst_registered_N_upper"],
        )
    else:
        run = Path(result["run_root"])
        experiment = read_json(run / "experiment.json")
        config = experiment["config"]
        if (
            result["temporal_mode"] != "strict"
            or not result["native_saved_state_and_peak_certificates_verified"]
            or file_sha256(run / "experiment.json") != result["experiment_sha256"]
        ):
            raise ValueError("a complete strict native archive is required")
        native = next(
            r
            for r in result["archive"]["fully_evaluated_feasible_plans"]
            if r["plan_id"] == plan_id
        )
        plan = next(r for r in config["plans"] if r["plan_id"] == plan_id)
        if any(
            sorted(plan[k]) != sorted(native[k])
            for k in ("demolished_ids", "cleared_physical_cell_ids")
        ):
            raise ValueError("native action differs from its registered geometry")
        plan = {**plan, "C_m2": native["cleared_area_m2"]}
        value = dict(
            name=plan_id,
            registered_families=len(config["source_fires"]),
            measured_worst_burned_area_m2=native["worst_case"]["A_burned_out_m2"],
            measured_worst_owner_counts=native["worst_case"]["N_burned_out"],
        )
    features, crs = physical_features(Path(config["study_path"]), plan)
    value.update(
        type="FeatureCollection",
        features=features,
        metric_crs=crs,
        construction_m2=plan["C_m2"],
        certificate_file_sha256=file_sha256(result_path),
    )
    with output_path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan-id", help="Export a completed action from a strict native archive")
    args = parser.parse_args()
    export(args.result, args.output, args.plan_id)
