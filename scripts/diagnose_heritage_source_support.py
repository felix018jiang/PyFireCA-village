"""Diagnose contact-support dependence without changing thermal parameters."""

import hashlib
import importlib.util
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from shapely.geometry import box

from pyfireca.planning.roof_diagnostics import extract_roof, mechanism_request
from pyfireca.planning.roof_ignition import (
    RoofIgnitionFootprint,
    refine_roof_layout,
    refined_contact_operator,
)
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location(
        "synth", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    geometries = {
        "S": box(4, 6, 8, 10),
        "M": box(20, 2, 22, 4),
        "G": box(20, 6, 22, 8),
        "F": box(20, 10, 22, 12),
        "U": box(20, 14, 22, 16),
    }
    bundle, _, inventory, _ = fixture.make_case(geometries=geometries, radius=15)
    contact_parameters = FlameContactParameters(
        1100,
        50,
        0.5,
        5,
        "synthetic",
        "same as frozen pingyan case; isolated geometry diagnostic",
        terrain_reference="shared_face",
    )
    transport = V3RadiativeTransportParameters(15, 5, 0.001, 0, 0, 0)
    parent = replace(bundle.request, flame_contact=contact_parameters)
    base = resolve_roof_layout(parent, inventory)
    footprint = RoofIgnitionFootprint(
        "S", box(4, 6, 6, 8), "synthetic fixed 2m x 2m corner ignition; no fitted parameters"
    )
    rows = []
    for h in [1.0, 0.5, 0.25]:
        layout = refine_roof_layout(
            base, inventory, cell_size_m=h, refined_ids=("S", "G"), footprints=(footprint,)
        )
        seeds = np.flatnonzero(np.asarray([x is not None for x in layout.ignition_owner]))
        scenario = replace(
            parent.scenario,
            max_dt_s=0.5,
            end_time_s=3600,
            ignitions=tuple(VillageIgnitionEvent(0, 0, int(i)) for i in seeds),
        )
        req = layout.node_inputs.bind(scenario)
        contact = refined_contact_operator(layout)
        source, target = [
            extract_roof(
                req,
                layout.to_mapping()["roof_components"],
                i,
                contact.neighbors,
                contact.geometry_fraction,
                parent_elevation_m=None,
            )
            for i in ("S", "G")
        ]
        for mode in ["source_contact", "source_both"]:
            request, c = mechanism_request(
                source, target, scenario, transport, contact_parameters, mode=mode
            )
            started = time.perf_counter()
            result = _run_resolved_v3_simulation(request, contact=c, retain_snapshots=False)
            check_final(request, result)
            elapsed = time.perf_counter() - started
            ig = result.final_state.ignition_time_s.ravel()[: source.count]
            bo = result.final_state.burnout_time_s.ravel()[: source.count]
            area = source.parameters.covered_area_m2.ravel()
            finite = np.isfinite(ig)
            secondary = finite & ~source.seed
            row = {
                "h_m": h,
                "mode": mode,
                "nodes": source.count,
                "initial_ignited_area_m2": float(area[source.seed].sum()),
                "source_area_m2": float(area.sum()),
                "initial_source_fuel_kg": float(
                    source.parameters.pyrolysable_dry_fuel_mass_kg.sum()
                ),
                "initial_source_energy_J": float(source.parameters.available_energy_J.sum()),
                "source_ignited_area_m2": float(area[finite].sum()),
                "first_secondary_ignition_s": float(ig[secondary].min())
                if secondary.any()
                else None,
                "last_source_ignition_s": float(ig[finite].max()) if finite.any() else None,
                "last_source_burnout_s": float(np.nanmax(bo)) if np.isfinite(bo).any() else None,
                "area_by_time": {
                    str(t): float(area[finite & (ig <= t)].sum())
                    for t in [0, 120, 240, 480, 960, 1920, 3600]
                },
                "elapsed_s": elapsed,
                "energy_closure_errors_J": check_final(request, result)["energy_closure_errors_J"],
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    output = {
        "scope": (
            "synthetic isolated 4m x 4m roof; fixed aligned 2m x 2m corner ignition; "
            "source_contact removes radiation, source_both restores original kernel; "
            "not site calibration or protective design"
        ),
        "dt_s": 0.5,
        "horizon_s": 3600,
        "rows": rows,
        "source_files_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "src/pyfireca/planning/roof_ignition.py",
                ROOT / "src/pyfireca/planning/roof_diagnostics.py",
                ROOT / "src/pyfireca/village/flame_contact_v3.py",
                ROOT / "src/pyfireca/village/solver_v3.py",
            ]
        },
    }
    (ROOT / "docs/results/heritage-source-support-control-20261110.json").write_text(
        json.dumps(output, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
