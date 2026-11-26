"""Finish registered wind cases on the complete original domain after the local-window run."""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import UTC, datetime
from pathlib import Path

from run_heritage_wind_extinction import (
    DEST,
    ORIGINAL,
    completed,
    group_job,
    signed_inventory,
    terminal_signature,
)


def checked_inventory(source, root):
    reg, originals, active = signed_inventory(source)
    signature = terminal_signature(originals[0]["solver_sha256"])
    manifest = json.loads((root / "registration.json").read_text())
    if (
        manifest["original_registration_sha256"] != originals[0]["registration_sha256"]
        or manifest["original_solver_sha256"] != originals[0]["solver_sha256"]
        or manifest["terminal_solver_sha256"] != signature
        or manifest["continued"] != [r["case_id"] for r in active]
        or manifest["already_extinct"]
        != [r["case_id"] for r in originals if r["extinction_closed"]]
    ):
        raise ValueError("The existing terminal registration differs from the verified inventory")
    return reg, active, signature


def build_jobs(rows, wind_ids, group_size):
    # Each physical layout and contact graph is prepared once for this set of
    # winds; the radiation graph is rebuilt just once per wind and serves all plans.
    groups = defaultdict(list)
    for row in rows:
        group = wind_ids.index(row["wind_id"]) // group_size
        groups[row["fire_id"], group].append(row["case_id"])
    return [(fire, keys) for (fire, _), keys in sorted(groups.items())]


def log_error(root, village, attempt, case_ids, error):
    record = {
        "utc": datetime.now(UTC).isoformat(),
        "village": village,
        "attempt": attempt,
        "case_ids": case_ids,
        "type": type(error).__name__,
        "error": str(error),
    }
    with (root / "full-domain-errors.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{village} attempt {attempt}: {type(error).__name__}: {error}", flush=True)


def finish_village(
    village, group_size, workers, attempts, source, root, reg, rows, signature, dry_run
):
    grid = reg["villages"][village]["grid"]
    radius = 2 * max(grid["width"], grid["height"]) * grid["cell_size_m"]
    wind_ids = reg["primary"]["wind_ids"]
    for attempt in range(1, attempts + 1):
        pending = [r for r in rows if not completed(root, r, signature)]
        if not pending:
            break
        # If a group failed, isolate wind-specific failures on retry. Any valid
        # records written before the failure are read back instead of recomputed.
        jobs = build_jobs(pending, wind_ids, group_size if attempt == 1 else 1)
        if attempt > 1 and attempt == attempts:
            jobs = [(fire, [key]) for fire, keys in jobs for key in keys]
        print(
            f"{village}: {len(pending)}/{len(rows)} active cases still pending, "
            f"{len(jobs)} jobs, window {radius:g} m, {workers} workers, attempt {attempt}",
            flush=True,
        )
        if dry_run:
            return []
        broken = False
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(group_job, (source, root, village, fire, keys, radius, signature)): (
                    fire,
                    keys,
                )
                for fire, keys in jobs
            }
            for future in as_completed(futures):
                fire, keys = futures[future]
                try:
                    unresolved = future.result()
                    if unresolved:
                        raise RuntimeError(
                            f"Full original domain unexpectedly requires expansion: {unresolved}"
                        )
                except Exception as exc:
                    log_error(root, village, attempt, keys, exc)
                    broken |= isinstance(exc, (BrokenProcessPool, MemoryError))
                else:
                    print(f"{village} {fire}: published {len(keys)} verified cases", flush=True)
        if broken:
            workers = max(1, workers // 2)
    missing = [] if dry_run else [r["case_id"] for r in rows if not completed(root, r, signature)]
    if not missing and not dry_run:
        print(f"{village}: all {len(rows)} previously active cases are certified.", flush=True)
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ORIGINAL)
    parser.add_argument("--output", type=Path, default=DEST)
    parser.add_argument("--village", action="append", choices=("batuan", "pingyan"))
    parser.add_argument("--batuan-workers", type=int, default=6)
    parser.add_argument("--pingyan-workers", type=int, default=4)
    parser.add_argument("--winds-per-job", choices=(2, 3), type=int, default=3)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument(
        "--dry-run", action="store_true", help="Verify saved cases and list remaining work only."
    )
    args = parser.parse_args()
    if min(args.batuan_workers, args.pingyan_workers, args.attempts) < 1:
        parser.error("worker limits and attempt count must be positive")
    source, root = args.source.resolve(), args.output.resolve()
    reg, active, signature = checked_inventory(source, root)
    failures = []
    # Sequential villages keep the two peak memory budgets from overlapping.
    for village in dict.fromkeys(args.village or ("batuan", "pingyan")):
        rows = [r for r in active if r["village"] == village]
        workers = args.batuan_workers if village == "batuan" else args.pingyan_workers
        failures.extend(
            finish_village(
                village,
                args.winds_per_job,
                workers,
                args.attempts,
                source,
                root,
                reg,
                rows,
                signature,
                args.dry_run,
            )
        )
    if failures:
        raise SystemExit(
            f"{len(failures)} cases still need valid certificates. "
            f"Details: {root / 'full-domain-errors.jsonl'}"
        )


if __name__ == "__main__":
    main()
