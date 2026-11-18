"""Editable vector mechanism figure for the 90-minute protection algorithm.

All geometry miniatures are mechanism schematics, not optimized field layouts.
No synthetic numerical performance is presented as an experimental result.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

ROOT = Path(__file__).resolve().parents[1]
NAVY, TEAL, ORANGE, GREY = "#17394d", "#168e83", "#db8654", "#74838e"


def main():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(8, 3.6))
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set(xlim=(0, 16), ylim=(0, 7.2))
    ax.axis("off")

    def text(x, y, s, size=10, color=NAVY, weight="normal", ha="left", va="center"):
        ax.text(
            x,
            y,
            s,
            fontsize=max(8, size * 0.8),
            color=color,
            fontweight=weight,
            ha=ha,
            va=va,
            linespacing=1.5,
        )

    def panel(x, y, w, h, fill="#f2f7fa", edge="#bfcfd9", dash=False):
        patch = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.025,rounding_size=0.12",
            facecolor=fill,
            edgecolor=edge,
            linewidth=1.2,
            linestyle="--" if dash else "-",
        )
        ax.add_patch(patch)

    def arrow(a, b, color=TEAL, style="-", rad=0):
        ax.add_patch(
            FancyArrowPatch(
                a,
                b,
                arrowstyle="-|>",
                mutation_scale=12,
                linewidth=1.5,
                color=color,
                linestyle=style,
                connectionstyle=f"arc3,rad={rad}",
            )
        )

    text(
        0.35,
        6.85,
        "Protection-constrained village firebreak design",
        19,
        weight="bold",
    )
    text(
        0.35,
        6.46,
        "Fixed spatial evidence and physics  →  free-geometry search  →  independent checks",
        11,
        GREY,
    )
    xs = [0.35, 4.28, 8.21, 12.14]
    w = 3.48
    top = 3.05
    h = 2.95
    for x in xs:
        panel(x, top, w, h)
    titles = [
        "(a) Spatial evidence",
        "(b) Thermal & fuel CA",
        "(c) Corridor design",
        "(d) Protection first",
    ]
    for x, title in zip(xs, titles, strict=True):
        text(x + 0.17, 5.70, title, 12, weight="bold")
    text(xs[0] + 0.18, 5.18, "Satellite + manual roofs\nTerrain + fuel evidence", 10)
    for j in range(8):
        for k in range(3):
            ax.add_patch(
                plt.Rectangle(
                    (xs[0] + 0.25 + j * 0.32, 3.35 + k * 0.30),
                    0.30,
                    0.28,
                    fc=["#d7e6c9", "#a9c9a1", "#91b6a1", "#e5dcc5"][(j + 2 * k) % 4],
                    ec="white",
                    lw=0.5,
                )
            )
    for dx, dy in ((0.58, 0.08), (1.05, 0.38), (1.72, 0.15), (2.12, 0.5)):
        x = xs[0] + 0.25 + dx
        y = 3.35 + dy
        ax.add_patch(
            Polygon(
                [(x, y), (x + 0.32, y), (x + 0.32, y + 0.22), (x + 0.16, y + 0.34), (x, y + 0.22)],
                fc="#d0af89",
                ec=NAVY,
                lw=1,
            )
        )
    text(xs[0] + 0.2, 4.49, "Aligned cells + evidence", 9, TEAL)
    text(
        xs[1] + 0.18,
        5.05,
        "Heating → drying → ignition\nFinite-fuel heat + burnout",
        10,
    )
    text(
        xs[1] + 0.18,
        4.34,
        "Contact heat + radiation\nHouses + bridge as targets",
        10,
    )
    panel(xs[1] + 0.18, 3.35, 3.08, 0.48, fill="#e5f3ef", edge="#a0cbbf")
    text(xs[1] + 1.72, 3.59, "CA solve for each layout", 9, TEAL, ha="center")
    text(
        xs[2] + 0.18,
        5.12,
        "Position · angle · width\nVertices · combinations",
        10,
    )
    for dy, coords in (
        (4.25, [(0.0, 0.0), (0.72, 0.0)]),
        (3.88, [(0.0, 0.0), (0.34, 0.17), (0.72, 0.0)]),
    ):
        ax.plot(
            [xs[2] + 0.3 + p[0] for p in coords],
            [dy + p[1] for p in coords],
            color=TEAL,
            lw=8,
            alpha=0.20,
            solid_capstyle="butt",
        )
        ax.plot(
            [xs[2] + 0.3 + p[0] for p in coords], [dy + p[1] for p in coords], color=TEAL, lw=1.4
        )
    text(xs[2] + 1.2, 4.25, "Single straight", 9)
    text(xs[2] + 1.2, 3.95, "Single polyline", 9)
    for yy in (3.42, 3.61):
        ax.plot([xs[2] + 0.3, xs[2] + 1.02], [yy, yy + 0.09], color=TEAL, lw=5, alpha=0.25)
    text(xs[2] + 1.2, 3.53, "Multiple corridors", 9)
    text(xs[3] + 0.18, 5.12, r"$H_s(90\,\mathrm{min})=0$", 13, TEAL)
    text(xs[3] + 0.18, 4.66, r"$B_s(90\,\mathrm{min})=0$", 13, TEAL)
    text(xs[3] + 0.18, 4.21, "For every training scenario", 9, GREY)
    text(xs[3] + 0.18, 3.63, r"Then minimize $A_{\rm clear}$", 12, weight="bold")
    text(xs[3] + 0.18, 3.25, "Actual clearance union", 8.5, GREY)
    for i in range(3):
        arrow((xs[i] + w + 0.035, 4.76), (xs[i + 1] - 0.06, 4.76))
    arrow((13.70, 3.02), (9.93, 3.02), rad=-0.24)
    text(11.80, 2.42, "Beam + local refinement", 10, TEAL, ha="center")
    arrow((9.63, 2.91), (6.06, 2.91), rad=-0.19)
    text(7.85, 2.42, "Layout → CA re-evaluation", 9, TEAL, ha="center")
    bottoms = [
        (0.35, 3.48, "(e) Freeze selection", "Frozen plans + checksum", False),
        (
            4.28,
            3.48,
            "(f) Independent checks",
            "New ignitions · rise -5%\nFull grid · same controls",
            False,
        ),
        (
            8.21,
            3.48,
            "(g) Validate & extend",
            "Calibration · wind / embers\nGrid size · village transfer",
            True,
        ),
        (
            12.14,
            3.48,
            "(h) Design rules",
            "Width · continuity · siting\nProtection + failure bounds",
            True,
        ),
    ]
    for x, ww, title, body, planned in bottoms:
        panel(
            x,
            0.57,
            ww,
            1.53,
            fill="#faf4ed" if not planned else "#f7f8f9",
            edge="#d7b590" if not planned else "#b1bdc5",
            dash=planned,
        )
        text(x + 0.17, 1.81, title, 11, weight="bold")
        text(x + 0.17, 1.18, body, 9.5, ORANGE if not planned else GREY)
    for i in range(3):
        arrow(
            (xs[i] + w + 0.035, 1.35),
            (xs[i + 1] - 0.06, 1.35),
            ORANGE if i == 0 else GREY,
            style="-" if i == 0 else "--",
        )
    # Evaluation/selection results enter a distinct, one-way validation branch.
    ax.add_patch(
        FancyArrowPatch((15.69, 3.7), (15.69, 2.28), arrowstyle="-", linewidth=1.2, color=ORANGE)
    )
    ax.plot([15.69, 0.52], [2.28, 2.28], color=ORANGE, lw=1.2)
    arrow((0.52, 2.28), (0.52, 2.11), ORANGE)
    text(
        0.35,
        0.24,
        (
            "Solid: implemented. Dashed: next research. Re-optimize after model updates. "
            "Geometry is schematic."
        ),
        8,
        GREY,
    )
    destination = ROOT / "docs/figures"
    destination.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "png"):
        fig.savefig(
            destination / f"batuan-protection90-method-v2.{suffix}", dpi=220, facecolor="white"
        )
    plt.close(fig)


if __name__ == "__main__":
    main()
