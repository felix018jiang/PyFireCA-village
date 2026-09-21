from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pyfireca.cli import main
from pyfireca.village.materials_v3 import load_material_catalog_v3, parse_material_catalog_v3

CATALOG = Path(__file__).parents[2] / "examples/village/material-catalog-3.gaoyou-evidence.yml"


def _raw_catalog() -> dict:
    return yaml.safe_load(CATALOG.read_text(encoding="utf-8"))


def test_gaoyou_evidence_catalog_has_parameter_provenance_but_no_village_defaults() -> None:
    catalog = load_material_catalog_v3(CATALOG)

    assert len(catalog.materials) == 22
    assert catalog.evidenced_parameter_count == 10
    assert catalog.parameter_complete_village_materials == ()
    assert not catalog.production_ready
    assert catalog.by_id["wood_traditional"].parameters["ignition_temperature_K"] is None
    reference = catalog.by_id["wood_douglas_ovendry_reference"]
    assert reference.scope == "reference_only"
    assert reference.parameters["ignition_temperature_K"].value == 623
    assert reference.parameters["ignition_temperature_K"].method == "measured"
    assert reference.parameters["thermal_inertia_J2_m4_K2_s"].value == 158_000
    assert reference.parameters["absorptivity"].method == "assumed"
    pine = catalog.by_id["wood_southern_pine_reference"]
    assert pine.parameters["heat_of_combustion_J_kg"].value == 16_260_000
    bamboo = catalog.by_id["bamboo_laminated_reference"]
    assert bamboo.parameters["moisture_content_dry_basis"].value == 0.105
    assert bamboo.parameters["critical_heat_flux_W_m2"].value == 6_000
    assert bamboo.parameters["ignition_temperature_K"].value == pytest.approx(570.15)
    assert bamboo.parameters["peak_hrrpua_W_m2"].value == 235_000
    # Xu et al. report TRP, which includes the ignition temperature rise; it is
    # not the k*rho*c quantity required by this field.
    assert bamboo.parameters["thermal_inertia_J2_m4_K2_s"] is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: raw["materials"][19]["parameters"]["ignition_temperature_K"].update(
                {"source_id": "missing"}
            ),
            "unknown source",
        ),
        (
            lambda raw: raw["materials"][19]["parameters"]["ignition_temperature_K"].update(
                {"unit": "degC"}
            ),
            "requires unit K",
        ),
        (
            lambda raw: raw["materials"][19]["parameters"]["ignition_temperature_K"].update(
                {"locator": ""}
            ),
            "locator must be non-empty",
        ),
        (
            lambda raw: raw["sources"]["Janssens1991"].update({"kind": "vendor_web_page"}),
            "unsupported source kind",
        ),
        (lambda raw: raw.update({"production_ready": True}), "cannot claim production readiness"),
    ],
)
def test_catalog_rejects_untraceable_or_overclaimed_values(mutation, message: str) -> None:
    raw = deepcopy(_raw_catalog())
    mutation(raw)
    with pytest.raises(ValueError, match=message):
        parse_material_catalog_v3(raw)


def test_catalog_cli_reports_missing_village_parameters(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["village", "validate-materials-v3", str(CATALOG)]) == 0
    output = capsys.readouterr().out
    assert "parameter-complete: 0" in output
    assert "sourced parameters: 10" in output
    assert "wood_traditional: missing" in output
