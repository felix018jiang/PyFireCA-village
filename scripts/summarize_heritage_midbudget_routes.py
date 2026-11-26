"""Verify terminal three-arm records and tabulate the matched-budget route comparison."""

# ruff: noqa: RUF001

from collections import defaultdict
from hashlib import sha256
from math import sqrt
from statistics import fmean, stdev

from prepare_heritage_midbudget_routes import ALIGNED, UNIFORM
from run_heritage_geometry_validation import PARENT, WIDE, read_json
from run_heritage_midbudget_routes import DEST, verify
from run_heritage_wind_strategies import atomic_json, digest, record_hash
from run_heritage_wind_validation import case_name
from shapely.geometry import shape
from summarize_heritage_geometry_validation import case, outcomes
from summarize_heritage_wind_validation import atomic_csv, atomic_text, close

VILLAGES = {"pingyan": "平岩", "batuan": "岜团"}
STRATA = {"near": "近源", "far": "远源"}
ARMS = {"wide": "原多点宽围护", "uniform": "均匀收窄", "aligned": "沿不燃地表改线"}


def token(*values):
    return sha256("/".join(values).encode()).hexdigest()[:16]


def read_triplet(village, setup, fire, wind, wide_info, new_info, backend):
    wide, uniform, aligned = setup["wide_reference"], *setup["plans"]
    if [p["plan_id"] for p in (wide, uniform, aligned)] != [WIDE, UNIFORM, ALIGNED]:
        raise ValueError("Unexpected three-arm design")
    row = {
        "village": village,
        "stratum": fire["stratum"],
        "roof_group": token("midbudget-roof", village, fire["fire_id"]),
        "wind_id": wind["wind_id"],
        "pair_id": token("midbudget-pair", village, fire["fire_id"], wind["wind_id"]),
    }
    for arm, plan in zip(ARMS, (wide, uniform, aligned), strict=True):
        root, reg_hash, model = wide_info if arm == "wide" else new_info
        raw, audited = case(root, reg_hash, model, backend, village, setup, fire, wind, plan)
        grid = setup["grid"]
        full = [0, grid["height"], 0, grid["width"]]
        for phase in ("at_60min", "final"):
            boundary = raw[phase]["boundary"]
            if boundary["window_parent_indices"] == full:
                continue
            if (
                arm != "wide"
                or not boundary["artificial_boundary_clear"]
                or not boundary["exterior_cfl_allows_max_dt"]
                or boundary["exterior_can_ignite_at_ambient"]
                or boundary["artificial_boundary_min_gap_m"]
                <= boundary["required_artificial_gap_strictly_over_m"]
            ):
                raise ValueError("Original physical-domain equivalence is unverified")
        if arm == "wide":
            row["wide_full_window"] = int(raw["final"]["boundary"]["window_parent_indices"] == full)
        values = outcomes(raw, plan, is_compact=arm != "wide")
        for field in ("source_area_m2", "source_energy_J"):
            if field in row and not close(row[field], audited[field]):
                raise ValueError(f"Source area or energy differs across plans in {row['pair_id']}")
            row[field] = audited[field]
        row.update(
            {
                f"{arm}_heritage": int(values["core_ignited_count"] > 0),
                f"{arm}_hour_heritage": int(values["at_60min_core_ignited_count"] > 0),
                f"{arm}_ordinary_burned_m2": values["ordinary_burned_m2"],
                f"{arm}_ordinary_lost_m2": values["ordinary_cleared_or_burned_m2"],
                f"{arm}_original_boundary_reached": values["original_boundary_reached"],
                f"{arm}_result_sha256": audited["result_sha256"],
                f"{arm}_certificate_sha256": audited["extinction_certificate_sha256"],
            }
        )
    return row


def collected(reg, ref, new):
    rows = []
    wide_info = (PARENT, reg["wide_registration"]["sha256"], ref)
    new_info = (DEST, digest(DEST / "registration.json"), new)
    backend = read_json(DEST / "backend-verification.json")["backend_sha256"]
    expected = set()
    for v in VILLAGES:
        s = reg["villages"][v]
        for f in s["fires"]:
            for w in reg["winds"]:
                rows.append(read_triplet(v, s, f, w, wide_info, new_info, backend))
                expected.update(
                    case_name(v, f["fire_id"], w["wind_id"], p["plan_id"]) + ".json"
                    for p in s["plans"]
                )
    if (
        len(rows) != reg["primary"]["planned_triplets"]
        or len({r["pair_id"] for r in rows}) != len(rows)
        or any(
            expected != {p.name for p in (DEST / d).glob("*.json")}
            for d in ("cases", "certificates")
        )
    ):
        raise ValueError("Missing, duplicate or unexpected terminal results")
    return rows


def tables(reg, rows):
    strata, contrasts = [], []
    for v in VILLAGES:
        for stratum in STRATA:
            sample = [r for r in rows if (r["village"], r["stratum"]) == (v, stratum)]
            groups = defaultdict(list)
            for r in sample:
                groups[r["roof_group"]].append(r)
            n = len(groups)
            pool = len(reg["villages"][v]["random_eligible_ids_by_stratum"][stratum])
            wind_ids = {w["wind_id"] for w in reg["winds"]}
            if any(
                {r["wind_id"] for r in g} != wind_ids or len(g) != len(wind_ids)
                for g in groups.values()
            ):
                raise ValueError("Incomplete or duplicate wind panel for an ignition roof")
            common = {
                "village": v,
                "stratum": stratum,
                "roofs": n,
                "eligible_roofs": pool,
                "pairs": len(sample),
            }
            for arm in ARMS:
                strata.append(
                    {
                        **common,
                        "arm": arm,
                        "heritage_cases": sum(r[f"{arm}_heritage"] for r in sample),
                        "first_after_1h": sum(
                            r[f"{arm}_heritage"] - r[f"{arm}_hour_heritage"] for r in sample
                        ),
                        "affected_roofs": sum(
                            any(r[f"{arm}_heritage"] for r in site) for site in groups.values()
                        ),
                        "mean_ordinary_burned_m2": fmean(
                            r[f"{arm}_ordinary_burned_m2"] for r in sample
                        ),
                        "mean_ordinary_lost_m2": fmean(
                            r[f"{arm}_ordinary_lost_m2"] for r in sample
                        ),
                        "max_ordinary_lost_m2": max(r[f"{arm}_ordinary_lost_m2"] for r in sample),
                        "original_boundary_cases": sum(
                            r[f"{arm}_original_boundary_reached"] for r in sample
                        ),
                    }
                )
            for a, b in (("aligned", "uniform"), ("uniform", "wide"), ("aligned", "wide")):
                result = {**common, "comparison": a, "reference": b}
                for metric in ("heritage", "ordinary_burned_m2", "ordinary_lost_m2"):
                    delta = [
                        fmean(r[f"{a}_{metric}"] - r[f"{b}_{metric}"] for r in site)
                        for site in groups.values()
                    ]
                    mult = 100 if metric == "heritage" else 1
                    field = "heritage_pp" if metric == "heritage" else metric
                    result[f"paired_difference_{field}"] = mult * fmean(delta)
                    result[f"roof_cluster_sampling_se_{field}"] = (
                        mult * stdev(delta) * sqrt((1 - n / pool) / n)
                    )
                result["comparison_only_heritage"] = sum(
                    r[f"{a}_heritage"] > r[f"{b}_heritage"] for r in sample
                )
                result["reference_only_heritage"] = sum(
                    r[f"{a}_heritage"] < r[f"{b}_heritage"] for r in sample
                )
                result["both_heritage"] = sum(
                    r[f"{a}_heritage"] and r[f"{b}_heritage"] for r in sample
                )
                result["neither_heritage"] = sum(
                    not (r[f"{a}_heritage"] or r[f"{b}_heritage"]) for r in sample
                )
                if sum(
                    result[k]
                    for k in (
                        "comparison_only_heritage",
                        "reference_only_heritage",
                        "both_heritage",
                        "neither_heritage",
                    )
                ) != len(sample):
                    raise ValueError("Inconsistent number of discordant and concordant pairs")
                roof_changes = [
                    r[f"{a}_ordinary_lost_m2"] - r[f"{b}_ordinary_lost_m2"] for r in sample
                ]
                result["ordinary_lost_less_pairs"] = sum(d < -1e-5 for d in roof_changes)
                result["ordinary_lost_more_pairs"] = sum(d > 1e-5 for d in roof_changes)
                result["ordinary_lost_equal_pairs"] = sum(abs(d) <= 1e-5 for d in roof_changes)
                result["min_ordinary_lost_difference_m2"] = min(roof_changes)
                result["max_ordinary_lost_difference_m2"] = max(roof_changes)
                contrasts.append(result)
    return strata, contrasts


def construction(reg):
    costs, maps = [], {}
    for v in VILLAGES:
        s = reg["villages"][v]
        data = read_json(DEST / f"{v}-routes.geojson")
        shapes = {
            (f["properties"]["plan_id"], f["properties"]["layer"]): shape(f["geometry"])
            for f in data["features"]
        }
        change = {}
        for layer in ("cleared_roof", "cleared_ground"):
            u, a = (shapes[k, layer] for k in (UNIFORM, ALIGNED))
            change[f"{layer}_added_m2"] = a.difference(u).area
            change[f"{layer}_released_m2"] = u.difference(a).area
        for arm, plan in zip(ARMS, (s["wide_reference"], *s["plans"]), strict=True):
            if arm != "wide":
                pid = plan["plan_id"]
                if not (
                    close(shapes[pid, "cleared_roof"].area, plan["roof_cleared_m2"])
                    and close(shapes[pid, "cleared_ground"].area, plan["ground_cleared_m2"])
                    and close(shapes[pid, "outer_edge"].length, plan["perimeter_m"])
                ):
                    raise ValueError("Exported geometry and construction quantities differ")
            costs.append(
                {
                    "village": v,
                    "arm": arm,
                    "budget_cap_m2": s["budget_cap_m2"],
                    **{
                        k: plan[k]
                        for k in (
                            "width_m",
                            "roof_cleared_m2",
                            "ground_cleared_m2",
                            "construction_m2",
                        )
                    },
                    **{
                        k: plan.get(k)
                        for k in ("perimeter_m", "open_perimeter_m", "open_perimeter_fraction")
                    },
                }
            )
        maps[v] = change
    return costs, maps


def render(reg, rows, strata, contrasts, costs, maps):
    lookup = {(r["village"], r["stratum"], r["arm"]): r for r in strata}
    totals = {a: sum(r[f"{a}_heritage"] for r in rows) for a in ARMS}
    ref_full = sum(r["wide_full_window"] for r in rows)
    summary_sentence = (
        "三种布局在这批火情中均保持五处指定遗产至熄灭未引燃。"
        if not any(totals.values())
        else f"原多点宽围护、均匀收窄和沿不燃地表改线的引燃场数依次为 "
        f"{totals['wide']}、{totals['uniform']}、{totals['aligned']}。"
    )
    lines = [
        "# 中等清理预算下的侗寨多点防火线选线实验",
        "",
        f"平岩和岜团的 103 处留出起火屋面与九种风况组成 {len(rows)} 组三方案比较。"
        f"**{summary_sentence}**"
        "保护对象包括平岩的一座鼓楼和两座风雨桥，以及岜团的一座鼓楼和一座风雨桥。"
        "每个计数代表一次模拟中至少有一处指定遗产引燃。",
        "",
    ]
    for v, name in VILLAGES.items():
        sample = [r for r in rows if r["village"] == v]
        n = len(sample)
        u, a = (sum(r[f"{arm}_heritage"] for r in sample) for arm in ("uniform", "aligned"))
        ref = reg["villages"][v]["wide_reference"]
        plans = reg["villages"][v]["plans"]
        saving = [100 * (1 - p["construction_m2"] / ref["construction_m2"]) for p in plans]
        roof_saved = [ref["roof_cleared_m2"] - p["roof_cleared_m2"] for p in plans]
        lines.append(
            f"- **{name}**的均匀线和改线分别少清理 {saving[0]:.2f}% "
            f"和 {saving[1]:.2f}% 的物理面积。"
            f"其中，施工少清除的普通屋面分别为 {roof_saved[0]:,.2f} 和 {roof_saved[1]:,.2f} m²。"
            f"两者在 {n} 个组合中分别出现 {u} 场和 {a} 场指定遗产引燃。"
        )
    lines += [
        "",
        "## 清理量和线路位置",
        "",
        "| 村落 | 方案 | 普通屋面清除（m²） | 地表清理（m²） | 合计（m²） | "
        "外缘位于既有不燃地表的比例 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for c in costs:
        fraction = c["open_perimeter_fraction"]
        portion = "—" if fraction is None else f"{100 * fraction:.2f}%"
        lines.append(
            f"| {VILLAGES[c['village']]} | {ARMS[c['arm']]} | {c['roof_cleared_m2']:,.2f} | "
            f"{c['ground_cleared_m2']:,.2f} | {c['construction_m2']:,.2f} | {portion} |"
        )
    lines += [
        "",
        "中档清理上限取每村精简布局与宽围护实际清理面积的算术平均数。"
        "两村的均匀收窄带宽均为 9.25 m。普通屋面的物理清除量按共享屋面扣除重叠。"
        "地表清理按整个 5 m 网格计量。原宽围护作为热模拟参照，外缘贴合度用于比较两条中档路线。",
        "",
    ]
    for v in VILLAGES:
        m = maps[v]
        lines.append(
            f"{VILLAGES[v]}中档清理上限为 {reg['villages'][v]['budget_cap_m2']:,.2f} m²。"
            f"相较均匀线，改线保留了原计划清除的 {m['cleared_roof_released_m2']:,.2f} m² 普通屋面，"
            f"同时有 {m['cleared_roof_added_m2']:,.2f} m² 其他普通屋面进入清理范围。"
            f"有 {m['cleared_ground_released_m2']:,.0f} m² 地表清理换了位置。"
        )
        lines.append("")
    lines += [
        "![两村的中档走线和实际屋面清理换位](走线与清理换位.png)",
        "",
        "图中上方为网格北。左栏显示两村的围护范围，点划矩形标出右栏局部。"
        "右栏色块显示两条中档路线中恰有一条会清除的普通屋面。"
        "局部窗口依据屋面清理位置的变化选取。",
        "",
    ]
    lines += [
        "## 熄灭前的遗产引燃与普通屋面损失",
        "",
        "表中三项依次为原多点宽围护、均匀收窄、沿不燃地表改线。普通屋面总损失等于施工清除面积加保留屋面的烧尽面积。",
        "",
        "| 村落与分层 | 屋面数 | 配对数 | 指定遗产引燃场数 | "
        "普通屋面平均烧尽（m²） | 普通屋面平均总损失（m²） |",
        "|---|---:|---:|---|---|---|",
    ]
    for v in VILLAGES:
        for s in STRATA:
            rs = [lookup[v, s, a] for a in ARMS]

            def seq(field, fmt, records=rs):
                return " / ".join(format(r[field], fmt) for r in records)

            lines.append(
                f"| {VILLAGES[v]}{STRATA[s]} | {rs[0]['roofs']} | {rs[0]['pairs']} | "
                f"{seq('heritage_cases', 'd')} | {seq('mean_ordinary_burned_m2', ',.2f')} | "
                f"{seq('mean_ordinary_lost_m2', ',.2f')} |"
            )
    lines += [
        "",
        "![三条路线的终态配对结果](终态配对结果.png)",
        "",
        "左图以各村原宽围护的实际清理量为 100%，点旁标注清理面积。图顶列出三方案的遗产引燃场数。"
        "右图显示两条中档路线相对宽围护的普通屋面平均总损失差，误差棒为一个按起火屋面聚类的抽样标准误。",
        "",
        "## 同等清理量下的配对差异",
        "",
        "同场比较给出改线相对均匀线的变化。前两项分别计数两条路线中恰好只有一条出现遗产引燃的组合。"
        "面积差值均为改线减均匀线。总损失差为负表示改线后火灾结束时保留了更多普通屋面。",
        "",
        "| 村落与分层 | 仅均匀线引燃 | 仅改线引燃 | 引燃比例差（百分点） | "
        "平均烧尽差（m²） | 平均总损失差（m²） | 总损失差的标准误（m²） |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in contrasts:
        if r["reference"] == "uniform":
            lines.append(
                f"| {VILLAGES[r['village']]}{STRATA[r['stratum']]} | "
                f"{r['reference_only_heritage']} | "
                f"{r['comparison_only_heritage']} | {r['paired_difference_heritage_pp']:+.2f} | "
                f"{r['paired_difference_ordinary_burned_m2']:+,.2f} | "
                f"{r['paired_difference_ordinary_lost_m2']:+,.2f} | "
                f"{r['roof_cluster_sampling_se_ordinary_lost_m2']:,.2f} |"
            )
    results = {(r["village"], r["stratum"], r["comparison"], r["reference"]): r for r in contrasts}
    pu = [results["pingyan", layer, "uniform", "wide"] for layer in STRATA]
    bn = [results["batuan", "near", arm, "wide"] for arm in ("uniform", "aligned")]
    bg = [results["batuan", layer, "aligned", "uniform"] for layer in STRATA]
    pf = results["pingyan", "far", "aligned", "uniform"]
    b0, b1 = reg["villages"]["batuan"]["plans"]
    lines += [
        "",
        "## 清理时省下的屋面，有多少留到了火后",
        "",
        f"平岩的均匀收窄方案在 {sum(r['pairs'] for r in pu)} 组火情中，"
        f"有 {sum(r['ordinary_lost_less_pairs'] for r in pu)} 组的普通屋面总损失低于原宽围护，"
        f"{sum(r['ordinary_lost_equal_pairs'] for r in pu)} 组持平，"
        f"{sum(r['ordinary_lost_more_pairs'] for r in pu)} 组增加。"
        "清除和烧尽合计之后，这一村落仍保留了收窄围护带的居住空间收益。"
        "逐场比较的持平容差为 0.00001 m²。",
        "",
        f"岜团近源层共有 {bn[0]['pairs']} 组。相对原宽围护，均匀线和改线分别在 "
        f"{bn[0]['ordinary_lost_more_pairs']} 和 {bn[1]['ordinary_lost_more_pairs']} "
        "组中增加了总损失。"
        "这些火情的增损幅度使全层平均总损失分别上升 "
        f"{bn[0]['paired_difference_ordinary_lost_m2']:,.2f} 和 "
        f"{bn[1]['paired_difference_ordinary_lost_m2']:,.2f} m²。",
        "",
        f"在相同地表清理量下，岜团改线相较均匀线使近源、远源的平均普通屋面烧尽面积分别减少 "
        f"{-bg[0]['paired_difference_ordinary_burned_m2']:.2f} 和 "
        f"{-bg[1]['paired_difference_ordinary_burned_m2']:.2f} m²，"
        f"两项均小于改线额外清除的 {b1['roof_cleared_m2'] - b0['roof_cleared_m2']:.2f} m²。"
        f"平岩远源层的改线使总损失平均减少 {-pf['paired_difference_ordinary_lost_m2']:.2f} m²，"
        f"其中 {pf['ordinary_lost_less_pairs']} 组减少，{pf['ordinary_lost_more_pairs']} 组增加，"
        f"{pf['ordinary_lost_equal_pairs']} 组持平。两村的结果说明，沿已有不燃地表走线所保留的"
        "屋面，需要结合具体火情和清理位置判断。",
    ]
    lines += [
        "",
        "## 适用范围与复核",
        "",
        "两条中档路线在各村全计算域运行，采用固定的 25 m² 起火斑块、"
        "九种等权稳态风、1 m 局部屋面网格、"
        "5 m 地表网格与 15 m 辐射搜索半径，模型中关闭飞火。"
        "两条中档路线用地图和两类材料的清理量选定。"
        "原面板已用于评价宽围护和精简布局，本次新增的路线在全部热模拟前固定。"
        "每组三臂核对起火斑块、能量、风况、材料输入和熄灭证书。"
        f"原宽围护有 {ref_full} 组使用全域，另 {len(rows) - ref_full} 组复用外缘满足隔离条件的"
        "已验证局部计算窗口。凡火势触及原物理域边界，只计域内火损。",
        "",
        "近源层为原抽样框中所有符合条件的屋面，远源层为各村随机抽取的 30 处屋面。"
        "抽样标准误按屋面聚类、九种风等权，使用有限总体修正。近源层的抽样标准误为零，仍有模型和实地条件的不确定性。"
        "全样本场数描述受检组合，不能直接换算为两村年火灾风险。",
        "",
        "地表底图将裸土、硬化地面和水体合并编码。外缘贴合度衡量边界位于该类已知不燃表面的长度占比。"
        "线路的现场连续性、不同地块的使用价值和实施可行性需要独立调查。",
        "",
        "[冻结实验方案](实验方案.md) · [路线清理量](路线清理量.csv) · [分层结果](分层结果.csv) · "
        "[逐场三方案配对](三方案配对.csv) · [分层配对差](分层配对差.csv) · "
        "[核验记录](核验记录.json)",
        "",
        "逐场表以配对编号连接三套方案，同一 roof_group 表示九种风共用一个起火屋面。"
        "清理量、烧尽面积及其合计均以平方米计。配对差表中的 pp 表示百分点。",
        "",
        "在项目根目录执行以下命令可重建选线、续算缺失场次并核验全部记录。"
        "已完成的场次在验证证书后复用。",
        "",
        "```sh",
        "PYTHONPATH=src:scripts .venv/bin/python scripts/prepare_heritage_midbudget_routes.py",
        "PYTHONPATH=src:scripts .venv/bin/python scripts/run_heritage_midbudget_routes.py",
        "PYTHONPATH=src:scripts .venv/bin/python scripts/summarize_heritage_midbudget_routes.py",
        "```",
        "",
        "在装有 Matplotlib、NumPy 和 Shapely 的 Python 环境中运行 "
        "`scripts/plot_heritage_midbudget_routes.py` 可重绘两张图。",
    ]
    return "\n".join(lines) + "\n"


def main():
    reg, ref, new = verify()
    rows = collected(reg, ref, new)
    strata, diffs = tables(reg, rows)
    costs, maps = construction(reg)
    files = {
        "三方案配对.csv": rows,
        "分层结果.csv": strata,
        "分层配对差.csv": diffs,
        "路线清理量.csv": costs,
    }
    for filename, data in files.items():
        atomic_csv(DEST / filename, data)
    evidence = {
        "registration_file_sha256": digest(DEST / "registration.json"),
        "parent_study_layout_runs_inherited_as_planned_pairs": reg["primary"]["planned_pairs"],
        "planned_new_runs": reg["primary"]["planned_new_runs"],
        "triplets": len(rows),
        "new_case_records_verified": 2 * len(rows),
        "archived_wide_records_verified": len(rows),
        "validated_extinction_certificates": 3 * len(rows),
        "all_source_areas_and_energies_matched": True,
        "both_maps_recounted": True,
        "changed_material_locations_m2": maps,
        "tables_sha256": {f: digest(DEST / f) for f in files},
    }
    evidence["evidence_sha256"] = record_hash(evidence)
    atomic_json(DEST / "核验记录.json", evidence)
    atomic_text(DEST / "补充实验结果.md", render(reg, rows, strata, diffs, costs, maps))
    print("Terminal records, three-arm pairing and material geometry verified", len(rows))


if __name__ == "__main__":
    main()
