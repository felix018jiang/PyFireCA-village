"""Distinguish nonignition from zero incident fire heat on verified terminal cohorts."""

import argparse
import math
from pathlib import Path

import numpy as np
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs/results"
RUNS = ROOT / "runs/research"


def sources():
    refs = {}

    def read(name):
        path = RESULTS / (name + "-20261111.json")
        refs[path.name] = file_sha256(path)
        return read_json(path)

    design = read("heritage-batuan-direct-D9-G0-complete8-audit")
    holdout = read("heritage-batuan-independent-D9-G0-native-strict")
    local = read("heritage-pingyan-three-policies-complete8-table")
    rows = [
        (
            "batuan",
            "economic",
            "design",
            [Path(design["run_directory"])],
            [r["manifest_sha256"] for r in design["scenario_rows"] if r["strict_passed"]],
        ),
        (
            "batuan",
            "economic",
            "independent",
            [Path(holdout["run_root"])],
            [r["manifest_sha256"] for r in holdout["native_checks"] if r["terminal_complete"]],
        ),
    ]
    for village in ("batuan", "pingyan"):
        joint = read(f"heritage-joint-loss-{village}-complete-native-audit")
        rows.append(
            (
                village,
                "joint",
                "combined_design" if village == "batuan" else "design",
                [RUNS / f"heritage-joint-loss-native-{village}-20261111"],
                [r["manifest_sha256"] for r in joint["checked_native_cases"]],
            )
        )
    for i, plan in enumerate(local["plan_order"]):
        rows.append(
            (
                "pingyan",
                plan,
                "design",
                list(map(Path, local["origin_native_evaluations_by_original_campaign"])),
                [r["in_plan_order"][i]["manifest_sha256"] for r in local["paired_observations"]],
            )
        )
    return refs, rows


def measure(manifest, expected_cores):
    files = read_json(manifest)["files_sha256"]
    folder = manifest.parent
    for name in ("check.json", "evaluation.json", "final-state.npz", "layout.npz"):
        if file_sha256(folder / name) != files[name]:
            raise ValueError("native node state, peak record or layout changed")
    check = read_json(folder / "check.json")
    record = read_json(folder / "evaluation.json")
    e, d = check["evidence"], record["diagnostics"]
    cores = e["core_outcomes"]
    if (
        check["status"] != "passed"
        or e["invalid_reasons"]
        or not d["extinction_closed"]
        or d["temporal_closure_rejection_reasons"]
        or len(cores) != expected_cores
        or len({c["building_id"] for c in cores}) != expected_cores
    ):
        raise ValueError("complete valid heritage-protected extinction required")
    peaks = np.array(d["maximum_valid_surface_temperature_by_node_K"], dtype=float)
    with np.load(folder / "layout.npz") as layout, np.load(folder / "final-state.npz") as state:
        offsets, owners = layout["owner_offsets"], layout["owner_ids"]
        nodes = np.repeat(np.arange(len(offsets) - 1), np.diff(offsets))
        radiation = state["absorbed_radiation_J"].ravel()
        contact = state["received_convection_J"].ravel()
        ignition = state["ignition_time_s"].ravel()
        incident = state["incident_radiation_J"].ravel()
        union = np.zeros(len(peaks), dtype=bool)
        for core in cores:
            indices = np.unique(nodes[owners == core["building_id"]])
            if (
                len(indices) != core["component_count"]
                or len(indices) == 0
                or np.any(np.isfinite(ignition[indices]))
                or core["ignited"] is not False
            ):
                raise ValueError("heritage footprint or ignition state differs")
            for observed, stored in (
                (np.max(peaks[indices]), core["maximum_valid_surface_temperature_K"]),
                (np.sum(radiation[indices]), core["absorbed_radiation_J"]),
                (np.sum(contact[indices]), core["received_convection_J"]),
            ):
                if not math.isclose(observed, stored, rel_tol=1e-12, abs_tol=1e-9):
                    raise ValueError("reported heritage exposure differs from native node arrays")
            union[indices] = True
        if not np.all(np.isfinite(peaks[union])) or not np.all(peaks[union] >= 300):
            raise ValueError("invalid heritage peak temperatures")
        incoming = float(np.sum(incident[union]) + np.sum(contact[union]))
        absorbed = float(np.sum(radiation[union]) + np.sum(contact[union]))
        if (incoming == 0) != (absorbed == 0) or incoming < 0 or absorbed < 0:
            raise ValueError("incoming and absorbed heat inconsistent with protected materials")
        peak = float(np.max(peaks[union]))
        if incoming == 0 and peak != 300:
            raise ValueError("registered ambient-only observation differs")
        return peak, incoming, absorbed


def audit():
    inputs, cohorts = sources()
    rows = []
    for village, plan, cohort, paths, expected in cohorts:
        lookup = {
            file_sha256(p): p for run in paths for p in run.glob("evaluations/*/complete.json")
        }
        expected_size = 16 if village == "batuan" and plan == "joint" else 8
        if len(expected) != len(set(expected)) or len(expected) != expected_size:
            raise ValueError("terminal cohort incomplete or duplicated")
        measured = [measure(lookup[h], 3 if village == "pingyan" else 2) for h in expected]
        peak = max(x[0] for x in measured)
        rows.append(
            dict(
                village=village,
                plan=plan,
                cohort=cohort,
                strict_terminal_cases=len(measured),
                heritage_ignition_cases=0,
                positive_incoming_fire_heat_cases=sum(x[1] > 0 for x in measured),
                no_incoming_fire_heat_cases=sum(x[1] == 0 for x in measured),
                maximum_surface_temperature_K=peak,
                maximum_surface_temperature_C=peak - 273.15,
                maximum_rise_above_300_K=peak - 300,
                maximum_total_absorbed_fire_heat_J_across_heritage_union=max(
                    x[2] for x in measured
                ),
                verified_manifest_sha256=sorted(expected),
            )
        )
    return dict(
        model_initial_temperature_K=300,
        cohorts=rows,
        source_receipts_sha256=inputs,
        measure="Any positive incident radiation or received contact heat on the union of protected physical nodes; cumulative absorbed heat and maximum valid temperature separately reconstructed. No building-specific exposure or origin ranking.",
        interpretation="Thermal exposure is a model output; material or decoration damage is not measured by these experiments.",
        additional_fire_simulations=0,
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    result = audit()
    result["receipt_sha256"] = content_hash(result)
    immutable(parser.parse_args().output, encoded(result))
