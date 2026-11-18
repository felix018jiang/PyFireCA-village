"""Combine completed mechanism controls; optional standalone scientific figure."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def summarize(root):
    runs, quadrature, hashes = [], [], {}
    for probe in ("gulou", "bridge"):
        result = json.loads((root / probe / "summary.json").read_text())
        if not result["complete"]:
            raise ValueError("mechanism experiment incomplete")
        runs.extend(result["runs"])
        quadrature.extend(result["quadrature"])
        hashes.update(json.loads((root / probe / "input-hashes.json").read_text()))
    attribution = json.loads((root / "full-scene-attribution.json").read_text())["runs"]
    return {
        "schema_version": "heritage-mechanism-summary-1",
        "scope": "mechanism_controls_not_protection_evaluation",
        "runs": runs,
        "quadrature": quadrature,
        "full_scene_attribution": attribution,
        "checks": {
            "complete_1200s_runs": len(runs),
            "all_energy_budgets_passed": all(
                max(r["validation"]["energy_closure_errors_J"].values())
                <= r["validation"]["energy_tolerance_J"]
                for r in runs
            ),
            "maximum_energy_closure_error_J": max(
                max(r["validation"]["energy_closure_errors_J"].values()) for r in runs
            ),
            "all_core_contact_disabled": all(r["core_received_convection_J"] == 0 for r in runs),
            "input_files_unchanged": len(hashes),
            "source_quadrature_max_flux_relative_change_025_to_0125": max(
                abs(
                    q["references"]["0.25"]["maximum_unit_flux"]
                    / q["references"]["0.125"]["maximum_unit_flux"]
                    - 1
                )
                for q in quadrature
            ),
            "maximum_full_scene_incident_reconstruction_error_J": max(
                r["incident_reconstruction_error_J"] for r in attribution
            ),
        },
        "real_candidate_ids": [],
        "real_fixed_source_ids": [],
        "optimization": False,
        "source_file_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "src/pyfireca/planning/roof_diagnostics.py",
                ROOT / "scripts/audit_heritage_mechanisms.py",
                ROOT / "scripts/audit_heritage_heat_attribution.py",
                Path(__file__),
            ]
        },
    }


def render(result, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout="constrained")
    labels = ["Seed only", "Internal radiation", "Contact only", "Both"]
    modes = ["seed_only", "source_radiation", "source_contact", "source_both"]
    colors = ["#336a9a", "#c97828"]
    for line, probe in enumerate(("gulou", "bridge")):
        title = "Drum tower" if probe == "gulou" else "Bridge"
        for k, h in enumerate((1.0, 0.5)):
            rows = [
                next(
                    r
                    for r in result["runs"]
                    if r["probe"] == probe
                    and r["area"] == 25.0
                    and r["hs"] == h
                    and r["ht"] == 0.5
                    and r["mode"] == mode
                )
                for mode in modes
            ]
            axes[line, 0].bar(
                np.arange(4) + (k - 0.5) * 0.34,
                [r["source_ignited_area_m2"]["600"] for r in rows],
                width=0.32,
                color=colors[k],
                label=f"Source h={h:g} m",
            )
            rows = [
                next(
                    r
                    for r in result["runs"]
                    if r["probe"] == probe
                    and r["area"] == 5.0
                    and r["hs"] == h
                    and r["ht"] == 0.5
                    and r["mode"] == mode
                )
                for mode in ("seed_only", "source_both")
            ]
            axes[line, 1].bar(
                np.arange(2) + (k - 0.5) * 0.34,
                [r["maximum_valid_core_temperature_K"] - 300 for r in rows],
                bottom=300,
                width=0.32,
                color=colors[k],
                label=f"Source h={h:g} m",
            )
        axes[line, 0].set(
            xticks=np.arange(4),
            xticklabels=labels,
            ylabel="Source area ever ignited by 600 s (m²)",
            title=f"{title}: 25 m² ignition\nReceiver h=0.5 m",
        )
        axes[line, 0].tick_params(axis="x", labelrotation=18)
        axes[line, 0].legend(frameon=False)
        axes[line, 1].set(
            xticks=[0, 1],
            xticklabels=["Seed only", "Source spread enabled"],
            ylabel="Maximum valid core temperature (K)",
            ylim=(300, 480),
            title=f"{title}: 5 m² ignition\nReceiver h=0.5 m",
        )
        q = next(q for q in result["quadrature"] if q["probe"] == probe and q["area"] == "all")
        rows = [r for r in q["axis_controls"] if "receiver_h_m" in r]
        ref = q["references"]["0.125"]["maximum_unit_flux"]
        axes[line, 2].plot(
            [r["receiver_h_m"] for r in rows],
            [100 * (r["maximum_unit_flux"] / ref - 1) for r in rows],
            "o-",
            color="#307f70",
        )
        axes[line, 2].axhline(0, color="#777777", linewidth=0.7)
        axes[line, 2].set(
            xlabel="Receiver sampling scale h (m)",
            ylabel="Sampled peak flux vs reference (%)",
            title=f"{title}: whole-source fire\nFixed source integration",
        )
    fig.suptitle(
        "Mechanism isolation: fixed ignition polygons and inherited materials\n"
        "Two-object controls omit village feedback; quadrature is an integration reference",
        fontsize=12,
    )
    fig.savefig(destination.with_suffix(".png"), dpi=180)
    fig.savefig(destination.with_suffix(".svg"))
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figure", action="store_true")
    args = parser.parse_args()
    result = summarize(args.root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if args.figure:
        render(result, args.output)
