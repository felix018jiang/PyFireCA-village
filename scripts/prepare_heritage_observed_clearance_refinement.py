"""Retain only additional wide-band actions required to cover observed narrow-band burnout."""

import json
from pathlib import Path

import numpy as np
from prepare_heritage_joint_longcap import encoded, immutable
from run_heritage_joint_comparison import (
    finite_source_catalog_certificate,
    plan_from_mapping,
    read_master_reference,
    validate_config,
)

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import demolition_geometry, file_sha256, read_json
from pyfireca.planning.study_io import load_study


def prepare(root):
    run = root / "runs/research/heritage-protected-union-pingyan-20261111"
    parent = run / "experiment.json"
    exp = read_json(parent)
    conf = exp["config"]
    narrow, wide = (
        next(p for p in conf["plans"] if p["plan_id"].endswith(s)) for s in ("w2.5", "w15.01")
    )
    cases = {}
    needed = {"check.json", "layout.npz", "final-state.npz", "loss-basis.npz"}
    for path in run.glob("evaluations/*/complete.json"):
        manifest = read_json(path)
        trace = path.parent / "check.json"
        if file_sha256(trace) != manifest["files_sha256"][trace.name]:
            raise ValueError("invalid design case")
        c = read_json(trace)
        for p in (narrow, wide):
            if c["evidence"]["scale_m"] != 1.0 or any(
                c[k] != p[k] for k in ("demolished_ids", "cleared_physical_cell_ids")
            ):
                continue
            for fname in needed:
                if file_sha256(path.parent / fname) != manifest["files_sha256"][fname]:
                    raise ValueError("native design evidence checksum mismatch")
            if (
                c["status"] != "passed"
                or c["evidence"]["invalid_reasons"]
                or any(item["ignited"] for item in c["evidence"]["core_outcomes"])
            ):
                raise ValueError("design training cases must have valid natural extinction")
            cases[p["plan_id"], c["evidence"]["fire_id"]] = (path, c["evidence"])
    added_roofs, added_ground, refs = set(), set(), []
    for fire in conf["source_fires"]:
        paths, observations = zip(
            *(cases[p["plan_id"], fire["fire_id"]] for p in (narrow, wide)), strict=True
        )
        a, b = observations
        if any(
            a[k] != b[k]
            for k in (
                "source_id",
                "source_budget",
                "source_footprint_sha256",
                "frozen_integrated_node_mask_sha256",
                "common_layout_sha256",
            )
        ):
            raise ValueError("the local bands do not share an actual source and mesh")
        x, y = (p.parent for p in paths)
        family_roofs, family_grass = set(), set()
        with (
            np.load(x / "layout.npz") as geom,
            np.load(x / "final-state.npz") as state,
            np.load(x / "loss-basis.npz") as small,
            np.load(y / "loss-basis.npz") as large,
        ):
            direct = (
                np.isfinite(state["burnout_time_s"].ravel())
                & (small["post_intervention_covered_area_m2"].ravel() > 0)
                & (large["post_intervention_covered_area_m2"].ravel() == 0)
            )
            offsets, owners, ids = (
                geom["owner_offsets"],
                geom["owner_ids"],
                geom["physical_cell_id"].ravel(),
            )
            for i in np.flatnonzero(direct):
                if offsets[i] == offsets[i + 1]:
                    family_grass.add(int(ids[i]))
                else:
                    family_roofs.update(owners[offsets[i] : offsets[i + 1]].tolist())
        added_roofs.update(family_roofs)
        added_ground.update(family_grass)
        refs.append([file_sha256(p) for p in paths])
    roofs = set(narrow["demolished_ids"]) | added_roofs
    grass = set(narrow["cleared_physical_cell_ids"]) | added_ground
    if not roofs <= set(wide["demolished_ids"]) or not grass <= set(
        wide["cleared_physical_cell_ids"]
    ):
        raise ValueError("the selected intervention is not a subset of the wide plan")
    candidate = dict(
        plan_id="local-band-with-observed-clearance-subset",
        demolished_ids=sorted(roofs),
        cleared_physical_cell_ids=sorted(grass),
        ground_evidence="registered wide-band actions covering observed narrow-band burnout",
    )
    _, _, inventory = load_study(Path(conf["study_path"]))
    _, _, roof_union, _ = demolition_geometry(inventory, sorted(roofs))
    config_dir = root / "examples/research/heritage/area-20261110"
    reference = read_json(conf["reference_master_experiment_path"])
    proof = finite_source_catalog_certificate(
        [plan_from_mapping(candidate)],
        reference["problem"]["fires"],
        inventory_sha256=reference["problem"]["inventory_sha256"],
        reference_problem_sha256=reference["problem_sha256"],
    )
    for r in proof["rows"]:
        original = cases[narrow["plan_id"], r["fire_id"]][1]
        if (
            original["source_id"] != r["actual_source_id"]
            or original["source_footprint_sha256"] != r["source_footprint_sha256"]
        ):
            raise ValueError("candidate changes the actual source")
    certificate = (
        config_dir / "pingyan-observed-clearance-subset-20261111.finite-source-certificate.json"
    )
    immutable(certificate, encoded(proof))
    receipt = (
        root / "docs/results/heritage-pingyan-observed-clearance-subset-registration-20261111.json"
    )
    item = dict(
        method_sha256=file_sha256(__file__),
        parent_experiment_sha256=file_sha256(parent),
        training_source_pairs=len(refs),
        training_scale_m=1.0,
        training_native_manifest_sha256=refs,
        distinct_added_original_roof_actions=len(roofs - set(narrow["demolished_ids"])),
        added_ground_cells=len(grass - set(narrow["cleared_physical_cell_ids"])),
        C_m2=roof_union.area + 25 * len(grass),
        roof_C_m2=roof_union.area,
        ground_C_m2=25 * len(grass),
        action=candidate,
        protected_heritage_outcome_unknown_before_replay=True,
        design_scope="least added actions covering the specified observed material",
        no_holdout_fire_outcomes_used=True,
    )
    item["receipt_sha256"] = content_hash(item)
    immutable(receipt, encoded(item))
    conf.update(
        plans=[candidate],
        execution_priority_plan_ids=[candidate["plan_id"]],
        stop_plan_after_protection_failure=False,
        max_total_checks=8,
        max_new_checks=8,
        new_independent_fire_draw_count=0,
        scope="eight-case verification on the same four physical sources and common mesh",
    )
    conf["additional_frozen_inputs_sha256"].update(
        {str(p): file_sha256(p) for p in (parent, certificate, receipt, Path(__file__).resolve())}
    )
    out = config_dir / "pingyan-observed-clearance-subset-20261111.json"
    validate_config(conf)
    read_master_reference(out, conf)
    immutable(out, encoded(conf))
    return {
        k: item[k]
        for k in (
            "training_source_pairs",
            "distinct_added_original_roof_actions",
            "added_ground_cells",
            "C_m2",
        )
    }


if __name__ == "__main__":
    print(json.dumps(prepare(Path(__file__).resolve().parents[1])))
