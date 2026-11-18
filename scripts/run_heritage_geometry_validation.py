"""Replay the held-out wind panel with the two fixed, lower-clearance geometries."""

import argparse
import json
from hashlib import sha256
from math import isclose
from pathlib import Path

import finish_heritage_wind_validation as full_domain
import numpy as np
import run_heritage_wind_validation as validation
from measure_heritage_roof_clearances import roof_action
from shapely import union_all

from pyfireca.planning.heritage import content_hash

ROOT = validation.ROOT
PARENT = validation.DEST
DEST = ROOT / "output/heritage-geometry-validation-20261118"
MAPS = ROOT / "output/heritage-firebreak-actions-20261111"
RESULTS = ROOT / "docs/results"
WIDE = "multipoint-wide"
COMPACT = {
    "batuan": "direct-thin-D9-with-no-grass",
    "pingyan": "local-band-with-observed-clearance-subset",
}
ORIGINAL_OBSERVATIONS = validation.observations


def read_json(path):
    return json.loads(path.read_bytes())


def file_binding(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": validation.digest(path)}


def verify_binding(binding):
    path = ROOT / binding["path"]
    if validation.digest(path) != binding["sha256"]:
        raise ValueError(f"Frozen input changed: {path}")
    return path


def design_inputs(village):
    if village == "batuan":
        path = RESULTS / "heritage-batuan-direct-D9-G0-complete8-audit-20261111.json"
        evidence = read_json(path)
        sources = {r["actual_source_id"] for r in evidence["scenario_rows"]}
        return evidence["plan"], sources, [file_binding(path)]
    path = RESULTS / "heritage-pingyan-observed-clearance-subset-registration-20261111.json"
    experiment = (
        ROOT / "runs/research/heritage-pingyan-observed-clearance-subset-20261111/experiment.json"
    )
    sources = {
        r["actual_source_id"]
        for r in read_json(experiment)["source_realizations"]
        if r["plan_id"] == COMPACT[village]
    }
    return read_json(path)["action"], sources, [file_binding(path), file_binding(experiment)]


def verify_actions(village, setup, plan, mapped_roof):
    bundle, _, inventory = validation.study(setup["study_path"])
    req = bundle.request
    grid = req.grid
    if inventory.sha256 != setup["inventory_sha256"] or grid.cell_size_m != 5:
        raise ValueError(f"{village}: original inventory or grid differs")
    removed = set(plan["demolished_ids"])
    protected = set(setup["core_ids"]) | set(setup["fixed_source_ids"])
    if not removed <= inventory.by_id.keys() - protected:
        raise ValueError(f"{village}: compact clears a protected or design source roof")
    retained_roof = union_all([b.geometry for i, b in inventory.by_id.items() if i not in removed])
    cleared_roof = union_all([inventory.by_id[i].geometry for i in removed]).difference(
        retained_roof
    )
    if cleared_roof.symmetric_difference(mapped_roof).area > 1e-4:
        raise ValueError(f"{village}: exported roof map and physical roof clearance differ")
    ground = plan["cleared_physical_cell_ids"]
    if len(ground) != len(set(ground)):
        raise ValueError(f"{village}: duplicate ground actions")
    for cell in ground:
        row, col = grid.row_col(cell)
        if not (
            req.domain_mask[row, col]
            and req.burnable_mask[row, col]
            and req.building_index[row, col] < 0
        ):
            raise ValueError(f"{village}: ground action {cell} does not clear burnable ground")
    if not isclose(cleared_roof.area, plan["roof_cleared_m2"], abs_tol=1e-4) or not isclose(
        len(ground) * grid.cell_area_m2, plan["ground_cleared_m2"], abs_tol=1e-7
    ):
        raise ValueError(f"{village}: actual construction quantities differ from the action map")


def freeze_village(village, setup, index):
    option_id = COMPACT[village]
    option = index["options"][option_id]
    binding = option["maps"][0]
    path = MAPS / binding["path"]
    geojson = read_json(path)
    roof_ids, cleared_roof = roof_action(option)
    ground = [
        i
        for f in geojson["features"]
        if f["properties"]["material"] == "whole_ground_cells"
        for i in f["properties"]["action_ids"]
    ]
    planned, design_roofs, evidence = design_inputs(village)
    if (
        len(design_roofs) != 4
        or design_roofs != set(setup["fixed_source_ids"])
        or roof_ids != set(planned["demolished_ids"])
        or sorted(ground) != sorted(planned["cleared_physical_cell_ids"])
    ):
        raise ValueError(f"{village}: geometry and prior registered actions differ")
    areas = binding["verified_material_area_m2"]
    compact = {
        "plan_id": option_id,
        "width_m": None,
        "construction_m2": geojson["construction_m2"],
        "roof_cleared_m2": areas["roof_union"],
        "ground_cleared_m2": areas.get("whole_ground_cells", 0.0),
        "demolished_ids": sorted(roof_ids),
        "cleared_physical_cell_ids": sorted(ground),
    }
    verify_actions(village, setup, compact, cleared_roof)
    wide = next(p for p in setup["plans"] if p["plan_id"] == WIDE)
    if (
        not isclose(
            compact["construction_m2"],
            compact["roof_cleared_m2"] + compact["ground_cleared_m2"],
            abs_tol=1e-6,
        )
        or compact["construction_m2"] >= wide["construction_m2"]
    ):
        raise ValueError(f"{village}: incorrect compact construction budget")
    excluded = roof_ids | set(wide["demolished_ids"]) | design_roofs
    pool = setup["random_eligible_ids_by_stratum"]
    source_ids = {f["source_id"] for f in setup["fires"]}
    if len(source_ids) != len(setup["fires"]) or excluded.intersection(
        set(pool["near"]) | set(pool["far"]) | source_ids
    ):
        raise ValueError(f"{village}: the archive does not cover the common held-out source frame")
    return {
        **setup,
        "plans": [compact],
        "wide_reference": wide,
        "candidate_map": file_binding(path),
        "candidate_design_receipts": evidence,
        "candidate_design_roofs": sorted(design_roofs),
        "reference_map": file_binding((PARENT / setup["geojson_path"]).resolve()),
    }


def solver_signatures(parent):
    original = validation.solver_signature()
    if original != parent["original_solver_sha256"]:
        raise ValueError("The frozen heat model differs from the archived wide runs")
    terminal = validation.terminal_signature(original)
    reference = sha256(
        (terminal + validation.digest(Path(validation.__file__))).encode()
    ).hexdigest()
    # The geometry extension changes only the readout and the registration.
    compact = content_hash({"reference": reference, "readout": validation.digest(Path(__file__))})
    return reference, compact


def registration():
    parent_path = PARENT / "registration.json"
    parent = read_json(parent_path)
    if (
        content_hash({k: v for k, v in parent.items() if k != "registration_sha256"})
        != parent["registration_sha256"]
    ):
        raise ValueError("The original held-out registration is altered")
    reference_model, compact_model = solver_signatures(parent)
    options = MAPS / "verified-options.json"
    index = read_json(options)
    if (
        content_hash({k: v for k, v in index.items() if k != "index_content_sha256"})
        != index["index_content_sha256"]
    ):
        raise ValueError("Verified action index is altered")
    villages = {v: freeze_village(v, setup, index) for v, setup in parent["villages"].items()}
    pair_count = len(parent["winds"]) * sum(len(setup["fires"]) for setup in villages.values())
    data = {
        **parent,
        "schema_version": "heritage-fixed-geometry-heldout-terminal-1",
        "scope": "Two fixed compact geometries versus archived multipoint-wide runs on the same "
        "103 held-out roofs and nine wind states. Continue each case to certified extinction.",
        "primary": {**parent["primary"], "planned_pairs": pair_count},
        "villages": villages,
        "wide_registration": file_binding(parent_path),
        "action_index": file_binding(options),
        "backend_verification": file_binding(PARENT / "backend-verification.json"),
        "wide_reference_solver_sha256": reference_model,
        "compact_reference_solver_sha256": compact_model,
        "geometry_runner": file_binding(Path(__file__).resolve()),
        "main_endpoints": [
            "any heritage ignition to extinction and per-core peak rise and absorbed radiation",
            "non-heritage roof burned-out area and original roof tags with any burned-out patch",
            "union of cleared and burned-out non-heritage physical roof area",
            "ground cleared and burned-out areas, reported separately",
        ],
    }
    data.pop("registration_sha256")
    data["registration_sha256"] = content_hash(data)
    return data


def verify_registration(root):
    path = root / "registration.json"
    reg = read_json(path)
    if (
        content_hash({k: v for k, v in reg.items() if k != "registration_sha256"})
        != reg["registration_sha256"]
    ):
        raise ValueError("Geometry validation registration changed")
    for field in ("wide_registration", "action_index", "backend_verification", "geometry_runner"):
        verify_binding(reg[field])
    for setup in reg["villages"].values():
        for binding in (
            setup["candidate_map"],
            setup["reference_map"],
            *setup["candidate_design_receipts"],
        ):
            verify_binding(binding)
        for name, checksum in setup["input_files_sha256"].items():
            if validation.digest(ROOT / name) != checksum:
                raise ValueError(f"Changed physical input: {name}")
    if solver_signatures(reg) != (
        reg["wide_reference_solver_sha256"],
        reg["compact_reference_solver_sha256"],
    ):
        raise ValueError("Geometry readout or reference solver changed since registration")
    return reg


def observe_ordinary_roofs(request, result, peak, owners, core_ids, initial, boundary):
    metrics = ORIGINAL_OBSERVATIONS(request, result, peak, owners, core_ids, initial, boundary)
    cores = set(core_ids)
    ordinary = np.fromiter((bool(o) and cores.isdisjoint(o) for o in owners), dtype=bool)
    burned = result.final_state.state.ravel() == 4
    metrics["ordinary_roof_burned_out_m2"] = float(
        request.parameters.covered_area_m2.ravel()[ordinary & burned].sum()
    )
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEST)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    if min(args.workers, args.batch_size) < 1:
        parser.error("positive worker count and batch size required")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    reg_file = root / "registration.json"
    if not reg_file.exists():
        data = registration()
        with reg_file.open("x") as output:
            json.dump(data, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
    reg = verify_registration(root)
    reg_hash = validation.digest(reg_file)
    print(f"Frozen {reg['primary']['planned_pairs']} pairs: {reg_hash}", flush=True)
    if args.register_only:
        return

    receipt = root / "backend-verification.json"
    original_receipt = verify_binding(reg["backend_verification"])
    if not receipt.exists():
        with receipt.open("xb") as output:
            output.write(original_receipt.read_bytes())
    if validation.digest(receipt) != reg["backend_verification"]["sha256"]:
        raise ValueError("Copied backend verification does not match the original")
    for subdir in ("cases", "certificates", "expansions"):
        (root / subdir).mkdir(exist_ok=True)
    validation.observations = observe_ordinary_roofs
    for village in reg["villages"]:
        remaining = None
        for workers in (args.workers, max(1, args.workers // 2)):
            remaining = full_domain.run_pass(
                root,
                reg,
                reg_hash,
                reg["compact_reference_solver_sha256"],
                village,
                workers,
                args.batch_size,
                remaining,
            )
            if not remaining:
                break
        if remaining:
            raise SystemExit(f"Unresolved geometry cases in {village}: {sorted(remaining)}")
    print("All compact geometries have certified terminal results.", flush=True)


if __name__ == "__main__":
    main()
