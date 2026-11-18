"""Prepare only the two missing arms of the frozen B roof/grass 2x2 ablation.

Actions are whole ordinary-building IDs and complete eligible parent cells.
No infinitesimal-width interpretation or fire-safety certificate is required.
The two existing arms are immutable external references, never new CA tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import shapely
from prepare_heritage_all_roof_bands import parent_cell_polygons
from prepare_heritage_balanced_roof_bands import finite_retained_sources
from prepare_heritage_joint_longcap import encoded, immutable
from prepare_heritage_optimized_balanced_catalog import zero_ca_validation
from run_heritage_joint_comparison import (
    finite_source_catalog_certificate,
    plan_from_mapping,
    read_master_reference,
    validate_config,
)

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
SEED = ("B0061", "B0071", "B0076")
THIN_PLAN = "roof-seed-core-source-hull-ring-b0-w0.01"
EXPECTED_SOURCES = {
    "B0075-known": "B0075",
    "B0071-known": "B0067",
    "B0062-known": "B0062",
    "B0061-known": "B0063",
}


def ablation_rows(seed, thin):
    """Declare exactly D3/G12 and D9/G0; never schedule the old diagonal."""
    if tuple(sorted(seed)) != SEED or thin["plan_id"] != THIN_PLAN:
        raise ValueError("the original D3 seed and exact frozen thin .01 action are required")
    d9 = sorted(thin["demolished_ids"])
    g12 = sorted(thin["cleared_physical_cell_ids"])
    if len(d9) != 9 or not set(seed) < set(d9) or len(g12) != 12 or len(set(g12)) != 12:
        raise ValueError("the frozen whole-object reference must contain D9/G12")
    return [
        {
            "plan_id": "direct-seed-D3-with-thin-G12",
            "demolished_ids": list(SEED),
            "cleared_physical_cell_ids": g12,
            "ground_evidence": "exact_frozen_thin12_parent_cells;direct_whole_object_ablation",
        },
        {
            "plan_id": "direct-thin-D9-with-no-grass",
            "demolished_ids": d9,
            "cleared_physical_cell_ids": [],
            "ground_evidence": "no_vegetation_action;direct_whole_object_ablation",
        },
    ]


def action_cost(parent, base, inventory, row, protected):
    """Use all-owner physical roof removal and disjoint whole-cell footprints."""
    plan = plan_from_mapping(row)
    removed, cells = set(plan.demolished_ids), plan.cleared_physical_cell_ids
    if removed & set(protected):
        raise ValueError("a direct action removes a core or any actual source")
    if parent.grid.cell_size_m != 5:
        raise ValueError("direct grass actions require the frozen 5m parent grid")
    if removed - set(inventory.by_id):
        raise ValueError("a direct roof action has an unknown inventory identity")
    roof_clear = np.array([bool(o) and set(o) <= removed for o in base.owners], bool)
    protected_clear = any(
        yes and set(o) & set(protected) for yes, o in zip(roof_clear, base.owners, strict=True)
    )
    if protected_clear:
        raise ValueError("a direct action physically clears protected shared roof")
    eligible = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    roof_cells = {int(c) for c, o in zip(base.cell_id, base.owners, strict=True) if o}
    if any(i >= len(eligible) or not eligible[i] or i in roof_cells for i in cells):
        raise ValueError("a direct grass action is not an eligible whole nonroof parent cell")
    roof_shapes = [g for g, yes in zip(base.geometry, roof_clear, strict=True) if yes]
    grass_shapes = parent_cell_polygons(parent.grid)[list(cells)]
    full_roofs = shapely.union_all([b.geometry for b in inventory.by_id.values()])
    grass_union = shapely.union_all(grass_shapes)
    if grass_union.intersection(full_roofs).area > 1e-8:
        raise ValueError("a direct grass construction footprint overlaps a real inventory roof")
    roof_area = math.fsum(g.area for g in roof_shapes)
    grass_area = len(cells) * parent.grid.cell_area_m2
    union = shapely.union_all([*roof_shapes, *grass_shapes])
    if not math.isclose(union.area, roof_area + grass_area, rel_tol=0, abs_tol=1e-6):
        raise ValueError("construction footprints fail the unique physical-union check")
    actual_clear = roof_clear | np.isin(base.cell_id, cells)
    return {
        **deepcopy(row),
        "planned_roof_ID_count": len(removed),
        "cleared_roof_node_count": int(roof_clear.sum()),
        "cleared_roof_geometry_union_area_m2": roof_area,
        "cleared_vegetation_parent_cell_count": len(cells),
        "cleared_vegetation_ground_footprint_area_m2": grass_area,
        "cleared_vegetation_covered_fuel_area_m2": float(
            parent.parameters.covered_area_m2.ravel()[list(cells)].sum()
        ),
        "cleared_roof_covered_fuel_area_m2": float(
            base.node_inputs.parameters.covered_area_m2.ravel()[roof_clear].sum()
        ),
        "C_construction_action_area_m2": roof_area + grass_area,
        "physical_union_recomputed_area_m2": float(union.area),
        "actual_cleared_base_node_mask_sha256": hashlib.sha256(actual_clear.tobytes()).hexdigest(),
        "protected_original_roof_node_cleared_count": 0,
        "cost_basis": (
            "all_original_roof_owners_removed_AND;whole25m2_ground_footprints;unique_union"
        ),
        "covered_area_is_not_added_to_construction_footprint": True,
    }


def prepare(thin_config_path, seed_config_path, thin_run, seed_run, output, report_prefix):
    started = time.monotonic()
    thin_config_path, seed_config_path, thin_run, seed_run, output, report_prefix = [
        Path(p).resolve()
        for p in (thin_config_path, seed_config_path, thin_run, seed_run, output, report_prefix)
    ]
    template, seed_config = read_json(thin_config_path), read_json(seed_config_path)
    validate_config(template)
    if (
        template.get("terminal_fire_verification") is not True
        or template.get("terminal_horizon_cap_s") != 172800
        or template.get("required_solver_source_sha256") is None
    ):
        raise ValueError("the clock-audited 172800s thin production template is required")
    clock_path = Path(template["clock_compatibility_receipt_path"])
    clock = read_json(clock_path)
    solver = file_sha256(ROOT / "src/pyfireca/village/solver_v3.py")
    if (
        clock.get("schema_version") != "v3-sample-clock-error-path-extension-audit-1"
        or clock.get("remove_exact_added_error_branch_restores_entire_old_file_byte_for_byte")
        is not True
        or clock.get("audit_sha256")
        != content_hash({k: v for k, v in clock.items() if k != "audit_sha256"})
        or clock.get("regression_validation", {}).get("total_passed", 0) < 123
        or solver != clock["new_solver_sha256"]
        or solver != template["required_solver_source_sha256"]
        or file_sha256(clock_path) != template["clock_compatibility_receipt_sha256"]
    ):
        raise ValueError("the exact audited solver and clock receipt are required")
    reference, _ = read_master_reference(thin_config_path, template)
    external = []
    inputs = {
        "thin-config.json": thin_config_path,
        "seed-config.json": seed_config_path,
        "thin-catalog.json": Path(template["vegetation_proposals_path"]),
        "clock-receipt.json": clock_path,
        "seed-receipt.json": Path(template["roof_seed_receipt_path"]),
        "seed-search.json": Path(template["roof_seed_search_snapshot_path"]),
        "preparer.py": Path(__file__).resolve(),
        "union-helper.py": ROOT / "scripts/prepare_heritage_all_roof_bands.py",
        "validation-helper.py": ROOT / "scripts/prepare_heritage_optimized_balanced_catalog.py",
    }
    for role, path, run, config, plan_id in (
        ("D3-G0", seed_config_path, seed_run, seed_config, "roof-seed"),
        ("D9-G12", thin_config_path, thin_run, template, THIN_PLAN),
    ):
        read_master_reference(path, config)
        experiment_path = run / "experiment.json"
        experiment = read_json(experiment_path)
        if (
            experiment["config"] != config
            or experiment["config_sha256"] != file_sha256(path)
            or content_hash(experiment["problem"]) != experiment["problem_sha256"]
            or config["reference_master_experiment_sha256"]
            != template["reference_master_experiment_sha256"]
            or content_hash(experiment["problem"]["fires"])
            != content_hash(reference["problem"]["fires"])
        ):
            raise ValueError("an immutable external reference differs from the common source mesh")
        external_solvers = [
            digest
            for relative, digest in experiment["source_sha256"].items()
            if relative.endswith("/solver_v3.py")
        ]
        if external_solvers != [clock["old_solver_sha256"] if role == "D3-G0" else solver]:
            raise ValueError("external solver is outside the exact clock-compatibility audit")
        row = next(p for p in config["plans"] if p["plan_id"] == plan_id)
        if finite_retained_sources(reference["problem"]["fires"], row["demolished_ids"]) != (
            EXPECTED_SOURCES
        ):
            raise ValueError("an external arm has different actual retained sources")
        inputs[role + "-experiment.json"] = experiment_path
        external.append(
            {
                "factorial_arm": role,
                "run_path": str(run),
                "experiment_path": str(experiment_path),
                "experiment_sha256": file_sha256(experiment_path),
                "problem_sha256": experiment["problem_sha256"],
                "plan": row,
                "included_in_new_evaluation_plans": False,
                "existing_outcomes_must_be_verified_natively_before_composition": True,
                "native_terminal_values_or_statuses_transferred": False,
            }
        )
    if external[0]["plan"]["cleared_physical_cell_ids"]:
        raise ValueError("the external seed reference is not D3/G0")
    thin = next(
        p for p in read_json(inputs["thin-catalog.json"])["plans"] if p["plan_id"] == THIN_PLAN
    )
    if any(
        thin[k] != external[1]["plan"][k]
        for k in ("demolished_ids", "cleared_physical_cell_ids")
    ):
        raise ValueError("the external thin physical action differs from its frozen catalog")
    rows = ablation_rows(external[0]["plan"]["demolished_ids"], thin)
    certificate = finite_source_catalog_certificate(
        [plan_from_mapping(p) for p in rows],
        reference["problem"]["fires"],
        inventory_sha256=reference["problem"]["inventory_sha256"],
        reference_problem_sha256=reference["problem_sha256"],
    )
    if any(r["actual_source_id"] != EXPECTED_SOURCES[r["fire_id"]] for r in certificate["rows"]):
        raise ValueError("direct actions changed an actual source; replacement is forbidden")
    initial = {str(p): file_sha256(p) for p in inputs.values()}
    for path, digest in template["additional_frozen_inputs_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("an inherited thin frozen input changed: " + path)
    for path, digest in reference["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("a reference physical/source input changed: " + path)
    bundle, _, inventory = load_study(Path(template["study_path"]))
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=template["max_nodes"],
        core_overlap_policy=template.get("core_overlap_policy", "reject"),
    )
    protected = {t.building_id for t in inventory.targets} | set(EXPECTED_SOURCES.values())
    plans = [action_cost(bundle.request, base, inventory, p, protected) for p in rows]
    external_costs = [
        action_cost(bundle.request, base, inventory, p["plan"], protected) for p in external
    ]
    if any(
        not math.isclose(a["C_construction_action_area_m2"], expected, rel_tol=0, abs_tol=1e-6)
        for a, expected in zip(plans, (743.8419687401408, 1089.0173624408872), strict=True)
    ):
        raise ValueError("the declared direct-action cost no longer matches the frozen geometry")
    design_path = Path(str(report_prefix) + "-design.json")
    catalog_path = Path(str(report_prefix) + "-catalog.json")
    receipt_path = Path(str(report_prefix) + "-preparation.json")
    artifacts = {output.with_suffix("." + name): p.read_bytes() for name, p in inputs.items()}
    design = {
        "schema_version": "heritage-direct-action-factorial-design-1",
        "new_factorial_arms": ["D3-G12", "D9-G0"],
        "external_factorial_arms_not_rescheduled": ["D3-G0", "D9-G12"],
        "selection": "parent_authorized_whole_object_roof_grass_contribution_ablation",
        "no_nanometre_width_or_geometric_fire_safety_claim": True,
        "same_four_actual_sources_required": EXPECTED_SOURCES,
        "new_independent_fire_draw_count": 0,
        "planned_new_CA_checks": 16,
        "native_CA_outcomes_read_for_selection_by_this_preparer": [],
        "input_sha256": initial,
    }
    artifacts[design_path] = encoded(design)
    proof_path = output.with_suffix(".finite-source-certificate.json")
    artifacts[proof_path] = encoded(certificate)
    catalog = {
        "schema_version": "heritage-direct-action-ablation-catalog-1",
        "village_id": template["village_id"],
        "plans": plans,
        "reference_master_problem_sha256": reference["problem_sha256"],
        "finite_source_certificate_sha256": certificate["certificate_sha256"],
        "external_existing_arms": [
            {**e, "geometry_cost_receipt": c}
            for e, c in zip(external, external_costs, strict=True)
        ],
        "source_coverage_scope": "exact_two_declared_plans;old_roof_area_bound_not_extended",
        "global_optimality_proven": False,
        "new_CA_checks": 0,
    }
    artifacts[catalog_path] = encoded(catalog)
    additional = {str(p): hashlib.sha256(data).hexdigest() for p, data in artifacts.items()}
    config = deepcopy(template)
    config.update(
        plans=rows,
        max_new_checks=16,
        max_total_checks=16,
        max_wall_time_s=172800.0,
        terminal_horizon_cap_s=172800.0,
        source_coverage_policy="finite_declared_plans",
        vegetation_proposals_path=str(catalog_path),
        vegetation_proposals_sha256=additional[str(catalog_path)],
        roof_seed_receipt_path=str(output.with_suffix(".seed-receipt.json")),
        roof_seed_search_snapshot_path=str(output.with_suffix(".seed-search.json")),
        clock_compatibility_receipt_path=str(output.with_suffix(".clock-receipt.json")),
        additional_frozen_inputs_sha256=additional,
        scope="two_new_whole_object_factorial_arms;two_existing_arms_external_only;finite_source_catalog",
        seed_reverification="old_seed_is_external_control_only;new_arms_require_all8_fresh_checks",
    )
    for stale in ("execution_scenario_ids", "execution_case_order", "execution_priority_plan_ids"):
        config.pop(stale, None)
    validate_config(config)
    for path, data in artifacts.items():
        immutable(path, data)
    immutable(output, encoded(config))
    audit = zero_ca_validation(output, config, certificate)
    if any(
        r["actual_source_id"] != EXPECTED_SOURCES[r["fire_id"]]
        or abs(r["prescribed_source_footprint_area_m2"] - 25) > 1e-8
        for r in audit["plan_source_grass_checks"]
    ):
        raise ValueError("source identity or full 25m2 source budget changed in a direct arm")
    if any(file_sha256(p) != digest for p, digest in initial.items()):
        raise ValueError("an immutable input changed during direct-action preparation")
    receipt = {
        "schema_version": "heritage-direct-action-ablation-preparation-1",
        "config_path": str(output),
        "config_sha256": file_sha256(output),
        "planned_new_CA_checks": 16,
        "new_CA_checks": 0,
        "graph_builds": 0,
        "producer_frozen": False,
        "source_and_parent_guard": audit,
        "cost_receipts": plans,
        "external_existing_arms": external,
        "additional_frozen_inputs_sha256": additional,
        "required_solver_source_sha256": solver,
        "elapsed_s": time.monotonic() - started,
    }
    immutable(receipt_path, encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thin-config", type=Path, required=True)
    parser.add_argument("--seed-config", type=Path, required=True)
    parser.add_argument("--thin-run", type=Path, required=True)
    parser.add_argument("--seed-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-prefix", type=Path, required=True)
    args = parser.parse_args()
    receipt = prepare(
        args.thin_config,
        args.seed_config,
        args.thin_run,
        args.seed_run,
        args.output,
        args.report_prefix,
    )
    print(
        {
            k: receipt[k]
            for k in ("config_path", "planned_new_CA_checks", "new_CA_checks", "elapsed_s")
        }
    )


if __name__ == "__main__":
    main()
