"""Plot the complete primary wind comparisons from the audited, case-level CSV."""

# ruff: noqa: RUF001

import argparse
import csv
import json
from itertools import product
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors, font_manager, ticker

VILLAGES = {"pingyan": "平岩村", "batuan": "岜团村"}
PLANS = {
    "baseline": "无清理",
    "heritage-narrow": "遗产围护   2.5 m",
    "heritage-wide": "遗产围护   15.01 m",
    "multipoint-narrow": "多点围护   2.5 m",
    "multipoint-wide": "多点围护   15.01 m",
}
DIRECTIONS = {0: "北", 90: "东", 180: "南", 270: "西"}
INK, MUTED = "#182b33", "#47606a"
DEFAULT = Path(__file__).resolve().parents[1] / "output/heritage-wind-20261112"


def read_matrices(folder):
    reg = json.loads((folder / "registration.json").read_text())
    primary = reg["primary"]
    winds = primary["wind_ids"]
    extra = reg["sensitivity"]
    expected = set()
    for village, metadata in reg["villages"].items():
        plan_ids = {p["plan_id"] for p in metadata["plans"]}
        if plan_ids != PLANS.keys():
            raise ValueError("The publication figure requires the five registered layouts")
        fire_ids = [f["fire_id"] for f in metadata["fires"]]
        if len(fire_ids) != 8 or len(set(fire_ids)) != 8:
            raise ValueError("Each panel requires eight distinct registered fire footprints")
        expected.update(product([village], fire_ids, winds, plan_ids, [primary["radius_m"]]))
        expected.update(
            product(
                [village],
                extra["fire_ids"],
                extra["wind_ids"],
                extra["plan_ids"],
                [extra["radius_m"]],
            )
        )

    with (folder / "cases.csv").open(encoding="utf-8-sig", newline="") as f:
        cases = {}
        for row in csv.DictReader(f):
            key = (
                row["village"],
                row["fire_id"],
                row["wind_id"],
                row["plan_id"],
                float(row["radius_m"]),
            )
            if key in cases or key not in expected:
                raise ValueError(f"Duplicate or unregistered case {key}")
            cases[key] = row
    if cases.keys() != expected:
        raise ValueError(f"Missing {len(expected - cases.keys())} audited cases")
    if len({r["solver_sha256"] for r in cases.values()}) != 1:
        raise ValueError("Case solver signatures differ")

    group_shape = (len(VILLAGES), len(PLANS), len(winds))
    failures = np.empty(group_shape, dtype=int)
    ordinary = np.empty(group_shape, dtype=float)
    roof_burned = np.empty(group_shape, dtype=float)
    for i, village in enumerate(VILLAGES):
        fires = reg["villages"][village]["fires"]
        for j, plan in enumerate(PLANS):
            for k, wind in enumerate(winds):
                rows = [
                    cases[village, f["fire_id"], wind, plan, primary["radius_m"]] for f in fires
                ]
                failures[i, j, k] = sum(int(r["core_ignited_count"]) > 0 for r in rows)
                ordinary[i, j, k] = np.mean([int(r["ordinary_roof_ignited_count"]) for r in rows])
                roof_burned[i, j, k] = np.mean([float(r["roof_burned_out_m2"]) for r in rows])
    return reg, failures, ordinary, roof_burned.mean(axis=2)


def configure_chinese():
    paths = (
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    )
    for candidate in paths:
        if Path(candidate).is_file():
            font_manager.fontManager.addfont(candidate)
            family = font_manager.FontProperties(fname=candidate).get_name()
            plt.rcParams.update({"font.family": [family], "axes.unicode_minus": False})
            return
    raise RuntimeError("Install a Simplified Chinese font to render the figure")


def draw(folder, reg, failures, ordinary):
    configure_chinese()
    primary = reg["primary"]
    winds = {w["wind_id"]: w for w in reg["winds"]}
    labels = []
    for name in primary["wind_ids"]:
        w = winds[name]
        labels.append(
            "静风"
            if not w["wind_speed_m_s"]
            else f"{DIRECTIONS[w['wind_from_deg']]} {w['wind_speed_m_s']:g}"
        )
    cmap = colors.LinearSegmentedColormap.from_list(
        "water", ["#f4f8f8", "#b4d9d6", "#62aaa7", "#237477", "#11474f"]
    )
    n = len(reg["villages"]["pingyan"]["fires"])
    ordinary_max = max(1.0, float(np.ceil(ordinary.max())))
    norms = (colors.Normalize(0, n), colors.Normalize(0, ordinary_max))

    fig, axes = plt.subplots(2, 2, figsize=(14.5, 7.65))
    fig.subplots_adjust(left=0.192, right=0.976, top=0.735, bottom=0.235, wspace=0.10, hspace=0.49)
    fig.text(0.043, 0.943, "两村防火线在九种风况下的受火结果", fontsize=23, color=INK)
    fig.text(
        0.045,
        0.892,
        f"每格包含同村相同的 {n} 个起火斑块（4 个固定、4 个随机）。"
        "风向取气象来向，风速单位为 m/s。",
        fontsize=12.4,
        color=MUTED,
    )
    titles = ("至少一处遗产点燃的火情数", "每场有屋面点燃的普通建筑标注数")
    for j, title in enumerate(titles):
        fig.text(axes[0, j].get_position().x0, 0.814, title, color=INK, fontsize=15)
    for i, (village, title) in enumerate(VILLAGES.items()):
        y = axes[i, 0].get_position().y1
        fig.text(0.044, y - 0.018, title, color=INK, fontsize=15)
        fig.text(
            0.045,
            y - 0.055,
            f"{len(reg['villages'][village]['core_ids'])} 处遗产",
            color=MUTED,
            fontsize=10,
        )
        for j, (data, norm) in enumerate(zip((failures[i], ordinary[i]), norms, strict=True)):
            ax = axes[i, j]
            ax.imshow(data, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
            ax.set_xticks(range(len(labels)), labels, fontsize=10.5)
            ax.xaxis.tick_top()
            ax.set_yticks(
                range(len(PLANS)),
                list(PLANS.values()) if j == 0 else [""] * len(PLANS),
                fontsize=11,
            )
            ax.tick_params(length=0, pad=5, labelcolor=INK)
            ax.set_xticks(np.arange(len(labels) + 1) - 0.5, minor=True)
            ax.set_yticks(np.arange(len(PLANS) + 1) - 0.5, minor=True)
            ax.tick_params(which="minor", length=0)
            ax.grid(which="minor", color="white", linewidth=1.4)
            for x in (0.5, 4.5):
                ax.axvline(x, color="white", linewidth=3)
            for spine in ax.spines.values():
                spine.set_visible(False)
            for r, c in np.ndindex(data.shape):
                value = data[r, c]
                rgb = np.asarray(cmap(norm(value))[:3])
                lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                text = str(int(value)) if j == 0 else f"{value:.2f}"
                ax.text(
                    c,
                    r,
                    text,
                    ha="center",
                    va="center",
                    fontsize=10.7,
                    color=INK if lum > 0.59 else "white",
                )
    for j, norm in enumerate(norms):
        pos = axes[1, j].get_position()
        cax = fig.add_axes([pos.x0, 0.185, pos.width * 0.57, 0.010])
        cbar = fig.colorbar(
            plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal"
        )
        if j == 0:
            cbar.set_ticks([0, 2, 4, 6, 8])
        else:
            cbar.locator = ticker.MaxNLocator(nbins=5, integer=True)
            cbar.update_ticks()
        cbar.ax.tick_params(length=0, labelsize=9, pad=2, labelcolor=MUTED)
        cbar.outline.set_visible(False)
        fig.text(
            pos.x0 + pos.width * 0.60, 0.180, ("场 / 8 场", "个 / 场")[j], fontsize=10, color=MUTED
        )
    fig.text(
        0.045,
        0.094,
        f"主试验共 {primary['planned_pairs']} 场，"
        f"观测至 {primary['horizon_s'] / 60:g} 分钟或自然熄灭，"
        f"辐射计算半径 {primary['radius_m']:g} m。",
        color=INK,
        fontsize=11,
    )
    fig.text(
        0.045,
        0.056,
        "普通建筑按原始屋面标注计数，包含初始起火屋面。各火点等权，均值保留两位小数。",
        color=MUTED,
        fontsize=10.5,
    )
    path = folder / "wind-outcomes.png"
    temporary = folder / "wind-outcomes.tmp.png"
    fig.savefig(temporary, dpi=190, facecolor="white")
    plt.close(fig)
    temporary.replace(path)
    return path


def draw_roof_areas(folder, reg, roof_burned):
    """Compare nonoverlapping cleared and burned-out roof areas across matching fires."""
    configure_chinese()
    cleared = np.empty_like(roof_burned)
    for i, village in enumerate(VILLAGES):
        plans = {p["plan_id"]: p for p in reg["villages"][village]["plans"]}
        cleared[i] = [plans[plan_id]["roof_cleared_m2"] for plan_id in PLANS]
    total = cleared + roof_burned
    limit = 1000 * np.ceil(total.max() * 1.13 / 1000)
    cleared_color, burned_color = "#368087", "#ce8045"
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.8), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.165, right=0.96, bottom=0.215, top=0.710, wspace=0.13)
    fig.text(0.055, 0.925, "施工清理与火灾燃尽的屋面面积", fontsize=22, color=INK)
    primary = reg["primary"]
    n = len(reg["villages"]["pingyan"]["fires"]) * len(primary["wind_ids"])
    fig.text(
        0.056,
        0.858,
        f"每村每种布局按相同的 {n} 场主试验等权平均，计入遗产与普通屋面。",
        fontsize=12,
        color=MUTED,
    )
    positions = np.arange(len(PLANS))
    for i, (ax, village) in enumerate(zip(axes, VILLAGES.values(), strict=True)):
        ax.barh(positions, cleared[i], height=0.55, color=cleared_color, label="施工清理屋面")
        ax.barh(
            positions,
            roof_burned[i],
            left=cleared[i],
            height=0.55,
            color=burned_color,
            label="火灾燃尽屋面",
        )
        for row, value in enumerate(total[i]):
            ax.annotate(
                f"{value:,.0f}",
                (value, row),
                xytext=(5, 0),
                textcoords="offset points",
                va="center",
                color=INK,
                fontsize=10.5,
            )
        ax.set_title(village, loc="left", fontsize=15, color=INK, pad=9)
        ax.set_yticks(positions, PLANS.values(), fontsize=11)
        ax.set_xlim(0, limit)
        ax.xaxis.set_major_locator(ticker.MultipleLocator(2000))
        ax.xaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#e1e7e9", linewidth=0.8)
        ax.tick_params(length=0, pad=5, labelcolor=INK)
        ax.spines[:].set_visible(False)
        ax.set_xlabel("屋面面积（m²）", fontsize=11, color=MUTED)
    axes[0].invert_yaxis()
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="center left",
        bbox_to_anchor=(0.049, 0.790),
        ncol=2,
        frameon=False,
        fontsize=11,
        labelcolor=INK,
    )
    fig.text(
        0.056,
        0.055,
        f"条末数字为两项之和（m²）。火灾燃尽按起火后 {primary['horizon_s'] / 60:g} 分钟"
        "或提前熄灭时的状态计算。",
        fontsize=10.5,
        color=MUTED,
    )
    path = folder / "roof-area-outcomes.png"
    temporary = path.with_suffix(".tmp.png")
    fig.savefig(temporary, dpi=230, facecolor="white")
    plt.close(fig)
    temporary.replace(path)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default=DEFAULT, type=Path)
    folder = parser.parse_args().folder
    reg, failures, ordinary, roof_burned = read_matrices(folder)
    print(draw(folder, reg, failures, ordinary))
    print(draw_roof_areas(folder, reg, roof_burned))
