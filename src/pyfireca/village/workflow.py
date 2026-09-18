"""File-based prepare, validate, and run workflow for village simulations."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from pyfireca import __version__
from pyfireca.village.config import VillageScenarioConfig, load_village_scenario
from pyfireca.village.exposure import build_exposure_edges, estimate_exposure_resources
from pyfireca.village.outputs import write_village_viewer
from pyfireca.village.prepare import load_prepared_village, prepare_village_case
from pyfireca.village.solver import (
    VillageSimulationRequest,
    VillageSimulationResult,
    run_village_simulation,
)


@dataclass(frozen=True, slots=True)
class VillageRunArtifacts:
    directory: Path
    outputs: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_village_run(
    prepared_dir: str | Path, scenario_path: str | Path
) -> tuple[VillageSimulationRequest, VillageScenarioConfig]:
    """Validate a prepared case and assemble scenario-specific exposure edges."""

    prepared = Path(prepared_dir)
    report = json.loads((prepared / "quality_report.json").read_text(encoding="utf-8"))
    if not report.get("valid", False):
        raise ValueError(f"prepared case has blocking quality errors: {report.get('errors', [])}")
    domain = load_prepared_village(prepared)
    scenario = load_village_scenario(scenario_path)
    edges = build_exposure_edges(
        domain,
        exposure_radius_m=scenario.exposure_radius_m,
        decay_length_m=scenario.decay_length_m,
        wind_speed_m_s=scenario.wind_speed_m_s,
        wind_from_deg=scenario.wind_from_deg,
        wind_beta_s_m=scenario.wind_beta_s_m,
    )
    request = VillageSimulationRequest(
        domain=domain,
        scenario=scenario.scenario,
        exposure_edges=edges,
        sample_interval_s=scenario.sample_interval_s,
    )
    return request, scenario


def _write_outputs(
    result: VillageSimulationResult,
    request: VillageSimulationRequest,
    prepared_dir: Path,
    directory: Path,
) -> None:
    outputs = directory / "outputs"
    outputs.mkdir()
    final = result.final_state
    np.save(outputs / "state_final.npy", final.state, allow_pickle=False)
    np.save(outputs / "ignition_time_s.npy", final.ignition_time_s, allow_pickle=False)
    np.save(outputs / "burnout_time_s.npy", final.burnout_time_s, allow_pickle=False)
    np.save(outputs / "fuel_remaining_kg.npy", final.fuel_remaining_kg, allow_pickle=False)
    np.save(outputs / "ignition_cause.npy", result.ignition_cause, allow_pickle=False)
    if result.snapshots:
        np.save(
            outputs / "sample_times_s.npy",
            np.array([snapshot.time_s for snapshot in result.snapshots]),
            allow_pickle=False,
        )
        np.save(
            outputs / "state_samples.npy",
            np.stack([snapshot.state for snapshot in result.snapshots]),
            allow_pickle=False,
        )
        initial = result.initial_fuel_kg
        ratios = [
            np.divide(
                snapshot.fuel_remaining_kg,
                initial,
                out=np.zeros_like(initial),
                where=initial > 0.0,
            )
            for snapshot in result.snapshots
        ]
        np.save(outputs / "fuel_fraction_samples.npy", np.stack(ratios), allow_pickle=False)
    with (outputs / "cell_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "row",
                "col",
                "material_index",
                "final_state",
                "ignition_time_s",
                "burnout_time_s",
                "fuel_initial_kg",
                "fuel_remaining_kg",
            ]
        )
        for row in range(request.domain.grid.height):
            for col in range(request.domain.grid.width):
                ignition = final.ignition_time_s[row, col]
                burnout = final.burnout_time_s[row, col]
                writer.writerow(
                    [
                        row,
                        col,
                        int(request.domain.material_index[row, col]),
                        int(final.state[row, col]),
                        "" if not np.isfinite(ignition) else float(ignition),
                        "" if not np.isfinite(burnout) else float(burnout),
                        float(result.initial_fuel_kg[row, col]),
                        float(final.fuel_remaining_kg[row, col]),
                    ]
                )

    overlap_rows: list[dict[str, str]] = []
    with (prepared_dir / "building_overlap.csv").open(encoding="utf-8", newline="") as stream:
        overlap_rows = list(csv.DictReader(stream))
    building_ids = sorted({row["building_id"] for row in overlap_rows})
    with (outputs / "buildings.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "building_id",
                "mapped_area_m2",
                "first_ignition_s",
                "affected_area_m2",
                "affected_fraction",
                "initial_fuel_kg",
                "consumed_fuel_kg",
                "consumed_fraction",
            ]
        )
        consumed = result.initial_fuel_kg - final.fuel_remaining_kg
        for building_id in building_ids:
            rows = [row for row in overlap_rows if row["building_id"] == building_id]
            mapped_area = sum(float(row["overlap_area_m2"]) for row in rows)
            ignitions: list[float] = []
            affected_area = 0.0
            initial_fuel = 0.0
            consumed_fuel = 0.0
            for row in rows:
                cell_row, cell_col = int(row["row"]), int(row["col"])
                area = float(row["overlap_area_m2"])
                share = area / request.domain.grid.cell_area_m2
                ignition = final.ignition_time_s[cell_row, cell_col]
                if np.isfinite(ignition):
                    ignitions.append(float(ignition))
                    affected_area += area
                initial_fuel += float(result.initial_fuel_kg[cell_row, cell_col]) * share
                consumed_fuel += float(consumed[cell_row, cell_col]) * share
            writer.writerow(
                [
                    building_id,
                    mapped_area,
                    min(ignitions) if ignitions else "",
                    affected_area,
                    affected_area / mapped_area if mapped_area else "",
                    initial_fuel,
                    consumed_fuel,
                    consumed_fuel / initial_fuel if initial_fuel else "",
                ]
            )


def run_village_config(
    prepared_dir: str | Path,
    scenario_path: str | Path,
    output_dir: str | Path,
) -> tuple[VillageSimulationResult, VillageRunArtifacts]:
    """Validate, execute, and write one reproducible village run."""

    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    request, scenario = validate_village_run(prepared_dir, scenario_path)
    resource_estimate = estimate_exposure_resources(
        request.domain, exposure_radius_m=scenario.exposure_radius_m
    )
    output.mkdir(parents=True)
    run_record = {
        "schema_version": "village-run-1",
        "status": "running",
        "scenario_id": scenario.scenario_id,
        "pyfireca_version": __version__,
    }
    (output / "run.json").write_text(json.dumps(run_record, indent=2), encoding="utf-8")
    try:
        shutil.copy2(scenario.path, output / "config.resolved.yml")
        result = run_village_simulation(request)
        _write_outputs(result, request, Path(prepared_dir), output)
        write_village_viewer(result, request, prepared_dir, output)
        run_record.update(
            {
                "status": "completed",
                "stop_reason": result.stop_reason,
                "main_loop_stop_time_s": result.main_loop_stop_time_s,
                "end_time_s": result.final_state.time_s,
                "consumed_fuel_kg": result.consumed_fuel_kg,
                "ignited_cell_count": int(
                    np.count_nonzero(np.isfinite(result.final_state.ignition_time_s))
                ),
                "scenario_sha256": _sha256(scenario.path),
                "edge_count": len(request.exposure_edges.source),
                "resource_estimate": resource_estimate.as_dict(),
            }
        )
        (output / "run.json").write_text(
            json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metadata = {
            "prepared_directory": str(Path(prepared_dir).resolve()),
            "scenario": yaml.safe_load(scenario.path.read_text(encoding="utf-8")),
            "grid": {
                "height": request.domain.grid.height,
                "width": request.domain.grid.width,
                "cell_size_m": request.domain.grid.cell_size_m,
            },
            "resource_estimate": resource_estimate.as_dict(),
        }
        (output / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        run_record.update({"status": "failed", "error": str(exc)})
        (output / "run.json").write_text(
            json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    return result, VillageRunArtifacts(directory=output, outputs=output / "outputs")


__all__ = [
    "VillageRunArtifacts",
    "prepare_village_case",
    "run_village_config",
    "validate_village_run",
]
