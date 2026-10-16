"""Build a terrain-coupled Batuan demo from manually annotated building footprints."""
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.warp import Resampling, reproject

from pyfireca.village.annotations import load_labelme_buildings, write_prepared_buildings
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.imagery_landcover import prepare_rgb_landcover
from pyfireca.village.physics_v3 import resolve_vegetation_physics
from pyfireca.village.solver_v3 import V3Scenario, V3SimulationRequest
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.vegetation_config import VegetationCase
from pyfireca.village.vegetation_materials import load_vegetation_profiles
from pyfireca.village.vegetation_prepare import building_cell_intersections
from pyfireca.village.workflow_v3 import run_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "runs/batuan-slope-contact-20261016"
TRANSPORT = V3RadiativeTransportParameters(15, 5, 0.001, 0, 0, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--annotations",
        type=Path,
        default=ROOT / "data/local/imagery/batuan-mosaic-20261014/batuan-composite-utm49n.json",
    )
    parser.add_argument("--cell-size-m", type=float, default=5.0)
    parser.add_argument("--end-time-s", type=float, default=600.0)
    parser.add_argument("--ignition-row", type=int)
    parser.add_argument("--ignition-col", type=int)
    parser.add_argument(
        "--flame-contact-config",
        type=Path,
        default=ROOT / "examples/village/vegetation_v3/flame-contact-batuan-slope-5m.synthetic.yml",
    )
    parser.add_argument("--no-flame-contact", action="store_true", help="仅用于辐射传播对照")
    parser.add_argument("--keep-unknown", action="store_true", help="保留严格RGB规则未分类的地表")
    args = parser.parse_args()
    if (args.ignition_row is None) != (args.ignition_col is None):
        parser.error("--ignition-row 和 --ignition-col 必须同时指定")
    contact = (
        None if args.no_flame_contact else load_flame_contact_parameters(args.flame_contact_config)
    )
    if contact is not None and args.cell_size_m > contact.max_cell_size_m:
        parser.error("网格大小超过所选接触传热配置的显式上限")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    imagery_root = ROOT / "data/local/imagery/batuan-mosaic-20261014"
    imagery = imagery_root / "batuan-composite-utm49n.tif"
    with rasterio.open(imagery) as source:
        reference = dict(
            width_px=source.width,
            height_px=source.height,
            affine_pixel_to_world=list(source.transform)[:6],
        )
    roofs = load_labelme_buildings(
        args.annotations, reference, labels=("building", "fengyuqiao"), repair_invalid=True
    )
    output.mkdir(parents=True)
    (output / "buildings").mkdir()
    write_prepared_buildings(roofs, output / "buildings/buildings.json")
    shutil.copy2(args.annotations, output / "buildings/source.labelme.json")
    annotation_report = dict(
        source=str(args.annotations.resolve()),
        source_sha256=hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
        structure_count=len(roofs),
        building_count=sum(r["properties"]["source_label"] == "building" for r in roofs),
        bridge_count=sum(r["properties"]["source_label"] == "fengyuqiao" for r in roofs),
        repairs=[
            dict(
                building_id=r["building_id"],
                source_shape_index=r["properties"]["source_shape_index"],
                method=r["properties"]["repair_method"],
            )
            for r in roofs
            if r["properties"]["geometry_repaired"]
        ],
        geometry_evidence="manual_labelme",
        combustion_evidence="synthetic_unmeasured",
        bridge_policy=(
            "retain fengyuqiao label; same synthetic equivalent-building fuel assumption, "
            "not measured construction"
        ),
    )
    print(
        f"人工标注：{annotation_report['building_count']} 栋建筑，"
        f"{annotation_report['bridge_count']} 座风雨桥，"
        f"修复{len(annotation_report['repairs'])}处轮廓",
        flush=True,
    )
    classified = prepare_rgb_landcover(
        imagery,
        output / "classification",
        cell_size_m=args.cell_size_m,
        complete_unknown=not args.keep_unknown,
    )
    grid = VillageGridSpec(**json.loads((classified / "grid.json").read_text()))
    domain = np.load(classified / "arrays/domain_mask.npy")
    raw = np.load(classified / "arrays/landcover_index.npy")
    inferred = np.load(classified / "arrays/inferred_mask.npy")
    landcover = np.full(grid.shape, -1, dtype=np.int32)
    landcover[(raw == 1) & domain] = 4  # woody equivalent fuel hypothesis
    landcover[(raw == 2) & domain] = 1
    landcover[np.isin(raw, [3, 4, 5]) & domain] = 0  # explicit inert surface hypothesis
    intersections, coverage_building, _ = building_cell_intersections(roofs, grid)
    building_cells = (coverage_building > 1e-9) & domain
    landcover[building_cells] = 5
    coverage = np.where(landcover >= 0, 1.0, 0.0)
    coverage[building_cells] = coverage_building[building_cells]
    known = domain & (landcover >= 0)
    evidence = np.where(domain, np.where(known, 1, 0), 255).astype(np.uint8)
    evidence[building_cells] = 2  # annotated geometry; combustion properties remain synthetic
    inferred_nonbuilding = inferred & domain & ~building_cells
    material_index = landcover.copy()
    for cover, material in [(4, 6), (1, 7), (0, 8)]:
        material_index[inferred_nonbuilding & (landcover == cover)] = material
    buildings = np.full(grid.shape, -1, dtype=np.int32)
    identifiers = {record["building_id"]: i for i, record in enumerate(roofs)}
    overlaps = {}
    for identifier, row, col, area in intersections:
        if not building_cells[row, col]:
            continue
        if area > overlaps.get((row, col), 0):
            overlaps[(row, col)] = area
            buildings[row, col] = identifiers[identifier]

    spatial = output / "spatial"
    spatial.mkdir()
    from PIL import Image

    # Generate directly from the source TIFF; never depend on a previous viewer export.
    image_name = "batuan-" + hashlib.sha256(imagery.read_bytes()).hexdigest()[:16] + ".png"
    with rasterio.open(imagery) as source:
        Image.fromarray(source.read().transpose(1, 2, 0)).save(spatial / image_name)
        (spatial / "image_ref.json").write_text(json.dumps(reference))
    shutil.copy2(output / "buildings/buildings.json", spatial / "buildings.json")
    (spatial / "manifest.json").write_text(json.dumps(dict(background=image_name)))
    with (spatial / "building_overlap.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["building_id", "row", "col", "area_m2"])
        writer.writeheader()
        for identifier, row, col, area in intersections:
            if building_cells[row, col]:
                writer.writerow(dict(building_id=identifier, row=row, col=col, area_m2=area))

    profile_file = output / "profiles.synthetic.yml"
    library_raw = yaml.safe_load(
        (ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml").read_text()
    )
    library_raw["library_id"] = "batuan-demo-synthetic-profiles"
    profile_file.write_text(yaml.safe_dump(library_raw, allow_unicode=True, sort_keys=False))
    profiles = load_vegetation_profiles(profile_file)
    profile_ids = np.full(grid.shape, "", dtype="U64")
    for code, profile_id in {
        0: "inert_bare_or_road",
        1: "synthetic_grass",
        4: "synthetic_tree_equivalent",
        5: "synthetic_building",
    }.items():
        profile_ids[landcover == code] = profile_id
    burnable = np.isin(landcover, [1, 4, 5]) & known
    case = VegetationCase(
        path=profile_file,
        case_id="batuan-demo",
        evidence="manual_footprints_with_synthetic_fuels",
        prepared_dir=spatial,
        grid=grid,
        profiles=profiles,
        profile_id_by_cell=profile_ids,
        landcover_index=landcover,
        domain_mask=domain,
        known_mask=known,
        evidence_index=evidence,
        burnable_mask=burnable,
        coverage_fraction=coverage,
        weather={},
        scenario={},
        transport={},
        scope={},
        resources={},
        ignitions=(),
        spatial_budgets=(),
    )
    physics = resolve_vegetation_physics(case).arrays

    dem = ROOT / "data/local/elevation/fabdem-cache/N25E109_FABDEM_V1-2.tif"
    from rasterio.transform import from_origin

    transform = from_origin(grid.x_min_m, grid.y_max_m, grid.cell_size_m, grid.cell_size_m)
    elevation = np.full(grid.shape, np.nan, dtype=np.float64)
    with rasterio.open(dem) as source:
        reproject(
            rasterio.band(source, 1),
            elevation,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        dem_resolution = source.res
    if not np.isfinite(elevation).all():
        raise ValueError("DEM coverage incomplete; do not replace missing terrain with zero")
    with rasterio.open(
        output / "elevation.tif",
        "w",
        driver="GTiff",
        count=1,
        width=grid.width,
        height=grid.height,
        dtype="float32",
        crs=grid.crs,
        transform=transform,
        compress="deflate",
    ) as destination:
        destination.write(elevation.astype(np.float32), 1)
    print(f"地形范围：{elevation.min():.1f}–{elevation.max():.1f} m；构建辐射边", flush=True)
    target = known & (physics.receiver_area_m2 > 0)
    edges = build_v3_radiative_transport_edges(
        grid,
        burnable,
        target,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        TRANSPORT,
        max_edge_count=5_000_000,
        max_memory_mb=2048,
        ground_elevation_m=elevation,
    )
    candidates = np.argwhere(building_cells & burnable)
    if not len(candidates):
        candidates = np.argwhere(burnable)
    if not len(candidates):
        raise ValueError("No burnable ignition candidates")
    # Pick a well-covered building cell near the image centre; users can choose any burnable cell.
    centres = np.array(
        [np.mean(record["polygons"][0]["exterior"][:-1], axis=0) for record in roofs]
    )
    if len(centres):
        density = (((centres[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2) < 60**2).sum(
            axis=1
        )
        x, y = centres[np.argmax(density)]
        centre = np.array(
            [(grid.y_max_m - y) / grid.cell_size_m, (x - grid.x_min_m) / grid.cell_size_m]
        )
    else:
        centre = np.array(grid.shape) / 2
    distance = ((candidates - centre) ** 2).sum(axis=1)
    score = distance + (1 - coverage[tuple(candidates.T)]) * 10000
    row, col = map(int, candidates[np.argmin(score)])
    if args.ignition_row is not None:
        row, col = args.ignition_row, args.ignition_col
        if not (0 <= row < grid.height and 0 <= col < grid.width and burnable[row, col]):
            raise ValueError("所选起火点必须位于可燃格")
    scenario = V3Scenario(1.0, args.end_time_s, 300.0, 0.01, (VillageIgnitionEvent(0.0, row, col),))
    request = V3SimulationRequest(
        grid,
        domain,
        known,
        burnable,
        burnable,
        target,
        burnable,
        np.where(known, material_index, -1),
        landcover,
        buildings,
        physics,
        edges,
        scenario,
        unknown_policy="exclude" if args.keep_unknown else "error",
        sample_interval_s=max(1.0, args.end_time_s / 100),
        ground_elevation_m=elevation,
        material_evidence_index=evidence,
        flame_contact=contact,
    )
    labels = {
        0: "裸土/硬化/水体（不燃假设）",
        1: "草地/农田等效燃料（RGB推测）",
        4: "林木/灌丛等效燃料（RGB推测）",
        5: "建筑/风雨桥等效燃料（人工轮廓，合成参数）",
    }
    landcover_labels = labels.copy()
    labels.update(
        {
            6: "林木/灌丛等效燃料（阴影/邻域推测补全）",
            7: "草地/农田等效燃料（阴影/邻域推测补全）",
            8: "裸土/硬化/水体（推测补全，不燃假设）",
        }
    )
    metadata = dict(
        scenario_id="batuan-manual-inferred-terrain-demo"
        if not args.keep_unknown
        else "batuan-manual-terrain-demo",
        place_name="岜团村",
        assumptions=True,
        simulation_scope="mosaic_extent",
        includes_nonbuilding_fuels=True,
        spatial_assets_dir=str(spatial),
        elevation_physics_activated=True,
        building_labels={str(index): identifier for identifier, index in identifiers.items()},
        building_label_count=len(roofs),
        annotation_report=annotation_report,
        ignition_source=dict(row=row, col=col, cell_id=row * grid.width + col),
        material_survey_status=(
            "manual building geometry; RGB vegetation and synthetic combustion "
            "profiles; no field calibration"
        ),
        input_summary=(
            f"采用人工标注的{annotation_report['building_count']}栋建筑和{annotation_report['bridge_count']}座风雨桥。"
            f"{int(inferred_nonbuilding.sum())}格地表已结合RGB颜色和邻域推测补全，材质名称标明推测来源。"
            f"燃烧参数为合成演示；地形来自约30米FABDEM，计算网格{grid.cell_size_m:g}米。"
            + (
                f"已启用近场火焰接触传热（{grid.cell_size_m:g}米网格演示配置，未实测校准）。"
                if contact
                else "未启用近场接触传热。"
            )
            + (
                "相邻可燃格按连续坡面公共边计算接触，辐射保留DEM高差。"
                if contact and contact.terrain_reference == "shared_face"
                else ""
            )
        ),
        near_field_contact_config=contact.to_mapping() if contact else None,
        radiation_geometry="relative_flame_and_receiver_heights_with_separate_ground_elevation",
        inferred_landcover_cell_count=int(inferred_nonbuilding.sum()),
        inferred_landcover_by_type={
            str(code): int((inferred_nonbuilding & (landcover == code)).sum()) for code in [0, 1, 4]
        },
        landcover_completion=json.loads((classified / "classification_report.json").read_text())[
            "completion"
        ],
        inference_mask_path=str(classified / "arrays/inferred_mask.npy"),
        ground_elevation=dict(
            path=str(dem),
            native_resolution_degrees=list(dem_resolution),
            resampling="bilinear",
            mode="terrain_coupled",
        ),
        transport_parameters={
            name: getattr(TRANSPORT, name) for name in TRANSPORT.__dataclass_fields__
        },
        wind_evidence="synthetic calm-wind scenario; not observed weather",
        unknown_domain_cell_count=int((domain & ~known).sum()),
        sources_sha256={
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [imagery, dem, profile_file, args.annotations.resolve()]
        },
    )
    if contact:
        config = args.flame_contact_config.resolve()
        metadata["sources_sha256"][str(config)] = hashlib.sha256(config.read_bytes()).hexdigest()
    represented = {
        identifier for identifier, rr, cc, area in intersections if building_cells[rr, cc]
    }
    annotation_report["unrepresented_ids"] = sorted(set(identifiers) - represented)
    annotation_report["union_area_in_domain_m2"] = float(
        (coverage_building[domain] * grid.cell_area_m2).sum()
    )
    (output / "buildings/annotation_report.json").write_text(
        json.dumps(annotation_report, ensure_ascii=False, indent=2)
    )
    bundle = write_v3_case_bundle(
        request,
        output / "case",
        material_labels=labels,
        landcover_labels=landcover_labels,
        metadata=metadata,
    )
    print(
        f"计算网格{grid.shape}，建筑格{building_cells.sum()}，可燃格{burnable.sum()}，辐射边{len(edges.source)}；初始起火点{row},{col}",
        flush=True,
    )
    result, _ = run_v3_case_bundle(
        bundle,
        output / "run",
        chunk_frames=4,
        progress_callback=lambda t, end: (
            print(f"初始模拟 {t:.0f}/{end:.0f}s", flush=True) if round(t) % 60 == 0 else None
        ),
    )
    summary = dict(
        grid_shape=list(grid.shape),
        manual_structure_count=len(roofs),
        ignition_row=row,
        ignition_col=col,
        unknown_cells=int((domain & ~known).sum()),
        ignited_cells=int(np.isfinite(result.final_state.ignition_time_s).sum()),
        burned_out_cells=int((result.final_state.state == 4).sum()),
        elevation_min_m=float(elevation.min()),
        elevation_max_m=float(elevation.max()),
        flame_contact_enabled=contact is not None,
        transferred_contact_energy_J=float(result.energy_budget.convective_transferred_J),
        energy_limited_contact_source_steps=result.energy_budget.energy_limited_contact_source_steps,
    )
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
