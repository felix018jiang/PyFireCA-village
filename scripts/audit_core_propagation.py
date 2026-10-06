"""Measure synthetic spread/time-step sensitivity and village fuel connectivity."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from collections import deque
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.solver_v3 import run_v3_simulation

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "examples/village/vegetation_v3"


def fixture_module():
    spec = importlib.util.spec_from_file_location("spread_fixture", DIRECTORY / "audit_spread.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture


def label_components(mask, *, diagonals):
    """Connected input fuel only; no assertion about heat reaching these cells."""
    labels = np.full(mask.shape, -1, dtype=np.int32)
    sizes = []
    offsets = [(0, 1), (0, -1), (1, 0), (-1, 0)]
    if diagonals:
        offsets += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    height, width = mask.shape
    for r, c in np.argwhere(mask):
        if labels[r, c] >= 0:
            continue
        component = len(sizes)
        labels[r, c] = component
        queue = deque([(int(r), int(c))])
        size = 0
        while queue:
            row, col = queue.popleft()
            size += 1
            for dr, dc in offsets:
                nr, nc = row + dr, col + dc
                if 0 <= nr < height and 0 <= nc < width and mask[nr, nc] and labels[nr, nc] < 0:
                    labels[nr, nc] = component
                    queue.append((nr, nc))
        sizes.append(size)
    return labels, sizes


def audit(output, case_dir, results_dir):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    fixture = fixture_module()
    contact = load_flame_contact_parameters(DIRECTORY / "flame-contact-low-building.synthetic.yml")
    cases = {
        "grass_chain": ("grass",) * 4,
        "shrub_chain": ("shrub",) * 4,
        "tree_chain": ("tree_equivalent",) * 3,
        "grass_to_building": ("grass", "building"),
        "shrub_to_building": ("shrub", "building"),
        "tree_to_building": ("tree_equivalent", "building"),
        "inert_gap": ("grass", "bare_or_road", "grass"),
    }
    rows = []
    for name, materials in cases.items():
        for dt in (1.0, 0.5, 0.25):
            # Match village: override contact height ONLY; retain radiation height.
            request = replace(
                fixture.make_request(materials, max_dt_s=dt, end_time_s=1800),
                flame_contact=contact,
                sample_interval_s=30,
            )
            original_height = request.parameters.receiver_height_m.copy()
            result = run_v3_simulation(request)
            np.testing.assert_array_equal(original_height, request.parameters.receiver_height_m)
            times = result.final_state.ignition_time_s.ravel()
            finite = np.isfinite(times)
            first = float(times[1]) if finite[1] else None
            chain_rate = (
                60 * (len(materials) - 1) * request.grid.cell_size_m / float(times[-1])
                if "chain" in name and finite.all() and times[-1] > 0
                else None
            )
            initial = result.initial_fuel_kg.ravel()
            remaining = result.final_state.fuel_remaining_kg.ravel()
            released = float(
                np.sum(
                    (initial - remaining) * request.parameters.heat_of_combustion_J_kg_dry.ravel()
                )
            )
            closure = abs(released - result.energy_budget.released_J)
            assert closure <= 1e-5 + 1e-9 * released
            rows.append(
                dict(
                    case=name,
                    dt_s=dt,
                    neighbor_ignition_s=first,
                    modeled_chain_rate_m_min=chain_rate,
                    ignited_cells=int(finite.sum()),
                    burned_out_cells=int((result.final_state.state == 4).sum()),
                    ignition_times_s=[float(t) if np.isfinite(t) else None for t in times],
                    energy_mass_closure_error_J=closure,
                )
            )
    convergence = []
    for name in cases:
        runs = [r for r in rows if r["case"] == name]
        baseline = runs[-1]["neighbor_ignition_s"]
        coarse = runs[0]["neighbor_ignition_s"]
        convergence.append(
            dict(
                case=name,
                ignition_outcome_agrees=len({r["ignited_cells"] for r in runs}) == 1,
                dt_1_vs_025_neighbor_difference_s=abs(coarse - baseline)
                if coarse is not None and baseline is not None
                else None,
                relative_difference=abs(coarse - baseline) / baseline
                if coarse is not None and baseline is not None and baseline > 0
                else None,
            )
        )
    case_dir = Path(case_dir)
    mask = np.load(case_dir / "burnable_mask.npy", allow_pickle=False)
    manifest = json.loads((case_dir / "manifest.json").read_text())
    maps = {mode: label_components(mask, diagonals=(mode == "eight")) for mode in ("four", "eight")}
    geometry = {
        key: np.load(case_dir / f"{key}.npy", mmap_mode="r", allow_pickle=False)
        for key in ("ground_elevation_m", "source_height_m", "receiver_height_m", "material_index")
    }
    overrides = dict(contact.receiver_height_overrides_m)
    selected_runs = []
    for path in sorted(Path(results_dir).glob("*/run/viewer/manifest.json")):
        record = json.loads(path.read_text())
        source = record["scenario"].get("ignition_source", {})
        row, col = source.get("row"), source.get("col")
        if not isinstance(row, int) or not isinstance(col, int):
            continue
        if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]) or not mask[row, col]:
            continue
        viewer_grid = json.loads((path.parent / "grid.json").read_text())
        if any(viewer_grid.get(key) != value for key, value in manifest["grid"].items()):
            continue
        ignition_times = np.fromfile(path.parent / record["ignition_time"]["path"], dtype="<f8")
        if ignition_times.size != mask.size:
            raise ValueError(f"ignition array does not match village grid: {path}")
        neighbors = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = row + dr, col + dc
            if not (0 <= nr < mask.shape[0] and 0 <= nc < mask.shape[1]):
                continue
            material = int(geometry["material_index"][nr, nc])
            receiver = overrides.get(material, float(geometry["receiver_height_m"][nr, nc]))
            relative_z = (
                float(geometry["ground_elevation_m"][nr, nc])
                + receiver
                - float(geometry["ground_elevation_m"][row, col])
            )
            eligible = bool(mask[nr, nc]) and (
                0 <= relative_z <= float(geometry["source_height_m"][row, col])
            )
            neighbors.append(
                dict(
                    row=nr,
                    col=nc,
                    material=material,
                    burnable=bool(mask[nr, nc]),
                    contact_eligible=bool(eligible),
                    relative_receiver_z_m=relative_z,
                )
            )
        selected_runs.append(
            dict(
                run_id=path.parents[2].name,
                row=row,
                col=col,
                duration_s=record["scenario"]["end_time_s"],
                ignited_cells=int(np.isfinite(ignition_times).sum()),
                source_material=int(geometry["material_index"][row, col]),
                initial_face_neighbors=neighbors,
                contact_eligible_face_count=sum(n["contact_eligible"] for n in neighbors),
                **{
                    f"{mode}_connected_fuel_cells": sizes[int(labels[row, col])]
                    for mode, (labels, sizes) in maps.items()
                },
            )
        )
    report = dict(
        scope="diagnostic_synthetic_not_field_calibrated",
        contact_receiver_override_m=contact.receiver_height_overrides_m,
        radiation_receiver_height_changed=False,
        synthetic_runs=rows,
        time_step_comparison=convergence,
        village=dict(
            grid_shape=list(mask.shape),
            burnable_cells=int(mask.sum()),
            components={
                mode: dict(count=len(sizes), largest=max(sizes, default=0))
                for mode, (_, sizes) in maps.items()
            },
            metadata=manifest.get("metadata", {}),
            selected_runs=selected_runs,
        ),
        limitations=[
            "Chain rate is model output for 2m synthetic cells, not measured wildfire spread.",
            "Fuel connectivity is topology, not an ignition bound; radiation crosses gaps.",
            "Inert includes both actual road and hypothetically filled unknown land.",
            "No fitting against synthetic outputs; no production physics changes.",
        ],
    )
    output.mkdir(parents=True)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    )
    with (output / "spread.csv").open("w", newline="") as f:
        keys = [k for k in rows[0] if k != "ignition_times_s"]
        writer = csv.DictWriter(f, keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {"convergence": convergence, "village": report["village"]["components"]}, indent=2
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path, default=ROOT / "runs/gaoyou-core-full-20261006/case")
    parser.add_argument(
        "--results", type=Path, default=ROOT / "runs/gaoyou-core-full-interactive-20261006"
    )
    args = parser.parse_args()
    audit(args.output, args.case, args.results)
