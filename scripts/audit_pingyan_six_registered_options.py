"""Find minimum construction in six declared options under the wide option's loss cap."""

import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]


def require(check, message):
    if not check:
        raise ValueError(message)


def actions(row):
    return tuple(sorted(row["demolished_ids"])), tuple(sorted(row["cleared_physical_cell_ids"]))


def audit():
    res = ROOT / "docs/results"
    experiment = ROOT / "runs/research/heritage-protected-union-pingyan-20261111/experiment.json"
    sources = dict(
        local=experiment,
        costs=res / "heritage-pingyan-w2.5-and-w15.01-native-strict-20261111.json",
        prefix=res / "heritage-pingyan-w0.01-first-ignition-prefix-20261111.json",
        extra=res / "heritage-pingyan-observed-clearance-subset-registration-20261111.json",
        composed=res / "heritage-pingyan-three-policies-seven-plus-one-strict-20261111.json",
    )
    local, ledger, prefix, extra, joined = (read_json(p) for p in sources.values())
    for obj, key in [
        (prefix, "receipt_sha256"),
        (extra, "receipt_sha256"),
        (joined, "composition_receipt_sha256"),
    ]:
        require(
            obj[key] == content_hash({k: v for k, v in obj.items() if k != key}),
            "bad evidence signature",
        )
    require(
        prefix["experiments_sha256"][str(experiment.relative_to(ROOT))] == file_sha256(experiment)
        and prefix["first_protected_ignition_at_saved_stop"]
        and prefix["local_native_rows_added"] == 0,
        "prefix does not bind local catalogue",
    )
    plans = local["config"]["plans"]
    prices = {
        r["plan_id"]: r["canonical_C_m2"] for r in ledger["canonical_construction_area_ledger"]
    }
    require(
        len(plans) == len(prices) == 5 and set(prices) == {r["plan_id"] for r in plans},
        "different local plans",
    )
    require(joined["temporal_mode"] == "strict", "wrong endpoint rules")
    observations = {actions(r): r for r in joined["archive"]["fully_evaluated_feasible_plans"]}
    new = extra["action"]
    require(
        len(observations) == 3 and len(set(map(actions, [*plans, new]))) == 6,
        "unexpected number of actions",
    )
    for r in observations.values():
        require(
            r["status"] == "passed" and len(r["scenario_observations"]) == 8,
            "full strict protection in eight checks is necessary",
        )
    wide = next(r for r in plans if r["plan_id"].endswith("-w15.01"))
    _, ncap, acap = observations[actions(wide)]["objective_values"]
    require(ncap == 3 and acap == 241.48854379824064, "wrong wide benchmark")
    picked = observations[actions(new)]
    cost = picked["objective_values"][0]
    require(abs(extra["C_m2"] - cost) < 1e-6, "extra action and native costs disagree")
    rows = []
    for plan in [*plans, new]:
        rid = plan["plan_id"]
        recorded = observations.get(actions(plan))
        price = prices.get(rid, cost)
        row = dict(
            policy=rid,
            C_m2=price,
            strict_closed_full_checks=None,
            core_safe_across_full_set=None,
            max_A_m2=None,
            worst_original_N=None,
        )
        if recorded:
            C, N, A = recorded["objective_values"]
            require(abs(C - price) < 1e-6, "price does not match strict archive")
            w = recorded["worst_case"]["N_burned_out"]
            require(w["upper"] == N, "owner upper disagrees")
            row.update(
                strict_closed_full_checks=8,
                core_safe_across_full_set=True,
                max_A_m2=A,
                worst_original_N=w,
            )
            row["constraint_verdict"] = "meets" if acap >= A and ncap >= N else "exceeds_loss_cap"
        elif rid == prefix["plan"]:
            row.update(
                core_safe_across_full_set=False,
                constraint_verdict="first_heritage_ignition_counterexample",
            )
        else:
            require(price > cost, "an unevaluated competitor could be cheaper")
            row["constraint_verdict"] = "loss_and_safety_unknown_price_already_higher"
        rows.append(row)
    rows.sort(key=lambda r: r["C_m2"])
    passed = [r for r in rows if r["constraint_verdict"] == "meets"]
    winner = min(passed, key=lambda r: r["C_m2"])
    cheaper = [r for r in rows if r["C_m2"] < winner["C_m2"]]
    require(
        winner["policy"] == new["plan_id"]
        and len(cheaper) == 2
        and {r["constraint_verdict"] for r in cheaper}
        == {"exceeds_loss_cap", "first_heritage_ignition_counterexample"},
        "a cheaper option remains open",
    )
    require(all(r == winner or r["C_m2"] != winner["C_m2"] for r in rows), "cost tie")
    return dict(
        schema_version="six-candidate-conditional-construction-minimum-1",
        village="pingyan",
        cohort="Four original design families on both supported grids, full strict endpoints.",
        policy_comparison="Each action selects its retained sources from four frozen orderings.",
        threshold_basis="In-sample loss cap measured for the registered wide comparator.",
        constraints=dict(
            required_no_heritage_ignition=True,
            maximum_original_owner_N_upper=ncap,
            maximum_burned_out_covered_area_m2=acap,
        ),
        declared_roof_enclosure_widths_m=[0.01, 2.5, 5, 10, 15.01],
        scope="Five registered local enclosures plus one registered reduced action.",
        observations=rows,
        finite_unique_minimum=winner["policy"],
        minimum_C_m2=winner["C_m2"],
        cheaper_options_excluded=len(cheaper),
        unmeasured_more_expensive_options=2,
        signed_input_file_hashes={
            str(p.relative_to(ROOT)): file_sha256(p) for p in sources.values()
        },
        extra_CA=0,
    )


if __name__ == "__main__":
    r = audit()
    r["method_sha256"] = file_sha256(__file__)
    r["receipt_sha256"] = content_hash(r)
    p = ROOT / "docs/results/heritage-pingyan-six-options-loss-capped-minimum-20261111.json"
    with p.open("x") as f:
        json.dump(r, f, indent=2, allow_nan=False)
        f.write("\n")
