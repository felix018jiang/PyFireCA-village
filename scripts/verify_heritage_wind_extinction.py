"""Replay selected continued cases with the original NumPy V3 clock to check terminal outcomes."""

import argparse
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
from heritage_wind_radiation import WindRadiationGeometry
from run_heritage_wind_extinction import DEST, ORIGINAL, WIND_FIELDS
from run_heritage_wind_strategies import (
    atomic_json,
    prepare_domain,
    record_hash,
    resolve_operators,
    retained_inputs,
    study,
)

from pyfireca.planning.roof_ignition import refined_contact_operator
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

EXAMPLES = (
    "pingyan_fixed-4_S4_r15_multipoint-narrow",
    "pingyan_fixed-1_N4_r15_baseline",
    "batuan_random-1_W4_r15_multipoint-wide",
)


def read_observation(directory, case_id):
    record = json.loads((directory / "cases" / f"{case_id}.json").read_text())
    signature = record.pop("result_sha256")
    if record_hash(record) != signature or record["case_id"] != case_id:
        raise ValueError(f"Corrupt study observation: {case_id}")
    record["result_sha256"] = signature
    return record


def verify(original, final, registration, window):
    village = registration["villages"][original["village"]]
    fire = next(f for f in village["fires"] if f["fire_id"] == original["fire_id"])
    wind = next(w for w in registration["winds"] if w["wind_id"] == original["wind_id"])
    plan = next(p for p in village["plans"] if p["plan_id"] == original["plan_id"])
    bundle, _, inventory = study(village["study_path"])
    domain, layout, integrated = prepare_domain(
        bundle,
        inventory,
        fire,
        village["core_ids"],
        window,
        scale=registration["primary"]["roof_scale_m"],
        overlap=village["core_overlap_policy"],
    )
    transport = replace(
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        exposure_radius_m=original["radius_m"],
        **{name: wind[name] for name in WIND_FIELDS},
    )
    geometry = WindRadiationGeometry(
        layout, integrated, registration["primary"]["quadrature_spacing_m"]
    )
    graph = geometry.graph(transport, max_edges=30_000_000)
    base, retained, ids, _ = retained_inputs(domain, layout, plan, final["last_burnout_s"] + 120)
    request, contact = resolve_operators(
        retained, ids, base, graph, refined_contact_operator(layout)
    )
    native = _run_resolved_v3_simulation(
        request,
        contact=contact,
        retain_snapshots=False,
        stop_when=lambda s: not np.any(s.state == 3),
    )
    state = native.final_state
    proof = certify_extinction_v3(request, state, post_apply_events_boundary=True, contact=contact)
    qa = check_final(request, native)
    ignitions = state.ignition_time_s.ravel()
    burning = state.state.ravel() == 3
    burned = state.state.ravel() == 4
    boundary = domain.boundary_audit(
        layout.cell_id[retained][np.isfinite(ignitions)], original["radius_m"]
    )
    if not proof["closed"] or np.any(burning) or not boundary["closed"]:
        raise ValueError(
            f"The native control needs a larger spatial or time window: {original['case_id']}"
        )
    owners = [layout.owners[i] for i in retained]
    roof = np.fromiter((bool(o) for o in owners), bool)
    area = request.parameters.covered_area_m2.ravel()
    cores = set(village["core_ids"])
    ignition_by_core = {}
    for key in sorted(cores):
        selected = np.fromiter((key in o for o in owners), bool)
        ts = ignitions[selected & np.isfinite(ignitions)]
        ignition_by_core[key] = float(ts.min()) if ts.size else None
    observations = {
        "last_burnout_s": float(np.nanmax(state.burnout_time_s)),
        "active_m2": float(area[burning].sum()),
        "roof_burned_out_m2": float(area[roof & burned].sum()),
        "ground_burned_out_m2": float(area[~roof & burned].sum()),
        "ordinary_roof_burned_out_count": len(
            {o for i in np.flatnonzero(roof & burned) for o in owners[i]} - cores
        ),
        "core_ignited_count": sum(t is not None for t in ignition_by_core.values()),
    }
    differences = {name: value - final[name] for name, value in observations.items()}
    if any(abs(delta) > 1e-6 for delta in differences.values()):
        raise ValueError(f"Terminal discrepancies in {original['case_id']}: {differences}")
    for key, ts in ignition_by_core.items():
        measured = final["core_outcomes"][key]["first_ignition_s"]
        if ts is None and measured is None:
            continue
        if (
            ts is None
            or measured is None
            or not math.isclose(ts, measured, rel_tol=0, abs_tol=1e-6)
        ):
            raise ValueError(f"Core ignition-time mismatch for {key}: {original['case_id']}")
    return {
        "case_id": original["case_id"],
        "original_result_sha256": original["result_sha256"],
        "production_terminal_result_sha256": final["result_sha256"],
        "original_active_m2": original["active_m2"],
        "native_terminal": observations,
        "native_minus_production_terminal": differences,
        "native_core_first_ignition_s": ignition_by_core,
        "native_boundary": boundary,
        "native_extinction_certificate": proof["certificate"],
        "native_energy_closure_error_J": max(qa["energy_closure_errors_J"].values()),
        "native_energy_closure_tolerance_J": qa["energy_tolerance_J"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ORIGINAL)
    parser.add_argument("--terminal", type=Path, default=DEST)
    parser.add_argument("--case", action="append", help="A registered active case ID")
    parser.add_argument("--window-m", type=float, default=220)
    args = parser.parse_args()
    registration_file = args.source / "registration.json"
    registration = json.loads(registration_file.read_text())
    registration_hash = hashlib.sha256(registration_file.read_bytes()).hexdigest()
    cases = []
    for case_id in args.case or EXAMPLES:
        original = read_observation(args.source, case_id)
        final = read_observation(args.terminal, case_id)
        if (
            original["active_m2"] <= 0
            or original["radius_m"] != 15
            or original["registration_sha256"] != registration_hash
            or final["original_result_sha256"] != original["result_sha256"]
            or not final["extinction_closed"]
            or not final["replay_60min_verified"]
        ):
            raise ValueError(f"Expected a continued, certified primary case: {case_id}")
        cases.append(verify(original, final, registration, args.window_m))
    receipt = {
        "method": "Original NumPy V3 clock from registered time-zero ignition to extinction",
        "original_registration_file_sha256": registration_hash,
        "sample_count": len(cases),
        "cases": cases,
        "absolute_comparison_tolerance_m2_or_s": 1e-6,
    }
    folder = args.terminal / "native-checks"
    folder.mkdir(exist_ok=True)
    atomic_json(folder / "verification.json", receipt)
    print(f"Verified {len(cases)} terminal cases against the native solver.")


if __name__ == "__main__":
    main()
