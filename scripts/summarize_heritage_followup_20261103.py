"""Summarize completed artifacts from the frozen two-village follow-up.

No simulations are launched. Missing evaluations remain unknown. Node ignition
areas are counted once in physical space, and ordinary-object comparisons use
the intersection of unambiguous retained IDs across available evaluations.

Run with /opt/anaconda3/bin/python, which provides matplotlib locally.
"""

# Chinese publication labels intentionally use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/results/heritage-followup-20261103"
CHECKPOINTS = (1200.0, 2400.0, 3600.0)
SCALES = (1.0, 0.5)
ARMS = ("baseline", "selected", "area-matched-position")
ARM_NAMES = {
    "baseline": "无拆除",
    "selected": "既有方案",
    "area-matched-position": "近等名义面积对照",
}
ARM_COLORS = {"baseline": "#66727f", "selected": "#087e8b", "area-matched-position": "#cc692e"}
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"


def read_json(path):
    return json.loads(Path(path).read_text())


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: ";".join(map(str, value)) if isinstance(value, (list, tuple)) else value
                    for key, value in row.items()
                }
            )


def cumulative_area(ignition, area, mask, times):
    """Exact weighted event sum evaluated at arbitrary times, with NaNs excluded."""
    use = np.asarray(mask, bool) & np.isfinite(ignition)
    order = np.argsort(ignition[use], kind="stable")
    event_times = ignition[use][order]
    weights = np.r_[0.0, np.cumsum(area[use][order], dtype=np.float64)]
    return weights[np.searchsorted(event_times, times, side="right")]


def active_area(ignition, burnout, area, mask, time_s):
    active = (
        np.asarray(mask, bool)
        & np.isfinite(ignition)
        & (ignition <= time_s)
        & (~np.isfinite(burnout) | (burnout > time_s))
    )
    return float(area[active].sum())


def owner_mask(offsets, owner_ids, selected_ids):
    result = np.zeros(len(offsets) - 1, bool)
    owner_nodes = np.repeat(np.arange(len(result)), np.diff(offsets))
    result[owner_nodes[np.isin(owner_ids, list(selected_ids))]] = True
    return result


def geometric_demolition_areas(geometries, identifiers):
    polygons = [geometries[i] for i in identifiers]
    nominal = float(sum(p.area for p in polygons))
    union = float(unary_union(polygons).area)
    return {
        "nominal_area_m2": nominal,
        "demolished_union_area_m2": union,
        "internal_duplicate_nominal_area_m2": nominal - union,
    }


def self_check():
    # One shared physical patch (area 7) has two owners, but contributes once.
    ignition = np.array([0.0, 10.0, np.nan, 30.0])
    burnout = np.array([np.nan, 20.0, np.nan, np.nan])
    areas = np.array([2.0, 3.0, 5.0, 7.0])
    offsets = np.array([0, 1, 2, 2, 4])
    owners = np.array(["a", "b", "a", "b"])
    roofs = np.diff(offsets) > 0
    assert np.array_equal(owner_mask(offsets, owners, {"a", "b"}), roofs)
    np.testing.assert_allclose(
        cumulative_area(ignition, areas, roofs, np.array([0, 10, 20, 30])), [2, 5, 5, 12]
    )
    assert active_area(ignition, burnout, areas, roofs, 20) == 2
    assert active_area(ignition, burnout, areas, roofs, 30) == 9
    # Two 2x2 rectangles overlap by 1x2: nominal 8, union 6, duplicate 2.
    polygons = {"a": box(0, 0, 2, 2), "b": box(1, 0, 3, 2)}
    accounting = geometric_demolition_areas(polygons, ["a", "b"])
    assert accounting == {
        "nominal_area_m2": 8.0,
        "demolished_union_area_m2": 6.0,
        "internal_duplicate_nominal_area_m2": 2.0,
    }
    union = unary_union(list(polygons.values()))
    retained = box(2.5, 0, 3.5, 2)
    assert union.intersection(retained).area == 1.0
    assert union.difference(retained).area == 5.0
    return {
        "nan_clocks": "passed",
        "shared_patch_count_once": "passed",
        "event_sum": "passed",
        "demolition_union_and_internal_overlap": "passed",
    }


def load_record(folder, experiment, plan, fire_id, scale):
    manifest = read_json(folder / "complete.json")
    scenario = f"{fire_id}-h{scale:g}"
    if (
        manifest["problem_sha256"] != experiment["problem_sha256"]
        or manifest["demolished_ids"] != sorted(plan["demolished_ids"])
        or manifest["scenario_id"] != scenario
    ):
        raise ValueError("complete manifest does not match the frozen problem/arm/scenario")
    for name in ("evaluation.json", "check.json", "curves.json", "layout.npz", "final-state.npz"):
        if hash_file(folder / name) != manifest["files_sha256"].get(name):
            raise ValueError(f"artifact hash mismatch: {name}")
    evaluation = read_json(folder / "evaluation.json")
    curves = read_json(folder / "curves.json")
    check = read_json(folder / "check.json")
    with np.load(folder / "final-state.npz", allow_pickle=False) as saved:
        ignition = saved["ignition_time_s"].ravel().copy()
        burnout = saved["burnout_time_s"].ravel().copy()
        final_time = float(saved["time_s"])
    with np.load(folder / "layout.npz", allow_pickle=False) as saved:
        layout = {
            key: saved[key].copy()
            for key in ("xy_m", "covered_area_m2", "owner_offsets", "owner_ids")
        }
    area = layout["covered_area_m2"].ravel()
    offsets = layout["owner_offsets"]
    roofs = np.diff(offsets) > 0
    if not (len(ignition) == len(burnout) == len(area) == len(roofs) == len(layout["xy_m"])):
        raise ValueError("node clocks and physical layout differ in size")
    if not np.all(np.isfinite(area)) or np.any(area < 0):
        raise ValueError("non-finite or negative physical node area")
    if offsets[0] != 0 or offsets[-1] != len(layout["owner_ids"]) or np.any(np.diff(offsets) < 0):
        raise ValueError("invalid node ownership offsets")
    failures = evaluation["diagnostics"].get("invalid_reasons", [])
    valid = not failures and check["status"] in {"passed", "failed"}
    expected_sources = sorted(experiment["problem"]["fires"][fire_id]["footprints"])
    if sorted(evaluation["metrics"]["actual_source_ids"]) != expected_sources:
        raise ValueError("actual source differs between frozen comparison arms")
    for checkpoint in curves["checkpoints"]:
        time_s = checkpoint["time_s"]
        computed = float(cumulative_area(ignition, area, roofs, np.array([time_s]))[0])
        if not np.isclose(
            computed, checkpoint["cumulative_ignited_roof_covered_area_m2"], atol=1e-6, rtol=1e-10
        ):
            raise ValueError("saved checkpoint roof area disagrees with physical node clocks")
        active = active_area(ignition, burnout, area, np.ones(len(area), bool), time_s)
        if not np.isclose(
            active, checkpoint["active_burning_covered_area_m2"], atol=1e-6, rtol=1e-10
        ):
            raise ValueError("saved active-burning area disagrees with NaN-aware clocks")
    return {
        "folder": str(folder.relative_to(ROOT)),
        "fire_id": fire_id,
        "plan_id": plan["plan_id"],
        "scale_m": scale,
        "evaluation": evaluation,
        "curves": curves,
        "check": check,
        "ignition": ignition,
        "burnout": burnout,
        "area": area,
        "roofs": roofs,
        "layout": layout,
        "final_time_s": final_time,
        "valid": valid,
        "reached_horizon": final_time >= experiment["config"]["horizon_s"] - 1e-8,
        "complete_manifest_sha256": hash_file(folder / "complete.json"),
    }


def load_village(village, run_parent):
    folder = run_parent / f"heritage-followup-{village}-20261103"
    if not (folder / "experiment.json").exists():
        return {"village_id": village, "run_root": str(folder), "state": "not_prepared"}
    experiment = read_json(folder / "experiment.json")
    inventory = read_json(folder / "inventory.json")
    config = experiment["config"]
    records, errors, absent = {}, [], []
    for fire in config["source_fires"]:
        fire_id = fire["fire_id"]
        for scale in config["roof_scales_m"]:
            scenario = f"{fire_id}-h{scale:g}"
            for plan in config["plans"]:
                key = (fire_id, plan["plan_id"], float(scale))
                result_folder = (
                    folder / "evaluations" / hash_value([sorted(plan["demolished_ids"]), scenario])
                )
                identity = {"fire_id": fire_id, "plan_id": plan["plan_id"], "scale_m": scale}
                if not (result_folder / "complete.json").exists():
                    absent.append({**identity, "status": "unknown", "reason": "not_completed"})
                    continue
                try:
                    records[key] = load_record(result_folder, experiment, plan, fire_id, scale)
                except (OSError, ValueError, KeyError, AssertionError) as error:
                    errors.append({**identity, "reason": str(error), "status": "invalid_artifact"})
    cores = {target["building_id"] for target in inventory["targets"]}
    intervention_union = {i for plan in config["plans"] for i in plan["demolished_ids"]}
    ordinary = {
        b["building_id"]
        for b in inventory["buildings"]
        if b["properties"]["source_label"] == "building" and b["building_id"] not in cores
    }
    for fire in config["source_fires"]:
        for scale in SCALES:
            group = [r for (f, _, s), r in records.items() if f == fire["fire_id"] and s == scale]
            layout_hashes = {r["evaluation"]["diagnostics"]["layout_sha256"] for r in group}
            if len(layout_hashes) > 1:
                errors.append(
                    {
                        "fire_id": fire["fire_id"],
                        "scale_m": scale,
                        "reason": "comparison arms have different frozen layouts",
                        "status": "invalid_comparison",
                    }
                )
                for record in group:
                    record["valid"] = False
    common = ordinary - intervention_union
    ambiguous = set()
    for record in records.values():
        common &= set(record["curves"]["retained_ordinary_ids"])
        ambiguous.update(record["curves"]["ambiguous_ordinary_annotation_ids"])
    expected = experiment["planned_CA_checks"]
    complete = len(records) == expected and all(
        r["valid"] and r["reached_horizon"] for r in records.values()
    )
    geometries = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    return {
        "village_id": village,
        "place_name": config["place_name"],
        "run_root": str(folder.relative_to(ROOT)),
        "state": "complete" if complete else "partial",
        "complete": complete,
        "experiment": experiment,
        "inventory": inventory,
        "geometries": geometries,
        "records": records,
        "artifact_errors": errors,
        "missing_evaluations": absent,
        "common_ordinary_ids": sorted(common) if records else [],
        "ordinary_annotation_count": len(ordinary),
        "common_cohort_finalized": len(records) == expected,
        "ambiguous_ordinary_ids": sorted(ambiguous & ordinary),
        "excluded_intervention_union_ids": sorted(intervention_union),
        "runner_summary": read_json(folder / "summary.json")
        if (folder / "summary.json").exists()
        else None,
    }


def aggregate_rows(villages):
    core_rows, ordinary_rows, area_rows, curves_rows = [], [], [], []
    for village in villages:
        if "experiment" not in village:
            continue
        config = village["experiment"]["config"]
        common = set(village["common_ordinary_ids"])
        geometric_areas = {
            p["plan_id"]: geometric_demolition_areas(village["geometries"], p["demolished_ids"])
            for p in config["plans"]
        }
        selected_nominal_area = geometric_areas["selected"]["nominal_area_m2"]
        for fire in config["source_fires"]:
            fire_id = fire["fire_id"]
            for plan in config["plans"]:
                plan_id = plan["plan_id"]
                pair = {
                    scale: village["records"].get((fire_id, plan_id, scale)) for scale in SCALES
                }
                base = {"village_id": village["village_id"], "fire_id": fire_id, "plan_id": plan_id}
                for scale, record in pair.items():
                    ledger = record["evaluation"]["intervention"] if record else {}
                    nominal_areas = geometric_areas[plan_id]
                    cleared = ledger.get("cleared_roof_union_area_m2")
                    shared = ledger.get("retained_shared_roof_area_m2")
                    balance_error = (
                        nominal_areas["demolished_union_area_m2"] - cleared - shared
                        if record
                        else None
                    )
                    reference = village["records"].get((fire_id, "selected", scale))
                    selected_cleared = (
                        reference["evaluation"]["intervention"]["cleared_roof_union_area_m2"]
                        if reference
                        else None
                    )
                    cleared_difference = (
                        cleared - selected_cleared
                        if cleared is not None and selected_cleared is not None
                        else None
                    )
                    area_rows.append(
                        {
                            **base,
                            "scale_m": scale,
                            "artifact_complete": record is not None,
                            "demolished_ids": plan["demolished_ids"],
                            "demolition_count": len(plan["demolished_ids"]),
                            **nominal_areas,
                            "actual_cleared_union_area_m2": cleared,
                            "retained_shared_roof_area_m2": shared,
                            "union_balance_error_m2": balance_error,
                            "union_balance_valid": abs(balance_error) <= 1e-6
                            if balance_error is not None
                            else None,
                            "nominal_area_difference_percent_vs_selected": 100.0
                            * (nominal_areas["nominal_area_m2"] - selected_nominal_area)
                            / selected_nominal_area,
                            "selected_actual_cleared_union_area_m2": selected_cleared,
                            "actual_cleared_area_difference_m2_vs_selected": cleared_difference,
                            "actual_cleared_area_difference_percent_vs_selected": 100.0
                            * cleared_difference
                            / selected_cleared
                            if cleared_difference is not None and selected_cleared > 0
                            else None,
                            "economic_cost": None,
                        }
                    )
                    if record:
                        sample_times = np.arange(
                            0.0, min(3600, record["final_time_s"]) + 1e-8, 30.0
                        )
                        values = cumulative_area(
                            record["ignition"], record["area"], record["roofs"], sample_times
                        )
                        curves_rows.extend(
                            {
                                **base,
                                "scale_m": scale,
                                "time_s": float(t),
                                "roof_area_m2": float(a),
                                "valid": record["valid"],
                            }
                            for t, a in zip(sample_times, values, strict=True)
                        )
                for time_s in CHECKPOINTS:
                    valid = {
                        s: r is not None and r["valid"] and r["final_time_s"] >= time_s - 1e-8
                        for s, r in pair.items()
                    }
                    both = all(valid.values())
                    for core in village["inventory"]["targets"] + [
                        {"building_id": "__joint__", "role": "all_cores"}
                    ]:
                        core_id = core["building_id"]
                        times = {}
                        for scale, record in pair.items():
                            firsts = record["curves"]["building_first_ignitions"] if record else {}
                            ids = (
                                [t["building_id"] for t in village["inventory"]["targets"]]
                                if core_id == "__joint__"
                                else [core_id]
                            )
                            observed = [
                                firsts[i] for i in ids if i in firsts and firsts[i] <= time_s
                            ]
                            times[scale] = min(observed) if observed and valid[scale] else None
                        failed = any(t is not None for t in times.values())
                        core_rows.append(
                            {
                                **base,
                                "time_s": time_s,
                                "core_id": core_id,
                                "role": core["role"],
                                "status": "failed" if failed else "passed" if both else "unknown",
                                "both_scales_valid": both,
                                "h1_valid": valid[1.0],
                                "h0_5_valid": valid[0.5],
                                "h1_first_ignition_s": times[1.0],
                                "h0_5_first_ignition_s": times[0.5],
                            }
                        )
                    ignited_sets = {}
                    for scale, record in pair.items():
                        firsts = record["curves"]["building_first_ignitions"] if record else {}
                        ids = (
                            {i for i in common if i in firsts and firsts[i] <= time_s}
                            if valid[scale]
                            else set()
                        )
                        ignited_sets[scale] = ids
                        source_ids = (
                            set(record["evaluation"]["metrics"]["actual_source_ids"])
                            if record
                            else set()
                        )
                        node_mask = (
                            owner_mask(
                                record["layout"]["owner_offsets"],
                                record["layout"]["owner_ids"],
                                common,
                            )
                            if record
                            else None
                        )
                        ordinary_rows.append(
                            {
                                **base,
                                "time_s": time_s,
                                "scale_m": scale,
                                "valid_observation": valid[scale],
                                "cohort_finalized": village["common_cohort_finalized"],
                                "common_unambiguous_retained_count": len(common)
                                if village["records"]
                                else None,
                                "ordinary_annotation_count": village["ordinary_annotation_count"],
                                "ambiguous_excluded_count": len(village["ambiguous_ordinary_ids"]),
                                "ignited_common_count": len(ids) if valid[scale] else None,
                                "ignited_common_ids": sorted(ids) if valid[scale] else None,
                                "secondary_common_count": len(ids - source_ids)
                                if valid[scale]
                                else None,
                                "common_roof_ignited_area_m2": float(
                                    cumulative_area(
                                        record["ignition"],
                                        record["area"],
                                        node_mask,
                                        np.array([time_s]),
                                    )[0]
                                )
                                if valid[scale]
                                else None,
                                "common_roof_active_area_m2": active_area(
                                    record["ignition"],
                                    record["burnout"],
                                    record["area"],
                                    node_mask,
                                    time_s,
                                )
                                if valid[scale]
                                else None,
                            }
                        )
                    union = ignited_sets[1.0] | ignited_sets[0.5]
                    ordinary_rows.append(
                        {
                            **base,
                            "time_s": time_s,
                            "scale_m": "union_1_and_0.5",
                            "valid_observation": both,
                            "cohort_finalized": village["common_cohort_finalized"],
                            "common_unambiguous_retained_count": len(common)
                            if village["records"]
                            else None,
                            "ordinary_annotation_count": village["ordinary_annotation_count"],
                            "ambiguous_excluded_count": len(village["ambiguous_ordinary_ids"]),
                            "ignited_common_count": len(union) if both else None,
                            "ignited_common_ids": sorted(union) if both else None,
                            "secondary_common_count": len(
                                union
                                - set(
                                    village["experiment"]["problem"]["fires"][fire_id]["footprints"]
                                )
                            )
                            if both
                            else None,
                            "common_roof_ignited_area_m2": None,
                            "common_roof_active_area_m2": None,
                        }
                    )
    return core_rows, ordinary_rows, area_rows, curves_rows


def plot_setup(output):
    os.environ.setdefault("MPLCONFIGDIR", str(output / ".matplotlib-cache"))
    os.environ.setdefault("XDG_CACHE_HOME", str(output / ".plot-cache"))
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt

    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update(
        {
            "font.family": font_manager.FontProperties(fname=FONT).get_name(),
            "axes.unicode_minus": False,
            "font.size": 11,
            "savefig.facecolor": "white",
        }
    )
    return plt


def plot_curves(villages, rows, output, plt):
    fig, axes = plt.subplots(2, 2, figsize=(15.5, 10.0), constrained_layout=True)
    for village_index, village in enumerate(villages):
        if "experiment" not in village:
            for ax in axes[village_index]:
                ax.text(0.5, 0.5, "实验尚未准备", ha="center", va="center", transform=ax.transAxes)
            continue
        relevant = [r for r in rows if r["village_id"] == village["village_id"] and r["valid"]]
        ymax = max([r["roof_area_m2"] for r in relevant] + [25.0]) * 1.08
        for source_index, fire in enumerate(village["experiment"]["config"]["source_fires"]):
            ax = axes[village_index, source_index]
            fire_id = fire["fire_id"]
            footprints = village["experiment"]["problem"]["fires"][fire_id]["footprints"]
            sources = "+".join(footprints)
            count = 0
            for arm in ARMS:
                for scale in SCALES:
                    series = [
                        r
                        for r in relevant
                        if (r["fire_id"], r["plan_id"], r["scale_m"]) == (fire_id, arm, scale)
                    ]
                    if not series:
                        continue
                    count += 1
                    ax.step(
                        [r["time_s"] / 60 for r in series],
                        [r["roof_area_m2"] for r in series],
                        where="post",
                        color=ARM_COLORS[arm],
                        linestyle="--" if scale == 1 else "-",
                        linewidth=1.8,
                        label=f"{ARM_NAMES[arm]} · {scale:g} m",
                    )
            source_role = "鼓楼" if "drum" in fire_id else "桥侧"
            ax.set(
                title=f"{village['place_name']} · {source_role}源 {sources}（{count}/6份）",
                xlabel="点火后时间（分钟）",
                ylabel="累计引燃屋面覆盖面积（m²）",
                xlim=(0, 60),
                ylim=(0, ymax),
            )
            ax.set_xticks([0, 20, 40, 60])
            ax.grid(alpha=0.18)
            if count:
                ax.legend(fontsize=8.5, loc="upper left", ncol=2)
            else:
                ax.text(0.5, 0.5, "尚无完整有效结果", ha="center", transform=ax.transAxes)
    phase = "完整批次" if all(v.get("complete", False) for v in villages) else "阶段性结果"
    fig.suptitle(
        f"固定同源条件下的火灾传播面积 · {phase}\n"
        "共享屋面物理节点只计一次；含起火区；30秒显示采样；累计引燃不等于整栋损毁",
        fontsize=16,
    )
    for extension in ("png", "svg"):
        fig.savefig(output / f"roof-ignition-curves.{extension}", dpi=180)
    plt.close(fig)


def polygon_lines(ax, geometry, **kwargs):
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for polygon in polygons:
        x, y = polygon.exterior.xy
        ax.plot(x, y, **kwargs)
        for hole in polygon.interiors:
            x, y = hole.xy
            ax.plot(x, y, **kwargs)


def plot_morphology(village, fire_id, output, plt, *, detail=False):
    if "experiment" not in village:
        return
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.lines import Line2D

    experiment = village["experiment"]
    source_kind = "drum" if "drum" in fire_id else "bridge"
    source_label = "鼓楼" if source_kind == "drum" else "桥侧"
    footprints = experiment["problem"]["fires"][fire_id]["footprints"]
    sources = {i: shape(v["geometry"]) for i, v in footprints.items()}
    core_ids = [t["building_id"] for t in village["inventory"]["targets"]]
    intervention_ids = set(village["excluded_intervention_union_ids"])
    source_union = unary_union(list(sources.values()))
    nearest_core = min(core_ids, key=lambda i: (source_union.distance(village["geometries"][i]), i))
    if detail:
        # The window depends only on frozen geometry, never on an arm's fire outcome.
        # All near-core interventions from every arm enter the same fixed viewport.
        nearest_geometry = village["geometries"][nearest_core]
        local_interventions = sorted(
            i
            for i in intervention_ids
            if village["geometries"][i].distance(nearest_geometry) <= 15.0
        )
        source_roof_ids = [i for i in sources if i in village["geometries"]]
        focus_ids = [nearest_core, *source_roof_ids, *local_interventions]
    else:
        focus_ids = core_ids + sorted(intervention_ids)
    focus = unary_union([village["geometries"][i] for i in focus_ids] + [source_union])
    x0, y0, x1, y1 = focus.bounds
    if not detail:
        for arm in ARMS:
            record = village["records"].get((fire_id, arm, 0.5))
            if record is not None and record["valid"]:
                used = (
                    record["roofs"] & np.isfinite(record["ignition"]) & (record["ignition"] <= 3600)
                )
                xy = record["layout"]["xy_m"][used]
                if len(xy):
                    x0, y0 = min(x0, xy[:, 0].min()), min(y0, xy[:, 1].min())
                    x1, y1 = max(x1, xy[:, 0].max()), max(y1, xy[:, 1].max())
    pad = max(8.0, 0.15 * max(x1 - x0, y1 - y0)) if detail else (0.06 * max(x1 - x0, y1 - y0) + 5)
    bounds = (x0 - pad, x1 + pad, y0 - pad, y1 + pad)
    fig, axes = plt.subplots(1, 3, figsize=(19, 9.5))
    fig.subplots_adjust(left=0.055, right=0.99, bottom=0.19, top=0.82, wspace=0.16)
    cmap = ListedColormap(["#efb14c", "#e96b3b", "#b72537"])
    norm = BoundaryNorm([0, 1200, 2400, 3600.00001], cmap.N)
    for arm, ax in zip(ARMS, axes, strict=True):
        for geometry in village["geometries"].values():
            polygon_lines(ax, geometry, color="#d2d8dd", linewidth=0.45, zorder=1)
        record = village["records"].get((fire_id, arm, 0.5))
        plan = next(p for p in experiment["config"]["plans"] if p["plan_id"] == arm)
        ax.set(
            xlim=bounds[:2],
            ylim=bounds[2:],
            aspect="equal",
            title=ARM_NAMES[arm],
            xlabel="东向坐标（m，UTM49N）",
        )
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.tick_params(axis="x", labelrotation=25, labelsize=8)
        ax.tick_params(axis="y", labelsize=8)
        if record is not None and record["valid"] and record["reached_horizon"]:
            used = record["roofs"] & np.isfinite(record["ignition"]) & (record["ignition"] <= 3600)
            xy = record["layout"]["xy_m"][used]
            ax.scatter(
                xy[:, 0],
                xy[:, 1],
                c=record["ignition"][used],
                s=6,
                marker="s",
                linewidths=0,
                cmap=cmap,
                norm=norm,
                zorder=2,
                rasterized=True,
            )
        else:
            ax.text(
                0.5,
                0.55,
                "0.5 m完整结果尚未就绪",
                ha="center",
                transform=ax.transAxes,
                bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "none"},
            )
        for core in core_ids:
            geometry = village["geometries"][core]
            polygon_lines(ax, geometry, color="#087e8b", linewidth=1.8, zorder=4)
            p = geometry.representative_point()
            ax.annotate(
                core,
                (p.x, p.y),
                xytext=(3, 4),
                textcoords="offset points",
                fontsize=8,
                color="#075f69",
                zorder=6,
            )
        for identifier in plan["demolished_ids"]:
            geometry = village["geometries"][identifier]
            polygon_lines(ax, geometry, color="#293441", linewidth=1.4, linestyle="--", zorder=5)
        for identifier, geometry in sources.items():
            polygon_lines(ax, geometry, color="#6d3aa5", linewidth=2.3, zorder=5)
            p = geometry.representative_point()
            ax.annotate(
                f"源 {identifier}",
                (p.x, p.y),
                xytext=(5, -12),
                textcoords="offset points",
                fontsize=8,
                color="#6d3aa5",
                zorder=6,
            )
        ax.grid(alpha=0.12)
    axes[0].set_ylabel("北向坐标（m，UTM49N）")
    handles = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=c, markersize=8, label=label)
        for c, label in zip(
            cmap.colors, ["0–20分钟到火", "20–40分钟到火", "40–60分钟到火"], strict=True
        )
    ]
    handles += [
        Line2D([0], [0], color="#087e8b", lw=2, label="核心遗产"),
        Line2D([0], [0], color="#293441", ls="--", label="拆除轮廓"),
        Line2D([0], [0], color="#6d3aa5", lw=2, label="精确点火区"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.06), ncol=6, fontsize=10)
    scope = (
        f"局部放大：火源与最近核心 {nearest_core}；统计仍包含全部 {len(core_ids)} 处核心"
        if detail
        else "完整范围：包含全部核心、干预建筑及三方案传播范围"
    )
    fig.suptitle(
        f"{village['place_name']} · {source_label}源 {'+'.join(sources)} · "
        f"60分钟累计传播形态（局部细化0.5 m）\n{scope}",
        fontsize=14,
        y=0.97,
    )
    fig.text(
        0.5,
        0.032,
        "色块为真实地理坐标上的屋面节点中心示意，三图共用范围与比例；轮廓不表示整栋烧毁",
        ha="center",
        fontsize=10,
    )
    suffix = "-detail" if detail else ""
    for extension in ("png", "svg"):
        fig.savefig(
            output / f"{village['village_id']}-{source_kind}-morphology{suffix}-h0.5.{extension}",
            dpi=180,
        )
    plt.close(fig)


def main(run_parent, output, no_plots=False):
    output.mkdir(parents=True, exist_ok=True)
    checks = self_check()
    villages = [load_village(v, run_parent) for v in ("batuan", "pingyan")]
    core_rows, ordinary_rows, area_rows, curve_rows = aggregate_rows(villages)
    write_csv(
        output / "core-checkpoints-dual-scale.csv",
        core_rows,
        [
            "village_id",
            "fire_id",
            "plan_id",
            "time_s",
            "core_id",
            "role",
            "status",
            "both_scales_valid",
            "h1_valid",
            "h0_5_valid",
            "h1_first_ignition_s",
            "h0_5_first_ignition_s",
        ],
    )
    write_csv(
        output / "common-retained-ordinary.csv",
        ordinary_rows,
        [
            "village_id",
            "fire_id",
            "plan_id",
            "time_s",
            "scale_m",
            "valid_observation",
            "cohort_finalized",
            "ordinary_annotation_count",
            "ambiguous_excluded_count",
            "common_unambiguous_retained_count",
            "ignited_common_count",
            "ignited_common_ids",
            "secondary_common_count",
            "common_roof_ignited_area_m2",
            "common_roof_active_area_m2",
        ],
    )
    write_csv(
        output / "intervention-areas.csv",
        area_rows,
        [
            "village_id",
            "fire_id",
            "plan_id",
            "scale_m",
            "artifact_complete",
            "demolition_count",
            "demolished_ids",
            "nominal_area_m2",
            "demolished_union_area_m2",
            "internal_duplicate_nominal_area_m2",
            "actual_cleared_union_area_m2",
            "retained_shared_roof_area_m2",
            "union_balance_error_m2",
            "union_balance_valid",
            "nominal_area_difference_percent_vs_selected",
            "selected_actual_cleared_union_area_m2",
            "actual_cleared_area_difference_m2_vs_selected",
            "actual_cleared_area_difference_percent_vs_selected",
            "economic_cost",
        ],
    )
    write_csv(
        output / "roof-ignition-curves.csv",
        curve_rows,
        ["village_id", "fire_id", "plan_id", "scale_m", "time_s", "roof_area_m2", "valid"],
    )
    snapshots = []
    for village in villages:
        snapshot = {
            k: v
            for k, v in village.items()
            if k not in {"records", "geometries", "experiment", "inventory", "runner_summary"}
        }
        if "experiment" in village:
            snapshot.update(
                problem_sha256=village["experiment"]["problem_sha256"],
                planned_CA_checks=village["experiment"]["planned_CA_checks"],
                loaded_CA_checks=len(village["records"]),
                runner_summary_complete=None
                if village["runner_summary"] is None
                else village["runner_summary"]["complete"],
                runner_summary_persisted_count=None
                if village["runner_summary"] is None
                else village["runner_summary"]["persisted_CA_checks"],
                record_audits=[
                    {
                        "fire_id": r["fire_id"],
                        "plan_id": r["plan_id"],
                        "scale_m": r["scale_m"],
                        "folder": r["folder"],
                        "valid": r["valid"],
                        "reached_horizon": r["reached_horizon"],
                        "final_time_s": r["final_time_s"],
                        "retained_unambiguous_ordinary_count": len(
                            r["curves"]["retained_ordinary_ids"]
                        ),
                        "ambiguous_annotation_count": len(
                            r["curves"]["ambiguous_ordinary_annotation_ids"]
                        ),
                        "complete_manifest_sha256": r["complete_manifest_sha256"],
                    }
                    for r in village["records"].values()
                ],
            )
        snapshots.append(snapshot)
    checked_areas = [r for r in area_rows if r["artifact_complete"]]
    area_audit_passed = all(r["union_balance_valid"] for r in checked_areas)
    report = {
        "schema_version": "heritage-followup-report-1",
        "date": "2026-11-03",
        "complete": all(v.get("complete", False) for v in villages) and area_audit_passed,
        "generator_sha256": hash_file(Path(__file__)),
        "mathematical_self_checks": checks,
        "area_accounting_audit": {
            "checked_rows": len(checked_areas),
            "all_present_rows_balanced": area_audit_passed if checked_areas else None,
            "maximum_absolute_balance_error_m2": max(
                (abs(r["union_balance_error_m2"]) for r in checked_areas), default=None
            ),
            "absolute_tolerance_m2": 1e-6,
            "identity": "demolished_polygon_union = cleared_union + retained_shared_union",
            "internal_overlap": "nominal_area_sum - demolished_polygon_union",
        },
        "interpretation": {
            "study": (
                "historical same-source pressure replays; not independent village risk samples"
            ),
            "core_status": (
                "failed if either valid scale ignites; "
                "passed only when both scales are valid and do not ignite"
            ),
            "ordinary_denominator": (
                "intersection of unambiguous retained ordinary IDs across available evaluations, "
                "excluding every planned intervention; not all ordinary buildings"
            ),
            "partial_cohort": (
                "intersection may shrink as missing evaluations complete; "
                "cohort_finalized flags full coverage"
            ),
            "area": "physical covered roof node area counted once even with multiple owners",
            "matched_area_scope": (
                "controls were matched by nominal polygon-area sum only; "
                "internal overlaps and retained shared roofs can change actual cleared area; "
                "same-fire same-scale cleared-area differences versus selected are reported"
            ),
            "map": "physical xy node centers; polygons are outlines, not whole-building loss",
            "map_detail": (
                "geometry-only common window around source, its nearest core, and all planned "
                "interventions within 15 m of that core; no change to full-core statistics; "
                "overview retains all cores, interventions, and observed propagation"
            ),
            "cost": (
                "nominal and actual cleared areas are not economic costs; "
                "money and household costs remain unknown"
            ),
        },
        "villages": snapshots,
        "core_checkpoint_rows": core_rows,
        "common_ordinary_rows": ordinary_rows,
        "intervention_area_rows": area_rows,
        "output_artifacts": {
            "data_files": [
                "summary.json",
                "core-checkpoints-dual-scale.csv",
                "common-retained-ordinary.csv",
                "intervention-areas.csv",
                "roof-ignition-curves.csv",
            ],
            "figure_files": [f"roof-ignition-curves.{ext}" for ext in ("png", "svg")]
            + [
                f"{village['village_id']}-"
                f"{'drum' if 'drum' in source['fire_id'] else 'bridge'}-"
                f"morphology{suffix}-h0.5.{ext}"
                for village in villages
                if "experiment" in village
                for source in village["experiment"]["config"]["source_fires"]
                for suffix in ("", "-detail")
                for ext in ("png", "svg")
            ],
            "figures_rendered_this_execution": False,
        },
    }
    write_json(output / "summary.json", report)
    if not no_plots:
        plt = plot_setup(output)
        plot_curves(villages, curve_rows, output, plt)
        for village in villages:
            if "experiment" in village:
                for source in village["experiment"]["config"]["source_fires"]:
                    plot_morphology(village, source["fire_id"], output, plt)
                    plot_morphology(village, source["fire_id"], output, plt, detail=True)
        report["output_artifacts"]["figures_rendered_this_execution"] = True
        write_json(output / "summary.json", report)
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "output": str(output),
                "villages": [
                    {
                        "id": v["village_id"],
                        "state": v["state"],
                        "loaded": v.get("loaded_CA_checks", 0),
                        "artifact_errors": len(v.get("artifact_errors", [])),
                    }
                    for v in snapshots
                ],
            },
            ensure_ascii=False,
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-parent", type=Path, default=ROOT / "runs/research")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    else:
        main(args.run_parent.resolve(), args.output.resolve(), args.no_plots)
