"""Continue all active primary wind trials to certified extinction on their original domains."""

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import numpy as np
from heritage_wind_radiation import WindRadiationGeometry
from run_heritage_wind_strategies import (
    DEFAULT as ORIGINAL,
)
from run_heritage_wind_strategies import (
    ROOT,
    atomic_json,
    digest,
    prepare_domain,
    record_hash,
    resolve_operators,
    retained_inputs,
    solver_signature,
    study,
)

from pyfireca.planning.roof_ignition import refined_contact_operator
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3, extinction_certificate_sha256
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

DEST = ROOT / "output/heritage-wind-final-20261112"
WIND_FIELDS = ("wind_speed_m_s", "wind_from_deg", "flame_tilt_coefficient_s_m")


def sha_object(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def signed_inventory(source):
    registration = json.loads((source / "registration.json").read_text())
    reg_sha = digest(source / "registration.json")
    old_solver = solver_signature()
    expected = set()
    for village, setup in registration["villages"].items():
        for name, checksum in setup["input_files_sha256"].items():
            if digest(ROOT / name) != checksum:
                raise ValueError(f"Changed physical input: {name}")
        expected.update(
            f"{village}_{f['fire_id']}_{w}_r15_{p['plan_id']}"
            for f in setup["fires"]
            for w in registration["primary"]["wind_ids"]
            for p in setup["plans"]
        )
    cases = []
    for key in sorted(expected):
        row = json.loads((source / "cases" / f"{key}.json").read_text())
        check = row.pop("result_sha256")
        if (
            record_hash(row) != check
            or row["case_id"] != key
            or row["registration_sha256"] != reg_sha
            or row["solver_sha256"] != old_solver
            or row["radius_m"] != 15
            or row["horizon_s"] != 3600
            or not row["boundary"]["closed"]
        ):
            raise ValueError(f"Invalid primary record: {key}")
        if row["active_m2"] > 0:
            if row["extinction_closed"] or not math.isclose(
                row["elapsed_simulated_s"], 3600, rel_tol=0, abs_tol=1e-8
            ):
                raise ValueError(f"Inconsistent active case: {key}")
        elif not row["extinction_closed"] or not row["extinction_certificate_sha256"]:
            raise ValueError(f"Missing existing closure: {key}")
        row["result_sha256"] = check
        cases.append(row)
    active = [r for r in cases if r["active_m2"] > 0]
    if len(cases) != 720 or len(active) != 488:
        raise ValueError("Primary or active case count differs from the original experiment")
    return registration, cases, active


def terminal_signature(old_solver):
    sources = [
        Path(__file__),
        Path(__file__).with_name("heritage_wind_terminal_engine.py"),
        Path(__file__).with_name("heritage_wind_terminal_kernel.cpp"),
    ]
    return sha_object([old_solver, *[(path.name, digest(path)) for path in sources]])


def seal(root, original, signature, final, certificate):
    key = original["case_id"]
    if certificate["certificate_sha256"] != extinction_certificate_sha256(certificate):
        raise ValueError(f"Invalid extinction certificate: {key}")
    # Publish the row last. A visible case file always points to its completed certificate.
    atomic_json(root / "certificates" / f"{key}.json", certificate)
    row = {
        **{
            field: original[field]
            for field in (
                "case_id",
                "village",
                "fire_id",
                "kind",
                "wind_id",
                "plan_id",
                "radius_m",
                "registration_sha256",
                "footprint_sha256",
            )
        },
        "solver_sha256": signature,
        "native_solver_sha256": original["solver_sha256"],
        "original_result_sha256": original["result_sha256"],
        "replay_60min_verified": True,
        "horizon_s": final["elapsed_simulated_s"],
        "extinction_closed": True,
        "extinction_certificate_sha256": certificate["certificate_sha256"],
        **final,
    }
    row["result_sha256"] = record_hash(row)
    atomic_json(root / "cases" / f"{key}.json", row)


def ensure_replay(original, metrics):
    scalar = (
        "source_area_m2",
        "source_energy_J",
        "core_ignited_count",
        "core_peak_rise_K",
        "core_absorbed_radiation_J",
        "ordinary_roof_ignited_count",
        "ordinary_roof_burned_out_count",
        "roof_ignited_m2",
        "roof_burned_out_m2",
        "ground_ignited_m2",
        "ground_burned_out_m2",
        "active_m2",
        "elapsed_simulated_s",
    )
    for name in scalar:
        if not math.isclose(metrics[name], original[name], rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError(
                f"{original['case_id']} at 60 min: {name} = {metrics[name]} vs {original[name]}"
            )
    for key, obs in original["core_outcomes"].items():
        for name, old in obs.items():
            new = metrics["core_outcomes"][key][name]
            if old is None and new is None:
                continue
            if old is None or new is None or not math.isclose(old, new, rel_tol=1e-8, abs_tol=1e-5):
                raise ValueError(f"{original['case_id']} at 60 min, {key}: {name} = {new} vs {old}")


def observations(request, result, peak, owners, core_ids, initial, boundary):
    state = result.final_state
    core_set = set(core_ids)
    roof = np.array([bool(o) for o in owners])
    core = np.array([bool(core_set.intersection(o)) for o in owners])
    core_owners = [o for o, keep in zip(owners, core, strict=True) if keep]
    energy = state.absorbed_radiation_J.ravel()[core]
    times = state.ignition_time_s.ravel()
    ambient = request.scenario.ambient_temperature_K
    details = {}
    for key in core_ids:
        selected = np.array([key in o for o in core_owners], dtype=bool)
        ts = times[core][selected]
        details[key] = {
            "first_ignition_s": float(np.nanmin(ts)) if np.any(np.isfinite(ts)) else None,
            "peak_rise_K": float(peak[selected].max(initial=ambient) - ambient),
            "absorbed_radiation_J": float(energy[selected].sum()),
        }
    area = request.parameters.covered_area_m2.ravel()
    burning = state.state.ravel() == 3
    burned = state.state.ravel() == 4
    ignited = np.isfinite(times)

    def ordinary(mask):
        return len({o for i in np.flatnonzero(mask & roof) for o in owners[i]} - core_set)

    qa = check_final(request, result)
    return {
        "elapsed_simulated_s": float(state.time_s),
        "core_outcomes": details,
        "core_ignited_count": sum(d["first_ignition_s"] is not None for d in details.values()),
        "core_peak_rise_K": float(peak.max(initial=ambient) - ambient),
        "core_absorbed_radiation_J": float(energy.sum()),
        "ordinary_roof_ignited_count": ordinary(ignited),
        "ordinary_roof_burned_out_count": ordinary(burned),
        "roof_ignited_m2": float(area[roof & ignited].sum()),
        "ground_ignited_m2": float(area[~roof & ignited].sum()),
        "roof_burned_out_m2": float(area[roof & burned].sum()),
        "ground_burned_out_m2": float(area[~roof & burned].sum()),
        "active_m2": float(area[burning].sum()),
        "source_area_m2": float(area[initial].sum()),
        "source_energy_J": float(request.parameters.available_energy_J.ravel()[initial].sum()),
        "energy_closure_error_J": max(qa["energy_closure_errors_J"].values()),
        "energy_closure_tolerance_J": qa["energy_tolerance_J"],
        "boundary": boundary,
    }


def run_attempt(domain, layout, graph, contact, plan, old, core_ids):
    from heritage_wind_terminal_engine import TerminalWindClock

    # With a single cohort of direct time-zero ignitions, the last flame cannot
    # outlive the sum of all finite node burn durations (plus one clock step per node).
    # No result is accepted at a time limit without an extinction certificate.
    p = layout.node_inputs.parameters
    horizon = 3601 + float(np.sum(p.growth_time_s + p.steady_time_s + p.decay_time_s + 1))
    base, retained, ids, initial = retained_inputs(domain, layout, plan, horizon)
    request, selected_contact = resolve_operators(retained, ids, base, graph, contact)
    owners = [layout.owners[i] for i in retained]
    core_set = set(core_ids)
    monitored = np.array(
        [i for i, o in enumerate(owners) if core_set.intersection(o)], dtype=np.int64
    )
    cells = layout.cell_id[retained]
    clock = TerminalWindClock(request, selected_contact, monitored_nodes=monitored)
    clock.advance(3600, stop_on_extinction=False)
    while True:
        result = clock.result()
        state = result.final_state
        boundary = domain.boundary_audit(
            cells[np.isfinite(state.ignition_time_s.ravel())], old["radius_m"]
        )
        if not boundary["closed"]:
            if (
                not boundary["exterior_cfl_allows_max_dt"]
                or boundary["exterior_can_ignite_at_ambient"]
            ):
                raise ValueError("The exterior can change the thermal clock")
            return None
        if math.isclose(state.time_s, 3600, rel_tol=0, abs_tol=1e-8):
            metrics = observations(
                request, result, clock.peak_K, owners, core_ids, initial, boundary
            )
            ensure_replay(old, metrics)
        if not np.any(state.state == 3):
            proof = certify_extinction_v3(
                request, state, post_apply_events_boundary=True, contact=selected_contact
            )
            if not proof["closed"]:
                raise ValueError(
                    f"Final cooling proof rejected: {old['case_id']}: {proof['rejection_reasons']}"
                )
            final = observations(request, result, clock.peak_K, owners, core_ids, initial, boundary)
            final["last_burnout_s"] = float(np.nanmax(state.burnout_time_s))
            return final, proof["certificate"]
        if state.time_s >= horizon:
            raise RuntimeError(f"Finite duration bound exhausted: {old['case_id']}")
        next_check = min(horizon, state.time_s + 3600)
        del result, state
        clock.advance(next_check, exact=False)


def group_job(args):
    source, root, village, fire_id, case_ids, window, signature = args
    reg = json.loads((source / "registration.json").read_text())
    setup = reg["villages"][village]
    fire = next(f for f in setup["fires"] if f["fire_id"] == fire_id)
    bundle, _, inv = study(setup["study_path"])
    domain, layout, integrated = prepare_domain(
        bundle,
        inv,
        fire,
        setup["core_ids"],
        window,
        scale=reg["primary"]["roof_scale_m"],
        overlap=setup["core_overlap_policy"],
    )
    geometry = WindRadiationGeometry(layout, integrated, reg["primary"]["quadrature_spacing_m"])
    contact = refined_contact_operator(layout)
    # These original transparent thermal inputs were fingerprinted by the first experiment.
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    plans = {plan["plan_id"]: plan for plan in setup["plans"]}
    batch = defaultdict(list)
    for key in case_ids:
        old = json.loads((source / "cases" / f"{key}.json").read_text())
        batch[old["wind_id"]].append(old)
    retry = []
    for wind in reg["winds"]:
        if wind["wind_id"] not in batch:
            continue
        windy = replace(
            transport,
            **{field: wind[field] for field in WIND_FIELDS},
            exposure_radius_m=reg["primary"]["radius_m"],
        )
        graph = geometry.graph(windy, max_edges=30_000_000)
        for old in batch[wind["wind_id"]]:
            result = run_attempt(
                domain, layout, graph, contact, plans[old["plan_id"]], old, setup["core_ids"]
            )
            if result is None:
                if domain.window == (0, bundle.request.grid.height, 0, bundle.request.grid.width):
                    raise RuntimeError("Cannot close the full original model domain")
                retry.append(old["case_id"])
            else:
                seal(root, old, signature, *result)
        del graph
    return retry


def completed(root, row, signature):
    file = root / "cases" / (row["case_id"] + ".json")
    if not file.exists():
        return False
    saved = json.loads(file.read_text())
    checksum = saved.pop("result_sha256")
    cert = json.loads((root / "certificates" / file.name).read_text())
    if (
        record_hash(saved) != checksum
        or saved["original_result_sha256"] != row["result_sha256"]
        or saved["solver_sha256"] != signature
        or saved["active_m2"]
        or not saved["extinction_closed"]
        or extinction_certificate_sha256(cert) != saved["extinction_certificate_sha256"]
    ):
        raise ValueError(f"Corrupt or incompatible saved case: {file}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ORIGINAL)
    parser.add_argument("--output", type=Path, default=DEST)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--large-workers", type=int, default=2)
    parser.add_argument(
        "--case", action="append", help="Limit execution to a specified original case (repeatable)."
    )
    args = parser.parse_args()
    source, root = args.source.resolve(), args.output.resolve()
    reg, originals, active = signed_inventory(source)
    signature = terminal_signature(originals[0]["solver_sha256"])
    for name in ("cases", "certificates"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = {
        "original_registration_sha256": originals[0]["registration_sha256"],
        "original_solver_sha256": originals[0]["solver_sha256"],
        "terminal_solver_sha256": signature,
        "continued": [row["case_id"] for row in active],
        "already_extinct": [r["case_id"] for r in originals if r["extinction_closed"]],
        "comparison": "All 720 primary radius-15-m cases, until certified modeled extinction.",
    }
    atomic_json(root / "registration.json", manifest)
    if args.case and not set(args.case) <= {r["case_id"] for r in active}:
        raise ValueError("Only active registered primary cases may be continued")
    pending = {
        row["case_id"]: row
        for row in active
        if (not args.case or row["case_id"] in args.case) and not completed(root, row, signature)
    }
    window = 220.0
    while pending:
        grouped = defaultdict(list)
        size = 2 if window <= 440 else 1
        for key, row in pending.items():
            wind_index = reg["primary"]["wind_ids"].index(row["wind_id"])
            grouped[row["village"], row["fire_id"], wind_index // size].append(key)
        jobs = [
            (source, root, v, fire, keys, window, signature)
            for (v, fire, _), keys in grouped.items()
        ]
        cap = args.workers if window <= 440 else args.large_workers
        print(
            f"window {window:g} m, {len(pending)} cases in {len(jobs)} jobs, {cap} workers",
            flush=True,
        )
        retry = {}
        with ProcessPoolExecutor(max_workers=cap) as pool:
            futures = {pool.submit(group_job, job): job for job in jobs}
            for future in as_completed(futures):
                failed = future.result()
                retry.update((key, pending[key]) for key in failed)
                job = futures[future]
                print(
                    time.strftime("%H:%M:%S"),
                    job[2:4],
                    f"completed {len(job[4]) - len(failed)}, expand {len(failed)}",
                    flush=True,
                )
        pending = retry
        window *= 2
    print("All selected cases have a verified terminal record.", flush=True)


if __name__ == "__main__":
    main()
