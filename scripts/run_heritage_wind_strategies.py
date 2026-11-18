"""Run registered wind, layout and ignition combinations on the native V3 thermal clock."""

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from heritage_local_wind_domain import crop_wind_domain
from heritage_wind_radiation import WindRadiationGeometry
from shapely.geometry import shape

from pyfireca.planning.environments import apply_static_wind
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.study_io import load_study
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "output/heritage-wind-20261112"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solver_signature():
    source = sorted((ROOT / "src/pyfireca").rglob("*.py"))
    source += [
        Path(__file__),
        Path(__file__).with_name("heritage_local_wind_domain.py"),
        Path(__file__).with_name("heritage_wind_radiation.py"),
    ]
    hashes = [(str(p.relative_to(ROOT)), digest(p)) for p in source]
    return hashlib.sha256(json.dumps(hashes).encode()).hexdigest()


def atomic_json(path, obj):
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    temporary.replace(path)


@lru_cache(maxsize=1)
def study(path):
    return load_study(ROOT / path)


def prepare_domain(
    bundle, inventory, fire, core_ids, window_m, *, scale=1.0, overlap="heritage_priority"
):
    domain = crop_wind_domain(bundle, inventory, fire["anchor_m"], window_m)
    base = resolve_roof_layout(
        domain.bundle.request,
        domain.inventory,
        max_nodes=600_000,
        core_overlap_policy=overlap,
    )
    footprint = RoofIgnitionFootprint(
        fire["source_id"], shape(fire["geometry"]), fire["provenance"]
    )
    if footprint.sha256 != fire["footprint_sha256"]:
        raise ValueError("altered registered ignition footprint")
    ids = sorted(({fire["source_id"]} | set(core_ids)) & domain.inventory.by_id.keys())
    layout = refine_roof_layout(
        base,
        domain.inventory,
        cell_size_m=scale,
        refined_ids=ids,
        footprints=(footprint,),
        max_nodes=650_000,
    )
    return domain, layout, ids


def retained_inputs(domain, layout, plan, horizon_s):
    # Select the same surviving physical nodes as whole-owner roof demolition and
    # whole ground-cell clearance. Shared atoms survive whenever any owner remains.
    removed = set(plan["demolished_ids"])
    ground = {domain.to_local_cell(int(i)) for i in plan["cleared_physical_cell_ids"]}
    ground.discard(None)
    cells = layout.cell_id
    keep = np.array(
        [
            not (set(ids) <= removed) if ids else cell not in ground
            for ids, cell in zip(layout.owners, cells, strict=True)
        ]
    )
    retained = np.flatnonzero(keep)
    ids = np.full(len(keep), -1, dtype=np.int32)
    ids[keep] = np.arange(len(retained), dtype=np.int32)
    initial = np.array([owner is not None for owner in layout.ignition_owner])
    if np.any(initial & ~keep):
        raise ValueError("a policy removed part of the common ignition footprint")
    events = tuple(VillageIgnitionEvent(0.0, 0, int(i)) for i in ids[initial])
    if not events:
        raise ValueError("no ignition footprint nodes")
    raw = layout.node_inputs
    old = raw.parameters
    thermal = replace(old, **{k: getattr(old, k)[:, keep] for k in old.__dataclass_fields__})
    grid = replace(raw.grid, width=len(retained))
    placeholder = ExposureEdges(
        np.array([], np.int32), np.array([], np.int32), np.array([]), len(retained)
    )
    fields = {
        k: getattr(raw, k)[:, keep]
        for k in (
            "domain_mask",
            "known_mask",
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
        )
    }
    inputs = replace(raw, grid=grid, parameters=thermal, exposure_edges=placeholder, **fields)
    scenario = replace(
        domain.bundle.request.scenario, max_dt_s=1.0, end_time_s=horizon_s, ignitions=events
    )
    return inputs.bind(scenario), retained, ids, initial[keep]


def resolve_operators(retained, ids, request, graph, contact):
    surviving = (ids[graph.source] >= 0) & (ids[graph.target] >= 0)
    edges = ExposureEdges(
        ids[graph.source[surviving]],
        ids[graph.target[surviving]],
        graph.weight[surviving],
        len(retained),
    )
    request = replace(request, exposure_edges=edges)
    if contact is None:
        return request, None
    old = contact.neighbors[retained]
    neighbors = np.where(old >= 0, ids[old], -1)
    fractions = np.where(neighbors >= 0, contact.geometry_fraction[retained], 0.0)
    return request, replace(
        contact, neighbors=neighbors, geometry_fraction=fractions, physics=request.parameters
    )


def run_case(domain, layout, plan, base_graph, base_contact, core_ids, radius_m, horizon_s):
    base, retained, ids, initial = retained_inputs(domain, layout, plan, horizon_s)
    request, contact = resolve_operators(retained, ids, base, base_graph, base_contact)
    core_set = set(core_ids)
    owners = [layout.owners[i] for i in retained]
    roof = np.array([bool(o) for o in owners])
    core = np.array([bool(core_set.intersection(o)) for o in owners])
    cells = layout.cell_id[retained]
    ambient = request.scenario.ambient_temperature_K
    peak = np.full(int(core.sum()), ambient)
    certificate = None
    next_audit = 0.0

    def observe(state):
        nonlocal certificate, next_audit
        temp = state.surface_temperature_K.ravel()[core]
        np.maximum(peak, temp, where=state.temperature_valid_mask.ravel()[core], out=peak)
        if state.time_s < next_audit:
            return False
        next_audit = state.time_s + 60.0
        if not domain.boundary_audit(cells[np.isfinite(state.ignition_time_s.ravel())], radius_m)[
            "artificial_boundary_clear"
        ]:
            return True
        if np.any(state.state == 3):
            return False
        attempt = certify_extinction_v3(
            request, state, post_apply_events_boundary=True, contact=contact
        )
        certificate = attempt["certificate"]
        return bool(attempt["closed"])

    result = _run_resolved_v3_simulation(
        request, contact=contact, stop_when=observe, retain_snapshots=False
    )
    final = result.final_state
    observe(final)
    ignition = final.ignition_time_s.ravel()
    ignited = np.isfinite(ignition)
    boundary = domain.boundary_audit(cells[ignited], radius_m, max_dt_s=request.scenario.max_dt_s)
    if not boundary["closed"]:
        if not boundary["exterior_cfl_allows_max_dt"] or boundary["exterior_can_ignite_at_ambient"]:
            raise ValueError("the cold exterior can affect the simulation clock or ignite")
        return None
    if final.time_s < horizon_s - 1e-8 and certificate is None:
        raise ValueError("incomplete case without extinction closure")
    qa = check_final(request, result)
    area = request.parameters.covered_area_m2.ravel()
    burned = final.state.ravel() == 4
    active = final.state.ravel() == 3
    if not np.isclose(area[initial].sum(), 25, atol=1e-6, rtol=0):
        raise ValueError("prescribed ignition area is inconsistent with physical fuel coverage")

    def count_ordinary(mask):
        return len({o for i in np.flatnonzero(mask & roof) for o in owners[i]} - core_set)

    outcomes = {}
    energy = final.absorbed_radiation_J.ravel()[core]
    core_owners = [owners[i] for i in np.flatnonzero(core)]
    for key in core_ids:
        selected = np.array([key in o for o in core_owners], dtype=bool)
        times = ignition[core][selected]
        finite = times[np.isfinite(times)]
        outcomes[key] = {
            "first_ignition_s": float(finite.min()) if finite.size else None,
            "peak_rise_K": float(peak[selected].max(initial=ambient) - ambient),
            "absorbed_radiation_J": float(energy[selected].sum()),
        }
    return dict(
        elapsed_simulated_s=float(final.time_s),
        extinction_closed=certificate is not None,
        extinction_certificate_sha256=certificate["certificate_sha256"] if certificate else None,
        core_outcomes=outcomes,
        core_ignited_count=sum(v["first_ignition_s"] is not None for v in outcomes.values()),
        core_peak_rise_K=float(peak.max(initial=ambient) - ambient),
        core_absorbed_radiation_J=float(energy.sum()),
        ordinary_roof_ignited_count=count_ordinary(ignited),
        ordinary_roof_burned_out_count=count_ordinary(burned),
        roof_ignited_m2=float(area[roof & ignited].sum()),
        ground_ignited_m2=float(area[~roof & ignited].sum()),
        roof_burned_out_m2=float(area[roof & burned].sum()),
        ground_burned_out_m2=float(area[~roof & burned].sum()),
        active_m2=float(area[active].sum()),
        source_area_m2=float(area[initial].sum()),
        source_energy_J=float(request.parameters.available_energy_J.ravel()[initial].sum()),
        energy_closure_error_J=max(qa["energy_closure_errors_J"].values()),
        energy_closure_tolerance_J=qa["energy_tolerance_J"],
        boundary=boundary,
    )


def group_job(args):
    output, village, fire_id, wind_ids, registration_hash, code_hash = args
    registration = json.loads((output / "registration.json").read_text())
    village_data = registration["villages"][village]
    fire = next(f for f in village_data["fires"] if f["fire_id"] == fire_id)
    winds = [w for w in registration["winds"] if w["wind_id"] in wind_ids]
    bundle, _, inventory = study(village_data["study_path"])
    primary, sensitivity = registration["primary"], registration["sensitivity"]
    if primary["horizon_s"] != sensitivity["horizon_s"]:
        raise ValueError("the sensitivity study requires matched horizons")
    horizon = primary["horizon_s"]
    pending = []
    for wind in winds:
        radii = [primary["radius_m"]]
        if fire_id in sensitivity["fire_ids"] and wind["wind_id"] in sensitivity["wind_ids"]:
            radii.append(sensitivity["radius_m"])
        for radius in radii:
            for plan in village_data["plans"]:
                if radius != primary["radius_m"] and plan["plan_id"] not in sensitivity["plan_ids"]:
                    continue
                key = f"{village}_{fire_id}_{wind['wind_id']}_r{radius}_{plan['plan_id']}"
                path = output / "cases" / (key + ".json")
                if path.exists():
                    old = json.loads(path.read_text())
                    check = old.pop("result_sha256")
                    if (
                        record_hash(old) != check
                        or old["registration_sha256"] != registration_hash
                        or old["solver_sha256"] != code_hash
                        or old["case_id"] != key
                    ):
                        raise ValueError(f"stale or corrupted case {path}")
                    continue
                pending.append((wind, radius, plan, key, path))
    window_m = 55.0
    while pending:
        domain, layout, integrated = prepare_domain(
            bundle,
            inventory,
            fire,
            village_data["core_ids"],
            window_m,
            scale=primary["roof_scale_m"],
            overlap=village_data["core_overlap_policy"],
        )
        contact = refined_contact_operator(layout)
        geometry = WindRadiationGeometry(layout, integrated, primary["quadrature_spacing_m"])
        previous = None
        retry = []
        for wind, radius, plan, key, path in pending:
            if previous != (wind["wind_id"], radius):
                _, transport = apply_static_wind(
                    domain.bundle,
                    **{
                        name: wind[name]
                        for name in (
                            "wind_speed_m_s",
                            "wind_from_deg",
                            "flame_tilt_coefficient_s_m",
                        )
                    },
                    max_edge_count=30_000_000,
                )
                # The native thermal clock consumes the resolved polygon graph directly.
                # The same terrain, material and parent grid serve both support-radius arms.
                transport = replace(transport, exposure_radius_m=radius)
                graph = geometry.graph(transport, max_edges=30_000_000)
                previous = (wind["wind_id"], radius)
            metrics = run_case(
                domain, layout, plan, graph, contact, village_data["core_ids"], radius, horizon
            )
            if metrics is None:
                retry.append((wind, radius, plan, key, path))
                continue
            row = dict(
                case_id=key,
                village=village,
                fire_id=fire_id,
                kind=fire["kind"],
                wind_id=wind["wind_id"],
                plan_id=plan["plan_id"],
                radius_m=radius,
                horizon_s=horizon,
                registration_sha256=registration_hash,
                solver_sha256=code_hash,
                footprint_sha256=fire["footprint_sha256"],
                **metrics,
            )
            row["result_sha256"] = record_hash(row)
            atomic_json(path, row)
        pending = retry
        if pending and domain.window == (
            0,
            bundle.request.grid.height,
            0,
            bundle.request.grid.width,
        ):
            raise RuntimeError("full parent domain did not pass the window audit")
        window_m *= 2
    return (village, fire_id, wind_ids)


def record_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output = args.output.resolve()
    registration_path = output / "registration.json"
    reg = json.loads(registration_path.read_text())
    registration_hash, code_hash = digest(registration_path), solver_signature()
    for data in reg["villages"].values():
        for name, expected in data["input_files_sha256"].items():
            if digest(ROOT / name) != expected:
                raise ValueError(f"study input has changed: {name}")
    (output / "cases").mkdir(exist_ok=True)
    jobs = []
    wind_ids = [w["wind_id"] for w in reg["winds"]]
    for village, data in reg["villages"].items():
        for fire in data["fires"]:
            for start in range(0, len(wind_ids), 3):
                jobs.append(
                    (
                        output,
                        village,
                        fire["fire_id"],
                        wind_ids[start : start + 3],
                        registration_hash,
                        code_hash,
                    )
                )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for done in executor.map(group_job, jobs):
            print("finished", *done, flush=True)


if __name__ == "__main__":
    main()
