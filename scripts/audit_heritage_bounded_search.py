"""Audit bounded coverage and optionally replay a native failure plus cold removal."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import tempfile
import zipfile
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def audit(root, *, native=False, output=None):
    root = Path(root).resolve()
    sys.path.insert(0, str(root / "source-snapshot/src"))
    import numpy as np

    from pyfireca.planning.building_search import ProtectionCheck, exact_demolition_search
    from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
    from pyfireca.planning.engines.village import request_fingerprint
    from pyfireca.planning.failure_cuts import FailureCutStore
    from pyfireca.planning.heritage import content_hash
    from pyfireca.planning.strategy_io import file_sha256, write_json
    from pyfireca.planning.study_io import load_study
    from pyfireca.village.core_validation_v3 import check_final
    from pyfireca.village.flame_contact_v3 import FlameContactOperator, FlameContactParameters
    from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
    from pyfireca.village.workflow_v3 import load_v3_case_bundle

    experiment, result = read(root / "experiment.json"), read(root / "search-result.json")
    problem, config = experiment["problem"], experiment["config"]
    assert content_hash(problem) == result["problem_sha256"] == experiment["problem_sha256"]
    for path, signature in experiment["source_sha256"].items():
        assert content_hash((root / "source-snapshot" / path).read_text()) == signature
    for path, signature in experiment["source_references_sha256"].items():
        assert file_sha256(path) == signature
    study = Path(config["material_binding_path"]).parent / config["study_path"]
    bundle, transport, inventory = load_study(study)
    assert request_fingerprint(bundle.request) == problem["case_sha256"]
    assert inventory.sha256 == problem["inventory_sha256"]
    assert set(config["candidate_ids"]) == set(inventory.candidate_ids)
    areas = {i: inventory.by_id[i].geometry.area for i in config["candidate_ids"]}
    cache, cuts = {}, FailureCutStore(experiment["problem_sha256"], areas)
    rows, worst_energy = [], 0.0
    for path in sorted(root.glob("evaluations/*/check.json")):
        row, evaluation = read(path), read(path.with_name("evaluation.json"))
        assert row["problem_sha256"] == experiment["problem_sha256"]
        assert row["demolished_ids"] == sorted(evaluation["intervention"]["demolished_ids"])
        evidence, diagnostics = row["evidence"], evaluation["diagnostics"]
        assert evidence["core_outcomes"] == evaluation["metrics"]["core_outcomes"]
        assert evidence["evaluation_sha256"] == evaluation["evaluation_sha256"]
        assert diagnostics["source_verified"] and diagnostics["parent_case_unchanged"]
        assert not diagnostics["invalid_reasons"]
        error = max(diagnostics["energy_closure_errors_J"].values())
        worst_energy = max(worst_energy, error)
        assert error <= diagnostics["energy_tolerance_J"]
        assert np.isclose(evidence["source_budget"]["covered_area_m2"], 25, atol=1e-6, rtol=1e-8)
        assert {c["building_id"] for c in evidence["core_outcomes"]} == {
            t.building_id for t in inventory.targets
        }
        assert row["status"] in {"passed", "failed"}
        if row["status"] == "passed":
            assert evidence["reached_horizon"] and all(
                not c["ignited"] for c in evidence["core_outcomes"]
            )
        else:
            assert any(c["ignited"] for c in evidence["core_outcomes"])
        ids, scenario = tuple(row["demolished_ids"]), row["scenario_id"]
        check = ProtectionCheck(row["status"], evidence)
        cache[experiment["problem_sha256"], ids, scenario] = check
        if check.status == "failed":
            cuts.learn(ids, scenario, check)
        rows.append(row)
    assert len(rows) <= config["max_total_checks"]
    scenarios = tuple(f"{f}-h{s:g}" for f in problem["fires"] for s in problem["scales_m"])

    def no_new_ca(*_):
        raise AssertionError("read-only proof replay must not execute CA")

    replay = exact_demolition_search(
        areas,
        scenarios,
        no_new_ca,
        problem_sha256=experiment["problem_sha256"],
        initial_ids=result["best_ids"],
        cache=dict(cache),
        max_new_checks=0,
        failure_cuts=cuts,
        maximum_demolitions=config["maximum_demolitions"],
        prioritize_failures=True,
    )
    if result["best_ids"] is not None:
        assert replay["best_ids"] == result["best_ids"]
    assert replay["minimum_count_lower_bound"] >= result["minimum_count_lower_bound"]
    count_certificate = None
    if result["best_ids"] is not None and len(result["best_ids"]) <= 3:
        failures = {tuple(r["demolished_ids"]) for r in rows if r["status"] == "failed"}
        rules = [
            (set(c["required_demolished_ids"]), set(c["required_retained_ids"]))
            for c in cuts.to_mapping()
        ]
        verified = {}
        for count in range(len(result["best_ids"])):
            direct = via_cut = unproved = 0
            for ids in itertools.combinations(sorted(areas), count):
                selected = set(ids)
                if ids in failures:
                    direct += 1
                elif any(a <= selected and not h & selected for a, h in rules):
                    via_cut += 1
                else:
                    unproved += 1
            verified[str(count)] = {
                "direct_failed": direct,
                "proved_by_valid_cut": via_cut,
                "unproved": unproved,
            }
        count_certificate = {
            "all_smaller_counts_excluded": all(v["unproved"] == 0 for v in verified.values()),
            "layers": verified,
            "CA_checks": 0,
        }
    area_interval = None
    if result["best_ids"] is not None and replay["minimum_count_lower_bound"] == len(
        result["best_ids"]
    ):
        pending = [
            r["objective"][1]
            for r in replay["trace"]
            if r["status"] == "unknown" and r["objective"][0] == len(result["best_ids"])
        ]
        area_interval = {
            "optimal_count": len(result["best_ids"]),
            "lower_m2": result["best_objective"][1]
            if replay["optimality_proven"]
            else min(pending),
            "upper_m2": result["best_objective"][1],
            "basis": "first_unresolved_leaf_in_admissible_best_first_area_order",
        }
    restoration = None
    if native:
        receipt = read(root / "native-witness.json")
        folder = root / receipt["folder"]
        assert file_sha256(folder / "native.zip") == receipt["archive_sha256"]
        witness = read(folder / "check.json")
        with tempfile.TemporaryDirectory(prefix="pyfireca-bounded-audit-") as directory:
            native_dir = Path(directory)
            with zipfile.ZipFile(folder / "native.zip") as archive:
                archive.extractall(native_dir)
            for path, digest in receipt["members_sha256"].items():
                assert file_sha256(native_dir / path) == digest
            saved_bundle = load_v3_case_bundle(native_dir / "node-storage-not-map")
            assert (
                request_fingerprint(saved_bundle.request)
                == read(native_dir / "evaluation.json")["diagnostics"]["physical_request_sha256"]
            )
            parameters = FlameContactParameters.from_mapping(read(native_dir / "contact.json"))
            with np.load(native_dir / "contact.npz") as arrays:
                contact = FlameContactOperator(
                    arrays["neighbors"],
                    arrays["geometry_fraction"],
                    parameters,
                    saved_bundle.request.parameters,
                )
            layout = read(native_dir / "layout.json")
            core_ids = {t.building_id for t in inventory.targets}
            nodes = [
                r["node"] for r in layout["roof_components"] if set(r["building_ids"]) & core_ids
            ]
            restored = _run_resolved_v3_simulation(
                saved_bundle.request,
                contact=contact,
                retain_snapshots=False,
                stop_when=lambda s: bool(np.isfinite(s.ignition_time_s.ravel()[nodes]).any()),
            )
            check_final(saved_bundle.request, restored)
            with np.load(native_dir / "final-state.npz") as expected:
                for name in restored.final_state.__dataclass_fields__:
                    np.testing.assert_array_equal(
                        getattr(restored.final_state, name), expected[name]
                    )
                np.testing.assert_array_equal(restored.ignition_cause, expected["ignition_cause"])
            cert = witness["evidence"]["failure_certificate"]
            room = config["maximum_demolitions"] - len(witness["demolished_ids"])
            extra = cert["extra_removal_eligible_ids"][: min(3, room)]
            extra_result = {}
            if extra:

                def collect(ids, scenario, check, evaluation, changed_layout, physical_result):
                    extra_result.update(
                        check=check,
                        record=evaluation,
                        layout=changed_layout,
                        result=physical_result,
                    )

                kwargs = (
                    {"core_overlap_policy": problem["core_overlap_policy"]}
                    if "core_overlap_policy" in problem
                    else {}
                )
                oracle = RoofDemolitionOracle(
                    bundle,
                    transport,
                    inventory,
                    candidate_ids=tuple(config["candidate_ids"]),
                    fires=[
                        {"fire_id": label, "protocol": item["protocol"]}
                        for label, item in problem["fires"].items()
                    ],
                    code_version=problem["code_version"],
                    horizon_s=config["horizon_s"],
                    scales_m=tuple(config["roof_scales_m"]),
                    quadrature_spacing_m=config["quadrature_spacing_m"],
                    max_nodes=config["max_nodes"],
                    max_radiation_edges=config["max_radiation_edges"],
                    refinement_ids=config["refinement_ids"],
                    maximum_demolitions=config["maximum_demolitions"],
                    on_evaluation=collect,
                    **kwargs,
                )
                assert oracle.problem_sha256 == experiment["problem_sha256"]
                ids = tuple(sorted(set(witness["demolished_ids"]) | set(extra)))
                check = oracle(ids, witness["scenario_id"])
                assert check.status == "failed"
                assert check.evidence["core_outcomes"] == witness["evidence"]["core_outcomes"]
                assert check.evidence["source_id"] == witness["evidence"]["source_id"]
                extra_result_state = extra_result["result"].final_state
                keep = np.ones(saved_bundle.request.grid.width, dtype=bool)
                for patch in layout["roof_components"]:
                    if set(patch["building_ids"]) and set(patch["building_ids"]) <= set(ids):
                        keep[patch["node"]] = False
                for name in restored.final_state.__dataclass_fields__:
                    a, b = getattr(restored.final_state, name), getattr(extra_result_state, name)
                    if isinstance(a, np.ndarray):
                        np.testing.assert_array_equal(a.ravel()[keep], b.ravel()[keep])
                    else:
                        assert a == b
            restoration = {
                "all_native_arrays_and_ignition_causes_equal": True,
                "certified_extra_demolitions": extra,
                "all_retained_state_arrays_equal_after_extra_demolition": bool(extra),
                "new_validation_CA_checks": 1 + int(bool(extra)),
            }
    report = {
        "schema_version": "heritage-bounded-search-audit-1",
        "problem_sha256": experiment["problem_sha256"],
        "audit_passed": True,
        "candidate_count": len(areas),
        "physical_checks": len(rows),
        "best_ids": result["best_ids"],
        "search_bounds": result["bounds"],
        "read_only_replay_minimum_count_lower_bound": replay["minimum_count_lower_bound"],
        "read_only_replay_new_CA_checks": 0,
        "failure_certificate_count": len(cuts.cuts),
        "worst_energy_error_J": worst_energy,
        "fully_verified_best_core_outcomes": [
            r for r in rows if r["demolished_ids"] == result["best_ids"]
        ],
        "native_restoration_and_cut_check": restoration,
        "replayed_layers": replay["layers"],
        "optimality_proven": replay["optimality_proven"],
        "all_original_candidate_ids_preserved": True,
        "independent_minimum_count_certificate": count_certificate,
        "certified_area_interval": area_interval,
    }
    write_json(root / "audit.json" if output is None else output, report)
    print(
        {
            k: report[k]
            for k in (
                "audit_passed",
                "candidate_count",
                "physical_checks",
                "best_ids",
                "read_only_replay_minimum_count_lower_bound",
                "failure_certificate_count",
            )
        },
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit(args.root, native=args.native, output=args.output)
