"""Plot saved susceptible roof temperatures; requires Matplotlib for rendering only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def curve(folder, identifier):
    layout = json.loads((folder / "layout.json").read_text())
    trace = json.loads((folder / "selected-trace.json").read_text())
    evaluation = json.loads((folder / "evaluation.json").read_text())
    core_nodes = {p["node"] for p in layout["roof_components"] if p["building_ids"] == [identifier]}
    positions = [i for i, node in enumerate(trace["node_ids"]) if node in core_nodes]
    outcome = next(
        o for o in evaluation["metrics"]["core_outcomes"] if o["building_id"] == identifier
    )
    time, temperature = [], []
    for frame in trace["frames"]:
        values = [
            frame["surface_temperature_K"][p]
            for p in positions
            if frame["temperature_valid_mask"][p]
        ]
        first = outcome["first_ignition_s"]
        time.append(frame["time_s"] / 60)
        temperature.append(
            max(values) if values and (first is None or frame["time_s"] < first) else np.nan
        )
    return time, temperature, outcome


def render(shared_root, local_root, area_root, output):
    shared_root, local_root, area_root, output = map(
        Path, (shared_root, local_root, area_root, output)
    )
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8), layout="constrained")
    colors = {"G": "#7752a3", "F": "#007f82"}
    for plan, style in (("baseline", "-"), ("remove-M", "--")):
        for identifier, name in (("G", "gulou"), ("F", "bridge")):
            t, temperature, outcome = curve(shared_root / "shared-cell" / plan, identifier)
            axes[0].plot(
                t, temperature, style, color=colors[identifier], lw=2, label=f"{name}, {plan}"
            )
            if outcome["first_ignition_s"] is not None:
                minute = outcome["first_ignition_s"] / 60
                axes[0].plot(minute, 350, "o", color=colors[identifier], ms=5)
                axes[0].annotate(
                    f"{outcome['first_ignition_s']:.0f} s",
                    (minute, 350),
                    xytext=(-8 if identifier == "G" else 8, 6),
                    textcoords="offset points",
                    ha="right" if identifier == "G" else "left",
                    fontsize=8,
                )
    axes[0].axhline(350, color="0.6", lw=0.8, ls=":")
    axes[0].set(title="a  Synthetic: separate states in one coarse cell", ylim=(298, 360))
    for ax, identifier, name, source in (
        (axes[1], "B0073", "gulou B0073", "technical-gulou-neighbor"),
        (axes[2], "B0120", "bridge B0120", "technical-bridge-neighbor"),
    ):
        for root, scenario, label, color in (
            (local_root, source, "one source roof patch", "#8c9ba3"),
            (
                area_root,
                source + "-source-area-control",
                "all unambiguous source roof patches",
                "#c3693c",
            ),
        ):
            t, temperature, outcome = curve(root / scenario / "baseline", identifier)
            ax.plot(t, temperature, lw=2, color=color, label=label)
            if outcome["first_ignition_s"] is not None:
                minute = outcome["first_ignition_s"] / 60
                ax.plot(minute, 520, "o", color=color, ms=6)
                ax.annotate(
                    f"ignition: {outcome['first_ignition_s']:.0f} s",
                    (minute, 520),
                    xytext=(8, -18),
                    textcoords="offset points",
                    fontsize=9,
                )
        ax.axhline(520, color="0.6", lw=0.8, ls=":")
        ax.set(title=f"{'b' if identifier == 'B0073' else 'c'}  Batuan: {name}", ylim=(298, 540))
    for ax in axes:
        ax.set(xlabel="Time (min)", ylabel="Susceptible roof surface temperature (K)", xlim=(0, 20))
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="0.92")
        ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.suptitle("Independent roof heating and ignition controls", fontsize=15)
    fig.supxlabel(
        "Curves stop at first core ignition. Synthetic threshold: 350 K; "
        "Batuan inherited threshold: 520 K.",
        fontsize=9,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    fig.savefig(output.with_suffix(".svg"))
    inputs = sorted(
        {
            p
            for root in (shared_root, local_root, area_root)
            for name in ("layout.json", "selected-trace.json", "evaluation.json")
            for p in root.rglob(name)
        }
    )
    output.with_suffix(".inputs.json").write_text(
        json.dumps(
            {
                "schema_version": "heritage-roof-figure-1",
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "inputs_sha256": {
                    str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
                },
            },
            indent=2,
        )
        + "\n"
    )
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-root", required=True, type=Path)
    parser.add_argument("--local-root", required=True, type=Path)
    parser.add_argument("--area-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.shared_root, args.local_root, args.area_root, args.output)
