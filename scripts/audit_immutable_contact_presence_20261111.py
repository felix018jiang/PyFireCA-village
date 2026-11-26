"""Resolve the contact-presence/CFL question without CA or village contact graphs."""

import ast
import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from pyfireca.planning.roof_ignition import refine_roof_layout, refined_contact_operator
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.village.flame_contact_v3 import FlameContactParameters

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = ROOT / "src/pyfireca/planning/roof_ignition.py"
    text = source.read_text()
    node = next(
        n
        for n in ast.parse(text).body
        if isinstance(n, ast.FunctionDef) and n.name == "refined_contact_operator"
    )
    rows = []
    for village in ("batuan", "pingyan"):
        run = ROOT / f"runs/research/heritage-retained-area-{village}-20261110"
        experiment = json.loads((run / "experiment.json").read_text())
        study = Path(experiment["config"]["study_path"])
        metadata = json.loads(study.read_text())
        manifest = (study.parent / metadata["case_dir"]).resolve() / "manifest.json"
        config = json.loads(manifest.read_text())["flame_contact"]
        frozen_source = run / "source-snapshot/src/pyfireca/planning/roof_ignition.py"
        if sha(frozen_source) != sha(source):
            raise ValueError("contact builder source differs from frozen master")
        if config is None:
            raise ValueError("this witness expects a fixed enabled parent contact configuration")
        rows.append(
            {
                "village_id": village,
                "parent_manifest_path": str(manifest),
                "parent_manifest_sha256": sha(manifest),
                "parent_flame_contact": config,
                "parent_flame_contact_sha256": hashlib.sha256(
                    json.dumps(config, sort_keys=True).encode()
                ).hexdigest(),
                "frozen_builder_file_sha256": sha(frozen_source),
            }
        )
    spec = importlib.util.spec_from_file_location(
        "empty_contact_presence_case", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle, _, inventory, _ = fixture.make_case(radius=0.1)
    parent = replace(
        bundle.request,
        flame_contact=FlameContactParameters.from_mapping(rows[0]["parent_flame_contact"]),
    )
    base = resolve_roof_layout(parent, inventory)
    layout = refine_roof_layout(
        base, inventory, cell_size_m=1, refined_ids=tuple(inventory.by_id), footprints=()
    )
    # Function-contract input with an empty candidate topology. No V3 run occurs.
    thermal = SimpleNamespace(
        parameters=layout.node_inputs.parameters,
        burnable_mask=np.zeros_like(layout.node_inputs.burnable_mask),
        material_index=layout.node_inputs.material_index,
    )
    probe = SimpleNamespace(
        physical_request=parent,
        thermal=thermal,
        geometry=layout.geometry,
        cell_id=layout.cell_id,
        support_area_m2=layout.support_area_m2,
    )
    operator = refined_contact_operator(probe)
    assert operator is not None and operator.neighbors.shape[1] == 0
    report = {
        "schema_version": "heritage-fixed-parent-contact-presence-witness-1",
        "witness_type": "fixed_parent_parameter_presence;not_immutable_edge_requirement",
        "rows": rows,
        "parent_contact_configurations_equal": rows[0]["parent_flame_contact"]
        == rows[1]["parent_flame_contact"],
        "builder_function_sha256": hashlib.sha256(
            ast.get_source_segment(text, node).encode()
        ).hexdigest(),
        "builder_file_sha256": sha(source),
        "empty_candidate_graph_result_is_not_none": operator is not None,
        "empty_graph_neighbors_shape": list(operator.neighbors.shape),
        "fixed_CFL_extra_h_W_m2_K": operator.parameters.heat_transfer_coefficient_W_m2_K,
        "reason": (
            "only parent.flame_contact=None returns None;empty edges return width-zero operator"
        ),
        "scope": "required refined_contact_operator;does_not_generalize_to_other_builders",
        "village_contact_graphs_built": 0,
        "new_CA_checks": 0,
        "earlier_contact_presence_risk_retracted_for_this_builder": True,
    }
    output = ROOT / "docs/results/heritage-immutable-contact-witness-20261111.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "new_CA_checks": 0, "empty_graph_is_not_none": True}))


if __name__ == "__main__":
    main()
