"""Finish registered evaluation, full-scene checks, replays and audits in order."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_batuan_adaptive_search import digest, dump

ROOT = Path(__file__).resolve().parents[1]


def phase(study, name, command):
    path = study / (name + ".log")
    if path.exists():
        count = 1
        while path.with_name(path.stem + f".attempt-{count}.log").exists():
            count += 1
        path.rename(path.with_name(path.stem + f".attempt-{count}.log"))
    print(json.dumps({"phase": name, "status": "started"}), flush=True)
    with path.open("w") as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(json.dumps({"phase": name, "status": "finished"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args()
    study = args.study.resolve()
    sources = {}
    for name in (
        Path(__file__).name,
        "validate_batuan_multiscenario.py",
        "audit_batuan_multiscenario.py",
        "render_batuan_multiscenario_report.py",
        "prefetch_batuan_multiscenario_controls.py",
    ):
        source = ROOT / "scripts" / name
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        dest = study / "postprocessing-sources" / sha / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        sources[name] = {"sha256": sha, "snapshot": str(dest.relative_to(study))}
    dump(study / "postprocessing-provenance.json", sources)
    report = ROOT / "scripts/render_batuan_multiscenario_report.py"
    while not (study / "selection.json").exists():
        subprocess.run([sys.executable, str(report), str(study)], cwd=ROOT, check=True)
        time.sleep(20)
    sha = digest(json.loads((study / "selection.json").read_text()))
    python = sys.executable
    with ThreadPoolExecutor(max_workers=2) as pool:
        final = pool.submit(
            phase,
            study,
            "final-comparison",
            [
                python,
                "scripts/run_batuan_multiscenario_search.py",
                "--output",
                str(study),
                "--phase",
                "finalize",
                "--workers",
                "3",
            ],
        )
        full = pool.submit(
            phase,
            study,
            "full-scene-validation",
            [
                python,
                "scripts/validate_batuan_multiscenario.py",
                str(study),
                "--phase",
                "full",
                "--workers",
                "6",
            ],
        )
        final.result()
        subprocess.run([python, str(report), str(study)], cwd=ROOT, check=True)
        full.result()
    # Initial full-scene batches may finish before the local comparison report;
    # a cached-only repeat attaches the now available crop/full comparison.
    phase(
        study,
        "full-comparison-audit",
        [
            python,
            "scripts/validate_batuan_multiscenario.py",
            str(study),
            "--phase",
            "full",
            "--workers",
            "6",
        ],
    )
    phase(
        study,
        "native-replays",
        [
            python,
            "scripts/validate_batuan_multiscenario.py",
            str(study),
            "--phase",
            "replays",
            "--workers",
            "3",
        ],
    )
    phase(study, "integrity-audit", [python, "scripts/audit_batuan_multiscenario.py", str(study)])
    subprocess.run([python, str(report), str(study)], cwd=ROOT, check=True)
    if digest(json.loads((study / "selection.json").read_text())) != sha:
        raise ValueError("postprocessing changed the selected plans")
    dump(
        study / "completion.json",
        {
            "status": "completed",
            "selection_sha256": sha,
            "full_validation": json.loads((study / "full-validation.json").read_text()),
            "audit": json.loads((study / "audit.json").read_text())["status"],
        },
    )


if __name__ == "__main__":
    main()
