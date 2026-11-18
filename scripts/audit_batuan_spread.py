"""Audit the reported Batuan non-spreading run with isolated mechanism ablations."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import (
    V3RadiativeTransportParameters,
    build_v3_radiative_transport_edges,
)
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs/batuan-inferred-20261016/interactive/92f313d1937f497d8ab023c46af97a8e"
OUTPUT = ROOT / "runs/batuan-spread-audit-20261016"


def rebuild(request, parameters, flat=False):
    ground = np.zeros(request.grid.shape) if flat else request.ground_elevation_m
    p = request.parameters
    edges = build_v3_radiative_transport_edges(
        request.grid,
        request.source_mask,
        request.target_mask,
        p.receiver_area_m2,
        p.source_height_m,
        p.receiver_height_m,
        np.ones(request.grid.shape),
        parameters,
        ground_elevation_m=ground,
    )
    return replace(request, exposure_edges=edges, ground_elevation_m=ground)


def dry(request):
    fields = {
        name: getattr(request.parameters, name).copy()
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    for name in [
        "live_water_mass_kg",
        "dead_water_mass_kg",
        "water_mass_kg",
        "moisture_content_dry_basis",
    ]:
        fields[name][:] = 0
    return replace(request, parameters=V3CellPhysicsArrays(**fields))


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    full = load_v3_case_bundle(SOURCE / "case")
    event = full.request.scenario.ignitions[0]
    cropped = OUTPUT / "crop"
    if not cropped.exists():
        crop_v3_case_bundle(
            SOURCE / "case",
            cropped,
            row_start=event.row - 10,
            row_stop=event.row + 11,
            col_start=event.col - 10,
            col_stop=event.col + 11,
            end_time_s=1800,
            sample_interval_s=10,
        )
    base = load_v3_case_bundle(cropped).request
    parameters = V3RadiativeTransportParameters(**full.metadata["transport_parameters"])
    corrected = rebuild(base, parameters)
    # 5 m contact is an explicit coarse-grid diagnostic, not an assertion of validation.
    contact = FlameContactParameters(
        1100, 50, 0.5, 5, "synthetic", "diagnostic_only_coarse_5m_contact"
    )
    cases = [
        ("original", base),
        ("height_fixed", corrected),
        ("original_plus_contact", replace(base, flame_contact=contact)),
        ("height_fixed_plus_contact", replace(corrected, flame_contact=contact)),
        ("height_fixed_dry", dry(corrected)),
        ("height_fixed_flat", rebuild(base, parameters, flat=True)),
        (
            "height_fixed_contact_flat",
            replace(rebuild(base, parameters, flat=True), flame_contact=contact),
        ),
    ]
    records = []
    for name, request in cases:
        result = run_v3_simulation(request)
        row, col = request.scenario.ignitions[0].row, request.scenario.ignitions[0].col
        sid = row * request.grid.width + col
        out = request.exposure_edges.source == sid
        power = (
            request.parameters.peak_hrr_W[row, col]
            * request.parameters.radiative_fraction[row, col]
        )
        target = request.exposure_edges.target[out]
        peak_flux = (
            power
            * request.exposure_edges.weight[out]
            / request.parameters.covered_area_m2.ravel()[target]
        )
        maxT = np.max(np.stack([s.surface_temperature_K for s in result.snapshots]), axis=0)
        others = request.burnable_mask.copy()
        others[row, col] = False
        record = dict(
            name=name,
            **check_final(request, result),
            source_peak_neighbour_flux_kW_m2=float(peak_flux.max() / 1000),
            peak_neighbour_temperature_K=float(maxT[others].max()),
            maximum_outgoing_radiation_fraction=float(
                np.bincount(
                    request.exposure_edges.source,
                    weights=request.exposure_edges.weight,
                    minlength=request.grid.height * request.grid.width,
                ).max()
            ),
            burning_reached_crop_edge=bool(
                np.isfinite(result.final_state.ignition_time_s)[[0, -1], :].any()
                or np.isfinite(result.final_state.ignition_time_s)[:, [0, -1]].any()
            ),
        )
        records.append(record)
        print(
            json.dumps(
                {
                    k: record[k]
                    for k in [
                        "name",
                        "ignited_cells",
                        "burned_out_cells",
                        "peak_neighbour_temperature_K",
                        "source_peak_neighbour_flux_kW_m2",
                        "burning_reached_crop_edge",
                    ]
                }
            ),
            flush=True,
        )
        (OUTPUT / "ablations.json").write_text(
            json.dumps(
                dict(
                    source=str(SOURCE),
                    crop_shape=list(base.grid.shape),
                    end_time_s=1800,
                    notes=(
                        "Contact at 5m is diagnostic only; reached-edge counts are crop-limited; "
                        "dry is sensitivity only"
                    ),
                    cases=records,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
