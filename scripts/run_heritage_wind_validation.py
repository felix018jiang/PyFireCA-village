"""Run the independent, frozen source panel on the original thermal and firebreak model."""

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
from heritage_wind_terminal_engine import TerminalWindClock
from run_heritage_wind_extinction import WIND_FIELDS, observations, terminal_signature
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
from pyfireca.village.extinction_v3 import certify_extinction_v3, extinction_certificate_sha256
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

DEST = ROOT / "output/heritage-wind-validation-20261112"


def case_name(village, fire, wind, plan):
    return f"{village}_{fire}_{wind}_r15_{plan}"


def checked_row(path, reg_hash, model_hash):
    row = json.loads(path.read_bytes())
    stamp = row.pop("result_sha256")
    if (
        stamp != record_hash(row)
        or row["case_id"] != path.stem
        or row["registration_sha256"] != reg_hash
        or row["solver_sha256"] != model_hash
    ):
        raise ValueError(f"Broken evidence chain for {path}")
    return row


def run_physics(domain, layout, graph, contact, plan, core_ids, radius):
    physics = layout.node_inputs.parameters
    bound = 3601 + float(
        np.sum(physics.growth_time_s + physics.steady_time_s + physics.decay_time_s + 1)
    )
    base, retained, ids, initial = retained_inputs(domain, layout, plan, bound)
    request, contacts = resolve_operators(retained, ids, base, graph, contact)
    owners = [layout.owners[i] for i in retained]
    targets = set(core_ids)
    monitored = np.array(
        [i for i, o in enumerate(owners) if targets.intersection(o)], dtype=np.int64
    )
    cells = layout.cell_id[retained]
    clock = TerminalWindClock(request, contacts, monitored_nodes=monitored)
    seconds = 3600.0

    def guard():
        burning_history = np.isfinite(clock.state.ignition_time_s.ravel())
        boundary = domain.boundary_audit(cells[burning_history], radius)
        if not boundary["exterior_cfl_allows_max_dt"] or boundary["exterior_can_ignite_at_ambient"]:
            raise ValueError("Unresolved physical exterior time step or cold ignition")
        return boundary

    def readout(boundary):
        return observations(
            request, clock.result(), clock.peak_K, owners, core_ids, initial, boundary
        )

    def certify(boundary):
        proof = certify_extinction_v3(
            request, clock.state, post_apply_events_boundary=True, contact=contacts
        )
        if not proof["closed"]:
            raise ValueError(f"Rejected extinction: {proof['rejection_reasons']}")
        obs = readout(boundary)
        obs["last_burnout_s"] = float(np.nanmax(clock.state.burnout_time_s))
        return obs, proof["certificate"]

    clock.advance(seconds, stop_on_extinction=True)
    boundary = guard()
    if not boundary["closed"]:
        return None, None, None
    early = not np.any(clock.state.state == 3)
    if early:
        final, cert = certify(boundary)
        if clock.state.time_s < seconds - 1e-8:
            clock.advance(seconds, stop_on_extinction=False)
        hour = readout(guard())
        return hour, final, cert

    hour = readout(boundary)
    while np.any(clock.state.state == 3):
        if clock.state.time_s >= bound:
            raise ValueError("Unresolved burning at the finite-fuel bound")
        # Check at natural step boundaries to preserve the native thermal clock.
        clock.advance(min(bound, clock.state.time_s + 3600), exact=False)
        boundary = guard()
        if not boundary["closed"]:
            return hour, None, None
    final, cert = certify(boundary)
    return hour, final, cert


def check_hour(before, after):
    for k in (
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
    ):
        if not math.isclose(before[k], after[k], rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError(f"Across-window 60-minute mismatch for {k}: {before[k]} vs {after[k]}")
    for target, obs in before["core_outcomes"].items():
        for field, val in obs.items():
            new = after["core_outcomes"][target][field]
            if val is None and new is None:
                continue
            if val is None or new is None or not math.isclose(val, new, rel_tol=1e-8, abs_tol=1e-5):
                raise ValueError(f"Across-window 60-minute mismatch for {target}/{field}")


def group(args):
    root, reg_hash, model_hash, village, fire_id, window, winds = args
    reg = json.loads((root / "registration.json").read_bytes())
    config = reg["villages"][village]
    fire = next(f for f in config["fires"] if f["fire_id"] == fire_id)
    bundle, _, inventory = study(config["study_path"])
    radius = reg["primary"]["radius_m"]
    physical_radius = (
        2
        * max(bundle.request.grid.height, bundle.request.grid.width)
        * bundle.request.grid.cell_size_m
        if window == "full"
        else float(window)
    )
    domain, layout, integrated = prepare_domain(
        bundle,
        inventory,
        fire,
        config["core_ids"],
        physical_radius,
        scale=reg["primary"]["roof_scale_m"],
        overlap=config["core_overlap_policy"],
    )
    geometry = WindRadiationGeometry(layout, integrated, reg["primary"]["quadrature_spacing_m"])
    contacts = refined_contact_operator(layout)
    transport = V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"])
    plans = {p["plan_id"]: p for p in config["plans"]}
    closed = retry = 0
    for wind in reg["winds"]:
        w = wind["wind_id"]
        if w not in winds:
            continue
        windy = replace(transport, exposure_radius_m=radius, **{k: wind[k] for k in WIND_FIELDS})
        graph = geometry.graph(windy, max_edges=30_000_000)
        for p in winds[w]:
            key = case_name(village, fire_id, w, p)
            file = root / "cases" / (key + ".json")
            marker = root / "expansions" / (key + ".json")
            if file.exists():
                checked_row(file, reg_hash, model_hash)
                continue
            hour, final, cert = run_physics(
                domain, layout, graph, contacts, plans[p], config["core_ids"], radius
            )
            ident = {
                "case_id": key,
                "village": village,
                "fire_id": fire_id,
                "stratum": fire["stratum"],
                "kind": fire["kind"],
                "wind_id": w,
                "radius_m": radius,
                "plan_id": p,
                "wind_speed_m_s": wind["wind_speed_m_s"],
                "wind_from_deg": wind["wind_from_deg"],
                "registration_sha256": reg_hash,
                "solver_sha256": model_hash,
                "footprint_sha256": fire["footprint_sha256"],
            }
            if hour is not None and (
                abs(hour["elapsed_simulated_s"] - 3600) > 1e-8
                or not math.isclose(hour["source_area_m2"], 25, rel_tol=0, abs_tol=1e-6)
            ):
                raise ValueError(f"Unresolved 60-minute readout or source area: {key}")
            if final is None:
                if window == "full":
                    raise ValueError(f"Full domain not closed: {key}")
                row = {**ident, "at_60min": hour, "window_m": physical_radius}
                row["result_sha256"] = record_hash(row)
                atomic_json(marker, row)
                retry += 1
                continue
            if marker.exists():
                previous = checked_row(marker, reg_hash, model_hash)["at_60min"]
                if previous is not None:
                    check_hour(previous, hour)
            if (
                cert["certificate_sha256"] != extinction_certificate_sha256(cert)
                or final["active_m2"] != 0
                or not final["boundary"]["closed"]
                or not hour["boundary"]["closed"]
            ):
                raise ValueError(f"Invalid terminal evidence: {key}")
            atomic_json(root / "certificates" / (key + ".json"), cert)
            row = {
                **ident,
                "at_60min": hour,
                "final": final,
                "extinction_closed": True,
                "extinction_certificate_sha256": cert["certificate_sha256"],
            }
            row["result_sha256"] = record_hash(row)
            atomic_json(file, row)
            closed += 1
        del graph
    return closed, retry


def schedule(root, reg, reg_hash, model_hash, villages, window, pack, fire_filter):
    jobs = []
    complete = expand = 0
    for v in villages:
        setup = reg["villages"][v]
        for fire in setup["fires"]:
            f = fire["fire_id"]
            if fire_filter and f != fire_filter:
                continue
            by_wind = defaultdict(list)
            for w in reg["primary"]["wind_ids"]:
                for plan in setup["plans"]:
                    pid = plan["plan_id"]
                    key = case_name(v, f, w, pid)
                    file = root / "cases" / (key + ".json")
                    marker = root / "expansions" / (key + ".json")
                    if file.exists():
                        row = checked_row(file, reg_hash, model_hash)
                        cert = json.loads((root / "certificates" / file.name).read_bytes())
                        if (
                            extinction_certificate_sha256(cert)
                            != row["extinction_certificate_sha256"]
                            or row["final"]["active_m2"]
                            or not row["extinction_closed"]
                        ):
                            raise ValueError(f"Invalid stored certificate: {key}")
                        complete += 1
                    elif window != "full" and marker.exists():
                        checked_row(marker, reg_hash, model_hash)
                        expand += 1
                    else:
                        by_wind[w].append(pid)
            wind_list = list(by_wind)
            for i in range(0, len(wind_list), pack):
                schedule_winds = {w: by_wind[w] for w in wind_list[i : i + pack]}
                jobs.append((root, reg_hash, model_hash, v, f, window, schedule_winds))
    return jobs, complete, expand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEST)
    parser.add_argument("--village", action="append", choices=["pingyan", "batuan"])
    parser.add_argument("--fire")
    parser.add_argument("--window", choices=["220", "full"], required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--wind-pack", type=int, default=3)
    args = parser.parse_args()
    if min(args.workers, args.wind_pack) < 1:
        parser.error("positive worker count and wind pack required")
    root = args.output.resolve()
    reg_path = root / "registration.json"
    reg = json.loads(reg_path.read_bytes())
    if (
        reg["primary"]["planned_pairs"] != 4635
        or reg["primary"]["radius_m"] != 15
        or reg["primary"]["horizon_s"] != 3600
    ):
        raise ValueError("Incorrect independent experiment")
    for setup in reg["villages"].values():
        for path, checksum in setup["input_files_sha256"].items():
            if digest(ROOT / path) != checksum:
                raise ValueError(f"Registered input changed: {path}")
    for d in ("cases", "certificates", "expansions"):
        (root / d).mkdir(exist_ok=True)
    reg_hash = digest(reg_path)
    native = terminal_signature(solver_signature())
    model_hash = hashlib.sha256((native + digest(Path(__file__))).encode()).hexdigest()
    jobs, complete, expand = schedule(
        root,
        reg,
        reg_hash,
        model_hash,
        args.village or list(reg["villages"]),
        args.window,
        args.wind_pack,
        args.fire,
    )
    print(
        time.strftime("%H:%M:%S"),
        f"Window {args.window}; {complete} complete; {expand} pending expansion; {len(jobs)} jobs",
        flush=True,
    )
    failures = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(group, job): job for job in jobs}
        for f in as_completed(futures):
            job = futures[f]
            try:
                new, retry = f.result()
            except Exception as exc:
                failures += 1
                print(time.strftime("%H:%M:%S"), job[3:5], "ERROR", repr(exc), flush=True)
            else:
                print(
                    time.strftime("%H:%M:%S"), job[3:5], "closed", new, "expand", retry, flush=True
                )
    if failures:
        raise SystemExit(f"{failures} failed jobs; valid records can be resumed")


if __name__ == "__main__":
    main()
