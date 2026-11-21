"""Assign documented construction types to existing roof automaton components by role."""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

tower, bridge = "tower_tile_timber", "bridge_tile_timber"
ordinary_types = {"batuan": "ordinary_mixed", "pingyan": "ordinary_timber_tile"}
role_type = {"gulou": tower, "fengyuqiao": bridge}
village_names = {"batuan": "岜团", "pingyan": "平岩"}


def basis(village, kind):
    if village == "batuan":
        return "岜团田野类型归纳"
    return "平岩民居参考照片" if kind == ordinary_types[village] else "参照岜团同类构造"


root_names = {
    "batuan": "batuan-provisional-all-ordinary-study.json",
    "pingyan": "pingyan-study-20261029.json",
}


def assign_roof_types(layout, inventory, village):
    """Accept base or locally refined RoofLayout; ground has no building type."""
    known = {b.building_id: ordinary_types[village] for b in inventory.buildings}
    known.update({t.building_id: role_type[t.role] for t in inventory.targets})
    assigned = []
    for owners in layout.owners:
        kinds = {known[key] for key in owners}
        if len(kinds) > 1:
            raise ValueError("Resolve conflicting physical overlap ownership before typing")
        assigned.append(kinds.pop() if kinds else "")
    return np.array(assigned), known


def export(project, target, verify_refinement=False):
    sys.path.insert(0, str(project / "src"))
    from pyfireca.planning.roof_ignition import refine_roof_layout
    from pyfireca.planning.roof_thermal import resolve_roof_layout
    from pyfireca.planning.study_io import load_study

    target.mkdir(parents=True, exist_ok=True)
    registration = json.loads(
        (project / "output/heritage-wind-20261112/registration.json").read_text()
    )
    totals = []
    for village, name in village_names.items():
        ordinary = ordinary_types[village]
        study = project / "examples/research/heritage" / root_names[village]
        bundle, _, inventory = load_study(study)
        reg = registration["villages"][village]
        if reg["inventory_sha256"] != inventory.sha256:
            raise ValueError("Inventory differs from the version used by the manuscript")
        layout = resolve_roof_layout(
            bundle.request,
            inventory,
            max_nodes=600000,
            core_overlap_policy=reg["core_overlap_policy"],
        )
        kinds, by_id = assign_roof_types(layout, inventory, village)
        roof = kinds != ""
        fields = layout.node_inputs.parameters
        heatcode = layout.node_inputs.material_index.ravel()
        if not np.all(heatcode[roof] == 5):
            raise ValueError("Current run uses another thermal profile")
        positive = roof & (fields.covered_area_m2.ravel() > 1.0e-7)
        for array, expected in (
            (fields.ignition_temperature_K.ravel()[positive], 520.0),
            (fields.moisture_content_dry_basis.ravel()[positive], 0.10),
            (
                fields.initial_dry_fuel_mass_kg.ravel()[positive]
                / fields.covered_area_m2.ravel()[positive],
                18.0,
            ),
        ):
            if not np.allclose(array, expected, rtol=1.0e-6, atol=1.0e-6):
                raise ValueError("Thermal properties differ from the reported equivalent assembly")
        represented = {item for ids in layout.owners for item in ids}
        if represented != set(by_id):
            raise ValueError("Unrepresented roof labels")
        with (target / f"{name}_屋面元胞.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "基准节点序号",
                    "父网格编号",
                    "物理斑块标识",
                    "构造类型",
                    "热模型材料代码",
                    "物理斑块面积_m2",
                ]
            )
            for i in np.flatnonzero(roof):
                writer.writerow(
                    [
                        i,
                        layout.cell_id[i],
                        layout.component_id[i],
                        kinds[i],
                        heatcode[i],
                        format(layout.geometry[i].area, ".15g"),
                    ]
                )
        for kind in (ordinary, tower, bridge):
            indices = np.flatnonzero(kinds == kind)
            totals.append(
                [
                    name,
                    kind,
                    sum(k == kind for k in by_id.values()),
                    len(indices),
                    sum(float(layout.geometry[i].area) for i in indices),
                    5,
                    basis(village, kind),
                ]
            )
        if verify_refinement:
            examples = list(
                {
                    next(b for b, c in by_id.items() if c == kind)
                    for kind in (ordinary, tower, bridge)
                }
            )
            refined = refine_roof_layout(
                layout,
                inventory,
                cell_size_m=1.0,
                refined_ids=examples,
                footprints=(),
                max_nodes=650000,
            )
            refined_kinds, _ = assign_roof_types(refined, inventory, village)
            by_component = {layout.component_id[i]: kinds[i] for i in np.flatnonzero(roof)}
            measured_area = {c: 0.0 for c in (ordinary, tower, bridge)}
            for i in np.flatnonzero(refined_kinds != ""):
                parent = refined.component_id[i].split("/local", 1)[0]
                if refined_kinds[i] != by_component[parent]:
                    raise ValueError("Refinement changes a roof type")
                measured_area[refined_kinds[i]] += refined.geometry[i].area
            for village_row in [r for r in totals if r[0] == name]:
                if not np.isclose(
                    measured_area[village_row[1]], village_row[4], rtol=1e-10, atol=1e-6
                ):
                    raise ValueError("Refinement changes the area of a roof type")
        print(
            name, "roof components", np.count_nonzero(roof), "roof labels", len(by_id), flush=True
        )
    with (target / "各村赋值统计.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "村落",
                "构造类型",
                "屋面标签数",
                "基准屋面元胞数",
                "物理面积_m2",
                "热模型材料代码",
                "依据",
            ]
        )
        writer.writerows(totals)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-refinement", action="store_true")
    args = parser.parse_args()
    export(args.project.resolve(), args.output.resolve(), args.verify_refinement)
