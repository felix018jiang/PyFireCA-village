"""Pair the registered 60-minute heritage trials with their extinction outcomes."""
# ruff: noqa: RUF001

import argparse
import json
from collections import Counter, defaultdict
from math import isclose, isfinite
from pathlib import Path
from statistics import fmean

from render_heritage_wind_maps import PLANS, VILLAGES
from summarize_heritage_wind_strategies import KEY, ROOT, load_results, write_csv

from pyfireca.planning.heritage import content_hash
from pyfireca.village.extinction_v3 import extinction_certificate_sha256

DEFAULT_PRIOR = ROOT / "output/heritage-wind-20261112"
DEFAULT_FINAL = ROOT / "output/heritage-wind-final-20261112"


def check_extinction(before, final):
    case_id = before["case_id"]
    if any(
        before[key] != final[key]
        for key in (*KEY, "case_id", "kind", "registration_sha256", "footprint_sha256")
    ):
        raise ValueError(f"{case_id}: case identity or frozen inputs changed")
    if any(
        not isclose(before[key], final[key], rel_tol=1e-9, abs_tol=1e-6)
        for key in ("source_area_m2", "source_energy_J")
    ):
        raise ValueError(f"{case_id}: ignition fuel or footprint area changed")
    cert = final["extinction_certificate_sha256"]
    if (
        final["extinction_closed"] is not True
        or not isinstance(cert, str)
        or len(cert) != 64
        or any(c not in "0123456789abcdef" for c in cert)
        or final["active_m2"] != 0
        or final["boundary"]["closed"] is not True
    ):
        raise ValueError(f"{case_id}: missing terminal extinction or boundary closure")
    error, tolerance = final["energy_closure_error_J"], final["energy_closure_tolerance_J"]
    if not (isfinite(error) and isfinite(tolerance) and 0 <= error <= tolerance):
        raise ValueError(f"{case_id}: invalid final energy balance")
    elapsed = final["elapsed_simulated_s"]
    if not isfinite(elapsed) or elapsed < before["elapsed_simulated_s"]:
        raise ValueError(f"{case_id}: final observation precedes the original one")
    if before["active_m2"] > 0 and elapsed <= before["horizon_s"]:
        raise ValueError(f"{case_id}: no post-horizon follow-up")
    for material in ("roof", "ground"):
        burned = final[f"{material}_burned_out_m2"]
        if (
            not isfinite(burned)
            or not isclose(burned, final[f"{material}_ignited_m2"], rel_tol=1e-10, abs_tol=1e-6)
            or burned + 1e-5 < before[f"{material}_ignited_m2"]
        ):
            raise ValueError(f"{case_id}: inconsistent final {material} area")
    cores = json.loads(before["core_outcomes"])
    if cores.keys() != final["core_outcomes"].keys():
        raise ValueError(f"{case_id}: heritage set changed")
    for target, obs in cores.items():
        t0 = obs["first_ignition_s"]
        t1 = final["core_outcomes"][target]["first_ignition_s"]
        if t1 is not None and not (isfinite(t1) and 0 <= t1 <= elapsed):
            raise ValueError(f"{case_id}: invalid final first ignition time")
        if t0 is not None and (t1 is None or not isclose(t0, t1, abs_tol=1e-6)):
            raise ValueError(f"{case_id}: earlier heritage ignition changed")
        if t0 is None and t1 is not None and t1 <= before["elapsed_simulated_s"]:
            raise ValueError(f"{case_id}: inconsistent first-hour heritage ignition")
    if final["core_ignited_count"] != sum(
        obs["first_ignition_s"] is not None for obs in final["core_outcomes"].values()
    ):
        raise ValueError(f"{case_id}: inconsistent heritage ignition count")


def pair_cases(prior, folder, reg):
    original = {
        row["case_id"]: row
        for row in load_results(prior, reg).values()
        if row["radius_m"] == reg["primary"]["radius_m"]
    }
    paths = {p.stem: p for p in (folder / "cases").glob("*.json")}
    active = {k for k, r in original.items() if r["active_m2"] > 0}
    manifest = json.loads((folder / "registration.json").read_bytes())
    if (
        set(manifest["continued"]) != active
        or set(manifest["already_extinct"]) != original.keys() - active
        or {r["registration_sha256"] for r in original.values()}
        != {manifest["original_registration_sha256"]}
        or {r["solver_sha256"] for r in original.values()} != {manifest["original_solver_sha256"]}
    ):
        raise ValueError("Continuation registration is inconsistent with the original records")
    if paths.keys() - original.keys():
        raise ValueError("Final case directory contains trials outside the primary registration")
    if missing := active - paths.keys():
        raise ValueError(f"Awaiting {len(missing)} of {len(active)} extinction trials")
    rows = []
    for key, before in sorted(original.items()):
        path = paths.get(key, prior / "cases" / f"{key}.json")
        final = json.loads(path.read_bytes())
        result_hash = final.pop("result_sha256")
        if result_hash != content_hash(final):
            raise ValueError(f"Result checksum mismatch in {path}")
        if key not in active and result_hash != before["result_sha256"]:
            raise ValueError(f"{key}: expected the original already-extinguished result")
        check_extinction(before, final)
        if key in active:
            if (
                final["original_result_sha256"] != before["result_sha256"]
                or final["replay_60min_verified"] is not True
                or final["native_solver_sha256"] != before["solver_sha256"]
                or final["solver_sha256"] != manifest["terminal_solver_sha256"]
            ):
                raise ValueError(f"{key}: missing verification against the original first hour")
            cert = json.loads((folder / "certificates" / f"{key}.json").read_bytes())
            expected = final["extinction_certificate_sha256"]
            if (
                cert["certificate_sha256"] != expected
                or extinction_certificate_sha256(cert) != expected
            ):
                raise ValueError(f"{key}: inconsistent full extinction certificate")
            if (
                not isclose(cert["actual_stop_time_s"], final["elapsed_simulated_s"], abs_tol=1e-7)
                or cert["future_mean_hrr_W"] != 0
                or not isclose(
                    cert["cumulative_burned_out_covered_area_m2"],
                    final["roof_burned_out_m2"] + final["ground_burned_out_m2"],
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
            ):
                raise ValueError(f"{key}: terminal state and its extinction certificate disagree")
        row = {
            name: before[name]
            for name in (
                *KEY,
                "case_id",
                "kind",
                "wind_speed_m_s",
                "wind_from_deg",
                "roof_cleared_m2",
                "ground_cleared_m2",
                "construction_m2",
                "footprint_sha256",
                "registration_sha256",
            )
        }
        row.update(
            extended=key in active,
            original_result_sha256=before["result_sha256"],
            final_result_sha256=result_hash,
            final_solver_sha256=final["solver_sha256"],
            extinction_certificate_sha256=final["extinction_certificate_sha256"],
            extinction_observed_s=final["elapsed_simulated_s"],
            original_physical_boundary_reached=final["boundary"][
                "original_physical_boundary_reached"
            ],
            core_count_at_60min=before["core_ignited_count"],
            final_core_count=final["core_ignited_count"],
            final_core_first_ignition_s=json.dumps(
                {k: x["first_ignition_s"] for k, x in final["core_outcomes"].items()},
                sort_keys=True,
            ),
            active_at_60min_m2=before["active_m2"],
            final_active_m2=final["active_m2"],
        )
        for material in ("roof", "ground"):
            row[f"{material}_at_60min_m2"] = before[f"{material}_burned_out_m2"]
            row[f"final_{material}_m2"] = final[f"{material}_burned_out_m2"]
        row["final_total_m2"] = row["final_roof_m2"] + row["final_ground_m2"]
        row["total_at_60min_m2"] = before["total_burned_out_m2"]
        rows.append(row)
    return rows


def summarize(rows, reg):
    groups = defaultdict(list)
    for row in rows:
        groups[row["village"], row["plan_id"]].append(row)
    summary = []
    for village, setup in reg["villages"].items():
        expected_fires = Counter(f["kind"] for f in setup["fires"])
        winds = len(reg["primary"]["wind_ids"])
        for plan in setup["plans"]:
            subset = groups[village, plan["plan_id"]]
            counts = Counter(r["kind"] for r in subset)
            if counts != {kind: n * winds for kind, n in expected_fires.items()}:
                raise ValueError("Incomplete paired layout group")
            extended = sum(r["extended"] for r in subset)
            item = {
                "village": village,
                "plan_id": plan["plan_id"],
                "width_m": plan["width_m"],
                "cases": len(subset),
                "early_extinguished": len(subset) - extended,
                "extended": extended,
                "roof_cleared_m2": plan["roof_cleared_m2"],
                "ground_cleared_m2": plan["ground_cleared_m2"],
                "construction_m2": plan["construction_m2"],
            }
            for mat in ("roof", "ground", "total"):
                end = [r[f"final_{mat}_m2"] for r in subset]
                item[f"mean_final_{mat}_m2"] = fmean(end)
                item[f"max_final_{mat}_m2"] = max(end)
                item[f"mean_added_{mat}_m2"] = fmean(
                    v - r[f"{mat}_at_60min_m2"] for v, r in zip(end, subset, strict=True)
                )
            for kind, n in counts.items():
                local = [r for r in subset if r["kind"] == kind]
                item[f"{kind}_cases"] = n
                item[f"{kind}_ignited_at_60min"] = sum(r["core_count_at_60min"] > 0 for r in local)
                item[f"{kind}_final_ignited"] = sum(r["final_core_count"] > 0 for r in local)
            item["newly_affected_cases"] = sum(
                r["core_count_at_60min"] == 0 < r["final_core_count"] for r in subset
            )
            item["additional_heritage_ignitions"] = sum(
                r["final_core_count"] - r["core_count_at_60min"] for r in subset
            )
            item["original_boundary_cases"] = sum(
                r["original_physical_boundary_reached"] for r in subset
            )
            item["latest_extinction_observed_s"] = max(r["extinction_observed_s"] for r in subset)
            summary.append(item)
    return summary


def report(summary):
    extended = sum(r["extended"] for r in summary)
    early = sum(r["early_extinguished"] for r in summary)
    text = [
        "# 村落防火线策略从起火到完全熄灭的模拟结果",
        "",
        f"两村共 {early + extended} 场主试验均已确认熄灭。其中 {early} 场在首次观测时已经熄灭，"
        f"{extended} 场在 60 分钟时仍有燃烧，本次将它们追踪至熄灭。各场最终活动燃烧面积均为零。",
        "",
        "每布局在同村比较相同的四个固定火点和四个随机火点。"
        "每处火点各用九种静态风况，包括静风及四个来向各 2、4 m/s。"
        "辐射作用半径为 15 m。五种布局使用相同材料、火点和风况，面积按去重后的物理斑块统计。"
        "遗产围护沿遗产屋面外缘布带，多点围护还包含四个固定起火屋面外缘。"
        "窄带与宽带分别为 2.5 m 和 15.01 m。",
        "",
    ]
    for v in dict.fromkeys(r["village"] for r in summary):
        local = [r for r in summary if r["village"] == v]
        text += [
            f"## {VILLAGES[v]}村",
            "",
            "清理面积是逐布局的一次施工面积。最终燃尽均值取该布局全部 72 场，"
            "已经提前熄灭的场次也计入分母。"
            "最坏总面积先逐场将屋面与地表燃尽相加，再取最大值。面积单位均为 m²。",
            "",
            "| 布局 | 清理屋面 | 清理地表 | 合计清理 | 平均最终屋面燃尽 | "
            "平均最终地表燃尽 | 平均最终总燃尽 | 最坏单场总燃尽 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in local:
            values = [
                r[key]
                for key in (
                    "roof_cleared_m2",
                    "ground_cleared_m2",
                    "construction_m2",
                    "mean_final_roof_m2",
                    "mean_final_ground_m2",
                    "mean_final_total_m2",
                    "max_final_total_m2",
                )
            ]
            text.append(
                f"| {PLANS[r['plan_id']]} | " + " | ".join(f"{n:,.2f}" for n in values) + " |"
            )
        text += [
            "",
            "固定、随机各 36 场。遗产点燃按至少一处遗产起燃的场数计。"
            "箭头左、右分别为第 60 分钟和最终熄灭时的累计场数。"
            "平均追加面积按该布局全部 72 场计，提前熄灭的场次追加量为零。",
            "",
            "| 布局 | 原已熄灭 / 延长 | 平均追加屋面 m² | 平均追加地表 m² | "
            "固定遗产点燃 60 分钟 → 最终 | 随机遗产点燃 60 分钟 → 最终 | "
            "延长后首次出现遗产点燃的场数 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for r in local:
            text.append(
                f"| {PLANS[r['plan_id']]} | {r['early_extinguished']} / {r['extended']} | "
                f"{r['mean_added_roof_m2']:,.2f} | {r['mean_added_ground_m2']:,.2f} | "
                f"{r['fixed_ignited_at_60min']} → {r['fixed_final_ignited']} | "
                f"{r['random_ignited_at_60min']} → {r['random_final_ignited']} | "
                f"{r['newly_affected_cases']} |"
            )
        edge = sum(r["original_boundary_cases"] for r in local)
        t = max(r["latest_extinction_observed_s"] for r in local)
        control = next(r for r in local if r["plan_id"] == "baseline")
        bands = [r for r in local if r["plan_id"] != "baseline"]
        low = min(bands, key=lambda r: r["mean_final_total_m2"])
        reduction = control["mean_final_total_m2"] - low["mean_final_total_m2"]
        safe = [r for r in bands if r["fixed_final_ignited"] + r["random_final_ignited"] == 0]
        if safe:
            smallest = min(safe, key=lambda r: r["construction_m2"])
            comparison = (
                f"有 {len(safe)} 套防火线在本村全部 72 场中始终未出现遗产点燃。"
                f"其中，{PLANS[smallest['plan_id']]}的清理面积最小，"
                f"共 {smallest['construction_m2']:,.2f} m²。"
            )
        else:
            failure_counts = {
                r["plan_id"]: r["fixed_final_ignited"] + r["random_final_ignited"] for r in bands
            }
            least = min(failure_counts.values())
            names = "、".join(PLANS[k] for k, n in failure_counts.items() if n == least)
            comparison = f"四套防火线在本村均出现遗产点燃。{names}的点燃场数最少，为 {least}/72。"
        text += [
            "",
            f"四套防火线中，{PLANS[low['plan_id']]}的平均最终总燃尽面积最低，"
            f"为 {low['mean_final_total_m2']:,.2f} m²。"
            f"与无清理基线的均值之差为 {reduction:,.2f} m²（基线减该布局）。" + comparison,
            "",
            f"最晚一次记录到完全熄灭为起火后 {t / 3600:.2f} 小时。"
            f"有 {edge} 场的火势触及原始模拟域边界。燃尽面积限于原始研究域。",
            "",
        ]
    text += [
        "施工清理材料与火灾燃尽材料分开统计。屋面燃尽包含普通及遗产屋面。可燃地表包括草地、农田、林木与灌丛。"
        "遗产点燃次数反映本批相同火点与风况的等权比较。",
        "",
        "每个延长场次都用其原火点和风况重建第 60 分钟状态并与原记录核对，"
        "随后计算至全部燃烧停止且求解器确认没有后续点燃。"
        "另选三场跨村正式试验，以及一场火势触及原始域边界的岜团试验，用原生求解器独立核验终态。",
        "",
        "[逐场终态与 60 分钟对照](cases.csv) · [分村分布局统计](by-layout.csv) · "
        "[三场独立终态核验](native-checks/verification.json) · "
        "[岜团广域终态核验](native-checks/wide-area-verification.json)",
        "",
        "[延长试验登记](registration.json) · "
        "[续算脚本](../../scripts/run_heritage_wind_extinction.py) · "
        "[汇总脚本](../../scripts/summarize_heritage_wind_extinction.py)",
        "",
    ]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, default=DEFAULT_PRIOR)
    parser.add_argument("--output", type=Path, default=DEFAULT_FINAL)
    args = parser.parse_args()
    reg = json.loads((args.prior / "registration.json").read_bytes())
    rows = pair_cases(args.prior, args.output, reg)
    summary = summarize(rows, reg)
    write_csv(args.output / "cases.csv", rows)
    write_csv(args.output / "by-layout.csv", summary)
    (args.output / "report.md").write_text(report(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
