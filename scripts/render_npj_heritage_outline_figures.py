"""Render manuscript figures from the frozen village inventories and audited comparisons."""

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PatchCollection
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as FigurePath
from matplotlib.ticker import PercentFormatter
from plot_heritage_geometry_validation import DEFAULT as GEOMETRY_RESULTS
from plot_heritage_geometry_validation import load as load_geometry_results
from plot_heritage_geometry_validation import plot as plot_geometry_results
from pyproj import Transformer
from render_heritage_wind_maps import polygons
from shapely import union_all
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
WIND = ROOT / "output/heritage-wind-20261112"
VALIDATION = ROOT / "output/heritage-wind-validation-20261112"
POSITION = ROOT / "docs/results/heritage-followup-20261103"
DEST = ROOT / "output/npj-heritage-science-outline/figures"
VILLAGES = ("pingyan", "batuan")
COMPACT = {
    "pingyan": "pingyan-local-observed-subset.geojson",
    "batuan": "batuan-economic-D9.geojson",
}
LAYOUTS = (
    ("baseline", "No clearance"),
    ("heritage-narrow", "Heritage narrow"),
    ("heritage-wide", "Heritage wide"),
    ("multipoint-narrow", "Multipoint narrow"),
    ("multipoint-wide", "Multipoint wide"),
)
ROOF = "#cdd5db"
HERITAGE = "#283d59"
NEAR = "#bd5340"
FAR = "#6a58a4"
CLEAR_ROOF = "#bd5340"
CLEAR_GROUND = "#56a6a0"
INK = "#25303c"


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save(fig, dest, name):
    for ext in ("pdf", "png"):
        fig.savefig(dest / f"{name}.{ext}", dpi=320, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def features(village):
    return [
        (f["properties"], shape(f["geometry"]))
        for f in read(WIND / f"{village}-layouts.geojson")["features"]
    ]


def fill(axis, geometry, face, edge=None, linewidth=0.25, zorder=2):
    patches = []
    for geom in geometry:
        for raw in polygons(geom):
            polygon = orient(raw, sign=1.0)
            vertices, codes = [], []
            for ring in (polygon.exterior, *polygon.interiors):
                points = np.asarray(ring.coords)
                vertices.extend(points)
                codes.extend(
                    [
                        FigurePath.MOVETO,
                        *([FigurePath.LINETO] * (len(points) - 2)),
                        FigurePath.CLOSEPOLY,
                    ]
                )
            patches.append(PathPatch(FigurePath(vertices, codes)))
    axis.add_collection(
        PatchCollection(
            patches, facecolor=face, edgecolor=edge or face, linewidth=linewidth, zorder=zorder
        )
    )


def bounds(geometries, pad):
    left, bottom, right, top = union_all(geometries).bounds
    return left - pad, bottom - pad, right + pad, top + pad


def map_axis(axis, extent, title, scale, *, north=True):
    left, bottom, right, top = extent
    axis.set(xlim=(left, right), ylim=(bottom, top), aspect="equal", xticks=[], yticks=[])
    axis.set_title(title, loc="left", weight="semibold", pad=9)
    axis.set_facecolor("#f5f7f8")
    for spine in axis.spines.values():
        spine.set_color("#c2ccd3")
    x, y = left + 0.05 * (right - left), bottom + 0.065 * (top - bottom)
    axis.plot((x, x + scale), (y, y), color=INK, linewidth=1.6, zorder=8)
    axis.annotate(
        f"{scale} m",
        (x + scale / 2, y),
        xytext=(0, 4),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        zorder=8,
    )
    if north:
        axis.annotate(
            "N",
            xy=(0.94, 0.94),
            xytext=(0.94, 0.82),
            xycoords="axes fraction",
            textcoords="axes fraction",
            ha="center",
            fontsize=8,
            arrowprops={"arrowstyle": "-|>", "lw": 0.8, "color": INK},
        )


def draw_buildings(axis, all_features, *, label=False, background=True):
    if background:
        fill(axis, [g for p, g in all_features if p["layer"] == "building"], ROOF, "#9ba9b4")
    heritage = [(p, g) for p, g in all_features if p["layer"] == "heritage"]
    fill(axis, [g for _, g in heritage], HERITAGE, "white", 0.4, zorder=6)
    if label:
        for p, geom in heritage:
            pt = geom.representative_point()
            axis.annotate(
                p["building_id"],
                (pt.x, pt.y),
                xytext=(5, 5),
                textcoords="offset points",
                color=HERITAGE,
                fontsize=7,
                zorder=9,
                bbox={"fc": "white", "ec": "none", "alpha": 0.85, "pad": 1.0},
            )


def legend(fig, handles):
    fig.legend(
        handles=handles,
        loc="outside lower center",
        ncols=len(handles),
        frameon=False,
        columnspacing=1.4,
        fontsize=8,
    )


def study_maps(dest, reg, geography):
    fig, axes = plt.subplots(
        1, 3, figsize=(10.5, 4.6), layout="constrained", gridspec_kw={"width_ratios": (0.77, 1, 1)}
    )
    locator = axes[0]
    to_geo = Transformer.from_crs(32649, 4326, always_xy=True)
    locations = {}
    for village in VILLAGES:
        union = union_all(
            [g for p, g in geography[village] if p["layer"] in ("heritage", "building")]
        )
        locations[village] = to_geo.transform(union.centroid.x, union.centroid.y)
    for village, (lon, lat) in locations.items():
        locator.scatter(lon, lat, color=HERITAGE, s=35, zorder=3)
        locator.annotate(
            village.title(),
            (lon, lat),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            weight="semibold",
        )
    locator.set_title("a  Northern Guangxi", loc="left", weight="semibold")
    locator.set(
        xlim=(
            min(x for x, _ in locations.values()) - 0.04,
            max(x for x, _ in locations.values()) + 0.04,
        ),
        ylim=(
            min(y for _, y in locations.values()) - 0.055,
            max(y for _, y in locations.values()) + 0.06,
        ),
        xlabel="Longitude (°E)",
        ylabel="Latitude (°N)",
    )
    locator.set_aspect(1 / np.cos(np.deg2rad(np.mean([y for _, y in locations.values()]))))
    locator.grid(color="#e7ecef", linewidth=0.7)
    for panel, village, axis in zip("bc", VILLAGES, axes[1:], strict=True):
        setup = reg["villages"][village]
        geometry = geography[village]
        box = bounds([g for p, g in geometry if p["layer"] in ("heritage", "building")], 30)
        map_axis(axis, box, f"{panel}  {village.title()}", 100)
        draw_buildings(axis, geometry, label=True)
        for stratum, colour, marker in (("near", NEAR, "o"), ("far", FAR, "^")):
            locations_utm = [
                shape(f["geometry"]).representative_point()
                for f in setup["fires"]
                if f["stratum"] == stratum
            ]
            axis.scatter(
                [p.x for p in locations_utm],
                [p.y for p in locations_utm],
                color=colour,
                marker=marker,
                s=12,
                edgecolor="white",
                linewidth=0.3,
                zorder=7,
            )
    legend(
        fig,
        [
            Patch(color=ROOF, label="Ordinary roof"),
            Patch(color=HERITAGE, label="Heritage roof"),
            Line2D([], [], marker="o", lw=0, color=NEAR, label="Near held-out roof"),
            Line2D([], [], marker="^", lw=0, color=FAR, label="Far held-out roof"),
        ],
    )
    save(fig, dest, "fig-01-study-sites")


def position_test(dest):
    data = rows(POSITION / "core-checkpoints-dual-scale.csv")
    experiments = (
        ("drum-B0077-known-hot", "B0073", "a  Drum tower near its design source"),
        ("bridge-B0062-known", "B0120", "b  Wind-and-rain bridge near its design source"),
    )
    plan_labels = {
        "baseline": "No clearance",
        "selected": "Selected position",
        "area-matched-position": "Alternative position",
    }
    fig, axes = plt.subplots(1, 2, figsize=(8.8, 3.3), sharey=True, layout="constrained")
    for axis, (fire, core, title) in zip(axes, experiments, strict=True):
        relevant = {
            r["plan_id"]: r
            for r in data
            if r["village_id"] == "batuan"
            and r["fire_id"] == fire
            and r["core_id"] == core
            and r["time_s"] == "3600.0"
        }
        if set(relevant) != set(plan_labels):
            raise ValueError(
                f"Position controls differ from the documented design: {relevant.keys()}"
            )
        for i, plan in enumerate(plan_labels):
            for delta, col, mark in (
                (-0.10, "h1_first_ignition_s", "o"),
                (0.10, "h0_5_first_ignition_s", "s"),
            ):
                field = relevant[plan][col]
                if field:
                    axis.scatter(
                        float(field) / 60, i + delta, marker=mark, color=NEAR, s=28, zorder=4
                    )
                else:
                    axis.scatter(
                        60,
                        i + delta,
                        marker=mark,
                        facecolor="white",
                        edgecolor=HERITAGE,
                        s=28,
                        zorder=4,
                    )
        axis.set(xlim=(0, 64), ylim=(2.65, -0.65), xlabel="Time since prescribed ignition (min)")
        for x in (20, 40, 60):
            axis.axvline(x, linewidth=0.75, linestyle="--", color="#cbd5dc", zorder=0)
        axis.set_title(title, loc="left", weight="semibold")
    axes[0].set_yticks(range(3), list(plan_labels.values()))
    legend(
        fig,
        [
            Line2D([], [], marker="o", lw=0, color=NEAR, label="1 m roof grid"),
            Line2D([], [], marker="s", lw=0, color=NEAR, label="0.5 m roof grid"),
            Line2D(
                [],
                [],
                marker="o",
                lw=0,
                markerfacecolor="white",
                markeredgecolor=HERITAGE,
                label="Unignited at 60 min",
            ),
        ],
    )
    save(fig, dest, "fig-02-position-controls")


def terminal_results(dest):
    records = {
        (r["village"], r["stratum"], r["plan_id"]): r for r in rows(VALIDATION / "by-stratum.csv")
    }
    fig, axes = plt.subplots(2, 2, figsize=(9, 5.4), sharex=True, sharey=True, layout="constrained")
    for i, village in enumerate(VILLAGES):
        for j, stratum in enumerate(("near", "far")):
            ax = axes[i, j]
            for k, (layout, _) in enumerate(LAYOUTS):
                item = records[village, stratum, layout]
                n = int(item["cases"])
                a = 100 * int(item["at_60min_ignited_winds"]) / n
                b = 100 * int(item["final_ignited_winds"]) / n
                ax.plot((a, b), (k + 0.12, k - 0.12), linewidth=1.0, color="#a7b3bc", zorder=2)
                ax.scatter(a, k + 0.12, s=25, facecolor="white", edgecolor=HERITAGE, zorder=4)
                ax.scatter(b, k - 0.12, s=25, color=NEAR, zorder=4)
            ax.set(xlim=(-3, 103), ylim=(4.65, -0.65), xticks=(0, 25, 50, 75, 100))
            ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
            ax.grid(axis="x", linewidth=0.7, color="#e2e7eb")
            n = records[village, stratum, "baseline"]["cases"]
            ax.set_title(
                f"{'abcd'[i * 2 + j]}  {village.title()} {stratum} sources  (n = {n} per layout)",
                loc="left",
                weight="semibold",
            )
            if i == 1:
                ax.set_xlabel("Cases with heritage ignition")
    axes[0, 0].set_yticks(range(5), [label for _, label in LAYOUTS])
    legend(
        fig,
        [
            Line2D(
                [],
                [],
                marker="o",
                lw=0,
                markerfacecolor="white",
                markeredgecolor=HERITAGE,
                label="By 60 min",
            ),
            Line2D([], [], marker="o", lw=0, color=NEAR, label="By extinction"),
        ],
    )
    save(fig, dest, "fig-03-terminal-strategies")


def clearance_maps(dest, reg, geography):
    fig, axes = plt.subplots(2, 2, figsize=(9, 7.6), layout="constrained")
    project = Transformer.from_crs(4326, 32649, always_xy=True).transform
    for i, village in enumerate(VILLAGES):
        data = geography[village]
        wide = [
            (p["layer"], g)
            for p, g in data
            if p.get("plan_id") == "multipoint-wide"
            and p["layer"] in ("cleared_roof", "cleared_ground")
        ]
        compact_data = read(ROOT / "output/heritage-firebreak-actions-20261111" / COMPACT[village])
        compact = [
            (
                "cleared_roof" if f["properties"]["material"] == "roof_union" else "cleared_ground",
                transform(project, shape(f["geometry"])),
            )
            for f in compact_data["features"]
        ]
        extent = bounds(
            [g for _, g in [*wide, *compact]] + [g for p, g in data if p["layer"] == "heritage"], 55
        )
        setup = reg["villages"][village]
        for j, (title, alterations, plan) in enumerate(
            (
                ("multipoint wide", wide, setup["wide_reference"]),
                ("compact", compact, setup["plans"][0]),
            )
        ):
            axis = axes[i, j]
            map_axis(
                axis,
                extent,
                f"{'abcd'[i * 2 + j]}  {village.title()} {title}"
                f"  ({plan['construction_m2']:,.0f} m²)",
                50,
            )
            draw_buildings(axis, data)
            fill(
                axis,
                [g for layer, g in alterations if layer == "cleared_ground"],
                CLEAR_GROUND,
                zorder=3,
            )
            fill(
                axis,
                [g for layer, g in alterations if layer == "cleared_roof"],
                CLEAR_ROOF,
                zorder=4,
            )
            draw_buildings(axis, data, label=True, background=False)
    legend(
        fig,
        [
            Patch(color=ROOF, label="Ordinary roof"),
            Patch(color=HERITAGE, label="Heritage roof"),
            Patch(color=CLEAR_ROOF, label="Removed roof area"),
            Patch(color=CLEAR_GROUND, label="Cleared ground footprint"),
        ],
    )
    save(fig, dest, "fig-04-clearance-geometries")


def domain_maps(dest, reg):
    fig, axes = plt.subplots(1, 2, figsize=(8.8, 4.3), layout="constrained")
    colours = ("#ffffff", "#e5e0d6", "#c8be82", "#769692", "#334662")
    cmap = ListedColormap(colours)
    norm = BoundaryNorm((-1.5, -0.5, 0.5, 2.5, 4.5, 5.5), cmap.N)
    for panel, ax, village in zip("ab", axes, VILLAGES, strict=True):
        setup = reg["villages"][village]
        study = Path(setup["study_path"])
        case = (study.parent / read(study)["case_dir"]).resolve()
        grid = setup["grid"]
        x0, y1 = grid["x_min_m"], grid["y_max_m"]
        x1 = x0 + grid["width"] * 5
        y0 = y1 - grid["height"] * 5
        mask = np.load(case / "domain_mask.npy")
        land = np.where(mask, np.load(case / "landcover_index.npy"), -1)
        ax.imshow(land, extent=(x0, x1, y0, y1), cmap=cmap, norm=norm, interpolation="nearest")
        map_axis(ax, (x0, y0, x1, y1), f"{panel}  {village.title()} original model domain", 250)
    legend(
        fig,
        [
            Patch(color=colours[i], label=label)
            for i, label in enumerate(
                ("Outside domain", "Inert", "Open fuel", "Woody fuel", "Roof")
            )
        ],
    )
    save(fig, dest, "fig-s01-model-domains")


def wind_outcomes(dest):
    data = rows(GEOMETRY_RESULTS / "paired-cases.csv")
    winds = ("calm", "N2", "N4", "E2", "E4", "S2", "S4", "W2", "W4")
    labels = ("Calm", "N 2", "N 4", "E 2", "E 4", "S 2", "S 4", "W 2", "W 4")
    fig, axes = plt.subplots(2, 2, figsize=(8.8, 4.5), sharex=True, layout="constrained")
    for i, village in enumerate(VILLAGES):
        for j, stratum in enumerate(("near", "far")):
            ax = axes[i, j]
            group = [r for r in data if r["village"] == village and r["stratum"] == stratum]
            panels = []
            for wind in winds:
                cases = [r for r in group if r["wind_id"].lower() == wind.lower()]
                panels.append(
                    [
                        100
                        * sum(int(r[f"{layout}_core_ignited_count"]) > 0 for r in cases)
                        / len(cases)
                        for layout in ("wide", "compact")
                    ]
                )
            ax.bar(range(len(winds)), [p[1] for p in panels], color=NEAR, width=0.55)
            ax.scatter(range(len(winds)), [p[0] for p in panels], color=HERITAGE, s=14, zorder=3)
            ax.set(ylim=(-5, 105), yticks=(0, 25, 50, 75, 100))
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
            ax.set_title(
                f"{'abcd'[i * 2 + j]}  {village.title()} {stratum}"
                f" ({len(group) // 9} roofs per wind)",
                loc="left",
                weight="semibold",
            )
            ax.set_xticks(range(len(winds)), labels)
    axes[1, 0].set_xlabel("Wind from direction and speed (m/s)")
    axes[1, 1].set_xlabel("Wind from direction and speed (m/s)")
    legend(
        fig,
        [
            Patch(color=NEAR, label="Compact"),
            Line2D([], [], marker="o", lw=0, color=HERITAGE, label="Multipoint wide"),
        ],
    )
    save(fig, dest, "fig-s04-wind-outcomes")


def asset_outcomes(dest):
    data = rows(GEOMETRY_RESULTS / "by-heritage.csv")
    asset_names = {
        "B0035": "Pingyan  bridge B0035",
        "B0036": "Pingyan  bridge B0036",
        "B0166": "Pingyan  drum tower B0166",
        "B0120": "Batuan  bridge B0120",
        "B0073": "Batuan  drum tower B0073",
    }
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.4), sharey=True, layout="constrained")
    for panel, (axis, metric, title) in enumerate(
        zip(
            axes,
            ("compact_ignited_cases", "compact_maximum_rise_K"),
            ("Cases igniting the designated roof", "Maximum valid surface temperature rise"),
            strict=True,
        )
    ):
        for i, asset in enumerate(asset_names):
            group = {r["stratum"]: r for r in data if r["heritage_id"] == asset}
            for stratum, marker, offset, color in (
                ("near", "o", -0.12, NEAR),
                ("far", "^", 0.12, FAR),
            ):
                record = group[stratum]
                value = float(record[metric])
                if panel == 0:
                    value = 100 * value / int(record["pairs"])
                axis.scatter(value, i + offset, color=color, marker=marker, s=26, zorder=3)
        axis.set_title(f"{'ab'[panel]}  {title}", loc="left", weight="semibold")
        axis.set_xlabel("Share of cases" if panel == 0 else "Temperature rise (K)")
        axis.grid(axis="x", color="#e2e7eb", linewidth=0.75)
    axes[0].set(
        xticks=(0, 15, 30, 45, 60), yticks=range(len(asset_names)), yticklabels=asset_names.values()
    )
    axes[0].xaxis.set_major_formatter(PercentFormatter(xmax=100))
    axes[1].set(xlim=(-5, 233), xticks=(0, 50, 100, 150, 200))
    axes[0].invert_yaxis()
    legend(
        fig,
        [
            Line2D([], [], marker="o", lw=0, color=NEAR, label="Near compact"),
            Line2D([], [], marker="^", lw=0, color=FAR, label="Far compact"),
        ],
    )
    save(fig, dest, "fig-s05-heritage-outcomes")


def radius_outcomes(dest):
    data = rows(WIND / "radius-pairs.csv")
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.4), sharey=True, layout="constrained")
    fields = (
        ("delta_core_ignited_count", "Change in designated roofs igniting"),
        ("delta_roof_burned_out_m2", "Change in all-roof burnout (m²)"),
    )
    for panel, (axis, (field, title)) in enumerate(zip(axes, fields, strict=True)):
        axis.axvline(0, color="#919ca7", linewidth=0.7)
        for i, (plan, _) in enumerate(LAYOUTS):
            for j, (village, color) in enumerate(zip(VILLAGES, (FAR, NEAR), strict=True)):
                group = sorted(
                    float(r[field])
                    for r in data
                    if r["village"] == village and r["plan_id"] == plan
                )
                if len(group) != 4:
                    raise ValueError("Unexpected number of paired radius cases")
                axis.scatter(
                    group, i + (j - 0.5) * 0.28 + np.linspace(-0.09, 0.09, 4), color=color, s=16
                )
        axis.set(xlabel="25 m minus 15 m at 60 min")
        axis.set_title(f"{'ab'[panel]}  {title}", loc="left", weight="semibold")
        axis.grid(axis="x", linewidth=0.7, color="#e2e7eb")
    axes[0].set(yticks=range(len(LAYOUTS)), yticklabels=[label for _, label in LAYOUTS])
    axes[0].set_xticks(sorted({int(row["delta_core_ignited_count"]) for row in data}))
    axes[0].invert_yaxis()
    legend(
        fig,
        [
            Line2D([], [], marker="o", lw=0, color=FAR, label="Pingyan"),
            Line2D([], [], marker="o", lw=0, color=NEAR, label="Batuan"),
        ],
    )
    save(fig, dest, "fig-s06-radius-sensitivity")


def reference_replays(dest):
    archives = (
        ("paired-late-ignition-reference-check.json", "Pingyan near-006, E 4 m/s"),
        ("paired-late-ignition-reference-check-batuan.json", "Batuan near-003, W 2 m/s"),
    )
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 4.2), layout="constrained")
    for panel, (axis, (file, title)) in enumerate(zip(axes, archives, strict=True)):
        archive = read(GEOMETRY_RESULTS / file)
        for case in archive["cases"]:
            wide = case["case_id"].endswith("_multipoint-wide")
            i, color = (0, HERITAGE) if wide else (1, NEAR)
            terminal = case["observation_at_extinction"]
            finish = terminal["elapsed_simulated_s"] / 3600
            axis.plot([0, finish], [i, i], lw=2, color=color, alpha=0.5)
            axis.scatter(finish, i, color=color, marker="D", s=22)
            for asset, result in terminal["core_outcomes"].items():
                if result["first_ignition_s"] is not None:
                    event = result["first_ignition_s"] / 3600
                    axis.scatter(event, i, color=color, marker="o", s=25)
                    axis.annotate(asset, (event, i), xytext=(2, 9), textcoords="offset points")
        axis.axvline(1, ls="--", lw=0.8, color="#8797a3")
        axis.set(
            xlim=(-0.15, axis.get_xlim()[1]),
            ylim=(1.53, -0.45),
            yticks=(0, 1),
            yticklabels=("Multipoint wide", "Compact"),
        )
        axis.set_title(f"{'ab'[panel]}  {title}", loc="left", weight="semibold")
        axis.set_xlabel("Time since prescribed ignition (h)")
    legend(
        fig,
        [
            Line2D(
                [], [], marker="o", lw=0, color=NEAR, label="First ignition of the labeled roof"
            ),
            Line2D([], [], marker="D", lw=0, color=HERITAGE, label="Certified extinction"),
            Line2D([], [], ls="--", lw=0.8, color="#8797a3", label="60 min"),
        ],
    )
    save(fig, dest, "fig-s07-reference-replays")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEST)
    dest = parser.parse_args().output
    dest.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.labelcolor": INK,
            "text.color": INK,
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    reg = read(GEOMETRY_RESULTS / "registration.json")
    geography = {v: features(v) for v in VILLAGES}
    study_maps(dest, reg, geography)
    position_test(dest)
    terminal_results(dest)
    clearance_maps(dest, reg, geography)
    plot_geometry_results(
        dest,
        load_geometry_results(GEOMETRY_RESULTS),
        stem="fig-05-compact-performance",
        panels="ab",
        dpi=320,
    )
    domain_maps(dest, reg)
    wind_outcomes(dest)
    asset_outcomes(dest)
    radius_outcomes(dest)
    reference_replays(dest)


if __name__ == "__main__":
    main()
