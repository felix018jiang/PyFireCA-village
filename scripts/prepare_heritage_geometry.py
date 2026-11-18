"""Refresh an annotated case and audit core mesh independence; no fire-source selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import box
from shapely.ops import nearest_points

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import audit_inventory, bind_labelme_targets, inventory_from_records
from pyfireca.planning.spatial import audit_target_mesh, refresh_building_geometry
from pyfireca.village.annotations import load_labelme_buildings
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def registered_rgb_ground(bundle):
    directory = Path(bundle.metadata["inference_mask_path"]).parent
    grid_file = directory.parent / "grid.json"
    if json.loads(grid_file.read_text()) != asdict(bundle.request.grid):
        raise ValueError("RGB classification grid is not registered to the parent")
    raw_file, inferred_file = directory / "landcover_index.npy", directory / "inferred_mask.npy"
    raw, inferred = (
        np.load(raw_file, allow_pickle=False),
        np.load(inferred_file, allow_pickle=False),
    )
    landcover = np.full(raw.shape, -1, np.int32)
    landcover[raw == 1], landcover[raw == 2] = 4, 1
    landcover[np.isin(raw, [3, 4, 5])] = 0
    material = landcover.copy()
    for cover, code in ((4, 6), (1, 7), (0, 8)):
        material[inferred & (landcover == cover)] = code
    return material, landcover, {str(p): sha(p) for p in (grid_file, raw_file, inferred_file)}


def local_grid(request_grid, geometry, spacing, *, margin=20.0, phase=(0.0, 0.0)):
    if (
        not math.isfinite(spacing)
        or spacing <= 0
        or not math.isclose(5 / spacing, round(5 / spacing))
    ):
        raise ValueError("spacing must be a positive divisor of the common 5 m support")
    xmin, ymin, xmax, ymax = geometry.bounds
    left, bottom = math.floor((xmin - margin) / 5) * 5, math.floor((ymin - margin) / 5) * 5
    right, top = math.ceil((xmax + margin) / 5) * 5, math.ceil((ymax + margin) / 5) * 5
    return VillageGridSpec(
        round((top - bottom) / spacing),
        round((right - left) / spacing),
        spacing,
        left + phase[0] * spacing,
        top + phase[1] * spacing,
        request_grid.coordinate_mode,
        request_grid.crs,
    )


def render_mesh_comparison(output, inventory, meshes):
    """A diagnostic map of fixed outlines and grids; not a fire-risk map."""
    width, panel, height = 2070, 470, 225 + len(inventory.targets) * 535
    canvas = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font_file = Path("/System/Library/Fonts/Supplemental/Arial.ttf")

    def font(size):
        return (
            ImageFont.truetype(str(font_file), size)
            if font_file.exists()
            else ImageFont.load_default()
        )

    draw.text((30, 18), "Mesh independence near heritage buildings", fill="#182f41", font=font(28))
    draw.text(
        (30, 58),
        "Fixed vector geometry. Counts refer to the entire target, not only this local view.",
        fill="#526370",
        font=font(18),
    )
    draw.text(
        (30, 91),
        "Blue: heritage outline   Grey: neighbours   Pink: shared temperature cell",
        fill="#526370",
        font=font(18),
    )
    by_key = {(r["target_id"], r["grid"]["cell_size_m"]): r for r in meshes}
    for row, target in enumerate(inventory.targets):
        geometry = inventory.by_id[target.building_id].geometry
        nearest = min(
            (b for b in inventory.buildings if b.building_id != target.building_id),
            key=lambda b: geometry.distance(b.geometry),
        )
        a, b = nearest_points(geometry, nearest.geometry)
        cx, cy = (a.x + b.x) / 2, (a.y + b.y) / 2
        span = max(12.0, 2 * geometry.distance(nearest.geometry) + 8)
        xmin, ymin = cx - span / 2, cy - span / 2
        xmax, ymax = cx + span / 2, cy + span / 2
        window = box(xmin, ymin, xmax, ymax)
        for col, spacing in enumerate((5.0, 1.0, 0.5, 0.25)):
            key = (target.building_id, spacing)
            if key not in by_key:
                continue
            audit = by_key[key]
            left, top = 28 + col * 515, 185 + row * 535

            def xy(x, y, left=left, top=top, xmin=xmin, ymax=ymax, span=span):
                return left + (x - xmin) / span * panel, top + (ymax - y) / span * panel

            draw.text(
                (left, top - 54),
                f"{target.building_id} / {spacing:g} m cells",
                fill="#182f41",
                font=font(22),
            )
            draw.text(
                (left, top - 27),
                f"Shared: {audit['shared_temperature_cells']} | Gap faces: "
                f"{audit['positive_gap_cell_face_count']}",
                fill="#526370",
                font=font(17),
            )
            draw.rectangle((left, top, left + panel, top + panel), fill="#fafbfc")
            clipped = []
            for building in inventory.buildings:
                if not building.geometry.intersects(window):
                    continue
                shape = building.geometry.intersection(window)
                pieces = [shape] if shape.geom_type == "Polygon" else getattr(shape, "geoms", ())
                for polygon in pieces:
                    if polygon.geom_type != "Polygon":
                        continue
                    points = [xy(x, y) for x, y in polygon.exterior.coords]
                    is_target = building.building_id == target.building_id
                    draw.polygon(points, fill="#dfedf6" if is_target else "#e6e8ea")
                    clipped.append((points, is_target))
            grid = VillageGridSpec(**audit["grid"])
            for i in range(
                math.ceil((xmin - grid.x_min_m) / spacing),
                math.floor((xmax - grid.x_min_m) / spacing) + 1,
            ):
                x, _ = xy(grid.x_min_m + i * spacing, ymin)
                draw.line((x, top, x, top + panel), fill=(110, 135, 155, 80), width=1)
            for i in range(
                math.ceil((grid.y_max_m - ymax) / spacing),
                math.floor((grid.y_max_m - ymin) / spacing) + 1,
            ):
                _, y = xy(xmin, grid.y_max_m - i * spacing)
                draw.line((left, y, left + panel, y), fill=(110, 135, 155, 80), width=1)
            with np.load(output / "meshes" / f"{target.building_id}-{spacing:g}m.npz") as arrays:
                shared = (arrays["object_count"] > 1) & (arrays["target_intersection_area_m2"] > 0)
                for r, c in np.argwhere(shared):
                    x0, y1 = grid.x_min_m + c * spacing, grid.y_max_m - r * spacing
                    if x0 >= xmax or x0 + spacing <= xmin or y1 <= ymin or y1 - spacing >= ymax:
                        continue
                    x0, y0 = xy(max(xmin, x0), min(ymax, y1))
                    x1, y1 = xy(
                        min(xmax, grid.x_min_m + (c + 1) * spacing),
                        max(ymin, grid.y_max_m - (r + 1) * spacing),
                    )
                    draw.rectangle((x0, y0, x1, y1), fill=(226, 68, 106, 85))
            for points, is_target in clipped:
                draw.line(
                    points, fill="#2477a6" if is_target else "#727e87", width=3 if is_target else 2
                )
            draw.rectangle((left, top, left + panel, top + panel), outline="#aebbc4", width=1)
    draw.text(
        (30, height - 30),
        "Geometry diagnostics only. No fire simulation or safe-distance inference.",
        fill="#526370",
        font=font(18),
    )
    canvas.save(output / "mesh-comparison.png")


def prepare(config_path, output, *, spacings=(5.0, 2.5, 1.0, 0.5, 0.25)):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "heritage-p1-study-1":
        raise ValueError("unsupported study config")
    if tuple(sorted(set(spacings), reverse=True)) != tuple(spacings):
        raise ValueError("spacings must be unique and ordered coarse to fine")
    parent_path = (config_path.parent / config["case_dir"]).resolve()
    parent = load_v3_case_bundle(parent_path)
    original_fingerprint = request_fingerprint(parent.request)
    original_dir = Path(parent.metadata["spatial_assets_dir"])
    old_file = original_dir / "buildings.json"
    original = json.loads(old_file.read_text())["buildings"]
    annotation_file = (config_path.parent / config["labelme_annotations"]).resolve()
    reference_file = (config_path.parent / config["image_reference"]).resolve()
    annotation_sha = sha(annotation_file)
    reference = json.loads(reference_file.read_text())
    current = load_labelme_buildings(
        annotation_file, reference, labels=("building", "gulou", "fengyuqiao"), repair_invalid=True
    )
    targets, reconciliation = bind_labelme_targets(
        original, current, identity_evidence=f"updated_labelme_sha256:{annotation_sha}"
    )
    by_source = {row["properties"]["source_shape_index"]: row["building_id"] for row in original}
    normalized = [
        {
            **row,
            "building_id": by_source[row["properties"]["source_shape_index"]],
            "properties": {
                **row["properties"],
                "annotation_source_label": row["properties"]["source_label"],
                "source_label": "building"
                if row["properties"]["source_label"] == "gulou"
                else row["properties"]["source_label"],
            },
        }
        for row in current
    ]
    inventory = inventory_from_records(
        normalized,
        parent.metadata["building_labels"],
        targets=targets,
        protected_ids=tuple(config.get("protected_ids", [])),
        fixed_source_ids=tuple(config.get("fixed_source_ids", [])),
        candidate_ids=tuple(config.get("candidate_ids", [])),
        candidate_evidence=config.get(
            "candidate_evidence", "pending_survey_no_candidates_declared"
        ),
    )
    ground_material, ground_landcover, ground_sources = registered_rgb_ground(parent)
    transport = V3RadiativeTransportParameters(**parent.metadata["transport_parameters"])
    request, refresh = refresh_building_geometry(
        parent,
        inventory,
        transport,
        ground_material=ground_material,
        ground_landcover=ground_landcover,
    )
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    frozen_annotation = inputs / "annotations.labelme.json"
    shutil.copy2(annotation_file, frozen_annotation)
    shutil.copy2(old_file, inputs / "parent-buildings.json")
    shutil.copy2(config_path, inputs / "parent-study.json")
    ground_context = output / "ground-context"
    (ground_context / "arrays").mkdir(parents=True)
    for source in ground_sources:
        source = Path(source)
        target_path = (
            ground_context / "grid.json"
            if source.name == "grid.json"
            else ground_context / "arrays" / source.name
        )
        shutil.copy2(source, target_path)
    spatial = output / "spatial"
    spatial.mkdir()
    dump(
        spatial / "buildings.json",
        {
            "schema_version": "prepared-buildings-1",
            "coordinate_space": "world_m",
            "buildings": normalized,
        },
    )
    dump(spatial / "image_ref.json", reference)
    dump(output / "inventory.json", inventory.to_mapping())
    dump(output / "annotation-reconciliation.json", reconciliation)
    dump(output / "geometry-refresh.json", refresh)
    coarse_audit = audit_inventory(request, inventory)
    dump(output / "coarse-inventory-audit.json", coarse_audit)
    metadata = {
        **parent.metadata,
        "parent_case": str(parent_path),
        "spatial_assets_dir": str(spatial),
        "heritage_targets": [asdict(t) for t in targets],
        "geometry_refresh": refresh,
        "latest_annotation_sha256": annotation_sha,
        "inference_mask_path": str(ground_context / "arrays/inferred_mask.npy"),
        "preparation_scope": "no_thermal_simulation_no_frozen_research_source",
        "input_summary": "Updated outlines retained; heritage roles separate from timber fuel.",
        "sources_sha256": {
            **parent.metadata.get("sources_sha256", {}),
            str(annotation_file): annotation_sha,
            **ground_sources,
        },
    }
    write_v3_case_bundle(
        request,
        output / "case",
        material_labels=parent.material_labels,
        landcover_labels=parent.landcover_labels,
        metadata=metadata,
    )
    loaded = load_v3_case_bundle(output / "case")
    if request_fingerprint(loaded.request) != refresh["refreshed_request_sha256"]:
        raise AssertionError("refreshed case did not round-trip")
    _targets, updated_binding = bind_labelme_targets(
        normalized, current, identity_evidence=f"updated_labelme_sha256:{annotation_sha}"
    )
    if not updated_binding["physical_case_matches_updated_annotations"]:
        raise AssertionError("refreshed geometry still differs from updated annotation")
    resolved = {
        **config,
        "case_dir": str(output / "case"),
        "structures_path": str(spatial / "buildings.json"),
        "image_reference": str(spatial / "image_ref.json"),
        "labelme_annotations": str(frozen_annotation),
        "targets": [asdict(t) for t in targets],
    }
    dump(output / "study.json", resolved)
    mesh_dir = output / "meshes"
    mesh_dir.mkdir()
    meshes, phase_checks, recommendations = [], [], {}
    for target in targets:
        geometry = inventory.by_id[target.building_id].geometry
        base_reports = []
        for spacing in spacings:
            grid = local_grid(request.grid, geometry, spacing)
            audit, arrays = audit_target_mesh(inventory, grid, target.building_id)
            audit["phase_cell_fractions"] = [0.0, 0.0]
            tag = f"{target.building_id}-{spacing:g}m"
            np.savez_compressed(mesh_dir / f"{tag}.npz", **arrays)
            dump(mesh_dir / f"{tag}.json", audit)
            meshes.append(audit)
            base_reports.append(audit)
            print(
                f"{tag}: shared={audit['shared_temperature_cells']}, "
                f"gap_faces={audit['positive_gap_cell_face_count']}",
                flush=True,
            )
        recommendations[target.building_id] = None
        for audit in base_reports:
            if not audit["independent_target_geometry"]:
                continue
            spacing = audit["grid"]["cell_size_m"]
            checks = []
            for index, phase in enumerate(((0.5, 0.0), (0.0, 0.5), (0.5, 0.5))):
                grid = local_grid(request.grid, geometry, spacing, phase=phase)
                checked, arrays = audit_target_mesh(inventory, grid, target.building_id)
                checked["phase_cell_fractions"] = list(phase)
                tag = f"{target.building_id}-{spacing:g}m-phase-{index}"
                dump(mesh_dir / f"{tag}.json", checked)
                np.savez_compressed(mesh_dir / f"{tag}.npz", **arrays)
                checks.append(checked)
                phase_checks.append(checked)
            if all(item["independent_target_geometry"] for item in checks):
                recommendations[target.building_id] = spacing
                break
    if (
        sha(annotation_file) != annotation_sha
        or request_fingerprint(parent.request) != original_fingerprint
    ):
        raise AssertionError("input annotation or parent changed during preparation")
    protocol = {
        "schema_version": "heritage-geometry-preparation-1",
        "config": config,
        "parent_case": str(parent_path),
        "parent_case_sha256": original_fingerprint,
        "labelme_annotations": str(annotation_file),
        "labelme_sha256": annotation_sha,
        "image_reference_sha256": sha(reference_file),
        "ground_sources_sha256": ground_sources,
        "inventory_sha256": inventory.sha256,
        "sources_sha256": {
            str(p.relative_to(ROOT)): sha(p)
            for p in [Path(__file__), *sorted((ROOT / "src/pyfireca").rglob("*.py"))]
        },
        "spacings_m": spacings,
        "phase_checks": "three half-cell origin shifts at selected spacing",
        "thermal_simulations_performed": 0,
    }
    for relative in protocol["sources_sha256"]:
        destination = output / "source-snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    dump(output / "protocol.json", protocol)
    summary = {
        "schema_version": "heritage-geometry-summary-1",
        "geometry_refresh": refresh,
        "updated_annotation_matches_refreshed_case": True,
        "meshes": meshes,
        "phase_checks": phase_checks,
        "recommended_geometry_spacing_m": recommendations,
        "uniform_fine_thermal_case_constructed": False,
        "thermal_simulations_performed": 0,
        "scope": "geometry_independence_not_ignition_time_convergence_or_heritage_safety",
    }
    render_mesh_comparison(output, inventory, meshes)
    dump(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--spacings-m", nargs="+", type=float, default=[5, 2.5, 1, 0.5, 0.25])
    args = parser.parse_args()
    result = prepare(args.config, args.output, spacings=tuple(args.spacings_m))
    print(
        json.dumps(
            {
                "recommended_geometry_spacing_m": result["recommended_geometry_spacing_m"],
                "summary": str(args.output / "summary.json"),
            },
            ensure_ascii=False,
        )
    )
