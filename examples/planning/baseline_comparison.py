"""P04 cold search baselines, equal construction budgets and auditable calls.

python examples/planning/baseline_comparison.py --output runs/planning-p04-new
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from math import isfinite
from pathlib import Path
from time import perf_counter

import numpy as np
import shapely

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.contracts import (
    Evidence,
    Ignition,
    PlanningGrid,
    Scenario,
    Target,
    TreatmentProfile,
)
from pyfireca.planning.geometry import prepare_plan
from pyfireca.planning.optimizers import CandidateSegment, enumerate_layouts
from pyfireca.planning.runner import PressureScenario, evaluate_layouts
from pyfireca.planning.search import search_layouts
from pyfireca.planning.visibility import CAPABILITIES, VisibilityConfig, VisibilityResourceLimit

METHODS = (
    ("joint_exhaustive", "joint_exhaustive", None),
    ("fixed_2m", "fixed_width_exhaustive", 2),
    ("fixed_4m", "fixed_width_exhaustive", 4),
    ("best_uniform_width", "uniform_width_exhaustive", None),
    ("myopic_greedy", "myopic_greedy", None),
    ("lookahead_3", "combination_lookahead", None),
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_study(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    grid = PlanningGrid((40, 40), 4, -80, 80)
    candidates = (
        CandidateSegment("upper", (0, 2), (0, 24), (0, 2, 4), "treated"),
        CandidateSegment("lower", (0, -24), (0, -2), (0, 2, 4), "treated"),
        CandidateSegment("connector", (0, -2), (0, 2), (0, 2, 4), "treated"),
    )
    layouts = enumerate_layouts(candidates, max_plans=27)
    targets = (
        Target("central", (40, 0), 2),
        Target("north", (40, 16), 1),
        Target("south", (40, -16), 1),
    )
    pressures = tuple(
        PressureScenario(Scenario(name, (Ignition((-40, y)),), targets, 87), 1)
        for name, y in (("central-ignition", 0), ("north-ignition", 16), ("south-ignition", -16))
    )
    budgets = (0, 8, 44, 88, 96, 144, 192)
    config = VisibilityConfig(2, 0.001, 256)
    study = {
        "schema": "planning-p04-cold-baselines-1",
        "evidence_level": "D0_synthetic",
        "grid": asdict(grid),
        "candidates": [asdict(c) for c in candidates],
        "pressures": [asdict(p) for p in pressures],
        "config": asdict(config),
        "mechanisms": ["M0", "M1"],
        "background_rate_m_s": 1,
        "treated_rate_m_s": 0.25,
        "cost_units_per_m2": 1,
        "budget_caps_units": budgets,
        "methods": [
            {"label": label, "method": method, "fixed_width_m": width}
            for label, method, width in METHODS
        ],
        "max_requests_per_trial": 1000,
        "global_solver_call_budget": 3000,
        "objective": "fixed T=87s target pressure mean, total target weight=4",
        "practical_equivalence_mean_weight": 0.05,
        "cache_scope": (
            "fresh cache for each method/budget/mechanism trial, fixed inputs within trial"
        ),
        "geometry_access": "all candidates and physical costs known; no pre-read loss table",
        "search_rule": (
            "forward changes; positive composite gain per added physical cost; deterministic ties"
        ),
        "incumbent_rule": (
            "best visited primary loss, then cost and ID; retain near ties in visited set"
        ),
        "lookahead_depth": 3,
        "reference_rule": (
            "fresh full enumeration per mechanism and budget, gaps computed after search"
        ),
        "timing_scope": (
            "shared geometry setup separate; cold search wall and solver times separate from I/O"
        ),
        "limitations": [
            "three-coordinate lookahead initially visits the whole feasible action set",
            "fixed width optimizes placement but has a smaller action set",
            "myopic zero-gain stopping is a diagnostic, not sole strong baseline",
            "same P03A search scenarios, not sample-out evaluation",
            "same construction cap is not necessarily equal simulation-call count",
            "no continuous optimum, annual risk, real fuel calibration or learning proxy",
        ],
        "capabilities": CAPABILITIES,
    }
    write_json(output / "study.resolved.json", study)
    domain = np.ones(grid.shape, bool)
    rates = np.ones(grid.shape)
    domain.setflags(write=False)
    rates.setflags(write=False)
    evidence = Evidence("synthetic", "P04 frozen comparison", "conditional isotropic D0 only")
    trials, queries, evaluations, timings, shared_setup = [], [], [], [], []
    total_calls = 0
    for mode in ("M0", "M1"):
        profile = TreatmentProfile(
            "treated",
            mode,
            evidence,
            1,
            ConstantDirectionalSpreadRate(0.25) if mode == "M1" else None,
        )
        started = perf_counter()
        costs = {
            layout.plan.plan_id: prepare_plan(grid, layout.plan, (profile,)).cost_units
            for layout in layouts
        }
        shared_setup.append({"mode": mode, "geometry_cost_setup_s": perf_counter() - started})
        for budget in budgets:
            joint_reference = None
            for label, method, width in METHODS:
                trial_id = f"{mode}-b{budget:g}-{label}"
                trial_calls = 0
                trial_misses = 0

                def oracle(layout, *, trial_id=trial_id, profile=profile, budget=budget):
                    nonlocal total_calls, trial_calls, trial_misses
                    if total_calls + len(pressures) > 3000:
                        raise VisibilityResourceLimit("P04 global solver call limit reached")

                    def log(plan_id, scenario_id, status, elapsed_s):
                        timings.append(
                            {
                                "trial_id": trial_id,
                                "plan_id": plan_id,
                                "scenario_id": scenario_id,
                                "status": status,
                                "solver_wall_s": elapsed_s,
                            }
                        )

                    batch = evaluate_layouts(
                        grid=grid,
                        domain_mask=domain,
                        background_rates_m_s=rates,
                        layouts=(layout,),
                        pressures=pressures,
                        profiles=(profile,),
                        config=config,
                        max_cost_units=budget,
                        max_solver_calls=3000 - total_calls,
                        on_evaluation=log,
                    )
                    total_calls += batch.solver_calls
                    trial_calls += batch.solver_calls
                    trial_misses += 1
                    outcome = batch.outcomes[0]
                    evaluations.append(
                        {
                            "trial_id": trial_id,
                            "plan_id": layout.plan.plan_id,
                            "widths_m": layout.widths_m,
                            "score": asdict(outcome.score),
                            "scenarios": [
                                {
                                    "scenario_id": c.scenario_id,
                                    "status": c.status,
                                    "error": c.error,
                                    "exposed_weight": c.evaluation.exposed_weight
                                    if c.evaluation
                                    else None,
                                    "target_arrivals_s": [
                                        t if isfinite(t) else None
                                        for t in c.evaluation.target_arrivals_s
                                    ]
                                    if c.evaluation
                                    else None,
                                    "target_graph_unreachable": [
                                        not isfinite(t) for t in c.evaluation.target_arrivals_s
                                    ]
                                    if c.evaluation
                                    else None,
                                }
                                for c in outcome.scenarios
                            ],
                        }
                    )
                    return outcome.score

                started = perf_counter()
                result = search_layouts(
                    layouts=layouts,
                    cost_by_id=costs,
                    oracle=oracle,
                    budget_units=budget,
                    method=method,
                    fixed_width_m=width,
                    lookahead_segments=3,
                    practical_difference=0.05,
                    max_requests=1000,
                )
                wall = perf_counter() - started
                for index, query in enumerate(result.queries):
                    queries.append(
                        {
                            "trial_id": trial_id,
                            "request_index": index,
                            "plan_id": query.plan_id,
                            "cache_hit": query.cache_hit,
                            "score": asdict(query.score),
                        }
                    )
                if label == "joint_exhaustive":
                    joint_reference = result
                complete_reference = (
                    joint_reference.coverage_complete and joint_reference.selected is not None
                )
                reference_loss = (
                    joint_reference.selected.mean_exposure if complete_reference else None
                )
                row = {
                    "trial_id": trial_id,
                    "mode": mode,
                    "budget_units": budget,
                    "method": label,
                    "selected": asdict(result.selected) if result.selected else None,
                    "near_optimal_visited_ids": result.near_optimal_visited_ids,
                    "action_set_size": result.action_set_size,
                    "coverage_complete": result.coverage_complete,
                    "unqueried_ids": result.unqueried_ids,
                    "unknown_ids": result.unknown_ids,
                    "evaluated_ids": result.evaluated_ids,
                    "accepted_path_ids": result.accepted_path_ids,
                    "stop_reason": result.stop_reason,
                    "requests": len(result.queries),
                    "cache_hits": sum(q.cache_hit for q in result.queries),
                    "unique_oracle_evaluations": trial_misses,
                    "solver_calls": trial_calls,
                    "joint_reference_loss": reference_loss,
                    "gap_to_joint_reference": result.selected.mean_exposure - reference_loss
                    if result.selected and reference_loss is not None
                    else None,
                    "gap_interpretation": "action-set restriction"
                    if width is not None or method == "uniform_width_exhaustive"
                    else "search gap within same action set",
                }
                trials.append(row)
                timings.append({"trial_id": trial_id, "cold_search_wall_s": wall})
    write_json(output / "trials.json", trials)
    write_json(output / "queries.json", queries)
    write_json(output / "oracle_evaluations.json", evaluations)
    write_json(output / "timings.json", timings)
    write_json(output / "shared_setup_timings.json", shared_setup)
    fixed_envelope = []
    for mode in ("M0", "M1"):
        for budget in budgets:
            group = [r for r in trials if r["mode"] == mode and r["budget_units"] == budget]
            fixed = [r for r in group if r["method"] in ("fixed_2m", "fixed_4m")]
            joint = next(r for r in group if r["method"] == "joint_exhaustive")
            complete = all(r["coverage_complete"] and r["selected"] is not None for r in fixed)
            best = min(r["selected"]["mean_exposure"] for r in fixed) if complete else None
            fixed_envelope.append(
                {
                    "mode": mode,
                    "budget_units": budget,
                    "best_fixed_loss": best,
                    "best_fixed_methods": [
                        r["method"]
                        for r in fixed
                        if complete and abs(r["selected"]["mean_exposure"] - best) <= 1e-12
                    ],
                    "joint_loss": joint["joint_reference_loss"],
                    "joint_advantage_over_best_fixed": best - joint["joint_reference_loss"]
                    if complete and joint["joint_reference_loss"] is not None
                    else None,
                    "interpretation": (
                        "retrospective two-width envelope, not a deployable prior "
                        "width-selection policy"
                    ),
                }
            )
    write_json(output / "fixed_width_envelope.json", fixed_envelope)
    checks = {
        "all_joint_references_complete": all(
            r["coverage_complete"] for r in trials if r["method"] == "joint_exhaustive"
        ),
        "uniform_action_sets_complete": all(
            r["coverage_complete"] for r in trials if r["method"] == "best_uniform_width"
        ),
        "uniform_search_matches_fixed_envelope": all(
            r["selected"] is not None
            and envelope["best_fixed_loss"] is not None
            and abs(r["selected"]["mean_exposure"] - envelope["best_fixed_loss"]) <= 1e-12
            for envelope in fixed_envelope
            for r in trials
            if r["method"] == "best_uniform_width"
            and r["mode"] == envelope["mode"]
            and r["budget_units"] == envelope["budget_units"]
        ),
        "fixed_action_sets_complete": all(
            r["coverage_complete"] for r in trials if r["method"] in ("fixed_2m", "fixed_4m")
        ),
        "no_unknown_results": all(
            not r["unknown_ids"] and r["selected"] is not None for r in trials
        ),
        "combo_matches_joint_in_three_coordinates": all(
            r["gap_to_joint_reference"] is not None and abs(r["gap_to_joint_reference"]) <= 1e-12
            for r in trials
            if r["method"] == "lookahead_3"
        ),
        "query_accounting_pass": all(
            r["requests"] == r["cache_hits"] + r["unique_oracle_evaluations"] for r in trials
        ),
        "solver_accounting_pass": all(
            r["solver_calls"] == 3 * r["unique_oracle_evaluations"] for r in trials
        ),
        "selected_cost_within_budget": all(
            r["selected"] is not None and r["selected"]["cost_units"] <= r["budget_units"] + 1e-8
            for r in trials
        ),
    }
    summary = {
        "trials": len(trials),
        "solver_calls": total_calls,
        "oracle_evaluations": len(evaluations),
        "requests": len(queries),
        "cache_hits": sum(q["cache_hit"] for q in queries),
        "checks": checks,
        "max_myopic_gap": max(
            r["gap_to_joint_reference"]
            for r in trials
            if r["method"] == "myopic_greedy" and r["gap_to_joint_reference"] is not None
        ),
        "max_joint_advantage_over_best_fixed": max(
            r["joint_advantage_over_best_fixed"]
            for r in fixed_envelope
            if r["joint_advantage_over_best_fixed"] is not None
        ),
        "phase_gate": "P04_D0_cold_baselines_complete"
        if all(checks.values())
        else "incomplete_or_unverified",
        "remaining": [
            "independent scenario/landscape validation",
            "mechanism/ROS sensitivity",
            "larger action sets for search-efficiency claims",
            "real treatment/crossing evidence",
            "P00 novelty matrix",
        ],
    }
    write_json(output / "validation.json", summary)
    lines = [
        "# P04 cold baseline comparison",
        "",
        "Synthetic conditional pressure objective; no sample-out or field-risk claim.",
        "",
        "| Mode | Budget | Method | Mean loss | Gap to joint | Requests | Unique solves |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in trials:
        if row["budget_units"] in (96, 192):
            loss = row["selected"]["mean_exposure"] if row["selected"] else None
            lines.append(
                f"| {row['mode']} | {row['budget_units']} | {row['method']} | {loss} | "
                f"{row['gap_to_joint_reference']} | {row['requests']} | "
                f"{row['unique_oracle_evaluations']} |"
            )
    lines.extend(
        [
            "",
            (
                "best_uniform_width independently searches both widths and placements "
                "with a fresh cache."
            ),
            (
                "The three-coordinate lookahead covers this entire small action set; "
                "this is not a demonstrated speed-up."
            ),
            "",
            (
                "The best-fixed envelope is retrospective. Both fixed widths must be "
                "reported; choosing one after the results is not an independent policy."
            ),
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n")
    root = Path(__file__).resolve().parents[2]
    paths = [
        Path(__file__),
        *(root / "src/pyfireca/planning").glob("*.py"),
        root / "src/pyfireca/arrival.py",
        root / "src/pyfireca/neighborhood.py",
        root / "src/pyfireca/propagation.py",
        root / "src/pyfireca/state.py",
        root / "pyproject.toml",
    ]
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "shapely": shapely.__version__,
        "source_sha256": {},
    }
    for path in paths:
        relative = path.relative_to(root)
        data = path.read_bytes()
        provenance["source_sha256"][str(relative)] = hashlib.sha256(data).hexdigest()
        snapshot = output / "source_snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
    try:
        provenance["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        provenance["dirty_status"] = subprocess.check_output(
            ["git", "status", "--short"], cwd=root, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        provenance["head"] = None
    write_json(output / "provenance.json", provenance)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_study(args.output), indent=2))
