"""Resume a frozen cohort with an explicitly recorded graph-capacity increase.

Only the abort threshold changes. Frozen inputs, graph equations, spatial
resolution, source bank and thermal solver remain those of the original runner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash


def resume(config_path, output, *, max_radiation_edges):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    experiment = json.loads((output / "experiment.json").read_text())
    if config != experiment["config"]:
        raise ValueError("resource resume requires the unchanged frozen configuration")
    if max_radiation_edges <= config["max_radiation_edges"]:
        raise ValueError("resource resume requires an increased edge capacity")
    script = Path(__file__).resolve()
    receipt = {
        "schema_version": "heritage-execution-resource-override-1",
        "reason": "original_radiation_edge_capacity_exceeded_before_simulation",
        "frozen_experiment_sha256": content_hash(experiment),
        "frozen_config_sha256": content_hash(config),
        "original_max_radiation_edges": config["max_radiation_edges"],
        "effective_max_radiation_edges": max_radiation_edges,
        "physical_parameters_and_frozen_inputs_unchanged": True,
        "driver": script.name,
        "driver_content_sha256": content_hash(script.read_text()),
    }
    receipt_path = output / "execution-resource-override.json"
    if receipt_path.exists():
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError("resource override differs from the saved execution receipt")
    else:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        destination = output / "execution-resource-source" / script.name
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(script.read_text())
    receipt_hash = content_hash(receipt)
    spec = importlib.util.spec_from_file_location(
        "frozen_random_runner", script.with_name("run_heritage_random_sources.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original_engine = runner.RoofHeritageEvaluationEngine

    class CapacityEngine(original_engine):
        def __init__(self, *args, **kwargs):
            kwargs["max_radiation_edges"] = max_radiation_edges
            super().__init__(*args, **kwargs)

        def simulate(self, *args, **kwargs):
            record, layout, result = super().simulate(*args, **kwargs)
            record.diagnostics["execution_resource_override_sha256"] = receipt_hash
            record.diagnostics["effective_max_radiation_edges"] = max_radiation_edges
            return record, layout, result

    runner.RoofHeritageEvaluationEngine = CapacityEngine
    return runner.run(config_path, output, resume=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-radiation-edges", type=int, required=True)
    args = parser.parse_args()
    resume(args.config, args.output, max_radiation_edges=args.max_radiation_edges)
