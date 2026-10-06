"""W02-d/e synthetic wind/finite-face/time-energy audit; no thermal CA update."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from pyfireca.village.flame_geometry_v3 import evaluate_flame_axes
from pyfireca.village.flame_terrain_v3 import terrain_penetrates_oriented_cylinder
from pyfireca.village.oriented_flame_surfaces_v3 import (
    oriented_segmented_cylinder,
    rotation_from_axis,
)
from pyfireca.village.radiation_clipping_v3 import clipped_view_factor
from pyfireca.village.radiation_time_v3 import integrate_dynamic_radiation
from pyfireca.village.wind_v3 import parse_wind


def quantity(value, unit):
    return dict(value=value, unit=unit, evidence="synthetic", source="synthetic:W02_de_audit")


def audit(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    wind = parse_wind(
        dict(
            mode="timeseries",
            direction_reference="grid_north",
            reference_height_m=quantity(10, "m"),
            evidence="synthetic",
            source="synthetic:turning_wind",
            samples=[
                dict(time_s=0, speed_m_s=2, from_deg=270),
                dict(time_s=5, speed_m_s=3, from_deg=180),
                dict(time_s=10, speed_m_s=2, from_deg=90),
            ],
        ),
        end_time_s=10,
    )
    parameters = dict(
        radius_m=0.2,
        axis_length_m=2,
        initial_base_xyz_m=[0, 0, 1],
        peak_hrr_W=1e6,
        heat_of_combustion_J_kg=18e6,
        radiative_fraction=0.35,
        wind_speed_factor=0.4,
        wind_reference_height_m=10,
        air_density_kg_m3=1.2,
        gravity_m_s2=9.81,
        footprint_area_m2=float(np.pi * 0.2**2),
        evidence="synthetic",
        flame_base_assumption="explicit_elevated_source_for_numerical_test",
    )
    target = (
        np.array([4.0, 0.0, 2.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
        2.0,
        2.0,
    )
    terrain = [(-4.0, 6.0, -4.0, 4.0, 0.0, False)]
    trace = []

    def callback(spatial_order, tilted=True):
        def evaluate(t):
            q = parameters["peak_hrr_W"] * (t / 10) ** 2
            frame = evaluate_flame_axes(
                time_s=t,
                wind=wind,
                wind_reference_height_m=10,
                wind_speed_factor=0.4,
                air_density_kg_m3=1.2,
                gravity_m_s2=9.81,
                mass_rate_kg_s=[q / 18e6],
                footprint_area_m2=[np.pi * 0.2**2],
                characteristic_diameter_m=[0.4],
                axis_length_m=[2],
                base_xyz_m=[[0, 0, 1]],
            )
            axis = frame.axis_unit[0] if tilted else np.array([0.0, 0.0, 1.0])
            if q == 0:
                return [0, 0], [0]
            source = oriented_segmented_cylinder(
                radius_m=0.2,
                axis_length_m=2,
                base_xyz_m=[0, 0, 1],
                rotation=rotation_from_axis(axis),
                target_geometry=target,
                order=spatial_order,
                terrain_cells=terrain,
                max_source_points=100_000,
            )
            fraction, evaluations, _ = clipped_view_factor(
                source, target, [], order=spatial_order, max_triangles=128, target_two_sided=False
            )
            trace.append(
                dict(
                    time_s=t,
                    spatial_order=spatial_order,
                    tilted=tilted,
                    q_W=q,
                    fraction=fraction,
                    kernel_evaluations=evaluations,
                )
            )
            return [q * 0.35, 0], [fraction]

        return evaluate

    results = {}
    for order in (6, 12):
        cases = [("whole", [5]), ("split", None)]
        for name, breaks in cases:
            pieces = [(0, 10, breaks)] if name == "whole" else [(0, 5, []), (5, 10, [])]
            energy, source_energy, unassigned, closure, calls = 0.0, 0.0, 0.0, 0.0, 0
            for low, high, knots in pieces:
                result = integrate_dynamic_radiation(
                    start_s=low,
                    end_s=high,
                    node_count=2,
                    edge_sources=[0],
                    edge_targets=[1],
                    evaluate=callback(order),
                    break_times_s=knots,
                    relative_tolerance=1e-4,
                    absolute_tolerance_J=1e-4,
                    initial_order=3,
                    max_order=24,
                )
                energy += result.edge_energy_J[0]
                source_energy += result.source_energy_J[0]
                unassigned += result.unassigned_source_energy_J[0]
                closure = max(closure, result.closure_error_J)
                calls += result.callback_evaluations
            results[f"{order}_{name}"] = dict(
                edge_energy_J=float(energy),
                source_energy_J=float(source_energy),
                unassigned_energy_J=float(unassigned),
                closure_error_J=float(closure),
                callback_evaluations=calls,
            )
    static = integrate_dynamic_radiation(
        start_s=0,
        end_s=10,
        node_count=2,
        edge_sources=[0],
        edge_targets=[1],
        evaluate=callback(12, tilted=False),
        break_times_s=[5],
        relative_tolerance=1e-4,
        absolute_tolerance_J=1e-4,
    )
    results["12_vertical_control"] = dict(edge_energy_J=float(static.edge_energy_J[0]))
    for order in (6, 12):
        assert (
            abs(
                results[f"{order}_whole"]["edge_energy_J"]
                - results[f"{order}_split"]["edge_energy_J"]
            )
            < 1e-7
        )
        assert abs(results[f"{order}_whole"]["source_energy_J"] - 1e6 * 0.35 * 10 / 3) < 1e-7
    spatial_error = (
        abs(results["6_whole"]["edge_energy_J"] - results["12_whole"]["edge_energy_J"])
        / results["12_whole"]["edge_energy_J"]
    )
    assert spatial_error < 0.01
    ground_rotation = rotation_from_axis([np.sqrt(0.5), 0, np.sqrt(0.5)])
    ground_failure = terrain_penetrates_oriented_cylinder(
        terrain_cell=terrain[0],
        radius_m=0.2,
        axis_length_m=2,
        base_xyz_m=[0, 0, 0],
        rotation=ground_rotation,
    )
    assert ground_failure
    module_root = Path(__file__).resolve().parents[3] / "src/pyfireca"
    files = {
        str(p.relative_to(module_root)): p.read_bytes() for p in sorted(module_root.rglob("*.py"))
    }
    report = dict(
        schema_version="village-dynamic-radiation-audit-1",
        thermal_solver_executed=False,
        ignition_or_thermal_state_updated=False,
        wind=wind.to_dict(),
        parameters=parameters,
        target_geometry=[v.tolist() if isinstance(v, np.ndarray) else v for v in target],
        terrain_cells=terrain,
        results=results,
        spatial_relative_difference=spatial_error,
        ground_based_tilt_rejected=ground_failure,
        limitations=[
            "Elevated flame is an explicit synthetic test, not ground-fire validation",
            "No source-source occlusion, contact heating or ground-clipped flame",
            "Inner spatial convergence checked on integrated edge energy for this case only",
            "Adaptive temporal error is an estimate, not a rigorous bound",
        ],
        source_sha256={name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        audit_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / "source_snapshot" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "manifest.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output / "quadrature_trace.json").write_text(
        json.dumps(trace, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps(results, indent=2))
    print(f"spatial difference={spatial_error:.3g}; ground tilt rejected={ground_failure}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.output)
