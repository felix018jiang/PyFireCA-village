"""Recover the exact two protective actions for an independently checkable universal-cap run."""

import argparse
from pathlib import Path

import optimize_heritage_all_roof_compartments as solver
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def recover(village):
    root = Path(__file__).resolve().parents[1]
    source = root / f"docs/results/heritage-all-ordinary-roofs-tight-cap-{village}-20261111.json"
    saved = read_json(source)
    if saved["method_sha256"] != file_sha256(solver.__file__) or saved[
        "receipt_sha256"
    ] != content_hash({k: v for k, v in saved.items() if k != "receipt_sha256"}):
        raise ValueError("universal-cap calculation or result changed")
    actions = {}

    def capture(value):
        digest = content_hash(value)
        if isinstance(value, dict) and set(value) == {
            "demolished_ids",
            "cleared_physical_cell_ids",
        }:
            actions[digest] = value
        return digest

    solver.content_hash = capture
    replay = solver.run(village)
    controls = {}
    for name in ("minimum_clearance", "maximum_roof_retention"):
        if saved[name] != replay[name]:
            raise ValueError("the replay did not reproduce the original protective action")
        controls[name] = actions[saved[name]["protective_action_sha256"]]
    result = dict(
        village=village,
        controls=controls,
        original_receipt_file_sha256=file_sha256(source),
        interpretation="Protective clearance decisions for the universal-loss sensitivity experiment.",
        method_sha256=file_sha256(__file__),
    )
    result["receipt_sha256"] = content_hash(result)
    folder = root / "runs/research/heritage-universal-tight-cap-audit-inputs-20261111"
    folder.mkdir(parents=True, exist_ok=True)
    immutable(folder / f"{village}-protective-actions.json", encoded(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village", choices=solver.CAPS)
    recover(parser.parse_args().village)
