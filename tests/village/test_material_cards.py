from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pyfireca.village.material_cards import (
    load_material_card_library,
    parse_material_card_library,
)
from pyfireca.village.survey import MATERIAL_CLASS_IDS

CATALOG = Path(__file__).parents[2] / "examples/village/material_cards/catalog.yml"


def _raw_catalog() -> dict:
    return yaml.safe_load(CATALOG.read_text(encoding="utf-8"))


def test_minimal_catalog_covers_every_field_material_class_without_runtime_numbers() -> None:
    library = load_material_card_library(CATALOG)

    assert library.library_id == "gaoyou-minimal-material-cards"
    assert library.evidence_inventory_status == "minimal_unresolved"
    assert not library.production_ready
    assert set(library.by_material_class) == set(MATERIAL_CLASS_IDS)
    assert len(library.cards) == len(MATERIAL_CLASS_IDS)
    assert library.evidence_status_counts == {
        "reference_only": 2,
        "pending_full_text": 9,
        "scenario_assumed": 0,
        "field_observation_only": 3,
    }
    wood = library.by_id["wood_unspecified"]
    assert wood.reference_profile_ids == (
        "wood_douglas_ovendry_reference",
        "wood_southern_pine_reference",
    )
    assert library.sources["Janssens1991"].role == "numeric_reference"
    assert library.sources["EC_96_603"].role == "classification_only"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: raw["cards"][0].update({"parameters": {"fuel_load_kg_m2": 1}}),
            "contains unknown keys",
        ),
        (
            lambda raw: raw["cards"][0].update({"condition": "dry"}),
            "contains unknown keys",
        ),
        (
            lambda raw: raw["cards"][0].update({"source_ids": ["missing"]}),
            "unknown sources",
        ),
        (
            lambda raw: raw["cards"][0].update(
                {"evidence_status": "reference_only", "reference_profile_ids": []}
            ),
            "needs reference_profile_ids",
        ),
        (
            lambda raw: raw["cards"].pop(),
            "missing material classes",
        ),
        (
            lambda raw: raw.update({"production_ready": True}),
            "production_ready false",
        ),
        (
            lambda raw: raw["sources"]["EC_96_603"].update({"role": "unverified"}),
            "must be one of",
        ),
    ],
)
def test_catalog_rejects_untraceable_card_changes(mutation, message: str | None) -> None:
    raw = deepcopy(_raw_catalog())
    mutation(raw)
    with pytest.raises((TypeError, ValueError), match=message):
        parse_material_card_library(raw)


def test_reference_card_requires_a_source_and_profile() -> None:
    raw = _raw_catalog()
    raw["cards"][1]["source_ids"] = []
    with pytest.raises(ValueError, match="reference_only card needs source_ids"):
        parse_material_card_library(raw)


def test_card_path_is_resolved() -> None:
    library = load_material_card_library(str(CATALOG))
    assert library.path == CATALOG.resolve()
