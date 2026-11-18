"""Measure plan-view separations from heritage roof outlines to retained ordinary roof material."""

import argparse
import json
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import GeometryCollection, Polygon, shape
from shapely.ops import transform

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "output/heritage-firebreak-actions-20261111"
INDEX = MAPS / "verified-options.json"
STUDIES = {
    "pingyan": "pingyan-study-20261029",
    "batuan": "batuan-provisional-all-ordinary-study",
}
OPTIONS = {
    "pingyan": (
        "roof-seed-protected-union-ring-b0-w2.5",
        "local-band-with-observed-clearance-subset",
        "fixed-core-and-first-escape-compartment",
        "roof-seed-protected-union-ring-b0-w15.01",
        "pingyan-zero-heat-core-only",
        "pingyan-zero-heat-four-tie-1",
        "pingyan-zero-heat-four-tie-2",
        "pingyan-zero-heat-four-tie-3",
        "pingyan-zero-heat-eight-known",
    ),
    "batuan": (
        "direct-thin-D9-with-no-grass",
        "batuan-joint-core-and-loss",
        "batuan-joint-zero-heat-and-loss",
    ),
}


def roof_action(option):
    binding = option["maps"][0]
    path = (MAPS / binding["path"]).resolve()
    if file_sha256(path) != binding["file_sha256"]:
        raise ValueError("Action map binding changed")
    filters = binding["feature_filter_allowed_values"]
    roof = []
    ids = set()
    to_metric = Transformer.from_crs(binding["coordinate_crs"], "EPSG:32649", always_xy=True)
    for feature in read_json(path)["features"]:
        p = feature["properties"]
        if not all(p.get(k) in valid for k, valid in filters.items()):
            continue
        if p.get("material", p.get("cleared_land_type")) not in {
            "roof_union",
            "cleared_roof_union",
        }:
            continue
        ids.update(p.get("action_ids", p.get("action_identifiers", [])))
        geom = transform(to_metric.transform, shape(feature["geometry"]))
        roof.append(shapely.make_valid(geom, method="structure", keep_collapsed=False))
    if not roof or not ids:
        raise ValueError(f"Roof action map lacks named material {path.name}")
    return ids, shapely.union_all(roof)


def village(name, options):
    study_path = ROOT / "examples/research/heritage" / f"{STUDIES[name]}.json"
    study = read_json(study_path)
    structures = (study_path.parent / study["structures_path"]).resolve()
    buildings = read_json(structures)
    if buildings["coordinate_space"] != "world_m":
        raise ValueError("Expected original metric coordinate space")
    shapes = {
        b["building_id"]: shapely.union_all(
            [Polygon(p["exterior"], p["holes"]) for p in b["polygons"]]
        )
        for b in buildings["buildings"]
    }
    protected = set(study["protected_ids"])
    if protected != {t["building_id"] for t in study["targets"]}:
        raise ValueError("Expected core and protected list to coincide")
    core = np.asarray([shapes[i] for i in sorted(protected)], dtype=object)
    core_union = shapely.union_all(core)
    ordinary_ids = sorted(shapes.keys() - protected)
    ordinary = np.asarray([shapes[i] for i in ordinary_ids], dtype=object)
    full_cover = shapely.union_all([core_union, *ordinary])
    distances = shapely.distance(ordinary[:, None], ordinary[None, :])
    np.fill_diagonal(distances, np.inf)
    rows = []
    for key in [None, *OPTIONS[name]]:
        selected, mapped = roof_action(options[key]) if key else (set(), GeometryCollection())
        if not selected.issubset(ordinary_ids):
            raise ValueError("Roof action includes a nonordinary ID")
        keep = np.asarray([i not in selected for i in ordinary_ids])
        surviving = shapely.union_all(ordinary[keep])
        true_cleared = full_cover.difference(shapely.union_all([core_union, surviving]))
        delta = true_cleared.symmetric_difference(mapped).area
        if delta > 0.0001:
            raise ValueError(f"Clearing physical union mismatch {key}: {delta}")
        # Any patch co-owned by a protected roof is part of its core, not exterior fuel.
        external_roof = surviving.difference(core_union)
        edge = shapely.distance(core, external_roof)
        by_roof = np.min(distances[np.ix_(keep, keep)], axis=1)
        rows.append(
            dict(
                option=key or "baseline",
                construction_m2=options[key]["C_m2"] if key else 0,
                ordinary_annotation_actions=len(selected),
                retained_ordinary_labels_not_selected=int(keep.sum()),
                physical_clearing_map_symmetric_difference_m2=delta,
                protected_to_external_ordinary_roof_net_gap_sorted_m=sorted(map(float, edge)),
                minimum_protected_to_external_ordinary_roof_net_gap_m=float(edge.min()),
                minimum_other_ordinary_neighbour_distance_m=float(by_roof.min()),
                median_other_ordinary_neighbour_distance_m=float(np.median(by_roof)),
                ordinary_annotations_touching_or_overlapping_another=int((by_roof == 0).sum()),
            )
        )
    return dict(
        village=name,
        protected_roofs=len(core),
        original_ordinary_roof_annotations=len(ordinary),
        structures_file=str(structures.relative_to(ROOT)),
        structures_sha256=file_sha256(structures),
        study_sha256=file_sha256(study_path),
        measurements=rows,
    )


def audit():
    index = read_json(INDEX)
    if index["index_content_sha256"] != content_hash(
        {k: v for k, v in index.items() if k != "index_content_sha256"}
    ):
        raise ValueError("Expected verified decision index digest")
    output = dict(
        definitions=(
            "EPSG:32649 horizontal minimum polygon-to-polygon distance in metres. "
            "For each core roof take the closest still-present ordinary roof material outside "
            "the union of protected roof polygons; list sorted values without target identifiers. "
            "Ordinary-to-ordinary statistics are for original ordinary labels not selected for "
            "removal, and their nearest *distinct* other retained ordinary label; shared or "
            "touching footprints count as zero. These are mapped roofs, not walls, facade "
            "setbacks, distances to grass, or lengths along terrain."
        ),
        geometry_check=(
            "Compare original roof union minus physical retained coverage to each certified "
            "GIS clearance layer; overlapping roof material remains where any owner is retained."
        ),
        model_scope="Descriptive geometry only; no transfer threshold, field survey or new fire.",
        construction_index_sha256=file_sha256(INDEX),
        method_sha256=file_sha256(Path(__file__)),
        villages=[village(name, index["options"]) for name in STUDIES],
    )
    output["content_sha256"] = content_hash(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    dest = parser.parse_args().output
    dest.write_text(json.dumps(audit(), indent=2, allow_nan=False) + "\n")
