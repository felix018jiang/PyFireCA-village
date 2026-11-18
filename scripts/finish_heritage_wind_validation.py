"""Share read-only full-domain geometry across workers while advancing every validation case."""

import argparse
import gc
import hashlib
import json
import multiprocessing as mp
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import run_heritage_wind_validation as validation
from heritage_wind_radiation import WindRadiationGeometry

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_ignition import refined_contact_operator

# The parent initializes these arrays once, before forking a fresh worker pool.
# Each worker still constructs its own wind graph, intervention, state and certificate.
GEOMETRY = {}
LAYOUTS = {}
GRAPH_GATE = None
BASE_CHECK = validation.checked_row
BASE_WRITE = validation.atomic_json
FAST_CLOCK = None
BACKEND_SHA = None


def optimized_model_signature(reference):
    return hashlib.sha256(f"{reference}/{BACKEND_SHA}".encode()).hexdigest()


def discover_verified_backend(root):
    global FAST_CLOCK, BACKEND_SHA
    receipt = root / "backend-verification.json"
    if not receipt.exists():
        return
    from heritage_wind_validation_fast import FastTerminalWindClock, backend_signature

    evidence = json.loads(receipt.read_bytes())
    checksum = backend_signature()
    if evidence["passed"] is not True or evidence["backend_sha256"] != checksum:
        raise ValueError("Accelerated backend differs from its numeric verification receipt")
    FAST_CLOCK = FastTerminalWindClock
    BACKEND_SHA = checksum


def compatible_read(path, registration, reference):
    record = json.loads(path.read_bytes())
    if record["solver_sha256"] == reference:
        return BASE_CHECK(path, registration, reference)
    if (
        BACKEND_SHA is None
        or record.get("execution_backend_sha256") != BACKEND_SHA
        or record.get("reference_solver_sha256") != reference
    ):
        raise ValueError(f"Unverified solver implementation in {path}")
    return BASE_CHECK(path, registration, optimized_model_signature(reference))


def record_execution(path, payload):
    if path.parent.name == "cases":
        payload = dict(payload)
        payload.pop("result_sha256")
        reference = payload["solver_sha256"]
        payload["reference_solver_sha256"] = reference
        payload["execution_backend_sha256"] = BACKEND_SHA
        payload["solver_sha256"] = optimized_model_signature(reference)
        payload["result_sha256"] = validation.record_hash(payload)
    BASE_WRITE(path, payload)


class GraphBuilder:
    def __init__(self, geometry):
        self.geometry = geometry

    def graph(self, *args, **kwargs):
        with GRAPH_GATE:
            return self.geometry.graph(*args, **kwargs)


def reuse_domain(bundle, inventory, fire, core_ids, window, *, scale, overlap):
    domain, layout, ids, expected = LAYOUTS[fire["footprint_sha256"]]
    if expected != (id(bundle), id(inventory), tuple(core_ids), window, scale, overlap):
        raise ValueError("Shared domain does not match the requested physical inputs")
    return domain, layout, ids


def reuse_radiation(layout, integrated, spacing):
    geometry, _, expected_ids = GEOMETRY[id(layout)]
    if geometry.spacing_m != spacing or tuple(integrated) != expected_ids:
        raise ValueError("Shared quadrature does not match the registered source and targets")
    return GraphBuilder(geometry)


def reuse_contacts(layout):
    return GEOMETRY[id(layout)][1]


def configure_worker():
    validation.prepare_domain = reuse_domain
    validation.WindRadiationGeometry = reuse_radiation
    validation.refined_contact_operator = reuse_contacts
    if FAST_CLOCK is not None:
        validation.TerminalWindClock = FAST_CLOCK
        validation.atomic_json = record_execution


def prepare_batch(reg, village, fire_ids):
    config = reg["villages"][village]
    bundle, _, inventory = validation.study(config["study_path"])
    window = (
        2
        * max(bundle.request.grid.height, bundle.request.grid.width)
        * bundle.request.grid.cell_size_m
    )
    scale = reg["primary"]["roof_scale_m"]
    spacing = reg["primary"]["quadrature_spacing_m"]
    overlap = config["core_overlap_policy"]
    core_ids = config["core_ids"]
    for fire in config["fires"]:
        if fire["fire_id"] not in fire_ids:
            continue
        domain, layout, ids = validation.prepare_domain(
            bundle, inventory, fire, core_ids, window, scale=scale, overlap=overlap
        )
        if domain.window != (0, bundle.request.grid.height, 0, bundle.request.grid.width):
            raise ValueError("Full-domain job has an artificial boundary")
        geometry = WindRadiationGeometry(layout, ids, spacing)
        GEOMETRY[id(layout)] = (geometry, refined_contact_operator(layout), tuple(ids))
        LAYOUTS[fire["footprint_sha256"]] = (
            domain,
            layout,
            ids,
            (id(bundle), id(inventory), tuple(core_ids), window, scale, overlap),
        )


def run_pass(
    root,
    reg,
    reg_hash,
    model_hash,
    village,
    worker_count,
    sources_per_batch,
    remaining_fires=None,
    selected_winds=None,
):
    global GRAPH_GATE
    discover_verified_backend(root)
    validation.checked_row = compatible_read
    jobs, completed, _ = validation.schedule(
        root, reg, reg_hash, model_hash, [village], "full", 1, None
    )
    per_fire = defaultdict(list)
    for job in jobs:
        if selected_winds and not selected_winds.intersection(job[-1]):
            continue
        if remaining_fires is None or job[4] in remaining_fires:
            per_fire[job[4]].append(job)
    print(
        time.strftime("%H:%M:%S"),
        village,
        completed,
        "already certified;",
        len(per_fire),
        "source panels pending",
        flush=True,
    )
    ctx = mp.get_context("fork")
    keys = list(per_fire)
    errors = set()
    for start in range(0, len(keys), sources_per_batch):
        batch = keys[start : start + sources_per_batch]
        discover_verified_backend(root)
        GRAPH_GATE = ctx.Semaphore(2)
        prepare_batch(reg, village, set(batch))
        scheduled = [j for f in batch for j in per_fire[f]]
        with ProcessPoolExecutor(
            max_workers=min(worker_count, len(scheduled)),
            mp_context=ctx,
            initializer=configure_worker,
        ) as pool:
            futures = {pool.submit(validation.group, j): j for j in scheduled}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    count, unclosed = future.result()
                    if unclosed:
                        raise RuntimeError("A full domain requested expansion")
                    print(
                        time.strftime("%H:%M:%S"),
                        village,
                        job[4],
                        list(job[-1]),
                        "certified",
                        count,
                        flush=True,
                    )
                except Exception as exc:
                    errors.add(job[4])
                    print(
                        time.strftime("%H:%M:%S"),
                        "ERROR",
                        village,
                        job[4],
                        list(job[-1]),
                        repr(exc),
                        flush=True,
                    )
        GEOMETRY.clear()
        LAYOUTS.clear()
        GRAPH_GATE = None
        gc.collect()
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=validation.DEST)
    parser.add_argument("--village", action="append", choices=("batuan", "pingyan"))
    parser.add_argument("--fire", action="append", help="Limit to named fires within village")
    parser.add_argument("--wind", action="append", help="Limit to selected registered winds")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--passes", type=int, default=2)
    args = parser.parse_args()
    if min(args.workers, args.batch_size, args.passes) < 1:
        parser.error("positive scheduling limits required")
    root = args.output.resolve()
    reg_file = root / "registration.json"
    reg = json.loads(reg_file.read_bytes())
    registration_content = dict(reg)
    seal = registration_content.pop("registration_sha256")
    if content_hash(registration_content) != seal:
        raise ValueError("Validation registration no longer matches the frozen contents")
    if args.wind and not set(args.wind) <= set(reg["primary"]["wind_ids"]):
        raise ValueError("Unknown wind condition")
    previous = validation.ROOT / "output/heritage-wind-20261112/registration.json"
    if validation.digest(previous) != reg["original_registration_sha256"]:
        raise ValueError("The originally registered study has changed")
    for v in reg["villages"].values():
        for name, sha in v["input_files_sha256"].items():
            if validation.digest(validation.ROOT / name) != sha:
                raise ValueError(f"Changed study input: {name}")
    reg_hash = validation.digest(reg_file)
    original = validation.solver_signature()
    if original != reg["original_solver_sha256"]:
        raise ValueError("The registered native heat model has changed")
    model_hash = hashlib.sha256(
        (
            validation.terminal_signature(original) + validation.digest(Path(validation.__file__))
        ).encode()
    ).hexdigest()
    for village in args.village or ["batuan", "pingyan"]:
        fires = set(args.fire) if args.fire else None
        if fires is not None and not fires <= {
            f["fire_id"] for f in reg["villages"][village]["fires"]
        }:
            raise ValueError("Unknown source")
        for attempt in range(args.passes):
            fires = run_pass(
                root,
                reg,
                reg_hash,
                model_hash,
                village,
                max(1, args.workers // (1 + attempt)),
                args.batch_size,
                fires,
                set(args.wind or []),
            )
            if not fires:
                break
        if fires:
            raise SystemExit(f"Unresolved {village} source groups after retries: {len(fires)}")
    print("Completed all selected independent full-domain cases.", flush=True)


if __name__ == "__main__":
    main()
