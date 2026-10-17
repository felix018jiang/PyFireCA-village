"""Independent cache, protection and deterministic search-recovery checks."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import dump, sha
from run_batuan_joint_search import BASE, ROOT, Oracle, digest, search_mode

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.planning.joint import combined_seed


def audit(output):
    protocol = json.loads((output / "protocol.json").read_text())
    report = json.loads((output / "report.json").read_text())
    oracle = Oracle(output)  # Verify frozen sources and inputs before reading any cache.
    raw = protocol["training"][0]
    scenario = EvaluationScenario(raw["scenario_id"], (Ignition(tuple(raw["point_m"])),), 5400)
    engine = oracle.engine(raw["environment_id"])
    singleton_checks = {}
    for region, mapping in protocol["historical_plans"].items():
        atomic = clearing_plan_from_mapping(mapping)
        a, fa, sa = engine.prepare(scenario, atomic)
        b, fb, sb = engine.prepare(scenario, combined_seed((atomic,)))
        # Geometry union/intersection may introduce floating-point roundoff.
        # Preserve topology exactly and thermal arrays to declared tolerance.
        np.testing.assert_allclose(fa, fb, rtol=0, atol=1e-12)
        for name in a.parameters.__dataclass_fields__:
            np.testing.assert_allclose(
                getattr(a.parameters, name), getattr(b.parameters, name), rtol=1e-12, atol=1e-12
            )
        for name in (
            "burnable_mask",
            "source_mask",
            "target_mask",
            "material_index",
            "building_index",
            "landcover_index",
            "piloted_ignition_mask",
        ):
            np.testing.assert_array_equal(getattr(a, name), getattr(b, name))
        for name in ("source", "target"):
            np.testing.assert_array_equal(
                getattr(a.exposure_edges, name), getattr(b.exposure_edges, name)
            )
        np.testing.assert_allclose(
            a.exposure_edges.weight, b.exposure_edges.weight, rtol=1e-12, atol=1e-12
        )
        assert a.scenario == b.scenario
        singleton_checks[region] = {
            "same_to_declared_tolerance": True,
            "array_rtol": 1e-12,
            "array_atol": 1e-12,
            "bitwise_request_fingerprint_equal": request_fingerprint(a) == request_fingerprint(b),
            "max_clearance_fraction_difference": float(np.max(np.abs(fa - fb))),
            "atomic_area_m2": sa["cleared_area_m2"],
            "joint_singleton_area_m2": sb["cleared_area_m2"],
        }
    if report["selection"]["protocol_sha256"] != sha(output / "protocol.json"):
        raise AssertionError("report selection protocol mismatch")
    cache_counts = {}
    for folder in ["cache", "full-cache"]:
        count = 0
        for path in (output / folder).glob("*.json"):
            raw = json.loads(path.read_text())
            if raw["signature"] != path.stem or digest(raw["record"]) != raw["record_sha256"]:
                raise AssertionError(f"invalid cache: {path}")
            count += 1
        cache_counts[folder] = count
    protected_matches = []
    for row in report["confirmation"]:
        if not row["full_grid"]:
            continue
        other = next(
            x
            for x in report["confirmation"]
            if not x["full_grid"] and x["label"] == row["label"] and x["role"] == row["role"]
        )
        left = {r["scenario_id"]: r for r in row["records"]}
        for r in other["records"]:
            full = left[r["scenario_id"]]
            for x in [r, full]:
                if not x["diagnostics"]["reached_requested_horizon"]:
                    raise AssertionError("final comparison is physically incomplete")
                if x["metrics"]["burning_reached_domain_edge"]:
                    raise AssertionError("final comparison fire touched domain boundary")
            keys = ["ignited_house_count", "ignited_bridge_count"]
            protected_matches.append(
                {
                    "label": row["label"],
                    "scenario_id": r["scenario_id"],
                    "protected_counts_match": all(
                        r["metrics"][k] == full["metrics"][k] for k in keys
                    ),
                }
            )
    recovery = output / "recovery-audit"
    recovery.mkdir(exist_ok=False)
    shutil.copy2(output / "protocol.json", recovery / "protocol.json")
    (recovery / "search-case").symlink_to(output / "search-case", target_is_directory=True)
    (recovery / "cache").symlink_to(output / "cache", target_is_directory=True)
    import pyfireca.planning.engines.village as engine_module

    original = engine_module.VillageEvaluationEngine.evaluate

    def forbid(*args, **kwargs):
        raise AssertionError("recovery attempted a new physical simulation")

    engine_module.VillageEvaluationEngine.evaluate = forbid
    recovered = []
    try:
        for mode in ["outer_proxy", "inner_proxy", "joint"]:
            _, selected = search_mode((str(recovery), mode))
            expected = report["selection"]["selected"][mode]
            a = selected["plan"] if selected else None
            b = expected["plan"] if expected else None
            if a != b:
                raise AssertionError("recovered selection differs")
            actual = json.loads((recovery / (mode + "-search.json")).read_text())
            first = json.loads((output / (mode + "-search.json")).read_text())
            if actual["records"] != first["records"]:
                raise AssertionError("recovered search trajectory differs")
            if actual["physical_solves"] != 0:
                raise AssertionError("recovery made a new CA solve")
            recovered.append(mode)
    finally:
        engine_module.VillageEvaluationEngine.evaluate = original
    pair_audits = []
    for revision in ["r2", "r3"]:
        root = ROOT / f"runs/research/batuan-building-pairs-20261017-{revision}"
        p = json.loads((root / "protocol.json").read_text())
        r = json.loads((root / "report.json").read_text())
        for name, h in p["source_hashes"].items():
            if sha(root / "source_snapshot" / name) != h:
                raise AssertionError("pair source snapshot changed")
        for name, h in p["input_hashes"].items():
            if sha(name) != h:
                raise AssertionError("pair input changed")
        if sha(root / "protocol.json") != r["protocol_sha256"]:
            raise AssertionError("pair protocol mismatch")
        if not r["all_full_horizon"] or r["any_guard_ignition"]:
            raise AssertionError("invalid pair horizon/guard")
        for record in r["records"]:
            if record != json.loads((root / "records" / (record["key"] + ".json")).read_text()):
                raise AssertionError("pair report/cache mismatch")
        pair_audits.append(
            {"revision": revision, "records": len(r["records"]), "source_snapshot_verified": True}
        )
    result = {
        "cache_counts": cache_counts,
        "scoped_singleton_checks": singleton_checks,
        "source_files": len(protocol["source_hashes"]),
        "input_files": len(protocol["input_hashes"]),
        "protected_crop_full_comparison": protected_matches,
        "all_protected_counts_match": all(x["protected_counts_match"] for x in protected_matches),
        "recovered_modes": recovered,
        "new_physical_solves_during_recovery": 0,
        "pair_audits": pair_audits,
        "base_case": str(BASE),
        "field_accuracy_claimed": False,
    }
    dump(output / "audit.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    audit(parser.parse_args().output.resolve())
