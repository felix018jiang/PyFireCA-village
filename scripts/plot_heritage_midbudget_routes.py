"""Plot the frozen routes, material changes and paired terminal outcomes."""

# ruff: noqa: RUF001

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import shapely as sh
from matplotlib import font_manager
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from shapely.geometry import shape
from shapely.plotting import patch_from_polygon

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "output/heritage-midbudget-20261120"
WIND = ROOT / "output/heritage-wind-validation-20261112"
UNIFORM, ALIGNED = "uniform-mid", "open-ground-mid"
INK, UNIFORM_COLOR, ALIGNED_COLOR = "#283740", "#aa4e27", "#006c66"
OPEN, ROOF, FIXED = "#dbe9ed", "#d5d1c9", "#677078"


def read(path):
    return json.loads(path.read_bytes())


def draw_line(ax, geom, **kwargs):
    for part in sh.get_parts(geom):
        if part.geom_type in ("LineString", "LinearRing"):
            ax.plot(*part.xy, **kwargs)
        else:
            draw_line(ax, part, **kwargs)


def area_patch(ax, geom, **kwargs):
    if geom.is_empty:
        return
    for poly in sh.get_parts(geom):
        if poly.geom_type in ("Polygon", "MultiPolygon"):
            ax.add_patch(patch_from_polygon(poly, **kwargs))


def make_map(ax, data, bounds, detail):
    setup, buildings, parts, wide, open_pixels = data
    x0, y0, x1, y1 = bounds
    grid = setup["grid"]
    step = grid["cell_size_m"]
    c0 = max(0, int((x0 - grid["x_min_m"]) / step) - 1)
    c1 = min(grid["width"], int((x1 - grid["x_min_m"]) / step) + 2)
    r0 = max(0, int((grid["y_max_m"] - y1) / step) - 1)
    r1 = min(grid["height"], int((grid["y_max_m"] - y0) / step) + 2)
    ax.imshow(
        open_pixels[r0:r1, c0:c1],
        origin="upper",
        interpolation="nearest",
        extent=(
            grid["x_min_m"] + c0 * step,
            grid["x_min_m"] + c1 * step,
            grid["y_max_m"] - r1 * step,
            grid["y_max_m"] - r0 * step,
        ),
        cmap=ListedColormap(["#fbfaf7", OPEN]),
        vmin=0,
        vmax=1,
        zorder=0,
    )
    visible = sh.box(*bounds)
    fixed = set(setup["wide_reference"]["anchor_ids"])
    for key, poly in buildings.items():
        if poly.intersects(visible):
            area_patch(
                ax,
                poly,
                facecolor=FIXED if key in fixed else ROOF,
                edgecolor="#9a9892",
                linewidth=0.3,
                zorder=1,
            )
    if detail:
        u, a = (parts[n, "cleared_roof"] for n in (UNIFORM, ALIGNED))
        for geom, color in ((u.difference(a), UNIFORM_COLOR), (a.difference(u), ALIGNED_COLOR)):
            area_patch(
                ax, geom, facecolor=color, edgecolor="white", linewidth=0.4, zorder=2, alpha=0.83
            )
    draw_line(ax, wide, color="#798187", lw=0.75 if detail else 0.65, ls=(0, (1, 2)), zorder=3)
    draw_line(
        ax, parts[ALIGNED, "outer_edge"], color=ALIGNED_COLOR, lw=1.8 if detail else 1.15, zorder=4
    )
    draw_line(
        ax,
        parts[UNIFORM, "outer_edge"],
        color=UNIFORM_COLOR,
        lw=1.5 if detail else 1.0,
        ls=(0, (4, 2)),
        zorder=5,
    )
    ax.set(xlim=(x0, x1), ylim=(y0, y1), aspect="equal", xticks=[], yticks=[])
    for spine in ax.spines.values():
        spine.set_color("#bac0bf")
        spine.set_linewidth(0.65)
    size = 10 if detail else (50 if x1 - x0 > 180 else 25)
    x = x0 + (x1 - x0) * 0.065
    y = y0 + (y1 - y0) * 0.068
    ax.plot([x, x + size], [y, y], lw=2.3, color=INK, zorder=7)
    ax.text(
        x + size / 2,
        y + (y1 - y0) * 0.02,
        f"{size} m",
        ha="center",
        fontsize=8,
        color=INK,
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=0.7),
        zorder=8,
    )


def rect(center, width, height):
    x, y = center
    return x - width / 2, y - height / 2, x + width / 2, y + height / 2


def plot_outcomes():
    with (DEST / "分层结果.csv").open(encoding="utf-8-sig", newline="") as handle:
        counts = {(r["village"], r["stratum"], r["arm"]): r for r in csv.DictReader(handle)}
    with (DEST / "分层配对差.csv").open(encoding="utf-8-sig", newline="") as handle:
        delta = {
            (r["village"], r["stratum"], r["comparison"]): r
            for r in csv.DictReader(handle)
            if r["reference"] == "wide"
        }
    with (DEST / "路线清理量.csv").open(encoding="utf-8-sig", newline="") as handle:
        costs = {
            (r["village"], r["arm"]): float(r["construction_m2"]) for r in csv.DictReader(handle)
        }
    groups = [
        ("pingyan", "near", "平岩近源"),
        ("pingyan", "far", "平岩远源"),
        ("batuan", "near", "岜团近源"),
        ("batuan", "far", "岜团远源"),
    ]
    styles = {
        "wide": ("#77818a", "s", "原多点宽围护"),
        "uniform": (UNIFORM_COLOR, "o", "均匀收窄"),
        "aligned": (ALIGNED_COLOR, "D", "沿不燃地表改线"),
    }
    fig, axes = plt.subplots(1, 2, figsize=(10.7, 4.9), gridspec_kw={"width_ratios": [1, 1.1]})
    fig.subplots_adjust(left=0.085, right=0.98, bottom=0.215, top=0.79, wspace=0.32)
    total_pairs = sum(int(r["pairs"]) for (v, s, a), r in counts.items() if a == "wide")
    event_counts = [
        sum(int(r["heritage_cases"]) for (v, s, a), r in counts.items() if a == arm)
        for arm in styles
    ]
    fig.text(
        0.505,
        0.965,
        f"指定遗产引燃（{total_pairs} 组）     原宽围护 {event_counts[0]} 场"
        f"     均匀收窄 {event_counts[1]} 场     沿不燃地表改线 {event_counts[2]} 场",
        ha="center",
        va="top",
        fontsize=11,
        color=INK,
    )
    for village_index, v in enumerate(("pingyan", "batuan")):
        for j, (arm, (color, marker, _)) in enumerate(styles.items()):
            relative = 100 * costs[v, arm] / costs[v, "wide"]
            y = 0.5 + 2 * village_index + (j - 1) * 0.38
            axes[0].hlines(y, 0, relative, lw=1.1, color=color, alpha=0.45)
            axes[0].plot(
                relative,
                y,
                marker=marker,
                color=color,
                markerfacecolor="white" if j == 0 else color,
                ms=5.8,
                zorder=3,
            )
            axes[0].annotate(
                f"{costs[v, arm]:,.0f} m²",
                (relative, y),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                color=color,
            )
    for bucket, (v, s, _) in enumerate(groups):
        for j, arm in enumerate(("uniform", "aligned")):
            color, marker, _ = styles[arm]
            d = delta[v, s, arm]
            axes[1].errorbar(
                float(d["paired_difference_ordinary_lost_m2"]),
                bucket + (j - 0.5) * 0.22,
                xerr=float(d["roof_cluster_sampling_se_ordinary_lost_m2"]),
                fmt=marker,
                ms=5.7,
                color=color,
                capsize=3,
                elinewidth=1,
                zorder=3,
            )
    axes[0].set_xlim(-1, 126)
    axes[0].set_xticks([0, 25, 50, 75, 100])
    axes[0].set_xlabel("占原多点宽围护清理量的比例（%）")
    axes[1].axvline(0, color="#80888a", ls="--", lw=0.9)
    axes[1].set_xlabel("中档路线减原宽围护（m²）")
    axes[0].set_title("a   实际清理总面积", loc="left", fontsize=13, pad=10)
    axes[1].set_title("b   普通屋面平均总损失差", loc="left", fontsize=13, pad=10)
    for ax in axes:
        ax.set_ylim(3.53, -0.54)
        ax.grid(axis="x", color="#e6e9e8", lw=0.8)
        ax.tick_params(axis="both", length=0, pad=5, labelcolor=INK)
        for k in ("top", "right", "left"):
            ax.spines[k].set_visible(False)
        ax.spines["bottom"].set_color("#b5bdbe")
        ax.axhline(1.5, color="#cdd3d3", lw=0.8)
    axes[0].set_yticks([0.5, 2.5], ["平岩", "岜团"])
    axes[1].set_yticks(range(len(groups)), [label for _, _, label in groups])
    handles = [
        Line2D(
            [],
            [],
            marker=m,
            ms=6,
            ls="none",
            color=c,
            markerfacecolor="white" if a == "wide" else c,
            label=label,
        )
        for a, (c, m, label) in styles.items()
    ]
    fig.legend(
        handles=handles,
        ncol=3,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.015),
        frameon=False,
        fontsize=10,
    )
    fig.savefig(
        DEST / "终态配对结果.png", dpi=220, bbox_inches="tight", pad_inches=0.2, facecolor="white"
    )
    plt.close(fig)


def main():
    font = Path("/Library/Fonts/Arial Unicode.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    else:
        plt.rcParams["font.family"] = ["Noto Sans CJK SC", "Source Han Sans SC", "sans-serif"]
    plt.rcParams.update({"axes.unicode_minus": False, "axes.titlecolor": INK})
    reg = read(DEST / "registration.json")
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 10.8), gridspec_kw={"width_ratios": [0.88, 1.2]})
    fig.subplots_adjust(left=0.055, right=0.965, bottom=0.115, top=0.938, wspace=0.12, hspace=0.17)
    for idx, (v, name) in enumerate((("pingyan", "平岩"), ("batuan", "岜团"))):
        setup = reg["villages"][v]
        archive = read((WIND / setup["geojson_path"]).resolve())
        buildings = {
            f["properties"]["building_id"]: shape(f["geometry"])
            for f in archive["features"]
            if f["properties"]["layer"] in ("heritage", "building")
        }
        wide = (
            sh.union_all([buildings[k] for k in setup["wide_reference"]["anchor_ids"]])
            .buffer(setup["wide_reference"]["width_m"], quad_segs=64)
            .boundary
        )
        plan = read(DEST / f"{v}-routes.geojson")
        parts = {
            (f["properties"]["plan_id"], f["properties"]["layer"]): shape(f["geometry"])
            for f in plan["features"]
        }
        study = Path(setup["study_path"])
        case = (study.parent / read(study)["case_dir"]).resolve()

        def raster(key, directory=case):
            return np.load(directory / f"{key}.npy", mmap_mode="r")

        mask = (
            raster("domain_mask")
            & raster("known_mask")
            & ~raster("burnable_mask")
            & (raster("landcover_index") == 0)
            & (raster("building_index") < 0)
        )
        data = setup, buildings, parts, wide, mask
        u, a = (parts[k, "cleared_roof"] for k in (UNIFORM, ALIGNED))
        # Center the detail on nearby roof changes using geometry alone.
        changed = u.symmetric_difference(a)
        centers = [buildings[k].centroid for k in setup["wide_reference"]["anchor_ids"]]
        focus = max(centers, key=lambda pt: changed.intersection(pt.buffer(40)).area)
        detail = rect((focus.x, focus.y), 108, 85)
        x0, y0, x1, y1 = sh.union_all([wide.envelope, sh.box(*detail)]).bounds
        center = (0.5 * (x0 + x1), 0.5 * (y0 + y1))
        height = max((y1 - y0) * 1.18, (x1 - x0) * 1.18 / 0.86)
        overview = rect(center, 0.86 * height, height)
        for col, (bounds, label) in enumerate(((overview, "围护范围"), (detail, "清理换位局部"))):
            ax = axes[idx, col]
            make_map(ax, data, bounds, bool(col))
            letter = "abcd"[2 * idx + col]
            ax.set_title(f"{letter}   {name}  ·  {label}", loc="left", fontsize=13, pad=7)
        dx, dy, ex, ey = detail
        axes[idx, 0].add_patch(
            Rectangle(
                (dx, dy),
                ex - dx,
                ey - dy,
                fill=False,
                edgecolor=INK,
                linewidth=1,
                ls=(0, (6, 2, 1, 2)),
                zorder=6,
            )
        )
    handles = [
        Line2D([], [], color="#798187", ls=":", label="原多点宽围护"),
        Line2D([], [], color=UNIFORM_COLOR, ls="--", label="均匀收窄"),
        Line2D([], [], color=ALIGNED_COLOR, label="沿不燃地表改线"),
        Patch(color=OPEN, label="底图不燃地表"),
        Patch(color=FIXED, label="固定围护点屋面"),
        Patch(color=ROOF, label="其他屋面"),
        Patch(color=UNIFORM_COLOR, label="仅均匀线清除的屋面"),
        Patch(color=ALIGNED_COLOR, label="仅改线清除的屋面"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=4,
        frameon=False,
        fontsize=9.5,
        handlelength=2.3,
        bbox_to_anchor=(0.5, 0.025),
        columnspacing=1.65,
    )
    fig.savefig(
        DEST / "走线与清理换位.png",
        dpi=220,
        bbox_inches="tight",
        pad_inches=0.18,
        facecolor="white",
    )
    plt.close(fig)
    if (DEST / "分层结果.csv").is_file() and (DEST / "分层配对差.csv").is_file():
        plot_outcomes()


if __name__ == "__main__":
    main()
