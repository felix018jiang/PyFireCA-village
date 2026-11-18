"""Audit the complete geometry comparison and report matched heritage and roof outcomes."""

# ruff: noqa: RUF001

import argparse
from collections import defaultdict
from hashlib import sha256
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev

from run_heritage_geometry_validation import DEST, PARENT, read_json, verify_registration
from run_heritage_wind_strategies import atomic_json, digest, record_hash
from run_heritage_wind_validation import case_name
from summarize_heritage_wind_validation import (
    atomic_csv,
    atomic_text,
    close,
    inspect_case,
    site_risk_upper_count,
)

VILLAGES = {"pingyan": "平岩", "batuan": "岜团"}
STRATA = {"near": "近源", "far": "远源"}
AREA_TOLERANCE_M2 = 1e-5
METRICS = (
    "ordinary_burned_m2",
    "ordinary_cleared_or_burned_m2",
    "ordinary_burned_owner_labels",
    "ground_burned_m2",
    "ground_cleared_footprint_m2",
)


def case(root, reg_hash, expected_solver, verified_backend, village, setup, fire, wind, plan):
    identifier = case_name(village, fire["fire_id"], wind["wind_id"], plan["plan_id"])
    path = root / "cases" / f"{identifier}.json"
    raw = read_json(path)
    audited = inspect_case(path, raw, reg_hash, fire, wind, plan, village, setup["core_ids"], root)
    backend = audited["execution_backend_sha256"]
    expected = (
        sha256(f"{expected_solver}/{backend}".encode()).hexdigest() if backend else expected_solver
    )
    if (
        audited["reference_solver_sha256"] != expected_solver
        or audited["solver_sha256"] != expected
        or backend not in ("", verified_backend)
    ):
        raise ValueError(f"Physical or numerical model identity changed: {identifier}")
    return raw, audited


def outcomes(row, plan, *, is_compact):
    obs = row["final"]
    roof = obs["roof_burned_out_m2"]
    if not is_compact and obs["core_ignited_count"]:
        raise ValueError("Archived wide arm requires a separate ordinary-roof readout")
    ordinary = obs["ordinary_roof_burned_out_m2"] if is_compact else roof
    if (
        not isfinite(ordinary)
        or ordinary < 0
        or ordinary > roof + AREA_TOLERANCE_M2
        or (obs["core_ignited_count"] == 0 and not close(ordinary, roof))
    ):
        raise ValueError("Ordinary and core roof areas are inconsistent")
    return {
        "ordinary_burned_m2": ordinary,
        "ordinary_cleared_or_burned_m2": plan["roof_cleared_m2"] + ordinary,
        "ordinary_burned_owner_labels": obs["ordinary_roof_burned_out_count"],
        "ground_burned_m2": obs["ground_burned_out_m2"],
        "ground_cleared_footprint_m2": plan["ground_cleared_m2"],
        "core_ignited_count": obs["core_ignited_count"],
        "at_60min_core_ignited_count": row["at_60min"]["core_ignited_count"],
        "core_peak_rise_K": obs["core_peak_rise_K"],
        "core_absorbed_radiation_J": obs["core_absorbed_radiation_J"],
        "last_burnout_s": obs["last_burnout_s"],
        "original_boundary_reached": int(obs["boundary"]["original_physical_boundary_reached"]),
    }


def matched_cases(root, reg):
    compact_hash = digest(root / "registration.json")
    wide_hash = reg["wide_registration"]["sha256"]
    backend_receipt = read_json(PARENT / "backend-verification.json")
    if backend_receipt["passed"] is not True or not backend_receipt["cases"]:
        raise ValueError("Verified numerical backend is required")
    backend = backend_receipt["backend_sha256"]
    pairs, heritage = [], []
    for village, setup in reg["villages"].items():
        (compact,) = setup["plans"]
        wide = setup["wide_reference"]
        for fire in setup["fires"]:
            for wind in reg["winds"]:
                c, c_audit = case(
                    root,
                    compact_hash,
                    reg["compact_reference_solver_sha256"],
                    backend,
                    village,
                    setup,
                    fire,
                    wind,
                    compact,
                )
                w, w_audit = case(
                    PARENT,
                    wide_hash,
                    reg["wide_reference_solver_sha256"],
                    backend,
                    village,
                    setup,
                    fire,
                    wind,
                    wide,
                )
                if any(
                    not close(c_audit[name], w_audit[name])
                    for name in ("source_area_m2", "source_energy_J")
                ):
                    raise ValueError(f"Nonpaired live source footprint or energy: {c['case_id']}")
                common = {
                    "village": village,
                    "stratum": fire["stratum"],
                    "fire_id": fire["fire_id"],
                    "source_building_id": fire["source_id"],
                    "wind_id": wind["wind_id"],
                }
                record = {
                    **common,
                    "footprint_sha256": fire["footprint_sha256"],
                    "source_area_m2": c_audit["source_area_m2"],
                    "source_energy_J": c_audit["source_energy_J"],
                }
                for label, row, plan, audit in (
                    ("wide", w, wide, w_audit),
                    ("compact", c, compact, c_audit),
                ):
                    record.update(
                        {
                            f"{label}_case_id": row["case_id"],
                            f"{label}_result_sha256": audit["result_sha256"],
                            f"{label}_certificate_sha256": audit["extinction_certificate_sha256"],
                            f"{label}_construction_m2": plan["construction_m2"],
                            f"{label}_roof_cleared_m2": plan["roof_cleared_m2"],
                        }
                    )
                    record.update(
                        {
                            f"{label}_{k}": v
                            for k, v in outcomes(row, plan, is_compact=label == "compact").items()
                        }
                    )
                for key in METRICS:
                    record[f"compact_minus_wide_{key}"] = (
                        record[f"compact_{key}"] - record[f"wide_{key}"]
                    )
                pairs.append(record)
                for core in setup["core_ids"]:
                    heritage.append(
                        {
                            **common,
                            "heritage_id": core,
                            **{
                                f"{label}_{k}": val
                                for label, row in (("wide", w), ("compact", c))
                                for k, val in row["final"]["core_outcomes"][core].items()
                            },
                        }
                    )
    expected = {f"{p['compact_case_id']}.json" for p in pairs}
    if len(pairs) != reg["primary"]["planned_pairs"] or any(
        expected != {f.name for f in (root / subdir).glob("*.json")}
        for subdir in ("cases", "certificates")
    ):
        raise ValueError("The observed cases do not match the complete frozen comparison")
    return pairs, heritage


def summarize(reg, pairs):
    groups = defaultdict(list)
    for row in pairs:
        groups[row["village"], row["stratum"]].append(row)
    rows = []
    for (village, stratum), group in groups.items():
        by_roof = defaultdict(list)
        for case_row in group:
            by_roof[case_row["source_building_id"]].append(case_row)
        n = len(by_roof)
        population = len(reg["villages"][village]["random_eligible_ids_by_stratum"][stratum])
        row = {
            "village": village,
            "stratum": stratum,
            "roofs": n,
            "pool": population,
            "pairs": len(group),
        }
        for arm in ("wide", "compact"):
            affected = sum(
                any(c[f"{arm}_core_ignited_count"] for c in cases) for cases in by_roof.values()
            )
            row.update(
                {
                    f"{arm}_first_ignition_cases": sum(
                        c[f"{arm}_core_ignited_count"] > 0 for c in group
                    ),
                    f"{arm}_first_ignition_cases_after_hour": sum(
                        c[f"{arm}_core_ignited_count"] > 0
                        and not c[f"{arm}_at_60min_core_ignited_count"]
                        for c in group
                    ),
                    f"{arm}_affected_roofs_any_wind": affected,
                    f"{arm}_affected_roof_fraction_upper95": site_risk_upper_count(
                        population, n, affected
                    )
                    / population,
                    f"{arm}_max_core_rise_K": max(c[f"{arm}_core_peak_rise_K"] for c in group),
                    f"{arm}_physical_boundary_cases": sum(
                        c[f"{arm}_original_boundary_reached"] for c in group
                    ),
                }
            )
            for metric in METRICS:
                amounts = [c[f"{arm}_{metric}"] for c in group]
                row[f"{arm}_mean_{metric}"] = fmean(amounts)
                row[f"{arm}_max_{metric}"] = max(amounts)
        for metric in METRICS:
            field = f"compact_minus_wide_{metric}"
            diffs = [c[field] for c in group]
            clustered = [fmean(c[field] for c in cases) for cases in by_roof.values()]
            row[f"mean_{field}"] = fmean(clustered)
            row[f"se_{field}"] = sqrt((1 - n / population) / n) * stdev(clustered)
            row[f"pairs_increased_{metric}"] = sum(d > AREA_TOLERANCE_M2 for d in diffs)
            row[f"pairs_decreased_{metric}"] = sum(d < -AREA_TOLERANCE_M2 for d in diffs)
        rows.append(row)
    return rows


def heritage_summary(heritage):
    groups = defaultdict(list)
    for record in heritage:
        groups[record["village"], record["stratum"], record["heritage_id"]].append(record)
    return [
        {
            "village": v,
            "stratum": s,
            "heritage_id": h,
            "pairs": len(group),
            **{
                f"{arm}_{metric}": value
                for arm in ("wide", "compact")
                for metric, value in (
                    ("ignited_cases", sum(c[f"{arm}_first_ignition_s"] is not None for c in group)),
                    ("maximum_rise_K", max(c[f"{arm}_peak_rise_K"] for c in group)),
                    (
                        "maximum_absorbed_radiation_J",
                        max(c[f"{arm}_absorbed_radiation_J"] for c in group),
                    ),
                )
            },
        }
        for (v, s, h), group in groups.items()
    ]


def arrow(row, field, decimals=2):
    return " → ".join(f"{row[f'{arm}_{field}']:,.{decimals}f}" for arm in ("wide", "compact"))


def render_report(reg, summary, cores):
    total = sum(r["pairs"] for r in summary)
    n_wide = sum(r["wide_first_ignition_cases"] for r in summary)
    n_compact = sum(r["compact_first_ignition_cases"] for r in summary)
    lines = [
        "# 侗寨精简防火线的留出火情终态验证",
        "",
        f"共完成 {total:,} 组同源配对。多点宽围护中有 {n_wide:,} 场引燃指定遗产，"
        f"固定精简布局中有 {n_compact:,} 场。所有结果均已核验至熄灭。",
        "",
        "下表的箭头均由多点宽围护指向固定精简布局。每组共用同一 25 m² 起火斑块、"
        "初始能量、网格与风况。每村近源覆盖原抽样框全部屋面，远源使用原分层随机样本。"
        "每处屋面采用相同的九种风况，包括静风及东、南、西、北各 2、4 m/s 的稳态风。"
        "屋面局部细化为 1 m，地表沿用 5 m 网格，采用 15 m 辐射作用半径，飞火关闭。"
        "宽围护复用已归档的同源终态，并逐场核验输入与证书。风况等权，分析范围为原模型域。",
        "",
        "## 施工清理面积",
        "",
        "| 村落 | 清理合计 m² | 清理屋面 m² | 清理地表 m² | 节省合计 m² | 节省比例 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for v, setup in reg["villages"].items():
        wide, compact = setup["wide_reference"], setup["plans"][0]
        wide_total = wide["construction_m2"]
        saved = wide_total - compact["construction_m2"]
        areas = " | ".join(
            f"{wide[key]:,.2f} → {compact[key]:,.2f}"
            for key in ("construction_m2", "roof_cleared_m2", "ground_cleared_m2")
        )
        lines.append(f"| {VILLAGES[v]} | {areas} | {saved:,.2f} | {saved / wide_total:.2%} |")
    lines += [
        "",
        "## 遗产引燃与村落损失",
        "",
        "| 村落与层 | 起火屋面 | 配对数 | 有遗产引燃场数 | 民居平均烧尽 m² | "
        "民居最坏烧尽 m² | 民居清除加烧尽均值 m² | 地表平均烧尽 m² | 触及原域边界场数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {VILLAGES[r['village']]}{STRATA[r['stratum']]} | {r['roofs']}/{r['pool']} | "
            f"{r['pairs']} | {arrow(r, 'first_ignition_cases', 0)} | "
            f"{arrow(r, 'mean_ordinary_burned_m2')} | {arrow(r, 'max_ordinary_burned_m2')} | "
            f"{arrow(r, 'mean_ordinary_cleared_or_burned_m2')} | "
            f"{arrow(r, 'mean_ground_burned_m2')} | {arrow(r, 'physical_boundary_cases', 0)} |"
        )
    late = sum(r["compact_first_ignition_cases_after_hour"] for r in summary)
    lines += [
        "",
        f"精简布局中有 {late:,} 场在一小时后才首次引燃指定遗产。",
        "",
        "民居火损使用普通屋面物理面积。施工清除和残余屋面烧尽发生在互不重叠的斑块，"
        "两项相加即失去的普通屋面面积。地表施工用整格面积，地表火损用实际燃料覆盖面积。"
        "屋面标注计数表示有燃尽斑块归属的原始标签。触边场次的域外蔓延范围未知。",
        "分层表内的配对差按起火屋面聚类，保留每个屋面下九种风的全部结果。"
        "论文图中误差棒为一个按起火屋面计算的抽样标准误，近源层为抽样框普查。",
        "",
        "## 逐处遗产",
        "",
        "| 村落与层 | 遗产标注 | 引燃场数 | 最大表面温升 K | 最大模型吸收辐射 MJ |",
        "|---|---|---:|---:|---:|",
    ]
    for r in cores:
        rad = " → ".join(
            f"{r[f'{arm}_maximum_absorbed_radiation_J'] / 1e6:,.2f}" for arm in ("wide", "compact")
        )
        lines.append(
            f"| {VILLAGES[r['village']]}{STRATA[r['stratum']]} | {r['heritage_id']} | "
            f"{arrow(r, 'ignited_cases', 0)} | {arrow(r, 'maximum_rise_K')} | {rad} |"
        )
    lines += [
        "",
        "温升及辐射量用于比较模型热暴露。拟判断材料损伤或现场安全间距，需要相应实测依据。",
        "",
        "[逐场配对表](paired-cases.csv) · [分层统计](by-stratum.csv) · "
        "[逐处遗产逐场表](heritage-by-case.csv) · [逐处遗产汇总](by-heritage.csv) · "
        "[核验证据](evidence.json) · [冻结设计](registration.json) · "
        "[论文图](geometry-comparison.pdf)",
        "",
    ]
    return "\n".join(lines)


def replay_bindings(root, pairs):
    checked = {
        p[f"{arm}_case_id"]: (p[f"{arm}_result_sha256"], p[f"{arm}_certificate_sha256"])
        for arm in ("wide", "compact")
        for p in pairs
    }
    checks = {}
    for name in (
        "verification.json",
        "paired-late-ignition-reference-check.json",
        "paired-late-ignition-reference-check-batuan.json",
    ):
        path = root / name
        if not path.exists():
            continue
        proof = read_json(path)
        if proof.pop("verification_sha256") != record_hash(proof) or proof[
            "registration_sha256"
        ] != digest(root / "registration.json"):
            raise ValueError(f"Altered replay evidence: {name}")
        for entry in proof["cases"]:
            evidence = read_json(root / entry["receipt_path"]) if "receipt_path" in entry else entry
            if "receipt_path" in entry and (
                evidence.pop("verification_sha256") != entry["verification_sha256"]
                or record_hash(evidence) != entry["verification_sha256"]
            ):
                raise ValueError(f"Altered replay receipt: {entry['receipt_path']}")
            if checked[entry["case_id"]] != (
                evidence["production_result_sha256"],
                evidence["production_certificate_sha256"],
            ):
                raise ValueError(f"Replay is disconnected from production: {entry['case_id']}")
        checks[name] = {"sha256": digest(path), "cases": len(proof["cases"])}
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEST)
    root = parser.parse_args().output.resolve()
    reg = verify_registration(root)
    pairs, heritage = matched_cases(root, reg)
    by_stratum = summarize(reg, pairs)
    by_heritage = heritage_summary(heritage)
    tables = {
        "paired-cases.csv": pairs,
        "heritage-by-case.csv": heritage,
        "by-stratum.csv": by_stratum,
        "by-heritage.csv": by_heritage,
    }
    for name, rows in tables.items():
        atomic_csv(root / name, rows)
    atomic_text(root / "report.md", render_report(reg, by_stratum, by_heritage))
    evidence = {
        "registration_sha256": digest(root / "registration.json"),
        "wide_archive_registration_sha256": reg["wide_registration"]["sha256"],
        "new_compact_terminal_cases": len(pairs),
        "paired_old_wide_certified_terminal_cases": len(pairs),
        "shared_ignition_roofs": len({(p["village"], p["fire_id"]) for p in pairs}),
        "all_matched_footprints_energy_and_winds": True,
        "all_energy_and_certified_extinction_checks_passed": True,
        "reference_ordinary_roof_area_method": "Wide-arm burned roof area with zero core ignitions",
        "independent_numerical_replays": replay_bindings(root, pairs),
        "outputs_sha256": {name: digest(root / name) for name in [*tables, "report.md"]},
        "summarizer_sha256": digest(Path(__file__)),
    }
    evidence["evidence_sha256"] = record_hash(evidence)
    atomic_json(root / "evidence.json", evidence)
    print(
        f"Audited {len(pairs)} matched terminal pairs across {len(by_stratum)} strata", flush=True
    )


if __name__ == "__main__":
    main()
