"""Rebuild searches from copied caches with every fresh CA solve forbidden."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from run_batuan_adaptive_search import digest, dump
from run_batuan_multiscenario_search import ROOT, ZONES, search_task

import pyfireca.planning.engines.village as engine_module


def check(study):
    study = Path(study).resolve()
    protocol = json.loads((study / "protocol.json").read_text())
    selection_sha = digest(json.loads((study / "selection.json").read_text()))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("resume requested an uncached CA solve")

    original = engine_module.run_v3_simulation
    comparisons = []
    try:
        engine_module.run_v3_simulation = forbidden
        with tempfile.TemporaryDirectory(prefix="batuan-resume-check-", dir=ROOT / "runs") as temp:
            target = Path(temp)
            shutil.copyfile(study / "protocol.json", target / "protocol.json")
            (target / "search-case").symlink_to(study / "search-case", target_is_directory=True)
            shutil.copytree(
                study / "evaluation-cache",
                target / "evaluation-cache",
                ignore=shutil.ignore_patterns("*.lock"),
            )
            for zone in ZONES:
                for replica in range(len(protocol["configs"])):
                    search_task(target, zone, replica)
                    filename = f"{zone}-replica{replica}-search.json"
                    old = json.loads((study / filename).read_text())
                    new = json.loads((target / filename).read_text())
                    for key in ("representative", "best_key", "selected_ids", "area_bound_pruned"):
                        if digest(old[key]) != digest(new[key]):
                            raise ValueError(f"resumed search differs: {filename} {key}")
                    fields = ("plan_id", "status", "effectiveness", "cleared_area_m2", "reason")
                    if [{k: r[k] for k in fields if k in r} for r in old["records"]] != [
                        {k: r[k] for k in fields if k in r} for r in new["records"]
                    ]:
                        raise ValueError("resumed candidate trajectory differs")
                    if new["physical_solves_this_invocation"]:
                        raise ValueError("resume computed fresh physical results")
                    comparisons.append(
                        {
                            "zone": zone,
                            "replica": replica,
                            "requests": len(new["records"]),
                            "same_choices_and_trajectory": True,
                            "fresh_solves": new["physical_solves_this_invocation"],
                        }
                    )
    finally:
        engine_module.run_v3_simulation = original
    if selection_sha != digest(json.loads((study / "selection.json").read_text())):
        raise ValueError("resume check modified original selection")
    result = {
        "status": "passed",
        "original_outputs_untouched": True,
        "selection_sha256": selection_sha,
        "searches": comparisons,
        "fresh_CA_solves": 0,
    }
    dump(study / "resume-audit.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("study", type=Path)
    print(json.dumps(check(p.parse_args().study), indent=2))
