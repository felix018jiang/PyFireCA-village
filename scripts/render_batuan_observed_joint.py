"""Render progress or audit the completed observed-object joint comparison."""

# ruff: noqa: E501, RUF001

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_batuan_building_pairs import ROOT, dump, sha
from run_batuan_joint_search import digest

TITLES = {
    "baseline": "无新增干预",
    "outer_proxy": "仅村外优化",
    "inner_proxy": "仅村内优化",
    "joint_conditional": "原条件联合搜索",
    "joint_diverse": "分配分层联合搜索",
    "reference_outer_proxy": "原村外参考方案（本轮未找到达标解）",
    "reference_inner_proxy": "原村内参考方案（本轮未找到达标解）",
}


def render(output):
    protocol = json.loads((output / "protocol.json").read_text())
    report_path = output / "report.json"
    rows = []
    modes = ["outer_proxy", "inner_proxy", "joint_conditional", "joint_diverse"]
    for mode in modes:
        path = output / f"{mode}-search.json"
        progress = output / f"{mode}-progress.json"
        value = json.loads(path.read_text()) if path.exists() else None
        if value:
            best = value.get("selected")
            rows.append(
                {
                    "mode": mode,
                    "queries": len(value["records"]),
                    "stage": "设计集搜索完成" if best else "未找到设计集达标解",
                    "area_m2": best["cleared_area_m2"] if best else None,
                    "selected": best["plan_id"] if best else None,
                }
            )
        else:
            state = json.loads(progress.read_text()) if progress.exists() else {}
            rows.append(
                {
                    "mode": mode,
                    "queries": state.get("query_index", 0),
                    "stage": "设计集搜索中" if state else "已预设，等待运行",
                    "area_m2": None,
                    "selected": None,
                }
            )
    table = "".join(
        f"<tr><td>{TITLES[r['mode']]}</td><td>{r['queries']}/{protocol['candidate_budget_per_mode']}</td><td>{r['stage']}</td><td>{r['area_m2']:.2f}</td></tr>"
        if r["area_m2"] is not None
        else f"<tr><td>{TITLES[r['mode']]}</td><td>{r['queries']}/{protocol['candidate_budget_per_mode']}</td><td>{r['stage']}</td><td>待确认</td></tr>"
        for r in rows
    )
    confirmation = ""
    audit = None
    if report_path.exists():
        report = json.loads(report_path.read_text())
        selection = json.loads((output / "selection.json").read_text())
        if (
            report["selection"] != selection
            or selection["protocol_sha256"] != sha(output / "protocol.json")
            or selection["held_out_results_read"]
        ):
            raise AssertionError("selection was not frozen as declared")
        for mode, chosen in selection["selected"].items():
            search = json.loads((output / f"{mode}-search.json").read_text())
            if len(search["records"]) > protocol["candidate_budget_per_mode"]:
                raise AssertionError("search exceeded its declared request cap")
            valid = [
                r for r in search["records"] if r["status"] == "ok" and r["effectiveness"][0] == 0
            ]
            expected_choice = (
                min(valid, key=lambda r: (r["cleared_area_m2"], r["plan_id"])) if valid else None
            )
            if chosen != expected_choice or search["selected"] != chosen:
                raise AssertionError("frozen selection differs from design-only search")
            if chosen and (
                len(chosen["training_metrics"]) != len(protocol["training"])
                or any(
                    m["ignited_house_count"] or m["ignited_bridge_count"]
                    for m in chosen["training_metrics"]
                )
            ):
                raise AssertionError("selected plan did not pass every declared design scenario")
        for name, h in protocol["source_hashes"].items():
            if sha(ROOT / name) != h or sha(output / "source_snapshot" / name) != h:
                raise AssertionError(f"frozen source changed: {name}")
        for name, h in protocol["input_hashes"].items():
            if sha(name) != h:
                raise AssertionError(f"frozen input changed: {name}")
        caches = 0
        for folder in ["cache", "full-cache"]:
            for file in (output / folder).glob("*.json"):
                entry = json.loads(file.read_text())
                if (
                    entry["signature"] != file.stem
                    or digest(entry["record"]) != entry["record_sha256"]
                ):
                    raise AssertionError("cache signature/checksum mismatch")
                caches += 1
        expected = {
            r["scenario_id"]
            for role in ["training", "held_out", "internal_probes"]
            for r in protocol[role]
        }
        summary = []
        for label in report["plans"]:
            records = [r for r in report["confirmation"] if r["label"] == label]
            if len(records) != len(expected) or {r["scenario_id"] for r in records} != expected:
                raise AssertionError("incomplete confirmation suite")
            first = records[0]["record"]
            for item in records:
                record = item["record"]
                d = record["diagnostics"]
                m = record["metrics"]
                a = item["assessment"]
                if (
                    not d["reached_requested_horizon"]
                    or abs(d["simulated_until_s"] - 5400) > 1e-8
                    or m["burning_reached_domain_edge"]
                ):
                    raise AssertionError("confirmation incomplete or boundary affected")
                if (
                    m["object_observation"]["object_count"] != 333
                    or m["object_observation"]["unrepresented_labels"]
                    or m["object_observation"]["partly_unrepresented"]
                ):
                    raise AssertionError("full object observation incomplete")
                if a["source_family"] != item["source_family"]:
                    raise AssertionError("source families mixed")
                if max(d["energy_closure_errors_J"].values()) > d["energy_tolerance_J"]:
                    raise AssertionError("energy closure failed")
                cached = output / "confirm" / f"{label}-{item['scenario_id']}.json"
                if json.loads(cached.read_text()) != item:
                    raise AssertionError("confirmation record differs")
            counts = {}
            for role in ["training", "held_out", "internal_probes"]:
                sids = {s["scenario_id"] for s in protocol[role]}
                r = [x for x in records if x["scenario_id"] in sids]
                counts[role] = {
                    "passed": sum(x["assessment"]["all_protected_unignited"] for x in r),
                    "count": len(r),
                }
            summary.append(
                {
                    "label": label,
                    "area_m2": first["intervention"]["cleared_area_m2"],
                    "zone_areas_m2": first["intervention"]["zone_areas_m2"],
                    "counts": counts,
                }
            )
        confirmation = (
            "<h2>完整场地90分钟确认</h2><table><tr><th>方案</th><th>设计</th><th>新留出</th><th>内部源探针（单独）</th><th>清理㎡</th></tr>"
            + "".join(
                f"<tr><td>{TITLES[r['label']]}</td>"
                + "".join(
                    f"<td>{r['counts'][key]['passed']}/{r['counts'][key]['count']}</td>"
                    for key in ["training", "held_out", "internal_probes"]
                )
                + f"<td>{r['area_m2']:.2f}</td></tr>"
                for r in summary
            )
            + "</table>"
        )
        audit = {
            "protocol_sha256": sha(output / "protocol.json"),
            "frozen_selection_verified": True,
            "confirmation_count": len(report["confirmation"]),
            "all_full_horizon": True,
            "any_boundary_ignition": False,
            "observed_object_count": 333,
            "cache_records_verified": caches,
            "summary": summary,
            "internal_probes_not_used_for_search": True,
            "field_accuracy_claimed": False,
            "global_optimality_claimed": False,
        }
        dump(output / "audit.json", audit)
        dump(ROOT / "docs/results/batuan-observed-joint-20261017.json", audit)
        lines = [
            "# 岜团村逐标注对象联合配置：新一轮结果",
            "",
            "保护对象采用333个人工标注的稀疏观测，不重复计量燃料；嵌套标注的实际对象语义仍未确认。",
            "设计12项，新留出4项，内部起火2项探针单独报告；内部探针不参与本轮外部来火选线。",
            "",
            "| 方案 | 设计 | 新留出 | 内部探针 | 清理㎡ |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in summary:
            values = [
                f"{row['counts'][key]['passed']}/{row['counts'][key]['count']}"
                for key in ["training", "held_out", "internal_probes"]
            ]
            lines.append(
                f"| {TITLES[row['label']]} | {' | '.join(values)} | {row['area_m2']:.2f} |"
            )
        lines.extend(
            [
                "",
                "每个模式最多32项独特候选请求。保留不同内外清理面积比例的父代，并加入同时带宽交换和区域重组；仅按设计集冻结方案。",
                "当前比较达到相同保护目标所需的实际清理面积；仍需完整的等面积、多重启动和预算收敛研究，不宣称连续全局最优。",
                "村内探针只排除预先指定且t=0直接引燃的源对象，不能据外部情景通过率推断村内分隔成功。",
                "主体CA燃烧/辐射/接触规则未改；含水率固定、合成等效木材、无飞火、无扑救。整村5米网格不解析亚格独立温度。",
                "",
            ]
        )
        (ROOT / "docs/BATUAN_OBSERVED_JOINT_RESULTS_20261017.md").write_text("\n".join(lines))
    else:
        completed = (
            len(list((output / "confirm").glob("*.json"))) if (output / "confirm").exists() else 0
        )
        confirmation = f"<h2>完整场地确认</h2><p>已写出{completed}项确认记录；最终统一审计尚未完成。新留出结果不会反向用于本轮选线。</p>"
    review = output / "review"
    review.mkdir(exist_ok=True)
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>岜团村｜逐对象联合优化</title><style>body{font:16px/1.7 system-ui,sans-serif;background:#f6f4ee;color:#24343b;max-width:1120px;margin:40px auto;padding:0 24px}h1{font-size:32px}h2{margin-top:34px}aside{background:#e7f0ed;padding:18px;border-radius:12px}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:11px;border-bottom:1px solid #dce3de}th{background:#eaf0eb}.muted{color:#566d74}a{color:#19615e}</style><p class="muted">PYFIRECA · 2026-10-17 · 新协议独立运行</p><h1>逐建筑观测与村内外联合优化</h1><aside>当前燃料仍只计量一次；333个标注对象分别观测，共享格保留一个温度。原始嵌套标注的独立建筑语义尚未确认。主体燃烧规则、固定水分与合成等效木材不变。</aside><p>本轮预设12项外部来火设计情景、4项新位置/风向留出、2项内部起火探针。四种搜索使用相同的32项候选请求上限。先按设计集冻结，再完整场地确认。</p><h2>搜索状态</h2><table><tr><th>模式</th><th>候选请求</th><th>阶段</th><th>设计集达标面积㎡</th></tr>TABLE</table>CONFIRM<h2>检验重点</h2><p>检验保留不同内外分配候选能否改善搜索；不预设混合投入一定占优。面积为实际清理植被并集，未加入货币成本。内部起火仅排除指定源建筑，其余民居与风雨桥仍受保护。</p><p>地表仍为RGB推测分类，DEM原始约30米；本轮整村计算5米。98项局部细网格试验已说明清理燃料不等于消除建筑间辐射，不能据本页推导施工安全宽度。</p><p><a href="../">返回上一轮完整报告</a> · <a href="../local-mesh/">查看局部细网格核验</a></p></html>"""
    (review / "index.html").write_text(
        page.replace("TABLE", table).replace("CONFIRM", confirmation)
    )
    dump(
        output / "status.json",
        {
            "stage": "audited" if audit else "running",
            "search": rows,
            "report_exists": report_path.exists(),
            "audit_exists": audit is not None,
        },
    )
    print(json.dumps({"report": str(review / "index.html"), "audited": audit is not None}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    render(parser.parse_args().output.resolve())
