"""Render saved scale controls with Matplotlib; no new simulation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon


def polygons(geometry):
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


def render(summary_path, output):
    summary_path, output = Path(summary_path), Path(output)
    data = json.loads(summary_path.read_text())
    colors = {5.0: "#3286a0", 25.0: "#bf7337", "all": "#98475e"}
    labels = {
        5.0: "5 m² initial fire",
        25.0: "25 m² initial fire",
        "all": "all unambiguous source roof",
    }
    scales = [5.0, 2.5, 1.0, 0.5]
    fig, axes = plt.subplots(2, 3, figsize=(16, 8.5), layout="constrained")
    for rr, probe in enumerate(("gulou", "bridge")):
        rows = [r for r in data["runs"] if r["probe_id"] == probe]
        for area in (5.0, 25.0, "all"):
            base = sorted(
                [
                    r
                    for r in rows
                    if r["area_m2"] == area and r["phase"] == [0.0, 0.0] and r["dt_s"] == 1.0
                ],
                key=lambda r: -r["h_m"],
            )
            x = [scales.index(r["h_m"]) for r in base]
            axes[rr, 0].plot(
                x,
                [r["outcome"]["maximum_valid_surface_temperature_K"] for r in base],
                "o-",
                color=colors[area],
                lw=1.8,
                ms=4,
                label=labels[area],
            )
            time = [r["outcome"]["first_ignition_s"] for r in base]
            axes[rr, 1].plot(
                x,
                [v if v is not None else np.nan for v in time],
                "o-",
                color=colors[area],
                lw=1.8,
                ms=4,
            )
            for xx, t in zip(x, time, strict=True):
                if t is None:
                    jitter = -0.035 if area == 5.0 else 0.035 if area == 25.0 else 0
                    axes[rr, 1].plot(
                        xx + jitter, 1250, "v", color=colors[area], ms=5, fillstyle="none"
                    )
            axes[rr, 2].plot(
                x,
                [
                    r["source_fire_development"]["ignited_area_m2"]["600"]
                    / r["source_fire_development"]["unambiguous_source_roof_area_m2"]
                    for r in base
                ],
                "o-",
                color=colors[area],
                lw=1.8,
                ms=4,
            )
        for r in rows:
            if r["kind"] in {"phase", "phase_x", "phase_y"}:
                x = scales.index(r["h_m"])
                offset = -0.08 if r["kind"] == "phase_x" else 0.08 if r["kind"] == "phase_y" else 0
                axes[rr, 0].plot(
                    x + offset,
                    r["outcome"]["maximum_valid_surface_temperature_K"],
                    "x",
                    color="0.2",
                    ms=5,
                )
                t = r["outcome"]["first_ignition_s"]
                axes[rr, 1].plot(x + offset, 1250 if t is None else t, "x", color="0.2", ms=5)
        axes[rr, 0].axhline(520, ls=":", lw=0.8, color="0.5")
        axes[rr, 0].set(ylabel=f"{probe}: max susceptible T (K)", ylim=(295, 535))
        axes[rr, 1].set(
            ylabel="First core ignition (s)",
            ylim=(0, 1310),
            yticks=[0, 300, 600, 900, 1250],
            yticklabels=["0", "300", "600", "900", "none by 1200"],
        )
        axes[rr, 2].set(ylabel="Source roof ignited fraction at 600 s", ylim=(-0.03, 1.06))
        for ax in axes[rr]:
            ax.set(
                xticks=range(4),
                xticklabels=["5", "2.5", "1", "0.5"],
                xlabel="Local roof component scale (m)",
            )
            ax.grid(axis="y", color=".91")
            ax.spines[["top", "right"]].set_visible(False)
    for ax, title in zip(
        axes[0], ("Heating", "Ignition / censoring", "Source fire development"), strict=True
    ):
        ax.set_title(title)
    fig.legend(
        *axes[0, 0].get_legend_handles_labels(), loc="outside upper center", ncol=3, frameon=False
    )
    fig.supxlabel(
        "Full background and 5 m ground grid held fixed. "
        "Crosses: half-cell phase controls of the 25 m² fire.",
        fontsize=10,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5), layout="constrained")
    for ax, probe in zip(axes, ("gulou", "bridge"), strict=True):
        first = next(r for r in data["runs"] if r["probe_id"] == probe)
        folder = Path(first["evaluation"]).parent.parent
        config = json.loads((folder / "protocol.json").read_text())["probe"]
        origin = np.asarray(config["anchor_m"])
        footprints = json.loads((folder / "ignition-footprints.json").read_text())
        inventory = json.loads((folder / "inventory.json").read_text())
        target = next(b for b in inventory["buildings"] if b["building_id"] == config["target_id"])
        for p in target["polygons"]:
            ax.add_patch(
                Polygon(
                    np.asarray(p["exterior"]) - origin,
                    facecolor="#bcdde1",
                    edgecolor="#277a87",
                    lw=1,
                    label="Heritage roof",
                )
            )
        for area in ("all", 25.0, 5.0):
            for rings in polygons(footprints[str(area)]["geometry"]):
                ax.add_patch(
                    Polygon(
                        np.asarray(rings[0]) - origin,
                        facecolor=colors[area],
                        alpha=0.55,
                        edgecolor="white",
                        lw=0.6,
                        label=labels[area],
                    )
                )
                for hole in rings[1:]:
                    ax.add_patch(
                        Polygon(np.asarray(hole) - origin, facecolor="white", edgecolor="white")
                    )
        ax.plot(0, 0, "k+", ms=9, label="Frozen anchor")
        ax.autoscale_view()
        ax.set_aspect("equal")
        ax.set(
            title=f"{probe}: {config['target_id']} / technical source {config['source_id']}",
            xlabel="East from frozen source anchor (m)",
            ylabel="North from source anchor (m)",
        )
        ax.spines[["top", "right"]].set_visible(False)
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        loc="outside upper center",
        ncol=3,
        frameon=False,
        title="Vector ignition areas frozen before meshing",
    )
    fig.savefig(output.with_name(output.stem + "-footprints.png"), dpi=180)
    plt.close(fig)
    output.with_suffix(".inputs.json").write_text(
        json.dumps(
            {
                "summary": str(summary_path.resolve()),
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.summary, args.output)
