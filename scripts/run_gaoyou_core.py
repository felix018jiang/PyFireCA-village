"""Prepare and simulate the full Gaoyou village with the basic CA model."""

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

ROOT = Path(__file__).resolve().parents[1]


def prepare_and_run(source, output, *, end_time_s=1200, row=190, col=391, contact_config=None):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    bundle = load_v3_case_bundle(source)
    if not bundle.request.burnable_mask[row, col]:
        raise ValueError("selected ignition cell must be combustible")
    request = replace(
        bundle.request,
        scenario=replace(
            bundle.request.scenario,
            end_time_s=end_time_s,
            ignitions=(VillageIgnitionEvent(0, row, col),),
        ),
        sample_interval_s=max(1, float(np.ceil(end_time_s / 100))),
        flame_contact=load_flame_contact_parameters(
            contact_config
            or ROOT / "examples/village/vegetation_v3/flame-contact-low-building.synthetic.yml"
        ),
    )
    metadata = dict(bundle.metadata)
    metadata.update(
        scenario_id="gaoyou-core-full-village",
        place_name="高友村",
        simulation_scope="full_village",
        parent_case=str(Path(source).resolve()),
        ignition_source={"row": row, "col": col, "cell_id": row * request.grid.width + col},
        material_survey_status="pending_field_survey; demonstration hypotheses retained",
        assumptions=True,
    )
    case = write_v3_case_bundle(
        request,
        output / "case",
        metadata=metadata,
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
    )

    def progress(time_s, end_s):
        print(f"模拟进度 {time_s / end_s:.0%} ({time_s:.0f}/{end_s:.0f} s)", flush=True)

    result, run = run_v3_case_bundle(
        case,
        output / "run",
        chunk_frames=4,
        progress_callback=progress,
    )
    record = dict(
        case=str(case),
        run=str(run),
        grid=list(request.grid.shape),
        end_time_s=end_time_s,
        ignited_cells=int(np.isfinite(result.final_state.ignition_time_s).sum()),
        burned_out_cells=int((result.final_state.state == 4).sum()),
        remaining_fuel_kg=float(result.final_state.fuel_remaining_kg.sum()),
    )
    (output / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "runs/gaoyou-vegetation-v3/v12-gaoyou-terrain-coupled-full-domain-case",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--end-time-s", type=float, default=1200)
    parser.add_argument("--row", type=int, default=190)
    parser.add_argument("--col", type=int, default=391)
    parser.add_argument("--flame-contact-config", type=Path)
    args = parser.parse_args()
    prepare_and_run(
        args.source,
        args.output,
        end_time_s=args.end_time_s,
        row=args.row,
        col=args.col,
        contact_config=args.flame_contact_config,
    )
