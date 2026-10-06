"""Replay basic CA from each fuel class in an existing village image crop."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.workflow_v3 import (
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)


def run_core_map(
    source: Path, output: Path, end_time_s: float = 1200, contact_config: Path | None = None
):
    """Keep source physics and geography; select the central cell of each class."""
    if output.exists():
        raise FileExistsError(output)
    bundle = load_v3_case_bundle(source)
    request = bundle.request
    if contact_config is not None:
        request = replace(request, flame_contact=load_flame_contact_parameters(contact_config))
    records = []
    for name, material in (("grass", 1), ("shrub", 2), ("tree", 4), ("building", 5)):
        cells = np.argwhere(
            request.domain_mask & request.burnable_mask & (request.material_index == material)
        )
        if not len(cells):
            continue
        center = (np.array(request.grid.shape) - 1) / 2
        row, col = map(int, cells[np.argmin(np.sum((cells - center) ** 2, axis=1))])
        selected = replace(
            request,
            scenario=replace(
                request.scenario,
                end_time_s=end_time_s,
                ignitions=(VillageIgnitionEvent(0, row, col),),
            ),
            sample_interval_s=10,
        )
        # Recompute inherited full-map counts and replace its original ignition.
        metadata = dict(bundle.metadata)
        inherited_transport = {
            key: metadata.pop(key)
            for key in ("resource_limits", "v04_reference_edge_count", "rebuilt_edge_count")
            if key in metadata
        }
        metadata.update(
            scenario_id=f"basic-map-{name}",
            simulation_scope="village_crop_basic_ca",
            classified_burnable_cell_count=int(np.count_nonzero(request.burnable_mask)),
            unknown_domain_cell_count=int(
                np.count_nonzero(request.domain_mask & ~request.known_mask)
            ),
            outside_domain_cell_count=int(np.count_nonzero(~request.domain_mask)),
            hypothetically_filled_unknown_cell_count=int(
                np.count_nonzero(request.domain_mask & ~request.known_mask)
            ),
            building_label_count=len(
                np.unique(request.building_index[request.building_index >= 0])
            ),
            ignition_source={"row": row, "col": col, "material_index": material},
            rebuilt_edge_count=len(request.exposure_edges.source),
            parent_transport_metadata=inherited_transport,
            core_demo_source=str(source.resolve()),
            contact_config_source=str(contact_config.resolve()) if contact_config else None,
            core_demo_scope=(
                "Image-derived material hypotheses and synthetic combustion presets; "
                "not field calibration."
            ),
        )
        case = write_v3_case_bundle(
            selected,
            output / name / "case",
            metadata=metadata,
            material_labels=bundle.material_labels,
            landcover_labels=bundle.landcover_labels,
        )
        result, _ = run_v3_case_bundle(case, output / name / "run")
        final = result.final_state
        ignited = np.isfinite(final.ignition_time_s)
        record = dict(
            name=name,
            ignition={"row": row, "col": col},
            end_time_s=end_time_s,
            ignited_cells=int(np.count_nonzero(ignited)),
            burned_out_cells=int(np.count_nonzero(final.state == 4)),
            ignited_by_material={
                str(i): int(np.count_nonzero(ignited & (request.material_index == i)))
                for i in np.unique(request.material_index)
            },
            released_J=result.energy_budget.released_J,
            remaining_fuel_kg=float(final.fuel_remaining_kg.sum()),
        )
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    if not records:
        raise ValueError("source contains none of the supported combustible classes")
    (output / "summary.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--end-time-s", default=1200, type=float)
    parser.add_argument("--flame-contact-config", type=Path)
    args = parser.parse_args()
    run_core_map(args.source, args.output, args.end_time_s, args.flame_contact_config)
