"""Replay declared historical draws in a new common-layout comparison."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash


def run(config_path, output, *, prepare_only=False, resume=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = json.loads(config_path.read_text())
    protocol = json.loads((config_path.parent / config["source_protocol_path"]).read_text())
    if content_hash(protocol) != config["source_protocol_sha256"]:
        raise ValueError("prescribed source protocol changed")
    spec = importlib.util.spec_from_file_location(
        "prescribed_random_runner", Path(__file__).with_name("run_heritage_random_sources.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    def freeze(inventory, **kwargs):
        if inventory.sha256 != protocol["inventory_sha256"]:
            raise ValueError("prescribed source inventory differs")
        if any(
            protocol["ignition_area_m2" if k == "area_m2" else k] != v for k, v in kwargs.items()
        ):
            raise ValueError("configuration and prescribed source settings differ")
        result = {k: v for k, v in protocol.items() if k != "protocol_sha256"}
        result["prescribed_sampler_source_sha256"] = content_hash(Path(__file__).read_text())
        result["protocol_sha256"] = content_hash(result)
        return result

    runner.freeze_source_protocol = freeze
    if not resume:
        result = runner.run(config_path, output, prepare_only=True)
        (output / "prescribed-source-snapshot.py").write_text(Path(__file__).read_text())
        if prepare_only:
            return result
    elif content_hash((output / "prescribed-source-snapshot.py").read_text()) != content_hash(
        Path(__file__).read_text()
    ):
        raise ValueError("prescribed source driver changed")
    return runner.run(config_path, output, prepare_only=prepare_only, resume=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config, args.output, prepare_only=args.prepare_only, resume=args.resume)
