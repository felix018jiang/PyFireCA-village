"""Audit registered protective actions with two aggregate, source-independent dose calculations."""

import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
from audit_heritage_thermal_optimum import material_rows, require, verify_seal
from verify_heritage_total_protection_and_loss import NativeSupport, read_sealed

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def examine():
    runs = ROOT / "runs/research"
    res = ROOT / "docs/results"
    independent = (
        runs / "heritage-pingyan-independent-three-stage-supplement-20261111" / "experiment.json"
    )
    slim = runs / "heritage-pingyan-observed-clearance-subset-20261111" / "experiment.json"
    baseline = runs / "heritage-thermal-and-geometric-pareto-pingyan-20261111" / "experiment.json"
    registration = (
        ROOT
        / "examples/research/heritage/confirmation-20261110"
        / "pingyan-three-fixed-plans-20261111/final-candidates.json"
    )
    margin = res / "heritage-full-capacity-discrete-optimality-pingyan-20261111.json"
    best = res / "heritage-thermal-clearance-pingyan-20261111.json"
    recovery = res / "heritage-pingyan-hrr-error-recovery-registration-20261111.json"
    ref, held, extra = (read_json(p) for p in (baseline, independent, slim))
    orig = verify_seal(best)
    proof = read_sealed(margin)
    recovered = read_json(recovery)
    require(
        held["config"]["plans"] == read_json(registration)["plans"], "independent actions changed"
    )
    require(len(extra["config"]["plans"]) == 1, "unexpected design action")
    common = ref["problem"]
    c = ref["config"]
    bundle, t, inv = load_study(Path(c["study_path"]))
    phys = bundle.request
    require(
        request_fingerprint(phys) == common["case_sha256"]
        and inv.sha256 == common["inventory_sha256"]
        and asdict(t) == common["transport_parameters"]
        and t.wind_speed_m_s == 0
        and phys.firebrands is None,
        "parent physics changed",
    )
    stages = {}
    solver = "src/pyfireca/village/solver_v3.py"
    for f, exp in ((baseline, ref), (independent, held), (slim, extra)):
        require(
            all(
                exp["problem"][k] == common[k]
                for k in (
                    "case_sha256",
                    "inventory_sha256",
                    "transport_parameters",
                    "overlap_policy",
                )
            ),
            "physical recipe changed",
        )
        require(
            all(exp["config"][k] == c[k] for k in ("max_nodes", "core_overlap_policy", "max_dt_s")),
            "native geometry or dt changed",
        )
        physical_keys = {k for k in ref["source_sha256"] if k.startswith("src/")}
        require(
            physical_keys == {k for k in exp["source_sha256"] if k.startswith("src/")},
            "module set differs",
        )
        for k in physical_keys:
            sha = exp["source_sha256"][k]
            require(sha == file_sha256(f.parent / "source-snapshot" / k), "native module differs")
            if k == solver:
                require(
                    sha in {recovered["old_solver_sha256"], recovered["new_solver_sha256"]},
                    "unregistered fuel step",
                )
            else:
                require(sha == ref["source_sha256"][k], "transport or fuel model differs")
        stages[str(f.relative_to(ROOT))] = exp["source_sha256"][solver]
    layout = resolve_roof_layout(
        phys, inv, max_nodes=c["max_nodes"], core_overlap_policy=c["core_overlap_policy"]
    )
    rows, _, contact = material_rows(phys, layout, t, inv)
    model = NativeSupport(layout, t)
    protected = {x.building_id for x in inv.targets}
    core = np.flatnonzero([bool(protected.intersection(g)) for g in layout.owners])
    ex = np.setdiff1d(np.flatnonzero(layout.node_inputs.burnable_mask.ravel()), core)
    require(len(protected) == 3 and len(rows) == len(core), "core support mismatch")
    tr, sr, frac, _, contact_src = model.pairs(ex, core)
    original_area = build_cleared_area_function(
        layout.owners,
        [g.area for g in layout.geometry],
        area_basis="original_disjoint_roof_geometry;all_owners_removed",
    )
    legal = (
        phys.domain_mask
        & phys.known_mask
        & phys.burnable_mask
        & (phys.building_index < 0)
        & (phys.parameters.covered_area_m2 > 0)
    ).ravel()
    optimal = {
        "plan_id": "core_protection_only_reference",
        "demolished_ids": orig["roof_actions"],
        "cleared_physical_cell_ids": orig["ground_parent_actions"],
    }
    roles = ["independently_registered"] * 3 + ["design_only", "core_only_reference"]
    actions = [*held["config"]["plans"], *extra["config"]["plans"], optimal]
    results = []
    for role, action in zip(roles, actions, strict=True):
        D = set(action["demolished_ids"])
        G = set(action["cleared_physical_cell_ids"])
        removed = D | G
        cleared = model.mask(D, G)
        cells = np.isin(layout.cell_id, list(G))
        require(
            set(inv.candidate_ids) >= D
            and not D & protected
            and legal[list(G)].all()
            and int(cells.sum()) == len(G)
            and not np.any(cells & model.roof)
            and not cleared[core].any(),
            "illegal action or missing heritage receptor",
        )
        first = np.array(
            [math.fsum(q for g, q in row.items() if not set(g) <= removed) for row in rows]
        )
        kept = ~cleared[sr]
        second = np.bincount(tr[kept], weights=frac[kept], minlength=len(layout.owners))[core]
        require(
            np.allclose(first, second, rtol=1e-11, atol=1e-11),
            "independent receptor bounds disagree",
        )
        retained = any(not set(g) <= removed for g in contact)
        require(
            retained == bool((~cleared[contact_src]).any()),
            "independent exterior contacts disagree",
        )
        peak = float(max(first))
        ok = not retained and peak <= 0.99
        c_area = original_area(D) + len(G) * phys.grid.cell_area_m2
        results.append(
            dict(
                role=role,
                plan=action["plan_id"],
                C_m2=c_area,
                direct_exterior_contacts_all_blocked=not retained,
                worst_absorbed_energy_over_dry_ignition_requirement=peak,
                registered_99pct_all_core_certificate=ok,
                unreserved_strict_all_core_condition=(not retained and peak < 1.0 - 1e-9),
                all_external_only_ignition_heritage_safety="certified" if ok else "unknown",
            )
        )
    subproof = proof["closed_relaxation_verification"]
    require(
        proof["audited_closed_threshold"] == 1.0
        and proof["strict_threshold_met_by_this_incumbent"]
        and subproof["cheaper_discrete_solutions_excluded"]
        and subproof["unresolved_cheaper_roof_subsets"] == 0
        and proof["strict_threshold_minimum_proved_for_registered_equation_and_geometry_tables"]
        and results[-1]["registered_99pct_all_core_certificate"]
        and abs(results[-1]["C_m2"] - subproof["C_m2"]) < 1e-6,
        "cost floor or control failed",
    )
    paths = (
        baseline,
        independent,
        slim,
        registration,
        margin,
        best,
        recovery,
        ROOT / "scripts/audit_heritage_thermal_optimum.py",
        ROOT / "scripts/verify_heritage_total_protection_and_loss.py",
    )
    return dict(
        village="pingyan",
        criterion="All external fuel may burn; direct core contact "
        "must be blocked, total radiation into each core receptor below dry ignition heat.",
        scope="Frozen finite fuel, zero wind, no firebrands. External direct starts only. "
        "Refinement conserves parent material and does not increase the bound.",
        core_building_count=len(protected),
        core_parent_fragments=len(core),
        separate_per_receptor_computations_agreed=True,
        registered_source_locations_used_in_energy_calculation=False,
        fresh_source_draws=0,
        aggregate_only=True,
        CA_runs=0,
        loss_endpoints_computed=0,
        closed_and_strict_capacity_least_C_m2=subproof["C_m2"],
        registered_99pct_and_closed_100pct_min_cost_same=True,
        actions=results,
        numerical_scope="Fuel-conserving analytical inequality. The registered HRR "
        "arithmetic fallback clips interval energy to remaining fuel times combustion heat.",
        solver_versions=stages,
        input_file_hashes={str(p.relative_to(ROOT)): file_sha256(p) for p in paths},
    )


if __name__ == "__main__":
    result = examine()
    result["method_sha256"] = file_sha256(__file__)
    result["receipt_sha256"] = content_hash(result)
    out = (
        ROOT
        / "docs/results"
        / "heritage-pingyan-registered-all-exterior-heritage-energy-audit-20261111.json"
    )
    with out.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
