"""Reconstruct full-domain physical exposure ledgers from archived roof results.

This does not run CA or infer structural destruction. Pre-clearing area and fuel
are reconstructed from the frozen parent-cell budget and saved partition shares.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.fire_loss import summarize_fire_loss
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def annotation_group(loss, selected_ids, removed_ids):
    selected = set(selected_ids)
    retained = [
        b for b in loss["per_building"] if b["building_id"] in selected and not b["demolished"]
    ]
    lower = sum(b["cumulative_ignited_area_m2"]["lower"] > 0 for b in retained)
    upper = sum(
        b["cumulative_ignited_area_m2"]["upper"] is None
        or b["cumulative_ignited_area_m2"]["upper"] > 0
        for b in retained
    )
    removed = len(selected.intersection(removed_ids))
    return {
        "retained_inventory_count": len(retained),
        "ignited_annotation_attribution_count": {"lower": lower, "upper": upper},
        "clearing_plus_ignited_annotation_count": {
            "lower": lower + removed,
            "upper": upper + removed,
        },
    }


def recount(village):
    root = ROOT / f"runs/research/heritage-followup-{village}-20261103"
    experiment = read_json(root / "experiment.json")
    config = experiment["config"]
    config_path = (
        ROOT / f"examples/research/heritage/followup-20261103/{village}-fixed-comparison.json"
    )
    bundle, _, inventory = load_study((config_path.parent / config["study_path"]).resolve())
    if (
        request_fingerprint(bundle.request) != experiment["problem"]["case_sha256"]
        or inventory.sha256 != experiment["problem"]["inventory_sha256"]
    ):
        raise ValueError("current parent physics or inventory differs from the frozen experiment")
    core_ids = {t.building_id for t in inventory.targets}
    ordinary_ids = {b.building_id for b in inventory.buildings if b.source_label == "building"}
    rows = []
    for folder in sorted((root / "evaluations").iterdir()):
        if not (folder / "complete.json").exists():
            continue
        manifest = read_json(folder / "complete.json")
        if manifest["problem_sha256"] != experiment["problem_sha256"]:
            raise ValueError("archived physical problem differs from the experiment")
        for name, digest in manifest["files_sha256"].items():
            if file_sha256(folder / name) != digest:
                raise ValueError(f"changed archive: {folder / name}")
        record = read_json(folder / "evaluation.json")
        with np.load(folder / "layout.npz", allow_pickle=False) as saved:
            cells = saved["physical_cell_id"]
            shares = saved["area_fraction"]
            component_ids = saved["component_id"]
            owner_offsets = saved["owner_offsets"]
            owner_ids = saved["owner_ids"]
            roofs = np.flatnonzero(np.diff(owner_offsets) > 0)
            # Original immutable component IDs retain the partition's owners
            # even when demolition removes IDs from retained shared fuel.
            owners = [tuple() for _ in cells]
            for node in roofs:
                parts = str(component_ids[node]).split(":")
                if len(parts) != 3 or parts[1] == "ground":
                    raise ValueError("unrecognized archived component identity")
                owners[node] = tuple(parts[1].split("+"))
                if not set(owners[node]) <= inventory.by_id.keys():
                    raise ValueError("archived component names an unknown owner")
                start, end = owner_offsets[node : node + 2]
                if not set(owner_ids[start:end]) <= set(owners[node]):
                    raise ValueError("saved retained owners conflict with immutable component")
            area = bundle.request.parameters.covered_area_m2.ravel()[cells] * shares
            baseline_fuel = np.where(
                bundle.request.burnable_mask.ravel()[cells],
                bundle.request.parameters.pyrolysable_dry_fuel_mass_kg.ravel()[cells] * shares,
                0.0,
            )
            post_area = saved["covered_area_m2"]
            cleared = (area > 0) & (post_area == 0)
            if not np.allclose(area[~cleared], post_area[~cleared], rtol=1e-9, atol=1e-7):
                raise ValueError("parent partition does not reproduce retained physical area")
            known = bundle.request.known_mask.ravel()[cells]
        with np.load(folder / "final-state.npz", allow_pickle=False) as state:
            loss = summarize_fire_loss(
                covered_area_m2=area,
                node_owners=owners,
                ignition_time_s=state["ignition_time_s"],
                burnout_time_s=state["burnout_time_s"],
                final_time_s=float(state["time_s"]),
                observation_time_s=float(state["time_s"]),
                inventory_ids=inventory.by_id.keys(),
                demolished_ids=manifest["demolished_ids"],
                source_ids=record["metrics"]["actual_source_ids"],
                cleared_node_mask=cleared,
                initial_fuel_kg=np.where(cleared, 0, baseline_fuel),
                final_fuel_remaining_kg=state["fuel_remaining_kg"],
                baseline_initial_fuel_kg=baseline_fuel,
                known_node_mask=known,
            )
        clearing_error = (
            loss["physical"]["roof"]["cleared_covered_area_m2"]
            - record["intervention"]["cleared_roof_union_area_m2"]
        )
        # Geometric intervention cost and inherited raster covered-area budgets
        # are distinct bases. Preserve their discrepancy, checking against the
        # existing geometry-to-parent coverage contract (relative tolerance 1e-6).
        reconstruction_tolerance = 1e-6 * max(1.0, float(area[cleared].sum()))
        if abs(clearing_error) > reconstruction_tolerance:
            raise ValueError("reconstructed clearing differs from native intervention ledger")
        plan = next(
            p for p in config["plans"] if sorted(p["demolished_ids"]) == manifest["demolished_ids"]
        )
        rows.append(
            {
                "village_id": village,
                "plan_id": plan["plan_id"],
                "scenario_id": manifest["scenario_id"],
                "scale_m": record["diagnostics"]["roof_resolution_protocol"]["cell_size_m"],
                "core_joint_failure": record["metrics"]["core_joint_failure_observed"],
                "demolished_ids": manifest["demolished_ids"],
                "geometric_cleared_area_m2": record["intervention"]["cleared_roof_union_area_m2"],
                "reconstructed_area_error_m2": clearing_error,
                "reconstruction_error_tolerance_m2": reconstruction_tolerance,
                "physical": loss["physical"],
                "annotation_counts": loss["annotation_counts"],
                "ordinary_annotation_counts": annotation_group(
                    loss, ordinary_ids, manifest["demolished_ids"]
                ),
                "core_annotation_counts": annotation_group(loss, core_ids, ()),
                "fuel": loss["fuel"],
                "fire_extinguished_at_observation": loss["fire_extinguished_at_observation"],
                "source_manifest_sha256": file_sha256(folder / "complete.json"),
            }
        )
    return rows


def main():
    rows = [row for village in ("batuan", "pingyan") for row in recount(village)]
    output = ROOT / "docs/results/heritage-area-baseline-ledger-20261110.json"
    report = {
        "schema_version": "heritage-full-domain-recount-1",
        "date": "2026-11-10",
        "new_CA_checks": 0,
        "recounted_evaluations": len(rows),
        "script_sha256": file_sha256(Path(__file__)),
        "rows": rows,
        "scope": (
            "full physical domain; annotation attribution bounds; not real structural destruction"
        ),
    }
    write_json(output, report)
    print(
        json.dumps({"output": str(output), "recounted_evaluations": len(rows), "new_CA_checks": 0})
    )


if __name__ == "__main__":
    main()
