"""Freeze the eight-case Pingyan zero-core-heat experiment and verify both native graph stages."""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from prepare_heritage_joint_longcap import encoded, immutable
from check_heritage_registered_roof_compartment import footprint_budget, verify
from verify_heritage_multiplan_confirmation_sources import check_support


def prepare(root):
    run = root / "runs/research"
    res = root / "docs/results"
    area = root / "examples/research/heritage/area-20261110"
    out = area / "pingyan-zero-core-heat-and-design-loss-native-20261111.json"
    producer = run / "heritage-registered-stage-capacity-producer-20261111"
    from run_heritage_joint_comparison import (
        make_oracle,
        validate_config,
        content_hash,
        file_sha256,
        read_json,
        refine_roof_layout,
    )
    from heritage_confirmation_stage_capacity import stage_counts
    import run_heritage_joint_comparison as runner
    import heritage_confirmation_stage_capacity as capacity

    verify(
        Path(runner.__file__).resolve() == (producer / "scripts/run_heritage_joint_comparison.py")
        and Path(capacity.__file__).resolve()
        == (producer / "scripts/heritage_confirmation_stage_capacity.py"),
        "frozen producer must own oracle and stage counts",
    )
    for module in ("pyfireca.planning.roof_ignition", "pyfireca.village.solver_v3"):
        verify(
            Path(sys.modules[module].__file__).resolve().is_relative_to(producer),
            "numerical physics must be frozen",
        )
    certpath = res / "heritage-pingyan-zero-heat-and-design-loss-20261111.json"
    gis = certpath.with_suffix(".geojson")
    cert = read_json(certpath)
    recovery = (
        run / "heritage-thermal-and-geometric-pareto-hrr-recovery-pingyan-20261111/experiment.json"
    )
    recovery_native = read_json(recovery)
    geometryparent = run / "heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
    geometries = read_json(geometryparent)
    local_parent = run / "heritage-pingyan-observed-clearance-subset-20261111/experiment.json"
    joint_parent = run / "heritage-joint-loss-native-pingyan-20261111/experiment.json"
    comparisons = {"compact_3585": read_json(local_parent), "joint_8465": read_json(joint_parent)}
    manifest = producer / "producer-manifest.json"
    verify(
        cert["receipt_sha256"]
        == content_hash({k: v for k, v in cert.items() if k != "receipt_sha256"})
        and read_json(gis)["certificate_file_sha256"] == file_sha256(certpath)
        and cert["receipt_sources_sha256"]["native_registered_source_and_equations"]
        == file_sha256(geometryparent),
        "sealed geometry inputs changed",
    )
    template = recovery_native["config"]
    zero = next(
        a
        for a in geometries["config"]["plans"]
        if a["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    p = cert["model_action"]
    target = {k: p[k] for k in ("plan_id", "demolished_ids", "cleared_physical_cell_ids")}
    target["ground_evidence"] = (
        "whole legal cells in sealed zero-core-heat and first-escape dose certificate"
    )
    core_set = {
        k
        for k in template["refinement_ids"]
        if k not in {v for prior in template["plans"] for v in prior["demolished_ids"]}
    }
    # Reconstruct four common projected layouts in a new physical question. All interventions,
    # native inherited nonintervention refinements, and the protected buildings remain explicit.
    conf = {
        k: template[k]
        for k in (
            "schema_version",
            "village_id",
            "study_path",
            "source_policy",
            "source_fires",
            "horizon_s",
            "checkpoints_s",
            "terminal_fire_verification",
            "terminal_horizon_cap_s",
            "roof_scales_m",
            "quadrature_spacing_m",
            "max_dt_s",
            "core_overlap_policy",
            "max_nodes",
            "max_radiation_edges",
            "max_wall_time_s",
            "minimum_free_disk_GB",
            "required_solver_source_sha256",
        )
    }
    source_paths = [Path(f["protocol_path"]) for f in conf["source_fires"]]
    verify(
        geometries["problem"]["fires"] == recovery_native["problem"]["fires"]
        and len(source_paths) == 4
        and conf["roof_scales_m"] == [1.0, 0.5],
        "original sources/scale specifications differ",
    )
    extra = [
        certpath,
        gis,
        recovery,
        geometryparent,
        local_parent,
        joint_parent,
        manifest,
        Path(__file__).resolve(),
        *source_paths,
    ]
    conf.update(
        plans=[target],
        refinement_ids=sorted(set(target["demolished_ids"]) | core_set),
        max_new_checks=8,
        max_total_checks=8,
        new_independent_fire_draw_count=0,
        execution_case_order="plans_first",
        execution_priority_plan_ids=[p["plan_id"]],
        stop_plan_after_protection_failure=False,
        additional_frozen_inputs_sha256={str(path): file_sha256(path) for path in extra},
        scope="Eight independent-budget full-fire geometric checks on four original design rankings. Preassigned 25m2 source supports identical to zero-core-only base. No independent draw and no imported native outcome.",
    )
    validate_config(conf)
    weight_probe = conf | {"plans": [zero], "execution_priority_plan_ids": [zero["plan_id"]]}
    preliminary = area / "pingyan-zero-core-heat-and-design-loss-native-registration-20261111.json"
    immutable(preliminary, encoded(conf))
    ora, _ = make_oracle(preliminary, conf, file_sha256(runner.__file__))
    ref, _ = make_oracle(preliminary, weight_probe, file_sha256(runner.__file__))
    verify(
        ora.problem["fires"] == ref.problem["fires"]
        and ora.problem["case_sha256"] == ref.problem["case_sha256"]
        and ora.problem["freeze_integration_membership"] is True,
        "zero-only future control mesh or common integration differs",
    )
    protected = set(t.building_id for t in ora.inventory.targets)
    verify(
        all(protected <= set(f["refined_ids"]) for f in ora.fires.values()),
        "heritage mesh not refined",
    )
    firedig = content_hash(ora.problem["fires"])
    del ref
    L = ora.base
    poly = np.asarray(L.geometry, dtype=object)
    tree = shapely.STRtree(poly)
    D, G = set(target["demolished_ids"]), set(target["cleared_physical_cell_ids"])
    rows = []
    matched = {key: 0 for key in comparisons}
    for ordinal, (name, fire) in enumerate(ora.fires.items(), 1):
        registered = geometries["problem"]["fires"][name]
        source = next(s for s in fire["protocol"]["draws"][0]["priority_ids"] if s not in D)
        fp = fire["footprints"][source]
        verify(
            fire["protocol"] == registered["protocol"]
            and content_hash(registered["footprints"][source]) == fp.sha256,
            "sealed 25 m2 polygon or ordering changed",
        )
        for key, exp in comparisons.items():
            arm = exp["config"]["plans"][0]
            prior = exp["problem"]["fires"][name]
            actual = next(
                s
                for s in prior["protocol"]["draws"][0]["priority_ids"]
                if s not in arm["demolished_ids"]
            )
            matched[key] += (
                actual == source and content_hash(prior["footprints"][actual]) == fp.sha256
            )
        budget, _ = footprint_budget(L, poly, tree, source, fp, D, G)
        for scale in ora.scales_m:
            layout = refine_roof_layout(
                L,
                ora.inventory,
                cell_size_m=scale,
                refined_ids=fire["refined_ids"],
                footprints=tuple(fire["footprints"].values()),
                max_nodes=ora.max_nodes,
            )
            direct = check_support(layout, source, fp.geometry, D, budget)
            bounds, stages = stage_counts(
                layout, ora.transport, fire["refined_ids"], ora.plans.values()
            )
            statuses = [
                max(stage.values()) <= ora.max_radiation_edges
                for stage in stages[target["plan_id"]].values()
            ]
            admitted = len(layout.owners) <= ora.max_nodes and all(statuses)
            verify(admitted, "registered physical graph exceeds hard native capacity")
            rows.append(
                dict(
                    draw_ordinal=ordinal,
                    scale_m=scale,
                    nodes=len(layout.owners),
                    **bounds,
                    physical_support_by_plan={
                        target["plan_id"]: {"footprint_sha256": fp.sha256, **direct}
                    },
                    actual_graph_stage_bound_by_plan=stages,
                    preflight_passed=max(bounds.values()) <= ora.max_radiation_edges,
                    actual_stages_and_nodes_passed=admitted,
                )
            )
            del layout
            print(
                "checked a common geometry with both real material stages",
                len(rows),
                "of 8",
                flush=True,
            )
    verify(
        len(rows) == 8
        and all(v == n for v, n in zip(matched.values(), [2, 3], strict=True))
        and ora._engine is None,
        "incomplete or inconsistent common geometry",
    )
    witness = res / "heritage-pingyan-zero-heat-native-support-and-capacity-20261111.json"
    evidence = dict(
        schema_version="fixed-zero-heat-plan-precomputed-stage-and-source-support-1",
        supplemental_diagnostics_for_all_eight_common_layouts=True,
        rows=rows,
        solver_digest=conf["required_solver_source_sha256"],
        source_coverage=4,
        scales_m=conf["roof_scales_m"],
        checked_source_polygon_geometry_area_mass_energy=True,
        common_design_mesh_all_four_with_zero_core_base=True,
        identical_integration_membership_all_four_with_zero_core_base=True,
        common_layout_source_and_refinement_spec_sha256=firedig,
        same_actual_source_footprint_families=matched,
        previous_design_native_observations_imported=0,
        new_CA_checks=0,
        additional_physical_scope="Complete common source geometry sets are compared on prospective zero-only base before graph accounting. Runtime native layout and frozen integration SHA should still be compared for any future empirical baseline pair.",
        preoptimization_certificate_file_sha256=file_sha256(certpath),
        preliminary_config_file_sha256=file_sha256(preliminary),
        stage_counter_sha256=file_sha256(capacity.__file__),
        method_sha256=file_sha256(__file__),
    )
    evidence["receipt_sha256"] = content_hash(evidence)
    immutable(witness, encoded(evidence))
    conf["supplemental_geometry_receipt"] = {"path": str(witness), "sha256": file_sha256(witness)}
    conf["additional_frozen_inputs_sha256"].update(
        {str(p): file_sha256(p) for p in (preliminary, witness)}
    )
    validate_config(conf)
    immutable(out, encoded(conf))
    print(
        "registered eight independent-budget native evaluations and zero-only common reference",
        flush=True,
    )


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
