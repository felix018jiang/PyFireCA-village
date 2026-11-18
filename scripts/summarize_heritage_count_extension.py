"""Audit a frozen cardinality continuation and export its protection evidence."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

from extend_heritage_bounded_search import read, sha, write


def restore_witness(root, experiment):
    """One new CA only, with a separate reusable completion receipt."""
    import numpy as np

    from pyfireca.planning.engines.village import request_fingerprint
    from pyfireca.village.core_validation_v3 import check_final
    from pyfireca.village.flame_contact_v3 import FlameContactOperator, FlameContactParameters
    from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    output = root / "native-restoration.json"
    receipt = read(root / "native-witness.json")
    folder = root / receipt["folder"]
    archive_sha = sha(folder / "native.zip")
    assert archive_sha == receipt["archive_sha256"]
    if output.exists():
        result = read(output)
        assert result["native_zip_sha256"] == archive_sha and result["passed"]
        return result
    with tempfile.TemporaryDirectory(prefix="pyfireca-count-restoration-") as directory:
        native = Path(directory)
        with zipfile.ZipFile(folder / "native.zip") as archive:
            archive.extractall(native)
        assert all(
            sha(native / path) == digest for path, digest in receipt["members_sha256"].items()
        )
        bundle = load_v3_case_bundle(native / "node-storage-not-map")
        record = read(native / "evaluation.json")
        assert (
            request_fingerprint(bundle.request) == record["diagnostics"]["physical_request_sha256"]
        )
        parameters = FlameContactParameters.from_mapping(read(native / "contact.json"))
        with np.load(native / "contact.npz") as data:
            contact = FlameContactOperator(
                data["neighbors"], data["geometry_fraction"], parameters, bundle.request.parameters
            )
        core_ids = {t["building_id"] for t in experiment["study"]["targets"]}
        nodes = [
            patch["node"]
            for patch in read(native / "layout.json")["roof_components"]
            if set(patch["building_ids"]) & core_ids
        ]
        attempt = root / "native-restoration-attempt.json"
        if attempt.exists():
            raise RuntimeError(
                "native audit attempt already spent; inspect its log before recovery"
            )
        write(attempt, {"native_zip_sha256": archive_sha, "reserved_CA_attempts": 1})
        restored = _run_resolved_v3_simulation(
            bundle.request,
            contact=contact,
            retain_snapshots=False,
            stop_when=lambda state: bool(np.isfinite(state.ignition_time_s.ravel()[nodes]).any()),
        )
        check_final(bundle.request, restored)
        with np.load(native / "final-state.npz") as expected:
            for field in restored.final_state.__dataclass_fields__:
                np.testing.assert_array_equal(getattr(restored.final_state, field), expected[field])
            np.testing.assert_array_equal(restored.ignition_cause, expected["ignition_cause"])
    result = {
        "passed": True,
        "native_zip_sha256": archive_sha,
        "all_state_arrays_and_ignition_causes_equal": True,
        "new_validation_CA_checks": 1,
    }
    write(output, result)
    return result


def summarize(root, output, *, native=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    frozen = root / "source-snapshot"
    sys.path.insert(0, str(frozen / "src"))
    from summarize_heritage_bounded_villages import checked_rows, pair_scales

    from pyfireca.planning.building_search import ProtectionCheck
    from pyfireca.planning.failure_cuts import FailureCutStore
    from pyfireca.planning.heritage import content_hash

    experiment, rows = checked_rows(root)
    receipt, result = read(root / "continuation.json"), read(root / "search-result.json")
    assert sha(frozen / "scripts/extend_heritage_bounded_search.py") == receipt["driver_sha256"]
    parent = Path(receipt["parent_root"])
    assert all(
        sha(parent / name) == digest for name, digest in receipt["parent_files_sha256"].items()
    )
    assert (
        result["problem_sha256"]
        == experiment["problem_sha256"]
        == receipt["config"]["problem_sha256"]
    )
    assert result["all_original_candidate_ids_preserved"]
    assert result["candidate_ids"] == sorted(experiment["config"]["candidate_ids"])
    source = read(root / "inventory.json")
    # Use the original inventory's geometry areas, independently of reported objective fields.
    from pyfireca.planning.study_io import load_study

    original = experiment["config"]
    bundle, _, inventory = load_study(
        Path(original["material_binding_path"]).parent / original["study_path"]
    )
    assert content_hash(source) == inventory.sha256
    areas = {i: inventory.by_id[i].geometry.area for i in result["candidate_ids"]}
    cuts, cache = FailureCutStore(result["problem_sha256"], areas), {}
    new_rows = []
    imported = set(receipt["imported_evaluation_folders"])
    for path in sorted(root.glob("evaluations/*/check.json")):
        raw = read(path)
        key = tuple(raw["demolished_ids"]), raw["scenario_id"]
        cache[key] = ProtectionCheck(raw["status"], raw["evidence"])
        if raw["status"] == "failed":
            cuts.learn(*key, cache[key])
        if path.parent.name in imported:
            assert sha(path) == receipt["parent_files_sha256"][str(path.relative_to(root))]
            assert (
                sha(path.with_name("evaluation.json"))
                == receipt["parent_files_sha256"][
                    str(path.with_name("evaluation.json").relative_to(root))
                ]
            )
        else:
            new_rows.append(raw)
    assert len(rows) == result["physical_CA_checks_total"]
    assert len(new_rows) == result["new_checks_total"]
    for count in (2, 3, 4):
        used = sum(len(row["demolished_ids"]) == count for row in new_rows)
        assert used == result["new_checks_by_stage"][str(count)]
        assert used <= receipt["config"]["stage_limits"][str(count)]["new_CA_checks"]
    spec = importlib.util.spec_from_file_location(
        "frozen_count_execution", frozen / "scripts/extend_heritage_bounded_search.py"
    )
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    scenarios = tuple(f"{f}-h{s:g}" for f in experiment["problem"]["fires"] for s in (1, 0.5))
    assert driver.plan_status(tuple(result["best_ids"]), scenarios, cache) == "passed"
    classified_pairs = [
        {
            "demolished_ids": row["demolished_ids"],
            "status": driver.plan_status(tuple(row["demolished_ids"]), scenarios, cache),
        }
        for row in result["pair_frontier"]
    ]
    assert classified_pairs == result["pair_frontier"]
    lower, replayed = 2, []
    if result["pair_frontier_complete"]:
        assert len(classified_pairs) == 17
        if all(row["status"] == "failed" for row in classified_pairs):
            lower = 3
            for layer in result["layers"]:
                # Recompute a claimed closed layer with final cuts and no new physical calls.
                if not layer["all_combinations_failed_or_proved_failed"]:
                    break
                calls = 0

                def guard():
                    nonlocal calls
                    calls += 1
                    if calls > 300000:
                        raise driver.StageLimit("read_only_audit_prefix_limit")

                def no_ca(*_):
                    raise driver.StageLimit("unobserved_physical_plan")

                replay = driver.search_layer(
                    areas,
                    layer["demolished_count"],
                    scenarios,
                    cache,
                    no_ca,
                    cuts,
                    guard,
                    lambda _: None,
                )
                assert replay["all_combinations_failed_or_proved_failed"]
                replayed.append(
                    {
                        "demolished_count": layer["demolished_count"],
                        "bound_calls": calls,
                        "complete": True,
                    }
                )
                lower += 1
    assert lower == result["minimum_count_lower_bound"]
    restoration = None
    if native and (root / "native-witness.json").exists():
        restoration = restore_witness(root, experiment)
    elif native:
        restoration = {
            "passed": True,
            "new_validation_CA_checks": 0,
            "reason": "no_new_failure_witness",
        }
    selected = [row for row in rows if row["demolished_ids"] in ([], result["best_ids"])]
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "heritage-count-extension-audit-1",
        "audit_passed": True,
        "problem_sha256": result["problem_sha256"],
        "problem_identical_to_parent": True,
        "all_parent_evaluation_bytes_preserved": True,
        "all947_candidates_preserved": True,
        "read_only_audit_CA_checks": 0,
        "native_restoration": restoration,
        "parent_CA_checks": result["inherited_checks"],
        "new_search_CA_checks": len(new_rows),
        "new_checks_by_stage": result["new_checks_by_stage"],
        "budget_respected": True,
        "best_ids": result["best_ids"],
        "best_objective": result["best_objective"],
        "minimum_count_proven": result["minimum_count_proven"],
        "optimality_proven": result["optimality_proven"],
        "bounds": result["bounds"],
        "classified_original_pair_frontier": classified_pairs,
        "independently_replayed_closed_layers": replayed,
        "layers": [
            {k: v for k, v in layer.items() if k != "visited_plans"} for layer in result["layers"]
        ],
        "baseline_and_best": pair_scales(selected),
        "all_new_checks": [
            r
            for r in rows
            if any(
                tuple(r["demolished_ids"]) == tuple(n["demolished_ids"])
                and r["scenario_id"] == n["scenario_id"]
                for n in new_rows
            )
        ],
        "scope": (
            "original_design_families;heldout_not_used_for_selection;"
            "early_stopped_other_cores_censored"
        ),
        "source_result_sha256": sha(root / "search-result.json"),
    }
    write(root / "audit.json", report)
    write(output / "summary.json", report)
    flat = []
    for row in rows:
        for core in row["core_outcomes"]:
            flat.append(
                {
                    "demolished_ids": "+".join(row["demolished_ids"]),
                    "scenario_id": row["scenario_id"],
                    "source_id": row["source_id"],
                    "source_replaced": row["source_replaced"],
                    "scale_m": row["scale_m"],
                    "observed_until_s": row["observed_until_s"],
                    "core_id": core["building_id"],
                    "classification": "failed"
                    if core["ignited"]
                    else "protected"
                    if row["reached_horizon"]
                    else "censored",
                    "peak_temperature_K": core["maximum_valid_surface_temperature_K"],
                    "first_ignition_s": core["first_ignition_s"],
                }
            )
    with (output / "core-outcomes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    # Presentation consumes the parent's declared objective, never a new physical setting.
    # The first frozen continuation driver omitted this export-only result field.
    sys.path.insert(0, str(frozen / "scripts"))
    from run_heritage_bounded_search import export_best

    parent_result = read(parent / "search-result.json")
    presentation_result = {**result, "objective_policy": parent_result["objective_policy"]}
    oracle_view = SimpleNamespace(
        problem=experiment["problem"], problem_sha256=result["problem_sha256"], scenarios=scenarios
    )
    triple_cache = {
        (result["problem_sha256"], ids, scenario): check for (ids, scenario), check in cache.items()
    }
    export_best(original, output, bundle, inventory, oracle_view, presentation_result, triple_cache)
    write(
        output / "presentation-receipt.json",
        {
            "new_CA_checks": 0,
            "physical_results_and_snapshot_unchanged": True,
            "raw_result_sha256": sha(root / "search-result.json"),
            "objective_policy_source": str(parent / "search-result.json"),
            "objective_policy": parent_result["objective_policy"],
            "export_metadata_completed_from_frozen_parent": "objective_policy" not in result,
        },
    )
    write(
        output / "audit-receipt.json",
        {
            "auditing_driver_sha256": sha(__file__),
            "frozen_execution_driver_sha256": receipt["driver_sha256"],
            "summary_sha256": sha(output / "summary.json"),
            "new_search_CA_checks": 0,
            "native_validation_CA_checks": 0
            if restoration is None
            else restoration["new_validation_CA_checks"],
        },
    )
    print(
        {
            "audit_passed": True,
            "new_search_CA_checks": len(new_rows),
            "bounds": result["bounds"],
            "best_ids": result["best_ids"],
        },
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    summarize(args.root, args.output, native=args.native)
