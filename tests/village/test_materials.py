from __future__ import annotations

import pytest

from pyfireca.village.materials import parse_material_catalog


def _catalog() -> dict:
    return {
        "schema_version": "material-catalog-1",
        "library_id": "fixture-v1",
        "materials": [
            {
                "id": "demo_wood",
                "label": "Synthetic wood",
                "role": "building_fuel",
                "burnable": True,
                "evidence": "synthetic",
                "source_id": "test-fixture",
                "parameters": {
                    "fuel_load_kg_m2": 2.0,
                    "heat_of_combustion_J_kg": 1_000_000.0,
                    "mass_loss_flux_kg_m2_s": 0.1,
                    "active_area_ratio": 1.0,
                    "emission_fraction": 0.2,
                    "absorptivity": 0.8,
                    "ignition_dose_J_m2": 5000.0,
                    "exposure_decay_s": 10.0,
                },
            },
            {
                "id": "stone",
                "label": "Stone",
                "role": "inert",
                "burnable": False,
                "evidence": "synthetic",
                "source_id": "test-fixture",
                "parameters": {},
            },
        ],
    }


def test_parse_catalog_keeps_explicit_material_semantics() -> None:
    catalog = parse_material_catalog(_catalog())
    assert catalog.library_id == "fixture-v1"
    assert catalog.by_id["demo_wood"].parameters["fuel_load_kg_m2"] == 2.0
    assert not catalog.by_id["stone"].burnable


def test_research_mode_rejects_synthetic_burnable_parameters() -> None:
    with pytest.raises(ValueError, match="research mode rejects synthetic"):
        parse_material_catalog(_catalog(), mode="research")


@pytest.mark.parametrize("material_id", ["unknown", ""])
def test_reserved_or_empty_material_ids_are_rejected(material_id: str) -> None:
    data = _catalog()
    data["materials"][0]["id"] = material_id
    with pytest.raises(ValueError):
        parse_material_catalog(data)


def test_unknown_parameter_is_rejected() -> None:
    data = _catalog()
    data["materials"][0]["parameters"]["mystery"] = 2.0
    with pytest.raises(ValueError, match="unknown parameters"):
        parse_material_catalog(data)
