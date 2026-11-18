"""Independently replay held-out wind cases with the original NumPy V3 solver."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from heritage_wind_radiation import WindRadiationGeometry
from prepare_heritage_wind_validation import DEFAULT_OUTPUT
from run_heritage_wind_extinction import WIND_FIELDS
from run_heritage_wind_strategies import (
    atomic_json,
    digest,
    prepare_domain,
    record_hash,
    resolve_operators,
    retained_inputs,
    study,
)
from verify_heritage_wind_extinction import read_observation

from pyfireca.planning.roof_ignition import refined_contact_operator
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3, extinction_certificate_sha256
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters


def summarize_state(state, area, owners, core_ids, peak, ambient):
    roof = np.asarray([bool(owner) for owner in owners])
    ignition = state.ignition_time_s.ravel()
    ignited = np.isfinite(ignition)
    burned = state.state.ravel() == 4
    cores = set(core_ids)
    core_nodes = [i for i, owner in enumerate(owners) if cores.intersection(owner)]

    def labels(mask):
        return len({label for i in np.flatnonzero(roof & mask) for label in owners[i]} - cores)

    first = {}
    for core in core_ids:
        hits = [ignition[i] for i in core_nodes if core in owners[i] and ignited[i]]
        first[core] = float(min(hits)) if hits else None
    return {
        "elapsed_simulated_s": float(state.time_s),
        "roof_ignited_m2": float(area[roof & ignited].sum()),
        "roof_burned_out_m2": float(area[roof & burned].sum()),
        "ground_ignited_m2": float(area[~roof & ignited].sum()),
        "ground_burned_out_m2": float(area[~roof & burned].sum()),
        "ordinary_roof_ignited_count": labels(ignited),
        "ordinary_roof_burned_out_count": labels(burned),
        "active_m2": float(area[state.state.ravel() == 3].sum()),
        "core_ignited_count": sum(t is not None for t in first.values()),
        "core_peak_rise_K": float(peak.max(initial=ambient) - ambient),
        "core_absorbed_radiation_J": float(state.absorbed_radiation_J.ravel()[core_nodes].sum()),
    }, first


def compare(measured, first, expected, key):
    delta = {k: v - expected[k] for k, v in measured.items()}
    for field, err in delta.items():
        relative = 1e-9 if field.endswith("_J") else 0
        if not np.isclose(measured[field], expected[field], rtol=relative, atol=1e-6):
            raise ValueError(f"{key}: {field} discrepancy {err}")
    for core, onset in first.items():
        reference = expected["core_outcomes"][core]["first_ignition_s"]
        if onset is None and reference is None:
            continue
        if (
            onset is None
            or reference is None
            or not np.isclose(onset, reference, rtol=0, atol=1e-6)
        ):
            raise ValueError(f"{key}: divergent first ignition time for heritage")
    return delta


def verify(folder, reg, case_id, window):
    row = read_observation(folder, case_id)
    reg_hash = digest(folder / "registration.json")
    cert = json.loads((folder / "certificates" / f"{case_id}.json").read_bytes())
    if (
        row["registration_sha256"] != reg_hash
        or not row["extinction_closed"]
        or extinction_certificate_sha256(cert) != row["extinction_certificate_sha256"]
    ):
        raise ValueError(f"{case_id}: invalid reference")
    v = reg["villages"][row["village"]]
    fire = next(f for f in v["fires"] if f["fire_id"] == row["fire_id"])
    wind = next(w for w in reg["winds"] if w["wind_id"] == row["wind_id"])
    plan = next(p for p in v["plans"] if p["plan_id"] == row["plan_id"])
    if row["footprint_sha256"] != fire["footprint_sha256"] or row["stratum"] != fire["stratum"]:
        raise ValueError(f"{case_id}: frozen source differs")
    bundle, _, inventory = study(v["study_path"])
    domain, layout, integrated = prepare_domain(
        bundle,
        inventory,
        fire,
        v["core_ids"],
        window,
        scale=reg["primary"]["roof_scale_m"],
        overlap=v["core_overlap_policy"],
    )
    transport = replace(
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        exposure_radius_m=row["radius_m"],
        **{k: wind[k] for k in WIND_FIELDS},
    )
    graph = WindRadiationGeometry(layout, integrated, reg["primary"]["quadrature_spacing_m"]).graph(
        transport, max_edges=30_000_000
    )
    last = row["final"]["last_burnout_s"]
    horizon = max(last, 3600) + 1
    request, retained, ids, _ = retained_inputs(domain, layout, plan, horizon)
    request, contact = resolve_operators(
        retained, ids, request, graph, refined_contact_operator(layout)
    )
    # Both frozen sampling clocks already have an exact 3600 s boundary.
    if request.sample_interval_s is None or 3600 % request.sample_interval_s:
        raise ValueError("Native frozen sampling clock does not include 60 minutes")
    area = request.parameters.covered_area_m2.ravel()
    owners = [layout.owners[i] for i in retained]
    cells = layout.cell_id[retained]
    core = np.asarray([bool(set(v["core_ids"]).intersection(owner)) for owner in owners])
    ambient = request.scenario.ambient_temperature_K
    peak = np.full(int(core.sum()), ambient)
    checks = {}
    native_cert = None

    def observe(state):
        nonlocal native_cert
        hot = state.surface_temperature_K.ravel()[core]
        np.maximum(peak, hot, where=state.temperature_valid_mask.ravel()[core], out=peak)
        at_hour = abs(state.time_s - 3600) <= 1e-8
        at_end = native_cert is None and not np.any(state.state == 3)
        if at_hour or at_end:
            measured, onset = summarize_state(state, area, owners, v["core_ids"], peak, ambient)
            boundary = domain.boundary_audit(
                cells[np.isfinite(state.ignition_time_s.ravel())], row["radius_m"]
            )
            if not boundary["closed"]:
                raise ValueError(f"{case_id}: native validation window needs enlargement")
            if at_end:
                proof = certify_extinction_v3(
                    request, state, post_apply_events_boundary=True, contact=contact
                )
                if not proof["closed"]:
                    raise ValueError(f"{case_id}: independent extinction proof rejected")
                native_cert = proof["certificate"]
            for key, due in (("at_60min", at_hour), ("final", at_end)):
                if due:
                    values = dict(measured)
                    if key == "final":
                        values["last_burnout_s"] = float(np.nanmax(state.burnout_time_s))
                    checks[key] = {
                        "native": values,
                        "native_minus_production": compare(values, onset, row[key], case_id),
                        "first_ignition_s_by_heritage": onset,
                        "boundary": boundary,
                    }
        return native_cert is not None and "at_60min" in checks

    native = _run_resolved_v3_simulation(
        request, contact=contact, retain_snapshots=False, stop_when=observe
    )
    observe(native.final_state)
    qa = check_final(request, native)
    if set(checks) != {"at_60min", "final"} or native_cert is None:
        raise ValueError(f"{case_id}: independent run did not complete both observations")
    return {
        "case_id": case_id,
        "village": row["village"],
        "stratum": row["stratum"],
        "wind_id": row["wind_id"],
        "plan_id": row["plan_id"],
        "window_m": window,
        "registration_sha256": reg_hash,
        "production_result_sha256": row["result_sha256"],
        "production_certificate_sha256": row["extinction_certificate_sha256"],
        "native_extinction_certificate": native_cert,
        "checks": checks,
        "native_energy_closure_error_J": max(qa["energy_closure_errors_J"].values()),
        "native_energy_closure_tolerance_J": qa["energy_tolerance_J"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--case", action="append", required=True)
    p.add_argument("--window-m", type=float, default=220)
    args = p.parse_args()
    folder = args.output.resolve()
    reg = json.loads((folder / "registration.json").read_bytes())
    check_dir = folder / "native-checks"
    check_dir.mkdir(exist_ok=True)
    for key in args.case:
        data = verify(folder, reg, key, args.window_m)
        data["verification_sha256"] = record_hash(data)
        atomic_json(check_dir / f"{key}.json", data)
        print("Native hour and terminal verified:", key, flush=True)
    cases = []
    for f in sorted(check_dir.glob("*.json")):
        receipt = json.loads(f.read_bytes())
        signature = receipt.pop("verification_sha256")
        if record_hash(receipt) != signature or receipt["registration_sha256"] != digest(
            folder / "registration.json"
        ):
            raise ValueError(f"Stale or altered native QA receipt: {f}")
        cases.append(
            dict(
                case_id=receipt["case_id"],
                village=receipt["village"],
                stratum=receipt["stratum"],
                plan_id=receipt["plan_id"],
                wind_id=receipt["wind_id"],
                active_m2_at_60min=receipt["checks"]["at_60min"]["native"]["active_m2"],
                last_burnout_s=receipt["checks"]["final"]["native"]["last_burnout_s"],
                verification_sha256=signature,
                receipt_path=str(f.relative_to(folder)),
            )
        )
    summary = {
        "method": "Independent original NumPy V3 replay, both at 3600 s and certified extinction",
        "registration_sha256": digest(folder / "registration.json"),
        "sample_count": len(cases),
        "cases": cases,
    }
    summary["verification_sha256"] = record_hash(summary)
    atomic_json(folder / "verification.json", summary)


if __name__ == "__main__":
    main()
