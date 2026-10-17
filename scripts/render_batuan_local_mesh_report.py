"""Audit local mesh invariants and export inspectable numerical comparisons."""

# ruff: noqa: RUF001, E501

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

import numpy as np
import shapely
from run_batuan_building_pairs import ROOT, dump, sha
from run_batuan_local_mesh import PAIRS, clear_local_ground, geometry_grid
from shapely.geometry import Polygon
from shapely.ops import nearest_points, unary_union

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.village.workflow_v3 import load_v3_case_bundle


def check(output):
    p = json.loads((output / "protocol.json").read_text())
    report = json.loads((output / "report.json").read_text())
    mesh_inputs = json.loads((output / "mesh-inputs.json").read_text())
    for name, digest in {**p["source_hashes"], **p["input_hashes"]}.items():
        if sha(ROOT / name) != digest:
            raise AssertionError(f"changed source/input: {name}")
    for name, digest in mesh_inputs.items():
        if sha(output / name) != digest:
            raise AssertionError(f"changed mesh: {name}")
    records = report["records"]
    if len(records) != 98 or len({r["key"] for r in records}) != 98:
        raise AssertionError("expected 98 unique prespecified runs")
    if sha(output / "protocol.json") != report["protocol_sha256"]:
        raise AssertionError("report/protocol mismatch")
    for r in records:
        if r != json.loads((output / "records" / f"{r['key']}.json").read_text()):
            raise AssertionError("report/cache differs")
        if r["protocol_sha256"] != report["protocol_sha256"]:
            raise AssertionError("record/protocol mismatch")
        if abs(r["actual_horizon_s"] - 5400) > 1e-8 or r["guard_ignited"]:
            raise AssertionError("incomplete/boundary affected run")
        v = r["validation"]
        if max(v["energy_closure_errors_J"].values()) > v["energy_tolerance_J"]:
            raise AssertionError("energy closure failed")
    structures = json.loads((output / "buildings.source.json").read_text())["buildings"]
    all_polygons = unary_union(
        [Polygon(v["exterior"], v["holes"]) for b in structures for v in b["polygons"]]
    )
    audits = []
    for folder in sorted((output / "cases").iterdir()):
        bundle = load_v3_case_bundle(folder)
        q = bundle.request
        a = bundle.metadata["mesh_audit"]
        if request_fingerprint(q) != a["request_sha256"]:
            raise AssertionError("prepared request fingerprint differs")
        # Fine outlines must conserve both buildings and separate their faces.
        errors = [abs(x["vector_area_m2"] - x["mesh_area_m2"]) for x in a["building_area"]]
        if a["spacing_m"] <= 2.5 and (max(errors) > 1e-7 or a["mixed_pair_cells"]):
            raise AssertionError("fine mesh lost a building area")
        if a["spacing_m"] <= 1 and a["direct_building_face_pairs"]:
            raise AssertionError("fine mesh retains spurious direct building contact")
        cleared = clear_local_ground(q)
        building = q.building_index >= 0
        for name in q.parameters.__dataclass_fields__:
            np.testing.assert_array_equal(
                getattr(q.parameters, name)[building], getattr(cleared.parameters, name)[building]
            )
        ground = q.burnable_mask & ~building
        footprints = geometry_grid(q.grid)[ground]
        overlap = float(shapely.area(shapely.intersection(footprints, all_polygons)).sum())
        if overlap > 1e-8:
            raise AssertionError("ground clearing touches an annotated structure")
        # Whole-ground removal must recreate the no-ground control exactly.
        none_folder = folder.parent / f"{folder.name.rsplit('-', 1)[0]}-none"
        # Ground mode contains underscores, never a hyphen.
        none = load_v3_case_bundle(none_folder).request
        if request_fingerprint(cleared) != request_fingerprint(none):
            raise AssertionError("clearing changed building-to-building transport")
        audits.append(
            {
                "case": folder.name,
                "max_building_area_error_m2": max(errors),
                "ground_clearance_building_overlap_m2": overlap,
                "cleared_equals_no_ground_request": True,
                **a,
            }
        )
    time_checks = []
    for source in p["pairs"][0]["owner_ids"]:
        pair = [
            r
            for r in records
            if r["pair_id"] == PAIRS[0]
            and r["spacing_m"] == 1
            and r["ground_mode"] == "none"
            and r["source_owner"] == source
            and r["channel"] == "both"
        ]
        x, y = sorted(pair, key=lambda r: -r["dt_s"])
        a, b = x["first_receiver_ignition_s"], y["first_receiver_ignition_s"]
        time_checks.append(
            {
                "source_owner": source,
                "same_ignition_outcome": (a is None) == (b is None),
                "time_difference_s": abs(a - b) if a is not None and b is not None else None,
            }
        )
    result = {
        "protocol_sha256": report["protocol_sha256"],
        "record_count": len(records),
        "source_hashes_verified": True,
        "mesh_inputs_verified": len(mesh_inputs),
        "all_full_90min": True,
        "any_guard_ignition": False,
        "all_energy_checks_passed": True,
        "cases": audits,
        "time_step_checks": time_checks,
        "field_accuracy_claimed": False,
        "all_fields_other_buildings_preserved_by_clearing": True,
    }
    dump(output / "audit.json", result)
    return p, report, result


def mesh_figure(output, pair):
    rows = json.loads((output / "buildings.source.json").read_text())["buildings"]
    polygons = [
        unary_union([Polygon(v["exterior"], v["holes"]) for v in b["polygons"]])
        for key in pair["building_ids"]
        for b in rows
        if b["building_id"] == key
    ]
    a, b = nearest_points(*polygons)
    center = np.mean([a.coords[0], b.coords[0]], axis=0)
    xmin, ymin = center - 10
    xmax, ymax = center + 10
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 410">',
        '<rect width="1440" height="410" fill="#f6f4ee"/>',
    ]
    for i, spacing in enumerate([5, 2.5, 1, 0.5]):
        folder = output / "cases" / f"{pair['pair_id']}-{spacing:g}m-none"
        if not folder.exists():
            continue
        q = load_v3_case_bundle(folder).request
        xoff = 15 + 360 * i
        yoff = 57
        scale = 16

        def screen(x, y, xoff=xoff, yoff=yoff, scale=scale):
            return xoff + (x - xmin) * scale, yoff + (ymax - y) * scale

        parts.extend(
            [
                f'<text x="{xoff}" y="24" font-family="sans-serif" font-size="19">{spacing:g} m mesh</text>',
                f'<text x="{xoff}" y="45" font-family="sans-serif" font-size="13">Direct face pairs: {load_v3_case_bundle(folder).metadata["mesh_audit"]["direct_building_face_pairs"]}</text>',
                f'<svg x="{xoff}" y="{yoff}" width="320" height="320" viewBox="0 0 320 320">',
                '<rect width="320" height="320" fill="white"/>',
            ]
        )
        for r, c in np.argwhere(q.building_index >= 0):
            left = q.grid.x_min_m + c * spacing
            top = q.grid.y_max_m - r * spacing
            if left > xmax or left + spacing < xmin or top < ymin or top - spacing > ymax:
                continue
            px, py = screen(left, top)
            color = "#5a8eb4" if q.building_index[r, c] == pair["owner_ids"][0] else "#d99255"
            parts.append(
                f'<rect x="{px - xoff}" y="{py - yoff}" width="{spacing * scale}" height="{spacing * scale}" fill="{color}" fill-opacity=".65" stroke="white" stroke-width=".5"/>'
            )
        for polygon in polygons:
            for p in getattr(polygon, "geoms", [polygon]):
                coords = [screen(x, y) for x, y in p.exterior.coords]
                points = " ".join(f"{x - xoff},{y - yoff}" for x, y in coords)
                parts.append(
                    f'<polygon points="{points}" fill="none" stroke="#1e252b" stroke-width="1.6"/>'
                )
        parts.append("</svg>")
    parts.append(
        '<text x="15" y="400" font-family="sans-serif" font-size="14">Black: annotated footprint. Color: occupied CA cells (uniform subcell mixing). Same 20 m window; white gap is visible at fine meshes.</text></svg>'
    )
    path = output / "review" / "narrow-gap-mesh.svg"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(parts))


def render(output):
    protocol, report, audit = check(output)
    review = output / "review"
    review.mkdir(exist_ok=True)
    mesh_figure(output, next(p for p in protocol["pairs"] if p["pair_id"] == PAIRS[0]))
    rows = report["records"]
    fields = [
        "pair_id",
        "spacing_m",
        "source_owner",
        "ground_mode",
        "cleared",
        "dt_s",
        "channel",
        "first_receiver_ignition_s",
        "ignited_receiver_area_m2",
        "removed_ground_area_m2",
        "receiver_radiation_J",
        "receiver_contact_J",
        "actual_horizon_s",
    ]
    with (review / "records.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    def value(x):
        return "90 分钟未引燃" if x is None else f"{x:.2f} s"

    comparisons = []
    for pair in protocol["pairs"]:
        for spacing in [5, 2.5, 1]:
            for source in pair["owner_ids"]:
                subset = [
                    r
                    for r in rows
                    if r["pair_id"] == pair["pair_id"]
                    and r["spacing_m"] == spacing
                    and r["source_owner"] == source
                    and r["channel"] == "both"
                    and r["dt_s"] == 1
                ]
                lookup = {(r["ground_mode"], r["cleared"]): r for r in subset}
                base = lookup["none", False]
                comparisons.append(
                    {
                        "pair": pair["pair_id"],
                        "gap_m": pair["polygon_gap_m"],
                        "spacing_m": spacing,
                        "source": source,
                        "no_ground_s": base["first_receiver_ignition_s"],
                        "mapped_s": lookup["mapped", False]["first_receiver_ignition_s"],
                        "cleared_s": lookup["mapped", True]["first_receiver_ignition_s"],
                        "synthetic_bridge_s": lookup["synthetic_grass_bridge", False][
                            "first_receiver_ignition_s"
                        ],
                        "cleared_area_m2": lookup["mapped", True]["removed_ground_area_m2"],
                    }
                )
    fine = [c for c in comparisons if c["spacing_m"] == 1]
    summary = {
        "protocol_sha256": sha(output / "protocol.json"),
        "audit_sha256": sha(output / "audit.json"),
        "count": len(rows),
        "comparisons": comparisons,
        "extra_controls": [
            r for r in rows if r["dt_s"] != 1 or r["channel"] != "both" or r["spacing_m"] == 0.5
        ],
        "scope": protocol["scope"],
        "mesh_audits": audit["cases"],
        "time_step_checks": audit["time_step_checks"],
    }
    dump(ROOT / "docs/results/batuan-local-mesh-20261017.json", summary)
    lines = [
        "# 岜团村局部细网格核验（2026-10-17）",
        "",
        "先核查冻结的完整场地复算，再用人工轮廓重新建立局部网格；未修改主模拟器、点燃阈值或建筑材质。",
        "",
        f"共完成 {len(rows)} 项90分钟对照，全部通过能量、计算边界和输入哈希检查。",
        "3组建筑对按既有几何分层选取；5/2.5/1米对照，最窄间隙补充0.5米及时间步、传播通道核验。",
        "",
        "## 1米网格结果",
        "",
        "| 建筑对 | 几何间距 | 起火建筑 | 原映射地表 | 全清理地表 | 假定草本连接 | 清理面积㎡ |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for c in fine:
        lines.append(
            f"| {c['pair']} | {c['gap_m']:.2f}m | B{c['source'] + 1:04d} | {value(c['mapped_s'])} | {value(c['cleared_s'])} | {value(c['synthetic_bridge_s'])} | {c['cleared_area_m2']:.2f} |"
        )
    lines.extend(
        [
            "",
            "清理是固定局部范围内的全部地面燃料移除，用于检查最大可能作用，不是已经优化的窄带施工方案。",
            "“假定草本连接”是单独的合成机制对照，不代表已经识别出现场巷道长草。",
            "",
            "## 证据边界与下一步",
            "",
            "1. 5米网格会把3.50米间隙处理为建筑相邻；2.5米仍有一条伪共享边，1米和0.5米则无直接建筑共享边。",
            "2. 全清理后两栋建筑的质量、热释放率及建筑间辐射图与无地面燃料控制完全一致。清理不产生防火墙。",
            "3. 各网格按真实轮廓交面积重建，保持单位面积木构参数。5米存在一个双建筑混合格，损失约0.043㎡，在表中保留，不伪称严格守恒。",
            "4. 细网格只提高建筑几何解析度：地表沿用5米推测分类，DEM沿用约30米FABDEM。原建筑格中新露出的地面属于未核实区域，设为不燃控制而非宣称实测无燃料。",
            "5. 源建筑全部屋面元胞同时点燃，是固定面热释放率的高暴露对照；无风、固定合成等效木材与水分、无飞火、无扑救，不是实地安全间距。",
            "6. 用这些结果修正村内措施的作用边界，随后补充巷道实测燃料和建筑防火构造数据；不以降低点燃阈值来制造清理收益。",
            "",
            "所有98项记录、原始案例和独立审计保存在 `runs/research/batuan-local-mesh-20261017-r2`。",
            "完整场地验证结果另见 [联合原型结果](BATUAN_JOINT_PILOT_RESULTS_20261017.md)。",
            "",
        ]
    )
    (ROOT / "docs/BATUAN_LOCAL_MESH_RESULTS_20261017.md").write_text("\n".join(lines))
    table = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(v))}</td>"
            for v in [
                c["pair"],
                f"{c['spacing_m']:g}",
                f"B{c['source'] + 1:04d}",
                value(c["mapped_s"]),
                value(c["cleared_s"]),
                value(c["synthetic_bridge_s"]),
                f"{c['cleared_area_m2']:.1f}",
            ]
        )
        + "</tr>"
        for c in comparisons
    )
    extras = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(v))}</td>"
            for v in [
                r["spacing_m"],
                f"B{r['source_owner'] + 1:04d}",
                r["dt_s"],
                r["channel"],
                value(r["first_receiver_ignition_s"]),
            ]
        )
        + "</tr>"
        for r in summary["extra_controls"]
    )
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>岜团村｜局部细网格验证</title><style>body{margin:0;background:#f6f4ee;color:#1f2d35;font:16px/1.65 system-ui,sans-serif}main{max-width:1200px;margin:45px auto;padding:0 24px}h1{font-size:34px}h2{margin-top:38px;font-size:24px}.meta{color:#51666b}section{background:white;padding:22px;border-radius:12px;margin:20px 0}img{width:100%}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:9px;border-bottom:1px solid #dae1df;text-align:left}th{background:#edf1ed}a{color:#16615e}.scroll{overflow-x:auto}code{background:#e9eeeb;padding:2px 6px}</style><main><p class="meta">PYFIRECA · B A T U A N · 2026-10-17</p><h1>窄巷被网格抹掉后，清理措施还有多大作用？</h1><p>已完成98项局部90分钟对照。建筑来自人工轮廓；保留原传播核、固定合成木材参数。此页展示数值核验，尚不能给出现场安全间距。</p><section><strong>先读比较方式</strong><p>原映射地表与“全清理地表”比较地面燃料的作用。假定草本连接是独立合成控制。全清理不会遮挡建筑间辐射；源建筑全屋面同时点燃，为高暴露测试。</p></section><h2>同一个3.50米间隙，四种网格</h2><img src="narrow-gap-mesh.svg" alt="5米、2.5米、1米和0.5米网格对同一个人工建筑间隙的表达"><p class="meta">黑线为标注轮廓；色格为建筑元胞。1米和0.5米已消除两栋建筑的伪共享边。0.5米只核验最窄建筑对的无地表控制。</p><h2>三组间隙：首次受体建筑引燃时间</h2><div class="scroll"><table><thead><tr><th>建筑对</th><th>网格m</th><th>源建筑</th><th>原映射地表</th><th>全清理地表</th><th>假定草本连接</th><th>清理㎡</th></tr></thead><tbody>TABLE</tbody></table></div><h2>最窄间隙的时间步与传播通道对照</h2><table><tr><th>网格m</th><th>源建筑</th><th>时间步s</th><th>通道</th><th>首次引燃</th></tr>EXTRAS</table><section><strong>数据边界</strong><p>局部只保留两栋活动建筑与固定范围地表，其他建筑作为不活动机制控制。细网格地表仍继承5米推测分类；DEM仍来自约30米FABDEM。新露出但缺少地表记录的间隙设为不燃控制，并单独记录。98项均达到5400秒、未触及活动边界、通过能量审计。</p><p>结果用于区分清理连接燃料与建筑跨间隙辐射，不能把模型内未引燃解释为施工标准。</p></section><p><a href="records.csv">下载全部对照 CSV</a> · <a href="../">返回完整场地验证报告</a></p></main></html>"""
    (review / "index.html").write_text(page.replace("TABLE", table).replace("EXTRAS", extras))
    print(json.dumps({"report": str(review / "index.html"), "runs": len(rows), "audit": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    render(parser.parse_args().output.resolve())
