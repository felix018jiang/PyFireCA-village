"""Resume the frozen observed-object batch, render progress and archive results.

This supervisor never commits. A file lock prevents duplicate batch supervisors.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from run_batuan_building_pairs import ROOT, dump, sha


def call(args, stream):
    return subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=stream, stderr=stream)


def progress(output):
    subprocess.run(
        [sys.executable, "scripts/render_batuan_observed_joint.py", "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def archive(output):
    dest = ROOT.parent / "PyFireCA-backups" / output.name
    if (dest / "backup-validation.json").exists():
        proof = json.loads((dest / "backup-validation.json").read_text())
        if sha(dest / "observed-joint-experiment.tar.gz") != proof["archive_sha256"]:
            raise RuntimeError("existing backup checksum mismatch")
        return dest
    dest.mkdir(parents=True, exist_ok=False)
    files = [
        p
        for p in output.rglob("*")
        if p.is_file()
        and p.suffix not in {".lock", ".tmp"}
        and p.name not in {"completion.json", "pipeline.json", "pipeline.log"}
    ]
    files.extend(
        p
        for p in [
            ROOT / "docs/results/batuan-observed-joint-20261017.json",
            ROOT / "docs/BATUAN_OBSERVED_JOINT_RESULTS_20261017.md",
        ]
        if p.exists()
    )
    manifest = {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}
    path = dest / "observed-joint-experiment.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for p in sorted(files):
            tar.add(p, arcname=str(p.relative_to(ROOT)), recursive=False)
    with tarfile.open(path) as tar:
        for name, h in manifest.items():
            if hashlib.sha256(tar.extractfile(name).read()).hexdigest() != h:
                raise AssertionError("archive file checksum mismatch")
    bundle = dest / "repository.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=ROOT, check=True)
    check = subprocess.run(
        ["git", "bundle", "verify", str(bundle)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    (dest / "bundle-validation.log").write_text(check.stdout + check.stderr)
    dump(
        dest / "backup-validation.json",
        {
            "archive_sha256": sha(path),
            "repository_bundle_sha256": sha(bundle),
            "verified_files": len(manifest),
            "files": manifest,
            "result_documents_committed": False,
            "automatic_commit_performed": False,
        },
    )
    return dest


def main(output, workers):
    with (output / "pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Existing supervisor is already running.")
            return
        if (output / "completion.json").exists():
            print("Study already completed.")
            return
        prior = (
            json.loads((output / "pipeline.json").read_text())
            if (output / "pipeline.json").exists()
            else {}
        )
        child = prior.get("child_pid")
        if child:
            ps = subprocess.run(
                ["ps", "-p", str(child), "-o", "command="], capture_output=True, text=True
            )
            if "scripts/run_batuan_observed_joint.py" in ps.stdout:
                print("Existing worker is still running; no duplicate batch started.")
                return

        def status(stage, **kwargs):
            dump(
                output / "pipeline.json",
                {
                    "stage": stage,
                    "pid": os.getpid(),
                    "workers": workers,
                    "automatic_commit": False,
                    **kwargs,
                },
            )

        with (output / "pipeline.log").open("a", buffering=1) as stream:
            for phase, artifact in [("search", "selection.json"), ("confirm", "report.json")]:
                if (output / artifact).exists():
                    continue
                status(phase)
                process = call(
                    [
                        "scripts/run_batuan_observed_joint.py",
                        "--output",
                        str(output),
                        "--phase",
                        phase,
                        "--workers",
                        str(workers),
                    ],
                    stream,
                )
                status(phase, child_pid=process.pid)
                while process.poll() is None:
                    try:
                        progress(output)
                    except subprocess.CalledProcessError as error:
                        # A report rendering error must not orphan active CA workers.
                        stream.write(f"Progress render warning: {error.stderr}\n")
                    time.sleep(20)
                if process.returncode:
                    raise RuntimeError(
                        f"{phase} failed with exit {process.returncode}; see pipeline.log"
                    )
                progress(output)
            status("audit_and_archive")
            progress(output)
            if not (output / "audit.json").exists():
                raise AssertionError("final audit missing")
            backup = archive(output)
            dump(
                output / "completion.json",
                {
                    "status": "audited_and_archived",
                    "backup": str(backup),
                    "automatic_commit": False,
                    "audit_sha256": sha(output / "audit.json"),
                },
            )
            status("complete", backup=str(backup))
            print(json.dumps({"complete": str(output), "backup": str(backup)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    try:
        main(args.output.resolve(), args.workers)
    except Exception as error:
        dump(
            args.output.resolve() / "pipeline-error.json", {"error": str(error), "pid": os.getpid()}
        )
        raise
