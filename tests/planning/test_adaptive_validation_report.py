"""A zero-house result must not conceal worse bridge exposure at longer time."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("bridges,area,worse", [(1, 12.5, True), (0, 0, False)])
def test_validation_table_reports_all_structures(monkeypatch, bridges, area, worse):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "adaptive_validation_renderer", ROOT / "scripts/finalize_batuan_adaptive_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def metrics(b=0, a=0):
        return {
            "metrics": {
                "ignited_house_count": 0,
                "ignited_bridge_count": b,
                "ignited_building_area_m2": a,
            }
        }

    section = module.validation_section(
        {
            "protocol": {"training": [{"horizon_s": 3600}]},
            "validation": {
                "legacy_outer_proxy": {"held_out": metrics(), "extended": metrics()},
                "adaptive_outer_proxy": {"held_out": metrics(), "extended": metrics(bridges, area)},
            },
        }
    )
    assert "留出桥" in section and "延时桥" in section
    assert "受热但尚未引燃" in section
    assert ("高于原单带" in section) is worse
    assert f"{area:.2f}" in section
