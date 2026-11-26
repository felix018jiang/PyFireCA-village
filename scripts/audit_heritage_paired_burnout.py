"""Compare matched terminal burned patches across feasible heritage actions."""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
from pathlib import Path

import numpy as np
from summarize_heritage_joint_pareto import content_hash, file_sha256, read_json


def verified_bytes(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Changed native artifact: {path}")
    return data


def patch_observation(case, partition):
    binding = case["binding"]
    if case["status"] != "passed" or not binding.get("native_certificate_sha256"):
        raise ValueError("A paired comparison requires a verified closed case")
    path = Path(binding["manifest_path"])
    manifest = json.loads(verified_bytes(path, binding["manifest_sha256"]))
    files = manifest["files_sha256"]

    def artifact(name):
        return verified_bytes(path.parent / name, files[name])

    with np.load(io.BytesIO(artifact("final-state.npz")), allow_pickle=False) as state:
        clock = state["burnout_time_s"]
        burned = np.isfinite(clock)
        if np.any(clock[burned] > float(state["time_s"])) or np.any(clock[burned] < 0):
            raise ValueError("Saved burnout clock outside terminal observation")
    with np.load(io.BytesIO(artifact("loss-basis.npz")), allow_pickle=False) as basis:
        area = basis["baseline_covered_area_m2"]
        retained_area = basis["post_intervention_covered_area_m2"]
        expected = partition["baseline_covered_area"]
        if (
            list(area.shape) != expected["shape"]
            or str(area.dtype) != expected["dtype"]
            or hashlib.sha256(area.tobytes()).hexdigest() != expected["bytes_sha256"]
            or area.shape != burned.shape
            or not np.array_equal(area[burned], retained_area[burned])
            or not np.all(retained_area[burned] > 0)
            or float(retained_area[burned].sum()) != case["observation"]["A_burned_out_m2"]
        ):
            raise ValueError("Burned mask differs from saved whole-domain loss or partition")
    loss = json.loads(artifact("fire-loss.json"))["physical_remaining_roof_annotation_counts"]
    per_owner = sorted(
        (row["building_id"], row["burned_out_area_m2"]) for row in loss["per_original_owner"]
    )
    return {
        "mask": burned,
        "owner_profile": content_hash(per_owner),
        "unrepresented": tuple(sorted(loss["unrepresented_original_ids"])),
        "source": content_hash(binding["native_actual_source"]),
        "manifest_sha256": binding["manifest_sha256"],
    }


def audit(path):
    archive = read_json(path)
    digest = archive.pop("composition_receipt_sha256")
    if content_hash(archive) != digest:
        raise ValueError("Case-composition receipt integrity check failed")
    certificate = archive["physical_equivalence_certificate"]
    partitions = certificate["verified_saved_baseline_partitions"]
    scenarios = certificate["scenario_ids"]
    if set(scenarios) != set(partitions):
        raise ValueError("Paired analysis requires common physical partitions for every scenario")
    observations, costs, summaries = {}, {}, []
    for plan in archive["archive"]["fully_evaluated_feasible_plans"]:
        key = plan["plan_id"]
        costs[key] = plan["cleared_area_m2"]
        observations[key] = {}
        for scenario in scenarios:
            records = plan["case_composition_evidence"][scenario]
            actual = [c for c in records["original_native_cases"] if c["binding"] is not None]
            if records["status"] != "passed" or not actual:
                raise ValueError("Plan lacks a closed native case")
            # The sealed compositor checks every duplicate for state and loss identity.
            observations[key][scenario] = patch_observation(actual[0], partitions[scenario])
        summaries.append(
            {
                "action": key,
                "C_m2": costs[key],
                "N_original_roof_owner_bounds": plan["worst_case"]["N_burned_out"],
                "A_burned_out_m2": plan["worst_case"]["A_burned_out_m2"],
                "closed_cases": len(scenarios),
                "native_manifest_sha256s": sorted(
                    m["manifest_sha256"] for m in observations[key].values()
                ),
            }
        )
    pairs = []
    for left, right in itertools.combinations(sorted(costs, key=lambda k: (costs[k], k)), 2):
        matched = equal_mask = equal_owners = contained = 0
        for scenario in scenarios:
            a, b = observations[left][scenario], observations[right][scenario]
            if a["unrepresented"] != b["unrepresented"]:
                raise ValueError("Original-owner inventory scope changed")
            matched += a["source"] == b["source"]
            equal_mask += np.array_equal(a["mask"], b["mask"])
            contained += not np.any(a["mask"] & ~b["mask"])
            equal_owners += a["owner_profile"] == b["owner_profile"]
        count = len(scenarios)
        pairs.append(
            {
                "left": left,
                "right": right,
                "left_C_m2": costs[left],
                "right_C_m2": costs[right],
                "same_source_observations": matched,
                "exactly_equal_burned_patch_masks": equal_mask,
                "exactly_equal_per_original_owner_loss_profiles": equal_owners,
                "left_burned_mask_subset_of_right": contained,
                "strictly_cheaper_with_identical_paired_model_loss": (
                    costs[left] < costs[right] and matched == equal_mask == equal_owners == count
                ),
            }
        )
    return {
        "schema_version": "heritage-paired-terminal-burned-patches-1",
        "input_composition": str(path.resolve()),
        "input_composition_file_sha256": file_sha256(path),
        "input_composition_seal": digest,
        "analysis_source_sha256": file_sha256(__file__),
        "temporal_mode": archive["temporal_mode"],
        "scenario_count": len(scenarios),
        "plans": summaries,
        "pairs": pairs,
        "new_CA_checks": 0,
        "interpretation": (
            "Equal masks on the same original mesh imply identical represented burned patches "
            "and identical ownership outcomes under every common patch-to-owner assignment. "
            "Unrepresented roof owners retain their original unknown scope. "
            "Only declared, fully closed scenarios enter the comparison."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("composition", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = audit(args.composition)
    report["audit_sha256"] = content_hash(report)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
