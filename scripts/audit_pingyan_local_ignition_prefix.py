"""Check a deterministic shared-prefix counterexample against both frozen readers."""

import json
from itertools import pairwise
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.village.extinction_v3 import extinction_observation_fingerprints
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence():
    base = ROOT / "runs/research"
    old = base / "heritage-joint-action-pingyan-20261111"
    local = base / "heritage-protected-union-pingyan-20261111"
    a, b = (read_json(p / "experiment.json") for p in (old, local))
    old_physics = a["native_problem"]["roof_preparation"]
    new_physics = b["problem"]
    shared = old_physics.keys() & new_physics.keys()
    differences = {k for k in shared if old_physics[k] != new_physics[k]}
    require(differences == {"horizon_s", "early_stop", "code_version"}, "different frozen physics")
    require(
        all(
            list(fire["footprints"]) == list(new_physics["fires"][k]["footprints"])
            for k, fire in old_physics["fires"].items()
        ),
        "footprint iteration differs",
    )
    same = [
        "max_dt_s",
        "max_nodes",
        "max_radiation_edges",
        "quadrature_spacing_m",
        "core_overlap_policy",
        "study_path",
    ]
    require(all(a["config"][k] == b["config"][k] for k in same), "different integration settings")
    require(
        b["config"]["terminal_fire_verification"]
        and a["config"]["horizon_s"] == b["config"]["terminal_horizon_cap_s"] == 172800.0,
        "unequal effective end times",
    )
    modules = sorted(
        k for k in a["source_sha256"].keys() & b["source_sha256"].keys() if k.startswith("src/")
    )
    for k in modules:
        require(
            a["source_sha256"][k]
            == b["source_sha256"][k]
            == file_sha256(old / "source-snapshot" / k)
            == file_sha256(local / "source-snapshot" / k),
            "kernel hash mismatch",
        )
    plan = next(x for x in b["config"]["plans"] if x["plan_id"].endswith("-w0.01"))
    require(
        len(plan["demolished_ids"]) == 6 and not plan["cleared_physical_cell_ids"],
        "action mismatch",
    )
    (manifest,) = (
        p
        for p in old.glob("evaluations/*/complete.json")
        if set(read_json(p)["demolished_ids"]) == set(plan["demolished_ids"])
        and not read_json(p)["cleared_physical_cell_ids"]
        and read_json(p.with_name("evaluation.json"))["metrics"]["ignited_core_ids"]
    )
    m = read_json(manifest)
    files = m["files_sha256"]
    require(
        len(files) == 7 and all(file_sha256(manifest.with_name(k)) == v for k, v in files.items()),
        "saved artifacts changed",
    )
    check = read_json(manifest.with_name("check.json"))["evidence"]
    record = read_json(manifest.with_name("evaluation.json"))
    diag = record["diagnostics"]
    require(
        old_physics["case_sha256"] == new_physics["case_sha256"] == record["case_sha256"],
        "different parent request",
    )
    scenario = m["scenario_id"]
    family = scenario.rsplit("-h", 1)[0]
    src = next(
        x["actual_source_id"]
        for x in b["source_realizations"]
        if x["plan_id"] == plan["plan_id"] and x["scenario_id"] == scenario
    )
    require(
        src == check["source_id"]
        and check["source_footprint_sha256"]
        == content_hash(new_physics["fires"][family]["footprints"][src]),
        "different frozen source footprint",
    )
    require(
        diag["stop_at_first_core_ignition"]
        and diag["stop_at_extinction"]
        and diag["source_verified"]
        and diag["parent_case_unchanged"]
        and not diag["invalid_reasons"],
        "invalid process or stop flags",
    )
    require(
        check["native_strict_status"] == "unknown"
        and check["terminal_verification"]["core_ignition_observed"],
        "changed native status",
    )
    hits = {}
    for p in local.glob("evaluations/*/complete.json"):
        j = read_json(p)
        if j["scenario_id"] != scenario:
            continue
        for fn in ("check.json", "evaluation.json"):
            require(
                file_sha256(p.with_name(fn)) == j["files_sha256"][fn],
                "local comparator artifact changed",
            )
        e = read_json(p.with_name("check.json"))["evidence"]
        other = read_json(p.with_name("evaluation.json"))["diagnostics"]
        keys = (
            "source_id",
            "source_footprint_sha256",
            "source_budget",
            "frozen_integrated_node_mask_sha256",
        )
        require(
            all(e[k] == check[k] for k in keys) and other["layout_sha256"] == diag["layout_sha256"],
            "different frozen budget, layout or integration membership",
        )
        hits[str(p.relative_to(ROOT))] = file_sha256(p)
    require(hits, "no independent local mesh/budget witness")
    with np.load(manifest.with_name("final-state.npz")) as d:
        vals = {k: d[k].copy() for k in V3State.__dataclass_fields__}
    vals["time_s"], vals["step_index"] = float(vals["time_s"]), int(vals["step_index"])
    state = V3State(**vals)
    require(
        extinction_observation_fingerprints(
            state, diag["maximum_valid_surface_temperature_by_node_K"]
        )
        == check["observation_fingerprints"],
        "state/peak fingerprint mismatch",
    )
    core = {x["building_id"] for x in record["metrics"]["core_outcomes"]}
    with np.load(manifest.with_name("layout.npz")) as d:
        ids, offsets = d["owner_ids"], d["owner_offsets"]
        mask = np.array([bool(core & set(ids[i:j])) for i, j in pairwise(offsets)])
    clocks = state.ignition_time_s.ravel()[mask]
    finite = clocks[np.isfinite(clocks)]
    require(
        finite.size
        and abs(finite.min() - state.time_s) < 1.0e-8
        and 0 < state.time_s < record["horizon_s"],
        "no physical first ignition at stop",
    )
    drivers = {
        str((run / "source-snapshot" / key).relative_to(ROOT)): sha
        for run, exp in [(old, a), (local, b)]
        for key, sha in exp["source_sha256"].items()
        if key.startswith("scripts/") and ("joint_comparison" in key or "joint_master" in key)
    }
    require(all(file_sha256(ROOT / p) == v for p, v in drivers.items()), "driver hash mismatch")
    return dict(
        schema_version="protected-roof-deterministic-prefix-1",
        village="pingyan",
        plan=plan["plan_id"],
        roof_actions=6,
        ground_actions=0,
        native_master_manifest=str(manifest.relative_to(ROOT)),
        native_master_manifest_sha256=file_sha256(manifest),
        seven_master_artifact_hashes=files,
        physical_module_count=len(modules),
        shared_frozen_physical_module_digest=content_hash(
            {k: a["source_sha256"][k] for k in modules}
        ),
        frozen_drivers_sha256=drivers,
        experiments_sha256={
            str((p / "experiment.json").relative_to(ROOT)): file_sha256(p / "experiment.json")
            for p in (old, local)
        },
        equivalent_mesh_source_and_budget_native_comparators=hits,
        actual_end_cap_s=172800.0,
        same_action_source_mesh_integrated_membership_transport_and_time_step=True,
        state_fingerprints=check["observation_fingerprints"],
        first_protected_ignition_at_saved_stop=True,
        observed_time_s=state.time_s,
        inference=(
            "Same deterministic state updates and cap. Both observe extinction after events. "
            "Until the master detects the first finite core ignition, callbacks agree. "
            "At that state the local full trajectory already contains the ignition."
        ),
        scope=(
            "A failed protection condition on the four-design-family mesh. "
            "The native master status is preserved. Full fire losses remain unmeasured."
        ),
        original_native_strict_status=check["native_strict_status"],
        new_CA_checks=0,
        local_native_rows_added=0,
    )


if __name__ == "__main__":
    result = evidence()
    result["method_sha256"] = file_sha256(__file__)
    result["receipt_sha256"] = content_hash(result)
    out = ROOT / "docs/results/heritage-pingyan-w0.01-first-ignition-prefix-20261111.json"
    with out.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
