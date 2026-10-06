"""W02-a flame-axis diagnostic; deliberately does not alter heat transport.

AGA is a pool/crib fire correlation, not a calibrated vegetation model. Axis
lengths remain prescribed. Footprints are explicit hypotheses, never inferred
from the active heat-transfer area or from RGB moisture defaults.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.wind_v3 import (
    PrescribedWind,
    _number,
    _object,
    _quantity,
    _UniqueKeyLoader,
    load_wind,
)

SCHEMA = "village-flame-axis-diagnostic-1"


def _array(value, name, *, shape=None, minimum=None, positive=False, nan=False):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf":
        raise ValueError(f"{name} must be a numeric array")
    array = raw.astype(np.float64, copy=True)
    if shape is not None and array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    invalid = np.isinf(array) if nan else ~np.isfinite(array)
    if np.any(invalid) or (minimum is not None and np.any(array < minimum)):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    if positive and np.any(array <= 0):
        raise ValueError(f"{name} must be positive")
    return array


def instantaneous_prescribed_hrr(
    time_s, ignition_time_s, burnout_time_s, peak_hrr_W, growth_s, steady_s, decay_s
):
    """Instantaneous quadratic/plateau/linear HRR, not the sampled step mean.

    Unknown ignition/burnout is NaN. A finite recorded burnout truncates the
    curve; future ignition, age zero and the end of the curve have zero HRR.
    """
    time = _number(time_s, "time_s", minimum=0)
    peak = _array(peak_hrr_W, "peak_hrr_W", minimum=0)
    if peak.ndim != 1:
        raise ValueError("HRR inputs must be one-dimensional")
    shape = peak.shape
    ignition = _array(ignition_time_s, "ignition_time_s", shape=shape, minimum=0, nan=True)
    burnout = _array(burnout_time_s, "burnout_time_s", shape=shape, minimum=0, nan=True)
    growth = _array(growth_s, "growth_s", shape=shape, minimum=0)
    steady = _array(steady_s, "steady_s", shape=shape, minimum=0)
    decay = _array(decay_s, "decay_s", shape=shape, minimum=0)
    if np.any((peak > 0) & ((growth <= 0) | (decay <= 0))):
        raise ValueError("positive HRR needs positive growth and decay durations")
    if np.any(np.isfinite(burnout) & (~np.isfinite(ignition) | (burnout < ignition))):
        raise ValueError("recorded burnout needs an earlier or equal ignition")
    with np.errstate(over="ignore", invalid="ignore"):
        duration = growth + steady + decay
    if np.any(~np.isfinite(duration)):
        raise ValueError("curve duration overflows")
    age = time - ignition
    active = (peak > 0) & (age > 0) & (age < duration)
    active &= ~np.isfinite(burnout) | (time < burnout)
    result = np.zeros(shape)
    rise = active & (age < growth)
    plateau = active & (age >= growth) & (age < growth + steady)
    fall = active & ~(rise | plateau)
    result[rise] = peak[rise] * (age[rise] / growth[rise]) ** 2
    result[plateau] = peak[plateau]
    result[fall] = peak[fall] * (1 - (age[fall] - growth[fall] - steady[fall]) / decay[fall])
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class FlameAxisFrame:
    active: np.ndarray
    mass_flux_kg_m2_s: np.ndarray
    dimensionless_wind: np.ndarray
    tilt_from_vertical_rad: np.ndarray
    axis_unit: np.ndarray
    centroid_xyz_m: np.ndarray
    tip_xyz_m: np.ndarray


def evaluate_flame_axes(
    *,
    time_s,
    wind: PrescribedWind,
    wind_reference_height_m,
    wind_speed_factor,
    air_density_kg_m3,
    gravity_m_s2,
    mass_rate_kg_s,
    footprint_area_m2,
    characteristic_diameter_m,
    axis_length_m,
    base_xyz_m,
) -> FlameAxisFrame:
    """AGA axis, world east/north/up. Inactive axes/positions are NaN.

    U*=U/(g*m''*D/rho)^(1/3), cos(theta)=1 for U*<=1,
    else U*^(-1/2). Query declared reference height, then apply the explicitly
    supplied speed factor. This contains no terrain wind-field correction.
    """
    if not isinstance(wind, PrescribedWind):
        raise TypeError("W02-a requires a validated prescribed wind")
    height = _number(wind_reference_height_m, "wind_reference_height_m", minimum=0)
    factor = _number(wind_speed_factor, "wind_speed_factor", minimum=0)
    rho = _number(air_density_kg_m3, "air_density_kg_m3", minimum=0)
    gravity = _number(gravity_m_s2, "gravity_m_s2", minimum=0)
    if min(height, factor, rho, gravity) <= 0:
        raise ValueError("height, factor, density and gravity must be positive")
    if wind.reference_height_m != height:
        raise ValueError("wind reference height does not match flame configuration")
    velocity = wind.velocity_at(time_s, z_m=height)
    with np.errstate(over="ignore", invalid="ignore"):
        uv = np.asarray(velocity[:2]) * factor
    speed = np.hypot(*uv)
    if not np.isfinite(speed):
        raise ValueError("effective wind speed overflows")
    rate = _array(mass_rate_kg_s, "mass_rate_kg_s", minimum=0)
    if rate.ndim != 1:
        raise ValueError("mass rate must be one-dimensional")
    shape = rate.shape
    area = _array(footprint_area_m2, "footprint_area_m2", shape=shape, positive=True)
    diameter = _array(
        characteristic_diameter_m, "characteristic_diameter_m", shape=shape, positive=True
    )
    length = _array(axis_length_m, "axis_length_m", shape=shape, minimum=0)
    base = _array(base_xyz_m, "base_xyz_m", shape=(rate.size, 3))
    active = rate > 0
    if np.any(active & (length <= 0)):
        raise ValueError("active flame requires positive prescribed axis length")
    flux = np.zeros(shape)
    ustar = np.zeros(shape)
    angle = np.full(shape, np.nan)
    axis = np.full((rate.size, 3), np.nan)
    centroid, tip = axis.copy(), axis.copy()
    # Log arithmetic avoids underflow/overflow in the intermediate product.
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        flux[active] = rate[active] / area[active]
        if speed > 0:
            log_u = (
                np.log(speed)
                - (
                    np.log(gravity)
                    + np.log(rate[active])
                    - np.log(area[active])
                    + np.log(diameter[active])
                    - np.log(rho)
                )
                / 3
            )
            ustar[active] = np.exp(log_u)
        cosine = np.ones(np.count_nonzero(active))
        bent = ustar[active] > 1
        cosine[bent] = ustar[active][bent] ** -0.5
        angle[active] = np.arccos(cosine)
        axis[active, 2] = cosine
        axis[active, :2] = 0 if speed == 0 else np.sin(angle[active])[:, None] * (uv / speed)
        centroid[active] = base[active] + 0.5 * length[active, None] * axis[active]
        tip[active] = base[active] + length[active, None] * axis[active]
    if (
        np.any(~np.isfinite(flux))
        or np.any(active & (flux <= 0))
        or np.any(~np.isfinite(ustar))
        or any(np.any(~np.isfinite(a[active])) for a in (angle, axis, centroid, tip))
    ):
        raise ValueError("flame geometry exceeds numerical range")
    for array in (active, flux, ustar, angle, axis, centroid, tip):
        array.setflags(write=False)
    return FlameAxisFrame(active, flux, ustar, angle, axis, centroid, tip)


def inspect_component_flames(
    run_root,
    wind_path,
    geometry_path,
    output_dir,
    *,
    end_time_s=None,
    sample_interval_s=10,
    max_records=200_000,
):
    """Reconstruct saved prescribed curves without rerunning/changing the solver."""
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    root, output = Path(run_root).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema_version") != "village-components-run-1":
        raise ValueError("diagnostic requires a saved component run")
    if type(max_records) is not int or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    interval = _number(sample_interval_s, "sample_interval_s", minimum=0)
    if interval == 0:
        raise ValueError("sample_interval_s must be positive")
    bundle = load_v3_case_bundle(root / "base-case")
    request = bundle.request
    end = (
        request.scenario.end_time_s
        if end_time_s is None
        else _number(end_time_s, "end_time_s", minimum=0)
    )
    if not 0 < end <= request.scenario.end_time_s:
        raise ValueError("diagnostic end must be within the saved simulation window")
    if manifest["physical_grid"] != asdict(request.grid):
        raise ValueError("saved physical grid must match base-case")
    frame_count = (
        int(np.ceil(end / interval)) + 1 if np.isfinite(end / interval) else max_records + 1
    )
    if (
        not 0 < manifest["node_count"] <= max_records
        or frame_count * manifest["node_count"] > max_records
    ):
        raise ValueError("RESOURCE_LIMIT: flame diagnostic exceeds max_records")
    wind = load_wind(wind_path, end_time_s=end)
    config = yaml.load(Path(geometry_path).read_text(), Loader=_UniqueKeyLoader)
    keys = {
        "schema_version",
        "air_density_kg_m3",
        "gravity_m_s2",
        "wind_reference_height_m",
        "wind_speed_factor",
        "footprints",
    }
    _object(config, keys, "flame geometry")
    if config["schema_version"] != SCHEMA:
        raise ValueError("unsupported flame geometry schema")
    common = {
        key: _quantity(config[key], unit, key)
        for key, unit in (
            ("air_density_kg_m3", "kg/m3"),
            ("gravity_m_s2", "m/s2"),
            ("wind_reference_height_m", "m"),
            ("wind_speed_factor", "1"),
        )
    }
    footprints = config["footprints"]
    if not isinstance(footprints, dict) or not footprints:
        raise ValueError("footprints must be a nonempty component mapping")
    sizes = {}
    for name, values in footprints.items():
        _object(values, {"area_m2", "diameter_m"}, f"footprint {name}")
        sizes[name] = (
            _quantity(values["area_m2"], "m2", name),
            _quantity(values["diameter_m"], "m", name),
        )
        if min(sizes[name]) <= 0:
            raise ValueError("footprint dimensions must be positive")
    with (root / "components.csv").open(newline="") as stream:
        rows = []
        for row in csv.DictReader(stream):
            if len(rows) >= manifest["node_count"]:
                raise ValueError("components table exceeds declared node count")
            rows.append(row)
    if len(rows) != manifest["node_count"]:
        raise ValueError("components table must match declared node count")

    columns = {}

    def column(name):
        if name not in columns:
            columns[name] = np.asarray([float(row[name]) if row[name] else np.nan for row in rows])
        return columns[name]

    cells, node_ids = column("cell_id"), column("node_id")
    count = request.grid.height * request.grid.width
    if (
        not np.array_equal(node_ids, np.arange(len(rows)))
        or np.any(~np.isfinite(cells))
        or np.any((cells < 0) | (cells >= count) | (cells != np.floor(cells)))
    ):
        raise ValueError("components table has invalid node or cell indices")
    cells = cells.astype(int)
    if len(
        {(int(cell), row["component_id"]) for cell, row in zip(cells, rows, strict=True)}
    ) != len(rows):
        raise ValueError("components table has duplicate cell/component pairs")
    mass = _array(column("initial_pyrolysable_fuel_kg"), "initial fuel", minimum=0)
    energy = _array(column("available_energy_J"), "available energy", minimum=0)
    configured_energy = _array(column("configured_curve_energy_J"), "curve energy", minimum=0)
    with np.errstate(over="ignore", invalid="ignore"):
        integrated_energy = column("peak_hrr_W") * (
            column("growth_time_s") / 3 + column("steady_time_s") + column("decay_time_s") / 2
        )
    if (
        np.any(~np.isfinite(integrated_energy))
        or not np.allclose(integrated_energy, configured_energy, rtol=1e-9, atol=1e-6)
        or np.any(configured_energy > energy + 1e-6 + 1e-9 * energy)
    ):
        raise ValueError("saved prescribed curve must close against its energy budget")
    heat = np.ones(len(rows))
    burning = mass > 0
    heat[burning] = energy[burning] / mass[burning]
    if np.any(burning & ((heat <= 0) | ~np.isfinite(heat))):
        raise ValueError("positive fuel requires finite positive heat of combustion")
    area, diameter = np.ones(len(rows)), np.ones(len(rows))
    for index in np.flatnonzero(burning):
        name = rows[index]["component_id"]
        if name not in sizes:
            raise ValueError(f"missing explicit footprint for {name}")
        area[index], diameter[index] = sizes[name]
    base = np.empty((len(rows), 3))
    cell_rows, cell_cols = np.divmod(cells, request.grid.width)
    if np.any(column("row") != cell_rows) or np.any(column("col") != cell_cols):
        raise ValueError("components table row/col must match cell_id")
    base[:, 0] = request.grid.x_min_m + (cell_cols + 0.5) * request.grid.cell_size_m
    base[:, 1] = request.grid.y_max_m - (cell_rows + 0.5) * request.grid.cell_size_m
    base[:, 2] = _array(column("source_base_height_m"), "source base", minimum=0)
    if request.ground_elevation_m is not None:
        base[:, 2] += request.ground_elevation_m.ravel()[cells]
    records = []
    times = np.append(np.arange(frame_count - 1) * interval, end)
    for time in times:
        hrr = instantaneous_prescribed_hrr(
            time,
            column("ignition_time_s"),
            column("burnout_time_s"),
            column("peak_hrr_W"),
            column("growth_time_s"),
            column("steady_time_s"),
            column("decay_time_s"),
        )
        if np.any((~burning) & (hrr > 0)):
            raise ValueError("zero initial fuel cannot have positive HRR")
        frame = evaluate_flame_axes(
            time_s=float(time),
            wind=wind,
            **common,
            mass_rate_kg_s=hrr / heat,
            footprint_area_m2=area,
            characteristic_diameter_m=diameter,
            axis_length_m=column("flame_extent_m"),
            base_xyz_m=base,
        )
        for node in range(len(rows)):
            active = bool(frame.active[node])
            records.append(
                {
                    "time_s": float(time),
                    "node_id": node,
                    "cell_id": int(cells[node]),
                    "component_id": rows[node]["component_id"],
                    "active": active,
                    "instantaneous_prescribed_hrr_W": float(hrr[node]),
                    "mass_rate_kg_s": float(hrr[node] / heat[node]),
                    "mass_flux_kg_m2_s": float(frame.mass_flux_kg_m2_s[node]),
                    "dimensionless_wind": float(frame.dimensionless_wind[node]) if active else None,
                    "tilt_from_vertical_deg": float(np.degrees(frame.tilt_from_vertical_rad[node]))
                    if active
                    else None,
                    **{
                        f"{point}_{axis}_m": float(array[node, i]) if active else None
                        for point, array in (
                            ("centroid", frame.centroid_xyz_m),
                            ("tip", frame.tip_xyz_m),
                        )
                        for i, axis in enumerate("xyz")
                    },
                }
            )
    inputs = [
        root / "manifest.json",
        root / "components.csv",
        Path(wind_path).resolve(),
        Path(geometry_path).resolve(),
        *sorted((root / "base-case").glob("*")),
    ]
    hashes = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in inputs
        if path.is_file()
    }
    source_root = Path(__file__).resolve().parents[1]
    source_bytes = {
        str(path.relative_to(source_root)): path.read_bytes()
        for path in sorted(source_root.rglob("*.py"))
    }
    report = {
        "schema_version": SCHEMA,
        "thermal_solver_executed": False,
        "wind_coupled_heat_transport": False,
        "axis_length_model": "prescribed_saved_length_not_HRR_prediction",
        "hrr_semantics": "instantaneous_prescribed_curve_truncated_at_recorded_burnout",
        "ground_mode": "saved_dem"
        if request.ground_elevation_m is not None
        else "explicit_case_flat_ground",
        "correlation_scope": (
            "AGA pool/crib fire proxy; not validated for grass line fire, crowns or structures"
        ),
        "wind": wind.to_dict(),
        "geometry": config,
        "input_sha256": hashes,
        "implementation_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()
        },
        "numpy_version": np.__version__,
        "time_s": times.tolist(),
        "node_count": len(rows),
        "record_count": len(records),
        "active_records": sum(record["active"] for record in records),
        "limitations": [
            "No feedback to ignition, HRR, moisture, spread or source/receiver edges",
            "No slope-following wind, flame-length prediction, canopy flow or firebrands",
            "Footprints apply by component id across materials; whole shares a synthetic footprint",
        ],
    }
    output.mkdir(parents=True)
    for name, data in source_bytes.items():
        snapshot = output / "source_snapshot" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
    (output / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    with (output / "flame_axes.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return report
