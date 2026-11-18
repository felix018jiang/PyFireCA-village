"""Identify annotation overlaps and objects lost by single-owner rasterization."""

# ruff: noqa: E501, RUF001

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import BASE, ROOT, dump, sha
from run_batuan_local_mesh import geometry_grid
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from pyfireca.village.workflow_v3 import load_v3_case_bundle


def inventory_audit(structures, owner_ids):
    ids = [b["building_id"] for b in structures]
    polygons = [
        unary_union([Polygon(v["exterior"], v["holes"]) for v in b["polygons"]]) for b in structures
    ]
    tree = STRtree(polygons)
    overlaps = []
    for i, p in enumerate(polygons):
        for j in sorted(map(int, tree.query(p))):
            if j <= i:
                continue
            area = p.intersection(polygons[j]).area
            if area <= 1e-6:
                continue
            overlaps.append(
                {
                    "left": ids[i],
                    "right": ids[j],
                    "overlap_m2": area,
                    "left_fraction": area / p.area,
                    "right_fraction": area / polygons[j].area,
                }
            )
    absent = []
    for key, p in zip(ids, polygons, strict=True):
        if key in owner_ids:
            continue
        parents = []
        for j in sorted(map(int, tree.query(p))):
            if ids[j] == key:
                continue
            area = p.intersection(polygons[j]).area
            if area > 1e-6:
                parents.append(
                    {
                        "label": ids[j],
                        "overlap_m2": area,
                        "fraction_of_absent": area / p.area,
                        "parent_is_grid_owner": ids[j] in owner_ids,
                    }
                )
        absent.append(
            {
                "label": key,
                "vector_area_m2": p.area,
                "overlapping_objects": sorted(parents, key=lambda x: -x["overlap_m2"]),
                "classification": (
                    "nested_annotation_requires_semantic_review"
                    if max((r["fraction_of_absent"] for r in parents), default=0) >= 0.99
                    else "partial_overlap_and_raster_identity_loss"
                    if parents
                    else "isolated_unrepresented_geometry"
                ),
            }
        )
    return {
        "annotated_objects": len(ids),
        "independent_grid_objects": len(owner_ids),
        "unrepresented_objects": absent,
        "positive_area_overlaps": overlaps,
        "sum_annotation_area_m2": sum(p.area for p in polygons),
        "union_annotation_area_m2": unary_union(polygons).area,
        "allow_complete_independent_object_claim": not absent,
        "automatic_geometry_or_fuel_change": False,
    }


def audit(output):
    output.mkdir(parents=True, exist_ok=False)
    b = load_v3_case_bundle(BASE)
    annotation = Path(b.metadata["spatial_assets_dir"]) / "buildings.json"
    structures = json.loads(annotation.read_text())["buildings"]
    labels = b.metadata["building_labels"]
    represented = set(map(int, np.unique(b.request.building_index))) - {-1}
    report = inventory_audit(structures, {labels[str(i)] for i in represented})
    polygons = {
        r["building_id"]: unary_union([Polygon(v["exterior"], v["holes"]) for v in r["polygons"]])
        for r in structures
    }
    # This checks geometric coverage by any modeled building, separately from identity.
    roof = unary_union(geometry_grid(b.request.grid)[b.request.building_index >= 0].ravel())
    for row in report["unrepresented_objects"]:
        p = polygons[row["label"]]
        row["area_under_any_building_cell_m2"] = p.intersection(roof).area
        row["fraction_under_any_building_cell"] = p.intersection(roof).area / p.area
    report.update(
        annotation_sha256=sha(annotation),
        building_index_sha256=sha(BASE / "building_index.npy"),
        source_sha256=sha(Path(__file__)),
        note="Nested labels do not justify duplicate fuel; do not silently delete annotations.",
    )
    snapshot = output / "audit-script.py"
    shutil.copy2(Path(__file__), snapshot)
    shutil.copy2(annotation, output / "buildings.source.json")
    dump(output / "report.json", report)
    dump(ROOT / "docs/results/batuan-annotation-inventory-20261017.json", report)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 530">',
        '<rect width="1100" height="530" fill="#f6f4ee"/>',
    ]
    for i, row in enumerate(report["unrepresented_objects"]):
        child = polygons[row["label"]]
        parent = polygons[row["overlapping_objects"][0]["label"]]
        xmin, ymin, xmax, ymax = parent.union(child).bounds
        scale = min(450 / (xmax - xmin), 380 / (ymax - ymin))
        xoff = 30 + i * 550
        yoff = 90
        parts.append(
            f'<text x="{xoff}" y="32" font-family="sans-serif" font-size="21">{row["label"]} inside {row["overlapping_objects"][0]["label"]}</text>'
        )
        for polygon, color in [(parent, "#5a8eb4"), (child, "#d99255")]:
            for p in getattr(polygon, "geoms", [polygon]):
                points = " ".join(
                    f"{xoff + (x - xmin) * scale:.2f},{yoff + (ymax - y) * scale:.2f}"
                    for x, y in p.exterior.coords
                )
                parts.append(
                    f'<polygon points="{points}" fill="{color}" fill-opacity=".65" stroke="#253642" stroke-width="1.5"/>'
                )
        fraction = row["overlapping_objects"][0]["fraction_of_absent"] * 100
        parts.append(
            f'<text x="{xoff}" y="500" font-family="sans-serif" font-size="16">Overlap of nested label: {fraction:.4f}%</text>'
        )
    parts.append("</svg>")
    (output / "overlap-review.svg").write_text("".join(parts))
    lines = [
        "# 岜团村建筑对象覆盖审计（2026-10-17）",
        "",
        f"人工标注{report['annotated_objects']}个对象；当前单归属建筑网格有{report['independent_grid_objects']}个独立编号。",
        "这不是简单漏画两栋建筑：两处内部标注几乎完全与外层标注重合，其位置仍在已有建筑元胞内。",
        "",
        "| 内层标签 | 最重叠外层 | 内层面积㎡ | 与外层重叠比例 | 被任意建筑格覆盖 |",
        "|---|---|---:|---:|---:|",
    ]
    for row in report["unrepresented_objects"]:
        p = row["overlapping_objects"][0]
        lines.append(
            f"| {row['label']} | {p['label']} | {row['vector_area_m2']:.2f} | {p['fraction_of_absent']:.6%} | {row['fraction_under_any_building_cell']:.6%} |"
        )
    lines.extend(
        [
            "",
            "模型燃料按空间并集处理；不自动复制、删除或重写原标注。",
            "下一步需要区分：外层是否屋面组团/重复标注，或内层是否独立建筑。先保留原始标签与父子关系，",
            "开发独立于燃料单归属网格的逐对象观测掩膜，允许多个标注观测同一燃料格而不重复增加燃料。",
            "原整村结果的逐对象计数范围为331个网格对象；不能据此称已验证333栋独立建筑。",
            "零建筑元胞引燃的方案覆盖了这两处位置的现有建筑格，但尚未完成其独立对象语义与亚格热暴露验证。",
            "",
        ]
    )
    (ROOT / "docs/BATUAN_ANNOTATION_INVENTORY_20261017.md").write_text("\n".join(lines))
    print(
        json.dumps(
            {
                "annotated": report["annotated_objects"],
                "grid": report["independent_grid_objects"],
                "nested": [r["label"] for r in report["unrepresented_objects"]],
                "overlap_pairs": len(report["positive_area_overlaps"]),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args().output.resolve())
