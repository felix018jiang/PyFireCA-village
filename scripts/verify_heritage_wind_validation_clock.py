"""Verify the accelerated validation kernel on both historical full-domain fires."""

import gc
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic, process_time

import numpy as np
import run_heritage_wind_strategies as original
from heritage_wind_radiation import WindRadiationGeometry
from heritage_wind_terminal_engine import TerminalWindClock
from heritage_wind_validation_fast import COMPILE_FLAGS, FastTerminalWindClock, backend_signature
from run_heritage_wind_extinction import ensure_replay, observations

from pyfireca.planning.roof_ignition import refined_contact_operator
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

OLD = original.DEFAULT
FINAL = OLD.with_name("heritage-wind-final-20261112")
RECEIPT = OLD.with_name("heritage-wind-validation-20261112") / "backend-verification.json"
CASES = ("batuan_fixed-2_N4_r15_heritage-wide", "pingyan_fixed-1_W4_r15_baseline")
SYNTHETIC = "tests/planning/test_wind_validation_clock.py"


def case_inputs(reg, old):
    village = reg["villages"][old["village"]]
    wind = next(w for w in reg["winds"] if w["wind_id"] == old["wind_id"])
    fire = next(f for f in village["fires"] if f["fire_id"] == old["fire_id"])
    plan = next(p for p in village["plans"] if p["plan_id"] == old["plan_id"])
    core_ids = village["core_ids"]
    grid = village["grid"]
    window = 2 * max(grid["height"], grid["width"]) * grid["cell_size_m"]
    primary = reg["primary"]
    bundle, _, inventory = original.study(village["study_path"])
    domain, layout, integrated = original.prepare_domain(
        bundle,
        inventory,
        fire,
        core_ids,
        window,
        scale=primary["roof_scale_m"],
        overlap=village["core_overlap_policy"],
    )
    wind_params = replace(
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        exposure_radius_m=old["radius_m"],
        **{k: wind[k] for k in ("wind_speed_m_s", "wind_from_deg", "flame_tilt_coefficient_s_m")},
    )
    graph = WindRadiationGeometry(layout, integrated, primary["quadrature_spacing_m"]).graph(
        wind_params, max_edges=30_000_000
    )
    p = layout.node_inputs.parameters
    horizon = 3601 + float(np.sum(p.growth_time_s + p.steady_time_s + p.decay_time_s + 1))
    base, retained, ids, initial = original.retained_inputs(domain, layout, plan, horizon)
    req, contact = original.resolve_operators(
        retained, ids, base, graph, refined_contact_operator(layout)
    )
    owners = [layout.owners[i] for i in retained]
    return req, contact, domain, owners, core_ids, initial, layout.cell_id[retained], horizon


def physical_match(reference, candidate):
    comparisons = 0
    for field in reference.final_state.__dataclass_fields__:
        left = getattr(reference.final_state, field)
        right = getattr(candidate.final_state, field)
        if field == "incident_radiation_J":
            np.testing.assert_allclose(left, right, rtol=2e-10, atol=1e-7, err_msg=field)
        else:
            assert np.array_equal(left, right, equal_nan=True), field
            comparisons += 1
    np.testing.assert_array_equal(reference.ignition_cause, candidate.ignition_cause)
    incident = reference.final_state.incident_radiation_J
    diff = np.abs(incident - candidate.final_state.incident_radiation_J)
    budget_differences = {}
    for name in reference.energy_budget.__dataclass_fields__:
        left = getattr(reference.energy_budget, name)
        right = getattr(candidate.energy_budget, name)
        np.testing.assert_allclose(left, right, rtol=2e-10, atol=3e-4, err_msg=name)
        budget_differences[name] = abs(float(left) - float(right))
    return {
        "exact_state_field_count_excluding_incident_radiation": comparisons,
        "exact_ignition_cause": True,
        "incident_max_abs_diff_J": float(np.max(diff)),
        "incident_max_relative_diff_at_nodes_ge_1J": float(
            np.max(diff / np.maximum(1.0, incident))
        ),
        "max_energy_budget_field_abs_diff_J": max(budget_differences.values()),
    }


def verify_site(reg, case):
    print(f"Building original full domain for {case}", flush=True)
    filename = f"{case}.json"
    old = json.loads((OLD / "cases" / filename).read_text())
    final = json.loads((FINAL / "cases" / filename).read_text())
    req, contact, domain, owners, core_ids, initial, cells, horizon = case_inputs(reg, old)
    gc.collect()
    cores = set(core_ids)
    monitored = np.array([i for i, o in enumerate(owners) if cores.intersection(o)], dtype=np.int64)
    clocks = [
        cls(req, contact, monitored_nodes=monitored)
        for cls in (TerminalWindClock, FastTerminalWindClock)
    ]
    results = {}
    timing = {type(clock).__name__: {"wall_s": 0.0, "process_cpu_s": 0.0} for clock in clocks}
    for label, target, archived in [("60min", 3600.0, old), ("extinction", horizon, final)]:
        outcomes = []
        for clock in clocks:
            timing_entry = timing[type(clock).__name__]
            wall, cpu = monotonic(), process_time()
            clock.advance(target, stop_on_extinction=(label == "extinction"), exact=True)
            timing_entry["wall_s"] += monotonic() - wall
            timing_entry["process_cpu_s"] += process_time() - cpu
            out = clock.result()
            state = out.final_state
            boundary = domain.boundary_audit(
                cells[np.isfinite(state.ignition_time_s.ravel())], old["radius_m"]
            )
            assert boundary["closed"] and boundary["artificial_boundary_min_gap_m"] is None
            metrics = observations(req, out, clock.peak_K, owners, core_ids, initial, boundary)
            ensure_replay(archived, metrics)
            assert metrics["energy_closure_error_J"] <= metrics["energy_closure_tolerance_J"]
            if label == "extinction":
                assert boundary["original_physical_boundary_reached"]
                assert not np.any(state.state == 3)
                assert float(np.nanmax(state.burnout_time_s)) == archived["last_burnout_s"]
                assert certify_extinction_v3(
                    req, state, post_apply_events_boundary=True, contact=contact
                )["closed"]
            outcomes.append(out)
            print(
                f"{case} {label} {type(clock).__name__} checked after "
                f"{timing_entry['process_cpu_s']:.2f} CPU s",
                flush=True,
            )
        np.testing.assert_array_equal(clocks[0].peak_K, clocks[1].peak_K)
        candidate = outcomes[1]
        qa = check_final(req, candidate)
        results[label] = {
            **physical_match(*outcomes),
            "monitored_peaks_exact": True,
            "archive_paired_replay_passed": True,
            "elapsed_simulated_s": candidate.final_state.time_s,
            "step_count": int(candidate.final_state.step_index),
            "max_energy_closure_residual_J": max(qa["energy_closure_errors_J"].values()),
            "energy_tolerance_J": qa["energy_tolerance_J"],
            "original_boundary_reached": boundary["original_physical_boundary_reached"],
            "closed_domain": boundary["closed"],
            "active_nodes": int(qa["burning_cells"]),
        }
    steps = results["extinction"]["step_count"]
    for entry in timing.values():
        entry["steps_per_process_cpu_s"] = steps / entry["process_cpu_s"]
    return {
        "archive_case": case,
        "village": old["village"],
        "wind": old["wind_id"],
        "archive_60min_result_sha256": old["result_sha256"],
        "archive_terminal_result_sha256": final["result_sha256"],
        "two_checkpoint_checks": results,
        "terminal_extinction_certificate_verified": True,
        "timing": timing,
    }


def main():
    reg = json.loads((OLD / "registration.json").read_text())
    subprocess.run([sys.executable, "-m", "pytest", "-q", SYNTHETIC], cwd=original.ROOT, check=True)
    cases = [verify_site(reg, case) for case in CASES]
    report = {
        "passed": True,
        "backend_sha256": backend_signature(),
        "architecture": platform.machine(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).strip(),
        "compiler_flags": list(COMPILE_FLAGS),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "synthetic_test": SYNTHETIC,
        "synthetic_test_sha256": hashlib.sha256(
            (original.ROOT / SYNTHETIC).read_bytes()
        ).hexdigest(),
        "comparison": (
            "Exact physical node states, ignition and burnout timing, causes and monitored "
            "temperatures. Integrated incident and budget partition accounting are compared "
            "within floating-point tolerances."
        ),
        "integrated_incident_tolerance": {"rtol": 2e-10, "atol_J": 1e-7},
        "energy_budget_tolerance": {"rtol": 2e-10, "atol_J": 3e-4},
        "timing_scope": (
            "Compiled integration only, excluding construction and verification. Process CPU "
            "time and wall time were measured, electrical power was not instrumented."
        ),
        "cases": cases,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    original.atomic_json(RECEIPT, report)
    print(RECEIPT, report["backend_sha256"], flush=True)


if __name__ == "__main__":
    main()
