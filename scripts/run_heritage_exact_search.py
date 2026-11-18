"""Run a frozen small exact demolition search with retained-source dual-scale CA."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path

from pyfireca.planning.building_search import ProtectionCheck, exact_demolition_search
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.heritage import content_hash

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def run(config_path, output, *, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = read(config_path)
    if config["schema_version"] != "heritage-exact-search-config-1":
        raise ValueError("unsupported exact search configuration")
    spec = importlib.util.spec_from_file_location(
        "exact_roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    study_path = config_path.parent / config["study_path"]
    bundle, transport, inventory = runner.load_study(study_path)
    fires, references = [], {}
    for row in config["fires"]:
        path = (config_path.parent / row["protocol_path"]).resolve()
        original = read(path)
        if (
            content_hash({k: v for k, v in original.items() if k != "protocol_sha256"})
            != original["protocol_sha256"]
        ):
            raise ValueError("saved source protocol fingerprint mismatch")
        references[str(path)] = content_hash(original)
        protocol = {
            **original,
            "draws": [d for d in original["draws"] if d["draw_id"] == row["draw_id"]],
        }
        protocol.pop("protocol_sha256")
        protocol["protocol_sha256"] = content_hash(protocol)
        fires.append({"fire_id": row["fire_id"], "protocol": protocol})
    paths = [Path(__file__), Path(runner.__file__), *sorted((ROOT / "src/pyfireca").rglob("*.py"))]
    source = {str(p.relative_to(ROOT)): p.read_text() for p in paths}
    code_hash = content_hash({p: content_hash(text) for p, text in source.items()})
    initial = tuple(sorted(config["initial_ids"]))

    def persist(ids, scenario, check, record, layout, result):
        folder = output / "evaluations" / content_hash([list(ids), scenario])
        folder.mkdir(parents=True, exist_ok=True)
        dump(folder / "evaluation.json", record.to_mapping())
        dump(
            folder / "check.json",
            {
                "problem_sha256": oracle.problem_sha256,
                "demolished_ids": ids,
                "scenario_id": scenario,
                "status": check.status,
                "evidence": check.evidence,
            },
        )
        if ids == initial and scenario == next(iter(oracle.scenarios)):
            runner.save_evaluation(
                folder / "native", record, layout, result, oracle._engine.contact_builder(layout)
            )
        print(
            json.dumps(
                {
                    "ids": ids,
                    "scenario": scenario,
                    "status": check.status,
                    "source": check.evidence["source_id"],
                    "elapsed_s": round(check.evidence["elapsed_s"], 2),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    oracle = RoofDemolitionOracle(
        bundle,
        transport,
        inventory,
        candidate_ids=tuple(config["candidate_ids"]),
        fires=fires,
        code_version=code_hash,
        horizon_s=config["horizon_s"],
        scales_m=tuple(config["roof_scales_m"]),
        quadrature_spacing_m=config["quadrature_spacing_m"],
        max_nodes=config["max_nodes"],
        max_radiation_edges=config["max_radiation_edges"],
        on_evaluation=persist,
        refinement_ids=config.get("refinement_ids"),
        maximum_demolitions=config.get("maximum_demolitions"),
    )
    experiment = {
        "config": config,
        "study": read(study_path),
        "problem": oracle.problem,
        "problem_sha256": oracle.problem_sha256,
        "source_references": references,
        "source_sha256": {p: content_hash(text) for p, text in source.items()},
    }
    if resume:
        if read(output / "experiment.json") != json.loads(json.dumps(experiment)):
            raise ValueError("cannot resume: frozen problem or producer changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(output / "experiment.json", experiment)
        dump(output / "inventory.json", inventory.to_mapping())
        for relative, text in source.items():
            destination = output / "source-snapshot" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
    cache = {}
    for path in (output / "evaluations").glob("*/check.json"):
        saved = read(path)
        if saved["problem_sha256"] != oracle.problem_sha256:
            raise ValueError("saved check belongs to a different frozen problem")
        key = (oracle.problem_sha256, tuple(saved["demolished_ids"]), saved["scenario_id"])
        cache[key] = ProtectionCheck(saved["status"], saved["evidence"])

    def evaluate(ids, scenario):
        if shutil.disk_usage(output).free < config["minimum_free_disk_GB"] * 1e9:
            raise RuntimeError("insufficient disk for remaining exact search evaluations")
        print(f"Starting {ids}/{scenario}", flush=True)
        return oracle(ids, scenario)

    result = exact_demolition_search(
        {i: inventory.by_id[i].geometry.area for i in config["candidate_ids"]},
        tuple(oracle.scenarios),
        evaluate,
        problem_sha256=oracle.problem_sha256,
        initial_ids=initial,
        max_new_checks=min(
            config["max_new_checks"],
            max(
                0,
                config.get("max_total_checks", len(cache) + config["max_new_checks"]) - len(cache),
            ),
        ),
        cache=cache,
        dominated_candidates=(
            oracle.proven_dominated_candidates()
            if config.get("use_dominance_pruning", False)
            else None
        ),
        prioritize_failures=config.get("prioritize_failures", False),
        max_wall_time_s=config.get("max_wall_time_s"),
    )
    dump(output / "search-result.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "optimality_proven",
                    "best_ids",
                    "best_objective",
                    "new_checks",
                    "cache_hits",
                )
            }
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, resume=args.resume)
