"""Register the tied-minimum representative on exactly the existing zero-heat native meshes."""

import json
import sys
from pathlib import Path


def prepare(root):
    base = root / "runs/research"
    prod = base / "heritage-hrr-error-recovery-producer-20261111"
    original = base / "heritage-pingyan-zero-core-heat-and-design-loss-20261111"
    out = base / "heritage-pingyan-zero-heat-minimum-native-20261111"
    area = root / "examples/research/heritage/area-20261110"
    results = root / "docs/results"
    config_path = area / "pingyan-zero-heat-minimum-native-20261111.json"
    config0 = area / "pingyan-zero-core-heat-and-design-loss-native-20261111.json"
    cert = results / "heritage-pingyan-zero-heat-first-extensions-minimum-20261111.json"
    geometry = cert.with_suffix(".geojson")
    source_measure = (
        results / "heritage-pingyan-zero-heat-native-support-and-capacity-20261111.json"
    )
    sys.path[:0] = [str(prod / "scripts"), str(prod / "src")]
    import run_heritage_joint_comparison as n
    from pyfireca.planning.heritage import content_hash
    from pyfireca.planning.strategy_io import file_sha256, read_json

    def check(test, msg):
        if not test:
            raise ValueError(msg)

    old, e, pre = (
        read_json(config0),
        read_json(original / "experiment.json"),
        read_json(original / "preflight.json"),
    )
    v, w = read_json(cert), read_json(source_measure)
    check(
        all(
            obj["receipt_sha256"]
            == content_hash({k: d for k, d in obj.items() if k != "receipt_sha256"})
            for obj in (v, w)
        ),
        "sealed inputs changed",
    )
    check(
        e["config"] == old
        and e["config_sha256"] == file_sha256(config0)
        and e["problem_sha256"] == content_hash(e["problem"])
        and pre["passed"]
        and len(pre["rows"]) == 8,
        "old campaign geometry not complete",
    )
    layers = read_json(geometry)
    check(
        abs(layers["construction_m2"] - v["minimum_C_m2"]) < 1e-6
        and layers["certificate_file_sha256"] == file_sha256(cert),
        "physical GIS differs",
    )
    (plan0,) = old["plans"]
    plan = {
        k: v["model_action"][k] for k in ("plan_id", "demolished_ids", "cleared_physical_cell_ids")
    }
    plan["ground_evidence"] = (
        "legal whole-cell clearing from the frozen exhaustive one-layer containment enumeration"
    )
    check(
        plan["demolished_ids"] == plan0["demolished_ids"]
        and len(plan["cleared_physical_cell_ids"]) == 148
        and abs(v["saved_vs_original_m2"] - 100) < 1e-6,
        "representative differs from fixed common roof stage",
    )
    for k in [
        "src/pyfireca/village/solver_v3.py",
        "src/pyfireca/planning/roof_ignition.py",
        "src/pyfireca/planning/roof_thermal.py",
        "src/pyfireca/planning/engines/roof_joint.py",
        "src/pyfireca/planning/terminal_verification.py",
    ]:
        check(
            file_sha256(prod / k) == e["source_sha256"][k], "physical reader differs from baseline"
        )
    config = {k: v for k, v in old.items() if k != "supplemental_geometry_receipt"}
    config.update(
        plans=[plan],
        execution_priority_plan_ids=[plan["plan_id"]],
        max_new_checks=8,
        max_total_checks=8,
        scope="Full independent eight-slot native verification of the selected minimum among all simultaneous initially feasible compartment extensions. Same four fixed design sources and physical meshes as the zero-heat parent; no new or holdout draws.",
    )
    extras = [
        cert,
        geometry,
        config0,
        original / "experiment.json",
        original / "preflight.json",
        source_measure,
        Path(__file__).resolve(),
        prod / "producer-manifest.json",
    ]
    config["additional_frozen_inputs_sha256"] = {
        **old["additional_frozen_inputs_sha256"],
        **{str(p): file_sha256(p) for p in extras},
    }
    n.validate_config(config)
    if config_path.exists() or out.exists():
        raise FileExistsError("new native run or configuration already exists; duplicate denied")
    with config_path.open("x") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")

    def shared_roof_stage(oracle):
        check(
            content_hash(oracle.problem["fires"]) == content_hash(e["problem"]["fires"])
            and oracle.problem["case_sha256"] == e["problem"]["case_sha256"]
            and oracle.freeze_integration_membership,
            "changed sources, refinements or native case",
        )
        (p,) = oracle.plans.values()
        parent = oracle.base.physical_request
        legal = (
            parent.domain_mask
            & parent.known_mask
            & parent.burnable_mask
            & (parent.building_index < 0)
            & (parent.parameters.covered_area_m2 > 0)
        ).ravel()
        check(all(legal[g] for g in p.cleared_physical_cell_ids), "noneligible grass clearing")
        rows = []
        for ordinal, f in enumerate(config["source_fires"], 1):
            name = f["fire_id"]
            fire = oracle.fires[name]
            source = next(
                i for i in fire["protocol"]["draws"][0]["priority_ids"] if i not in p.demolished_ids
            )
            for scale in oracle.scales_m:
                measurement = next(
                    q for q in w["rows"] if (q["draw_ordinal"], q["scale_m"]) == (ordinal, scale)
                )
                oldrow = next(
                    q for q in pre["rows"] if (q["fire_id"], q["scale_m"]) == (name, scale)
                )
                counted = oldrow["actual_graph_stage_bound_by_plan"][plan0["plan_id"]][
                    "after_roofs_before_grass"
                ]
                physical = measurement["physical_support_by_plan"][plan0["plan_id"]]
                check(
                    source == oracle.actual_source_for(p, f"{name}-h{scale:g}")
                    and physical["footprint_sha256"] == fire["footprints"][source].sha256
                    and abs(physical["native_budget"]["covered_area_m2"] - 25) < 1e-6,
                    "geometry/occupancy does not match",
                )
                check(
                    counted
                    == measurement["actual_graph_stage_bound_by_plan"][plan0["plan_id"]][
                        "after_roofs_before_grass"
                    ]
                    and max(counted.values()) <= oracle.max_radiation_edges
                    and oldrow["nodes"] == measurement["nodes"] <= oracle.max_nodes,
                    "unknown or over-capacity shared stage",
                )
                rows.append(
                    dict(
                        fire_id=name,
                        scale_m=scale,
                        nodes=oldrow["nodes"],
                        raw_point_edges=oldrow["raw_point_edges"],
                        integrated_edges_upper_bound=oldrow["integrated_edges_upper_bound"],
                        actual_stage_upper_bounds={
                            "after_roofs_before_grass": counted,
                            "after_roofs_and_grass": counted,
                        },
                        grass_stage_is_conservative_upper_bound_from_the_same_roofs=True,
                        passed=True,
                    )
                )
        check(len(rows) == 8, "eight native cases expected")
        return rows

    n.prepare_geometry_preflight = shared_roof_stage
    n.prepare(config_path, out)
    conf = read_json(out / "experiment.json")
    check(
        conf["problem"]["fires"] == e["problem"]["fires"]
        and conf["source_sha256"]["src/pyfireca/village/solver_v3.py"]
        == e["source_sha256"]["src/pyfireca/village/solver_v3.py"],
        "prepared geometry or solver drift",
    )
    binding = dict(
        schema_version="same-roof-native-resource-equivalence-1",
        source_eight_case_preflight_sha256=file_sha256(original / "preflight.json"),
        source_geometry_witness_sha256=file_sha256(source_measure),
        selected_problem_sha256=conf["problem_sha256"],
        new_config_sha256=file_sha256(config_path),
        full_source_and_refinement_sha256=content_hash(e["problem"]["fires"]),
        C_m2=v["minimum_C_m2"],
        eight_case_separate_budget=8,
        native_scenarios_committed=8,
        old_budget_touched=False,
        proof="Masks after grass removals are subsets of those following the identical roof removals. Frozen point and integrated membership, geometry, and fixed event parameters are unchanged; both upper bounds monotonically enclose the new true edges. Native builder hard guards stay active.",
        method_sha256=file_sha256(__file__),
        new_CA_fires=0,
    )
    binding["receipt_sha256"] = content_hash(binding)
    with (out / "same-roof-capacity-qualification.json").open("x") as f:
        json.dump(binding, f, indent=2, allow_nan=False)
        f.write("\n")
    print(
        "verified identical four source footprints, eight meshes and roof-stage bounds", flush=True
    )


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
