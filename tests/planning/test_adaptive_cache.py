"""A resumed optimizer must not reuse results from another scene/protocol."""

import hashlib
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.adaptive import SearchRejected, canonical_plan
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.interventions.clearing import ClearingPath
from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

ROOT = Path(__file__).parents[2]


@pytest.fixture
def script(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "adaptive_entrypoint", ROOT / "scripts/run_batuan_adaptive_search.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Engine:
    def __init__(self):
        self.bundle = SimpleNamespace(request=SimpleNamespace(grid=VillageGridSpec(5, 5, 2, 0, 10)))
        self.case_sha256, self.code_version = "case", "code"
        self.zones = {"z": np.ones((5, 5), dtype=bool)}
        self.structure_types = {}
        self.transport = V3RadiativeTransportParameters(8, 0.5, 0, 0, 0, 0)
        self.calls = 0

    def evaluate(self, scenario, plan):
        self.calls += 1
        return EvaluationResult(
            "test",
            self.case_sha256,
            "eval",
            "fake",
            scenario.scenario_id,
            plan.plan_id,
            scenario.horizon_s,
            {"burning_reached_domain_edge": False},
            {**plan.to_mapping(), "cleared_area_m2": 10},
            {"code_version": self.code_version},
            {},
        )


def inputs():
    return (
        EvaluationScenario("s", (Ignition((1.0, 1.0)),), 60),
        canonical_plan("z", (ClearingPath(((3.0, 5.0), (7.0, 5.0)), 1),)),
    )


def test_cache_reuse_context_isolation_and_corruption(script, tmp_path):
    engine = Engine()
    scenario, plan = inputs()
    a = script.TrainingOracle(engine, (scenario,), (scenario,), tmp_path, "experiment")
    assert a(plan) == a(plan)
    assert engine.calls == 1
    assert a.cache_hits == 1
    # A genuinely new instance resumes without another solver call.
    b = script.TrainingOracle(engine, (scenario,), (scenario,), tmp_path, "experiment")
    b(plan)
    assert b.cache_hits == 1
    engine.zones["z"][0, 0] = False
    c = script.TrainingOracle(engine, (scenario,), (scenario,), tmp_path, "experiment")
    c(plan)
    assert engine.calls == 2
    for path in tmp_path.glob("*.json"):
        raw = json.loads(path.read_text())
        raw["payload"]["status"] = "corrupted"
        path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        c(plan)


def test_protected_ignition_is_checked_before_any_solver_call(script, tmp_path):
    engine = Engine()
    scenario, _ = inputs()
    plan = canonical_plan("z", (ClearingPath(((0.0, 1.0), (4.0, 1.0)), 1),))
    oracle = script.TrainingOracle(engine, (scenario,), (scenario,), tmp_path, "experiment")
    with pytest.raises(SearchRejected, match="protected ignition"):
        oracle(plan)
    with pytest.raises(SearchRejected):
        oracle(plan)
    assert oracle.cache_hits == 1
    assert engine.calls == 0


def test_scenario_change_does_not_hit_the_old_cache(script, tmp_path):
    engine = Engine()
    scenario, plan = inputs()
    script.TrainingOracle(engine, (scenario,), (scenario,), tmp_path, "x")(plan)
    changed = EvaluationScenario("s", scenario.ignitions, 120)
    script.TrainingOracle(engine, (changed,), (scenario,), tmp_path, "x")(plan)
    assert engine.calls == 2


def test_warm_cache_audits_physics_and_retains_original_signature(script, tmp_path):
    engine = Engine()
    scenario, plan = inputs()
    old = tmp_path / "old"
    old.mkdir()
    name = "src/pyfireca/planning/engines/village.py"
    source = old / "source_snapshot" / name
    source.parent.mkdir(parents=True)
    source.write_text("unchanged physical evaluator")
    protocol = {
        "code": engine.code_version,
        "case_sha256": "case",
        "annotated_structures_sha256": "structures",
        "zone_geometry_sha256": "zone",
        "window": [0, 5, 0, 5],
        "training": [asdict(scenario)],
        "held_out": asdict(scenario),
        "search_domain": "local",
        "source_hashes": {name: hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    script.dump(old / "protocol.json", protocol)
    script.dump(old / "input-lock.json", {"crop_case_sha256": "case"})
    record = engine.evaluate(scenario, plan).to_mapping()
    signature = hashlib.sha256()
    for item in (
        engine.case_sha256,
        engine.code_version,
        asdict(engine.transport),
        asdict(scenario),
        plan.to_mapping(),
        engine.structure_types,
    ):
        signature.update(json.dumps(item, sort_keys=True).encode())
    signature.update(engine.zones[plan.zone_id].tobytes())
    record["evaluation_sha256"] = signature.hexdigest()
    payload = {"status": "ok", "evaluations": [record]}
    script.dump(
        old / "evaluation-cache" / "cached.json",
        {"payload": payload, "payload_sha256": script.digest(payload)},
    )
    new_protocol = {**protocol, "code": "changed optimizer"}
    engine.code_version = "changed optimizer"
    engine.calls = 0
    warm = script.compatible_training_cache(old, new_protocol, engine, (scenario,))
    oracle = script.TrainingOracle(
        engine, (scenario,), (scenario,), tmp_path / "new", "new experiment", warm_records=warm
    )
    result = oracle(plan)[0]
    assert result.evaluation_sha256 == record["evaluation_sha256"]
    assert result.diagnostics["code_version"] == "code"
    assert oracle.warm_hits == 1
    assert engine.calls == 0
    bad = {**new_protocol, "source_hashes": {name: "changed physics"}}
    with pytest.raises(ValueError, match="model mismatch"):
        script.compatible_training_cache(old, bad, engine, (scenario,))
    record["evaluation_sha256"] = "forged"
    script.dump(
        old / "evaluation-cache" / "cached.json",
        {"payload": payload, "payload_sha256": script.digest(payload)},
    )
    with pytest.raises(ValueError, match="evaluation signature mismatch"):
        script.compatible_training_cache(old, new_protocol, engine, (scenario,))
