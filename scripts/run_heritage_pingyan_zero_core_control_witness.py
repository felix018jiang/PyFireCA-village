"""Observe registered zero-core-only solver states and store the first valid coarse lower bound."""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np


def main(mode):
    root = Path(__file__).resolve().parents[1]
    cfg = (
        root
        / "examples/research/heritage/area-20261110"
        / "pingyan-zero-core-only-coarse-control-20261111.json"
    )
    res = root / "docs/results"
    run = root / "runs/research/heritage-pingyan-zero-core-only-coarse-control-20261111"
    src = run / "source-snapshot"
    sys.path[:0] = [str(src / "scripts"), str(src / "src")]
    import run_heritage_joint_comparison as native

    from pyfireca.planning.engines import roof_heritage
    from pyfireca.planning.heritage import content_hash
    from pyfireca.planning.strategy_io import file_sha256, read_json

    def verify(condition, message):
        if not condition:
            raise ValueError(message)

    verify(
        Path(native.__file__).resolve().is_relative_to(src)
        and Path(roof_heritage.__file__).resolve().is_relative_to(src),
        "independent frozen physical source snapshot required",
    )
    c = read_json(cfg)
    exp = read_json(run / "experiment.json")
    budget = read_json(run / "execution-budget.json")
    stages = read_json(run / "preflight.json")
    verify(
        stages["passed"] and exp["config_sha256"] == file_sha256(cfg) and c["max_new_checks"] == 4,
        "bad independent control registration",
    )
    analytic = read_json(res / "heritage-pingyan-zero-heat-and-design-loss-20261111.json")
    limit = analytic["worst_registered_burned_area_upper_m2"]
    cap = analytic["worst_registered_original_building_owner_count_upper"]
    keys = [(p, s) for p, s in native.execution_checks(c)]
    verify(len(keys) == 4 and all(s.endswith("-h1") for _, s in keys), "incorrect scale/scheduling")
    binding = dict(
        physical_module_sha256={
            k: exp["source_sha256"][k]
            for k in (
                "src/pyfireca/planning/engines/roof_heritage.py",
                "src/pyfireca/village/solver_v3.py",
            )
        },
        observer_sha256=file_sha256(__file__),
        support_config_sha256=file_sha256(cfg),
        native_problem_sha256=exp["problem_sha256"],
        independent_started_slots_allowed=4,
        only_allowed_difference=(
            "Read-only observation after scheduled events, using unchanged "
            "natural-extinction observer first. Selected step can truncate independently "
            "after the burned-area ceiling is already exceeded. Time horizon, event times, "
            "source area, graph and solver arithmetic are untouched."
        ),
        early_return_metric=(
            "Compensated lower bound from previously burned (physically known) area only. "
            "Existing native files preserve an incomplete/unknown temporal endpoint after "
            "observational stop."
        ),
        prefix_check_period_native_steps=10,
        strict_witness_threshold_A_m2=limit,
        unambiguous_original_owner_witness_threshold=cap,
    )
    binding["receipt_sha256"] = content_hash(binding)
    binding_path = run / "observer-and-stop-registration.json"
    if mode == "register":
        if binding_path.exists():
            verify(read_json(binding_path) == binding, "different observer registration exists")
        else:
            verify(
                budget["started_checks"] == 0 and budget["active"] is None,
                "registration must precede first attempted physical case",
            )
            with binding_path.open("x") as f:
                json.dump(binding, f, indent=2)
                f.write("\n")
        print("coarse observation protocol frozen", flush=True)
        return
    verify(
        read_json(binding_path) == binding and budget["active"] is None,
        "registered observer/budget differs or active job exists",
    )
    before = roof_heritage._run_resolved_v3_simulation

    def physical_run(request, **kwargs):
        original = kwargs["stop_when"]
        area = request.parameters.covered_area_m2.ravel()

        def observe(state):
            if original(state):
                return True
            if state.step_index % 10 != 0:
                return False
            spent = np.isfinite(state.burnout_time_s.ravel()) & (
                state.burnout_time_s.ravel() <= state.time_s
            )
            if float(area[spent].sum()) <= limit + 1e-6:
                return False
            return math.fsum(area[spent]) > limit + 1e-6

        kwargs["stop_when"] = observe
        return before(request, **kwargs)

    original_checks = native.execution_checks

    def witness(path):
        record = read_json(path)
        parent = path.parent
        verify(
            record["problem_sha256"] == exp["problem_sha256"]
            and all(file_sha256(parent / k) == v for k, v in record["files_sha256"].items()),
            "saved coarse evidence changed",
        )
        chk = read_json(parent / "check.json")["evidence"]
        diagnostic = read_json(parent / "evaluation.json")["diagnostics"]
        loss = read_json(parent / "fire-loss.json")
        verify(
            chk["scale_m"] == 1
            and abs(chk["source_budget"]["covered_area_m2"] - 25) < 1e-6
            and chk["source_budget"]["initial_dry_fuel_mass_kg"] > 0
            and chk["source_budget"]["available_energy_J"] > 0
            and diagnostic["source_verified"],
            "wrong actual direct footprint or fuel",
        )
        D = set(c["plans"][0]["demolished_ids"])
        f = exp["problem"]["fires"][chk["fire_id"]]
        owner = next(v for v in f["protocol"]["draws"][0]["priority_ids"] if v not in D)
        verify(
            chk["source_id"] == owner
            and chk["source_footprint_sha256"] == content_hash(f["footprints"][owner]),
            "wrong frozen source realization",
        )
        with np.load(parent / "final-state.npz") as st, np.load(parent / "loss-basis.npz") as lb:
            T = float(st["time_s"])
            spent = np.isfinite(st["burnout_time_s"].ravel()) & (st["burnout_time_s"].ravel() <= T)
            a = math.fsum(lb["post_intervention_covered_area_m2"].ravel()[spent])
            step = int(st["step_index"])
        N = loss["physical_remaining_roof_annotation_counts"][
            "burned_out_annotation_attribution_count"
        ]
        verify(
            abs(a - loss["physical"]["all"]["cumulative_burned_out_covered_area_m2"]) < 1e-7,
            "loss reported by native and independent event counting disagree",
        )
        native_closed = chk["terminal_verification"]["terminal_complete"]
        valid = not diagnostic["invalid_reasons"] and diagnostic["source_verified"]
        qualifies = bool(valid and (a > limit + 1e-6 or N["lower"] > cap))
        return dict(
            monotone_prefix_without_native_physical_invalid_reasons=bool(valid),
            full_natural_extinction=bool(native_closed),
            qualifies=qualifies,
            observed_burned_area_lower_bound_m2=a,
            observed_unique_original_buildings_lower_bound=N["lower"],
            observed_original_owner_upper_at_prefix=N["upper"],
            native_steps_at_saved_state=step,
            source_scenario_scale_m=chk["scale_m"],
            native_observation_manifest_sha256=file_sha256(path),
            source_budget_and_footprint_verified=True,
        )

    roof_heritage._run_resolved_v3_simulation = physical_run
    try:
        for item in keys:
            prior = [witness(p) for p in run.glob("evaluations/*/complete.json")]
            if any(r["qualifies"] for r in prior):
                break
            native.execution_checks = lambda settings, pair=item: iter((pair,))
            native.resume(cfg, run)
    finally:
        native.execution_checks = original_checks
        roof_heritage._run_resolved_v3_simulation = before
    all_rows = [witness(p) for p in run.glob("evaluations/*/complete.json")]
    accepted = next((v for v in all_rows if v["qualifies"]), None)
    post = read_json(run / "execution-budget.json")
    verify(
        post["active"] is None and len(all_rows) <= post["started_checks"] <= 4,
        "physical control budget inconsistent",
    )
    report = dict(
        schema_version="zero-core-only-coarse-loss-prefix-1",
        completed_saved_observations=len(all_rows),
        started_slots=post["started_checks"],
        valid_exceedance_found=accepted is not None,
        observables_for_first_valid_witness=accepted,
        all_observed_native_manifests_digest=content_hash(
            sorted(r["native_observation_manifest_sha256"] for r in all_rows)
        ),
        no_real_world_claim=True,
        baseline_construction_m2=analytic["base_zero_heat_construction_m2"],
        comparison_constructed_m2=analytic["C_m2"],
        comparison_certified_A_cap_m2=limit,
        comparison_certified_N_cap=cap,
        full_target_ensemble_completed=False,
        stopping_rule_receipt_sha256=file_sha256(binding_path),
        source_configuration_sha256=file_sha256(cfg),
        method_sha256=file_sha256(__file__),
    )
    report["receipt_sha256"] = content_hash(report)
    rp = res / "heritage-pingyan-zero-core-only-coarse-control-witness-20261111.json"
    if rp.exists():
        verify(read_json(rp) == report, "saved comparison differs")
    else:
        with rp.open("x") as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write("\n")
    print("registered coarse baseline bound", bool(accepted), post["started_checks"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["register", "resume"])
    main(parser.parse_args().mode)
