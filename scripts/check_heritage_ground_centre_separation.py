"""Tighten protective separation using the native point support of whole ground cells."""

import argparse
import json
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_core_separation import distances_to_individual_core_atoms, require

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study


def compute(cert_path, verified):
    cert = read_json(cert_path)
    village = cert["village"]
    parent_record = next(row for row in verified["villages"] if row["village"] == village)
    require(
        parent_record["input_certificate_sha256"] == file_sha256(cert_path), "upstream mismatch"
    )
    exp = read_json(Path(cert["native_reference_run"]) / "experiment.json")
    config = exp["config"]
    bundle, transport, inventory = load_study(Path(config["study_path"]))
    p = bundle.request
    require(
        request_fingerprint(p) == cert["native_case_sha256"]
        and inventory.sha256 == cert["inventory_sha256"],
        "physical parent differs",
    )
    root = Path(__file__).resolve().parents[1]
    for code, sha in exp["source_sha256"].items():
        if code.startswith("src/"):
            require(file_sha256(root / code) == sha, "reader code differs from native snapshot")
    layout = resolve_roof_layout(
        p,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config["core_overlap_policy"],
    )
    geometry = np.asarray(layout.geometry, dtype=object)
    protected = {b.building_id for b in inventory.targets}
    is_core = np.array([bool(protected.intersection(ids)) for ids in layout.owners])
    is_roof = np.array([bool(ids) for ids in layout.owners])
    active = layout.node_inputs.burnable_mask.ravel()
    core = np.flatnonzero(is_core)
    outside = np.flatnonzero(~is_core & active)
    radius = transport.exposure_radius_m
    cutoff = radius + p.grid.cell_size_m
    polygon_gap = distances_to_individual_core_atoms(geometry, core, outside, cutoff)
    supports = geometry.copy()
    ground = np.flatnonzero(active & ~is_roof)
    supports[ground] = shapely.points(layout.xy_m[ground])
    limit_gap = distances_to_individual_core_atoms(supports, core, outside, cutoff)
    # Disjoint polygons preclude the native positive-length contact path.
    obligated = outside[(limit_gap <= radius) | (polygon_gap <= 0)]
    roofs = {k for i in obligated for k in layout.owners[i]}
    cells = {int(layout.cell_id[i]) for i in obligated if not layout.owners[i]}
    require(roofs == set(cert["roof_actions"]), "unexpected roof delta")
    old_ground = set(cert["ground_parent_actions"])
    require(cells <= old_ground, "new ground node in tightened criterion")
    cleared = np.asarray([bool(g) and set(g) <= roofs for g in layout.owners])
    cleared |= np.isin(layout.cell_id, list(cells))
    require(not np.any(cleared[core]), "core cleared")
    retained = ~cleared[outside]
    nearest_support = float(limit_gap[retained].min())
    nearest_polygon = float(polygon_gap[retained].min())
    require(radius + 1e-8 < nearest_support < cutoff, "radiation inequality failed")
    require(0 < nearest_polygon < cutoff, "contact inequality failed")
    restored_mask = np.isin(layout.cell_id[outside], list(old_ground - cells))
    require(
        not np.any(is_roof[outside][restored_mask])
        and np.count_nonzero(restored_mask) == len(old_ground - cells),
        "not whole ground cells",
    )
    # Recalculate identical result from the original polygon criterion by restoring only whole
    # ground cells that provably cannot transmit directly to any core.
    exact_restorable = set(
        layout.cell_id[outside][
            np.isin(layout.cell_id[outside], list(old_ground))
            & (limit_gap > radius)
            & (polygon_gap > 0)
        ].tolist()
    )
    require(exact_restorable == old_ground - cells, "restoration and direct construction differ")
    feasible_cost = cert["roof_union_m2"] + len(cells) * p.grid.cell_area_m2
    amount_saved = p.grid.cell_area_m2 * len(exact_restorable)
    require(
        abs(parent_record["raw_owner_difference_C_m2"] - amount_saved - feasible_cost) < 1e-6,
        "independent physical union cost changed",
    )
    return {
        "village": village,
        "parent_certificate_sha256": file_sha256(cert_path),
        "protected_buildings": len(protected),
        "external_burnable_parent_patches_examined": len(outside),
        "necessary_external_parent_patches": len(obligated),
        "roof_action_count": len(roofs),
        "ground_clearing_action_count": len(cells),
        "ground_cells_restored": len(exact_restorable),
        "roof_union_m2": cert["roof_union_m2"],
        "whole_ground_clearance_m2": len(cells) * p.grid.cell_area_m2,
        "minimum_C_m2_for_mixed_support_rule": feasible_cost,
        "area_saved_m2": amount_saved,
        "radiation_radius_m": radius,
        "minimum_retained_radiation_support_gap_m": nearest_support,
        "minimum_retained_polygon_gap_m": nearest_polygon,
        "possible_cross_core_contact_pairs": 0,
        "possible_cross_core_radiation_pairs": 0,
        "canonical_actions_sha256": content_hash(
            {"demolished_ids": sorted(roofs), "cleared_physical_cell_ids": sorted(cells)}
        ),
        "exterior_direct_sources_only": True,
        "full_fire_N": None,
        "full_fire_A_m2": None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-audit", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("certificates", nargs="+", type=Path)
    args = ap.parse_args()
    up = read_json(args.original_audit)
    require(up.pop("audit_seal") == content_hash(up), "upstream independent audit seal invalid")
    require(
        file_sha256(Path(__file__).with_name("audit_heritage_core_separation.py"))
        == up["auditor_sha256"],
        "upstream checker differs",
    )
    result = {
        "schema_version": "native-ground-centre-core-separation-1",
        "upstream_independent_verification": file_sha256(args.original_audit),
        "analysis_source_sha256": file_sha256(__file__),
        "criteria": [
            "Each retained ordinary roof polygon is farther than R from every core polygon.",
            "Each retained ground centre is farther than R from every core polygon.",
            "Every retained exterior combustible polygon is disjoint from every core.",
        ],
        "native_argument": [
            (
                "Ground has one unsplit parent, retains the original centre, and is never "
                "an integrated roof."
            ),
            (
                "The final native radiation kernel tests representative points and roof "
                "quadrature samples."
            ),
            (
                "Ground receives and emits radiation from its fixed point, even when the "
                "other endpoint is integrated."
            ),
            (
                "Disjoint polygons exclude refined flame contact. With all cores initially "
                "at ambient, neither channel transfers heat to cores."
            ),
            (
                "Every patch violating either criterion must clear by its whole original "
                "owner group or sole ground cell. Costs and actions obey the frozen "
                "admissibility and overlap rules."
            ),
        ],
        "villages": [compute(p, up) for p in args.certificates],
        "new_CA_trajectories": 0,
        "claim_scope": (
            "Least actual clearance under this sufficient mixed-support rule; arbitrary "
            "subsequent direct ignition on retained exterior fuel; frozen finite-domain "
            "CA with disabled firebrands."
        ),
    }
    result["audit_sha256"] = content_hash(result)
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
