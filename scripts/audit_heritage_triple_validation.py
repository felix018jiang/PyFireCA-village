"""Check the completed frozen three-building validation without running physics."""

import collections
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash

ROOT = Path(__file__).resolve().parents[1]
x = json.loads((ROOT / "docs/results/heritage-triple-validation-20261024.json").read_text())
assert x["complete"] and x["new_simulations"] == 108 and len(x["paired_draws"]) == 54
for cohort in ("c", "d"):
    experiment = json.loads(
        (
            ROOT / f"runs/research/heritage-triple-validation-20261024-{cohort}/experiment.json"
        ).read_text()
    )
    for relative, expected in experiment["source_sha256"].items():
        assert content_hash((ROOT / relative).read_text()) == expected
by_draw = collections.defaultdict(list)
for r in x["runs"]:
    by_draw[(r["cohort"], r["draw_id"], r["plan_id"])].append(r)
    assert r["complete"] and r["source_verified"] and not r["invalid_reasons"]
    assert r["energy_error_J"] <= r["energy_tolerance_J"]
    root = ROOT / f"runs/research/heritage-triple-validation-20261024-{r['cohort']}"
    run_id = f"{r['draw_id']}-h{r['scale_m']}".replace(".", "p")
    native = json.loads((root / run_id / r["plan_id"] / "evaluation.json").read_text())
    assert native["diagnostics"]["simulated_until_s"] == 1200
    for k in [
        "demolished_ids",
        "demolished_count",
        "demolished_footprint_area_m2",
        "cleared_roof_union_area_m2",
        "retained_shared_roof_area_m2",
    ]:
        assert native["intervention"][k] == r["intervention"][k]
disagreements = []
for (cohort, draw_id, plan_id), rows in by_draw.items():
    assert len(rows) == 2 and {r["scale_m"] for r in rows} == {1.0, 0.5}
    outcomes = {
        str(r["scale_m"]): {o["building_id"]: o["ignited"] for o in r["core_outcomes"]}
        for r in rows
    }
    if outcomes["1.0"] != outcomes["0.5"]:
        disagreements.append(
            {
                "cohort": cohort,
                "draw_id": draw_id,
                "plan_id": plan_id,
                "core_ignition_by_scale": outcomes,
            }
        )
pairs = []
for a, b in [
    ("baseline", "remove-B0061"),
    ("baseline", "remove-B0061-B0071-B0076"),
    ("remove-B0061", "remove-B0061-B0071-B0076"),
]:
    rows = [
        p for p in x["paired_draws"] if p["baseline_plan_id"] == a and p["treated_plan_id"] == b
    ]
    assert len(rows) == 18
    layers = []
    for source_match in [True, False]:
        selected = [p for p in rows if p["same_source"] == source_match]
        layers.append(
            {
                "same_source": source_match,
                "draws": len(selected),
                "by_core": {
                    core: {
                        "failed_to_protected": sum(
                            p["baseline_core_ignited"][core] and not p["treated_core_ignited"][core]
                            for p in selected
                        ),
                        "protected_to_failed": sum(
                            not p["baseline_core_ignited"][core] and p["treated_core_ignited"][core]
                            for p in selected
                        ),
                        "both_failed": sum(
                            p["baseline_core_ignited"][core] and p["treated_core_ignited"][core]
                            for p in selected
                        ),
                    }
                    for core in ["B0073", "B0120"]
                },
            }
        )
    pairs.append({"baseline_plan_id": a, "treated_plan_id": b, "source_match_groups": layers})
failures = [
    {
        "cohort": r["cohort"],
        "draw_id": r["draw_id"],
        "source_id": r["source_id"],
        "plan_id": r["plan_id"],
        "scale_m": r["scale_m"],
        "core_id": o["building_id"],
        "first_ignition_s": o["first_ignition_s"],
    }
    for r in x["runs"]
    for o in r["core_outcomes"]
    if o["ignited"]
]
restoration = json.loads(
    (ROOT / "docs/results/heritage-triple-validation-restoration-20261024.json").read_text()
)
assert restoration["complete"] and all(
    c["all_final_state_fields_equal"] for c in restoration["cases"]
)
result = {
    "schema_version": "heritage-triple-validation-checks-1",
    "complete": True,
    "run_count": 108,
    "paired_plan_draw_count": 54,
    "unique_fire_draws": 18,
    "audit_driver_content_sha256": content_hash(Path(__file__).read_text()),
    "current_original_producer_code_matches_frozen_snapshots": True,
    "complete_1200s_native_evaluations_verified": True,
    "native_and_summary_demolition_ledgers_equal": True,
    "demolition_vector_geometry_verified_by_primary_report": x["validation"][
        "all_demolition_ledgers_match_vector_geometry"
    ],
    "restoration_checks_previously_completed": True,
    "scale_classification_disagreement_count": len(disagreements),
    "scale_classification_disagreements": disagreements,
    "paired_comparisons": pairs,
    "failures": failures,
    "maximum_energy_error_J": max(r["energy_error_J"] for r in x["runs"]),
    "maximum_energy_error_to_tolerance_ratio": max(
        r["energy_error_J"] / r["energy_tolerance_J"] for r in x["runs"]
    ),
}
p = ROOT / "docs/results/heritage-triple-validation-checks-20261024.json"
p.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k not in ["failures"]}, ensure_ascii=False))
