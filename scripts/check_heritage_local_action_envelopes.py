"""Test a sufficient first-escape inequality on already frozen Pingyan local placements."""

import json
import math
from pathlib import Path

import numpy as np
from check_heritage_compartment_first_escape import load_bound_inputs, pair_conditional_doses
from check_heritage_registered_roof_compartment import area_outward, footprint_budget
from shapely import STRtree

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import realize_sources
from pyfireca.planning.strategy_io import file_sha256, read_json


def audit(root):
    runs = root / "runs/research"
    paths = [
        runs / x / "experiment.json"
        for x in (
            "heritage-protected-union-pingyan-20261111",
            "heritage-pingyan-observed-clearance-subset-20261111",
        )
    ]
    old, new = (read_json(x) for x in paths)
    if old["problem"]["fires"] != new["problem"]["fires"]:
        raise ValueError("physical source meshes differ")
    plans = [p for p in old["config"]["plans"] if p["plan_id"].endswith(("w2.5", "w15.01"))] + new[
        "config"
    ]["plans"]
    _, _, layout, inv, transport, _, _, _, conf = load_bound_inputs("pingyan")
    if any(e["problem"]["inventory_sha256"] != inv.sha256 for e in (old, new)):
        raise ValueError("different village inventory")
    polygons = np.asarray(layout.geometry, dtype=object)
    tree = STRtree(polygons)
    area = layout.node_inputs.parameters.covered_area_m2.ravel()
    protected = {x.building_id for x in inv.targets}
    core = np.array([not protected.isdisjoint(g) for g in layout.owners])
    unrepresented = set(inv.by_id) - {
        o for i, g in enumerate(layout.owners) if area[i] > 0 for o in g
    }
    results = []
    for plan in plans:
        roofs, ground = set(plan["demolished_ids"]), set(plan["cleared_physical_cell_ids"])
        clear = np.array([bool(g) and set(g) <= roofs for g in layout.owners]) | np.isin(
            layout.cell_id, list(ground)
        )
        if np.any(clear & core):
            raise ValueError("protected area in clearing")
        active = layout.node_inputs.burnable_mask.ravel() & ~clear
        families = []
        for ordinal, fire in enumerate(old["problem"]["fires"].values(), 1):
            (draw,) = realize_sources(fire["protocol"], inv, demolished_ids=sorted(roofs))
            source, fp = draw["source_id"], draw["footprint"]
            if fp.sha256 != content_hash(fire["footprints"][source]):
                raise ValueError("different physical footprint")
            _, hits = footprint_budget(layout, polygons, tree, source, fp, roofs, ground)
            group = np.array([source in g for g in layout.owners]) & active
            if not np.all(group[hits]):
                raise ValueError("source not enclosed")
            row = dict(family_ordinal=ordinal, source_footprint_sha256=fp.sha256, certified=False)
            for _ in range(12):
                nodes = np.flatnonzero(group)
                if np.any(group & core):
                    row["sufficient_test"] = "potential_envelope_includes_core"
                    break
                if len(nodes) > 256:
                    row["sufficient_test"] = "local_search_size_limit"
                    break
                external = np.flatnonzero(active & ~group)
                doses, contact, _ = pair_conditional_doses(
                    layout, polygons, nodes, external, [tuple(nodes)], transport
                )
                adds = contact | {i for i, q in doses.items() if q >= 1 - 1e-10}
                if not adds:
                    owners = unrepresented.union(*(set(layout.owners[i]) for i in nodes))
                    row.update(
                        certified=True,
                        sufficient_test="no_external_contact_or_ignition_energy",
                        core_nonignition=True,
                        N_owner_upper=len(owners),
                        A_upper_m2=area_outward(
                            math.fsum(area[nodes]), len(nodes), conf["max_nodes"]
                        ),
                    )
                    break
                group[list(adds)] = True
            else:
                row["sufficient_test"] = "local_search_round_limit"
            families.append(row)
        results.append(
            dict(
                plan_id=plan["plan_id"],
                families=families,
                certified_family_count=sum(r["certified"] for r in families),
            )
        )
    return dict(
        registered_families=4,
        plans=results,
        native_CA_checks=0,
        scope="fixed local actions, no core immunity assumed, no new fire draws",
        noncertification="the sufficient bound is inconclusive, not an ignition outcome",
        parent_experiments_sha256=[file_sha256(p) for p in paths],
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    result = audit(root)
    result["receipt_sha256"] = content_hash(result)
    with (root / "docs/results/heritage-pingyan-local-first-escape-envelopes-20261111.json").open(
        "x"
    ) as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
