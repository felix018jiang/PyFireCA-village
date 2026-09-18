"""Strict YAML configuration for village cases and scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver import VillageScenario


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _keys(value: dict[str, Any], required: set[str], name: str) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class VillageCaseConfig:
    path: Path
    case_id: str
    mode: str
    grid: VillageGridSpec
    imagery_path: Path
    image_reference_path: Path | None
    buildings_path: Path
    buildings_format: str
    material_grid_path: Path
    material_format: str
    material_codes_path: Path | None
    material_catalog_path: Path


@dataclass(frozen=True, slots=True)
class VillageScenarioConfig:
    path: Path
    scenario_id: str
    scenario: VillageScenario
    exposure_radius_m: float
    decay_length_m: float
    wind_speed_m_s: float
    wind_from_deg: float
    wind_beta_s_m: float
    sample_interval_s: float


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def load_village_case(path: str | Path) -> VillageCaseConfig:
    """Load the version-1 local-image/grid-CSV case contract."""

    source = Path(path).resolve()
    root = _mapping(_load_yaml(source), "village case")
    _keys(
        root,
        {"schema_version", "case_id", "mode", "imagery", "buildings", "materials", "spatial"},
        "village case",
    )
    if root["schema_version"] != "village-case-1":
        raise ValueError("unsupported village case schema_version")
    if root["mode"] not in {"synthetic_demo", "research"}:
        raise ValueError("case mode must be synthetic_demo or research")
    imagery = _mapping(root["imagery"], "imagery")
    buildings = _mapping(root["buildings"], "buildings")
    materials = _mapping(root["materials"], "materials")
    spatial = _mapping(root["spatial"], "spatial")
    _keys(imagery, {"path", "reference"}, "imagery")
    _keys(buildings, {"path", "format"}, "buildings")
    material_required = {"source", "format", "catalog"}
    material_unknown = materials.keys() - material_required - {"codes"}
    material_missing = material_required - materials.keys()
    if material_missing:
        raise ValueError(f"materials is missing keys: {sorted(material_missing)}")
    if material_unknown:
        raise ValueError(f"materials contains unknown keys: {sorted(material_unknown)}")
    _keys(spatial, {"coordinate_mode", "crs", "grid"}, "spatial")
    if buildings["format"] not in {"local_json", "labelme_json", "geojson_wgs84"}:
        raise ValueError("buildings.format must be local_json, labelme_json, or geojson_wgs84")
    if materials["format"] not in {"grid_csv", "geotiff_codes"}:
        raise ValueError("materials.format must be grid_csv or geotiff_codes")
    raw_grid = _mapping(spatial["grid"], "spatial.grid")
    _keys(
        raw_grid,
        {"x_min_m", "y_max_m", "height", "width", "cell_size_m"},
        "spatial.grid",
    )
    grid = VillageGridSpec(
        height=raw_grid["height"],
        width=raw_grid["width"],
        cell_size_m=float(raw_grid["cell_size_m"]),
        x_min_m=float(raw_grid["x_min_m"]),
        y_max_m=float(raw_grid["y_max_m"]),
        coordinate_mode=spatial["coordinate_mode"],
        crs=spatial["crs"],
    )
    base = source.parent

    def resolve(value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("case file paths must be non-empty strings")
        result = (base / value).resolve()
        if not result.is_file():
            raise FileNotFoundError(result)
        return result

    reference = imagery["reference"]
    if reference is not None and not isinstance(reference, str):
        raise TypeError("imagery.reference must be a path string or null")
    if reference is None and Path(str(imagery["path"])).suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("ordinary images require imagery.reference")
    codes = materials.get("codes")
    if materials["format"] == "geotiff_codes" and codes is None:
        raise ValueError("geotiff_codes materials require a codes mapping file")
    if materials["format"] == "grid_csv" and codes is not None:
        raise ValueError("grid_csv materials must not define a codes mapping")

    return VillageCaseConfig(
        path=source,
        case_id=str(root["case_id"]),
        mode=root["mode"],
        grid=grid,
        imagery_path=resolve(imagery["path"]),
        image_reference_path=None if reference is None else resolve(reference),
        buildings_path=resolve(buildings["path"]),
        buildings_format=buildings["format"],
        material_grid_path=resolve(materials["source"]),
        material_format=materials["format"],
        material_codes_path=None if codes is None else resolve(codes),
        material_catalog_path=resolve(materials["catalog"]),
    )


def load_village_scenario(path: str | Path) -> VillageScenarioConfig:
    """Load an effective-exposure version-1 scenario."""

    source = Path(path).resolve()
    root = _mapping(_load_yaml(source), "village scenario")
    _keys(
        root,
        {
            "schema_version",
            "scenario_id",
            "model",
            "weather",
            "ignitions",
            "numerics",
            "outputs",
            "visualization",
        },
        "village scenario",
    )
    if root["schema_version"] != "village-scenario-1":
        raise ValueError("unsupported village scenario schema_version")
    model = _mapping(root["model"], "model")
    weather = _mapping(root["weather"], "weather")
    numerics = _mapping(root["numerics"], "numerics")
    outputs = _mapping(root["outputs"], "outputs")
    visual = _mapping(root["visualization"], "visualization")
    _keys(
        model,
        {
            "name",
            "exposure_radius_m",
            "decay_length_m",
            "wind_beta_s_m",
            "coefficients_evidence",
            "coefficients_source_id",
        },
        "model",
    )
    if model["name"] != "effective_exposure_ca_v1":
        raise ValueError("unsupported village model")
    _keys(weather, {"wind_speed_m_s", "wind_from_deg"}, "weather")
    _keys(numerics, {"max_dt_s", "end_time_s"}, "numerics")
    _keys(outputs, {"sample_interval_s"}, "outputs")
    _keys(visual, {"heating_display_fraction"}, "visualization")
    raw_ignitions = root["ignitions"]
    if not isinstance(raw_ignitions, list):
        raise TypeError("ignitions must be a list")
    ignitions: list[VillageIgnitionEvent] = []
    for index, raw in enumerate(raw_ignitions):
        item = _mapping(raw, f"ignitions[{index}]")
        _keys(item, {"cell", "time_s", "kind"}, f"ignitions[{index}]")
        cell = _mapping(item["cell"], f"ignitions[{index}].cell")
        _keys(cell, {"row", "col"}, f"ignitions[{index}].cell")
        ignitions.append(
            VillageIgnitionEvent(float(item["time_s"]), cell["row"], cell["col"], item["kind"])
        )
    scenario = VillageScenario(
        max_dt_s=float(numerics["max_dt_s"]),
        end_time_s=float(numerics["end_time_s"]),
        heating_display_fraction=float(visual["heating_display_fraction"]),
        ignitions=tuple(ignitions),
    )
    sample_interval_s = float(outputs["sample_interval_s"])
    if sample_interval_s <= 0.0:
        raise ValueError("sample_interval_s must be positive")
    return VillageScenarioConfig(
        path=source,
        scenario_id=str(root["scenario_id"]),
        scenario=scenario,
        exposure_radius_m=float(model["exposure_radius_m"]),
        decay_length_m=float(model["decay_length_m"]),
        wind_speed_m_s=float(weather["wind_speed_m_s"]),
        wind_from_deg=float(weather["wind_from_deg"]),
        wind_beta_s_m=float(model["wind_beta_s_m"]),
        sample_interval_s=sample_interval_s,
    )
