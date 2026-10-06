"""Explicit component HRR clocks closed against their existing dry-fuel budget.

This is a prescribed flaming-curve experiment, not a moisture/oxygen-resolved
combustion model. Shortening a clock necessarily increases its peak at fixed
fuel energy; a required caller-supplied PUA ceiling makes that tradeoff visible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.vegetation_prepare import LANDCOVER_IDS

SCHEMA = "village-component-curves-1"


@dataclass(frozen=True, slots=True)
class ComponentBurnCurve:
    growth_time_s: float
    steady_time_s: float
    decay_time_s: float
    max_peak_hrrpua_W_m2_active: float

    def __post_init__(self) -> None:
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"curve {key} must be numeric")
            if not np.isfinite(value) or value < 0 or (key != "steady_time_s" and value == 0):
                raise ValueError(f"curve {key} must be finite and positive (steady may be zero)")
        if not np.isfinite(self.duration_s) or not np.isfinite(self.effective_duration_s):
            raise ValueError("curve duration overflows")

    @property
    def effective_duration_s(self) -> float:
        return self.growth_time_s / 3 + self.steady_time_s + self.decay_time_s / 2

    @property
    def duration_s(self) -> float:
        return self.growth_time_s + self.steady_time_s + self.decay_time_s


@dataclass(frozen=True, slots=True)
class ComponentCurveConfiguration:
    evidence: str
    provenance: str
    curves: dict[str, dict[str, ComponentBurnCurve]]

    def __post_init__(self) -> None:
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("curve evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("curve provenance must be nonempty")
        if not isinstance(self.curves, dict) or not self.curves:
            raise ValueError("curves must be a nonempty mapping")
        clean = {}
        for label, parts in self.curves.items():
            if label not in LANDCOVER_IDS[1:] or not isinstance(parts, dict) or not parts:
                raise ValueError("curves require burnable landcover/component mappings")
            for name, curve in parts.items():
                if not isinstance(name, str) or not name or not name.isascii():
                    raise ValueError("curve component id must be nonempty ASCII")
                if not isinstance(curve, ComponentBurnCurve):
                    raise TypeError("curves must contain ComponentBurnCurve values")
            clean[label] = dict(parts)
        object.__setattr__(self, "curves", clean)

    def validate_component_ids(self, recipes: dict[str, tuple[str, ...]]) -> None:
        # Validate even when a configured landcover is absent from this domain:
        # a misspelled upper component must never silently fall back to inherited clocks.
        for label, parts in self.curves.items():
            allowed = recipes.get(label, ("whole",))
            for name in parts:
                if name not in allowed:
                    raise ValueError(f"unknown curve component {label}/{name}; expected {allowed}")

    def to_mapping(self) -> dict:
        return {
            "schema_version": SCHEMA,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "curves": {
                label: {name: asdict(curve) for name, curve in parts.items()}
                for label, parts in self.curves.items()
            },
        }

    @classmethod
    def from_mapping(cls, value: dict) -> ComponentCurveConfiguration:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            *cls.__dataclass_fields__,
        }:
            raise ValueError("curve configuration requires exact schema keys")
        if value["schema_version"] != SCHEMA or not isinstance(value["curves"], dict):
            raise ValueError("invalid curve configuration schema or curves")
        curves = {}
        for label, parts in value["curves"].items():
            if not isinstance(parts, dict):
                raise ValueError("curve components must be a mapping")
            curves[label] = {}
            for name, fields in parts.items():
                if not isinstance(fields, dict) or set(fields) != set(
                    ComponentBurnCurve.__dataclass_fields__
                ):
                    raise ValueError("component curve requires exact timing and ceiling keys")
                curves[label][name] = ComponentBurnCurve(**fields)
        return cls(value["evidence"], value["provenance"], curves)


def load_component_curve_configuration(path: str | Path) -> ComponentCurveConfiguration:
    return ComponentCurveConfiguration.from_mapping(yaml.safe_load(Path(path).read_text()))


def apply_component_curves(
    fields: dict[str, np.ndarray],
    cell_ids: np.ndarray,
    component_ids: list[str],
    landcover: np.ndarray,
    burnable: np.ndarray,
    configuration: ComponentCurveConfiguration,
) -> dict:
    """Modify local expanded arrays only; preserve fuel, water, area and integrated energy."""
    diagnostics = {}
    names = np.asarray(component_ids)
    for label, parts in configuration.curves.items():
        for name, curve in parts.items():
            mask = (
                (landcover[cell_ids] == LANDCOVER_IDS.index(label))
                & (names == name)
                & burnable[cell_ids]
            )
            old_peak = fields["peak_hrr_W"][mask].copy()
            old_duration = sum(
                fields[key][mask] for key in ("growth_time_s", "steady_time_s", "decay_time_s")
            )
            peak = fields["available_energy_J"][mask] / curve.effective_duration_s
            pua = peak / fields["active_surface_area_m2"][mask]
            if not np.all(np.isfinite(peak)) or not np.all(np.isfinite(pua)):
                raise ValueError(f"nonfinite resolved peak for {label}/{name}")
            if np.any(pua > curve.max_peak_hrrpua_W_m2_active):
                raise ValueError(
                    f"curve {label}/{name} exceeds explicit peak PUA ceiling: "
                    f"{pua.max():.9g} > {curve.max_peak_hrrpua_W_m2_active:.9g} W/m2 active; "
                    "revise measured fuel/area/timing inputs; no clipping or fuel deletion applied"
                )
            for key in ("growth_time_s", "steady_time_s", "decay_time_s"):
                fields[key][mask] = getattr(curve, key)
            fields["peak_hrr_W"][mask] = peak
            fields["peak_hrrpua_W_m2_active"][mask] = pua
            fields["configured_curve_energy_J"][mask] = peak * curve.effective_duration_s
            diagnostics[f"{label}/{name}"] = {
                "applied_nodes": int(mask.sum()),
                "duration_s": curve.duration_s,
                "old_duration_range_s": [float(old_duration.min()), float(old_duration.max())]
                if mask.any()
                else None,
                "peak_ratio_range": [float((peak / old_peak).min()), float((peak / old_peak).max())]
                if mask.any()
                else None,
                "resolved_peak_pua_range_W_m2_active": [float(pua.min()), float(pua.max())]
                if mask.any()
                else None,
                "ceiling_is_user_supplied_not_a_universal_material_limit": True,
            }
    return diagnostics
