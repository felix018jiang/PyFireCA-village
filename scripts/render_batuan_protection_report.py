"""Render the joint-protection study without feeding test outcomes into selection."""

# ruff: noqa: E501, RUF001
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from render_batuan_adaptive_report import map_image
from run_batuan_adaptive_search import digest, dump

FAMILY = {"straight": "单直线", "polyline": "单折线", "multiple": "多条带组合"}
ZONE = {"outer_proxy": "村外代理区", "inner_proxy": "村内近邻代理区"}


def label_name(label):
    if label == "baseline":
        return "无防火线"
    if label.startswith("previous_adaptive_"):
        return "上一版60分钟选线 · " + ZONE[label.removeprefix("previous_adaptive_")]
    for zone, title in ZONE.items():
        if label.startswith(zone + "_"):
            return title + " · " + FAMILY[label.removeprefix(zone + "_")]
    return label


def metric_cells(records):
    cells = []
    for record in records:
        m = record["metrics"]
        h, b = m["ignited_house_count"], m["ignited_bridge_count"]
        cells.append(f'<td class="{"pass" if h + b == 0 else "fail"}">{h} / {b}</td>')
    return "".join(cells)


def history_svg(search):
    # Plot only fully protective incumbents; infeasible points cannot look efficient.
    points = [h for h in search["history"] if h["best_effectiveness"][0] == 0]
    if not points:
        return "<p>在本次预算内尚未找到训练情景全部保护的该类方案。</p>"
    max_area = max(h["best_cleared_area_m2"] for h in points) * 1.05
    cap = search["config"]["max_evaluations"]
    path = " ".join(
        f"{45 + 500 * h['evaluations'] / cap:.2f},{205 - 160 * h['best_cleared_area_m2'] / max_area:.2f}"
        for h in points
    )
    return f'<svg viewBox="0 0 600 255" role="img" aria-label="已满足保护约束的当前最好面积收敛曲线"><path d="M45 25V205H555" fill="none" stroke="#708495"/><polyline points="{path}" fill="none" stroke="#62dcc7" stroke-width="2.5"/><text x="45" y="18">清理面积（上限 {max_area:.0f} m²）</text><text x="360" y="240">累计方案请求 → {cap}</text></svg>'


def annotate_replay_objective(study, report):
    """Correct a legacy exporter display label; preserve all physical arrays."""
    audit_path = study / "replay-display-annotations.json"
    changes = json.loads(audit_path.read_text()) if audit_path.exists() else []
    objective = "joint_90min_house_bridge_protection_then_minimum_actual_clearance_area"
    for label, entry in report["full_domain_comparison"].items():
        root = study / "full" / entry["evaluation"]["intervention_id"]
        for relative, nested in (
            ("case/manifest.json", "metadata"),
            ("run/run.json", None),
            ("run/viewer/manifest.json", "scenario"),
        ):
            path = root / relative
            original = path.read_bytes()
            value = json.loads(original)
            target = value[nested] if nested else value
            if target.get("research_objective") == objective:
                continue
            before = target.get("research_objective")
            target["research_objective"] = objective
            target["research_selection_sha256"] = report["selection_sha256"]
            dump(path, value)
            changes.append(
                {
                    "label": label,
                    "path": str(path.relative_to(study)),
                    "previous_objective": before,
                    "objective": objective,
                    "before_sha256": hashlib.sha256(original).hexdigest(),
                    "after_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "changes_physics_or_arrays": False,
                }
            )
    dump(audit_path, changes)


def render(study):
    study = Path(study).resolve()
    report = json.loads((study / "report.json").read_text())
    if report["status"] != "completed":
        raise ValueError("complete the study before rendering its conclusions")
    frozen = json.loads((study / "selection.json").read_text())
    if (
        frozen["sha256"] != report["selection_sha256"]
        or digest(report["chosen_plans"]) != frozen["sha256"]
    ):
        raise ValueError("frozen selection checksum mismatch")
    annotate_replay_objective(study, report)
    coverage_note = ""
    coverage_path = study / "protection-coverage.json"
    if coverage_path.exists():
        coverage = json.loads(coverage_path.read_text())
        covered = all(
            e["uncovered_by_building_cells_m2"] < 1e-6 for e in coverage["unrepresented_structures"]
        )
        coverage_note = (
            f"<p>人工标注 {coverage['annotated_count']} 个轮廓，当前网格为 "
            f"{coverage['represented_count']} 个建筑归属；两处嵌套轮廓的面积全部由其他建筑格承载。"
            "零引燃判据包含这些重叠区域，但逐栋损失计数需核对轮廓包含关系。"
            '<a href="protection-coverage.json">保护对象覆盖审计</a></p>'
            if covered
            else "<p>有标注轮廓未被建筑格完整覆盖，不能声明全标注保护。</p>"
        )
    rows, sensitivity, cards, histories = [], [], [], []
    for label, records in report["controls"].items():
        area = records[0]["intervention"]["cleared_area_m2"]
        row = f"<tr><td>{html.escape(label_name(label))}</td><td>{area:.2f}</td>"
        rows.append(
            row + metric_cells(records) + metric_cells(report["validation"][label]) + "</tr>"
        )
        sensitivity.append(row + metric_cells(report["sensitivity"][label]) + "</tr>")
    for label, entry in report["full_domain_comparison"].items():
        filename = label + ".jpg"
        map_image(study, entry, filename, report)
        m = entry["evaluation"]["metrics"]
        pid = entry["evaluation"]["intervention_id"]
        cards.append(
            f'<article><h3>{label_name(label)}</h3><p>T1完整网格90分钟：{m["ignited_house_count"]} 栋民居引燃，{m["ignited_bridge_count"]} 座桥引燃。</p><img src="{filename}" alt="真实卫星底图、人工建筑轮廓、模拟火场与干预范围"><p>青色清理、橙色燃烧、深色燃尽、黄色受热、白色建筑轮廓。</p><a href="full/{pid}/run/viewer/">90分钟逐帧回放</a> · <a href="full/{pid}/intervention.geojson">GIS范围</a></article>'
        )
    for key, search in report["searches"].items():
        zone, family = key.split("/")
        chosen = next(r for r in search["records"] if r["plan_id"] == search["representative"])
        valid = sum(r["status"] == "ok" for r in search["records"])
        safe = sum(r["status"] == "ok" and r["effectiveness"][0] == 0 for r in search["records"])
        hist_rows = "".join(
            f"<tr><td>{h['generation']}</td><td>{h['evaluations']}</td><td>{h['step_m']:g}</td><td>{h['best_effectiveness'][0]}</td><td>{h['best_cleared_area_m2']:.2f}</td></tr>"
            for h in search["history"]
        )
        histories.append(
            f'<article><h3>{ZONE[zone]} · {FAMILY[family]}</h3><p>{len(search["records"])} 次请求，{valid} 个物理有效方案，{safe} 个训练情景均未引燃建筑/桥的方案。选中 {len(chosen["plan"]["paths"])} 条路径、节点数 {[len(p["points_m"]) for p in chosen["plan"]["paths"]]}。</p>{history_svg(search)}<details><summary>代际收敛记录</summary><table><tr><th>代</th><th>累计请求</th><th>步长m</th><th>最差引燃结构数</th><th>清理m²</th></tr>{hist_rows}</table></details><a href="{zone}-{family}-search.json">完整提案、几何与拒绝原因</a></article>'
        )
    title = "岜团村 · 90分钟民居与风雨桥联合保护"
    total = sum(len(s["records"]) for s in report["searches"].values())
    factor = report["protocol"]["sensitivity"]["rise_factor"]
    source = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#101c25;color:#e5edf2;font:15px/1.7 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:28px}}h1{{font-size:30px;line-height:1.35}}h2{{font-size:22px}}h3{{font-size:18px}}p{{color:#b9cad7}}a,summary{{color:#62dcc7}}section,article{{border:1px solid #354958;background:#172833;padding:22px;margin:20px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}}.grid article{{margin:0}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:11px 9px;border-bottom:1px solid #354958;text-align:left;white-space:nowrap}}th{{color:#bbcbd5}}.pass{{color:#79e3bf}}.fail{{color:#ffad96}}img{{display:block;width:100%}}svg{{width:100%;max-width:700px}}svg text{{fill:#b9cad7;font-size:13px}}code{{color:#f0ce91}}summary{{cursor:pointer}}.eyebrow{{color:#62dcc7;letter-spacing:.07em}}.callout{{border-left:3px solid #62dcc7;padding-left:16px}}
    </style><main><p class="eyebrow">PYFIRECA / BATUAN / FROZEN EXPERIMENT</p><h1>{title}</h1><p class="callout">先满足90分钟内全部训练情景的民居和桥均不引燃，再减少有效植被清理面积。三种线形分别搜索，两类代理区共 {total} 次方案请求。不把火灾损失与占地用任意权重相加。</p>
    <section><h2>相同预算下的线形比较</h2><p>每个区域、每种线形的请求上限均为 {report["protocol"]["configs"]["straight"]["max_evaluations"]}。直线、真实折线（至少一处非共线转折）、多条带（至少两条且各自有独立有效清理面积）分别优化。所有结果为90分钟终点。每格为<strong>引燃民居数 / 引燃风雨桥数</strong>，0 / 0 才通过保护目标。无干预及上一版选线均用本轮时限重新求解。</p><div class="scroll"><table><tr><th>方案</th><th>清理面积 m²</th><th>T1 基准</th><th>T2 基准</th><th>V1 新留出</th><th>V2 新留出</th></tr>{"".join(rows)}</table></div>{coverage_note}<p>T1=(160,151)，T2=(161,150)；V1=(163,151)，V2=(163,149)，均为父网格行列。T2原先是上一轮留出点，本轮已明确转为训练点；V1/V2是本轮预先固定的新邻近起火点，尚不代表多方向泛化。</p></section>
    <div class="grid">{"".join(cards)}</div>
    <section><h2>略易引燃的独立敏感性检验</h2><p>引燃温升乘以 {factor:g}：<code>T′ign = Tamb + {factor:g} × (Tign − Tamb)</code>。降低温升5%不等于引燃概率增加5%；含水率、蒸发、燃料量、热释放和传热公式不变。对同一批冻结方案统一重算，不利用此表重新选线。5%为敏感性假设，尚非材料实测标定。</p><div class="scroll"><table><tr><th>相同方案</th><th>清理面积 m²</th><th>T1 略易燃</th><th>T2 略易燃</th><th>V1 略易燃</th><th>V2 略易燃</th></tr>{"".join(sensitivity)}</table></div></section>
    <section><h2>搜索如何减少占地</h2><p>位置、方向、宽度、端点、折点、路径增删与重组由束搜索和局部模式搜索调整；停滞时缩小步长。曲线只显示满足训练保护约束后的最好面积。随机种子固定，本轮尚不是多种子统计优劣比较。</p><div class="grid">{"".join(histories)}</div></section>
    <section><h2>结果的研究含义</h2><p>当前结论针对南侧林地的局部起火压力测试，村外/村内以建筑20m缓冲区作为代理划分；村内措施仍是近邻植被清理，未包含拆房或建筑防火改造。无动态风、无飞火，参数含合成假设。全部建筑保留；清理按实际可燃地表并集计量，跨带辐射仍存在。不能将当前5m网格给出的窄带结果直接作为施工宽度。</p><p>完整网格对无干预及每区训练最优方案重算四个起火点并与局部网格对照；全图回放展示T1。引燃以有限 ignition_time 为准，回放中的“受影响”也包含受热未引燃。新留出及敏感性结果不反馈训练，最优指本轮访问到的最好方案，无全局最优保证。</p><p><a href="report.json">完整结果</a> · <a href="protocol.json">冻结协议</a> · <a href="selection.json">验证前冻结选线</a> · <a href="input-lock.json">输入指纹</a></p></section></main></html>"""
    (study / "index.html").write_text(source)
    snapshot = study / "render_source_snapshot" / Path(__file__).name
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    render_hashes = {}
    for name in (
        Path(__file__).name,
        "render_batuan_adaptive_report.py",
        "render_batuan_intervention_report.py",
    ):
        dependency = Path(__file__).with_name(name)
        (snapshot.parent / name).write_bytes(dependency.read_bytes())
        render_hashes[name] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    dump(
        study / "render-provenance.json",
        {
            "report_sha256": hashlib.sha256((study / "report.json").read_bytes()).hexdigest(),
            "selection_sha256": frozen["sha256"],
            "html_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "renderer_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "render_source_hashes": render_hashes,
            "validation_used_for_reselection": False,
        },
    )
    print(study / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    render(parser.parse_args().study)
