"""Render the frozen search report and add explicit independent structural checks.

Postprocessing is versioned separately from the already frozen CA experiment.
"""
# ruff: noqa: RUF001, E501

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from render_batuan_adaptive_report import LABELS, render


def validation_section(report):
    rows, caveats = [], []
    minutes = report["protocol"]["training"][0]["horizon_s"] / 60
    for label, checks in report["validation"].items():
        normal, longer = checks["held_out"]["metrics"], checks["extended"]["metrics"]
        rows.append(
            f"<tr><td>{html.escape(LABELS[label])}</td>"
            f"<td>{normal['ignited_house_count']}</td>"
            f"<td>{normal['ignited_bridge_count']}</td>"
            f"<td>{normal['ignited_building_area_m2']:.2f}</td>"
            f"<td>{longer['ignited_house_count']}</td>"
            f"<td>{longer['ignited_bridge_count']}</td>"
            f"<td>{longer['ignited_building_area_m2']:.2f}</td></tr>"
        )
        if label.startswith("adaptive_"):
            old = report["validation"][label.replace("adaptive_", "legacy_")]["extended"]["metrics"]
            if (
                longer["ignited_house_count"] > old["ignited_house_count"]
                or longer["ignited_bridge_count"] > old["ignited_bridge_count"]
                or longer["ignited_building_area_m2"] > old["ignited_building_area_m2"] + 1e-6
            ):
                caveats.append(
                    f"{LABELS[label]}：延时复核中至少一项结构受火指标高于原单带；"
                    f"{minutes:g}分钟训练效果相同不代表更长时间下保护效果相同。"
                )
    note = " ".join(caveats) or "上述数字来自冻结后的独立检查，未用来重新挑选方案。"
    return f"""<section id="independent-structural-validation"><h2>独立验证：民居、风雨桥与建筑受火面积</h2>
    <p>表内均使用同一留出起火点。左侧为训练时限，右侧为延长时限，均在声明的搜索域上计算。
    这些结果没有回传选优；民居零受火不能解释为全部结构都安全。</p>
    <p>本报告“受火”指已达到引燃判据；回放中的“受影响建筑/桥”还包含受热但尚未引燃的对象，两者统计口径不同。</p>
    <div class="scroll"><table><tr><th>方案</th><th>留出民居</th><th>留出桥</th><th>留出建筑受火面积 m²</th>
    <th>延时民居</th><th>延时桥</th><th>延时建筑受火面积 m²</th></tr>{"".join(rows)}</table></div>
    <p><strong>{html.escape(note)}</strong></p></section>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args()
    study = args.study.resolve()
    report = json.loads((study / "report.json").read_text())
    render(study)
    page = study / "index.html"
    content = page.read_text()
    anchor = '<div class="grid">'
    if content.count(anchor) != 1:
        raise ValueError("expected exactly one map-card insertion anchor")
    page.write_text(content.replace(anchor, validation_section(report) + anchor, 1))
    source = Path(__file__).resolve()
    snapshot = study / "render_source_snapshot" / source.name
    snapshot.parent.mkdir(exist_ok=True)
    shutil.copy2(source, snapshot)
    (study / "render-provenance.json").write_text(
        json.dumps(
            {
                "postprocessing_source": source.name,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "experiment_code": report["protocol"]["code"],
                "report_sha256": hashlib.sha256((study / "report.json").read_bytes()).hexdigest(),
                "index_sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                "selection_sha256": report["selection_sha256"],
                "validation_used_for_reselection": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
