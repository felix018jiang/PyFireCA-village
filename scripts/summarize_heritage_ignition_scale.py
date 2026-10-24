"""Summarize saved controls without selecting sources, demolition plans or mesh winners."""

from __future__ import annotations

import argparse
import hashlib
import json
from math import floor
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape


def compare(left, right, acceptance):
    a, b = left["outcome"], right["outcome"]
    classification = a["ignited"] == b["ignited"]
    valid = all(
        r["reached_requested_horizon"] and r["protection_status"] != "invalid"
        for r in (left, right)
    )
    if a["ignited"] and b["ignited"]:
        difference = abs(a["first_ignition_s"] - b["first_ignition_s"])
        tolerance = max(
            acceptance["ignition_time_absolute_s"],
            acceptance["ignition_time_relative"]
            * min(a["first_ignition_s"], b["first_ignition_s"]),
        )
        metric = "first_ignition_s"
    else:
        difference = abs(
            a["maximum_valid_surface_temperature_K"] - b["maximum_valid_surface_temperature_K"]
        )
        tolerance = acceptance["unignited_peak_temperature_K"]
        metric = "maximum_valid_surface_temperature_K"
    return {
        "left": left["case_id"],
        "right": right["case_id"],
        "classification_stable": classification,
        "valid_full_horizon": valid,
        "metric": metric,
        "absolute_difference": difference,
        "tolerance": tolerance,
        "response_gate_passed": valid and classification and difference <= tolerance,
    }


def enrich(row):
    folder = Path(row["evaluation"]).parent
    layout = json.loads((folder / "layout.json").read_text())
    parts = [p for p in layout["roof_components"] if p["building_ids"] == [row["source_id"]]]
    nodes = [p["node"] for p in parts]
    direct = [p["node"] for p in parts if p.get("prescribed_ignition_source_id") is not None]
    area = np.load(folder / "node-storage-not-map/covered_area_m2.npy", mmap_mode="r")[0, nodes]
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        ignition = saved["ignition_time_s"][0, nodes]
        released = saved["released_energy_J"]
        row["source_fire_development"] = {
            "unambiguous_source_roof_area_m2": float(area.sum()),
            "ignited_area_m2": {
                str(t): float(area[ignition <= t].sum()) for t in (0, 300, 600, 1200)
            },
            "source_released_energy_J": float(released[0, nodes].sum()),
            "prescribed_patch_released_energy_J": float(released[0, direct].sum()),
        }
    return row


def summarize(roots, output):
    roots, output = [Path(r).resolve() for r in roots], Path(output).resolve()
    all_rows, protocols = [], []
    for root in roots:
        for summary in sorted(root.rglob("summary.json")):
            data = json.loads(summary.read_text())
            if data.get("schema_version") != "heritage-ignition-scale-results-1":
                continue
            if not data["complete"]:
                raise ValueError(f"unfinished predeclared matrix: {summary}")
            all_rows.extend(enrich(row) for row in data["runs"])
        for protocol in sorted(root.rglob("protocol.json")):
            data = json.loads(protocol.read_text())
            protocols.append((protocol, data))
    if not all_rows:
        raise ValueError("no completed scale controls")
    reference = protocols[0][1]
    if any(
        p["source_sha256"] != reference["source_sha256"]
        or p["parent_case_sha256"] != reference["parent_case_sha256"]
        or p["inventory_sha256"] != reference["inventory_sha256"]
        for _, p in protocols
    ):
        raise ValueError("controls use different producer code or base inputs")
    acceptance = reference["config"]["acceptance"]
    checks, point_areas = [], []
    for probe in reference["config"]["technical_probes"]:
        rows = [r for r in all_rows if r["probe_id"] == probe["probe_id"]]

        def find(h, area, phase=(0.0, 0.0), dt=1.0, rows=rows, probe_id=probe["probe_id"]):
            found = [
                r
                for r in rows
                if r["h_m"] == h
                and r["area_m2"] == area
                and r["phase"] == list(phase)
                and r["dt_s"] == dt
            ]
            if len(found) != 1:
                raise ValueError(
                    f"missing or duplicated control {probe_id}, {h}, {area}, {phase}, {dt}"
                )
            return found[0]

        for area in (5.0, 25.0, "all"):
            checks.append(
                {
                    "probe_id": probe["probe_id"],
                    "kind": "spatial_1m_vs_0p5m",
                    "area_m2": area,
                    **compare(find(1.0, area), find(0.5, area), acceptance),
                }
            )
        for h, phases in ((1.0, ((0.5, 0.5),)), (0.5, ((0.5, 0.0), (0.0, 0.5), (0.5, 0.5)))):
            for phase in phases:
                checks.append(
                    {
                        "probe_id": probe["probe_id"],
                        "kind": "phase",
                        "h_m": h,
                        "phase": phase,
                        **compare(find(h, 25.0), find(h, 25.0, phase), acceptance),
                    }
                )
        checks.append(
            {
                "probe_id": probe["probe_id"],
                "kind": "time_step",
                "area_m2": "all",
                **compare(find(1.0, "all"), find(1.0, "all", dt=0.5), acceptance),
            }
        )
        # Geometry-only audit of the old one-point/one-component rule.
        protocol_folder = next(
            p.parent
            for p, v in protocols
            if v["probe"]["probe_id"] == probe["probe_id"] and "all" in v["config"]["areas_m2"]
        )
        roof = shape(
            json.loads((protocol_folder / "ignition-footprints.json").read_text())["all"][
                "geometry"
            ]
        )
        study = json.loads((protocol_folder / "study-input.json").read_text())
        grid = json.loads((Path(study["case_dir"]) / "manifest.json").read_text())["grid"]
        px, py = probe["anchor_m"]
        parent_h = grid["cell_size_m"]
        pc = floor((px - grid["x_min_m"]) / parent_h)
        pr = floor((grid["y_max_m"] - py) / parent_h)
        parent_box = box(
            grid["x_min_m"] + pc * parent_h,
            grid["y_max_m"] - (pr + 1) * parent_h,
            grid["x_min_m"] + (pc + 1) * parent_h,
            grid["y_max_m"] - pr * parent_h,
        )
        for h in (5.0, 2.5, 1.0, 0.5):
            cc = floor((px - grid["x_min_m"]) / h)
            rr = floor((grid["y_max_m"] - py) / h)
            tile = box(
                grid["x_min_m"] + cc * h,
                grid["y_max_m"] - (rr + 1) * h,
                grid["x_min_m"] + (cc + 1) * h,
                grid["y_max_m"] - rr * h,
            )
            geometry = roof.intersection(parent_box).intersection(tile)
            parts = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
            hit = [p for p in parts if p.contains(Point(px, py))]
            if len(hit) != 1:
                raise ValueError("legacy point lies on a patch boundary")
            point_areas.append(
                {
                    "probe_id": probe["probe_id"],
                    "h_m": h,
                    "one_point_component_area_m2": float(hit[0].area),
                    "thermal_run_performed_for_this_point_rule": False,
                }
            )
    budget_checks = []
    for probe in reference["config"]["technical_probes"]:
        for area in (5.0, 25.0, "all"):
            rows = [
                r for r in all_rows if r["probe_id"] == probe["probe_id"] and r["area_m2"] == area
            ]
            if len({r["ignition_geometry_sha256"] for r in rows}) != 1:
                raise AssertionError("source polygon changed between scales")
            error = max(
                abs(r["source_budget"][name] / rows[0]["source_budget"][name] - 1)
                for r in rows
                for name in r["source_budget"]
                if rows[0]["source_budget"][name] > 0
            )
            budget_checks.append(
                {
                    "probe_id": probe["probe_id"],
                    "area_m2": area,
                    "geometry_identical": True,
                    "max_budget_relative_difference": error,
                }
            )
    value = {
        "schema_version": "heritage-ignition-scale-summary-1",
        "runs": all_rows,
        "checks": checks,
        "source_budget_checks": budget_checks,
        "legacy_point_source_geometry_audit": point_areas,
        "acceptance": acceptance,
        "background_grid_m": 5.0,
        "whole_domain_background_retained": True,
        "real_candidate_ids": [],
        "real_fixed_source_ids": [],
        "optimization": False,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "runs": len(all_rows),
                "gates": len(checks),
                "passed_gates": sum(c["response_gate_passed"] for c in checks),
                "largest_source_budget_difference": max(
                    c["max_budget_relative_difference"] for c in budget_checks
                ),
            },
            indent=2,
        )
    )
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summarize(args.root, args.output)
