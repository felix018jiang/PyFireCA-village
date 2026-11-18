"""Use registered independent integer cuts to verify the full-capacity relaxation."""

import argparse
import json
import tempfile
from pathlib import Path

from audit_heritage_thermal_optimum import require, verify, verify_seal

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def run(energy_path, geometric_path, margin_path, earlier_audit):
    base = verify_seal(energy_path)
    sensitivity = read_json(margin_path)
    require(sensitivity.pop("receipt_sha256") == content_hash(sensitivity), "sensitivity seal")
    parent = read_json(earlier_audit)
    require(parent.pop("receipt_sha256") == content_hash(parent), "parent audit seal")
    root = Path(__file__).resolve().parent
    require(
        sensitivity["registered_energy_sha256"] == file_sha256(energy_path)
        and sensitivity["parent_sha256"] == file_sha256(geometric_path)
        and sensitivity["method_sha256"]
        == file_sha256(root / "check_heritage_thermal_margin_sensitivity.py")
        and parent["source_certificate_file_sha256"] == file_sha256(energy_path)
        and parent["audit_source_sha256"]
        == file_sha256(root / "audit_heritage_thermal_optimum.py"),
        "bound parent, margin, or immutable audit source differs",
    )
    row = next(r for r in sensitivity["margins"] if r["allowed_fraction"] == 1.0)
    # The independent auditor's input contract uses the original model implementation digest.
    # This derived evaluator input explicitly binds the separate sensitivity method.
    bound = {
        "schema_version": "derived-independent-dose-auditor-input-1",
        "village": base["village"],
        "geometric_certificate_sha256": base["geometric_certificate_sha256"],
        "source_sha256": base["source_sha256"],
        "sensitivity_method_sha256": sensitivity["method_sha256"],
        "sensitivity_receipt_file_sha256": file_sha256(margin_path),
        "dry_energy_capacity_reserved_fraction": 1 - row["allowed_fraction"],
        "roof_actions": row["roof_actions"],
        "ground_parent_actions": row["ground_parent_actions"],
        "construction_area_m2": row["cost_m2"],
        "worst_aggregate_fraction_of_dry_ignition_energy": row["max_recomputed_fraction"],
    }
    bound["certificate_sha256"] = content_hash(bound)
    with tempfile.TemporaryDirectory(prefix="thermal-relaxed-input-") as temp:
        input_path = Path(temp) / "threshold-1.json"
        input_path.write_text(json.dumps(bound, sort_keys=True, allow_nan=False))
        verified = verify(input_path, geometric_path, 300.0)
    # The old schema hardcodes "99" in a label. Interpret the computed dose directly.
    verified.pop("all_receptors_below_99_percent_dry_capacity")
    require(
        verified["greatest_recomputed_dose_fraction"] < 1.0
        and abs(verified["C_m2"] - parent["C_m2"]) < 1e-8,
        "strict feasible upper cost or prior reference differs",
    )
    a, b = set(base["ground_parent_actions"]), set(row["ground_parent_actions"])
    return {
        "schema_version": "strict-dose-optimality-via-closed-relaxation-1",
        "village": base["village"],
        "audited_closed_threshold": 1.0,
        "strict_threshold_met_by_this_incumbent": True,
        "original_closed_threshold": 1 - base["dry_energy_capacity_reserved_fraction"],
        "original_independent_receipt_sha256": file_sha256(earlier_audit),
        "registered_geometry_receipt_sha256": file_sha256(geometric_path),
        "registered_energy_receipt_sha256": file_sha256(energy_path),
        "margin_experiment_sha256": file_sha256(margin_path),
        "independent_integer_auditor_sha256": parent["audit_source_sha256"],
        "derived_auditor_input_seal": bound["certificate_sha256"],
        "registered_plans_changed": False,
        "observations_used_for_equality": 0,
        "closed_relaxation_verification": verified,
        "old_and_new_same_roof_actions": set(base["roof_actions"]) == set(row["roof_actions"]),
        "exchanged_ground_cells_each_way": [len(a - b), len(b - a)],
        "equal_total_cost_proves_equal_village_fire_loss": False,
        "strict_threshold_minimum_proved_for_registered_equation_and_geometry_tables": verified[
            "cheaper_discrete_solutions_excluded"
        ]
        and verified["greatest_recomputed_dose_fraction"] < 1,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("village", choices=("batuan", "pingyan"))
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    r = Path("docs/results")
    out = run(
        r / f"heritage-thermal-clearance-{a.village}-20261111.json",
        r / f"heritage-core-separation-{a.village}-20261111.json",
        r / f"heritage-thermal-margin-{a.village}-20261111.json",
        r / f"heritage-thermal-clearance-{a.village}-independent-20261111.json",
    )
    out["wrapper_source_sha256"] = file_sha256(__file__)
    out["receipt_sha256"] = content_hash(out)
    with a.output.open("x") as h:
        json.dump(out, h, indent=2, allow_nan=False)
        h.write("\n")


if __name__ == "__main__":
    main()
