"""Bound aggregate losses for a frozen joint action on the four new Pingyan draws."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import shapely
from audit_heritage_compartment_bounds import components, potential_edges
from check_heritage_compartment_first_escape import load_bound_inputs, pair_conditional_doses
from check_heritage_registered_roof_compartment import area_outward, footprint_budget, verify

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def audit(directory):
    root = Path(__file__).resolve().parents[1]
    original = directory / "original-binding"
    freeze = directory / "final-candidates.json"
    bind = read_json(original / "binding-receipt.json")
    freeze_digest = file_sha256(freeze)
    verify(bind["final_candidates_sha256"] == freeze_digest, "final actions changed")
    protocols = read_json(original / "confirmation-config.json")["source_fires"]
    plan = read_json(freeze)["plans"][0]
    verify(plan["plan_id"] == "fixed-core-and-first-escape-compartment", "wrong frozen plan")
    joint_path = root / "docs/results/heritage-total-protection-and-loss-pingyan-20261111.json"
    joint = read_json(joint_path)
    verify(
        joint["receipt_sha256"]
        == content_hash({k: v for k, v in joint.items() if k != "receipt_sha256"}),
        "invalid joint certificate",
    )
    design = next(
        x for x in joint["optimized_total_construction"] if x["core_energy_ceiling"] == 0.99
    )
    roofs, grass = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
    verify(
        roofs == set(design["demolished_ids"])
        and grass == set(design["cleared_physical_cell_ids"]),
        "design action differs from confirmation",
    )
    _, _, layout, inv, transport, _, _, _, conf = load_bound_inputs("pingyan")
    polygons = np.asarray(layout.geometry, dtype=object)
    tree = shapely.STRtree(polygons)
    core = {t.building_id for t in inv.targets}
    physical_area = layout.node_inputs.parameters.covered_area_m2.ravel()
    removed = np.array([bool(g) and set(g) <= roofs for g in layout.owners]) | np.isin(
        layout.cell_id, list(grass)
    )
    kept = (
        layout.node_inputs.burnable_mask.ravel()
        & ~removed
        & np.array([core.isdisjoint(g) for g in layout.owners])
    )
    missing = set(inv.by_id) - {
        o for i, g in enumerate(layout.owners) if physical_area[i] > 0 for o in g
    }
    rows = []
    labels = None
    bound_by_fire = {r["fire_id"]: r for r in bind["common_source_and_refinement_requirements"]}
    verify(
        len(bound_by_fire) == len(protocols) == 4 and bind["new_independent_draws"] == 4,
        "independent denominator changed",
    )
    for ordinal, record in enumerate(protocols, 1):
        path = Path(record["protocol_path"])
        verify(
            file_sha256(path) == bind["source_protocol_files_sha256"][str(path)],
            "source protocol changed",
        )
        (actual,) = realize_sources(read_json(path), inv, demolished_ids=sorted(roofs))
        original_row = next(
            p
            for p in bound_by_fire[record["fire_id"]]["per_plan"]
            if p["plan_id"] == plan["plan_id"]
        )
        source, fp = actual["source_id"], actual["footprint"]
        verify(
            source == original_row["source_id"] and fp.sha256 == original_row["footprint_sha256"],
            "confirmation footprint changed",
        )
        budget, hits = footprint_budget(layout, polygons, tree, source, fp, roofs, grass)
        original_roof = np.array([source in g for g in layout.owners]) & kept
        group = original_roof.copy()
        verify(group[hits].all(), "initial ignition lies outside the envelope")
        # A bounded local proof search avoids quadratic work on large components.
        # If the search stops, use the entire connected potential-transfer component.
        method = "complete_geometric_component"
        for _ in range(12):
            nodes = np.flatnonzero(group)
            if len(nodes) > 256:
                break
            external = np.flatnonzero(kept & ~group)
            q, contacts, _ = pair_conditional_doses(
                layout, polygons, nodes, external, [tuple(nodes)], transport
            )
            added = contacts | {j for j, ratio in q.items() if ratio >= 1 - 1e-10}
            if not added:
                method = "closed_cumulative_energy_envelope"
                break
            group[list(added)] = True
        if method == "complete_geometric_component":
            if labels is None:
                labels = components(potential_edges(layout, transport.exposure_radius_m), kept)
            touched = np.unique(labels[original_roof])
            verify(-1 not in touched, "ignition not on retained material")
            group = np.isin(labels, touched)
        nodes = np.flatnonzero(group)
        owners = missing.union(*(set(layout.owners[j]) for j in nodes if layout.owners[j]))
        rows.append(
            dict(
                confirmation_ordinal=ordinal,
                source_footprint_sha256=fp.sha256,
                physical_direct_area_m2=budget[0],
                bound_method=method,
                A_upper_m2=area_outward(
                    math.fsum(physical_area[nodes]), len(nodes), conf["max_nodes"]
                ),
                N_owner_upper=len(owners),
            )
        )
    return dict(
        village="pingyan",
        frozen_action_file_sha256=freeze_digest,
        confirmation_binding_sha256=file_sha256(original / "binding-receipt.json"),
        joint_design_sha256=file_sha256(joint_path),
        basis=(
            "universal exterior-to-core protection, fixed action, "
            "first-escape energy envelope or full connected component, no native outcomes"
        ),
        C_m2=design["C_m2"],
        families=rows,
        native_CA_runs=0,
        new_plans_selected=0,
        worst_certified_A_upper_m2=max(r["A_upper_m2"] for r in rows),
        worst_certified_N_owner_upper=max(r["N_owner_upper"] for r in rows),
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = audit(args.confirmation_dir.resolve())
    value["receipt_sha256"] = content_hash(value)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
