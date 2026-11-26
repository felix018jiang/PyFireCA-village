"""Freeze paired roof/ground layouts and ignition footprints before reading any wind outcomes."""

import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from shapely.geometry import GeometryCollection, mapping

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import _rng, _uniform_anchor, realize_sources
from pyfireca.planning.roof_ignition import fixed_area_ignition, unambiguous_roof
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_inventory
from pyfireca.village.domain import VillageGridSpec

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "examples/research/heritage/area-20261110"
DESIGN = {
    "pingyan": "pingyan-protected-union-longcap-comparison-20261111.json",
    "batuan": "batuan-narrow-hull-longcap-comparison-20261111.json",
}
RANDOM_SEED = 20261112
NEAR_M = 50.0
FIRE_AREA_M2 = 25.0
POLICIES = (
    ("baseline", 0.0, False),
    ("heritage-narrow", 2.5, False),
    ("heritage-wide", 15.01, False),
    ("multipoint-narrow", 2.5, True),
    ("multipoint-wide", 15.01, True),
)


def feature(geometry, layer, **props):
    return {
        "type": "Feature",
        "geometry": mapping(geometry),
        "properties": {"layer": layer, **props},
    }


def fire_row(fire_id, kind, anchor, footprint, **metadata):
    if abs(footprint.geometry.area - FIRE_AREA_M2) > 1e-7:
        raise ValueError("all fixed and sampled fires must have the registered area")
    return {
        "fire_id": fire_id,
        "kind": kind,
        "anchor_m": anchor,
        **footprint.to_mapping(),
        "footprint_sha256": footprint.sha256,
        **metadata,
    }


def historical_fires(config, inventory, provenance):
    seed_path = Path(config["roof_seed_receipt_path"])
    seed = read_json(seed_path)
    master_path = Path(seed["master_experiment_path"])
    master = read_json(master_path)
    if file_sha256(master_path) != seed["master_experiment_sha256"]:
        raise ValueError("historical fixed-fire geometry master hash changed")
    historical = master["problem"]["fires"]
    provenance.update({str(p.relative_to(ROOT)): file_sha256(p) for p in (seed_path, master_path)})
    fires = []
    for index, ref in enumerate(config["source_fires"], start=1):
        path = Path(ref["protocol_path"])
        provenance[str(path.relative_to(ROOT))] = file_sha256(path)
        rows = realize_sources(read_json(path), inventory, demolished_ids=seed["seed_ids"])
        row = next(item for item in rows if item["draw_id"] == ref["draw_id"])
        footprint = row["footprint"]
        old = historical[ref["fire_id"]]["footprints"][footprint.source_id]
        if footprint.sha256 != content_hash(old):
            raise ValueError("historical ignition polygon or source ID changed")
        fires.append(
            fire_row(
                f"fixed-{index}",
                "fixed",
                row["anchor_m"],
                footprint,
                historical_fire_id=ref["fire_id"],
                historical_draw_id=ref["draw_id"],
            )
        )
    if len({row["source_id"] for row in fires}) != 4:
        raise ValueError("expected four distinct historical design roofs")
    return fires


def ground_action(grid, ring, eligible, landcover):
    if ring.is_empty:
        return [], GeometryCollection(), {}
    x0, y0, x1, y1 = ring.bounds
    size = grid.cell_size_m
    # Expand by one cell to include footprint contact at an exact grid edge.
    row0 = max(0, int(np.floor((grid.y_max_m - y1) / size)) - 1)
    row1 = min(grid.height, int(np.ceil((grid.y_max_m - y0) / size)) + 1)
    col0 = max(0, int(np.floor((x0 - grid.x_min_m) / size)) - 1)
    col1 = min(grid.width, int(np.ceil((x1 - grid.x_min_m) / size)) + 1)
    rows, cols = np.nonzero(eligible[row0:row1, col0:col1])
    rows, cols = rows + row0, cols + col0
    x, y = grid.x_min_m + cols * size, grid.y_max_m - rows * size
    cells = shapely.box(x, y - size, x + size, y)
    keep = shapely.intersects(ring, cells)
    values, counts = np.unique(landcover[rows[keep], cols[keep]], return_counts=True)
    return (
        (rows[keep] * grid.width + cols[keep]).tolist(),
        shapely.union_all(cells[keep]),
        {str(int(v)): int(n) for v, n in zip(values, counts, strict=True)},
    )


def generate_plans(grid, inventory, fixed_ids, eligible, landcover, features):
    buildings = inventory.by_id
    core_ids = sorted(t.building_id for t in inventory.targets)
    protected = set(core_ids) | set(fixed_ids)
    cores = [buildings[i].geometry for i in core_ids]
    core_union = shapely.union_all(cores)
    ordinary = {i: b.geometry for i, b in buildings.items() if i not in core_ids}
    plans = []
    for plan_id, width, use_sources in POLICIES:
        anchors = sorted(protected if use_sources else core_ids) if width else []
        inside = shapely.union_all([buildings[i].geometry for i in anchors])
        band = inside.buffer(width, quad_segs=64).difference(inside)
        removed = sorted(
            i
            for i, geom in ordinary.items()
            if i not in protected and band.intersects(geom) and band.intersection(geom).area > 1e-8
        )
        retained = shapely.union_all([g for i, g in ordinary.items() if i not in removed])
        cleared = shapely.union_all([ordinary[i] for i in removed]).difference(
            shapely.union_all([retained, core_union])
        )
        cells, ground, categories = ground_action(grid, band, eligible, landcover)
        if ground.intersection(shapely.union_all([retained, cleared, core_union])).area > 1e-7:
            raise ValueError("ground actions overlap roof material")
        clear_area = float(cleared.area)
        ground_area = len(cells) * grid.cell_area_m2
        if abs(ground_area - ground.area) > 1e-7:
            raise ValueError("ground footprints must be nonoverlapping complete cells")
        gap = min(geom.distance(retained.difference(core_union)) for geom in cores)
        plans.append(
            dict(
                plan_id=plan_id,
                width_m=width,
                anchor_ids=anchors,
                demolished_ids=removed,
                cleared_physical_cell_ids=cells,
                roof_cleared_m2=clear_area,
                ground_cleared_m2=ground_area,
                construction_m2=clear_area + ground_area,
                minimum_retained_roof_gap_m=float(gap),
                ground_landcover_counts=categories,
            )
        )
        for label, geometry in (
            ("nominal_band", band),
            ("cleared_roof", cleared),
            ("cleared_ground", ground),
        ):
            if not geometry.is_empty:
                features.append(feature(geometry, label, plan_id=plan_id))
    return plans


def random_fires(village, inventory, excluded):
    core = shapely.union_all([inventory.by_id[t.building_id].geometry for t in inventory.targets])
    strata = {"near": [], "background": []}
    for obj in sorted(inventory.buildings, key=lambda b: b.building_id):
        if obj.building_id in excluded or obj.source_label != "building":
            continue
        try:
            roof = unambiguous_roof(inventory, obj.building_id)
        except ValueError as exc:
            if str(exc) != "source has no unambiguous roof area":
                raise
            continue
        if roof.area >= FIRE_AREA_M2:
            stratum = "near" if obj.geometry.distance(core) <= NEAR_M else "background"
            strata[stratum].append(obj.building_id)
    fires = []
    for group, ids in strata.items():
        if len(ids) < 2:
            raise ValueError(f"{village}: stratum {group} has fewer than two eligible roofs")
        chosen = _rng(RANDOM_SEED, village, group, "permutation").permutation(ids)[:2]
        for identifier in chosen.tolist():
            roof = unambiguous_roof(inventory, identifier)
            anchor = _uniform_anchor(roof, _rng(RANDOM_SEED, village, group, identifier, "anchor"))
            footprint = fixed_area_ignition(inventory, identifier, anchor, FIRE_AREA_M2)
            fires.append(
                fire_row(
                    f"random-{len(fires) + 1}",
                    "random",
                    anchor,
                    footprint,
                    stratum=group,
                    stratum_population=len(ids),
                )
            )
    return fires, strata


def prepare_village(name, output):
    history = ARCHIVE / DESIGN[name]
    config = read_json(history)
    study_path = Path(config["study_path"])
    study = read_json(study_path)
    case = (study_path.parent / study["case_dir"]).resolve()
    manifest = read_json(case / "manifest.json")
    grid = VillageGridSpec(**manifest["grid"])
    if grid.crs != "EPSG:32649" or grid.cell_size_m != 5:
        raise ValueError("unexpected native geographic grid")
    structures = (study_path.parent / study["structures_path"]).resolve()
    annotations = (study_path.parent / study["labelme_annotations"]).resolve()
    reference = (study_path.parent / study["image_reference"]).resolve()
    inventory = load_inventory(
        SimpleNamespace(metadata=manifest["metadata"]),
        structures,
        study,
        annotations=annotations,
        reference=reference,
    )
    files = [history, study_path, case / "manifest.json", structures, annotations, reference]
    fields = (
        "domain_mask",
        "known_mask",
        "burnable_mask",
        "building_index",
        "covered_area_m2",
        "landcover_index",
    )
    files.extend(case / f"{field}.npy" for field in fields)
    provenance = {str(f.relative_to(ROOT)): file_sha256(f) for f in files}
    arrays = {field: np.load(case / f"{field}.npy", mmap_mode="r") for field in fields}
    eligible = (
        arrays["domain_mask"]
        & arrays["known_mask"]
        & arrays["burnable_mask"]
        & (arrays["building_index"] < 0)
        & (arrays["covered_area_m2"] > 0)
    )
    fixed = historical_fires(config, inventory, provenance)
    sources = [f["source_id"] for f in fixed]
    core_ids = sorted(t.building_id for t in inventory.targets)
    features = [
        feature(
            b.geometry,
            "heritage" if b.building_id in core_ids else "building",
            building_id=b.building_id,
        )
        for b in inventory.buildings
    ]
    plans = generate_plans(grid, inventory, sources, eligible, arrays["landcover_index"], features)
    excluded = set(core_ids + sources).union(*(p["demolished_ids"] for p in plans))
    sampled, strata = random_fires(name, inventory, excluded)
    fires = fixed + sampled
    features.extend(
        {
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "layer": f["kind"] + "_ignition",
                "fire_id": f["fire_id"],
                "source_id": f["source_id"],
            },
        }
        for f in fires
    )
    geojson = output / f"{name}-layouts.geojson"
    write_json(
        geojson,
        {
            "type": "FeatureCollection",
            "name": f"{name}_firebreak_layouts",
            "crs": {"type": "name", "properties": {"name": grid.crs}},
            "features": features,
        },
    )
    return dict(
        study_path=str(study_path),
        crs=grid.crs,
        core_ids=core_ids,
        fixed_source_ids=sources,
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
        plans=plans,
        fires=fires,
        random_eligible_ids_by_stratum=strata,
        inventory_sha256=inventory.sha256,
        grid=manifest["grid"],
        landcover_labels=manifest["landcover_labels"],
        geojson_path=geojson.name,
        geojson_sha256=file_sha256(geojson),
        input_files_sha256=provenance,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/heritage-wind-20261112")
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    winds = [
        dict(wind_id="calm", wind_speed_m_s=0.0, wind_from_deg=0.0, flame_tilt_coefficient_s_m=0.2)
    ]
    for speed in (2, 4):
        for direction, angle in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            winds.append(
                dict(
                    wind_id=f"{direction}{speed}",
                    wind_speed_m_s=speed,
                    wind_from_deg=angle,
                    flame_tilt_coefficient_s_m=0.2,
                )
            )
    data = {
        "schema_version": "heritage-paired-wind-1",
        "winds": winds,
        "wind_scope": "uniform static synthetic wind from grid north; tilt-mediated radiation",
        "primary": {
            "radius_m": 15,
            "horizon_s": 3600,
            "roof_scale_m": 1,
            "quadrature_spacing_m": 0.5,
            "wind_ids": [w["wind_id"] for w in winds],
            "planned_pairs": 720,
        },
        "sensitivity": {
            "radius_m": 25,
            "horizon_s": 3600,
            "fire_ids": ["fixed-1", "random-1"],
            "wind_ids": ["calm", "W4"],
            "plan_ids": [p[0] for p in POLICIES],
            "planned_pairs": 40,
        },
        "geometry_rule": "buffer(union(anchor roofs), width) minus union(anchor roofs); "
        "quad_segs=64",
        "construction_rule": "No seed removal; all core and four historical source roofs "
        "protected. Select other ordinary owners with band overlap above 1e-8 m²; "
        "retained owners keep shared roof. "
        "Select complete eligible nonroof burnable 5 m cells intersecting band including contact. "
        "Costs are physical cleared roof union and selected whole-ground footprints.",
        "random_design": {
            "seed": RANDOM_SEED,
            "draws_per_stratum": 2,
            "near_distance_m": NEAR_M,
            "source_area_m2": FIRE_AREA_M2,
            "sampling": "PCG64 keyed by SHA-256; uniform buildings "
            "without replacement within two strata on common retained ordinary roofs; "
            "uniform point inside unambiguous roof; disk clipped to roof at registered area",
            "strata": "near means polygon distance to any heritage roof <= 50m; background > 50m",
        },
        "villages": {name: prepare_village(name, output) for name in DESIGN},
    }
    data["registration_sha256"] = content_hash(data)
    write_json(output / "registration.json", data)
    print(data["registration_sha256"])


if __name__ == "__main__":
    main()
