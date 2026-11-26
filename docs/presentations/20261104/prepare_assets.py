"""Read archived evidence and render research figures for the project presentation.

This script does not launch a fire simulation or modify any archived experiment.
"""

# ruff: noqa: RUF001 -- Preserve the published Chinese axis labels.
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".mpl-cache"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Polygon as PatchPolygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ASSETS = OUT / "assets"
ASSETS.mkdir(exist_ok=True)
font_manager.fontManager.addfont("/System/Library/Fonts/STHeiti Medium.ttc")
plt.rcParams.update(
    {
        "font.family": font_manager.FontProperties(
            fname="/System/Library/Fonts/STHeiti Medium.ttc"
        ).get_name(),
        "axes.unicode_minus": False,
        "font.size": 14,
        "svg.fonttype": "none",
    }
)
C = {
    "ink": "#26332F",
    "teal": "#167D83",
    "fire": "#C95B37",
    "gray": "#6B7371",
    "muted": "#D3D7D3",
    "gold": "#E4B05B",
    "paper": "#F7F3EA",
}
spec = importlib.util.spec_from_file_location(
    "archived_followup", ROOT / "scripts/summarize_heritage_followup_20261103.py"
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def save(fig, name, transparent=False):
    for ext in ("png", "svg"):
        fig.savefig(
            ASSETS / f"{name}.{ext}",
            dpi=180,
            bbox_inches="tight",
            pad_inches=0.06,
            transparent=transparent,
        )
    plt.close(fig)


def equation(name, rows, size=29):
    fig = plt.figure(figsize=(13.2, max(0.75, 0.78 * len(rows))))
    for i, text in enumerate(rows):
        fig.text(
            0.02, 1 - (i + 0.7) / len(rows), f"${text}$", fontsize=size, color=C["ink"], va="center"
        )
    save(fig, name, True)


def polys(ax, geometry, **style):
    geoms = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for g in geoms:
        ax.add_patch(PatchPolygon(np.asarray(g.exterior.coords), **style))


def scale_bar(ax, bounds):
    x0, y0, x1, y1 = bounds
    length = 20 if x1 - x0 < 220 else 100
    x, y = x0 + 0.06 * (x1 - x0), y0 + 0.06 * (y1 - y0)
    ax.plot([x, x + length], [y, y], color=C["ink"], lw=3)
    ax.text(
        x + length / 2,
        y + 0.025 * (y1 - y0),
        f"{length} m",
        ha="center",
        fontsize=12,
        color=C["ink"],
    )


def village_map(v, name, core=None):
    geos = v["geometries"]
    plan = next(p for p in v["experiment"]["config"]["plans"] if p["plan_id"] == "selected")
    cores = [t["building_id"] for t in v["inventory"]["targets"]]
    if core:
        near = [geos[i] for i in plan["demolished_ids"] if geos[i].distance(geos[core]) <= 20]
        focus = unary_union([geos[core], *near])
        x0, y0, x1, y1 = focus.bounds
        pad = max(16, 0.22 * max(x1 - x0, y1 - y0))
    else:
        x0, y0, x1, y1 = unary_union(list(geos.values())).bounds
        pad = 0.035 * max(x1 - x0, y1 - y0)
    bounds = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    ax.set_aspect("equal")
    for g in geos.values():
        polys(ax, g, facecolor="#EAEEE9", edgecolor="#BDC7C0", linewidth=0.6)
    for bid in plan["demolished_ids"]:
        polys(ax, geos[bid], facecolor=C["fire"], edgecolor="white", linewidth=0.8, alpha=0.9)
        if core and geos[bid].distance(geos[core]) <= 20:
            p = geos[bid].representative_point()
            ax.text(
                p.x,
                p.y,
                bid,
                ha="center",
                va="center",
                fontsize=12,
                color="white",
                fontweight="bold",
            )
    for bid in cores:
        polys(ax, geos[bid], facecolor=C["teal"], edgecolor="white", linewidth=1.2)
        if not core or core == bid:
            p = geos[bid].representative_point()
            ax.annotate(
                bid,
                (p.x, p.y),
                xytext=(6, 7),
                textcoords="offset points",
                color=C["teal"],
                fontsize=13,
                fontweight="bold",
                bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none", "pad": 1.2},
            )
    ax.set(xlim=bounds[::2], ylim=bounds[1::2])
    scale_bar(ax, bounds)
    ax.axis("off")
    fig.tight_layout(pad=0.1)
    save(fig, name, True)


def morphology(v, fire_id, name, core):
    geos = v["geometries"]
    fire = v["experiment"]["problem"]["fires"][fire_id]
    from shapely.geometry import shape

    sources = [shape(x["geometry"]) for x in fire["footprints"].values()]
    selected_ids = set(v["excluded_intervention_union_ids"])
    focus = unary_union(
        [
            geos[core],
            *sources,
            *[geos[i] for i in selected_ids if geos[i].distance(geos[core]) <= 18],
        ]
    )
    x0, y0, x1, y1 = focus.bounds
    pad = max(8, 0.16 * max(x1 - x0, y1 - y0))
    bounds = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    cmap = ListedColormap([C["gold"], "#E47D48", "#AA3441"])
    norm = BoundaryNorm([0, 1200, 2400, 3600.00001], 3)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.08, top=0.91, wspace=0.04)
    names = ["无干预", "既有方案", "近等名义面积对照"]
    for arm, ax, title in zip(report.ARMS, axes, names, strict=True):
        for g in geos.values():
            polys(ax, g, fill=False, edgecolor="#CAD1CD", linewidth=0.55)
        rec = v["records"][(fire_id, arm, 0.5)]
        mask = rec["roofs"] & np.isfinite(rec["ignition"]) & (rec["ignition"] <= 3600)
        xy = rec["layout"]["xy_m"][mask]
        ax.scatter(
            xy[:, 0],
            xy[:, 1],
            c=rec["ignition"][mask],
            s=5,
            marker="s",
            cmap=cmap,
            norm=norm,
            lw=0,
            rasterized=True,
        )
        polys(ax, geos[core], fill=False, edgecolor=C["teal"], linewidth=2)
        p = geos[core].representative_point()
        ax.text(
            p.x,
            p.y,
            core,
            color=C["teal"],
            fontsize=12,
            fontweight="bold",
            ha="center",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "pad": 1},
        )
        plan = next(p for p in v["experiment"]["config"]["plans"] if p["plan_id"] == arm)
        for bid in plan["demolished_ids"]:
            polys(ax, geos[bid], fill=False, edgecolor=C["ink"], linewidth=1.2, linestyle="--")
        for g in sources:
            polys(ax, g, fill=False, edgecolor="#7253A2", linewidth=1.6)
        ax.set(xlim=bounds[::2], ylim=bounds[1::2], aspect="equal", title=title)
        ax.axis("off")
    save(fig, name)


equation(
    "eq_hrr",
    [
        r"\dot Q_i(\tau)=\dot Q_{i,\max}(\tau/t_g)^2\quad 0\leq\tau<t_g",
        r"\dot Q_i(\tau)=\dot Q_{i,\max}\quad t_g\leq\tau<t_g+t_s",
        r"\dot Q_i(\tau)=\dot Q_{i,\max}[1-(\tau-t_g-t_s)/t_d]\quad t_g+t_s\leq\tau<t_g+t_s+t_d",
    ],
    25,
)
equation(
    "eq_energy",
    [
        r"E_{i,0}=m_{i,\mathrm{dry}}f_{i,\mathrm{pyro}}H_i",
        r"\Delta E_i=\min\left[\int_{\tau}^{\tau"
        r"+\Delta t}\dot Q_i(u)du,\;m_{i,\mathrm{remain}}H_i\right]",
        r"\Delta m_i=\Delta E_i/H_i,\qquad \overline{\dot Q}_i=\Delta E_i/\Delta t",
    ],
    28,
)
equation(
    "eq_heat",
    [
        r"q''_{i,\mathrm{net}}=\alpha_i q''_{i,\mathrm{rad}}+q''_{i,\mathrm{contact}}",
        r"\qquad -h_{a,i}^{\mathrm{eff}}(T_i-T_\infty)"
        r"-\epsilon_i\sigma(T_i^4-T_\infty^4)-k_{b,i}(T_i-T_\infty)",
    ],
    28,
)
equation(
    "eq_dryheat",
    [
        r"\Delta T_i=\frac{q''_{i,\mathrm{net}}\Delta t}{m''_{i,"
        r"\mathrm{heated}}c_i}\quad\mathrm{(dry\ heating)}",
        r"\Delta m''_{w,i}=\min\left(m''_{w,i},E''_{"
        r"\mathrm{available}}/L_v\right)\quad\mathrm{(evaporation)}",
    ],
    28,
)
equation(
    "eq_rad",
    [
        r"w_{s\to t}=A_{t,\mathrm{rec}}\sum_{p,q}\lambda_{s,p}\lambda_{t,q}\frac{e^{"
        r"-\kappa d_{pq}}}{4\pi(d_{pq}^2+\varepsilon_r^2)}",
        r"P_{t,\mathrm{rad}}=\sum_s\chi_s\overline{\dot Q}_s w_{s\to t},"
        r"\qquad q''_{t,\mathrm{rad}}=P_{t,\mathrm{rad}}/A_{t,\mathrm{cov}}",
    ],
    27,
)
equation(
    "eq_contact",
    [
        r"P_{s\to t}^{\mathrm{demand}}=\phi_{st}A_{t,\mathrm{cov}}h_c\max(T_g-T_t,0)",
        r"\sum_s\phi_{st}\leq1,\qquad\sum_t P_{s\to t}^{\mathrm{contact}}\leq(1"
        r"-\chi_s)\overline{\dot Q}_s",
    ],
    28,
)
equation(
    "eq_ign",
    [
        r"\mathrm{ignite}_i=\mathrm{susceptible}_i\wedge"
        r"\mathrm{burnable}_i\wedge\mathrm{allowed}_i",
        r"\qquad\wedge\left(m_{i,\mathrm{remain}}>m_{i,\mathrm{tol}}"
        r"\right)\wedge\left(T_i^{n+1}\geq T_{i,\mathrm{ign}}\right)",
    ],
    29,
)
equation(
    "eq_objective",
    [
        r"\min_{x_i\in\{0,1\}}\ \left(\sum_i x_i,\;"
        r"\sum_i a_i x_i\right)\quad\mathrm{lexicographic}",
        r"\mathrm{subject\ to}\ \ I_{c,s,h}(x;T)=0\quad\forall c,\ s,\ h\in\{1.0,0.5\}\ \mathrm{m}",
    ],
    28,
)
equation(
    "eq_bound",
    [
        r"LB_A(P)=\sum_{i\in P}a_i+\sum_{j\in\mathrm{cheapest}(R,k-|P|)}a_j",
        r"LB_A(P)\geq A_{\mathrm{incumbent}}\ \Longrightarrow"
        r"\ \mathrm{prune\ at\ the\ same\ cardinality}",
    ],
    26,
)
equation(
    "eq_cut",
    [
        r"A\subseteq D,\quad D\cap H=\varnothing\ "
        r"\Longrightarrow\ \mathrm{same\ certified\ failure}",
        r"\sum_{i\in A}(1-x_i)+\sum_{i\in H}x_i\geq1",
    ],
    29,
)

t = np.linspace(0, 850, 851)
g, s, d = 120, 395.36, 300
y = np.where(t < g, (t / g) ** 2, np.where(t < g + s, 1, np.maximum(0, 1 - (t - g - s) / d))) * 300
fig, ax = plt.subplots(figsize=(7.2, 4))
ax.plot(t, y, color=C["fire"], lw=3)
ax.fill_between(t, 0, y, color=C["gold"], alpha=0.35)
for x in [g, g + s, g + s + d]:
    ax.axvline(x, color="#B6C0B8", ls="--", lw=1)
ax.set(
    xlabel="点燃后的燃烧龄期 τ（s）", ylabel="热释放率密度（kW/m²）", xlim=(0, 850), ylim=(0, 335)
)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", alpha=0.18)
save(fig, "hrr_curve", True)

with (ROOT / "docs/results/heritage-followup-20261103/roof-ignition-curves.csv").open(
    encoding="utf-8-sig"
) as f:
    curve_rows = list(csv.DictReader(f))
for vid, fire in [("batuan", "bridge-B0062-known"), ("pingyan", "drum-B0164-late-ignition")]:
    fig, ax = plt.subplots(figsize=(8.5, 4.65))
    for arm, color, label in [
        ("baseline", C["gray"], "无干预"),
        ("selected", C["teal"], "既有方案"),
        ("area-matched-position", C["fire"], "位置对照"),
    ]:
        for scale, ls in [(1.0, "--"), (0.5, "-")]:
            rows = [
                r
                for r in curve_rows
                if r["village_id"] == vid
                and r["fire_id"] == fire
                and r["plan_id"] == arm
                and float(r["scale_m"]) == scale
            ]
            ax.step(
                [float(r["time_s"]) / 60 for r in rows],
                [float(r["roof_area_m2"]) for r in rows],
                where="post",
                label=f"{label} · {scale:g} m",
                color=color,
                ls=ls,
                lw=2,
            )
    ax.set(xlim=(0, 60), xlabel="起火后时间（min）", ylabel="累计引燃屋面面积（m²）")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.2)
    ax.tick_params(labelsize=20)
    ax.xaxis.label.set_size(21)
    ax.yaxis.label.set_size(21)
    save(fig, vid + "_curve", True)

villages = {vid: report.load_village(vid, ROOT / "runs/research") for vid in ["batuan", "pingyan"]}
for vid, v in villages.items():
    village_map(v, vid + "_overview")
    for target in v["inventory"]["targets"]:
        village_map(v, vid + "_" + target["building_id"], target["building_id"])
morphology(villages["batuan"], "bridge-B0062-known", "batuan_bridge_morph", "B0120")
morphology(villages["pingyan"], "drum-B0164-late-ignition", "pingyan_drum_morph", "B0166")

assets = {
    p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    for p in sorted(ASSETS.glob("*"))
}
(OUT / "asset-manifest.json").write_text(
    json.dumps(
        {
            "source": "archived followup data + explicit formula schematics",
            "new_CA_runs": 0,
            "assets": assets,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(json.dumps({"assets": len(assets), "new_CA_runs": 0, "output": str(ASSETS)}))
