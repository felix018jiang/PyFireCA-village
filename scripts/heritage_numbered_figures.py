"""Render the numbered paper edition from the original plotters, GIS layers and field photos."""

# ruff: noqa: RUF001 -- The original Chinese plot labels are literal lookup keys.

import re
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import plot_heritage_geometry_validation as compact
import plot_heritage_midbudget_routes as routes
import render_npj_heritage_outline_figures as outline
from heritage_numbered_panels import (
    export_panel,
    extinction_replays,
    model_and_sites,
    structure_types,
)
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.text import Text
from PIL import ImageFont
from render_heritage_field_plate import draw_plate

# These filenames belong to the original generators. The numbered edition moves
# supplementary illustrations next to their first discussion in the manuscript.
FIGURES = {
    "fig-s01-model-domains": 3,
    "local-position": 4,
    "standard-layouts": 5,
    "fig-02-position-controls": 7,
    "fig-03-terminal-strategies": 8,
    "fig-04-clearance-geometries": 9,
    "fig-05-compact-performance": 10,
    "fig-s04-wind-outcomes": 11,
    "走线与清理换位": 13,
    "终态配对结果": 14,
    "fig-s06-radius-sensitivity": 15,
}
LABELS = {
    "Heritage narrow": "Heritage, narrow",
    "Heritage wide": "Heritage, wide",
    "Multipoint narrow": "Multipoint, narrow",
    "Multipoint wide": "Multipoint, wide",
    "Inert": "Noncombustible ground",
    "Open fuel": "Open-ground fuel",
    "Woody fuel": "Woody surface fuel",
    "Roof": "Equivalent roof",
    "a  Drum tower near its design source": "a  Drum tower scenario",
    "b  Wind-and-rain bridge near its design source": "b  Wind-and-rain bridge scenario",
    "Selected position": "Original site",
    "Alternative position": "Relocated site",
    "Time since prescribed ignition (min)": "Time since ignition (min)",
    "Cases with heritage ignition": "Scenarios with heritage ignition",
    "Wind from direction and speed (m/s)": "Wind direction (from) and speed (m/s)",
    "a  Change in designated roofs igniting": "a  Change in heritage structures ignited",
    "b  Change in all-roof burnout (m²)": "b  Change in roof area burned out (m²)",
    "25 m minus 15 m at 60 min": "25 m minus 15 m radius at 60 min",
    "Removed roof area": "Cleared ordinary roofs",
    "Cleared ground footprint": "Cleared ground",
    "Heritage roof": "Designated heritage roof",
    "a   Heritage ignition to extinction": "a   Heritage ignition by extinction",
    "Share of roof and wind combinations": "Share of roof–wind combinations",
    "Burned": "Burned out",
    "Cleared or burned": "Cleared or burned out",
    "原多点宽围护": "Wide multipoint firebreak",
    "均匀收窄": "Uniform narrowing",
    "沿不燃地表改线": "Ground-aligned route",
    "底图不燃地表": "Noncombustible ground",
    "固定围护点屋面": "Fixed anchor roofs",
    "其他屋面": "Other roofs",
    "仅均匀线清除的屋面": "Cleared only by uniform narrowing",
    "仅改线清除的屋面": "Cleared only by rerouting",
    "平岩": "Pingyan",
    "岜团": "Batuan",
    "平岩近源": "Pingyan · near",
    "平岩远源": "Pingyan · far",
    "岜团近源": "Batuan · near",
    "岜团远源": "Batuan · far",
    "占原多点宽围护清理量的比例（%）": "Share of wide multipoint clearance (%)",
    "中档路线减原宽围护（m²）": "Mid-budget route minus wide multipoint (m²)",
    "a   实际清理总面积": "a   Total area cleared",
    "b   普通屋面平均总损失差": "b   Mean difference in total ordinary roof loss",
    "a   平岩  ·  围护范围": "a   Pingyan · firebreak extent",
    "b   平岩  ·  清理换位局部": "b   Pingyan · changes in roof clearance",
    "c   岜团  ·  围护范围": "c   Batuan · firebreak extent",
    "d   岜团  ·  清理换位局部": "d   Batuan · changes in roof clearance",
}


def english_label(value):
    if value in LABELS:
        return LABELS[value]
    counts = re.fullmatch(
        r"指定遗产引燃（(\d+) 组）\s+原宽围护 (\d+) 场"
        r"\s+均匀收窄 (\d+) 场\s+沿不燃地表改线 (\d+) 场",
        value,
    )
    if counts:
        total, wide, uniform, aligned = counts.groups()
        return (
            f"Heritage ignitions in {total} scenarios per route at a 15 m radiation search radius\n"
            f"Wide multipoint  {wide}         Uniform narrowing  {uniform}"
            f"         Ground-aligned route  {aligned}"
        )
    footprint = re.fullmatch(
        r"([abcd])  (Pingyan|Batuan) (multipoint wide|compact)  \(([\d,]+ m²)\)", value
    )
    if footprint:
        letter, village, plan, area = footprint.groups()
        layout = "Wide multipoint" if plan == "multipoint wide" else "Compact"
        return f"{letter}  {village} · {layout}\n     {area} cleared"
    sample = re.fullmatch(
        r"([a-d])  (Pingyan|Batuan) (near|far) sources  \(n = (\d+) per layout\)", value
    )
    if sample:
        letter, village, stratum, count = sample.groups()
        return f"{letter}  {village} · {stratum} ({count} per layout)"
    return value


def source_plots(dest, reg, geo):
    """Localize Matplotlib text before layout/rasterization, using the original draw calls."""
    savefig = Figure.savefig
    written = set()

    def export(figure, path, *args, **kwargs):
        path = Path(path)
        if path.suffix == ".pdf":
            return
        number = FIGURES[path.stem]
        if number in written:
            raise ValueError(f"Duplicate figure {number}")
        # A draw regenerates tick strings from their formatter. Translate its
        # fixed labels as well as the ordinary Text artists.
        for ax in figure.axes:
            for axis in (ax.xaxis, ax.yaxis):
                old = [t.get_text() for t in axis.get_ticklabels()]
                new = [english_label(s) for s in old]
                if old != new:
                    axis.set_ticks(axis.get_ticklocs(), labels=new)
        for artist in figure.findobj(match=Text):
            text = english_label(artist.get_text())
            if re.search(r"[\u3400-\u9fff]", text):
                raise ValueError(f"Untranslated Figure {number} label: {text}")
            artist.set_text(text)
            artist.set_fontfamily("DejaVu Sans")
        if number == 13:
            legend = figure.legends[0]
            legend.set_ncols(4)
            for entry in legend.get_texts():
                entry.set_fontsize(8.4)
        kwargs.update(dpi=320, bbox_inches="tight", facecolor="white", pad_inches=0.07)
        savefig(figure, dest / f"Figure-{number:02d}.png", *args, **kwargs)
        written.add(number)

    # The archived plotters save directly, including two plots to a frozen
    # results directory. Redirect only during this render and keep those files intact.
    with (
        patch.object(Figure, "savefig", export),
        plt.rc_context(
            {
                "font.size": 9.5,
                "axes.titlesize": 10.3,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.labelcolor": outline.INK,
                "text.color": outline.INK,
            }
        ),
    ):
        outline.domain_maps(dest, reg)
        positional_comparison(dest, geo)
        standard_maps(dest, reg, geo)
        outline.position_test(dest)
        outline.terminal_results(dest)
        outline.clearance_maps(dest, reg, geo)
        compact.plot(
            dest,
            compact.load(outline.GEOMETRY_RESULTS),
            stem="fig-05-compact-performance",
            panels="ab",
        )
        outline.wind_outcomes(dest)
        outline.radius_outcomes(dest)
        routes.main()
    if written != set(FIGURES.values()):
        raise ValueError(f"Missing original plots: {set(FIGURES.values()) - written}")


def positional_comparison(dest, geography):
    """English edition of the published positional_comparison() map and curves."""
    areas = outline.rows(outline.POSITION / "intervention-areas.csv")
    curves = outline.rows(outline.POSITION / "roof-ignition-curves.csv")
    fig, axes = plt.subplots(
        2, 3, figsize=(10.5, 6.9), layout="constrained", gridspec_kw={"width_ratios": (1.05, 1, 1)}
    )
    plans = (
        ("baseline", "#78838b", ":", "No clearance"),
        ("selected", outline.HERITAGE, "-", "Original site"),
        ("area-matched-position", outline.NEAR, "--", "Relocated site"),
    )
    for i, village in enumerate(("batuan", "pingyan")):
        ax = axes[i, 0]
        buildings = geography[village]
        by_id = {
            p["building_id"]: g for p, g in buildings if p["layer"] in ("building", "heritage")
        }
        selected = {}
        for plan, _, _, _ in plans[1:]:
            row = next(r for r in areas if (r["village_id"], r["plan_id"]) == (village, plan))
            selected[plan] = [by_id[key] for key in re.findall(r"B\d+", row["demolished_ids"])]
        extent = outline.bounds(selected["selected"] + selected["area-matched-position"], 32)
        outline.map_axis(ax, extent, f"{'ad'[i]}  {village.title()} · clearance sites", 30)
        outline.draw_buildings(ax, buildings)
        for plan, color, style, _ in plans[1:]:
            for geometry in selected[plan]:
                for part in outline.polygons(geometry):
                    ax.plot(
                        *part.exterior.xy,
                        color=color,
                        lw=2.2 if plan == "selected" else 1.2,
                        ls=style,
                        zorder=7,
                    )
        for j, (fire, title) in enumerate((("drum", "drum tower"), ("bridge", "bridge"))):
            ax = axes[i, j + 1]
            for plan, color, style, _ in plans:
                subset = [
                    r
                    for r in curves
                    if r["village_id"] == village
                    and r["fire_id"].startswith(fire)
                    and r["plan_id"] == plan
                    and float(r["scale_m"]) == 0.5
                    and r["valid"] == "True"
                ]
                if not subset:
                    raise ValueError(f"Missing local fire curve: {village}, {fire}, {plan}")
                ax.plot(
                    [float(r["time_s"]) / 60 for r in subset],
                    [float(r["roof_area_m2"]) for r in subset],
                    color=color,
                    ls=style,
                    lw=1.6,
                )
            ax.set_title(
                f"{'bcef'[i * 2 + j]}  {village.title()} · {title}", loc="left", weight="semibold"
            )
            ax.set(
                xlim=(0, 60),
                xticks=[0, 20, 40, 60],
                xlabel="Time since ignition (min)",
                ylabel="Cumulative roof area ignited (m²)",
            )
            ax.grid(color="#e1e6e9", linewidth=0.65)
    fig.legend(
        handles=[
            Line2D([], [], color=color, ls=style, label=name, lw=1.8)
            for _, color, style, name in plans
        ],
        loc="outside lower center",
        ncols=3,
        frameon=False,
    )
    outline.save(fig, dest, "local-position")


def standard_maps(dest, reg, geography):
    """English edition of the published standard_maps() panels and nominal bands."""
    plans = (
        ("heritage-narrow", "Heritage, narrow"),
        ("heritage-wide", "Heritage, wide"),
        ("multipoint-narrow", "Multipoint, narrow"),
        ("multipoint-wide", "Multipoint, wide"),
    )
    fig, axes = plt.subplots(2, 4, figsize=(11.2, 6.3), layout="constrained")
    for i, village in enumerate(outline.VILLAGES):
        data = geography[village]
        extent = outline.bounds(
            [g for p, g in data if p["layer"] in ("heritage", "nominal_band")], 36
        )
        for j, (plan, label) in enumerate(plans):
            ax = axes[i, j]
            outline.map_axis(
                ax, extent, f"{'abcdefgh'[4 * i + j]}  {village.title()}\n     {label}", 50
            )
            outline.draw_buildings(ax, data)
            for layer, color in (
                ("cleared_ground", outline.CLEAR_GROUND),
                ("cleared_roof", outline.CLEAR_ROOF),
            ):
                outline.fill(
                    ax,
                    [g for p, g in data if p.get("plan_id") == plan and p["layer"] == layer],
                    color,
                    zorder=4,
                )
            for p, geom in data:
                if p.get("plan_id") == plan and p["layer"] == "nominal_band":
                    for part in outline.polygons(geom):
                        ax.plot(*part.exterior.xy, color="#526c9e", lw=0.8, zorder=5)
            if plan.startswith("multipoint"):
                ids = set(reg["villages"][village]["fixed_source_ids"])
                outline.fill(
                    ax,
                    [
                        g
                        for p, g in data
                        if p.get("building_id") in ids and p["layer"] == "building"
                    ],
                    "#cdc090",
                    zorder=5,
                )
            outline.draw_buildings(ax, data, background=False)
    handles = [
        Patch(color=color, label=name)
        for color, name in (
            (outline.HERITAGE, "Designated heritage"),
            ("#cdc090", "Additional protected roof"),
            (outline.CLEAR_ROOF, "Cleared roof"),
            (outline.CLEAR_GROUND, "Cleared ground"),
        )
    ]
    handles.append(Line2D([], [], color="#526c9e", label="Nominal outer edge"))
    fig.legend(handles=handles, loc="outside lower center", ncols=5, frameon=False, fontsize=9)
    outline.save(fig, dest, "standard-layouts")


def render(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    reg = outline.read(outline.GEOMETRY_RESULTS / "registration.json")
    geo = {v: outline.features(v) for v in outline.VILLAGES}
    source_plots(dest, reg, geo)
    export_panel(dest / "Figure-01.png", (560, 590), model_and_sites)
    export_panel(dest / "Figure-06.png", (550, 182), extinction_replays)
    export_panel(dest / "Figure-12.png", (550, 165), structure_types)
    bold = font_manager.findfont(
        font_manager.FontProperties(family=["Arial", "DejaVu Sans"], weight="bold")
    )
    draw_plate(ImageFont.truetype(bold, 33)).save(
        dest / "Figure-02.jpeg", quality=94, subsampling=0, dpi=(300, 300)
    )
