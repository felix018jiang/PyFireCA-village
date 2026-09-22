"""M02 selection and resolution of cards, presets, and initial water states.

This module resolves a survey record to explicit quantities without touching a
grid or running a solver.  Missing optional survey quantities remain visible as
errors; no ``standard`` quantity or zero moisture is inserted implicitly.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from pyfireca.village.assembly_presets import (
    AssemblyPreset,
    AssemblyPresetLibrary,
    PresetParameter,
)
from pyfireca.village.material_cards import MaterialCardLibrary
from pyfireca.village.moisture import MoistureInitialization
from pyfireca.village.survey import FieldSurvey

SCHEMA_VERSION = "village-resolved-survey-case-1"
RESOLUTION_MODES = ("demonstration", "evidence_checked")
PARAMETER_SOURCE_ORDER = (
    "field_measured",
    "literature_reference",
    "scenario_assumed",
    "numerically_derived",
)


@dataclass(frozen=True, slots=True)
class ResolutionIssue:
    code: str
    severity: str
    observation_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ResolvedMaterialRecord:
    observation_id: str
    object_id: str
    component: str
    geometry_ref: str
    material_class: str
    card_id: str | None
    source_ids: tuple[str, ...]
    assembly_preset_id: str | None
    quantity_band: str | None
    active: bool
    burnable: bool
    covered_area_m2: float | None
    dry_fuel_mass_kg: float | None
    pyrolysable_dry_fuel_mass_kg: float | None
    water_mass_kg: float | None
    moisture_content_dry_basis: float | None
    effective_heated_dry_mass_kg: float | None
    active_surface_area_m2: float | None
    receiver_area_m2: float | None
    peak_hrr_W: float | None
    available_energy_J: float | None
    growth_time_s: float | None
    steady_time_s: float | None
    decay_time_s: float | None
    configured_curve_energy_J: float | None
    parameter_provenance: dict[str, str]


@dataclass(frozen=True, slots=True)
class ResolvedSurveyCase:
    case_id: str
    mode: str
    survey_id: str
    material_library_id: str
    assembly_library_id: str
    moisture_package_id: str
    records: tuple[ResolvedMaterialRecord, ...]
    issues: tuple[ResolutionIssue, ...]
    assumptions: tuple[str, ...]
    configuration_sha256: str

    @property
    def can_run(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def missing_report(self) -> tuple[ResolutionIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code.startswith("missing_"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "mode": self.mode,
            "survey_id": self.survey_id,
            "material_library_id": self.material_library_id,
            "assembly_library_id": self.assembly_library_id,
            "moisture_package_id": self.moisture_package_id,
            "records": [_record_dict(record) for record in self.records],
            "issues": [_issue_dict(issue) for issue in self.issues],
            "assumptions": list(self.assumptions),
            "configuration_sha256": self.configuration_sha256,
        }


def _number(parameter: PresetParameter, name: str) -> float:
    if isinstance(parameter.value, str):
        raise TypeError(f"{name} must be numeric")
    return float(parameter.value)


def _record_dict(record: ResolvedMaterialRecord) -> dict[str, Any]:
    return {
        name: getattr(record, name)
        for name in record.__dataclass_fields__
        if name != "parameter_provenance"
    } | {"parameter_provenance": dict(sorted(record.parameter_provenance.items()))}


def _issue_dict(issue: ResolutionIssue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "observation_id": issue.observation_id,
        "message": issue.message,
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _empty_record(record: Any, active: bool) -> ResolvedMaterialRecord:
    return ResolvedMaterialRecord(
        observation_id=record.observation_id,
        object_id=record.object_id,
        component=record.component,
        geometry_ref=record.geometry_ref,
        material_class=record.material_class,
        card_id=None,
        source_ids=(),
        assembly_preset_id=None,
        quantity_band=record.quantity_band,
        active=active,
        burnable=False,
        covered_area_m2=None,
        dry_fuel_mass_kg=None,
        pyrolysable_dry_fuel_mass_kg=None,
        water_mass_kg=None,
        moisture_content_dry_basis=None,
        effective_heated_dry_mass_kg=None,
        active_surface_area_m2=None,
        receiver_area_m2=None,
        peak_hrr_W=None,
        available_energy_J=None,
        growth_time_s=None,
        steady_time_s=None,
        decay_time_s=None,
        configured_curve_energy_J=None,
        parameter_provenance={},
    )


def _issue(
    issues: list[ResolutionIssue], code: str, observation_id: str | None, message: str
) -> None:
    issues.append(ResolutionIssue(code, "error", observation_id, message))


def _select_preset(
    record: Any,
    card: Any,
    assemblies: AssemblyPresetLibrary,
    overrides: Mapping[str, str],
    issues: list[ResolutionIssue],
) -> AssemblyPreset | None:
    explicit = overrides.get(record.observation_id)
    candidates = (explicit,) if explicit else card.assembly_preset_ids
    if not candidates:
        _issue(
            issues,
            "missing_assembly_preset",
            record.observation_id,
            f"material card {card.id!r} has no assembly preset",
        )
        return None
    if len(candidates) != 1:
        _issue(
            issues,
            "ambiguous_assembly_preset",
            record.observation_id,
            f"material card {card.id!r} has multiple presets; select one explicitly",
        )
        return None
    preset = assemblies.by_id.get(candidates[0])
    if preset is None:
        _issue(
            issues,
            "missing_assembly_preset",
            record.observation_id,
            f"assembly preset {candidates[0]!r} is not in the selected library",
        )
        return None
    if preset.material_card_id != card.id:
        _issue(
            issues,
            "assembly_card_mismatch",
            record.observation_id,
            f"assembly preset {preset.id!r} belongs to {preset.material_card_id!r}, "
            f"not {card.id!r}",
        )
        return None
    if record.component not in preset.allowed_components:
        _issue(
            issues,
            "component_not_allowed",
            record.observation_id,
            f"component {record.component!r} is not allowed by assembly preset {preset.id!r}",
        )
        return None
    return preset


def _resolve_record(
    record: Any,
    card: Any,
    preset: AssemblyPreset,
    moisture: MoistureInitialization,
    area_m2: float,
    active: bool,
    mode: str,
    issues: list[ResolutionIssue],
    assumptions: set[str],
) -> ResolvedMaterialRecord:
    band_id = record.quantity_band
    if band_id is None:
        _issue(
            issues,
            "missing_quantity_band",
            record.observation_id,
            "quantity_band is optional in the survey but required for resolution; "
            "no default was inserted",
        )
        return _empty_record(record, active)
    band = preset.quantity_bands.get(band_id)
    if band is None:
        _issue(
            issues,
            "missing_quantity_band",
            record.observation_id,
            f"assembly preset {preset.id!r} has no quantity band {band_id!r}",
        )
        return _empty_record(record, active)
    if card.combustibility == "burnable" and not preset.burnable:
        _issue(
            issues, "burnability_mismatch", record.observation_id, "burnable card has inert preset"
        )
        return _empty_record(record, active)
    if card.combustibility == "inert" and preset.burnable:
        _issue(
            issues, "burnability_mismatch", record.observation_id, "inert card has burnable preset"
        )
        return _empty_record(record, active)
    if card.combustibility == "unknown":
        _issue(
            issues,
            "unknown_material",
            record.observation_id,
            "unknown material cannot be resolved without an explicit field or "
            "scenario classification",
        )
        return _empty_record(record, active)
    if not isfinite(area_m2) or area_m2 <= 0.0:
        _issue(
            issues, "invalid_geometry_area", record.observation_id, "covered area must be positive"
        )
        return _empty_record(record, active)

    base = preset.base_parameters
    provenance: dict[str, str] = {}
    for name, parameter in base.items():
        provenance[name] = parameter.provenance
    for name in (
        "fuel_load_kg_m2",
        "effective_heated_dry_mass_kg_m2",
        "active_surface_area_ratio",
        "receiver_area_ratio",
        "source_height_m",
        "receiver_height_m",
    ):
        provenance[name] = getattr(band, name).provenance
    moisture_state = (
        moisture.by_material_class.get(record.material_class) if preset.burnable else None
    )
    if preset.burnable and moisture_state is None:
        _issue(
            issues,
            "missing_moisture_state",
            record.observation_id,
            f"no initial moisture state covers {record.material_class!r}",
        )
        return _empty_record(record, active)
    if preset.burnable and moisture_state is not None:
        moisture_value = _number(
            moisture_state.moisture_content_dry_basis, "moisture_content_dry_basis"
        )
        provenance["moisture_content_dry_basis"] = (
            moisture_state.moisture_content_dry_basis.provenance
        )
        if moisture_state.status == "scenario_assumed":
            assumptions.add(f"{record.observation_id}: initial moisture from {moisture_state.id}")
            if mode == "evidence_checked":
                _issue(
                    issues,
                    "assumed_moisture_in_evidence_mode",
                    record.observation_id,
                    f"moisture state {moisture_state.id!r} is scenario_assumed",
                )
    else:
        moisture_value = 0.0

    fuel_load = _number(band.fuel_load_kg_m2, "fuel_load_kg_m2")
    effective_mass = _number(
        band.effective_heated_dry_mass_kg_m2, "effective_heated_dry_mass_kg_m2"
    )
    pyro_fraction = _number(
        base.get("pyrolysable_dry_mass_fraction", PresetParameter(0.0, "1", "inert")),
        "pyrolysable_dry_mass_fraction",
    )
    dry_mass = area_m2 * fuel_load
    pyro_mass = dry_mass * pyro_fraction
    water_mass = dry_mass * moisture_value
    active_area = area_m2 * _number(band.active_surface_area_ratio, "active_surface_area_ratio")
    receiver_area = area_m2 * _number(band.receiver_area_ratio, "receiver_area_ratio")
    heat = _number(
        base.get("heat_of_combustion_J_kg_dry", PresetParameter(0.0, "J/kg_dry", "inert")),
        "heat_of_combustion_J_kg_dry",
    )
    peak = active_area * _number(
        base.get("peak_hrrpua_W_m2", PresetParameter(0.0, "W/m^2_active", "inert")),
        "peak_hrrpua_W_m2",
    )
    available = pyro_mass * heat
    growth = _number(base.get("growth_time_s", PresetParameter(0.0, "s", "inert")), "growth_time_s")
    decay = _number(base.get("decay_time_s", PresetParameter(0.0, "s", "inert")), "decay_time_s")
    if peak == 0.0:
        steady = 0.0
        if available > 1e-9:
            _issue(
                issues,
                "burn_curve_over_budget",
                record.observation_id,
                "positive fuel energy has zero peak HRR",
            )
    else:
        steady = available / peak - growth / 3.0 - decay / 2.0
        if steady < -1e-9:
            _issue(
                issues,
                "burn_curve_over_budget",
                record.observation_id,
                "growth/decay curve exceeds the explicit fuel-energy budget",
            )
            steady = 0.0
        steady = max(0.0, steady)
    curve_energy = peak * (growth / 3.0 + steady + decay / 2.0)
    if curve_energy > available + max(1e-6, available * 1e-10):
        _issue(
            issues,
            "burn_curve_over_budget",
            record.observation_id,
            "configured curve exceeds fuel energy",
        )
    if preset.evidence_status == "scenario_assumed":
        assumptions.add(f"{record.observation_id}: assembly preset {preset.id}")
        if mode == "evidence_checked":
            _issue(
                issues,
                "assumed_assembly_in_evidence_mode",
                record.observation_id,
                f"assembly preset {preset.id!r} is scenario_assumed",
            )
    provenance.update(
        {
            "steady_time_s": "numerically_derived:energy_budget",
            "dry_fuel_mass_kg": "numerically_derived:area_times_fuel_load",
            "pyrolysable_dry_fuel_mass_kg": "numerically_derived:dry_mass_times_fraction",
            "water_mass_kg": "numerically_derived:dry_mass_times_initial_moisture",
            "active_surface_area_m2": "numerically_derived:area_times_active_ratio",
            "receiver_area_m2": "numerically_derived:area_times_receiver_ratio",
            "peak_hrr_W": "numerically_derived:active_area_times_hrrpua",
            "available_energy_J": "numerically_derived:pyrolysable_mass_times_heat",
            "configured_curve_energy_J": "numerically_derived:burn_curve_integral",
        }
    )
    return ResolvedMaterialRecord(
        observation_id=record.observation_id,
        object_id=record.object_id,
        component=record.component,
        geometry_ref=record.geometry_ref,
        material_class=record.material_class,
        card_id=card.id,
        source_ids=card.source_ids,
        assembly_preset_id=preset.id,
        quantity_band=band_id,
        active=active,
        burnable=preset.burnable,
        covered_area_m2=area_m2,
        dry_fuel_mass_kg=dry_mass,
        pyrolysable_dry_fuel_mass_kg=pyro_mass,
        water_mass_kg=water_mass,
        moisture_content_dry_basis=moisture_value,
        effective_heated_dry_mass_kg=area_m2 * effective_mass,
        active_surface_area_m2=active_area,
        receiver_area_m2=receiver_area,
        peak_hrr_W=peak,
        available_energy_J=available,
        growth_time_s=growth,
        steady_time_s=steady,
        decay_time_s=decay,
        configured_curve_energy_J=curve_energy,
        parameter_provenance=provenance,
    )


def resolve_material_selections(
    survey: FieldSurvey,
    cards: MaterialCardLibrary,
    assemblies: AssemblyPresetLibrary,
    moisture: MoistureInitialization,
    *,
    geometry_areas_m2: Mapping[str, float],
    mode: str = "demonstration",
    case_id: str = "resolved-survey",
    preset_by_observation: Mapping[str, str] | None = None,
    active_observation_ids: Iterable[str] | None = None,
) -> ResolvedSurveyCase:
    """Resolve a survey into auditable quantities and a deterministic hash."""

    if mode not in RESOLUTION_MODES:
        raise ValueError(f"mode must be one of {list(RESOLUTION_MODES)!r}")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    normalized_geometry_areas: dict[str, float] = {}
    for raw_geometry_ref, raw_area in geometry_areas_m2.items():
        if not isinstance(raw_geometry_ref, str) or not raw_geometry_ref.strip():
            raise ValueError("geometry_areas_m2 keys must be non-empty strings")
        if isinstance(raw_area, bool) or not isinstance(raw_area, (int, float)):
            raise TypeError(f"geometry area for {raw_geometry_ref!r} must be numeric")
        area = float(raw_area)
        if not isfinite(area) or area < 0.0:
            raise ValueError(
                f"geometry area for {raw_geometry_ref!r} must be finite and non-negative"
            )
        normalized_geometry_areas[raw_geometry_ref] = area
    overrides = dict(preset_by_observation or {})
    active_ids = set(active_observation_ids) if active_observation_ids is not None else None
    issues: list[ResolutionIssue] = []
    assumptions: set[str] = set()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for record in survey.records:
        grouped[record.geometry_ref].append(record)
    active_by_observation: dict[str, bool] = {}
    for geometry_ref, records in grouped.items():
        if len(records) == 1:
            active_by_observation[records[0].observation_id] = (
                active_ids is None or records[0].observation_id in active_ids
            )
            continue
        selected = [
            record
            for record in records
            if active_ids is not None and record.observation_id in active_ids
        ]
        if active_ids is None or len(selected) != 1:
            _issue(
                issues,
                "component_conflict",
                None,
                f"geometry {geometry_ref!r} has {len(records)} components; "
                "select exactly one active observation",
            )
            for record in records:
                active_by_observation[record.observation_id] = False
        else:
            for record in records:
                active_by_observation[record.observation_id] = record in selected
    records_out: list[ResolvedMaterialRecord] = []
    card_by_class = cards.by_material_class
    for record in survey.records:
        active = active_by_observation.get(record.observation_id, True)
        card = card_by_class.get(record.material_class)
        if card is None:
            _issue(
                issues, "missing_material_card", record.observation_id, "material class has no card"
            )
            records_out.append(_empty_record(record, active))
            continue
        preset = _select_preset(record, card, assemblies, overrides, issues)
        if preset is None:
            records_out.append(_empty_record(record, active))
            continue
        area_value = normalized_geometry_areas.get(record.geometry_ref)
        if area_value is None:
            _issue(
                issues,
                "missing_geometry_area",
                record.observation_id,
                f"geometry {record.geometry_ref!r} has no resolved area",
            )
            records_out.append(_empty_record(record, active))
            continue
        records_out.append(
            _resolve_record(
                record,
                card,
                preset,
                moisture,
                area_value,
                active,
                mode,
                issues,
                assumptions,
            )
        )
    issue_payload = [_issue_dict(issue) for issue in issues]
    record_payload = [_record_dict(record) for record in records_out]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "mode": mode,
        "survey_id": survey.survey_id,
        "material_library_id": cards.library_id,
        "assembly_library_id": assemblies.library_id,
        "moisture_package_id": moisture.package_id,
        "geometry_areas_m2": dict(sorted(normalized_geometry_areas.items())),
        "preset_by_observation": dict(sorted(overrides.items())),
        "active_observation_ids": sorted(active_ids) if active_ids is not None else None,
        "records": record_payload,
        "issues": issue_payload,
        "assumptions": sorted(assumptions),
    }
    return ResolvedSurveyCase(
        case_id=case_id,
        mode=mode,
        survey_id=survey.survey_id,
        material_library_id=cards.library_id,
        assembly_library_id=assemblies.library_id,
        moisture_package_id=moisture.package_id,
        records=tuple(records_out),
        issues=tuple(issues),
        assumptions=tuple(sorted(assumptions)),
        configuration_sha256=_hash_payload(payload),
    )


__all__ = [
    "PARAMETER_SOURCE_ORDER",
    "RESOLUTION_MODES",
    "SCHEMA_VERSION",
    "ResolutionIssue",
    "ResolvedMaterialRecord",
    "ResolvedSurveyCase",
    "resolve_material_selections",
]
