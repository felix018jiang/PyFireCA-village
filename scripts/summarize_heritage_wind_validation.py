"""Verify and report only the new 4,635 paired firebreak validation simulations."""
# ruff: noqa: RUF001

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import combinations
from math import comb, isclose, isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev

from render_heritage_wind_maps import PLANS, VILLAGES
from run_heritage_wind_extinction import terminal_signature
from summarize_heritage_wind_strategies import ROOT, write_csv

from pyfireca.planning.heritage import content_hash
from pyfireca.village.extinction_v3 import extinction_certificate_sha256

DEFAULT = ROOT / "output/heritage-wind-validation-20261112"
PAPER = ROOT / "docs/HERITAGE_WIND_INDEPENDENT_VALIDATION_20261112.md"
ARCHIVE = ROOT / "docs/results/heritage-wind-independent-validation-20261112"
SIZES = {
    ("pingyan", "near"): 23,
    ("pingyan", "far"): 30,
    ("batuan", "near"): 20,
    ("batuan", "far"): 30,
}
TIMES = ("at_60min", "final")
MATERIALS = ("roof", "ground", "total")
PRIMARY_PAIR = ("heritage-wide", "multipoint-wide")


def read_json(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stratum(name):
    return "far" if name == "background" else name


def registered_fires(reg, prior):
    pools = {}
    if reg["winds"] != prior["winds"] or len(reg["winds"]) != 9 or reg["primary"]["radius_m"] != 15:
        raise ValueError("Wind conditions or exposure radius differ from the original registration")
    for village, setup in reg["villages"].items():
        orig = prior["villages"][village]
        if any(
            setup[k] != orig[k] for k in ("plans", "core_ids", "study_path", "core_overlap_policy")
        ):
            raise ValueError(f"{village}: layout or physical inputs differ from the original study")
        old_ids = {f["source_id"] for f in orig["fires"]}
        used = [f["source_id"] for f in setup["fires"]]
        if len(used) != len(set(used)) or old_ids.intersection(used):
            raise ValueError(f"{village}: validation sites overlap within or across studies")
        for group, roof_ids in orig["random_eligible_ids_by_stratum"].items():
            s = stratum(group)
            available = set(roof_ids) - old_ids
            registered = setup["random_eligible_ids_by_stratum"][s]
            if len(registered) != len(available) or set(registered) != available:
                raise ValueError(
                    f"{village} {s}: finite population differs from original eligibility"
                )
            sample = {f["source_id"] for f in setup["fires"] if stratum(f["stratum"]) == s}
            if len(sample) != SIZES[village, s] or not sample <= available:
                raise ValueError(f"{village} {s}: validation sample mismatches eligible roofs")
            if s == "near" and sample != available:
                raise ValueError(f"{village}: near stratum is incomplete")
            pools[village, s] = len(available)
    if set(pools) != set(SIZES):
        raise ValueError("Incomplete village or stratum registration")
    return pools


def close(a, b):
    return isclose(a, b, rel_tol=1e-9, abs_tol=1e-5)


def inspect_observation(obs, cores, case):
    t = obs["elapsed_simulated_s"]
    fields = [
        "active_m2",
        "source_area_m2",
        "source_energy_J",
        "energy_closure_error_J",
        "energy_closure_tolerance_J",
        "elapsed_simulated_s",
    ]
    for mat in ("roof", "ground"):
        fields.extend((f"{mat}_burned_out_m2", f"{mat}_ignited_m2"))
    if not all(isfinite(obs[k]) and obs[k] >= 0 for k in fields):
        raise ValueError(f"{case}: invalid material area, energy or clock")
    if (
        obs["energy_closure_error_J"] > obs["energy_closure_tolerance_J"]
        or not obs["boundary"]["closed"]
    ):
        raise ValueError(f"{case}: invalid energy or spatial closure")
    active = sum(
        obs[f"{mat}_ignited_m2"] - obs[f"{mat}_burned_out_m2"] for mat in ("roof", "ground")
    )
    if not close(active, obs["active_m2"]):
        raise ValueError(f"{case}: active area inconsistent with ignited and burned areas")
    times = {key: value["first_ignition_s"] for key, value in obs["core_outcomes"].items()}
    if set(times) != set(cores) or obs["core_ignited_count"] != sum(
        x is not None for x in times.values()
    ):
        raise ValueError(f"{case}: heritage outcomes inconsistent with registered targets")
    if any(x is not None and not (isfinite(x) and 0 <= x <= t + 1e-7) for x in times.values()):
        raise ValueError(f"{case}: invalid first ignition time")
    return times


def inspect_case(path, raw, reg_sha, fire, wind, plan, village, cores, folder):
    key = raw["case_id"]
    checksum = raw.pop("result_sha256")
    if not (
        content_hash(raw) == checksum
        and key == path.stem
        and raw["village"] == village
        and raw["fire_id"] == fire["fire_id"]
        and raw["plan_id"] == plan["plan_id"]
        and raw["wind_id"] == wind["wind_id"]
        and raw["registration_sha256"] == reg_sha
        and raw["kind"] == fire["kind"]
        and stratum(raw["stratum"]) == stratum(fire["stratum"])
        and raw["wind_speed_m_s"] == wind["wind_speed_m_s"]
        and raw["wind_from_deg"] == wind["wind_from_deg"]
        and raw["footprint_sha256"] == fire["footprint_sha256"]
        and raw["radius_m"] == 15
    ):
        raise ValueError(f"{key}: signed case identity or inputs differ")
    early, last = raw["at_60min"], raw["final"]
    initial_times = inspect_observation(early, cores, key)
    final_times = inspect_observation(last, cores, key)
    if not close(early["elapsed_simulated_s"], 3600) or not close(
        early["source_area_m2"], fire["area_m2"]
    ):
        raise ValueError(f"{key}: first-hour boundary or ignition area mismatch")
    if not close(last["source_area_m2"], early["source_area_m2"]) or not close(
        last["source_energy_J"], early["source_energy_J"]
    ):
        raise ValueError(f"{key}: ignition source changed")
    if last["active_m2"] != 0 or not raw["extinction_closed"]:
        raise ValueError(f"{key}: extinction not certified")
    for h, t in final_times.items():
        e = initial_times[h]
        if t is None and e is None:
            continue
        if e is None and t is not None and t > 3600 + 1e-7:
            continue
        if e is None or t is None or not close(t, e):
            raise ValueError(f"{key}: cumulative heritage ignition is inconsistent across times")
    for mat in ("roof", "ground"):
        b = last[f"{mat}_burned_out_m2"]
        if not close(b, last[f"{mat}_ignited_m2"]) or b + 1e-5 < early[f"{mat}_ignited_m2"]:
            raise ValueError(f"{key}: cumulative {mat} loss decreased or fuel still burning")
    if last["elapsed_simulated_s"] < 3600 - 1e-7 and (
        early["active_m2"] != 0
        or any(
            not close(early[f"{mat}_burned_out_m2"], last[f"{mat}_burned_out_m2"])
            for mat in ("roof", "ground")
        )
    ):
        raise ValueError(f"{key}: first-hour observation disagrees with earlier extinction")
    certificate = read_json(folder / "certificates" / path.name)
    cert_hash = raw["extinction_certificate_sha256"]
    if (
        cert_hash != extinction_certificate_sha256(certificate)
        or cert_hash != certificate["certificate_sha256"]
        or not close(certificate["actual_stop_time_s"], last["elapsed_simulated_s"])
        or not certificate["post_apply_events_boundary"]
        or certificate["future_prescribed_event_count"] != 0
        or certificate["burning_node_count"] != 0
        or certificate["eligible_thermal_ignition_node_count"] != 0
        or certificate["future_mean_hrr_W"] != 0
        or not close(
            certificate["cumulative_burned_out_covered_area_m2"],
            last["roof_burned_out_m2"] + last["ground_burned_out_m2"],
        )
    ):
        raise ValueError(f"{key}: extinction certificate is not bound to the final state")
    row = dict(
        case_id=key,
        village=village,
        stratum=stratum(fire["stratum"]),
        fire_id=fire["fire_id"],
        wind_id=wind["wind_id"],
        wind_speed_m_s=wind["wind_speed_m_s"],
        wind_from_deg=wind["wind_from_deg"],
        plan_id=plan["plan_id"],
        width_m=plan["width_m"],
        source_area_m2=early["source_area_m2"],
        source_energy_J=early["source_energy_J"],
        registration_sha256=reg_sha,
        result_sha256=checksum,
        solver_sha256=raw["solver_sha256"],
        reference_solver_sha256=raw.get("reference_solver_sha256", raw["solver_sha256"]),
        execution_backend_sha256=raw.get("execution_backend_sha256", ""),
        extinction_certificate_sha256=cert_hash,
    )
    for suffix, obs in zip(TIMES, (early, last), strict=True):
        for mat in ("roof", "ground"):
            row[f"{suffix}_{mat}_m2"] = obs[f"{mat}_burned_out_m2"]
        row[f"{suffix}_total_m2"] = row[f"{suffix}_roof_m2"] + row[f"{suffix}_ground_m2"]
        row[f"{suffix}_heritage_count"] = obs["core_ignited_count"]
        row[f"{suffix}_any_heritage"] = int(obs["core_ignited_count"] > 0)
        row[f"{suffix}_original_boundary"] = int(
            obs["boundary"]["original_physical_boundary_reached"]
        )
        row[f"{suffix}_active_m2"] = obs["active_m2"]
        row[f"{suffix}_ordinary_roof_ignited_count"] = obs["ordinary_roof_ignited_count"]
        row[f"{suffix}_ordinary_roof_burned_out_count"] = obs["ordinary_roof_burned_out_count"]
    row["extinction_observed_s"] = last["elapsed_simulated_s"]
    row["newly_affected_after_60min"] = int(
        not row["at_60min_any_heritage"] and row["final_any_heritage"]
    )
    row["heritage_ignitions_after_60min"] = (
        row["final_heritage_count"] - row["at_60min_heritage_count"]
    )
    return row


def load_cases(folder, reg):
    registration_hash = sha(folder / "registration.json")
    params = {}
    for village, setup in reg["villages"].items():
        for fire in setup["fires"]:
            for plan in setup["plans"]:
                for wind in reg["winds"]:
                    key = (village, fire["fire_id"], wind["wind_id"], plan["plan_id"])
                    if key in params:
                        raise ValueError(f"Duplicate registration: {key}")
                    params[key] = fire, wind, plan, village, setup["core_ids"]
    paths = sorted((folder / "cases").glob("*.json"))
    if len(paths) != len(params) or len(params) != 4635:
        raise ValueError(f"{len(paths)} of 4,635 expected terminal records available")
    rows, sources = [], {}
    for path in paths:
        raw = read_json(path)
        key = tuple(raw[k] for k in ("village", "fire_id", "wind_id", "plan_id"))
        if key not in params:
            raise ValueError(f"{path}: duplicate or unregistered simulation")
        row = inspect_case(path, raw, registration_hash, *params.pop(key), folder)
        source = (row["source_area_m2"], row["source_energy_J"])
        ref = sources.setdefault((row["village"], row["fire_id"]), source)
        if not all(close(v, r) for v, r in zip(source, ref, strict=True)):
            raise ValueError(f"{path}: paired layouts differ in ignition fuel or area")
        rows.append(row)
    if params:
        raise ValueError("Some registered wind and layout pairs lack terminal observations")
    return rows


def native_replay_evidence(folder, rows):
    """Bind independent NumPy checks at both endpoints to the retained production cases."""
    path = folder / "verification.json"
    receipt = read_json(path)
    signature = receipt.pop("verification_sha256")
    registered = sha(folder / "registration.json")
    if (
        content_hash(receipt) != signature
        or receipt["registration_sha256"] != registered
        or len(receipt["cases"]) != receipt["sample_count"]
    ):
        raise ValueError("Invalid native replay index")
    by_id = {r["case_id"]: r for r in rows}
    seen = set()
    for entry in receipt["cases"]:
        identifier = entry["case_id"]
        check = read_json(folder / "native-checks" / f"{identifier}.json")
        result = by_id[identifier]
        if (
            identifier in seen
            or check.pop("verification_sha256") != entry["verification_sha256"]
            or content_hash(check) != entry["verification_sha256"]
            or check["case_id"] != identifier
            or check["registration_sha256"] != registered
            or check["production_result_sha256"] != result["result_sha256"]
            or check["production_certificate_sha256"] != result["extinction_certificate_sha256"]
            or set(check["checks"]) != set(TIMES)
        ):
            raise ValueError("Independent replay is not bound to both retained observations")
        seen.add(identifier)
    return {"count": len(seen), "verification_file_sha256": sha(path)}


def solver_evidence(folder, reg, rows):
    native = terminal_signature(reg["original_solver_sha256"])
    worker = sha(ROOT / "scripts/run_heritage_wind_validation.py")
    reference = hashlib.sha256((native + worker).encode()).hexdigest()
    if {r["reference_solver_sha256"] for r in rows} != {reference}:
        raise ValueError("A case does not use the frozen reference model")
    allowed = {"": reference}
    receipt_sha = None
    if any(r["execution_backend_sha256"] for r in rows):
        path = folder / "backend-verification.json"
        receipt = read_json(path)
        backend = receipt["backend_sha256"]
        if receipt["passed"] is not True or not receipt["cases"]:
            raise ValueError("Accelerated production cases lack a passed numerical cross-check")
        allowed[backend] = hashlib.sha256(f"{reference}/{backend}".encode()).hexdigest()
        receipt_sha = sha(path)
    if any(allowed.get(r["execution_backend_sha256"]) != r["solver_sha256"] for r in rows):
        raise ValueError("A case is not bound to a verified solver backend")
    return {
        "reference_solver_sha256": reference,
        "solver_signatures_and_counts": dict(
            sorted(Counter(r["solver_sha256"] for r in rows).items())
        ),
        "backend_verification_file_sha256": receipt_sha,
    }


def site_risk_upper_count(population, sampled, affected):
    """Invert a one-sided 5% hypergeometric test for the finite population count."""
    if sampled == population:
        return affected
    lo, hi = affected, population - sampled + affected
    denominator = comb(population, sampled)
    while lo < hi:
        candidate = (lo + hi + 1) // 2
        numerator = sum(
            comb(candidate, k) * comb(population - candidate, sampled - k)
            for k in range(max(0, sampled - (population - candidate)), affected + 1)
        )
        if 20 * numerator >= denominator:
            lo = candidate
        else:
            hi = candidate - 1
    return lo


def summarize(rows, reg):
    sites = defaultdict(list)
    for row in rows:
        sites[row["village"], row["stratum"], row["plan_id"], row["fire_id"]].append(row)
    site_rows = defaultdict(dict)
    summaries = []
    winds = {w["wind_id"] for w in reg["winds"]}
    for (v, s, plan, fire), group in sorted(sites.items()):
        if len(group) != 9 or {r["wind_id"] for r in group} != winds:
            raise ValueError("Incomplete nine-wind block")
        item = {}
        for t in TIMES:
            for mat in MATERIALS:
                item[f"{t}_{mat}_m2"] = fmean(r[f"{t}_{mat}_m2"] for r in group)
            item[f"{t}_ignited_winds"] = sum(r[f"{t}_any_heritage"] for r in group)
            item[f"{t}_heritage_ignitions"] = sum(r[f"{t}_heritage_count"] for r in group)
            item[f"{t}_any_wind"] = int(item[f"{t}_ignited_winds"] > 0)
            item[f"{t}_boundary_cases"] = sum(r[f"{t}_original_boundary"] for r in group)
        item["still_burning_at_60min"] = sum(r["at_60min_active_m2"] > 0 for r in group)
        item["newly_affected_after_60min"] = sum(r["newly_affected_after_60min"] for r in group)
        item["max_final_total_m2"] = max(r["final_total_m2"] for r in group)
        item["latest_extinction_s"] = max(r["extinction_observed_s"] for r in group)
        site_rows[v, s, plan][fire] = item
    for v, setup in reg["villages"].items():
        for s in ("near", "far"):
            for plan in setup["plans"]:
                group = list(site_rows[v, s, plan["plan_id"]].values())
                if len(group) != SIZES[v, s]:
                    raise ValueError(f"{v} {s}: missing sites for {plan['plan_id']}")
                item = dict(
                    village=v,
                    stratum=s,
                    plan_id=plan["plan_id"],
                    sites=len(group),
                    cases=9 * len(group),
                )
                population = len(setup["random_eligible_ids_by_stratum"][s])
                item["remaining_eligible_sites"] = population
                for name in ("width_m", "construction_m2", "roof_cleared_m2", "ground_cleared_m2"):
                    item[name] = plan[name]
                for t in TIMES:
                    for mat in MATERIALS:
                        item[f"mean_{t}_{mat}_m2"] = fmean(r[f"{t}_{mat}_m2"] for r in group)
                    for name in (
                        "ignited_winds",
                        "heritage_ignitions",
                        "any_wind",
                        "boundary_cases",
                    ):
                        item[f"{t}_{name}"] = sum(r[f"{t}_{name}"] for r in group)
                    affected = item[f"{t}_any_wind"]
                    item[f"{t}_site_fraction_upper95"] = (
                        site_risk_upper_count(population, len(group), affected) / population
                    )
                for name in ("still_burning_at_60min", "newly_affected_after_60min"):
                    item[name] = sum(r[name] for r in group)
                for name in ("max_final_total_m2", "latest_extinction_s"):
                    item[name] = max(r[name] for r in group)
                summaries.append(item)
    return summaries, site_rows


def paired_summary(site_rows, reg, pools):
    pairs = []
    for v, setup in reg["villages"].items():
        for s in ("near", "far"):
            for left, right in combinations([p["plan_id"] for p in setup["plans"]], 2):
                a, b = site_rows[v, s, left], site_rows[v, s, right]
                if a.keys() != b.keys():
                    raise ValueError(f"Unpaired sources: {v} {s} {left} {right}")
                n, population = len(a), pools[v, s]
                pair = dict(
                    village=v,
                    stratum=s,
                    sampled_sites=n,
                    remaining_eligible_sites=population,
                    reference=left,
                    comparison=right,
                )
                for t in TIMES:
                    outcomes = {
                        f"{t}_risk_pp": (f"{t}_ignited_winds", 100 / 9),
                        f"{t}_site_risk_pp": (f"{t}_any_wind", 100),
                    }
                    outcomes.update({f"{t}_{mat}_m2": (f"{t}_{mat}_m2", 1) for mat in MATERIALS})
                    for label, (field, scale) in outcomes.items():
                        diffs = [scale * (a[k][field] - b[k][field]) for k in a]
                        pair[f"mean_reduction_{label}"] = fmean(diffs)
                        pair[f"se_reduction_{label}"] = sqrt((1 - n / population) / n) * stdev(
                            diffs
                        )
                field = "final_any_wind"
                pair["reference_only_ignited_sites"] = sum(a[k][field] > b[k][field] for k in a)
                pair["comparison_only_ignited_sites"] = sum(b[k][field] > a[k][field] for k in a)
                pairs.append(pair)
    return pairs


def village_estimates(summary, pools):
    """Standardize to uniform roof selection in each village's remaining frozen pool."""
    by_key = {(r["village"], r["stratum"], r["plan_id"]): r for r in summary}
    output = []
    for v in VILLAGES:
        for plan in PLANS:
            n_near, n_far = pools[v, "near"], pools[v, "far"]
            weights = (n_near / (n_near + n_far), n_far / (n_near + n_far))
            groups = [by_key[v, s, plan] for s in ("near", "far")]
            result = dict(
                village=v,
                plan_id=plan,
                eligible_near=n_near,
                eligible_far=n_far,
                near_weight=weights[0],
                far_weight=weights[1],
            )
            for t in TIMES:
                for mat in MATERIALS:
                    key = f"mean_{t}_{mat}_m2"
                    result[key] = sum(w * r[key] for w, r in zip(weights, groups, strict=True))
                result[f"{t}_heritage_fraction"] = sum(
                    w * r[f"{t}_ignited_winds"] / r["cases"]
                    for w, r in zip(weights, groups, strict=True)
                )
                result[f"{t}_sites_ignited_fraction"] = sum(
                    w * r[f"{t}_any_wind"] / r["sites"]
                    for w, r in zip(weights, groups, strict=True)
                )
            output.append(result)
    return output


def compare_wide_layouts(reg, summary, pairs):
    by_key = {(r["village"], r["stratum"], r["plan_id"]): r for r in summary}
    passages = ["按遗产宽围护、多点宽围护的顺序比较终态遗产点燃场数。"]
    for v in reg["villages"]:
        heritage, multi = (by_key[v, "near", plan] for plan in PRIMARY_PAIR)
        near = f"{heritage['final_ignited_winds']}、{multi['final_ignited_winds']}"
        far = "、".join(str(by_key[v, "far", plan]["final_ignited_winds"]) for plan in PRIMARY_PAIR)
        extra = multi["construction_m2"] - heritage["construction_m2"]
        passages.append(
            f"{VILLAGES[v]}近层为 {near} 场（每布局 {heritage['cases']} 场），"
            f"远层为 {far} 场（每布局 {by_key[v, 'far', PRIMARY_PAIR[0]]['cases']} 场），"
            f"多点宽围护多清理 {extra:,.2f} m²。"
        )
    reduced = unchanged = 0
    increases = []
    for pair in pairs:
        if (pair["reference"], pair["comparison"]) != PRIMARY_PAIR:
            continue
        label = VILLAGES[pair["village"]] + ("近层" if pair["stratum"] == "near" else "远层")
        for material, name in (("roof", "屋面"), ("ground", "地表")):
            delta = round(pair[f"mean_reduction_final_{material}_m2"], 2)
            if delta < 0:
                increases.append(f"{label}{name}增加 {-delta:,.2f} m²")
            elif delta > 0:
                reduced += 1
            else:
                unchanged += 1
    passages.append(
        f"四层各比较最终屋面和地表燃尽均值，精确到 0.01 m²，多点宽围护有 "
        f"{reduced} 项下降、{unchanged} 项持平、{len(increases)} 项上升。"
    )
    if increases:
        passages.append("其中" + "，".join(increases) + "。")
    return "".join(passages)


def render_report(reg, summary, pairs, pools, prefix):
    observed = sum(r["cases"] for r in summary)
    active = sum(r["still_burning_at_60min"] for r in summary)
    newly = sum(r["newly_affected_after_60min"] for r in summary)
    later_heritage = sum(
        r["final_heritage_ignitions"] - r["at_60min_heritage_ignitions"] for r in summary
    )
    text = [
        "# 两村四种防火线的独立火点终态验证",
        "",
        f"本轮对 103 个新起火屋面进行了 {observed:,} 场配对模拟。"
        f"第 60 分钟有 {active:,} 场仍在燃烧，有 {newly:,} 场在一小时后首次点燃遗产。"
        f"按场次与建筑分别计数，一小时后新增 {later_heritage:,} 次遗产点燃。"
        "全部场次均已取得熄灭证书，最终仍燃面积均为零。",
        "",
        compare_wide_layouts(reg, summary, pairs),
        "",
        "五套布局共用同一组 25 m² 起火斑块及九种静态均匀风况。"
        "风况包括静风和东、南、西、北四个来向各 2、4 m/s。"
        "遗产围护沿遗产屋面轮廓布带，多点围护额外围住所保留的四个原固定起火屋面。"
        "窄、宽带的名义宽度分别为 2.5 m、15.01 m，均从围护对象的屋面轮廓向外量取。"
        "普通屋面与带内有面积重叠时按原屋面归属规则清理，"
        "可燃非屋面地表按与带相交的整格（5 m × 5 m）清理。"
        "施工量统计实际清除的屋面并集与整格地表面积。原 15 m 辐射作用半径和材料参数沿用至完全熄灭。"
        "每村原来的四个固定火点和四个随机火点均从本轮抽样框排除。"
        "四个固定火点所在的屋面继续作为原多点布局的围护对象。",
        "",
        "按普通屋面到最近遗产屋面的轮廓净距分层，近层不超过 50 m，远层超过 50 m。"
        "近层覆盖全部剩余候选，远层从剩余候选等概率无放回抽取 30 处。"
        f"抽样与屋面内落点的登记种子为 {reg['random_design']['seed']}。"
        "每处在单个屋面中固定一个斑块，并用于全部五套布局。"
        "面积均为 m²。清理量为每布局一次施工量，燃尽均值使同层的每处屋面与每个风况等权。"
        "一处或多处遗产在同一场曾经起燃，均记为一场。火点数则按该处九种风况中任何一种出现过遗产点燃来计数。",
        "",
    ]
    for v in reg["villages"]:
        text += [f"## {VILLAGES[v]}村", ""]
        wide_pairs = []
        for s, name in (("near", "近层"), ("far", "远层")):
            subset = [r for r in summary if r["village"] == v and r["stratum"] == s]
            n = subset[0]["sites"]
            text += [
                f"### {name}",
                "",
                f"本层取剩余 {pools[v, s]} 处合格屋面中的 {n} 处，每布局 {n * 9} 场。",
                "",
                "| 布局 | 清理屋面 | 清理地表 | 清理合计 | "
                "60 分钟屋面燃尽均值 | 60 分钟地表燃尽均值 | 最终屋面燃尽均值 | 最终地表燃尽均值 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
            for r in subset:
                keys = (
                    "roof_cleared_m2",
                    "ground_cleared_m2",
                    "construction_m2",
                    "mean_at_60min_roof_m2",
                    "mean_at_60min_ground_m2",
                    "mean_final_roof_m2",
                    "mean_final_ground_m2",
                )
                text.append(
                    f"| {PLANS[r['plan_id']]} | " + " | ".join(f"{r[k]:,.2f}" for k in keys) + " |"
                )
            text += [
                "",
                "| 布局 | 60 分钟遗产点燃场数 | 终态累计点燃场数 | "
                "有遗产点燃的火点数（60 分钟 → 终态） | 终态火点占比上界 | "
                "60 分钟仍燃场数 | 最终触边场数 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            for r in subset:
                text.append(
                    f"| {PLANS[r['plan_id']]} | {r['at_60min_ignited_winds']}/{r['cases']} | "
                    f"{r['final_ignited_winds']}/{r['cases']} | "
                    f"{r['at_60min_any_wind']}/{n} → {r['final_any_wind']}/{n} | "
                    f"{r['final_site_fraction_upper95']:.1%} | "
                    f"{r['still_burning_at_60min']} | {r['final_boundary_cases']} |"
                )
            base = next(r for r in subset if r["plan_id"] == "baseline")
            wide = next(r for r in subset if r["plan_id"] == "multipoint-wide")
            reduction = base["mean_final_total_m2"] - wide["mean_final_total_m2"]
            text += [
                "",
                f"无清理与多点宽围护的最终总燃尽均值分别为 {base['mean_final_total_m2']:,.2f} 和 "
                f"{wide['mean_final_total_m2']:,.2f} m²。"
                f"每场差值（基线减多点宽围护）为 {reduction:,.2f} m²。",
                "",
            ]
            wide_pairs.append(
                next(
                    r
                    for r in pairs
                    if r["village"] == v
                    and r["stratum"] == s
                    and (r["reference"], r["comparison"]) == PRIMARY_PAIR
                )
            )
        text += [
            "### 相同带宽下增加多点围护的配对差值",
            "",
            "以遗产宽围护为参照，对每个屋面先比较同一风况下两种布局，再对该屋面的九种风况及同层各屋面等权平均。"
            "正值表示多点宽围护减少相应损失。火点单边点燃栏分别统计仅遗产宽围护出现点燃、仅多点宽围护出现点燃的屋面数。",
            "",
            "| 层 | 终态点燃比例减少（百分点） | 终态屋面少烧 m² | "
            "终态地表少烧 m² | 火点单边点燃（遗产宽／多点宽） |",
            "|---|---:|---:|---:|---:|",
        ]
        for r in wide_pairs:
            text.append(
                f"| {'近层' if r['stratum'] == 'near' else '远层'} | "
                f"{r['mean_reduction_final_risk_pp']:.2f} | "
                f"{r['mean_reduction_final_roof_m2']:,.2f} | "
                f"{r['mean_reduction_final_ground_m2']:,.2f} | "
                f"{r['reference_only_ignited_sites']}／{r['comparison_only_ignited_sites']} |"
            )
        costs = {p["plan_id"]: p["roof_cleared_m2"] for p in reg["villages"][v]["plans"]}
        extra_roof = costs[PRIMARY_PAIR[1]] - costs[PRIMARY_PAIR[0]]
        changes = []
        for pair in wide_pairs:
            delta = extra_roof - pair["mean_reduction_final_roof_m2"]
            layer = "近层" if pair["stratum"] == "near" else "远层"
            changes.append(f"{layer}{'少' if delta < 0 else '多'} {abs(delta):,.2f} m²")
        text += [
            "",
            "将施工清除屋面与剩余屋面的最终平均燃尽面积相加，"
            "多点宽围护相较遗产宽围护的屋面合计损失为" + "、".join(changes) + "。",
            "",
        ]
    text += [
        "## 指标与数据来源",
        "",
        "遗产点燃比例按本批预设风况计算。各村近、远层分开报告。村级加权附表按各层剩余候选屋面数加权，"
        "估计在合格屋面中等概率选址、九种风况等权时的表现。两村使用各自的候选屋面数。"
        "实际火灾发生频率、屋面内其他起火位置与实测风频均需独立数据。",
        "",
        "“终态火点占比上界”对应每处屋面按登记种子选一个落点、九种风中任一风况引起遗产点燃的屋面占比。"
        "远层按无放回抽样的超几何分布计算单侧 95% 上置信限。"
        "近层覆盖全部剩余合格屋面，列出已观测的总体占比。"
        "各布局分别计算上界。",
        "",
        "同层布局差值附表包含全部十种两两比较，均保持同一火点、风况的配对。"
        "面积差为参照布局减比较布局。遗产点燃差以百分点表示。"
        "配对标准误按屋面间差值的样本标准差和分层无放回抽样比例计算，九种风况留在同一屋面内。"
        "近层覆盖剩余总体，因此该有限屋面总体的抽样标准误为零。屋面内落点、模拟参数及村际差异另有不确定性。"
        "燃尽面积统计覆盖原始研究域，触边场次的域外损失未知。",
        "",
        "原始登记、每场 60 分钟与熄灭观测、熄灭证书及原始 NumPy 交叉复算收据均保存在项目的 "
        "`output/heritage-wind-validation-20261112/`。在项目根目录运行 "
        "`.venv/bin/python scripts/summarize_heritage_wind_validation.py --archive` "
        "可复核并生成本文。",
        "",
        f"[村落和分层统计]({prefix}by-stratum.csv) · "
        f"[同火点同风况的布局差值]({prefix}paired-layouts.csv) · "
        f"[按合格屋面数加权的村级估计]({prefix}by-village-weighted.csv) · "
        f"[数据指纹与复核计数]({prefix}evidence.json)",
        "",
    ]
    return "\n".join(text)


def atomic_text(path, text):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def atomic_csv(path, rows):
    temp = path.with_suffix(path.suffix + ".tmp")
    write_csv(temp, rows)
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument(
        "--prior", type=Path, default=ROOT / "output/heritage-wind-20261112/registration.json"
    )
    parser.add_argument(
        "--archive", action="store_true", help="Publish verified group-level results in docs"
    )
    args = parser.parse_args()
    folder = args.output.resolve()
    reg = read_json(folder / "registration.json")
    if reg["registration_sha256"] != content_hash(
        {k: v for k, v in reg.items() if k != "registration_sha256"}
    ):
        raise ValueError("Validation registration content hash mismatch")
    prior = read_json(args.prior)
    if reg["original_registration_sha256"] != sha(args.prior):
        raise ValueError("Original registration file differs from the one used for sampling")
    pools = registered_fires(reg, prior)
    rows = load_cases(folder, reg)
    native_checks = native_replay_evidence(folder, rows)
    solvers = solver_evidence(folder, reg, rows)
    summary, sites = summarize(rows, reg)
    pairs = paired_summary(sites, reg, pools)
    village = village_estimates(summary, pools)
    tables = {
        "by-stratum.csv": summary,
        "paired-layouts.csv": pairs,
        "by-village-weighted.csv": village,
    }
    atomic_csv(folder / "cases.csv", rows)
    (folder / "cases.csv").chmod(0o600)
    for name, table in tables.items():
        atomic_csv(folder / name, table)
    evidence = {
        "new_only": True,
        "case_count": len(rows),
        "paired_60min_and_certified_terminal_observations": len(rows),
        "active_cases_at_60min": sum(r["at_60min_active_m2"] > 0 for r in rows),
        "active_cases_at_certified_end": sum(r["final_active_m2"] > 0 for r in rows),
        "cases_first_heritage_ignition_after_60min": sum(
            r["newly_affected_after_60min"] for r in rows
        ),
        "additional_heritage_building_ignitions_after_60min": sum(
            r["heritage_ignitions_after_60min"] for r in rows
        ),
        "independent_roof_count": len({(r["village"], r["fire_id"]) for r in rows}),
        "strata": len(pools),
        "village_stratum_layout_count": len(summary),
        "paired_layout_rows": len(pairs),
        "registration_sha256": sha(folder / "registration.json"),
        "original_registration_sha256": sha(args.prior),
        **solvers,
        "additional_execution_backends": sorted(
            {r["execution_backend_sha256"] for r in rows if r["execution_backend_sha256"]}
        ),
        "independent_native_replay": native_checks,
        "table_file_sha256": {name: sha(folder / name) for name in ("cases.csv", *tables)},
        "case_and_extinction_certificate_fingerprints_sha256": content_hash(
            sorted(
                (r["case_id"], r["result_sha256"], r["extinction_certificate_sha256"]) for r in rows
            )
        ),
    }
    atomic_text(folder / "evidence.json", json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    atomic_text(folder / "report.md", render_report(reg, summary, pairs, pools, ""))
    if args.archive:
        ARCHIVE.mkdir(exist_ok=True, parents=True)
        for name in (*tables, "evidence.json"):
            tmp = (ARCHIVE / name).with_suffix(".tmp")
            tmp.write_bytes((folder / name).read_bytes())
            tmp.replace(ARCHIVE / name)
        atomic_text(PAPER, render_report(reg, summary, pairs, pools, f"results/{ARCHIVE.name}/"))
        index = ROOT / "docs/HERITAGE_RESEARCH_EVIDENCE_CHAIN_20261024.md"
        old = index.read_text()
        if PAPER.name not in old:
            head, rest = old.split("\n", 1)
            insert = (
                "**2026-11-12 · 独立火点终态验证。** 103 处新屋面、九种静态风和五套布局的 "
                f"4,635 场均保留 60 分钟观察并已确认熄灭。[论文记录与分层结果]({PAPER.name})"
            )
            atomic_text(index, f"{head}\n\n{insert}\n{rest}")
    print(
        f"Verified {len(rows)} new cases, {len(summary)} stratum-layout groups, "
        f"{len(pairs)} paired contrasts."
    )


if __name__ == "__main__":
    main()
