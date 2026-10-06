"""P03A predeclared discrete geometry/width enumeration, D0 isotropic only.

python examples/planning/layout_enumeration.py --output runs/planning-p03a-new
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
from pyfireca.planning.objectives import select_near_optimal
from pyfireca.planning.optimizers import CandidateSegment, enumerate_layouts
from pyfireca.planning.runner import PressureScenario, evaluate_layouts
from pyfireca.planning.visibility import CAPABILITIES, VisibilityConfig


def write_json(path: Path, value) -> None:
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
    configs = {
        "coarse": VisibilityConfig(8, 0.01, 256),
        "refined": VisibilityConfig(4, 0.001, 256),
        "fine": VisibilityConfig(2, 0.001, 256),
    }
    budgets = (0, 8, 44, 88, 96, 144, 192)
    study = {
        "schema": "planning-p03a-enumeration-1",
        "evidence_level": "D0_synthetic",
        "grid": asdict(grid),
        "candidates": [asdict(c) for c in candidates],
        "pressures": [asdict(p) for p in pressures],
        "modes": ["M0", "M1"],
        "treated_rate_m_s": 0.25,
        "background_rate_m_s": 1,
        "cost_units_per_m2": 1,
        "configs": {name: asdict(config) for name, config in configs.items()},
        "budget_caps_units": budgets,
        "max_plans": 27,
        "solver_call_budget": 600,
        "benchmark_widths_m": [[0, 0, 0], [2, 2, 2], [4, 2, 2]],
        "pressure_objective": "equally weighted conditional mean exposure, not annual risk",
        "primary_endpoint": "fixed targets/weights at T=87s, total weight=4",
        "practical_equivalence_mean_weight": 0.05,
        "cost_tolerance_units": 1e-8,
        "refinement_arrival_relative_tolerance": 0.02,
        "ranking": "all feasible layouts at every resolution; retain all exact/near ties",
        "capabilities": CAPABILITIES,
        "limits": [
            "single uniform synthetic landscape with three ignition pressures",
            "candidate locations are fixed; no unconstrained continuous site search",
            "M0/M1 are separate mechanism assumptions, not fitted real treatments",
            "graph candidate optimum is not continuum or field optimum",
            "no learned surrogate or annual/CVaR inference",
        ],
    }
    write_json(output / "study.resolved.json", study)
    evidence = Evidence("synthetic", "P03A predeclared geometry study", "D0 isotropic only")
    timings, records, selections, benchmarks = [], [], [], []
    batches = {}
    total_calls = 0

    def callback(mode, phase):
        def log(plan_id, scenario_id, status, elapsed):
            timings.append(
                {
                    "mode": mode,
                    "phase": phase,
                    "plan_id": plan_id,
                    "scenario_id": scenario_id,
                    "status": status,
                    "wall_s": elapsed,
                }
            )

        return log

    def evaluate(mode, phase, these_layouts, profile, config):
        nonlocal total_calls
        batch = evaluate_layouts(
            grid=grid,
            domain_mask=np.ones(grid.shape, bool),
            background_rates_m_s=np.ones(grid.shape),
            layouts=these_layouts,
            pressures=pressures,
            profiles=(profile,),
            config=config,
            max_cost_units=max(budgets),
            max_solver_calls=600 - total_calls,
            on_evaluation=callback(mode, phase),
        )
        total_calls += batch.solver_calls
        return batch

    for mode in ("M0", "M1"):
        profile = TreatmentProfile(
            "treated",
            mode,
            evidence,
            1,
            ConstantDirectionalSpreadRate(0.25) if mode == "M1" else None,
        )
        benchmark_layouts = tuple(
            layout for layout in layouts if layout.widths_m in ((0, 0, 0), (2, 2, 2), (4, 2, 2))
        )
        probe = evaluate(mode, "benchmark", benchmark_layouts, profile, configs["coarse"])
        benchmarks.extend(
            {
                "mode": mode,
                "plan_id": o.score.plan_id,
                "status": o.score.status,
                "cost_units": o.score.cost_units,
                "mean_exposure": o.score.mean_exposure,
            }
            for o in probe.outcomes
        )
        for phase, config in configs.items():
            batch = evaluate(mode, phase, layouts, profile, config)
            batches[(mode, phase)] = batch
            scores = tuple(o.score for o in batch.outcomes)
            baseline = batch.outcomes[0]
            if baseline.score.status != "ok":
                raise ValueError("no-treatment baseline failed; pressure study is invalid")
            for outcome in batch.outcomes:
                result = {
                    "mode": mode,
                    "phase": phase,
                    "plan_id": outcome.score.plan_id,
                    "widths_m": outcome.layout.widths_m,
                    "status": outcome.score.status,
                    "cost_units": outcome.score.cost_units,
                    "area_m2": outcome.treated_area_m2,
                    "mean_exposure": outcome.score.mean_exposure,
                    "mean_exposure_reduction": baseline.score.mean_exposure
                    - outcome.score.mean_exposure
                    if outcome.score.mean_exposure is not None
                    else None,
                    "scenarios": [],
                }
                for context in outcome.scenarios:
                    evaluation = context.evaluation
                    result["scenarios"].append(
                        {
                            "scenario_id": context.scenario_id,
                            "status": context.status,
                            "error": context.error,
                            "exposed_weight": evaluation.exposed_weight if evaluation else None,
                            "target_arrivals_s": [
                                t if isfinite(t) else None for t in evaluation.target_arrivals_s
                            ]
                            if evaluation
                            else None,
                            "target_graph_unreachable": [
                                not isfinite(t) for t in evaluation.target_arrivals_s
                            ]
                            if evaluation
                            else None,
                            "target_paths_m": evaluation.target_paths_m if evaluation else None,
                            "nodes": evaluation.node_count if evaluation else None,
                            "tested_edges": evaluation.tested_edge_count if evaluation else None,
                        }
                    )
                records.append(result)
            for budget in budgets:
                selection = select_near_optimal(scores, budget, practical_difference=0.05)
                selections.append({"mode": mode, "phase": phase, **asdict(selection)})
    write_json(output / "evaluations.json", records)
    write_json(output / "selections.json", selections)
    write_json(output / "benchmark.json", benchmarks)
    write_json(output / "timings.json", timings)
    stability = []
    max_relative_change = 0.0
    target_flips = 0
    for mode in ("M0", "M1"):
        fine = {o.score.plan_id: o for o in batches[(mode, "fine")].outcomes}
        for phase in ("coarse", "refined"):
            previous = {o.score.plan_id: o for o in batches[(mode, phase)].outcomes}
            for plan_id, initial in previous.items():
                final = fine[plan_id]
                if initial.score.status == final.score.status == "ok":
                    for left, right in zip(initial.scenarios, final.scenarios, strict=True):
                        for a, b in zip(
                            left.evaluation.target_arrivals_s,
                            right.evaluation.target_arrivals_s,
                            strict=True,
                        ):
                            if isfinite(a) and isfinite(b):
                                max_relative_change = max(
                                    max_relative_change, abs(a - b) / max(b, 1e-12)
                                )
                                target_flips += int((a <= 87) != (b <= 87))
                            elif isfinite(a) != isfinite(b):
                                target_flips += 1
        for budget in budgets:
            chosen = [s for s in selections if s["mode"] == mode and s["budget_units"] == budget]
            sets = [set(s["near_optimal_ids"]) for s in chosen]
            stable_ids = sorted(set.intersection(*sets))
            losses = [s["best_known_loss"] for s in chosen]
            complete = all(s["candidate_set_complete"] for s in chosen)
            fine_selection = next(s for s in chosen if s["phase"] == "fine")
            regrets = [
                fine[pid].score.mean_exposure - fine_selection["best_known_loss"]
                for pid in chosen[0]["near_optimal_ids"]
                if fine[pid].score.status == "ok"
            ]
            stability.append(
                {
                    "mode": mode,
                    "budget_units": budget,
                    "near_set_intersection_ids": stable_ids,
                    "near_sets_identical": all(sets[0] == s for s in sets[1:]),
                    "best_loss_by_phase": dict(zip(configs, losses, strict=True)),
                    "candidate_set_complete": complete,
                    "max_coarse_near_fine_regret": max(regrets) if regrets else None,
                }
            )
    write_json(output / "stability.json", stability)
    actions = []
    for mode in ("M0", "M1"):
        for action, widths in (
            ("widen_upper", (4, 0, 0)),
            ("leave_gap", (2, 2, 0)),
            ("fill_gap", (2, 2, 2)),
            ("widen_all", (4, 4, 4)),
        ):
            row = next(
                r
                for r in records
                if r["mode"] == mode and r["phase"] == "fine" and tuple(r["widths_m"]) == widths
            )
            actions.append(
                {
                    "mode": mode,
                    "action": action,
                    "plan_id": row["plan_id"],
                    "widths_m": widths,
                    "cost_units": row["cost_units"],
                    "mean_exposure": row["mean_exposure"],
                    "mean_exposure_reduction": row["mean_exposure_reduction"],
                }
            )
    write_json(output / "action_comparison.json", actions)
    checks = {
        "all_candidate_scores_resolved": all(r["status"] == "ok" for r in records),
        "all_budget_optima_complete": all(s["candidate_set_complete"] for s in selections),
        "refinement_2pct_pass": max_relative_change <= 0.02,
        "target_horizon_classifications_stable": target_flips == 0,
        "near_sets_stable": all(s["near_sets_identical"] for s in stability),
        "coarse_near_regret_within_practical_threshold": all(
            s["max_coarse_near_fine_regret"] is not None
            and s["max_coarse_near_fine_regret"] <= 0.05
            for s in stability
        ),
    }
    summary = {
        "layouts_per_mode": len(layouts),
        "aggregated_records": len(records),
        "solver_calls_including_benchmark": total_calls,
        "checks": checks,
        "max_refinement_target_relative_change": max_relative_change,
        "target_horizon_flip_count": target_flips,
        "phase_gate": "P03A_D0_candidate_reference_complete"
        if all(checks.values())
        else "needs_numerical_refinement",
        "remaining": [
            "independent pressure scenarios",
            "additional candidate locations",
            "heuristic baselines",
            "mechanism/rate sensitivity",
            "real treatment/crossing evidence",
            "P00 novelty matrix",
        ],
    }
    write_json(output / "validation.json", summary)
    lines = [
        "# P03A D0 layout enumeration",
        "",
        "This is a conditional synthetic pressure study, "
        "not an annual-risk or field recommendation.",
        "",
        "| Mode | Action | Physical cost | Mean exposure | Reduction from baseline |",
        "|---|---|---:|---:|---:|",
    ]
    for row in actions:
        lines.append(
            f"| {row['mode']} | {row['action']} | {row['cost_units']:g} | "
            f"{row['mean_exposure']} | {row['mean_exposure_reduction']} |"
        )
    lines.extend(
        [
            "",
            f"Gate: `{summary['phase_gate']}`.",
            "",
            "All ties are retained in selections.json. "
            "Missing/resource-limited results cannot establish optimality.",
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
