"""Plot the audited terminal heritage and ordinary-roof outcomes for the manuscript."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter

DEFAULT = Path(__file__).resolve().parents[1] / "output/heritage-geometry-validation-20261118"
ORDER = (("pingyan", "near"), ("pingyan", "far"), ("batuan", "near"), ("batuan", "far"))
LABELS = ("Pingyan · near", "Pingyan · far", "Batuan · near", "Batuan · far")
WIDE = "#526477"
COMPACT = "#9a452f"
NET = "#166767"


def load(root):
    audit = json.loads((root / "evidence.json").read_text())
    data = root / "by-stratum.csv"
    if hashlib.sha256(data.read_bytes()).hexdigest() != audit["outputs_sha256"][data.name]:
        raise ValueError("The plotted table differs from the audited results")
    with data.open(encoding="utf-8-sig", newline="") as stream:
        rows = {(r["village"], r["stratum"]): r for r in csv.DictReader(stream)}
    return [rows[k] for k in ORDER]


def format_axis(axis, letter, title):
    axis.spines[["right", "top", "left"]].set_visible(False)
    axis.spines["bottom"].set_color("#9aa4ae")
    axis.tick_params(axis="y", length=0)
    axis.set_ylim(-0.6, 3.6)
    axis.invert_yaxis()
    axis.grid(axis="x", color="#dfe3e8", linewidth=0.7)
    axis.set_axisbelow(True)
    axis.set_title(f"{letter}   {title}", loc="left", fontsize=10, weight="semibold", pad=14)


def plot(root, rows, *, stem="geometry-comparison", panels="AB", dpi=240):
    plt.rcParams.update({"font.size": 9, "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, (risk, loss) = plt.subplots(
        1, 2, figsize=(9.0, 3.65), layout="constrained", gridspec_kw={"width_ratios": (1, 1.3)}
    )
    format_axis(risk, panels[0], "Heritage ignition to extinction")
    format_axis(loss, panels[1], "Ordinary roof area lost")
    highest = max(100 * int(r["compact_first_ignition_cases"]) / int(r["pairs"]) for r in rows)
    right = min(100, max(10, highest * 1.25))
    for index, row in enumerate(rows):
        cases = int(row["pairs"])
        for name, color, offset, mark in (
            ("wide", WIDE, -0.13, "o"),
            ("compact", COMPACT, 0.13, "s"),
        ):
            hits = int(row[f"{name}_first_ignition_cases"])
            percent = 100 * hits / cases
            risk.scatter(
                percent,
                index + offset,
                marker=mark,
                s=30,
                color=color,
                zorder=3,
                label=("Multipoint wide" if name == "wide" else "Compact") if not index else None,
            )
            label_on_left = percent > 0.86 * right
            risk.annotate(
                f"{hits}/{cases}",
                (percent, index + offset),
                xytext=(-7 if label_on_left else 7, 0),
                textcoords="offset points",
                va="center",
                ha="right" if label_on_left else "left",
                color=color,
                fontsize=8,
            )
        for field, color, offset, mark, caption in (
            ("ordinary_burned_m2", COMPACT, -0.12, "s", "Burned"),
            ("ordinary_cleared_or_burned_m2", NET, 0.12, "D", "Cleared or burned"),
        ):
            loss.errorbar(
                float(row[f"mean_compact_minus_wide_{field}"]),
                index + offset,
                xerr=float(row[f"se_compact_minus_wide_{field}"]),
                fmt=mark,
                color=color,
                markersize=4.5,
                capsize=3,
                linewidth=1.35,
                label=caption if not index else None,
            )
    risk.set_yticks(range(len(LABELS)), LABELS)
    loss.set_yticks(range(len(LABELS)))
    loss.tick_params(axis="y", labelleft=False)
    risk.set_xlim(-max(2, highest * 0.03), right)
    risk.xaxis.set_major_locator(MaxNLocator(5, integer=True, prune="lower"))
    risk.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    risk.set_xlabel("Share of roof and wind combinations")
    loss.axvline(0, color="#889099", linewidth=0.9, zorder=1)
    loss.xaxis.set_major_locator(MaxNLocator(5))
    loss.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))
    loss.set_xlabel("Mean paired difference, compact minus wide (m²)")
    for axis in (risk, loss):
        axis.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.22),
            ncols=2,
            frameon=False,
            handletextpad=0.45,
            columnspacing=1.15,
        )
    fig.savefig(root / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(root / f"{stem}.png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    plot(args.output, load(args.output))
