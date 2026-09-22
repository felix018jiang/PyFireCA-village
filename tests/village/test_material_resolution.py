from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pyfireca.village.assembly_presets import (
    load_assembly_preset_library,
    parse_assembly_preset_library,
)
from pyfireca.village.material_cards import load_material_card_library
from pyfireca.village.material_resolution import resolve_material_selections
from pyfireca.village.moisture import (
    load_moisture_initialization,
    parse_moisture_initialization,
)
from pyfireca.village.survey import parse_field_survey

ROOT = Path(__file__).parents[2]
CARDS = ROOT / "examples/village/material_cards/catalog.yml"
ASSEMBLIES = ROOT / "examples/village/material_cards/assemblies.synthetic.yml"
MOISTURE = ROOT / "examples/village/material_cards/moisture.initial.synthetic.yml"
SURVEY = ROOT / "examples/village/material_cards/survey.example.yml"


def _survey_with(*, quantity_band: str | None = "standard", duplicate: bool = False):
    raw = yaml.safe_load(SURVEY.read_text(encoding="utf-8"))
    raw["records"] = [raw["records"][0]]
    if quantity_band is not None:
        raw["records"][0]["quantity_band"] = quantity_band
    if duplicate:
        raw["records"].append(
            {
                "observation_id": "OBS-002",
                "object_id": "B0233",
                "component": "interior_wall",
                "geometry_ref": "wall-segments/B0233-east",
                "material_class": "masonry_brick",
                "quantity_band": "standard",
            }
        )
    return parse_field_survey(raw)


def _libraries():
    return (
        load_material_card_library(CARDS),
        load_assembly_preset_library(ASSEMBLIES),
        load_moisture_initialization(MOISTURE),
    )


def test_m02_libraries_are_explicit_and_do_not_store_steady_time() -> None:
    cards, assemblies, moisture = _libraries()

    assert not assemblies.production_ready
    assert assemblies.burn_curve_policy == "derive_steady_time_from_energy_budget"
    assert len(assemblies.presets) == 11
    assert set(cards.by_id["wood_unspecified"].assembly_preset_ids) == {"synthetic_wood_surface"}
    assert all(
        preset_id in assemblies.by_id
        for card in cards.cards
        for preset_id in card.assembly_preset_ids
    )
    assert set(moisture.by_material_class) == {
        "wood_unspecified",
        "bamboo_unspecified",
        "thatch_straw",
        "grass",
        "litter",
        "shrub",
        "tree_equivalent",
    }
    assert "steady_time_s" not in assemblies.by_id["synthetic_wood_surface"].base_parameters


def test_m02_resolves_mass_water_and_steady_time_deterministically() -> None:
    cards, assemblies, moisture = _libraries()
    survey = _survey_with(quantity_band="standard")
    kwargs = {
        "geometry_areas_m2": {"wall-segments/B0233-east": 10.0},
        "mode": "demonstration",
        "case_id": "m02-determinism",
    }
    first = resolve_material_selections(survey, cards, assemblies, moisture, **kwargs)
    second = resolve_material_selections(survey, cards, assemblies, moisture, **kwargs)

    assert first.can_run
    assert first.configuration_sha256 == second.configuration_sha256
    record = first.records[0]
    assert record.dry_fuel_mass_kg == pytest.approx(150.0)
    assert record.pyrolysable_dry_fuel_mass_kg == pytest.approx(90.0)
    assert record.water_mass_kg == pytest.approx(15.0)
    assert record.source_ids == (
        "Janssens1991",
        "TranWhite1992",
        "NIST_LIFT_CONE",
        "USDA_WOOD_HANDBOOK",
    )
    assert record.steady_time_s is not None and record.steady_time_s > 0
    assert record.configured_curve_energy_J == pytest.approx(record.available_energy_J)
    assert record.parameter_provenance["steady_time_s"] == "numerically_derived:energy_budget"


def test_m02_does_not_default_missing_quantity_or_moisture() -> None:
    cards, assemblies, moisture = _libraries()
    no_quantity = resolve_material_selections(
        _survey_with(quantity_band=None),
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
    )
    assert not no_quantity.can_run
    assert any(issue.code == "missing_quantity_band" for issue in no_quantity.missing_report)

    raw_moisture = yaml.safe_load(MOISTURE.read_text(encoding="utf-8"))
    raw_moisture["states"] = [
        state
        for state in raw_moisture["states"]
        if "wood_unspecified" not in state["material_classes"]
    ]
    incomplete_moisture = parse_moisture_initialization(raw_moisture)
    no_moisture = resolve_material_selections(
        _survey_with(quantity_band="standard"),
        cards,
        assemblies,
        incomplete_moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
    )
    assert not no_moisture.can_run
    assert any(issue.code == "missing_moisture_state" for issue in no_moisture.issues)


def test_m02_inert_material_needs_no_fuel_or_moisture_state() -> None:
    cards, assemblies, moisture = _libraries()
    survey = _survey_with(quantity_band="standard")
    raw = yaml.safe_load(SURVEY.read_text(encoding="utf-8"))
    raw["records"] = [
        {
            "observation_id": "OBS-BRICK",
            "object_id": "B0233",
            "component": "interior_wall",
            "geometry_ref": "buildings/B0233",
            "material_class": "masonry_brick",
            "quantity_band": "standard",
        }
    ]
    brick_survey = parse_field_survey(raw)
    resolved = resolve_material_selections(
        brick_survey,
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"buildings/B0233": 20.0},
    )
    assert resolved.can_run
    assert resolved.records[0].burnable is False
    assert resolved.records[0].dry_fuel_mass_kg == pytest.approx(0.0)
    assert resolved.records[0].water_mass_kg == pytest.approx(0.0)
    assert survey.records[0].material_class == "wood_unspecified"


def test_m02_requires_explicit_active_component_for_same_geometry() -> None:
    cards, assemblies, moisture = _libraries()
    survey = _survey_with(quantity_band="standard", duplicate=True)
    unresolved = resolve_material_selections(
        survey,
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
    )
    assert not unresolved.can_run
    assert any(issue.code == "component_conflict" for issue in unresolved.issues)
    selected = resolve_material_selections(
        survey,
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
        active_observation_ids=["OBS-001"],
    )
    assert selected.can_run
    assert [record.active for record in selected.records] == [True, False]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: raw["presets"][0]["base_parameters"].update(
                {"steady_time_s": {"value": 1, "unit": "s", "provenance": "bad"}}
            ),
            "unknown keys",
        ),
        (
            lambda raw: raw["presets"][0]["base_parameters"]["heat_of_combustion_J_kg_dry"].update(
                {"unit": "kJ/kg"}
            ),
            "unit must be",
        ),
        (
            lambda raw: raw["presets"][0]["quantity_bands"]["standard"]["fuel_load_kg_m2"].update(
                {"value": -1}
            ),
            "finite and non-negative",
        ),
    ],
)
def test_m02_preset_parser_rejects_hidden_or_invalid_values(mutation, message: str) -> None:
    raw = yaml.safe_load(ASSEMBLIES.read_text(encoding="utf-8"))
    mutation(raw)
    with pytest.raises((TypeError, ValueError), match=message):
        parse_assembly_preset_library(raw)


def test_m02_evidence_mode_rejects_synthetic_assumptions() -> None:
    cards, assemblies, moisture = _libraries()
    result = resolve_material_selections(
        _survey_with(quantity_band="standard"),
        cards,
        assemblies,
        moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
        mode="evidence_checked",
    )
    assert not result.can_run
    assert any(issue.code == "assumed_assembly_in_evidence_mode" for issue in result.issues)
    assert any(issue.code == "assumed_moisture_in_evidence_mode" for issue in result.issues)


def test_m02_reports_energy_budget_overrun() -> None:
    cards, _assemblies, moisture = _libraries()
    raw = yaml.safe_load(ASSEMBLIES.read_text(encoding="utf-8"))
    raw["presets"][0]["quantity_bands"]["thin"]["fuel_load_kg_m2"]["value"] = 0.0001
    altered = parse_assembly_preset_library(raw)
    raw_survey = yaml.safe_load(SURVEY.read_text(encoding="utf-8"))
    raw_survey["records"] = [raw_survey["records"][0]]
    raw_survey["records"][0]["quantity_band"] = "thin"
    result = resolve_material_selections(
        parse_field_survey(raw_survey),
        cards,
        altered,
        moisture,
        geometry_areas_m2={"wall-segments/B0233-east": 10.0},
    )
    assert not result.can_run
    assert any(issue.code == "burn_curve_over_budget" for issue in result.issues)


def test_m02_rejects_nonfinite_geometry_area_before_hashing() -> None:
    cards, assemblies, moisture = _libraries()
    with pytest.raises(ValueError, match="finite and non-negative"):
        resolve_material_selections(
            _survey_with(quantity_band="standard"),
            cards,
            assemblies,
            moisture,
            geometry_areas_m2={"wall-segments/B0233-east": float("nan")},
        )
