"""Summarize complete, paired outcomes on the predeclared heritage wind experiment."""
# ruff: noqa: RUF001

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from itertools import product
from math import isclose, isfinite
from pathlib import Path
from statistics import fmean, median

from render_heritage_wind_maps import PLANS, VILLAGES, render_maps

from pyfireca.planning.heritage import content_hash

ROOT = Path(__file__).resolve().parents[1]
KEY = ("village", "plan_id", "fire_id", "wind_id", "radius_m")
OUTCOMES = (
    "core_ignited_count",
    "core_peak_rise_K",
    "core_absorbed_radiation_J",
    "ordinary_roof_ignited_count",
    "ordinary_roof_burned_out_count",
    "roof_ignited_m2",
    "roof_burned_out_m2",
    "ground_ignited_m2",
    "ground_burned_out_m2",
    "total_ignited_m2",
    "total_burned_out_m2",
    "active_m2",
    "roof_cleared_plus_burned_out_m2",
)
COSTS = ("roof_cleared_m2", "ground_cleared_m2", "construction_m2", "minimum_retained_roof_gap_m")
CONTROL_PAIRS = (
    ("baseline", "heritage-narrow"),
    ("baseline", "heritage-wide"),
    ("baseline", "multipoint-narrow"),
    ("baseline", "multipoint-wide"),
    ("heritage-narrow", "heritage-wide"),
    ("multipoint-narrow", "multipoint-wide"),
    ("heritage-narrow", "multipoint-narrow"),
    ("heritage-wide", "multipoint-wide"),
)


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def expected_keys(reg):
    required = set()
    extra = reg["sensitivity"]
    for village, setup in reg["villages"].items():
        required.update(
            product(
                [village],
                [p["plan_id"] for p in setup["plans"]],
                [f["fire_id"] for f in setup["fires"]],
                [w["wind_id"] for w in reg["winds"]],
                [reg["primary"]["radius_m"]],
            )
        )
        required.update(
            product(
                [village],
                extra["plan_ids"],
                extra["fire_ids"],
                extra["wind_ids"],
                [extra["radius_m"]],
            )
        )
    return required


def load_results(folder, reg):
    plans = {(v, p["plan_id"]): p for v, s in reg["villages"].items() for p in s["plans"]}
    fires = {(v, f["fire_id"]): f for v, s in reg["villages"].items() for f in s["fires"]}
    expected = expected_keys(reg)
    winds = {w["wind_id"]: w for w in reg["winds"]}
    registration_hash = hashlib.sha256((folder / "registration.json").read_bytes()).hexdigest()
    cases, sources, solvers = {}, {}, set()
    for path in sorted((folder / "cases").glob("*.json")):
        raw = json.loads(path.read_text())
        signature = raw.pop("result_sha256")
        if (
            signature != content_hash(raw)
            or raw["registration_sha256"] != registration_hash
            or raw["case_id"] != path.stem
        ):
            raise ValueError(f"Observation bytes or registered inputs changed: {path}")
        solvers.add(raw["solver_sha256"])
        key = tuple(raw[k] for k in KEY)
        if key not in expected or key in cases:
            raise ValueError(f"Unregistered or duplicate observation {key}: {path}")
        village, plan, fire, _, radius = key
        group = reg["primary"] if radius == reg["primary"]["radius_m"] else reg["sensitivity"]
        if not isclose(raw["horizon_s"], group.get("horizon_s", reg["primary"]["horizon_s"])):
            raise ValueError(f"Observation horizon does not match registration: {key}")
        if not raw["extinction_closed"] and not isclose(
            raw["elapsed_simulated_s"], raw["horizon_s"]
        ):
            raise ValueError(f"Incomplete time coverage: {key}")
        if not raw["boundary"]["closed"]:
            raise ValueError(f"Unresolved spatial boundary: {key}")
        origin = fires[village, fire]
        if raw["kind"] != origin["kind"] or raw["footprint_sha256"] != origin["footprint_sha256"]:
            raise ValueError(f"Source family does not match registration: {key}")
        if raw["energy_closure_error_J"] > raw["energy_closure_tolerance_J"]:
            raise ValueError(f"Energy balance does not close: {key}")
        source = (raw["source_area_m2"], raw["source_energy_J"])
        reference = sources.setdefault((village, fire), source)
        if any(
            not isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
            for a, b in zip(source, reference, strict=True)
        ):
            raise ValueError(f"Source area or energy differs within paired fire: {key}")
        row = {k: raw[k] for k in KEY}
        row.update(
            case_id=raw["case_id"],
            kind=raw["kind"],
            source_id=origin["source_id"],
            source_stratum=origin.get("stratum", "fixed"),
            wind_speed_m_s=winds[raw["wind_id"]]["wind_speed_m_s"],
            wind_from_deg=winds[raw["wind_id"]]["wind_from_deg"],
            horizon_s=raw["horizon_s"],
            elapsed_simulated_s=raw["elapsed_simulated_s"],
            extinction_closed=raw["extinction_closed"],
        )
        row.update({k: plans[village, plan][k] for k in COSTS})
        raw["total_ignited_m2"] = raw["roof_ignited_m2"] + raw["ground_ignited_m2"]
        raw["total_burned_out_m2"] = raw["roof_burned_out_m2"] + raw["ground_burned_out_m2"]
        raw["roof_cleared_plus_burned_out_m2"] = row["roof_cleared_m2"] + raw["roof_burned_out_m2"]
        for name in (*OUTCOMES, "source_area_m2", "source_energy_J"):
            if not isfinite(raw[name]) or raw[name] < -1e-8:
                raise ValueError(f"Invalid {name}: {key}")
            row[name] = raw[name]
        if not isclose(
            row["total_ignited_m2"] - row["total_burned_out_m2"], row["active_m2"], abs_tol=1e-5
        ):
            raise ValueError(f"Ignited vs burned and active area do not reconcile: {key}")
        if row["extinction_closed"] and row["active_m2"] > 1e-8:
            raise ValueError(f"Extinction reported with active burning: {key}")
        row.update(
            registration_sha256=registration_hash,
            result_sha256=signature,
            footprint_sha256=raw["footprint_sha256"],
            solver_sha256=raw["solver_sha256"],
            energy_closure_error_J=raw["energy_closure_error_J"],
            energy_closure_tolerance_J=raw["energy_closure_tolerance_J"],
            extinction_certificate_sha256=raw["extinction_certificate_sha256"],
            boundary_closed=raw["boundary"]["closed"],
            original_physical_boundary_reached=raw["boundary"][
                "original_physical_boundary_reached"
            ],
            core_outcomes=json.dumps(raw["core_outcomes"], ensure_ascii=False, sort_keys=True),
        )
        cases[key] = row
    missing = expected - cases.keys()
    if missing:
        raise ValueError(
            f"{len(missing)} of {len(expected)} registered observations missing; "
            f"first: {min(missing)}"
        )
    if len(solvers) != 1:
        raise ValueError("Paired observations use different solver versions")
    return cases


def grouped(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    return groups


def summaries(cases, source_group="kind"):
    dimensions = ("village", "radius_m", "plan_id", source_group, "wind_id")
    output = []
    for key, rows in sorted(grouped(cases.values(), dimensions).items()):
        record = dict(zip(dimensions, key, strict=True))
        record.update(
            n_fires=len(rows),
            core_failure_fires=sum(r["core_ignited_count"] > 0 for r in rows),
            active_fires=sum(r["active_m2"] > 0 for r in rows),
            extinction_closed_fires=sum(r["extinction_closed"] for r in rows),
            original_boundary_fires=sum(r["original_physical_boundary_reached"] for r in rows),
        )
        record.update({k: rows[0][k] for k in COSTS})
        for name in OUTCOMES:
            values = [r[name] for r in rows]
            record[f"{name}_mean"] = fmean(values)
            record[f"{name}_max"] = max(values)
        output.append(record)
    return output


def paired_rows(cases, reg):
    primary, secondary = reg["primary"]["radius_m"], reg["sensitivity"]["radius_m"]
    differences, radial = [], []
    for (v, p, f, w, r), treatment in sorted(cases.items()):
        if r == secondary:
            first = cases[v, p, f, w, primary]
            record = {k: treatment[k] for k in ("village", "plan_id", "fire_id", "kind", "wind_id")}
            record.update(reference_radius_m=primary, trial_radius_m=secondary)
            for outcome in OUTCOMES:
                record[f"delta_{outcome}"] = treatment[outcome] - first[outcome]
            radial.append(record)
        for control, trial in CONTROL_PAIRS:
            if p != trial:
                continue
            first = cases[v, control, f, w, r]
            record = {
                k: treatment[k] for k in ("village", "radius_m", "fire_id", "kind", "wind_id")
            }
            record.update(
                control=control,
                trial=trial,
                extra_construction_m2=treatment["construction_m2"] - first["construction_m2"],
                avoided_any_core_ignition=int(first["core_ignited_count"] > 0)
                - int(treatment["core_ignited_count"] > 0),
            )
            for outcome in OUTCOMES:
                record[f"avoided_{outcome}"] = first[outcome] - treatment[outcome]
            differences.append(record)
    return differences, radial


def wind_effects(cases, reg):
    # Wind effects are compared within one source and one fixed construction layout.
    primary = reg["primary"]["radius_m"]
    rows = []
    for (v, p, f, w, r), windy in sorted(cases.items()):
        if r != primary or w == "calm":
            continue
        calm = cases[v, p, f, "calm", r]
        row = {
            k: windy[k] for k in ("village", "plan_id", "fire_id", "kind", "wind_id", "radius_m")
        }
        row.update({f"delta_{name}": windy[name] - calm[name] for name in OUTCOMES})
        rows.append(row)
    return rows


def heritage_rows(cases):
    rows = []
    for key in sorted(cases):
        case = cases[key]
        for target, observations in json.loads(case["core_outcomes"]).items():
            row = {name: case[name] for name in (*KEY, "kind", "source_stratum")}
            row.update(heritage_id=target, **observations)
            rows.append(row)
    return rows


def failure_count(rows, kind):
    subset = [r for r in rows if r["kind"] == kind]
    return f"{sum(r['core_ignited_count'] > 0 for r in subset)}/{len(subset)}"


def clearance_and_burn_tables(plans, records):
    by_plan = grouped(records, ["plan_id"])
    construction = [
        "### 防火线的施工清理面积",
        "",
        "清理面积是一次布置防火线所移除的可燃材料面积。"
        "各布局均按同一规则选择普通屋面和完整的 5 m 可燃地表格。"
        "清理标注数按施工涉及的原普通建筑标注计，清理屋面面积按实际移除的物理面积去重。",
        "",
        "| 布局 | 名义带宽 m | 遗产到最近保留普通屋面净距 m | 清理普通屋面标注数 | "
        "清理屋面 m² | 清理地表 m² | 合计清理 m² |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    burn = [
        "### 截至 60 分钟的燃尽与仍燃面积",
        "",
        "各均值取同村同布局的全部 72 场，包括提前熄灭的场次。"
        "燃尽表示斑块燃料耗尽，仍燃表示到观测终点仍有燃料在燃烧。"
        "每场先将屋面和地表燃尽面积相加，再取 72 场中的最大值。",
        "",
        "| 布局 | 平均屋面燃尽 m² | 平均地表燃尽 m² | 平均合计燃尽 m² | "
        "单场最大合计燃尽 m² | 平均仍燃 m² | 仍燃场数 / 72 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    strata = [
        "### 固定及新随机火点的平均损失",
        "",
        "两组各 36 场。每个火点的九个风况等权。原屋面损失按同一场的施工清理屋面"
        "与实际燃尽屋面相加，两部分面积互不重叠。"
        "下表各项均为每场平均面积，单位为 m²。",
        "",
        "| 布局 | 固定·屋面燃尽 | 固定·地表燃尽 | 固定·原屋面损失 | "
        "随机·屋面燃尽 | 随机·地表燃尽 | 随机·原屋面损失 | 全部·原屋面损失 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for plan in plans:
        plan_id = plan["plan_id"]
        name = PLANS[plan_id]
        subset = by_plan[plan_id,]
        construction.append(
            f"| {name} | {plan['width_m']:g} | {plan['minimum_retained_roof_gap_m']:.2f} | "
            f"{len(plan['demolished_ids'])} | {plan['roof_cleared_m2']:,.2f} | "
            f"{plan['ground_cleared_m2']:,.2f} | {plan['construction_m2']:,.2f} |"
        )
        means = [
            fmean(r[key] for r in subset)
            for key in ("roof_burned_out_m2", "ground_burned_out_m2", "total_burned_out_m2")
        ]
        burn.append(
            f"| {name} | {means[0]:,.2f} | {means[1]:,.2f} | {means[2]:,.2f} | "
            f"{max(r['total_burned_out_m2'] for r in subset):,.2f} | "
            f"{fmean(r['active_m2'] for r in subset):,.2f} | "
            f"{sum(r['active_m2'] > 0 for r in subset)}/{len(subset)} |"
        )
        by_kind = grouped(subset, ["kind"])
        values = [
            fmean(r[key] for r in by_kind[kind,])
            for kind in ("fixed", "random")
            for key in (
                "roof_burned_out_m2",
                "ground_burned_out_m2",
                "roof_cleared_plus_burned_out_m2",
            )
        ]
        values.append(fmean(r["roof_cleared_plus_burned_out_m2"] for r in subset))
        strata.append(f"| {name} | " + " | ".join(f"{v:,.2f}" for v in values) + " |")
    return [*construction, "", *burn, "", *strata, ""]


def report(reg, cases, differences, radial, wind):
    primary = reg["primary"]["radius_m"]
    rows = [r for r in cases.values() if r["radius_m"] == primary]
    horizon = reg["primary"]["horizon_s"]
    text = [
        "# 风况下的村落防火线对照",
        "",
        f"主试验完成 {len(rows)} 场，另有 {len(radial)} 场扩大辐射范围的配对复算。"
        f"主试验逐场随访至 {horizon / 60:g} 分钟或模型确认熄灭。"
        "各布局使用相同实际起火斑块、源燃料和风况。恒定风通过火焰倾斜改变辐射传热。风速和倾斜系数为情景输入。",
        "",
        "每村四个历史固定火点曾参与多点围护的几何设计。另从各布局共同保留的普通屋面中抽取四个新火点，"
        "距遗产不超过 50 m 和超过 50 m 的两个分层各抽两栋。每处点燃约 25 m² 的同一屋面斑块。"
        "九个风况包括静风和东、南、西、北四个来向在 2 m/s 与 4 m/s 下的组合，倾斜系数均为 0.2 s/m。"
        "以下计数表示已测试条件中的表现，未按当地火灾频率或风频加权。",
        "",
        "遗产围护从全部遗产屋面轮廓的并集向外扩展，多点围护额外围住四个固定起火屋面。"
        "每种位置各设 2.5 m 和 15.01 m 宽度。所有布局保护相同的锚点屋面。"
        "本批直接从几何带生成行动，未叠加此前的预置清理清单。",
        "",
    ]
    for village in reg["villages"]:
        comparison = grouped([r for r in rows if r["village"] == village], ["plan_id"])
        control = fmean(r["total_burned_out_m2"] for r in comparison["baseline",])
        wide = fmean(r["total_burned_out_m2"] for r in comparison["multipoint-wide",])
        text += [
            f"在{VILLAGES[village]}，从无清理改为多点宽围护，60 分钟内的每场平均总燃尽面积由 "
            f"{control:,.2f} m² 降至 {wide:,.2f} m²，降幅为 {(control - wide) / control:.1%}。",
            "",
        ]
    text += ["![两村各布局的施工清理屋面与每场平均燃尽屋面](roof-area-outcomes.png)", ""]
    for village, setup in reg["villages"].items():
        records = [r for r in rows if r["village"] == village]
        text += [
            f"## {VILLAGES[village]}村",
            "",
            f"![防火线布局与起火样本]({village}-strategies.png)",
            "",
            *clearance_and_burn_tables(setup["plans"], records),
            "### 遗产点燃与普通建筑受火",
            "",
            "表中损失取各布局主试验的最大值，各指标的最大值可能来自不同火情。"
            "固定、新随机各用四个火点在九个风况下复算，分母各为 36 场。"
            "遗产受火栏按至少一处遗产点燃计数。多点布局在设计中明确围护了固定火点，新随机组检验布局对其他火点的表现。",
            "",
            "| 布局 | 固定火点遗产受火 | "
            "随机火点遗产受火 | 最高温升 K | 最多普通屋面标注受火 / 局部燃尽 | "
            "屋面燃尽 m² | 非屋面燃尽 m² | 终点仍活动 m² |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for plan in setup["plans"]:
            subset = [r for r in records if r["plan_id"] == plan["plan_id"]]

            worst = {k: max(r[k] for r in subset) for k in OUTCOMES}
            text.append(
                f"| {PLANS[plan['plan_id']]} | "
                f"{failure_count(subset, 'fixed')} | {failure_count(subset, 'random')} | "
                f"{worst['core_peak_rise_K']:.2f} | {worst['ordinary_roof_ignited_count']} / "
                f"{worst['ordinary_roof_burned_out_count']} | "
                f"{worst['roof_burned_out_m2']:,.1f} | {worst['ground_burned_out_m2']:,.1f} | "
                f"{worst['active_m2']:,.1f} |"
            )
        eligible = [
            p
            for p in setup["plans"]
            if all(r["core_ignited_count"] == 0 for r in records if r["plan_id"] == p["plan_id"])
        ]
        if eligible:
            cheapest = min(eligible, key=lambda p: p["construction_m2"])
            combined = {
                p["plan_id"]: max(
                    r["roof_cleared_plus_burned_out_m2"]
                    for r in records
                    if r["plan_id"] == p["plan_id"]
                )
                for p in eligible
            }
            best = min(combined.values())
            names = "、".join(
                PLANS[p] for p, v in combined.items() if isclose(v, best, abs_tol=1e-6)
            )
            text += [
                "",
                f"在本村 72 组主试验火点与风况组合中，{len(eligible)} 套布局均未出现遗产点燃。"
                f"其中清理面积最小的是{PLANS[cheapest['plan_id']]}，"
                f"共 {cheapest['construction_m2']:,.2f} m²。"
                f"在这些布局中，施工清理屋面与火灾燃尽屋面的合计面积取各自最坏值，"
                f"最低为 {best:,.2f} m²（{names}）。",
            ]
        else:
            failures = {
                p["plan_id"]: sum(
                    r["core_ignited_count"] > 0 for r in records if r["plan_id"] == p["plan_id"]
                )
                for p in setup["plans"]
            }
            least = min(failures.values())
            names = "、".join(PLANS[p] for p, count in failures.items() if count == least)
            text += [
                "",
                "本村五套布局均至少出现一次遗产点燃。"
                f"在相同的 72 组主试验条件中，遗产点燃场数最少的是{names}，出现 {least} 场。",
            ]
        wide = next(p for p in setup["plans"] if p["plan_id"] == "multipoint-wide")
        text += [
            "",
            f"多点宽围护的名义带宽为 {wide['width_m']:g} m，"
            f"遗产到最近保留普通屋面的实际净距为 {wide['minimum_retained_roof_gap_m']:.2f} m。"
            "带宽从选定锚点的组合外缘计算，遗产与其他保留锚点之间仍可相距更近。"
            "净距按两类实际屋面多边形外缘量取。",
        ]
        text += [
            "",
            "### 同一火点、同一风况的布局差值",
            "",
            "正的减损面积表示后一个布局烧得更少。两类火点分别计算配对屋面减损均值，"
            "地表减损中位数取全部 72 组配对，每个已登记火点与风况组合等权。",
            "",
            "| 布局变化 | 多清理 m² | 固定火点平均少烧屋面 m² | 随机火点平均少烧屋面 m² | "
            "非屋面燃尽减损中位数 m² | "
            "普通屋面受火标注数减少 / 相同 / 增加 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for control, trial in CONTROL_PAIRS[4:]:
            pairs = [
                r
                for r in differences
                if r["village"] == village
                and r["radius_m"] == primary
                and r["control"] == control
                and r["trial"] == trial
            ]
            n = [r["avoided_ordinary_roof_ignited_count"] for r in pairs]
            avoided = {
                kind: fmean(r["avoided_roof_burned_out_m2"] for r in pairs if r["kind"] == kind)
                for kind in ("fixed", "random")
            }
            text.append(
                f"| {PLANS[control]} → {PLANS[trial]} | {pairs[0]['extra_construction_m2']:,.1f} | "
                f"{avoided['fixed']:,.1f} | {avoided['random']:,.1f} | "
                f"{median(r['avoided_ground_burned_out_m2'] for r in pairs):,.1f} | "
                f"{sum(i > 0 for i in n)} / {sum(i == 0 for i in n)} / {sum(i < 0 for i in n)} |"
            )
        local_wind = [r for r in wind if r["village"] == village]
        switches = {
            kind: [
                r["avoided_any_core_ignition"]
                for r in differences
                if r["village"] == village
                and r["radius_m"] == primary
                and r["kind"] == kind
                and r["control"] == "heritage-narrow"
                and r["trial"] == "multipoint-narrow"
            ]
            for kind in ("fixed", "random")
        }
        text += [
            "",
            "同为 2.5 m 窄带，把围护从遗产周边扩展到四个旧火点后，"
            f"固定组有 {switches['fixed'].count(1)} 场由遗产受火转为全部未点燃，"
            f"有 {switches['fixed'].count(-1)} 场反向变化。"
            f"新随机组对应为 {switches['random'].count(1)} 场"
            f"和 {switches['random'].count(-1)} 场。",
        ]
        delta = [r["delta_core_ignited_count"] for r in local_wind]
        local_rad = [r for r in radial if r["village"] == village]
        active = sum(r["active_m2"] > 0 for r in records)
        edges = sum(r["original_physical_boundary_reached"] for r in records)
        wind_temp = max(abs(r["delta_core_peak_rise_K"]) for r in local_wind)
        radius_temp = max(abs(r["delta_core_peak_rise_K"]) for r in local_rad)
        radius_roof = max(abs(r["delta_roof_burned_out_m2"]) for r in local_rad)
        radius2 = reg["sensitivity"]["radius_m"]
        wide_sensitivity = []
        for identifier in ("heritage-wide", "multipoint-wide"):
            subset = [
                r
                for r in cases.values()
                if r["radius_m"] == radius2
                and r["village"] == village
                and r["plan_id"] == identifier
            ]
            count = sum(r["core_ignited_count"] > 0 for r in subset)
            wide_sensitivity.append(f"{PLANS[identifier]} {count}/{len(subset)}")
        text += [
            "",
            f"与同源、同布局的静风配对相比，{len(local_wind)} 个有风对照中，"
            f"{sum(d > 0 for d in delta)} 个有更多遗产受火，"
            f"{sum(d < 0 for d in delta)} 个有更少遗产受火。"
            f"核心温升的最大绝对差为 {wind_temp:,.2f} K。",
            "",
            f"半径敏感性复算取一个固定火点和一个随机火点，分别配静风、西风 4 m/s。"
            f"将辐射范围从 {primary:g} m 扩大到 {radius2:g} m 的 {len(local_rad)} 个配对中，"
            f"{sum(r['delta_core_ignited_count'] != 0 for r in local_rad)} 个配对的受火遗产数改变。"
            f"屋面燃尽面积最大绝对差为 {radius_roof:,.1f} m²，"
            f"遗产最高温升最大绝对差为 {radius_temp:,.2f} K。"
            f"扩大半径后，两套宽围护的遗产点燃场数分别为{'、'.join(wide_sensitivity)}。",
            "",
            f"主试验中有 {active}/{len(records)} 场在 {horizon / 60:g} 分钟终点仍有燃烧斑块，"
            f"有 {edges} 场的火势到达原始研究域边界。损失统计止于规定时点和原始研究域。",
            "",
            f"该村已记录的 {len(records)} 个主试验均通过裁剪窗口的外部传播与时间步核查。",
            "",
        ]
        directions = {"N": "北", "E": "东", "S": "南", "W": "西"}
        wind_ids = reg["primary"]["wind_ids"]
        labels = ["静风" if w == "calm" else directions[w[0]] + w[1:] for w in wind_ids]
        text += [
            "### 各风况的遗产点燃场数",
            "",
            "每格分母为相同的八处起火斑块。列名给出来向和风速（m/s）。",
            "",
            "| 布局 | " + " | ".join(labels) + " |",
            "|---|" + "---:|" * len(wind_ids),
        ]
        for p in setup["plans"]:
            grouped_winds = grouped(
                [r for r in records if r["plan_id"] == p["plan_id"]], ["wind_id"]
            )
            counts = [
                str(sum(r["core_ignited_count"] > 0 for r in grouped_winds[w,])) for w in wind_ids
            ]
            text += [f"| {PLANS[p['plan_id']]} | " + " | ".join(counts) + " |"]
        text.append("")
    text += [
        "## 数据口径与逐场记录",
        "",
        "施工成本按去重后的实际清理屋面和完整 5 m 非屋面可燃地表格面积之和计算。"
        "地表施工包含草地/农田与林木/灌丛。"
        "受火面积累计曾点燃的物理斑块，"
        "燃尽面积要求斑块燃料已耗尽，仍活动面积是截至观测终点已点燃且未燃尽的面积。"
        "屋面面积含遗产，普通屋面标注数统计有任一斑块受火或燃尽的原普通建筑标注。"
        "共享屋面对应多个标注时，该计数取涉及数上界。"
        "非屋面损失覆盖整个模拟研究域的地表。",
        "",
        "逐场表的 roof_cleared_plus_burned_out_m2 将施工清理屋面与保留后被火燃尽的屋面相加。"
        "两者的物理面积互不重叠，后者含遗产屋面。该指标便于在增加屋面清理的同时评估实际屋面保全。",
        "",
        "[逐场结果](cases.csv) · [逐座遗产受热及引燃](heritage.csv) · "
        "[两村九种风况的结果对照](wind-outcomes.png) · "
        "[按火点类型及风况汇总](by-wind.csv) · "
        "[按距遗产远近与风况汇总](by-source-stratum.csv) · "
        "[同火同风的布局配对](layout-pairs.csv) · "
        "[同布局的静风对照](wind-pairs.csv) · [辐射范围配对](radius-pairs.csv) · "
        "[实验登记](registration.json)",
        "",
        "## 风场输入与复现",
        "",
        "每场使用相对网格北的气象来向。风速 U 的单位为 m/s，火焰倾斜系数 k 的单位为 s/m。"
        "模型令火焰倾角 θ = arctan(kU)，并把等效辐射源沿下风移动半个火焰高度乘 sin θ，"
        "垂直高度取半个火焰高度乘 cos θ。屋面间辐射在物理斑块上按面积积分。"
        "模型用倾斜前的水平距离截断候选传热边，两种辐射范围均保留相同的风倾机制。"
        "这次研究覆盖静态均匀背景风与辐射变化。街巷绕流、随时间变化的风驱热烟羽及飞火均在研究范围之外。",
        "",
        "在项目根目录执行。村落模型与布局地图使用项目虚拟环境，"
        "绘制风况结果图使用本机已安装 Matplotlib 的 Anaconda 环境。"
        "以下命令将复算写入新的本地目录。",
        "",
        "```bash",
        ".venv/bin/python scripts/prepare_heritage_wind_strategies.py --output "
        "output/wind-replication",
        ".venv/bin/python scripts/run_heritage_wind_strategies.py --output output/wind-replication",
        ".venv/bin/python scripts/summarize_heritage_wind_strategies.py output/wind-replication",
        "/opt/anaconda3/bin/python scripts/plot_heritage_wind_outcomes.py output/wind-replication",
        ".venv/bin/python scripts/verify_heritage_wind_physics.py --output output/wind-replication",
        "```",
        "",
        "[风况接口](../../src/pyfireca/planning/environments.py) · "
        "[面积积分](../../scripts/heritage_wind_radiation.py) · "
        "[注册布局与火点](../../scripts/prepare_heritage_wind_strategies.py) · "
        "[执行模拟](../../scripts/run_heritage_wind_strategies.py) · "
        "[生成报告](../../scripts/summarize_heritage_wind_strategies.py) · "
        "[绘制风况结果图](../../scripts/plot_heritage_wind_outcomes.py) · "
        "[输入与局地求解核验收据](physics-verification.json)",
        "",
        "局地窗口的逐场边界检查见 cases 目录各 JSON 的 boundary 字段。"
        "核验收据记录物理输入指纹和局地窗口与原生求解的数值一致性测试。",
        "",
    ]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder", nargs="?", type=Path, default=ROOT / "output/heritage-wind-20261112"
    )
    parser.add_argument("--maps-only", action="store_true")
    args = parser.parse_args()
    reg = json.loads((args.folder / "registration.json").read_text())
    if reg["registration_sha256"] != content_hash(
        {k: v for k, v in reg.items() if k != "registration_sha256"}
    ):
        raise ValueError("Registration contents differ from their frozen signature")
    for setup in reg["villages"].values():
        if (
            hashlib.sha256((args.folder / setup["geojson_path"]).read_bytes()).hexdigest()
            != setup["geojson_sha256"]
        ):
            raise ValueError("Strategy map layers differ from the registered geometry")
    render_maps(args.folder, reg)
    if args.maps_only:
        return
    cases = load_results(args.folder, reg)
    differences, radial = paired_rows(cases, reg)
    wind = wind_effects(cases, reg)
    write_csv(args.folder / "cases.csv", [cases[k] for k in sorted(cases)])
    write_csv(args.folder / "heritage.csv", heritage_rows(cases))
    write_csv(args.folder / "by-wind.csv", summaries(cases))
    write_csv(args.folder / "by-source-stratum.csv", summaries(cases, "source_stratum"))
    write_csv(args.folder / "layout-pairs.csv", differences)
    write_csv(args.folder / "radius-pairs.csv", radial)
    write_csv(args.folder / "wind-pairs.csv", wind)
    (args.folder / "report.md").write_text(
        report(reg, cases, differences, radial, wind), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
