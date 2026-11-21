"""Continue the original Pingyan independent narrow and wide arms after joint-arm reconciliation."""

import argparse
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import schedule_heritage_registered_joint_families as first

AREA = first.RUN / "resource-scheduling/registered-corridor-family-shards"
REG = AREA / "registration.json"
SCRIPT = Path(__file__).resolve()


def folder(i):
    return AREA / f"slot-{i:02d}"


def verify():
    first.verify_registration()
    reg = first.read(REG)
    if (
        reg["driver_sha256"] != first.digest(SCRIPT)
        or reg["joint_administration_registration_sha256"] != first.digest(first.REG)
        or reg["native_experiment_sha256"] != first.digest(first.RUN / "experiment.json")
        or reg["config_sha256"] != first.digest(first.CONFIG)
    ):
        raise RuntimeError("registered continuation inputs changed")
    return reg


def run_one(i):
    verify()
    native = first.frozen_native()
    _, cfg, rows = first.tables(native)
    home = folder(i)
    if (
        i not in range(9, 25)
        or first.read(home / "admission.json")["registration_sha256"] != first.digest(REG)
        or first.read(home / "execution-budget.json")
        != {"elapsed_s": 0.0, "started_checks": 0, "active": None}
    ):
        raise RuntimeError("missing admission or previously charged native slot")
    old = native.execution_checks

    def project(c):
        selected = [
            r for r in old(c) if r[0].plan_id == rows[i - 1][0].plan_id and r[1] == rows[i - 1][1]
        ]
        if len(selected) != 1 or c != cfg:
            raise ValueError("case is not the unique originally declared result")
        yield from selected

    native.execution_checks = project
    native.resume(first.CONFIG, home)


def admit(i, prior_started, ordinal_count):
    if prior_started + ordinal_count + 1 > 24:
        raise RuntimeError("exceeded total existing 24-slot allocation")
    home = folder(i)
    home.mkdir()
    for name in ("experiment.json", "preflight.json"):
        os.link(first.RUN / name, home / name)
    (home / "source-snapshot").symlink_to(first.RUN / "source-snapshot", target_is_directory=True)
    first.save(
        home / "execution-budget.json",
        {"elapsed_s": 0.0, "started_checks": 0, "active": None},
    )
    first.save(
        home / "admission.json",
        {
            "registration_sha256": first.digest(REG),
            "ordinal": i,
            "unix_before_launch_s": time.time(),
        },
    )
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    }
    with (home / "resume.log").open("a") as log:
        return subprocess.Popen(
            [sys.executable, str(SCRIPT), "--slot", str(i)],
            stdout=log,
            stderr=log,
            env=env,
        )


def execute_arm(number, native, exp, cfg, rows):
    ordinals = list(range(8 * (number - 1) + 1, 8 * number + 1))
    original = first.read(first.RUN / "execution-budget.json")
    if original["active"] is not None:
        raise RuntimeError("root native ledger has an active writer")
    if any(native.result_folder(first.RUN, *rows[i - 1]).exists() for i in ordinals):
        raise RuntimeError("continuation would duplicate a previously recorded ordinal")
    jobs, results, witnesses, closed, interrupted = {}, {}, {}, {}, {}
    paused = set()
    arm_ledger = AREA / f"arm-{number}-ledger.json"
    last_update = 0
    old_signature = None
    while True:
        now = time.time()
        active = {i: p for i, p in jobs.items() if p.poll() is None}
        for i in jobs:
            if i not in results:
                result, witness = first.strict_result(native, folder(i), rows[i - 1])
                if result:
                    results[i] = result
                    if witness:
                        witnesses[i] = witness
            if i not in active and i not in closed:
                b = first.read(folder(i) / "execution-budget.json")
                if b["active"] is not None or b["started_checks"] != 1 or i not in results:
                    interrupted[i] = (
                        "worker exited without a closed original native result and charge"
                    )
                closed[i] = b
        wall = original["elapsed_s"] + sum(b["elapsed_s"] for b in closed.values())
        wall += sum(
            now - first.read(folder(i) / "admission.json")["unix_before_launch_s"] for i in active
        )
        if wall >= cfg["max_wall_time_s"] - 300:
            for i, p in active.items():
                if i not in interrupted:
                    os.kill(p.pid, signal.SIGCONT)
                    os.kill(p.pid, signal.SIGINT)
                    interrupted[i] = "shared 24-case cumulative worker-wall limit"
            if not active:
                interrupted[0] = "insufficient remaining aggregate work time"
        ac, critical = first.power()
        for i, p in active.items():
            if not ac and critical and "T" not in first.ps(p.pid, "state"):
                os.kill(p.pid, signal.SIGSTOP)
                paused.add(i)
            if ac and i in paused:
                os.kill(p.pid, signal.SIGCONT)
                paused.remove(i)
        if not interrupted and not witnesses and ac and first.memory_free() >= 18:
            for i in ordinals:
                if len(active) >= 3:
                    break
                if i in jobs or (i % 2 == 0 and i - 1 not in closed):
                    continue
                # The narrow arm has the largest geometric envelope. Keep one fine
                # mesh at a time on this 16-GB host and allow other coarse families.
                if i % 2 == 0 and any(j % 2 == 0 for j in active):
                    continue
                if shutil.disk_usage(first.RUN).free < cfg["minimum_free_disk_GB"] * 1e9:
                    interrupted[i] = "registered disk minimum"
                    break
                jobs[i] = admit(i, original["started_checks"], len(jobs))
                active[i] = jobs[i]
        signature = (
            tuple(jobs),
            tuple(results),
            tuple(closed),
            tuple(witnesses),
            tuple(interrupted),
            tuple(sorted(paused)),
        )
        if signature != old_signature or now - last_update >= 30:
            first.save(
                arm_ledger,
                dict(
                    registration_sha256=first.digest(REG),
                    arm=number,
                    closed_seed_budget=original,
                    updated_unix_s=now,
                    candidate_ordinals=ordinals,
                    admitted_ordinals=list(jobs),
                    observed=results,
                    strict_witness_ordinals=sorted(witnesses),
                    stopped_or_incomplete=interrupted,
                    completed_native_shard_budgets=closed,
                    active_pids={i: p.pid for i, p in active.items()},
                    cumulative_used_or_live_wall_s=wall,
                    ceiling_on_started_with_reserved=original["started_checks"] + len(jobs),
                    paused_ordinals=sorted(paused),
                ),
            )
            old_signature = signature
            last_update = now
        if not active and (interrupted or witnesses or len(closed) == 8):
            break
        time.sleep(4)
    if interrupted:
        raise RuntimeError("continuation halted with charges preserved; see arm ledger")
    before = first.read(first.RUN / "execution-budget.json")
    if before != original:
        raise RuntimeError("another writer changed the global native budget")
    first.save(AREA / f"arm-{number}-opening-budget.json", original)
    for i in sorted(results):
        plan, scenario = rows[i - 1]
        src = native.result_folder(folder(i), plan, scenario)
        dest = native.result_folder(first.RUN, plan, scenario)
        if dest.exists() or first.digest(src / "complete.json") != results[i]["manifest_sha256"]:
            raise RuntimeError("native source lineage or target conflict")
        staging = dest.with_name(dest.name + ".importing")
        shutil.copytree(src, staging, copy_function=os.link)
        native.read_complete(
            staging,
            first.PROBLEM,
            plan_id=plan.plan_id,
            scenario_id=scenario,
            demolished_ids=plan.demolished_ids,
            cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
            terminal_required=True,
        )
        staging.rename(dest)
    skipped = []
    if witnesses:
        key = min(witnesses)
        witness = native.native_protection_failure_witness(
            first.RUN, first.PROBLEM, *rows[key - 1], terminal_required=True
        )
        for i in ordinals:
            if i not in results:
                native.persist_execution_skip(first.RUN, exp, *rows[i - 1], witness)
                skipped.append(i)
    charged = original["started_checks"] + sum(b["started_checks"] for b in closed.values())
    wall = original["elapsed_s"] + sum(b["elapsed_s"] for b in closed.values())
    if charged > 24 or wall > cfg["max_wall_time_s"] or len(results) + len(skipped) != 8:
        raise RuntimeError("registered arm does not reconcile to the declared bounds")
    final = dict(started_checks=charged, elapsed_s=wall, active=None)
    first.save(first.RUN / "execution-budget.json", final)
    summary = native.summarize(first.RUN)
    first.save(
        AREA / f"arm-{number}-merged.json",
        dict(
            native_problem=first.PROBLEM,
            registered_ordinals=ordinals,
            complete_ordinals=sorted(results),
            strict_witness_ordinals=sorted(witnesses),
            not_run_by_verified_failure=skipped,
            global_native_budget=final,
            original_native_summary_completed=summary["completed_checks"],
            individual_manifests=results,
        ),
    )


def supervise():
    native = first.frozen_native()
    exp, cfg, rows = first.tables(native)
    first.verify_registration()
    AREA.mkdir(parents=True, exist_ok=True)
    lock = (AREA / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if REG.exists():
        raise RuntimeError("existing registration requires targeted recovery, no automatic repeat")
    first.save(
        REG,
        dict(
            schema="heritage-registered-corridor-arms-1",
            unix_registration_s=time.time(),
            native_problem=first.PROBLEM,
            native_experiment_sha256=first.digest(first.RUN / "experiment.json"),
            config_sha256=first.digest(first.CONFIG),
            driver_sha256=first.digest(SCRIPT),
            joint_administration_registration_sha256=first.digest(first.REG),
            prior_arm_required=(
                "validated joint-arm merge and reconciliation before any new projection"
            ),
            capacity=dict(
                max_simultaneous_independent=3,
                maximum_simultaneous_fine=1,
                max_nodes=cfg["max_nodes"],
                max_edges=cfg["max_radiation_edges"],
                all_arms_combined_work_s=cfg["max_wall_time_s"],
                all_arms_combined_slots=cfg["max_new_checks"],
            ),
            phases=[
                {"ordinals": list(range(9, 17)), "plan": rows[8][0].plan_id},
                {"ordinals": list(range(17, 25)), "plan": rows[16][0].plan_id},
            ],
            failure_rule=(
                "Use original native strict complete protected-core witness. Already "
                "admitted ordinals complete. Earn no loss endpoint for omitted slots."
            ),
            comparator=(
                "Three separate original arms. Compare loss by a common source only where "
                "the verified actual physical patch is identical; no source redraws."
            ),
            physical_or_scientific_input_changed=False,
        ),
    )
    merge = first.AREA / "merged-registration-receipt.json"
    while not merge.exists():
        ledger = first.read(first.AREA / "ledger.json")
        if ledger["interruptions"]:
            raise RuntimeError("earlier arm has unresolved charged work")
        time.sleep(8)
    joint = first.read(merge)
    original = first.read(first.RUN / "execution-budget.json")
    if (
        original
        != {
            "elapsed_s": joint["charged_cumulative_wall_s"],
            "started_checks": joint["native_total_started"],
            "active": None,
        }
        or joint["problem_sha256"] != first.PROBLEM
    ):
        raise RuntimeError("joint-arm native reconciliation failed")
    for phase in (2, 3):
        verify()
        execute_arm(phase, native, exp, cfg, rows)
    consolidated = native.summarize(first.RUN)
    failures = native.read_execution_skips(first.RUN, exp) or {"skipped_cases": []}
    if consolidated["completed_checks"] + len(failures["skipped_cases"]) != 24:
        raise RuntimeError("final reconciliation failed")
    first.save(
        AREA / "complete-registration.json",
        dict(
            original_declared=24,
            completed_native=consolidated["completed_checks"],
            skipped_by_verified_core_failure=len(failures["skipped_cases"]),
            total_global_budget=first.read(first.RUN / "execution-budget.json"),
            native_summary_sha256=first.digest(first.RUN / "summary.json"),
            all_original_frozen_arms=True,
            every_declared_slot_is_an_actual_simulation=len(failures["skipped_cases"]) == 0,
        ),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slot", type=int)
    args = p.parse_args()
    if args.slot is None:
        supervise()
    else:
        run_one(args.slot)
