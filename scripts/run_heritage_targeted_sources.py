"""Use the existing random-fire runner for declared source-building coverage.

The full inventory defines unambiguous roofs. Only the source population is
restricted; the fire simulation still includes the entire village.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol


def targeted_protocol(inventory, *, source_ids, **kwargs):
    protocol = freeze_source_protocol(inventory, **kwargs)
    selected = set(source_ids)
    if not selected or not selected <= set(protocol["members"]):
        raise ValueError("targeted sources must be eligible ordinary source buildings")
    protocol["unrestricted_protocol_sha256"] = protocol.pop("protocol_sha256")
    protocol["strata"] = {
        key: [i for i in ids if i in selected] for key, ids in protocol["strata"].items()
    }
    protocol["members"] = {i: v for i, v in protocol["members"].items() if i in selected}
    draws = []
    for draw in protocol["draws"]:
        priority = [i for i in draw["priority_ids"] if i in selected]
        if priority:
            draws.append({**draw, "priority_ids": priority, "baseline_source_id": priority[0]})
    protocol.update(
        draws=draws,
        targeted_source_ids=sorted(selected),
        targeted_sampler_source_sha256=content_hash(Path(__file__).read_text()),
        sampling="targeted_source_population;independent_uniform_roof_anchors",
        source_condition="one_fire_in_declared_retained_target_sources;not_village_risk",
    )
    protocol["protocol_sha256"] = content_hash(protocol)
    return protocol


def run(config_path, output, *, prepare_only=False, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    spec = importlib.util.spec_from_file_location(
        "targeted_random_runner", Path(__file__).with_name("run_heritage_random_sources.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    def freeze(inventory, **kwargs):
        return targeted_protocol(inventory, source_ids=config["target_source_ids"], **kwargs)

    runner.freeze_source_protocol = freeze
    if not resume:
        result = runner.run(config_path, output, prepare_only=True)
        (output / "targeted-source-snapshot.py").write_text(Path(__file__).read_text())
        if prepare_only:
            return result
    elif content_hash((output / "targeted-source-snapshot.py").read_text()) != content_hash(
        Path(__file__).read_text()
    ):
        raise ValueError("targeted source sampler changed since experiment freeze")
    return runner.run(config_path, output, prepare_only=prepare_only, resume=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, prepare_only=args.prepare_only, resume=args.resume)
