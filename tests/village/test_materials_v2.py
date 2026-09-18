from __future__ import annotations

from pyfireca.village.materials_v2 import (
    V2_CONSTRUCTION_PARAMETERS,
    V2_PARAMETER_NAMES,
    parse_material_catalog_v2,
)


def _catalog() -> dict:
    parameters = {name: None for name in V2_PARAMETER_NAMES}
    parameters.update(
        {
            "critical_heat_flux_W_m2": 13_000,
            "ignition_temperature_K": 623,
            "thermal_inertia_J2_m4_K2_s": 158_000,
            "absorptivity": 0.88,
        }
    )
    return {
        "schema_version": "material-catalog-2",
        "model": "effective_exposure_ca_v2",
        "production_ready": False,
        "catalog_status": "evidence_seed_not_calibrated",
        "heat_of_combustion_basis": "dry_mass_loss_effective",
        "thermal_inertia_unit": "J^2/(m^4*K^2*s)",
        "missing_value_policy": "error_for_required_parameters",
        "materials": [
            {
                "id": "wood_reference",
                "label": "Reference wood",
                "role": "reference_only",
                "burnable": True,
                "evidence_level": "measured",
                "sources": ["source"],
                "conditions_and_limits": "Small specimen only.",
                "parameters": parameters,
            }
        ],
        "source_registry": {"source": "https://example.invalid/source"},
        "implementation_requirements": {
            "surface_area_basis": "exposed_receptor_area",
            "fuel_area_basis": "ground_projected_area",
            "additional_required_parameters": list(V2_CONSTRUCTION_PARAMETERS),
            "restrictions": ["Do not replace null by zero."],
        },
    }


def test_v2_evidence_catalog_preserves_null_and_reference_only_status() -> None:
    catalog = parse_material_catalog_v2(_catalog())
    material = catalog.by_id["wood_reference"]
    assert material.parameters["fuel_load_kg_m2"] is None
    assert material.parameters["ignition_temperature_K"] == 623.0
    assert catalog.runnable_material_ids == ()
    assert not catalog.production_ready
